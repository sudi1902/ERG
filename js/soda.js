/* ============================================================
   Groundwork ATX — SODA data layer
   Small client for the Socrata Open Data API with:
   • runtime field-name resolution against dataset metadata
   • sessionStorage caching with a TTL
   • SoQL helpers that only use fields that actually exist
   ============================================================ */

const SODA = (() => {
  const TTL_MS = (GW_CONFIG.cacheTTLminutes || 10) * 60 * 1000;
  const resolved = {}; // datasetKey -> { fieldRole: realFieldName|null }

  function cacheGet(key) {
    try {
      const raw = sessionStorage.getItem("gw:" + key);
      if (!raw) return null;
      const { t, v } = JSON.parse(raw);
      if (Date.now() - t > TTL_MS) return null;
      return v;
    } catch {
      return null;
    }
  }

  function cacheSet(key, v) {
    try {
      sessionStorage.setItem("gw:" + key, JSON.stringify({ t: Date.now(), v }));
    } catch {
      /* storage full or unavailable — caching is best-effort */
    }
  }

  async function fetchJSON(url) {
    const cached = cacheGet(url);
    if (cached) return cached;
    const headers = { Accept: "application/json" };
    if (GW_CONFIG.appToken) headers["X-App-Token"] = GW_CONFIG.appToken;
    const res = await fetch(url, { headers });
    if (!res.ok) throw new Error(`HTTP ${res.status} from ${url}`);
    const data = await res.json();
    cacheSet(url, data);
    return data;
  }

  /* Resolve each configured field role to the dataset's real column name.
     Prefers dataset metadata (lists every column even if null in samples);
     falls back to keys of a sample record. Exact match first, then a
     contains-match so e.g. "issued_date" still finds "co_issued_date". */
  async function resolveFields(dsKey) {
    if (resolved[dsKey]) return resolved[dsKey];
    const ds = GW_CONFIG.datasets[dsKey];
    let names = [];
    try {
      const meta = await fetchJSON(`${GW_CONFIG.portal}/api/views/${ds.id}.json`);
      names = (meta.columns || [])
        .map((c) => c.fieldName)
        .filter((n) => n && !n.startsWith(":"));
    } catch {
      /* metadata endpoint unavailable — sample a record instead */
    }
    if (!names.length) {
      try {
        const sample = await fetchJSON(`${GW_CONFIG.portal}/resource/${ds.id}.json?$limit=1`);
        names = sample[0] ? Object.keys(sample[0]) : [];
      } catch {
        names = [];
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

  function soqlDate(d) {
    return d.toISOString().slice(0, 10); // floating timestamps compare fine against yyyy-mm-dd
  }

  function daysAgo(n) {
    const d = new Date();
    d.setDate(d.getDate() - n);
    return d;
  }

  /* Build a resource query URL from SoQL params. */
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

  /* Rows from the last `days` days, newest first. Extra where-clauses
     are AND-ed in. Skips silently if the dataset has no date field. */
  async function recent(dsKey, days, { where = [], limit = 1000, select = null } = {}) {
    const f = await resolveFields(dsKey);
    const clauses = [...where.filter(Boolean)];
    if (f.date) clauses.push(`${f.date} >= '${soqlDate(daysAgo(days))}'`);
    return query(dsKey, {
      select: select || undefined,
      where: clauses.join(" AND ") || undefined,
      order: f.date ? `${f.date} DESC` : undefined,
      limit,
    });
  }

  /* Daily counts for the last `days` days: [{d, n}] ascending. */
  async function dailyCounts(dsKey, days, extraWhere = []) {
    const f = await resolveFields(dsKey);
    if (!f.date) return [];
    const clauses = [`${f.date} >= '${soqlDate(daysAgo(days))}'`, ...extraWhere.filter(Boolean)];
    const rows = await query(dsKey, {
      select: `date_trunc_ymd(${f.date}) AS d, count(*) AS n`,
      where: clauses.join(" AND "),
      group: "d",
      order: "d",
      limit: days + 10,
    });
    return rows
      .filter((r) => r.d)
      .map((r) => ({ d: r.d.slice(0, 10), n: +r.n }));
  }

  /* Single count within a window, with optional extra where. */
  async function countBetween(dsKey, fromDaysAgo, toDaysAgo, extraWhere = []) {
    const f = await resolveFields(dsKey);
    if (!f.date) return null;
    const clauses = [
      `${f.date} >= '${soqlDate(daysAgo(fromDaysAgo))}'`,
      ...(toDaysAgo > 0 ? [`${f.date} < '${soqlDate(daysAgo(toDaysAgo))}'`] : []),
      ...extraWhere.filter(Boolean),
    ];
    const rows = await query(dsKey, {
      select: "count(*) AS n",
      where: clauses.join(" AND "),
    });
    return rows[0] ? +rows[0].n : null;
  }

  /* Group-and-aggregate helper: returns [{k, n, v}] where v is an
     optional summed field (e.g. valuation). */
  async function groupBy(dsKey, roleOrField, days, { extraWhere = [], sumRole = null, limit = 12 } = {}) {
    const f = await resolveFields(dsKey);
    const field = f[roleOrField] || roleOrField;
    if (!f.date || !field) return [];
    const sumField = sumRole ? f[sumRole] : null;
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
    return rows
      .filter((r) => r.k)
      .map((r) => ({ k: r.k, n: +r.n, v: r.v != null ? +r.v : null }));
  }

  /* Escape a string literal for SoQL. */
  function q(s) {
    return `'${String(s).replace(/'/g, "''")}'`;
  }

  return { resolveFields, recent, dailyCounts, countBetween, groupBy, query, q, soqlDate, daysAgo };
})();
