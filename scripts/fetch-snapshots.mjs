/* ============================================================
   Groundwork ATX — nightly snapshot fetcher (runs in GitHub
   Actions, or anywhere with Node 18+ and internet access).

   Pulls the queries the dashboard needs from the City of Austin
   Socrata APIs and writes them to data/*.json. The browser app
   uses these as a fallback whenever a live fetch fails, so the
   published site always has data at most one night old.

   Usage:  node scripts/fetch-snapshots.mjs
   ============================================================ */

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const OUT = join(ROOT, "data");
mkdirSync(OUT, { recursive: true });

/* Reuse the browser config verbatim — single source of truth. */
const GW_CONFIG = new Function(
  readFileSync(join(ROOT, "js", "config.js"), "utf8") + "; return GW_CONFIG;"
)();

const PORTAL = GW_CONFIG.portal;
const soqlDate = (d) => d.toISOString().slice(0, 10);
const daysAgo = (n) => { const d = new Date(); d.setDate(d.getDate() - n); return d; };

async function getJSON(url) {
  const headers = { Accept: "application/json" };
  if (GW_CONFIG.appToken) headers["X-App-Token"] = GW_CONFIG.appToken;
  for (let attempt = 0; ; attempt++) {
    try {
      const res = await fetch(url, { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      if (attempt >= 3) throw e;
      await new Promise((r) => setTimeout(r, 2000 * 2 ** attempt));
    }
  }
}

async function resolveFields(dsKey) {
  const ds = GW_CONFIG.datasets[dsKey];
  let names = [];
  try {
    const meta = await getJSON(`${PORTAL}/api/views/${ds.id}.json`);
    names = (meta.columns || []).map((c) => c.fieldName).filter((n) => n && !n.startsWith(":"));
  } catch { /* fall through to sampling */ }
  if (!names.length) {
    const sample = await getJSON(`${PORTAL}/resource/${ds.id}.json?$limit=1`);
    names = sample[0] ? Object.keys(sample[0]) : [];
  }
  const map = {};
  for (const [role, candidates] of Object.entries(ds.fields)) {
    map[role] =
      candidates.find((c) => names.includes(c)) ||
      names.find((n) => candidates.some((c) => n.includes(c) || c.includes(n))) ||
      null;
  }
  if (!map.date) map.date = names.find((n) => /(^|_)date|date($|_)/.test(n)) || null;
  if (!map.lat) map.lat = names.find((n) => /latitude/.test(n)) || null;
  if (!map.lon) map.lon = names.find((n) => /longitude/.test(n)) || null;
  return map;
}

function resourceURL(dsKey, params) {
  const qs = Object.entries(params)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `$${k}=${encodeURIComponent(v)}`)
    .join("&");
  return `${PORTAL}/resource/${GW_CONFIG.datasets[dsKey].id}.json?${qs}`;
}

const meta = { fetchedAt: new Date().toISOString(), datasets: {}, files: {} };

function save(name, data) {
  writeFileSync(join(OUT, name + ".json"), JSON.stringify(data));
  meta.files[name] = Array.isArray(data) ? data.length : 1;
  console.log(`  wrote data/${name}.json (${meta.files[name]} rows)`);
}

let okCount = 0;

async function snapRecent(dsKey, days, limit, fields) {
  const clauses = fields.date ? [`${fields.date} >= '${soqlDate(daysAgo(days))}'`] : [];
  const rows = await getJSON(resourceURL(dsKey, {
    where: clauses.join(" AND ") || undefined,
    order: fields.date ? `${fields.date} DESC` : undefined,
    limit,
  }));
  save(dsKey + "_recent", rows);
}

async function snapDaily(name, dsKey, days, fields, extraWhere) {
  if (!fields.date) return;
  const rows = await getJSON(resourceURL(dsKey, {
    select: `date_trunc_ymd(${fields.date}) AS d, count(*) AS n`,
    where: [`${fields.date} >= '${soqlDate(daysAgo(days))}'`, ...extraWhere].join(" AND "),
    group: "d",
    order: "d",
    limit: days + 10,
  }));
  save(name, rows.filter((r) => r.d).map((r) => ({ d: r.d.slice(0, 10), n: +r.n })));
}

for (const [dsKey, spec] of Object.entries({
  permits: { days: 90, limit: 8000 },
  zoning: { days: 365, limit: 1500 },
  co: { days: 90, limit: 3000 },
  council: { days: 150, limit: 5000 },
})) {
  console.log(`Fetching ${dsKey} (${GW_CONFIG.datasets[dsKey].id})…`);
  try {
    const fields = await resolveFields(dsKey);
    meta.datasets[dsKey] = { fields };
    await snapRecent(dsKey, spec.days, spec.limit, fields);
    okCount++;
  } catch (e) {
    console.error(`  FAILED ${dsKey}: ${e.message}`);
    meta.datasets[dsKey] = { ...(meta.datasets[dsKey] || {}), error: e.message };
  }
}

/* Long-window daily counts for the trend charts (26 weeks > the
   90-day record snapshot, so these get their own files). */
try {
  const f = meta.datasets.permits?.fields;
  if (f?.classMapped) {
    await snapDaily("permits_daily_com", "permits", 190, f, [`${f.classMapped} = 'Commercial'`]);
    await snapDaily("permits_daily_res", "permits", 190, f, [`${f.classMapped} = 'Residential'`]);
  } else if (f) {
    await snapDaily("permits_daily_com", "permits", 190, f, []);
  }
} catch (e) {
  console.error(`  FAILED permit daily counts: ${e.message}`);
}

writeFileSync(join(OUT, "meta.json"), JSON.stringify(meta, null, 2));
console.log(`\nSnapshot complete: ${okCount}/4 datasets. Manifest written to data/meta.json.`);
if (okCount === 0) {
  console.error("Every dataset failed — refusing to overwrite good snapshots with nothing.");
  process.exit(1);
}
