/* ============================================================
   Groundwork ATX — data layer
   Two tiers, transparently:
     1. LIVE  — the Socrata Open Data API (SODA), cached in
        sessionStorage for a few minutes.
     2. SNAPSHOT — data/*.json files committed nightly by the
        GitHub Action (~12am Austin). Used automatically whenever
        a live call fails, so the dashboard still works when the
        city portal is down, rate-limiting, or unreachable.
   Field names are resolved at runtime against dataset metadata
   (or the snapshot manifest), so city-side schema drift degrades
   gracefully instead of breaking.
   ============================================================ */

const SODA = (() => {
  const TTL_MS = (GW_CONFIG.cacheTTLminutes || 10) * 60 * 1000;
  const resolved = {};   // datasetKey -> { fieldRole: realFieldName|null }
  const snapCache = {};  // fileName -> parsed JSON | null

  /* ---- status: are we live, or reading last night's snapshot? ---- */
  const status = { snapshotUsed: false, liveUsed: false, snapshotAt: null };
  let statusCb = null;
  function onStatus(cb) { statusCb = cb; }
  function noteLive() { status.liveUsed = true; if (statusCb) statusCb(status); }
  async function noteSnapshot() {
    status.snapshotUsed = true;
    if (!status.snapshotAt) {
      const meta = await snapshot("meta");
      status.snapshotAt = meta && meta.fetchedAt ? meta.fetchedAt : null;
    }
    if (statusCb) statusCb(status);
  }

  /* ---- live fetch with session cache ---- */
  function cacheGet(key) {
    try {
      const raw = sessionStorage.getItem("gw:" + key);
      if (!raw) return null;
      const { t, v } = JSON.parse(raw);
      if (Date.now() - t > TTL_MS) return null;
      return v;
    } catch { return null; }
  }
  function cacheSet(key, v) {
    try { sessionStorage.setItem("gw:" + key, JSON.stringify({ t: Date.now(), v })); }
    catch { /* storage full or unavailable — caching is best-effort */ }
  }

  async function fetchJSON(url) {
    const cached = cacheGet(url);
    if (cached) { noteLive(); return cached; }
    const headers = { Accept: "application/json" };
    if (GW_CONFIG.appToken) headers["X-App-Token"] = GW_CONFIG.appToken;
    const res = await fetch(url, { headers });
    if (!res.ok) throw new Error(`HTTP ${res.status} from ${url}`);
    const data = await res.json();
    cacheSet(url, data);
    noteLive();
    return data;
  }

  /* ---- snapshot files (nightly, committed by the Action) ---- */
  async function snapshot(name) {
    if (name in snapCache) return snapCache[name];
    try {
      const res = await fetch(`data/${name}.json`, { cache: "no-store" });
      snapCache[name] = res.ok ? await res.json() : null;
    } catch { snapCache[name] = null; }
    return snapCache[name];
  }

  /* ---- field resolution: metadata → sample row → manifest ---- */
  async function resolveFields(dsKey) {
    if (resolved[dsKey]) return resolved[dsKey];
    const ds = GW_CONFIG.datasets[dsKey];
    let names = [];
    try {
      const meta = await fetchJSON(`${GW_CONFIG.portal}/api/views/${ds.id}.json`);
      names = (meta.columns || [])
        .map((c) => c.fieldName)
        .filter((n) => n && !n.startsWith(":"));
    } catch { /* try the next tier */ }
    if (!names.length) {
      try {
        const sample = await fetchJSON(`${GW_CONFIG.portal}/resource/${ds.id}.json?$limit=1`);
        names = sample[0] ? Object.keys(sample[0]) : [];
      } catch { /* try the snapshot manifest */ }
    }
    if (!names.length) {
      const meta = await snapshot("meta");
      const stored = meta && meta.datasets && meta.datasets[dsKey] && meta.datasets[dsKey].fields;
      if (stored) {
        resolved[dsKey] = { _all: Object.values(stored).filter(Boolean), ...stored };
        return resolved[dsKey];
      }
    }
    const map = { _all: names };
    for (const [role, candidates] of Object.entries(ds.fields)) {
      map[role] =
        candidates.find((c) => names.includes(c)) ||
        names.find((n) => candidates.some((c) => n.includes(c) || c.includes(n))) ||
        null;
    }
    if (!map.date) map.date = names.find((n) => /(^|_)date|date($|_)/.test(n)) || null;
    if (!map.lat) map.lat = names.find((n) => /latitude/.test(n)) || null;
    if (!map.lon) map.lon = names.find((n) => /longitude/.test(n)) || null;
    resolved[dsKey] = map;
    return map;
  }

  /* ---- SoQL helpers ---- */
  function soqlDate(d) { return d.toISOString().slice(0, 10); }
  function daysAgo(n) { const d = new Date(); d.setDate(d.getDate() - n); return d; }
  function q(s) { return `'${String(s).replace(/'/g, "''")}'`; }

  function resourceURL(dsKey, params) {
    const ds = GW_CONFIG.datasets[dsKey];
    const qs = Object.entries(params)
      .filter(([, v]) => v !== null && v !== undefined && v !== "")
      .map(([k, v]) => `$${k}=${encodeURIComponent(v)}`)
      .join("&");
    return `${GW_CONFIG.portal}/resource/${ds.id}.json?${qs}`;
  }

  async function query(dsKey, params) {
    return fetchJSON(resourceURL(dsKey, params));
  }

  /* ---- snapshot-side computation primitives ----
     Snapshot fallbacks can't run SoQL, so callers provide
     `snapFilter(row, fields)` predicates mirroring their where-
     clauses; windows are applied on the resolved date field
     (Socrata floating timestamps compare lexicographically). */

  function inWindow(row, f, fromDaysAgo, toDaysAgo) {
    if (!f.date) return true;
    const v = String(row[f.date] || "");
    if (v < soqlDate(daysAgo(fromDaysAgo))) return false;
    if (toDaysAgo > 0 && v >= soqlDate(daysAgo(toDaysAgo))) return false;
    return true;
  }

  async function snapRows(dsKey, f, days, snapFilter, toDaysAgo = 0) {
    const rows = await snapshot(dsKey + "_recent");
    if (!rows) return null;
    await noteSnapshot();
    return rows.filter((r) => inWindow(r, f, days, toDaysAgo) && (!snapFilter || snapFilter(r, f)));
  }

  /* ================= public query API =================
     Each function tries live SODA first and falls back to the
     snapshot; if both fail, the live error propagates so views
     can render their error card. */

  /* Rows from the last `days` days, newest first. */
  async function recent(dsKey, days, { where = [], limit = 1000, snapFilter = null } = {}) {
    const f = await resolveFields(dsKey);
    try {
      const clauses = [...where.filter(Boolean)];
      if (f.date) clauses.push(`${f.date} >= '${soqlDate(daysAgo(days))}'`);
      return await query(dsKey, {
        where: clauses.join(" AND ") || undefined,
        order: f.date ? `${f.date} DESC` : undefined,
        limit,
      });
    } catch (e) {
      const rows = await snapRows(dsKey, f, days, snapFilter);
      if (!rows) throw e;
      if (f.date) rows.sort((a, b) => String(b[f.date] || "").localeCompare(String(a[f.date] || "")));
      return rows.slice(0, limit);
    }
  }

  /* Daily counts, ascending: [{d, n}]. `snap` names a precomputed
     daily-count snapshot file; otherwise the fallback recomputes
     from the dataset's record snapshot (limited to its window). */
  async function dailyCounts(dsKey, days, extraWhere = [], { snap = null, snapFilter = null } = {}) {
    const f = await resolveFields(dsKey);
    if (!f.date) return [];
    try {
      const clauses = [`${f.date} >= '${soqlDate(daysAgo(days))}'`, ...extraWhere.filter(Boolean)];
      const rows = await query(dsKey, {
        select: `date_trunc_ymd(${f.date}) AS d, count(*) AS n`,
        where: clauses.join(" AND "),
        group: "d",
        order: "d",
        limit: days + 10,
      });
      return rows.filter((r) => r.d).map((r) => ({ d: r.d.slice(0, 10), n: +r.n }));
    } catch (e) {
      if (snap) {
        const pre = await snapshot(snap);
        if (pre) {
          await noteSnapshot();
          const cut = soqlDate(daysAgo(days));
          return pre.filter((r) => r.d >= cut);
        }
      }
      const rows = await snapRows(dsKey, f, days, snapFilter);
      if (!rows) throw e;
      const byDay = new Map();
      for (const r of rows) {
        const d = String(r[f.date] || "").slice(0, 10);
        if (d) byDay.set(d, (byDay.get(d) || 0) + 1);
      }
      return [...byDay.keys()].sort().map((d) => ({ d, n: byDay.get(d) }));
    }
  }

  /* Count within a day-window (toDaysAgo 0 = through today). */
  async function countBetween(dsKey, fromDaysAgo, toDaysAgo, extraWhere = [], { snapFilter = null } = {}) {
    const f = await resolveFields(dsKey);
    if (!f.date) return null;
    try {
      const clauses = [
        `${f.date} >= '${soqlDate(daysAgo(fromDaysAgo))}'`,
        ...(toDaysAgo > 0 ? [`${f.date} < '${soqlDate(daysAgo(toDaysAgo))}'`] : []),
        ...extraWhere.filter(Boolean),
      ];
      const rows = await query(dsKey, { select: "count(*) AS n", where: clauses.join(" AND ") });
      return rows[0] ? +rows[0].n : null;
    } catch (e) {
      const rows = await snapRows(dsKey, f, fromDaysAgo, snapFilter, toDaysAgo);
      if (!rows) throw e;
      return rows.length;
    }
  }

  /* Sum of a numeric field within a day-window. */
  async function sumBetween(dsKey, sumRole, fromDaysAgo, toDaysAgo, extraWhere = [], { snapFilter = null } = {}) {
    const f = await resolveFields(dsKey);
    const field = f[sumRole];
    if (!f.date || !field) return null;
    try {
      const clauses = [
        `${f.date} >= '${soqlDate(daysAgo(fromDaysAgo))}'`,
        ...(toDaysAgo > 0 ? [`${f.date} < '${soqlDate(daysAgo(toDaysAgo))}'`] : []),
        ...extraWhere.filter(Boolean),
      ];
      const rows = await query(dsKey, { select: `sum(${field}) AS v`, where: clauses.join(" AND ") });
      return rows[0] && rows[0].v != null ? +rows[0].v : null;
    } catch (e) {
      const rows = await snapRows(dsKey, f, fromDaysAgo, snapFilter, toDaysAgo);
      if (!rows) throw e;
      return rows.reduce((s, r) => s + (+r[field] || 0), 0);
    }
  }

  /* Group by a field over a window: [{k, n, v?}] sorted by v (or n) desc. */
  async function groupBy(dsKey, roleOrField, days, { extraWhere = [], sumRole = null, limit = 12, snapFilter = null } = {}) {
    const f = await resolveFields(dsKey);
    const field = f[roleOrField] || roleOrField;
    if (!f.date || !field) return [];
    const sumField = sumRole ? f[sumRole] : null;
    try {
      const select = sumField
        ? `${field} AS k, count(*) AS n, sum(${sumField}) AS v`
        : `${field} AS k, count(*) AS n`;
      const rows = await query(dsKey, {
        select,
        where: [`${f.date} >= '${soqlDate(daysAgo(days))}'`, ...extraWhere.filter(Boolean)].join(" AND "),
        group: "k",
        order: sumField ? "v DESC" : "n DESC",
        limit,
      });
      return rows.filter((r) => r.k).map((r) => ({ k: r.k, n: +r.n, v: r.v != null ? +r.v : null }));
    } catch (e) {
      const rows = await snapRows(dsKey, f, days, snapFilter);
      if (!rows) throw e;
      const acc = new Map();
      for (const r of rows) {
        const k = r[field];
        if (!k) continue;
        const cur = acc.get(k) || { k, n: 0, v: 0 };
        cur.n++;
        if (sumField) cur.v += +r[sumField] || 0;
        acc.set(k, cur);
      }
      return [...acc.values()]
        .map((r) => ({ ...r, v: sumField ? r.v : null }))
        .sort((a, b) => (sumField ? b.v - a.v : b.n - a.n))
        .slice(0, limit);
    }
  }

  /* Top rows by a numeric field within a window. */
  async function topBy(dsKey, sortRole, days, { extraWhere = [], limit = 7, snapFilter = null } = {}) {
    const f = await resolveFields(dsKey);
    const field = f[sortRole];
    if (!f.date || !field) return [];
    try {
      return await query(dsKey, {
        where: [
          `${f.date} >= '${soqlDate(daysAgo(days))}'`,
          `${field} > 0`,
          ...extraWhere.filter(Boolean),
        ].join(" AND "),
        order: `${field} DESC`,
        limit,
      });
    } catch (e) {
      const rows = await snapRows(dsKey, f, days, snapFilter);
      if (!rows) throw e;
      return rows
        .filter((r) => +r[field] > 0)
        .sort((a, b) => +b[field] - +a[field])
        .slice(0, limit);
    }
  }

  return {
    resolveFields, recent, dailyCounts, countBetween, sumBetween, groupBy, topBy,
    query, q, soqlDate, daysAgo, onStatus, status,
  };
})();
