/* ============================================================
   Groundwork ATX — views
   Overview KPIs & charts, permit pipeline, zoning cases, and
   the council-watch legislation feed. All data arrives via the
   SODA layer; every renderer degrades to an explanatory error
   card if its dataset is unreachable.
   ============================================================ */

const Views = (() => {
  /* ---------- tiny utilities ---------- */
  const $ = (sel) => document.querySelector(sel);

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function parseDate(v) {
    if (!v) return null;
    const d = new Date(String(v).slice(0, 10) + "T12:00:00");
    return isNaN(d) ? null : d;
  }

  function fmtDate(v, { withYear = true } = {}) {
    const d = v instanceof Date ? v : parseDate(v);
    if (!d) return "—";
    return d.toLocaleDateString("en-US", {
      month: "short", day: "numeric", ...(withYear ? { year: "numeric" } : {}),
    });
  }

  function relDays(v) {
    const d = v instanceof Date ? v : parseDate(v);
    if (!d) return "";
    const diff = Math.round((d - new Date().setHours(12, 0, 0, 0)) / 86400000);
    if (diff === 0) return "today";
    if (diff === 1) return "tomorrow";
    if (diff === -1) return "yesterday";
    return diff > 0 ? `in ${diff}d` : `${-diff}d ago`;
  }

  function loading(el, label) {
    el.innerHTML = `<p class="loading-block">${esc(label || "Loading live data…")}</p>`;
  }

  function errorBlock(el, ds, err) {
    const page = GW_CONFIG.datasets[ds]?.page || GW_CONFIG.portal;
    el.innerHTML = `<div class="error-block">Couldn't reach the <a href="${page}" target="_blank" rel="noopener">${esc(GW_CONFIG.datasets[ds]?.name || ds)}</a> feed (${esc(err.message)}). The city portal may be busy — try Refresh in a minute.</div>`;
  }

  /* Bucket daily counts into ISO-week sums (last `weeks`). */
  function weeklyBuckets(daily, weeks) {
    const byWeek = new Map();
    for (const { d, n } of daily) {
      const date = parseDate(d);
      if (!date) continue;
      const monday = new Date(date);
      monday.setDate(date.getDate() - ((date.getDay() + 6) % 7));
      const key = monday.toISOString().slice(0, 10);
      byWeek.set(key, (byWeek.get(key) || 0) + n);
    }
    let keys = [...byWeek.keys()].sort();
    // drop the current partial week — it reads as a fake collapse in trend charts
    const thisMonday = new Date();
    thisMonday.setDate(thisMonday.getDate() - ((thisMonday.getDay() + 6) % 7));
    const curKey = thisMonday.toISOString().slice(0, 10);
    if (keys.length > 1) keys = keys.filter((k) => k < curKey);
    const out = keys.map((k) => ({
      label: "Week of " + fmtDate(k, { withYear: false }),
      short: fmtDate(k, { withYear: false }),
      value: byWeek.get(k),
      key: k,
    }));
    return out.slice(-weeks);
  }

  function whereCommercial(f) {
    return f.classMapped ? `${f.classMapped} = ${SODA.q("Commercial")}` : null;
  }

  /* Snapshot-side twin of whereCommercial — same semantics, in JS. */
  function snapCommercial(r, f) {
    return !f.classMapped || r[f.classMapped] === "Commercial";
  }

  /* ================= OVERVIEW ================= */

  const KPI_DEFS = [
    { id: "kpi-permits", label: "Commercial permits · 30d" },
    { id: "kpi-valuation", label: "Commercial valuation · 30d" },
    { id: "kpi-new", label: "New commercial buildings · 30d" },
    { id: "kpi-co", label: "Commercial C of O · 30d" },
    { id: "kpi-zoning", label: "Zoning cases · 90d" },
    { id: "kpi-council", label: "CRE council items · ±90d" },
  ];

  function tileShell() {
    $("#kpi-row").innerHTML = KPI_DEFS.map((k) => `
      <div class="stat-tile loading" id="${k.id}">
        <div class="stat-label">${k.label}</div>
        <div class="stat-value">·&nbsp;·&nbsp;·</div>
        <div class="stat-delta flat"></div>
        <div class="stat-spark"></div>
      </div>`).join("");
  }

  function setTile(id, { value, delta, deltaLabel, note, spark, sparkColor }) {
    const t = document.getElementById(id);
    if (!t) return;
    t.classList.remove("loading");
    t.querySelector(".stat-value").textContent = value;
    const d = t.querySelector(".stat-delta");
    if (delta == null) {
      d.textContent = note || "";
      d.className = "stat-delta flat";
    } else {
      const dir = delta > 0.001 ? "up" : delta < -0.001 ? "down" : "flat";
      const arrow = dir === "up" ? "▲" : dir === "down" ? "▼" : "◆";
      d.className = "stat-delta " + dir;
      d.textContent = `${arrow} ${Math.abs(delta * 100).toFixed(0)}% ${deltaLabel || "vs prior 30d"}`;
    }
    if (spark && spark.length > 1) {
      Charts.sparkline(t.querySelector(".stat-spark"), spark, { color: sparkColor || Charts.PAL.s1 });
    }
  }

  function failTile(id) {
    const t = document.getElementById(id);
    if (!t) return;
    t.classList.remove("loading");
    t.querySelector(".stat-value").textContent = "—";
    t.querySelector(".stat-delta").textContent = "feed unavailable";
  }

  async function renderOverview() {
    tileShell();
    updateCouncilTile();
    const velocityEl = $("#chart-velocity"), classEl = $("#chart-class"),
          valEl = $("#chart-valuation"), topEl = $("#top-projects");
    [velocityEl, classEl, valEl, topEl].forEach((el) => loading(el));

    /* --- permits: tiles + velocity + class comparison --- */
    (async () => {
      try {
        const f = await SODA.resolveFields("permits");
        const com = whereCommercial(f);

        const [cur, prev, daily] = await Promise.all([
          SODA.countBetween("permits", 30, 0, [com], { snapFilter: snapCommercial }),
          SODA.countBetween("permits", 60, 30, [com], { snapFilter: snapCommercial }),
          SODA.dailyCounts("permits", 26 * 7 + 7, [com], { snap: "permits_daily_com" }),
        ]);
        const weeks = weeklyBuckets(daily, 26);
        setTile("kpi-permits", {
          value: Charts.comma(cur),
          delta: prev ? (cur - prev) / prev : null,
          spark: weeks.slice(-12).map((w) => w.value),
        });
        Charts.columnChart(velocityEl, weeks, { tipLabel: "Permits issued: " });

        // residential line for comparison
        let resWeeks = [];
        if (f.classMapped) {
          const resDaily = await SODA.dailyCounts("permits", 26 * 7 + 7,
            [`${f.classMapped} = ${SODA.q("Residential")}`], { snap: "permits_daily_res" });
          resWeeks = weeklyBuckets(resDaily, 26);
        }
        const align = (ws) => weeks.map((w) => {
          const m = ws.find((x) => x.key === w.key);
          return { label: w.label, short: w.short, value: m ? m.value : 0 };
        });
        const series = [{ name: "Commercial", color: Charts.PAL.s1, points: weeks }];
        if (resWeeks.length) series.push({ name: "Residential", color: Charts.PAL.s2, points: align(resWeeks) });
        Charts.lineChart(classEl, series);

        // new commercial buildings tile (work class = New, building permits)
        if (f.workClass && f.type) {
          const newBP = [com, `${f.workClass} = ${SODA.q("New")}`, `${f.type} = ${SODA.q("BP")}`];
          const snapNewBP = (r, ff) => snapCommercial(r, ff) && r[ff.workClass] === "New" && r[ff.type] === "BP";
          const [nb, nbPrev] = await Promise.all([
            SODA.countBetween("permits", 30, 0, newBP, { snapFilter: snapNewBP }),
            SODA.countBetween("permits", 60, 30, newBP, { snapFilter: snapNewBP }),
          ]);
          setTile("kpi-new", {
            value: Charts.comma(nb),
            delta: nbPrev ? (nb - nbPrev) / nbPrev : null,
            note: "ground-up building permits",
          });
        } else {
          setTile("kpi-new", { value: "—", note: "work-class field unavailable" });
        }
      } catch (e) {
        ["kpi-permits", "kpi-new"].forEach(failTile);
        errorBlock(velocityEl, "permits", e);
        errorBlock(classEl, "permits", e);
      }
    })();

    /* --- valuation chart + tile + top projects --- */
    (async () => {
      try {
        const f = await SODA.resolveFields("permits");
        const com = whereCommercial(f);
        if (!f.valuation) {
          setTile("kpi-valuation", { value: "—", note: "valuation field unavailable" });
          const byType = await SODA.groupBy("permits", "typeDesc", 30, { extraWhere: [com], limit: 8, snapFilter: snapCommercial });
          Charts.hBarChart(valEl, byType.map((r) => ({ label: r.k, value: r.n })), { tipFormat: (v) => Charts.comma(v) + " permits" });
        } else {
          const [v, pv] = await Promise.all([
            SODA.sumBetween("permits", "valuation", 30, 0, [com], { snapFilter: snapCommercial }),
            SODA.sumBetween("permits", "valuation", 60, 30, [com], { snapFilter: snapCommercial }),
          ]);
          setTile("kpi-valuation", {
            value: Charts.money(v),
            delta: pv ? (v - pv) / pv : null,
          });

          const byType = await SODA.groupBy("permits", "typeDesc", 30, { extraWhere: [com], sumRole: "valuation", limit: 8, snapFilter: snapCommercial });
          const withVal = byType.filter((r) => r.v);
          if (withVal.length >= 2) {
            Charts.hBarChart(valEl,
              withVal.map((r) => ({ label: r.k, value: r.v, extra: `${Charts.comma(r.n)} permits` })),
              { format: Charts.money });
          } else {
            // valuation is only declared on building permits — count volume is the honest comparison
            const sub = valEl.closest(".card")?.querySelector(".card-sub");
            if (sub) sub.textContent = "Commercial permits issued by type, trailing 30 days (valuation is only declared on building permits)";
            Charts.hBarChart(valEl,
              byType.map((r) => ({ label: r.k, value: r.n, extra: r.v ? Charts.money(r.v) + " declared" : null })),
              { tipFormat: (v) => Charts.comma(v) + " permits" });
          }
        }

        // top projects table — phased projects file several permits at one
        // address with the same declared total, so dedupe by address
        let top = f.valuation
          ? await SODA.topBy("permits", "valuation", 30, { extraWhere: [com], limit: 24, snapFilter: snapCommercial })
          : [];
        const seenAddr = new Set();
        top = top.filter((r) => {
          const key = String(r[f.address] || Math.random()).trim().toUpperCase();
          if (seenAddr.has(key)) return false;
          seenAddr.add(key);
          return true;
        }).slice(0, 7);
        if (!top.length) {
          topEl.innerHTML = '<p class="chart-empty">No valuation data in this window.</p>';
        } else {
          topEl.innerHTML = `<table class="mini-table">
            <thead><tr><th>Project</th><th>Type</th><th>Valuation</th></tr></thead>
            <tbody>${top.map((r) => {
              const url = f.link && r[f.link] && (r[f.link].url || r[f.link]);
              const title = esc(r[f.desc] || r[f.address] || "Untitled project");
              return `<tr>
                <td>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener">${title}</a>` : title}
                  <div class="addr">${esc(r[f.address] || "")}${f.applicant && r[f.applicant] ? " · " + esc(r[f.applicant]) : ""}</div></td>
                <td>${esc(r[f.typeDesc] || "")}</td>
                <td class="money">${Charts.money(+r[f.valuation])}</td>
              </tr>`;
            }).join("")}</tbody></table>`;
        }
      } catch (e) {
        failTile("kpi-valuation");
        errorBlock(valEl, "permits", e);
        errorBlock(topEl, "permits", e);
      }
    })();

    /* --- certificates of occupancy tile (commercial only) --- */
    (async () => {
      try {
        const f = await SODA.resolveFields("co");
        const com = whereCommercial(f);
        const [cur, prev, daily] = await Promise.all([
          SODA.countBetween("co", 30, 0, [com], { snapFilter: snapCommercial }),
          SODA.countBetween("co", 60, 30, [com], { snapFilter: snapCommercial }),
          SODA.dailyCounts("co", 12 * 7, [com], { snapFilter: snapCommercial }),
        ]);
        setTile("kpi-co", {
          value: Charts.comma(cur),
          delta: prev ? (cur - prev) / prev : null,
          spark: weeklyBuckets(daily, 12).map((w) => w.value),
          sparkColor: Charts.PAL.s7,
        });
      } catch { failTile("kpi-co"); }
    })();

    /* --- zoning tile --- */
    (async () => {
      try {
        const [cur, prev] = await Promise.all([
          SODA.countBetween("zoning", 90, 0),
          SODA.countBetween("zoning", 180, 90),
        ]);
        setTile("kpi-zoning", {
          value: Charts.comma(cur),
          delta: prev ? (cur - prev) / prev : null,
          deltaLabel: "vs prior 90d",
        });
      } catch { failTile("kpi-zoning"); }
    })();

    /* council tile is filled by renderCouncil() once items load */
  }

  /* ================= PERMIT PIPELINE ================= */

  const permitState = { rows: [], shown: 100, f: null };

  async function loadPermitFeed() {
    const el = $("#permit-table");
    loading(el, "Pulling the live permit feed…");
    try {
      const f = await SODA.resolveFields("permits");
      permitState.f = f;
      const windowDays = +$("#pf-window").value;
      const cls = $("#pf-class").value;
      const where = [];
      if (cls && f.classMapped) where.push(`${f.classMapped} = ${SODA.q(cls)}`);
      permitState.rows = await SODA.recent("permits", windowDays, {
        where, limit: windowDays > 30 ? 5000 : 2500,
        snapFilter: cls ? (r, ff) => !ff.classMapped || r[ff.classMapped] === cls : null,
      });
      permitState.shown = 100;
      hydratePermitFilterOptions();
      renderPermitTable();
    } catch (e) {
      errorBlock(el, "permits", e);
    }
  }

  function hydratePermitFilterOptions() {
    const f = permitState.f;
    const typeSel = $("#pf-type"), distSel = $("#pf-district");
    const keepT = typeSel.value, keepD = distSel.value;
    const types = [...new Set(permitState.rows.map((r) => r[f.typeDesc]).filter(Boolean))].sort();
    typeSel.innerHTML = '<option value="">All permit types</option>' +
      types.map((t) => `<option${t === keepT ? " selected" : ""}>${esc(t)}</option>`).join("");
    const dists = [...new Set(permitState.rows.map((r) => r[f.district]).filter(Boolean))]
      .sort((a, b) => +a - +b);
    distSel.innerHTML = '<option value="">All districts</option>' +
      dists.map((d) => `<option value="${esc(d)}"${d === keepD ? " selected" : ""}>District ${esc(d)}</option>`).join("");
  }

  function filteredPermits() {
    const f = permitState.f;
    const type = $("#pf-type").value, dist = $("#pf-district").value,
          qstr = $("#pf-search").value.trim().toLowerCase();
    return permitState.rows.filter((r) => {
      if (type && r[f.typeDesc] !== type) return false;
      if (dist && String(r[f.district]) !== dist) return false;
      if (qstr) {
        const hay = [r[f.desc], r[f.address], r[f.applicant], r[f.contractor], r[f.number]]
          .map((v) => String(v ?? "").toLowerCase()).join(" ");
        if (!hay.includes(qstr)) return false;
      }
      return true;
    });
  }

  function renderPermitTable() {
    const el = $("#permit-table");
    const f = permitState.f;
    const rows = filteredPermits();
    if (!rows.length) {
      el.innerHTML = '<p class="chart-empty">No permits match these filters.</p>';
      return;
    }
    const slice = rows.slice(0, permitState.shown);
    el.innerHTML = `<table class="data-table">
      <thead><tr>
        <th>Issued</th><th>Type</th><th>Project</th><th>Valuation</th><th>Status</th><th>District</th>
      </tr></thead>
      <tbody>${slice.map((r) => {
        const url = f.link && r[f.link] && (r[f.link].url || r[f.link]);
        const num = esc(r[f.number] || "");
        return `<tr>
          <td class="num">${fmtDate(r[f.date])}</td>
          <td><span class="type-pill">${esc(r[f.typeDesc] || r[f.type] || "—")}</span>
            ${r[f.workClass] ? `<div class="status-note">${esc(r[f.workClass])}</div>` : ""}</td>
          <td>${esc(r[f.desc] || "").slice(0, 140) || "<em>No description</em>"}
            <div class="status-note">${esc(r[f.address] || "")}
              ${url ? ` · <a href="${esc(url)}" target="_blank" rel="noopener">${num || "record"}</a>` : num ? " · " + num : ""}</div></td>
          <td class="num">${f.valuation && r[f.valuation] ? Charts.money(+r[f.valuation]) : "—"}</td>
          <td>${esc(r[f.status] || "—")}</td>
          <td class="num">${esc(r[f.district] || "—")}</td>
        </tr>`;
      }).join("")}</tbody></table>
      ${rows.length > permitState.shown
        ? `<button class="load-more" id="pf-more">Show more (${Charts.comma(rows.length - permitState.shown)} remaining)</button>`
        : `<p class="status-note" style="text-align:center;margin-top:12px">${Charts.comma(rows.length)} permits shown</p>`}`;
    const more = $("#pf-more");
    if (more) more.addEventListener("click", () => { permitState.shown += 200; renderPermitTable(); });
  }

  function wirePermitFilters() {
    $("#pf-window").addEventListener("change", loadPermitFeed);
    $("#pf-class").addEventListener("change", loadPermitFeed);
    $("#pf-type").addEventListener("change", () => { permitState.shown = 100; renderPermitTable(); });
    $("#pf-district").addEventListener("change", () => { permitState.shown = 100; renderPermitTable(); });
    let t;
    $("#pf-search").addEventListener("input", () => {
      clearTimeout(t);
      t = setTimeout(() => { permitState.shown = 100; renderPermitTable(); }, 200);
    });
  }

  /* ================= ZONING CASES ================= */

  const zoningState = { rows: [], f: null };

  async function renderZoning() {
    const el = $("#zoning-list");
    loading(el, "Loading zoning cases…");
    try {
      const f = await SODA.resolveFields("zoning");
      zoningState.f = f;
      zoningState.rows = await SODA.recent("zoning", 365, { limit: 1000 });
      drawZoning();
    } catch (e) {
      errorBlock(el, "zoning", e);
    }
  }

  function drawZoning() {
    const el = $("#zoning-list");
    const f = zoningState.f;
    const qstr = $("#zf-search").value.trim().toLowerCase();
    const rows = zoningState.rows.filter((r) =>
      !qstr || Object.values(r).some((v) => String(v ?? "").toLowerCase().includes(qstr)));
    if (!rows.length) {
      el.innerHTML = '<p class="chart-empty">No zoning cases match.</p>';
      return;
    }
    el.innerHTML = rows.slice(0, 120).map((r) => {
      const number = r[f.number] || "Zoning case";
      const title = r[f.name] || r[f.address] || "";
      const detailRows = Object.entries(r)
        .filter(([k, v]) => v != null && typeof v !== "object" && String(v).trim() !== "")
        .map(([k, v]) => `<dt>${esc(k.replace(/_/g, " "))}</dt><dd>${esc(String(v).slice(0, 220))}</dd>`)
        .join("");
      return `<div class="case-card">
        <div class="case-status">${esc(r[f.status] || "status n/a")}${f.caseType && r[f.caseType] ? " · " + esc(r[f.caseType]) : ""}</div>
        <h3>${esc(number)}</h3>
        ${title ? `<p>${esc(title)}</p>` : ""}
        ${r[f.desc] ? `<p>${esc(String(r[f.desc]).slice(0, 180))}</p>` : ""}
        ${f.fromZone && (r[f.fromZone] || r[f.toZone])
          ? `<p><b>${esc(r[f.fromZone] || "?")}</b> → <b>${esc(r[f.toZone] || "?")}</b></p>` : ""}
        <p class="case-meta">${f.date && r[f.date] ? "Filed/updated " + fmtDate(r[f.date]) : ""}
          ${f.district && r[f.district] ? " · District " + esc(r[f.district]) : ""}</p>
        <details><summary>All fields</summary><dl>${detailRows}</dl></details>
      </div>`;
    }).join("");
  }

  function wireZoningFilters() {
    let t;
    $("#zf-search").addEventListener("input", () => {
      clearTimeout(t);
      t = setTimeout(drawZoning, 200);
    });
  }

  /* ================= COUNCIL WATCH ================= */

  const councilState = { rows: [], f: null, activeKeys: new Set(GW_CONFIG.creKeywords.map((k) => k.key)) };

  function matchKeywords(text) {
    const lower = text.toLowerCase();
    return GW_CONFIG.creKeywords.filter((g) => g.terms.some((t) => lower.includes(t)));
  }

  function rowText(r) {
    return Object.values(r).filter((v) => typeof v === "string").join(" · ");
  }

  async function renderCouncil() {
    const listEl = $("#council-list");
    loading(listEl, "Loading council agenda items…");
    $("#council-links").innerHTML = GW_CONFIG.councilLinks.map((l) => `
      <li><a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.name)}</a>
        <span class="link-note">${esc(l.note)}</span></li>`).join("");
    drawChips();
    try {
      const f = await SODA.resolveFields("council");
      councilState.f = f;
      const rows = await SODA.recent("council", 999, { limit: 5000 });
      // scrub training/test rows the city leaves in the dataset
      councilState.rows = rows.filter((r) => {
        if (/^\s*TRAINING/i.test(String(r[f.title] || ""))) return false;
        if (String(r[f.item] || "").toLowerCase() === "test") return false;
        const d = parseDate(r[f.date]);
        return !(d && d.getTime() - Date.now() > 400 * 86400000);
      });
      drawCouncil();
      updateCouncilTile();
    } catch (e) {
      errorBlock(listEl, "council", e);
      failTile("kpi-council");
    }
  }

  /* Overview tile: flagged items with meetings within ±30 days.
     Called from renderCouncil() and again if the overview panel
     builds its tiles after the council feed has already loaded. */
  function updateCouncilTile() {
    const f = councilState.f;
    if (!f || !councilState.rows.length) return;
    const now = Date.now();
    const flagged = councilState.rows.filter((r) => {
      const d = parseDate(r[f.date]);
      if (!d || Math.abs(d - now) > 90 * 86400000) return false;
      return matchKeywords(rowText(r)).length > 0;
    });
    setTile("kpi-council", {
      value: Charts.comma(flagged.length),
      note: flagged.length
        ? "agenda items matching the CRE lens"
        : "none within ±90d — see Council watch for the latest",
    });
  }

  function drawChips() {
    const el = $("#keyword-chips");
    el.innerHTML = GW_CONFIG.creKeywords.map((k) =>
      `<button class="chip ${councilState.activeKeys.has(k.key) ? "on" : ""}" data-key="${k.key}">${esc(k.label)}</button>`
    ).join("");
    el.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => {
      const key = c.dataset.key;
      if (councilState.activeKeys.has(key)) councilState.activeKeys.delete(key);
      else councilState.activeKeys.add(key);
      // never allow an empty lens — that reads as "show nothing"
      if (!councilState.activeKeys.size) GW_CONFIG.creKeywords.forEach((k) => councilState.activeKeys.add(k.key));
      drawChips();
      drawCouncil();
    }));
  }

  function drawCouncil() {
    const el = $("#council-list");
    const f = councilState.f;
    if (!f) return;
    const qstr = $("#cf-search").value.trim().toLowerCase();

    const items = [];
    for (const r of councilState.rows) {
      const text = rowText(r);
      const tags = matchKeywords(text);
      if (!tags.some((t) => councilState.activeKeys.has(t.key))) continue;
      if (qstr && !text.toLowerCase().includes(qstr)) continue;
      items.push({ r, tags, date: parseDate(r[f.date]) });
    }
    if (!items.length) {
      el.innerHTML = '<p class="chart-empty">No agenda items match the current lens. Toggle more topics or clear the search.</p>';
      return;
    }

    // group by meeting date: upcoming meetings first (soonest → latest), then past (newest → oldest)
    const groups = new Map();
    for (const it of items) {
      const key = it.date ? it.date.toISOString().slice(0, 10) : "undated";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(it);
    }
    const today = new Date().toISOString().slice(0, 10);
    const keys = [...groups.keys()].filter((k) => k !== "undated");
    const upcoming = keys.filter((k) => k >= today).sort();
    const past = keys.filter((k) => k < today).sort().reverse();
    const ordered = [...upcoming, ...past, ...(groups.has("undated") ? ["undated"] : [])];

    el.innerHTML = ordered.slice(0, 14).map((k) => {
      const its = groups.get(k);
      const isUpcoming = k !== "undated" && k >= today;
      const heading = k === "undated" ? "Undated items" : fmtDate(k);
      return `<div class="agenda-group">
        <h3 class="agenda-date">${heading}
          ${isUpcoming ? '<span class="upcoming-flag">Upcoming</span>' : ""}
          <span class="status-note">${k !== "undated" ? relDays(k) : ""} · ${its.length} item${its.length > 1 ? "s" : ""}</span></h3>
        <ul class="agenda-items">${its.slice(0, 40).map(({ r, tags }) => {
          const title = r[f.title] || rowText(r).slice(0, 200);
          const url = f.link && r[f.link] && (r[f.link].url || r[f.link]);
          const text = String(title).replace(/\s+/g, " ").trim();
          return `<li>
            <p class="ai-text">${esc(text.slice(0, 320))}${text.length > 320 ? "…" : ""}</p>
            <div class="ai-meta">
              ${r[f.item] ? `<span>Item ${esc(r[f.item])}</span>` : ""}
              ${f.itemType && r[f.itemType] ? `<span>${esc(r[f.itemType])}</span>` : ""}
              ${r[f.dept] ? `<span>${esc(r[f.dept])}</span>` : ""}
              ${f.sponsor && r[f.sponsor] ? `<span>Sponsor: ${esc(r[f.sponsor])}</span>` : ""}
              ${r[f.status] ? `<span>${esc(r[f.status])}</span>` : ""}
              ${tags.slice(0, 3).map((t) => `<span class="kw-tag">${esc(t.label)}</span>`).join("")}
              ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener">backup ↗</a>` : ""}
            </div>
          </li>`;
        }).join("")}</ul>
      </div>`;
    }).join("");
  }

  function wireCouncilFilters() {
    let t;
    $("#cf-search").addEventListener("input", () => {
      clearTimeout(t);
      t = setTimeout(drawCouncil, 200);
    });
  }

  return {
    renderOverview,
    loadPermitFeed, wirePermitFilters,
    renderZoning, wireZoningFilters,
    renderCouncil, wireCouncilFilters,
    esc, fmtDate, parseDate,
  };
})();
