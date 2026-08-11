/* ============================================================
   Groundwork ATX — hand-rolled SVG charts
   Specs: thin marks (bars ≤24px, 4px rounded data-end, square
   baseline), 2px lines, ≥8px end markers with a 2px surface
   ring, hairline solid grid, selective direct labels, hover
   tooltips that enhance (a data-table twin accompanies every
   chart), text in ink tokens — never the series color.
   ============================================================ */

const Charts = (() => {
  const PAL = {
    s1: "#2a78d6", // categorical slot 1 (blue)
    s2: "#eb6834", // slot 2 (orange)
    s3: "#1baf7a", // slot 3 (aqua)
    s7: "#4a3aa7", // slot 7 (violet)
    grid: "#e3ddcf",
    axis: "#c8c1b0",
    ink: "#1e1a14",
    ink2: "#5f584c",
    muted: "#8d8577",
    deemph: "#c8c1b0",
    surface: "#fdfcf9",
  };

  const NS = "http://www.w3.org/2000/svg";
  function el(tag, attrs = {}, children = []) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    for (const c of children) node.appendChild(c);
    return node;
  }
  function txt(tag, attrs, content) {
    const t = el(tag, attrs);
    t.textContent = content;
    return t;
  }

  /* ---- shared tooltip ------------------------------------- */
  let tipEl = null;
  function tip() {
    if (!tipEl) {
      tipEl = document.createElement("div");
      tipEl.className = "gw-tooltip";
      tipEl.setAttribute("role", "status");
      document.body.appendChild(tipEl);
    }
    return tipEl;
  }
  function showTip(html, x, y) {
    const t = tip();
    t.innerHTML = html;
    t.style.display = "block";
    const r = t.getBoundingClientRect();
    const px = Math.min(x + 14, window.innerWidth - r.width - 12);
    const py = Math.max(y - r.height - 12, 8);
    t.style.left = px + "px";
    t.style.top = py + "px";
  }
  function hideTip() {
    if (tipEl) tipEl.style.display = "none";
  }

  /* ---- number formatting ---------------------------------- */
  function compact(n) {
    if (n == null || isNaN(n)) return "—";
    const abs = Math.abs(n);
    if (abs >= 1e9) return (n / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
    if (abs >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (abs >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
    return String(Math.round(n));
  }
  function money(n) {
    return n == null || isNaN(n) ? "—" : "$" + compact(n);
  }
  function comma(n) {
    return n == null || isNaN(n) ? "—" : Number(n).toLocaleString("en-US");
  }

  function niceTicks(max, count = 4) {
    if (max <= 0) return [0, 1];
    const rough = max / count;
    const mag = Math.pow(10, Math.floor(Math.log10(rough)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= rough) || mag * 10;
    const ticks = [];
    for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(Math.round(v * 100) / 100);
    if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
    return ticks;
  }

  /* ---- data-table twin (values reachable without hover) --- */
  function tableTwin(container, headers, rows) {
    const details = document.createElement("details");
    details.className = "chart-table";
    const summary = document.createElement("summary");
    summary.textContent = "Data table";
    details.appendChild(summary);
    const tbl = document.createElement("table");
    tbl.innerHTML =
      "<thead><tr>" + headers.map((h) => `<th>${h}</th>`).join("") + "</tr></thead>" +
      "<tbody>" + rows.map((r) => "<tr>" + r.map((c) => `<td>${c}</td>`).join("") + "</tr>").join("") + "</tbody>";
    details.appendChild(tbl);
    container.appendChild(details);
  }

  function clear(container) {
    container.innerHTML = "";
  }

  /* ---- column chart (weekly counts etc.) ------------------ */
  function columnChart(container, points, { color = PAL.s1, format = comma, tipLabel = "" } = {}) {
    clear(container);
    if (!points.length) return empty(container);
    const W = container.clientWidth || 560;
    const H = 220, padL = 44, padR = 12, padT = 14, padB = 26;
    const iw = W - padL - padR, ih = H - padT - padB;
    const max = Math.max(...points.map((p) => p.value), 1);
    const ticks = niceTicks(max);
    const tmax = ticks[ticks.length - 1];
    const y = (v) => padT + ih - (v / tmax) * ih;
    const band = iw / points.length;
    const bw = Math.min(24, Math.max(4, band - 2)); // ≤24px thick, 2px surface gap minimum

    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", height: H, role: "img" });

    for (const t of ticks) {
      svg.appendChild(el("line", { x1: padL, x2: W - padR, y1: y(t), y2: y(t), stroke: PAL.grid, "stroke-width": 1 }));
      svg.appendChild(txt("text", { x: padL - 6, y: y(t) + 4, "text-anchor": "end", class: "ax" }, compact(t)));
    }
    svg.appendChild(el("line", { x1: padL, x2: W - padR, y1: y(0), y2: y(0), stroke: PAL.axis, "stroke-width": 1 }));

    const maxIdx = points.reduce((mi, p, i) => (p.value > points[mi].value ? i : mi), 0);

    points.forEach((p, i) => {
      const bx = padL + i * band + (band - bw) / 2;
      const by = y(p.value);
      const h = Math.max(0, y(0) - by);
      const r = Math.min(4, h); // 4px rounded data-end, square at the baseline
      const d = `M${bx},${y(0)} L${bx},${by + r} Q${bx},${by} ${bx + r},${by} L${bx + bw - r},${by} Q${bx + bw},${by} ${bx + bw},${by + r} L${bx + bw},${y(0)} Z`;
      svg.appendChild(el("path", { d, fill: color }));
      // generous hover hit area — the whole band
      const hit = el("rect", { x: padL + i * band, y: padT, width: band, height: ih, fill: "transparent" });
      hit.addEventListener("mousemove", (e) => showTip(`<strong>${p.label}</strong><br>${tipLabel}${format(p.value)}`, e.clientX, e.clientY));
      hit.addEventListener("mouseleave", hideTip);
      svg.appendChild(hit);
      // sparse x labels: first, last, and ~every 4th (kept clear of the last)
      const step = Math.ceil(points.length / 6);
      if (i === 0 || i === points.length - 1 || (i % step === 0 && points.length - 1 - i > step / 2)) {
        svg.appendChild(txt("text", { x: bx + bw / 2, y: H - 8, "text-anchor": "middle", class: "ax" }, p.short || p.label));
      }
      // selective direct labels: the max and the latest bar only
      if (i === maxIdx || i === points.length - 1) {
        svg.appendChild(txt("text", { x: bx + bw / 2, y: by - 6, "text-anchor": "middle", class: "val" }, compact(p.value)));
      }
    });

    container.appendChild(svg);
    tableTwin(container, ["Period", "Value"], points.map((p) => [p.label, format(p.value)]));
  }

  /* ---- multi-line chart (≤3 series) ----------------------- */
  function lineChart(container, series, { format = comma } = {}) {
    clear(container);
    const pts0 = series[0] ? series[0].points : [];
    if (!pts0.length) return empty(container);
    const W = container.clientWidth || 560;
    const H = 230, padL = 44, padR = 112, padT = 14, padB = 26;
    const iw = W - padL - padR, ih = H - padT - padB;
    const n = pts0.length;
    const max = Math.max(1, ...series.flatMap((s) => s.points.map((p) => p.value)));
    const ticks = niceTicks(max);
    const tmax = ticks[ticks.length - 1];
    const x = (i) => padL + (n === 1 ? iw / 2 : (i / (n - 1)) * iw);
    const y = (v) => padT + ih - (v / tmax) * ih;

    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", height: H, role: "img" });
    for (const t of ticks) {
      svg.appendChild(el("line", { x1: padL, x2: W - padR, y1: y(t), y2: y(t), stroke: PAL.grid, "stroke-width": 1 }));
      svg.appendChild(txt("text", { x: padL - 6, y: y(t) + 4, "text-anchor": "end", class: "ax" }, compact(t)));
    }
    svg.appendChild(el("line", { x1: padL, x2: W - padR, y1: y(0), y2: y(0), stroke: PAL.axis, "stroke-width": 1 }));

    series.forEach((s) => {
      const d = s.points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.value)}`).join(" ");
      svg.appendChild(el("path", { d, fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      const last = s.points[s.points.length - 1];
      // end marker ≥8px with 2px surface ring
      svg.appendChild(el("circle", { cx: x(n - 1), cy: y(last.value), r: 6, fill: s.color, stroke: PAL.surface, "stroke-width": 2 }));
    });
    // direct end labels only when they don't collide — otherwise the legend carries identity
    const endYs = series.map((s) => y(s.points[s.points.length - 1].value)).sort((a, b) => a - b);
    const collide = endYs.some((v, i) => i && v - endYs[i - 1] < 15);
    if (!collide) {
      series.forEach((s) => {
        const last = s.points[s.points.length - 1];
        svg.appendChild(el("circle", { cx: W - padR + 10, cy: y(last.value) - 4, r: 4, fill: s.color }));
        svg.appendChild(txt("text", { x: W - padR + 18, y: y(last.value), class: "endlbl" }, `${s.name} ${compact(last.value)}`));
      });
    }

    // sparse x labels; keep the modulo labels clear of the last one
    const step = Math.ceil(n / 5);
    pts0.forEach((p, i) => {
      if (i === 0 || i === n - 1 || (i % step === 0 && n - 1 - i > step / 2)) {
        svg.appendChild(txt("text", { x: x(i), y: H - 8, "text-anchor": "middle", class: "ax" }, p.short || p.label));
      }
    });

    // crosshair hover across all series
    const cross = el("line", { y1: padT, y2: padT + ih, stroke: PAL.axis, "stroke-width": 1, opacity: 0 });
    svg.appendChild(cross);
    const hit = el("rect", { x: padL, y: padT, width: iw, height: ih, fill: "transparent" });
    hit.addEventListener("mousemove", (e) => {
      const rect = svg.getBoundingClientRect();
      const rel = ((e.clientX - rect.left) * (W / rect.width) - padL) / iw;
      const i = Math.max(0, Math.min(n - 1, Math.round(rel * (n - 1))));
      cross.setAttribute("x1", x(i));
      cross.setAttribute("x2", x(i));
      cross.setAttribute("opacity", 1);
      const lines = series
        .map((s) => `<span class="tt-key" style="background:${s.color}"></span>${s.name}: <strong>${format(s.points[i].value)}</strong>`)
        .join("<br>");
      showTip(`<strong>${pts0[i].label}</strong><br>${lines}`, e.clientX, e.clientY);
    });
    hit.addEventListener("mouseleave", () => { cross.setAttribute("opacity", 0); hideTip(); });
    svg.appendChild(hit);

    container.appendChild(svg);

    // legend (always present for ≥2 series)
    if (series.length >= 2) {
      const leg = document.createElement("div");
      leg.className = "legend";
      leg.innerHTML = series.map((s) => `<span><i style="background:${s.color}"></i>${s.name}</span>`).join("");
      container.appendChild(leg);
    }
    tableTwin(container, ["Period", ...series.map((s) => s.name)],
      pts0.map((p, i) => [p.label, ...series.map((s) => format(s.points[i].value))]));
  }

  /* ---- horizontal bars (nominal categories, one hue) ------ */
  function hBarChart(container, items, { color = PAL.s1, format = comma, tipFormat = null } = {}) {
    clear(container);
    if (!items.length) return empty(container);
    items = [...items].sort((a, b) => b.value - a.value);
    const W = container.clientWidth || 560;
    const rowH = 34, labelW = Math.min(190, W * 0.38), padR = 64;
    const H = items.length * rowH + 8;
    const iw = W - labelW - padR;
    const max = Math.max(...items.map((d) => d.value), 1);
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", height: H, role: "img" });

    items.forEach((d, i) => {
      const cy = i * rowH + rowH / 2 + 2;
      const bw = Math.max(2, (d.value / max) * iw);
      const bh = 14; // thin marks
      const r = Math.min(4, bw);
      const bx = labelW, by = cy - bh / 2;
      const path = `M${bx},${by} L${bx + bw - r},${by} Q${bx + bw},${by} ${bx + bw},${by + r} L${bx + bw},${by + bh - r} Q${bx + bw},${by + bh} ${bx + bw - r},${by + bh} L${bx},${by + bh} Z`;
      svg.appendChild(el("path", { d: path, fill: color }));
      const name = d.label.length > 26 ? d.label.slice(0, 25) + "…" : d.label;
      svg.appendChild(txt("text", { x: labelW - 8, y: cy + 4, "text-anchor": "end", class: "cat" }, name));
      svg.appendChild(txt("text", { x: bx + bw + 8, y: cy + 4, class: "val" }, format(d.value)));
      const hit = el("rect", { x: 0, y: i * rowH, width: W, height: rowH, fill: "transparent" });
      hit.addEventListener("mousemove", (e) =>
        showTip(`<strong>${d.label}</strong><br>${(tipFormat || format)(d.value)}${d.extra ? "<br>" + d.extra : ""}`, e.clientX, e.clientY));
      hit.addEventListener("mouseleave", hideTip);
      svg.appendChild(hit);
    });

    container.appendChild(svg);
    tableTwin(container, ["Category", "Value"], items.map((d) => [d.label, format(d.value)]));
  }

  /* ---- stat-tile sparkline -------------------------------- */
  function sparkline(container, values, { color = PAL.s1 } = {}) {
    clear(container);
    if (!values.length) return;
    const W = 120, H = 34, pad = 4;
    const max = Math.max(...values, 1), min = Math.min(...values, 0);
    const x = (i) => pad + (i / (values.length - 1)) * (W - pad * 2);
    const y = (v) => H - pad - ((v - min) / (max - min || 1)) * (H - pad * 2);
    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, "aria-hidden": "true" });
    const d = values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" ");
    svg.appendChild(el("path", { d, fill: "none", stroke: PAL.deemph, "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round" }));
    const li = values.length - 1;
    svg.appendChild(el("circle", { cx: x(li), cy: y(values[li]), r: 4, fill: color, stroke: PAL.surface, "stroke-width": 2 }));
    container.appendChild(svg);
  }

  function empty(container) {
    const d = document.createElement("p");
    d.className = "chart-empty";
    d.textContent = "No data returned for this window.";
    container.appendChild(d);
  }

  return { PAL, columnChart, lineChart, hBarChart, sparkline, compact, money, comma, showTip, hideTip };
})();
