/* ============================================================
   Groundwork ATX — map explorer
   Leaflet map with four toggleable live layers scoped by one
   shared "activity window" (30d / 3mo / 6mo / 12mo). Changing
   the window refetches every layer for that range and swaps the
   markers only once the new data is ready — no blank flash; the
   SODA session cache makes toggling back instant. Clicking the
   map drops a survey circle and builds an "area report" from
   the loaded layer data (client-side haversine — no extra API
   round-trips).
   ============================================================ */

const MapView = (() => {
  const LAYER_DEFS = {
    building: { color: "#2a78d6", label: "Building permit", plural: "Building permits" },
    trades:   { color: "#eb6834", label: "Trade permit", plural: "Trade permits" },
    zoning:   { color: "#1baf7a", label: "Zoning case", plural: "Zoning cases" },
    co:       { color: "#4a3aa7", label: "Certificate of occupancy", plural: "Certificates of occupancy" },
  };

  const WINDOW_LABELS = { 30: "30 days", 90: "3 months", 180: "6 months", 365: "12 months" };

  let map = null;
  let layerGroups = {};
  let features = { building: [], trades: [], zoning: [], co: [] }; // {lat, lon, when, title, sub, layer, marker}
  let surveyCircle = null;
  let surveyMarker = null;
  let initialized = false;
  let windowDays = 90;
  let loadSeq = 0; // discards results of a superseded load
  let canvasRenderer = null;

  function haversineMeters(a, b) {
    const R = 6371000, toRad = (x) => (x * Math.PI) / 180;
    const dLat = toRad(b.lat - a.lat), dLon = toRad(b.lon - a.lon);
    const s =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(s));
  }

  function coords(row, f) {
    let lat = f.lat && row[f.lat] != null ? parseFloat(row[f.lat]) : NaN;
    let lon = f.lon && row[f.lon] != null ? parseFloat(row[f.lon]) : NaN;
    if ((isNaN(lat) || isNaN(lon))) {
      // fall back to any Socrata point column on the row
      for (const v of Object.values(row)) {
        if (v && typeof v === "object") {
          if (Array.isArray(v.coordinates) && v.coordinates.length >= 2) {
            lon = parseFloat(v.coordinates[0]); lat = parseFloat(v.coordinates[1]); break;
          }
          if (v.latitude != null && v.longitude != null) {
            lat = parseFloat(v.latitude); lon = parseFloat(v.longitude); break;
          }
        }
      }
    }
    if (isNaN(lat) || isNaN(lon)) return null;
    if (lat < 29 || lat > 31.5 || lon < -99 || lon > -96.5) return null; // not in the Austin area — bad geocode
    return { lat, lon };
  }

  function popupHTML(kicker, color, title, lines) {
    return `<div class="gw-popup">
      <div class="pop-kicker" style="color:${color}">${kicker}</div>
      <h4>${Views.esc(title)}</h4>
      ${lines.filter(Boolean).map((l) => `<p>${l}</p>`).join("")}
    </div>`;
  }

  /* Build a feature (with its marker) into `feats` — not on the map yet;
     the whole set is swapped in at once when the load completes. */
  function addFeature(feats, layerKey, pt, when, title, sub, popupLines) {
    const def = LAYER_DEFS[layerKey];
    const marker = L.circleMarker([pt.lat, pt.lon], {
      renderer: canvasRenderer,
      radius: 5,
      color: "#fdfcf9",       // 2px surface ring
      weight: 2,
      fillColor: def.color,
      fillOpacity: 0.85,
    }).bindPopup(popupHTML(def.label, def.color, title, popupLines), { maxWidth: 300 });
    feats[layerKey].push({ ...pt, when, title, sub, layer: layerKey, marker });
  }

  async function loadLayers() {
    const seq = ++loadSeq;
    const days = windowDays;
    const label = WINDOW_LABELS[days] || days + " days";
    const side = document.querySelector(".map-side .card-sub");
    if (side) side.textContent = `Loading ${label} of activity… (current markers stay until it's ready)`;

    const feats = { building: [], trades: [], zoning: [], co: [] };
    const tasks = [];

    tasks.push((async () => {
      const f = await SODA.resolveFields("permits");
      const com = f.classMapped ? `${f.classMapped} = ${SODA.q("Commercial")}` : null;
      const rows = await SODA.recent("permits", days, {
        where: [com], limit: days > 90 ? 12000 : 5000,
        snapFilter: (r, ff) => !ff.classMapped || r[ff.classMapped] === "Commercial",
      });
      for (const r of rows) {
        const pt = coords(r, f);
        if (!pt) continue;
        const typeDesc = String(r[f.typeDesc] || r[f.type] || "Permit");
        const isBuilding = /building|^bp$/i.test(typeDesc) || String(r[f.type]).toUpperCase() === "BP";
        const key = isBuilding ? "building" : "trades";
        const when = r[f.date];
        const val = f.valuation && r[f.valuation] ? Charts.money(+r[f.valuation]) : null;
        addFeature(feats, key, pt, when,
          r[f.address] || typeDesc,
          `${typeDesc}${r[f.workClass] ? " · " + r[f.workClass] : ""}`,
          [
            `${Views.esc(typeDesc)}${r[f.workClass] ? " · " + Views.esc(r[f.workClass]) : ""}`,
            r[f.desc] ? Views.esc(String(r[f.desc]).slice(0, 160)) : null,
            `Issued ${Views.fmtDate(when)}${val ? " · " + val : ""}`,
            r[f.status] ? "Status: " + Views.esc(r[f.status]) : null,
            f.link && r[f.link] ? `<a href="${Views.esc(r[f.link].url || r[f.link])}" target="_blank" rel="noopener">View permit record ↗</a>` : null,
          ]);
      }
    })());

    tasks.push((async () => {
      const f = await SODA.resolveFields("zoning");
      const rows = await SODA.recent("zoning", days, { limit: days > 90 ? 3000 : 1500 });
      for (const r of rows) {
        const pt = coords(r, f);
        if (!pt) continue;
        addFeature(feats, "zoning", pt, r[f.date],
          r[f.number] || "Zoning case",
          r[f.status] || "",
          [
            r[f.name] ? Views.esc(r[f.name]) : null,
            r[f.address] ? Views.esc(r[f.address]) : null,
            r[f.desc] ? Views.esc(String(r[f.desc]).slice(0, 140)) : null,
            r[f.status] ? "Status: " + Views.esc(r[f.status]) : null,
            r[f.date] ? "Filed/updated " + Views.fmtDate(r[f.date]) : null,
          ]);
      }
    })());

    tasks.push((async () => {
      const f = await SODA.resolveFields("co");
      const com = f.classMapped ? `${f.classMapped} = ${SODA.q("Commercial")}` : null;
      const rows = await SODA.recent("co", days, {
        where: [com], limit: days > 90 ? 6000 : 3000,
        snapFilter: (r, ff) => !ff.classMapped || r[ff.classMapped] === "Commercial",
      });
      for (const r of rows) {
        const pt = coords(r, f);
        if (!pt) continue;
        addFeature(feats, "co", pt, r[f.date],
          r[f.address] || r[f.desc] || "Certificate of occupancy",
          r[f.use] || "",
          [
            r[f.use] ? Views.esc(r[f.use]) : null,
            r[f.desc] ? Views.esc(String(r[f.desc]).slice(0, 140)) : null,
            r[f.date] ? "Issued " + Views.fmtDate(r[f.date]) : null,
          ]);
      }
    })());

    const results = await Promise.allSettled(tasks);
    if (seq !== loadSeq) return; // a newer window was selected while this one loaded

    // swap: clear old markers and mount the new set, honoring the toggles
    for (const key of Object.keys(LAYER_DEFS)) {
      layerGroups[key].clearLayers();
      for (const ft of feats[key]) layerGroups[key].addLayer(ft.marker);
    }
    features = feats;

    const legend = document.getElementById("layer-legend");
    if (legend) legend.textContent = `Layers · trailing ${label}`;

    const failed = results.filter((r) => r.status === "rejected").length;
    const total = Object.values(features).reduce((s, a) => s + a.length, 0);
    if (side) {
      let msg = failed
        ? `Loaded ${total.toLocaleString()} mapped records over ${label} (${failed} feed${failed > 1 ? "s" : ""} unavailable).`
        : `${total.toLocaleString()} mapped records over the last ${label}.`;
      if (SODA.status.snapshotUsed && days > 100) {
        msg += " Offline snapshot in use — it covers roughly the last 90 days of permits and COs.";
      }
      side.textContent = msg + " Click anywhere for an area report.";
    }

    if (surveyCircle) renderAreaReport(surveyCircle.getLatLng());
  }

  function renderAreaReport(latlng) {
    const radius = +document.getElementById("radius-select").value;
    const el = document.getElementById("area-report");
    const center = { lat: latlng.lat, lon: latlng.lng };

    if (surveyCircle) surveyCircle.remove();
    if (surveyMarker) surveyMarker.remove();
    surveyCircle = L.circle(latlng, {
      radius,
      color: "#b34a12",
      weight: 1.5,
      dashArray: null,
      fillColor: "#b34a12",
      fillOpacity: 0.07,
    }).addTo(map);
    surveyMarker = L.circleMarker(latlng, {
      radius: 4, color: "#fdfcf9", weight: 2, fillColor: "#b34a12", fillOpacity: 1,
    }).addTo(map);

    const enabled = new Set(
      [...document.querySelectorAll("#layer-toggles input:checked")].map((i) => i.dataset.layer));
    const hits = [];
    for (const [key, list] of Object.entries(features)) {
      if (!enabled.has(key)) continue;
      for (const ft of list) {
        const d = haversineMeters(center, ft);
        if (d <= radius) hits.push({ ...ft, dist: d });
      }
    }
    hits.sort((a, b) => (b.when || "").localeCompare(a.when || ""));

    const counts = {};
    for (const h of hits) counts[h.layer] = (counts[h.layer] || 0) + 1;
    const miles = radius === 400 ? "¼ mi" : radius === 800 ? "½ mi" : "1 mi";
    const label = WINDOW_LABELS[windowDays] || windowDays + " days";

    el.innerHTML = `
      <h3>Area report · ${miles} radius</h3>
      <p class="status-note">${latlng.lat.toFixed(4)}, ${latlng.lng.toFixed(4)} · trailing ${label}</p>
      <div class="report-stat-row">
        ${Object.entries(LAYER_DEFS).map(([k, def]) =>
          `<div class="report-stat"><b style="color:${def.color}">${counts[k] || 0}</b>${def.plural}</div>`).join("")}
      </div>
      ${hits.length ? `<ul class="report-list">${hits.slice(0, 30).map((h) => `
        <li>
          <div class="rl-top">
            <span class="rl-tag" style="color:${LAYER_DEFS[h.layer].color}">${LAYER_DEFS[h.layer].label}</span>
            <span class="rl-when">${Views.fmtDate(h.when, { withYear: false })} · ${Math.round(h.dist)} m</span>
          </div>
          <div>${Views.esc(h.title)}</div>
          ${h.sub ? `<div class="rl-desc">${Views.esc(String(h.sub).slice(0, 90))}</div>` : ""}
        </li>`).join("")}</ul>
        ${hits.length > 30 ? `<p class="status-note">…and ${hits.length - 30} more inside the circle. Zoom in and use marker popups for detail.</p>` : ""}`
      : `<p class="hint">Nothing in the last ${label} falls inside this circle. Try a larger radius, a longer activity window, or another spot.</p>`}`;
  }

  function init() {
    if (initialized) return;
    initialized = true;
    if (typeof L === "undefined") {
      document.getElementById("map").innerHTML =
        '<div class="error-block" style="margin:20px">The map library (Leaflet, loaded from unpkg.com) could not be fetched — check your network and reload. Permit, zoning, and council feeds on the other tabs are unaffected.</div>';
      return;
    }
    map = L.map("map", { zoomControl: true }).setView(GW_CONFIG.map.center, GW_CONFIG.map.zoom);
    L.tileLayer(GW_CONFIG.map.tiles.url, {
      attribution: GW_CONFIG.map.tiles.attribution,
      maxZoom: 19,
    }).addTo(map);
    canvasRenderer = L.canvas({ padding: 0.4 });

    for (const key of Object.keys(LAYER_DEFS)) {
      layerGroups[key] = L.layerGroup().addTo(map);
    }

    document.querySelectorAll("#layer-toggles input").forEach((cb) =>
      cb.addEventListener("change", () => {
        const g = layerGroups[cb.dataset.layer];
        if (cb.checked) g.addTo(map);
        else g.remove();
        if (surveyCircle) renderAreaReport(surveyCircle.getLatLng());
      }));

    // one shared activity window scopes every layer and the area report
    document.querySelectorAll("#window-chips .chip").forEach((chip) =>
      chip.addEventListener("click", () => {
        const days = +chip.dataset.days;
        if (days === windowDays) return;
        windowDays = days;
        document.querySelectorAll("#window-chips .chip").forEach((c) => {
          const on = c === chip;
          c.classList.toggle("on", on);
          c.setAttribute("aria-checked", on);
        });
        loadLayers();
      }));

    document.getElementById("radius-select").addEventListener("change", () => {
      if (surveyCircle) renderAreaReport(surveyCircle.getLatLng());
    });

    map.on("click", (e) => renderAreaReport(e.latlng));

    loadLayers();
  }

  /* Leaflet mis-sizes a map that boots in a hidden tab — re-measure on show. */
  function onShow() {
    init();
    setTimeout(() => map && map.invalidateSize(), 60);
  }

  return { onShow };
})();
