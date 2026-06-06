// CATALYX dashboard — Fase F v5 (entity-centric, cross-linked, run-aware, precompute-bounded).
//
// Boot: render the LATEST run from precomputed overview.json + docs.json with ZERO DuckDB-WASM.
// The whole page can be switched to any historical run via the sidebar selector — the latest run
// is baked, every OTHER run is loaded ON DEMAND from the parquet lake (DuckDB-WASM reads just that
// run_id partition and the result is cached). This keeps overview.json bounded no matter how many
// runs accrue. WASM also backs the per-sector history chart and per-run reports. Hash-routed
// (#/section/id) so every cross-link is shareable.
//
// CRITICAL: both heavy/3rd-party modules (duckdb-wasm ~MBs, marked) are loaded LAZILY via
// dynamic import(), never as static top-level imports — a static import of a slow/failing CDN
// module would block the WHOLE module from executing, blanking the precomputed first paint.
// The first paint must run on overview.json/docs.json alone, with zero external module deps.
const DUCKDB_URL = 'https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.0/+esm';
const MARKED_URL = 'https://cdn.jsdelivr.net/npm/marked@12/+esm';

const $ = (id) => document.getElementById(id);
const status = (m) => { const s = $('status'); if (s) s.textContent = m; };
// Cache-busting token injected by build_site (window.__BUILD__) — appended to every
// fetched asset so index.html, app.js, the JSON and the parquet always load as one set.
const V = (typeof window !== 'undefined' && window.__BUILD__) ? ('?v=' + window.__BUILD__) : '';

let OV = {};
let DOCS = { catalysts_structural: [], catalysts_event: [], studies: [], theses: [] };
let CUR_RUN = null;          // currently-viewed run_id
let CUR_DATA = { ranking: [], rank_moves: [], holdings: {} };  // its snapshot
const RUNCACHE = {};         // run_id → snapshot (dynamically loaded historical runs)
let LAST = { section: 'overview', id: null };

// ── lazy DuckDB-WASM (booted only when a non-latest run / history / report is needed) ───
let conn = null, dbPromise = null;
const tables = new Set();
async function ensureDuckDB() {
  if (conn) return conn;
  if (!dbPromise) dbPromise = (async () => {
    status('booting DuckDB-WASM…');
    const duckdb = await import(/* @vite-ignore */ DUCKDB_URL);
    const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
    const workerURL = URL.createObjectURL(new Blob([`importScripts("${bundle.mainWorker}");`], { type: 'text/javascript' }));
    const worker = new Worker(workerURL);
    const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(), worker);
    await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
    URL.revokeObjectURL(workerURL);
    const manifest = await (await fetch('manifest.json' + V)).json();
    conn = await db.connect();
    for (const [table, files] of Object.entries(manifest)) {
      const names = [];
      for (const f of files) {
        const name = f.replaceAll('/', '_');
        await db.registerFileURL(name, new URL(f + V, document.baseURI).href, duckdb.DuckDBDataProtocol.HTTP, false);
        names.push(`'${name}'`);
      }
      if (!names.length) continue;
      await conn.query(`CREATE OR REPLACE VIEW "${table}" AS SELECT * FROM read_parquet([${names.join(',')}], union_by_name=true)`);
      tables.add(table);
    }
    status(`${tables.size} lake tables ready`);
    return conn;
  })();
  return dbPromise;
}
function norm(v) { if (typeof v === 'bigint') return Number(v); if (v && typeof v === 'object' && !Array.isArray(v) && 'toString' in v) return v.toString(); return v; }
function sqlLiteral(v) { if (v === null || v === undefined) return 'NULL'; if (typeof v === 'number') return String(v); return "'" + String(v).replace(/'/g, "''") + "'"; }
async function q(sql, params) {
  await ensureDuckDB();
  let final = sql;
  if (params && params.length) { let i = 0; final = sql.replace(/\?/g, () => sqlLiteral(params[i++])); }
  const res = await conn.query(final);
  return res.toArray().map((r) => Object.fromEntries(res.schema.fields.map((f) => [f.name, norm(r[f.name])])));
}

// ── visual helpers ─────────────────────────────────────────────────────────────
function escapeHtml(s) { return String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])); }
// marked is loaded lazily (best-effort). Until it resolves — or if the CDN fails — md()
// degrades to escaped, whitespace-preserving text. It never blocks the first paint.
let marked = null;
import(/* @vite-ignore */ MARKED_URL).then((m) => { marked = m.marked; reRenderForMarked(); }).catch(() => {});
function reRenderForMarked() { try { if (LAST && RENDER[LAST.section]) RENDER[LAST.section](LAST.id); } catch (e) { /* ignore */ } }
function md(t) { if (!marked) return '<pre style="white-space:pre-wrap;font:inherit;background:none;border:none;padding:0">' + escapeHtml(t || '') + '</pre>'; try { return marked.parse(t || ''); } catch (e) { return escapeHtml(t || ''); } }
function err(where, e) { console.error(e); const el = $(where); if (el) el.innerHTML = `<div class="err">${(e && e.message) || e}</div>`; }
function scoreColor(v) { return v >= 66 ? 'var(--green)' : v >= 40 ? 'var(--amber)' : 'var(--red)'; }
function num(v, d = 1) { return (v === null || v === undefined || v === '') ? '—' : (Number.isInteger(v) ? v : Number(v).toFixed(d)); }
function signed(v, d = 1) { if (v === null || v === undefined) return '—'; return (v >= 0 ? '+' : '') + num(v, d); }
function maturityPill(m) { const c = { emerging: 'b', mainstream: 'a', crowded: 'r', exhausted: 'r' }[m] || ''; return m ? `<span class="pill ${c}">${m}</span>` : ''; }
function regimePill(s) { if (!s) return ''; const c = { intact: 'g', contested: 'a', breaking: 'r' }[s] || ''; return `<span class="pill ${c}">${s}</span>`; }
function bar(v, max = 100, color) { const p = Math.max(0, Math.min(100, (v / max) * 100)); return `<div class="bar"><i style="width:${p}%;background:${color || scoreColor(v)}"></i></div>`; }
// a labelled colored metric bar: [label] [bar] [value]
function metricBar(v, opts = {}) {
  const color = opts.invert ? (v >= 66 ? 'var(--red)' : v >= 40 ? 'var(--amber)' : 'var(--green)') : (opts.color || undefined);
  return `<span style="display:grid;grid-template-columns:1fr 24px;gap:6px;align-items:center">${bar(v || 0, 100, color)}<span class="v">${num(v, 0)}</span></span>`;
}
function spark(sets, o = {}) {
  const W = o.w || 220, H = o.h || 40, pad = 3;
  const all = []; sets.forEach((s) => s.values.forEach((v) => { if (v != null) all.push(v); }));
  if (all.length < 2) return '';
  const mn = Math.min(...all), mx = Math.max(...all), rng = (mx - mn) || 1;
  const path = (vals) => vals.map((v, i) => { const x = pad + i / (vals.length - 1 || 1) * (W - 2 * pad); const y = H - pad - ((v - mn) / rng) * (H - 2 * pad); return (i ? 'L' : 'M') + x.toFixed(1) + ' ' + y.toFixed(1); }).join(' ');
  const lines = sets.map((s) => `<path d="${path(s.values)}" fill="none" stroke="${s.color}" stroke-width="1.5"/>`).join('');
  return `<svg class="spark" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">${lines}</svg>`;
}
function tableHTML(rows, opts = {}) {
  if (!rows || !rows.length) return '<p class="hint">(no data)</p>';
  const cols = Object.keys(rows[0]); const sg = new Set(opts.signed || []);
  const head = cols.map((c) => `<th>${c}</th>`).join('');
  const body = rows.map((r) => '<tr>' + cols.map((c) => {
    let v = r[c]; let cls = (typeof v === 'number') ? 'num' : '';
    if (sg.has(c) && typeof v === 'number') cls += v >= 0 ? ' pos' : ' neg';
    if (typeof v === 'number' && !Number.isInteger(v)) v = Number(v.toFixed(2));
    return `<td class="${cls}">${v ?? ''}</td>`;
  }).join('') + '</tr>').join('');
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}
// Render any value (scalar / object / array) as readable HTML — never "[object Object]".
function fmtMeta(v) {
  if (v == null || v === '') return '';
  if (Array.isArray(v)) return '<ul>' + v.map((x) => `<li>${fmtMeta(x)}</li>`).join('') + '</ul>';
  if (typeof v === 'object') {
    return Object.entries(v).filter(([, val]) => val != null && val !== '')
      .map(([k, val]) => `<div style="margin:2px 0"><span class="lbl">${escapeHtml(k.replace(/_/g, ' '))}:</span> ${fmtMeta(val)}</div>`).join('');
  }
  return escapeHtml(String(v));
}
// A line chart with axes (fixed 0–100 scale, gridlines, x date ticks, legend).
function lineChart(series, dates, o = {}) {
  const n = dates.length;
  if (n < 2) return '<p class="hint">Only one run so far — no trend to chart yet.</p>';
  const W = o.w || 560, H = o.h || 210, padL = 28, padR = 12, padT = 10, padB = 28;
  const X = (i) => padL + (i / (n - 1)) * (W - padL - padR);
  const Y = (v) => padT + (1 - v / 100) * (H - padT - padB);
  let grid = '';
  [0, 25, 50, 75, 100].forEach((g) => {
    const y = Y(g);
    grid += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" stroke="var(--border)" stroke-width="1"/>`
      + `<text x="${padL - 5}" y="${(y + 3).toFixed(1)}" text-anchor="end" font-size="9" fill="var(--muted)">${g}</text>`;
  });
  const ticks = [...new Set([0, Math.floor((n - 1) / 2), n - 1])];
  const xlab = ticks.map((i) => `<text x="${X(i).toFixed(1)}" y="${H - 9}" text-anchor="middle" font-size="9" fill="var(--muted)">${dates[i]}</text>`).join('');
  const paths = series.map((s) => {
    const d = s.values.map((v, i) => (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(v || 0).toFixed(1)).join(' ');
    const dots = s.values.map((v, i) => `<circle cx="${X(i).toFixed(1)}" cy="${Y(v || 0).toFixed(1)}" r="2" fill="${s.color}"/>`).join('');
    return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2"/>${dots}`;
  }).join('');
  const legend = series.map((s) => `<span style="display:inline-flex;align-items:center;gap:6px;margin-right:14px"><span style="width:16px;height:3px;background:${s.color};display:inline-block;border-radius:2px"></span><span class="lbl">${s.label}</span></span>`).join('');
  return `<svg width="100%" viewBox="0 0 ${W} ${H}" style="max-width:${W}px;display:block">${grid}${xlab}${paths}</svg><div style="margin-top:8px">${legend}</div>`;
}

// ── run state ───────────────────────────────────────────────────────────────────
const runMeta = (rid) => (OV.runs || []).find((r) => r.run_id === rid) || {};
const runRanking = () => CUR_DATA.ranking || [];
const rankingRow = (sid) => runRanking().find((r) => r.sector_id === sid);
function prevRunId(rid) {
  const ids = (OV.runs || []).map((r) => r.run_id);
  const i = ids.indexOf(rid);
  return (i >= 0 && i + 1 < ids.length) ? ids[i + 1] : null;
}
function prevRankMap() {
  let pr = null;
  if (CUR_RUN === OV.latest_run_id) pr = OV.prev_ranking;
  else { const p = prevRunId(CUR_RUN); pr = p && RUNCACHE[p] ? RUNCACHE[p].ranking : null; }
  if (!pr) return null;
  const m = {}; pr.forEach((r) => { m[r.sector_id] = r.rank; }); return m;
}
function moveBadge(sid) {
  const m = prevRankMap(); if (!m) return '';
  const cur = rankingRow(sid); if (!cur) return '';
  if (!(sid in m)) return '<span class="pill b" title="new this run">NEW</span>';
  const d = m[sid] - cur.rank;                       // +ve = climbed
  if (d === 0) return '<span class="lbl" title="no change">–</span>';
  return d > 0 ? `<span class="pos" title="up ${d}">▲${d}</span>` : `<span class="neg" title="down ${-d}">▼${-d}</span>`;
}
async function getRunData(rid) {
  if (rid === OV.latest_run_id) return OV.latest;
  if (RUNCACHE[rid]) return RUNCACHE[rid];
  await ensureDuckDB();
  const ranking = tables.has('sector_snapshot') ? await q(
    `SELECT sector_id, rank, composite, catalyst_alignment, momentum, flow_confirmation, valuation_relative, crowding_risk, narrative_maturity, primary_etf, regime_state
     FROM sector_snapshot WHERE run_id = ? ORDER BY rank`, [rid]) : [];
  const rank_moves = tables.has('rank_event') ? await q(
    `SELECT sector_id, event_type, from_rank, to_rank, delta FROM rank_event WHERE run_id = ? ORDER BY abs(coalesce(delta,99)) DESC`, [rid]) : [];
  const holdings = {};
  if (tables.has('portfolio_holding')) {
    (await q(`SELECT portfolio_id, rank_in_portfolio, sector_id, primary_etf, weight_pct, composite, momentum, entry_price, narrative_maturity
              FROM portfolio_holding WHERE run_id = ? ORDER BY portfolio_id, rank_in_portfolio`, [rid]))
      .forEach((r) => { (holdings[r.portfolio_id] = holdings[r.portfolio_id] || []).push(r); });
  }
  return (RUNCACHE[rid] = { ranking, rank_moves, holdings });
}
async function setRun(rid) {
  if (!rid || !runMeta(rid).run_id) return;
  if (rid !== OV.latest_run_id) status('loading run ' + rid + '…');
  CUR_DATA = await getRunData(rid);
  CUR_RUN = rid;
  renderRunCurrent(rid);
  status('ready');
  applyRoute(LAST.section, LAST.id);
}
function renderRunCurrent(rid) {
  const m = runMeta(rid);
  const isLatest = rid === OV.latest_run_id;
  const el = $('run-current'); if (!el) return;
  el.innerHTML = `
    <div class="rc-date">${m.ts || rid} ${isLatest ? '<span class="pill g">latest</span>' : '<span class="pill a">historical</span>'}</div>
    <div class="rc-meta">${m.sector_count || '—'} sectors${m.notes ? '<br/>' + escapeHtml((m.notes || '').slice(0, 70)) : ''}</div>
    <a href="#/data">Browse all runs →</a>`;
}

// ── cross-link graph (from docs.json) ───────────────────────────────────────────
const studyFor = (sid) => (DOCS.studies || []).find((s) => s.sector_id === sid);
const catalystDoc = (cid) => (DOCS.catalysts_structural || []).find((c) => c.id === cid) || (DOCS.catalysts_event || []).find((c) => c.id === cid);
const sectorsForCatalyst = (cid) => (DOCS.studies || []).filter((s) => (s.active_catalyst_ids || []).includes(cid)).map((s) => s.sector_id);
const thesisForSector = (sid) => (DOCS.theses || []).find((t) => (t.sector || {}).sector_id === sid);
const thesesForCatalyst = (cid) => (DOCS.theses || []).filter((t) => (t.catalyst || {}).catalyst_event_id === cid);
function holdingPortfolios(sid) {
  const out = [];
  for (const [pid, rows] of Object.entries((OV.latest_holdings || {}).by_pid || {})) {
    const h = (rows || []).find((r) => r.sector_id === sid);
    if (h) out.push({ pid, weight: h.weight_pct });
  }
  return out;
}
const link = (route, id, text) => `<a href="#/${route}/${encodeURIComponent(id)}">${escapeHtml(text)}</a>`;
const sectorLink = (sid) => link('sectors', sid, sid);
const pfName = (pid) => ((OV.portfolios || []).find((p) => p.portfolio_id === pid) || {}).name || pid;

// ── OVERVIEW ─────────────────────────────────────────────────────────────────
function portfolioCard(p) {
  const beat = (p.vs_benchmark_pct ?? 0) >= 0;
  const sp = spark([{ values: p.nav, color: 'var(--accent)' }, { values: p.benchmark_nav, color: '#8b949e' }], { w: 230, h: 44 });
  const m = p.metrics || {};
  return `<a class="card click ${p.portfolio_id === curPf ? 'sel' : ''}" href="#/portfolios/${p.portfolio_id}">
    <div class="lbl">${escapeHtml(p.name || p.portfolio_id)}</div>
    <div class="big ${(p.return_pct ?? 0) >= 0 ? 'pos' : 'neg'}">${signed(p.return_pct)}%</div>
    <div style="margin:6px 0">${sp}</div>
    <div class="lbl">vs ${p.benchmark_etf || 'SPY'} <span class="pill ${beat ? 'g' : 'r'}">${signed(p.vs_benchmark_pct)}pp</span></div>
    <div class="lbl" style="margin-top:3px">Sharpe ${num(m.sharpe, 2)} · vol ${num(m.vol_pct)}% · maxDD ${num(m.max_drawdown_pct)}%</div>
  </a>`;
}
function renderOverview() {
  const m = runMeta(CUR_RUN);
  $('ov-runbadge').innerHTML = m.run_id
    ? `Run <b>${m.run_id}</b> · ${m.ts} · ${m.sector_count} sectors${CUR_RUN === OV.latest_run_id ? ' · <span class="pill g">latest</span>' : ' · <span class="pill a">historical view</span>'}`
      + (m.notes ? `<br/><span style="color:var(--muted)">${escapeHtml(m.notes)}</span>` : '')
      + `<div class="lbl" style="margin-top:8px;text-transform:uppercase;letter-spacing:.5px;font-size:10px">What changed this run</div>${runDigest(m.summary)}`
    : 'No scoring run yet.';

  $('ov-portfolios').innerHTML = (OV.portfolios || []).map(portfolioCard).join('') || '<p class="hint">No NAV yet.</p>';

  // top sectors with movement deltas + clear clickable affordance
  $('ov-ranking').innerHTML = '<div class="card">' + runRanking().slice(0, 12).map((s) => `
    <a class="rowlink barrow" style="grid-template-columns:22px minmax(120px,1fr) 84px 44px 40px 12px;text-decoration:none;color:inherit" href="#/sectors/${s.sector_id}">
      <span class="lbl" style="text-align:right">${s.rank}</span>
      <span class="nm">${s.sector_id} <span class="pill b">${s.primary_etf || '—'}</span></span>
      ${bar(s.composite)}
      <span class="v">${num(s.composite, 0)}</span>
      <span class="v">${moveBadge(s.sector_id)}</span>
      <span class="lbl" style="text-align:right">›</span>
    </a>`).join('') + '</div>';

  // alerts (dislocation = current snapshot; contextualized with the sector's standing)
  const d = OV.dislocation;
  let alerts = '';
  if (d) {
    const opp = (d.opportunities || []).slice(0, 5);
    if (opp.length) alerts += `<h3 style="margin-top:0">Opportunities — panic dips</h3>
      <p class="hint" style="margin:-2px 0 8px">Fell hard but fundamentals intact & catalyst-confirmed. <b>catalyst-alignment</b> = how strongly the sector's active catalysts still support it (0–100).</p>`
      + opp.map((o) => {
        const rr = rankingRow(o.sector_id) || {};
        return `<a class="rowlink" style="display:block;text-decoration:none;color:inherit;padding:6px" href="#/sectors/${o.sector_id}">
          <div style="display:flex;justify-content:space-between"><b>${o.sector_id}</b> <span class="neg">${num(o.drawdown_pct)}%</span></div>
          <div class="lbl">rank #${rr.rank ?? '—'} · composite ${num(rr.composite, 0)} · catalyst-alignment ${num(o.catalyst_alignment, 0)}/100 ${regimePill(rr.regime_state)}</div>
        </a>`;
      }).join('');
    const reg = (d.regime || []);
    if (reg.length) alerts += '<h3>Regime watch — non-intact</h3>' + reg.map((g) => `
      <a class="rowlink barrow" style="grid-template-columns:minmax(120px,1fr) auto 64px;text-decoration:none;color:inherit" href="#/sectors/${g.sector_id}">
        <span class="nm">${g.sector_id}</span>${regimePill(g.regime_state)}
        <span class="v neg">${num(g.drawdown_pct)}%</span></a>`).join('');
  }
  $('ov-alerts').innerHTML = alerts ? `<div class="card">${alerts}</div>` : '<p class="hint">No dislocation analysis yet.</p>';

  // biggest movers this run (computed from prev-run deltas — independent of rank_event)
  const pm = prevRankMap();
  let movesHtml = '<p class="hint">No previous run to compare.</p>';
  if (pm) {
    const moved = runRanking().filter((s) => s.sector_id in pm).map((s) => ({ sid: s.sector_id, d: pm[s.sector_id] - s.rank }))
      .filter((x) => x.d !== 0).sort((a, b) => Math.abs(b.d) - Math.abs(a.d)).slice(0, 8);
    movesHtml = moved.length ? '<div class="card">' + moved.map((x) => `
      <a class="rowlink barrow" style="grid-template-columns:minmax(120px,1fr) 56px;text-decoration:none;color:inherit" href="#/sectors/${x.sid}">
        <span class="nm">${x.sid}</span>
        <span class="v ${x.d > 0 ? 'pos' : 'neg'}">${x.d > 0 ? '▲' : '▼'}${Math.abs(x.d)}</span>
      </a>`).join('') + '</div>' : '<p class="hint">No rank changes vs previous run.</p>';
  }
  $('ov-moves').innerHTML = movesHtml;
}

// ── SECTORS (full comparison table + study + history, run-aware) ─────────────────
// Heatmap columns (higher = better). valuation_relative is intentionally omitted — it is a
// hardcoded 50 placeholder until valuation_engine exists, so a column of identical 50s only
// adds noise. crowding is shown as a categorical label (it derives from narrative_maturity).
const SEC_COLS = [
  { k: 'composite', label: 'composite', bold: true, tip: 'Blend used for the ranking (higher = better)' },
  { k: 'catalyst_alignment', label: 'catalyst', tip: 'How strongly active catalysts support the sector' },
  { k: 'momentum', label: 'momentum', tip: 'Cross-sectional price-momentum percentile' },
  { k: 'flow_confirmation', label: 'flow', tip: 'ETF net-flow confirmation (shares × NAV)' },
];
// continuous vivid heatmap: red (low) → amber → green (high)
function heatColor(v) {
  const t = Math.max(0, Math.min(100, v)) / 100;
  const h = t * 132;                 // 0=red → 132=green
  const s = 68, l = 30 + 14 * (1 - Math.abs(t - 0.5) * 2); // a touch lighter mid-range
  return `hsl(${h.toFixed(0)} ${s}% ${l.toFixed(0)}%)`;
}
function heatCell(v, bold) {
  if (v == null || v === '') return '<td style="text-align:center"><span class="lbl">—</span></td>';
  return `<td style="text-align:center"><span class="score" style="background:${heatColor(v)}${bold ? ';font-weight:700;min-width:40px' : ''}">${num(v, 0)}</span></td>`;
}
function crowdLabel(v) {
  if (v == null) return '<span class="lbl">—</span>';
  const [cls, txt] = v >= 66 ? ['r', 'high'] : v >= 40 ? ['a', 'medium'] : ['g', 'low'];
  return `<span class="pill ${cls}" title="crowding risk ${num(v, 0)} (from narrative maturity)">${txt}</span>`;
}
let SEC_FILTER = '', curSector = null, SEC_SORT = { k: 'rank', dir: 1 };
function drawSecTable() {
  const f = SEC_FILTER.toLowerCase();
  let rows = runRanking().filter((s) => !f || s.sector_id.toLowerCase().includes(f));
  const k = SEC_SORT.k, dir = SEC_SORT.dir;
  rows = rows.slice().sort((a, b) => {
    const av = a[k], bv = b[k];
    if (av == null) return 1; if (bv == null) return -1;
    return (av < bv ? -1 : av > bv ? 1 : 0) * dir;
  });
  const arrow = (col) => SEC_SORT.k === col ? `<span class="ar">${SEC_SORT.dir > 0 ? '▲' : '▼'}</span>` : '';
  const head = `<th data-sort="rank">#${arrow('rank')}</th><th data-sort="sector_id">sector${arrow('sector_id')}</th>`
    + SEC_COLS.map((c) => `<th class="num" data-sort="${c.k}" title="${c.tip}">${c.label}${arrow(c.k)}</th>`).join('')
    + `<th data-sort="crowding_risk" title="crowding (from narrative maturity) — lower is better">crowding${arrow('crowding_risk')}</th>`
    + `<th data-sort="regime_state" title="noise-vs-regime state">regime${arrow('regime_state')}</th><th title="rank move vs previous run">Δ</th><th></th>`;
  const chevron = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>';
  const body = rows.map((s) => `<tr data-sid="${s.sector_id}" class="${s.sector_id === curSector ? 'sel' : ''}">
      <td class="lbl">${s.rank}</td>
      <td><b>${s.sector_id}</b> <span class="pill b">${s.primary_etf || '—'}</span></td>
      ${SEC_COLS.map((c) => heatCell(s[c.k], c.bold)).join('')}
      <td>${crowdLabel(s.crowding_risk)}</td>
      <td>${regimePill(s.regime_state)}</td>
      <td>${moveBadge(s.sector_id)}</td>
      <td class="go" title="open sector report">${chevron}</td>
    </tr>`).join('') || `<tr><td colspan="${SEC_COLS.length + 6}" class="lbl" style="padding:14px">no match</td></tr>`;
  $('sec-table').innerHTML = `<div class="cmp"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`
    + `<p class="hint" style="margin-top:8px">Cell colour: <b style="color:var(--green)">green</b> high · <b style="color:var(--amber)">amber</b> mid · <b style="color:var(--red)">red</b> low (higher = better for all shown). <b>crowding</b> low is better. <code>valuation</code> hidden — it is a neutral placeholder (50) until <code>valuation_engine</code> is built.</p>`;
  $('sec-table').querySelector('thead').onclick = (ev) => {
    const th = ev.target.closest('th[data-sort]'); if (!th) return;
    const col = th.dataset.sort;
    // default direction: rank/sector ascending, score columns descending
    if (SEC_SORT.k === col) SEC_SORT.dir *= -1;
    else SEC_SORT = { k: col, dir: (col === 'rank' || col === 'sector_id') ? 1 : -1 };
    drawSecTable();
  };
  $('sec-table').querySelector('tbody').onclick = (ev) => {
    const tr = ev.target.closest('tr[data-sid]'); if (!tr) return;
    selectSector(tr.dataset.sid);
    $('sec-detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
}
function renderSectors(id) {
  const inp = $('sec-search'); inp.value = SEC_FILTER; inp.oninput = (e) => { SEC_FILTER = e.target.value; drawSecTable(); };
  drawSecTable();
  if (id) selectSector(id);
  else if (curSector && rankingRow(curSector)) selectSector(curSector);
  else $('sec-detail').innerHTML = '<p class="hint">Click a sector row above to see its study, history and links.</p>';
}
function selectSector(sid) {
  curSector = sid;
  document.querySelectorAll('#sec-table tr[data-sid]').forEach((el) => el.classList.toggle('sel', el.dataset.sid === sid));
  const row = rankingRow(sid) || {};
  const study = studyFor(sid);
  const th = thesisForSector(sid);
  const cats = (study && study.active_catalyst_ids) || [];
  const holders = holdingPortfolios(sid);
  const chips = [];
  cats.forEach((c) => chips.push(link('catalysts', c, c)));
  if (th) chips.push(`<span class="pill ${th.status === 'open' ? 'g' : ''}" title="${escapeHtml(th.id)}">thesis: ${th.status}</span>`);
  holders.forEach((h) => chips.push(link('portfolios', h.pid, `${pfName(h.pid)} ${num(h.weight, 0)}%`)));

  $('sec-detail').innerHTML = `
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap">
        <div>
          <div class="lbl">${sid}</div>
          <div style="font-size:19px;font-weight:650">${escapeHtml((study && study.sector_label) || sid)}
            <span class="pill b">${row.primary_etf || (study && study.etf_analysis && study.etf_analysis.primary_etf) || '—'}</span></div>
          <div style="margin-top:6px">${row.rank ? `<span class="pill">rank ${row.rank} ${moveBadge(sid)}</span> ` : ''}${regimePill(row.regime_state)} ${maturityPill(row.narrative_maturity || (study && study.narrative_maturity))}</div>
        </div>
        <div style="text-align:right"><div class="lbl">composite</div><div class="big" style="color:${scoreColor(row.composite || 0)}">${num(row.composite, 0)}</div></div>
      </div>
      <div style="margin-top:14px;display:grid;gap:8px">
        <div class="barrow" style="grid-template-columns:130px 1fr 40px"><span class="lbl">catalyst align</span>${bar(row.catalyst_alignment || 0)}<span class="v">${num(row.catalyst_alignment, 0)}</span></div>
        <div class="barrow" style="grid-template-columns:130px 1fr 40px"><span class="lbl">momentum</span>${bar(row.momentum || 0)}<span class="v">${num(row.momentum, 0)}</span></div>
        <div class="barrow" style="grid-template-columns:130px 1fr 40px"><span class="lbl">flow</span>${bar(row.flow_confirmation || 0)}<span class="v">${num(row.flow_confirmation, 0)}</span></div>
        <div class="barrow" style="grid-template-columns:130px 1fr"><span class="lbl">crowding</span><span>${crowdLabel(row.crowding_risk)} <span class="lbl">(${num(row.crowding_risk, 0)})</span></span></div>
      </div>
      ${chips.length ? `<h3>Linked</h3><div class="chips">${chips.join('')}</div>` : ''}
      <h3>Score history (all runs)</h3><div id="sec-hist"><p class="hint">loading…</p></div>
    </div>
    ${study ? `<div class="card" style="margin-top:16px"><h3 style="margin-top:0">Bottom-up study</h3>
        ${study.narrative_notes ? `<div class="md">${md(study.narrative_notes)}</div>` : ''}${renderStudyMeta(study)}</div>`
      : '<p class="hint" style="margin-top:16px">No sector study on file.</p>'}`;
  loadSectorHistory(sid);
}
function renderStudyMeta(s) {
  const parts = []; const dl = [];
  // plain scalars → compact key/value rows
  for (const k of ['study_type', 'narrative_maturity', 'analyst_narrative_score', 'narrative_trend', 'last_updated'])
    if (s[k] != null && s[k] !== '' && typeof s[k] !== 'object')
      dl.push(`<div class="barrow" style="grid-template-columns:170px 1fr"><span class="lbl">${k.replace(/_/g, ' ')}</span><span>${escapeHtml(String(s[k]))}</span></div>`);
  if (dl.length) parts.push(dl.join(''));
  // object-valued fields (cycle_position, technology_maturity, …) → block with assessment text
  for (const k of ['cycle_position', 'technology_maturity']) {
    const v = s[k]; if (v == null || v === '') continue;
    if (typeof v === 'object') {
      const text = v.assessment || v.note || v.summary;
      parts.push(`<h3>${k.replace(/_/g, ' ')}</h3>` + (text ? `<div class="md">${md(String(text))}</div>` : `<div>${fmtMeta(v)}</div>`));
    } else parts.push(`<h3>${k.replace(/_/g, ' ')}</h3><div>${escapeHtml(String(v))}</div>`);
  }
  for (const k of ['demand_drivers', 'supply_constraints', 'risks', 'key_metrics_to_monitor']) {
    const v = s[k];
    if (Array.isArray(v) && v.length) parts.push(`<h3>${k.replace(/_/g, ' ')}</h3><ul>${v.map((x) => `<li>${fmtMeta(x)}</li>`).join('')}</ul>`);
  }
  return parts.join('');
}
async function loadSectorHistory(sid) {
  try {
    const rows = await q(`SELECT substr(CAST(snapshot_at AS VARCHAR),1,10) AS date, composite, momentum, catalyst_alignment, crowding_risk
      FROM sector_snapshot WHERE sector_id = ? ORDER BY run_id`, [sid]);
    if (curSector !== sid) return;
    const el = $('sec-hist'); if (!el) return;
    el.innerHTML = lineChart([
      { label: 'composite', values: rows.map((r) => r.composite), color: 'var(--accent)' },
      { label: 'catalyst align', values: rows.map((r) => r.catalyst_alignment), color: 'var(--green)' },
      { label: 'momentum', values: rows.map((r) => r.momentum), color: '#8b949e' },
      { label: 'crowding risk', values: rows.map((r) => r.crowding_risk), color: 'var(--red)' },
    ], rows.map((r) => r.date), { w: 560, h: 210 });
  } catch (e) { const el = $('sec-hist'); if (el) el.innerHTML = `<div class="err">${(e && e.message) || e}</div>`; }
}

// ── CATALYSTS & THESES (sub-tabbed: structural / event / thesis, all rich) ──────
let CAT_KIND = 'structural', curCat = null, catWired = false;
const structuralDoc = (id) => (DOCS.catalysts_structural || []).find((c) => c.id === id);
const eventDoc = (id) => (DOCS.catalysts_event || []).find((c) => c.id === id);
const thesisDoc = (id) => (DOCS.theses || []).find((t) => t.id === id);
function catKindOf(id) {
  if (structuralDoc(id)) return 'structural';
  if (eventDoc(id)) return 'event';
  if (thesisDoc(id)) return 'thesis';
  return null;
}
function catItems(kind) {
  if (kind === 'event') return (DOCS.catalysts_event || []).map((c) => ({ id: c.id, label: c.id, score: c.strength_score }));
  if (kind === 'thesis') return (DOCS.theses || []).map((t) => ({ id: t.id, label: t.id, status: t.status }));
  return (DOCS.catalysts_structural || []).map((c) => ({ id: c.id, label: c.title || c.id, score: (c.intensity || {}).current_score }));
}
function buildCatList() {
  document.querySelectorAll('#cat-subtabs button').forEach((b) => b.classList.toggle('active', b.dataset.kind === CAT_KIND));
  $('cat-list').innerHTML = catItems(CAT_KIND).map((it) => `
    <a class="item" data-cid="${encodeURIComponent(it.id)}" href="#/catalysts/${encodeURIComponent(it.id)}">
      <span class="nm">${escapeHtml(it.label)}</span>
      ${it.score != null ? `<span class="v" style="color:${scoreColor(it.score)};font-weight:600">${it.score}</span>`
        : it.status ? `<span class="pill ${it.status === 'open' ? 'g' : ''}">${it.status}</span>` : ''}
    </a>`).join('') || '<div class="item"><span class="nm lbl">(none)</span></div>';
  document.querySelectorAll('#cat-list .item').forEach((el) => el.classList.toggle('sel', decodeURIComponent(el.dataset.cid) === curCat));
}
function renderCatalysts(id) {
  if (!catWired) {
    $('cat-subtabs').addEventListener('click', (ev) => {
      const b = ev.target.closest('button'); if (!b) return;
      CAT_KIND = b.dataset.kind; curCat = null; buildCatList();
      const items = catItems(CAT_KIND); selectCat(items[0] && items[0].id);
    });
    catWired = true;
  }
  if (id) { const k = catKindOf(id); if (k) CAT_KIND = k; }
  buildCatList();
  const items = catItems(CAT_KIND);
  selectCat(id || curCat || (items[0] && items[0].id));
}
function selectCat(id) {
  curCat = id;
  document.querySelectorAll('#cat-list .item').forEach((el) => el.classList.toggle('sel', decodeURIComponent(el.dataset.cid) === id));
  const kind = catKindOf(id);
  const el = $('cat-detail');
  if (kind === 'structural') el.innerHTML = structuralDetailHTML(structuralDoc(id));
  else if (kind === 'event') el.innerHTML = eventDetailHTML(eventDoc(id));
  else if (kind === 'thesis') el.innerHTML = thesisDetailHTML(thesisDoc(id));
  else el.innerHTML = '<p class="hint">Select an item.</p>';
}
function catHeader(idLine, title, rightLabel, rightVal, rightColor) {
  return `<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:14px">
    <div><div class="lbl">${idLine}</div><div style="font-size:18px;font-weight:650;margin-top:2px">${escapeHtml(title)}</div></div>
    ${rightVal != null ? `<div style="text-align:right"><div class="lbl">${rightLabel}</div><div class="big" style="color:${rightColor || 'inherit'}">${rightVal}</div></div>` : ''}
  </div>`;
}
function structuralDetailHTML(c) {
  if (!c) return '<p class="hint">Select a catalyst.</p>';
  const sectors = sectorsForCatalyst(c.id), ths = thesesForCatalyst(c.id);
  const intn = (c.intensity || {}).current_score;
  const inds = (c.indicators || []).map((i) => `
    <div style="margin:10px 0;padding:11px 13px;border:1px solid var(--border);border-radius:9px">
      <div style="display:flex;justify-content:space-between;gap:12px"><b>${escapeHtml(i.name || i.id)}</b>
        <span style="color:${scoreColor(i.score || 0)};font-weight:600">${i.score ?? '—'}</span></div>
      ${bar(i.score || 0)}<div class="lbl" style="margin-top:6px">value: <b>${i.current_value ?? '—'}</b> ${i.unit || ''}</div>
    </div>`).join('');
  return `<div class="card">
    ${catHeader(`${c.id} · ${c.catalyst_type || 'structural'} · ${c.status || ''}`, c.title || c.id, 'intensity', intn ?? null, scoreColor(intn || 0))}
    <div class="md" style="margin-top:8px">${md(c.description || '')}</div>
    ${sectors.length ? `<h3>Drives sectors</h3><div class="chips">${sectors.map(sectorLink).join('')}</div>` : ''}
    ${ths.length ? `<h3>Theses</h3><div class="chips">${ths.map((t) => link('catalysts', t.id, t.id)).join('')}</div>` : ''}
    ${inds ? `<h3>Indicators</h3>${inds}` : ''}</div>`;
}
function eventDetailHTML(e) {
  if (!e) return '<p class="hint">Select a catalyst.</p>';
  const sectors = sectorsForCatalyst(e.id);
  const related = (e.related_catalyst_ids || []);
  const chips = [];
  if (e.novelty_score != null) chips.push(`<span class="pill">novelty ${e.novelty_score}</span>`);
  if (e.magnitude) chips.push(`<span class="pill" title="magnitude">${escapeHtml(String(e.magnitude))}</span>`);
  if (e.relation_to_structural) chips.push(`<span class="pill ${e.relation_to_structural === 'contradicts' ? 'r' : 'g'}">${e.relation_to_structural}</span>`);
  if (e.is_priced_in_estimate != null) chips.push(`<span class="pill" title="priced-in estimate">priced-in ${e.is_priced_in_estimate}</span>`);
  if (e.consensus_surprise) chips.push(`<span class="pill" title="consensus surprise">${escapeHtml(String(e.consensus_surprise))}</span>`);
  if (e.decay_halflife_days) chips.push(`<span class="pill" title="decay half-life">½-life ${e.decay_halflife_days}d</span>`);
  if (e.geography) chips.push(`<span class="pill">${escapeHtml(String(e.geography))}</span>`);
  return `<div class="card">
    ${catHeader(`${e.id} · ${e.catalyst_type || 'event'}${e.catalyst_subtype ? ' / ' + e.catalyst_subtype : ''} · ${e.status || ''}`, e.id, 'strength', e.strength_score ?? null, scoreColor(e.strength_score || 0))}
    <div class="md" style="margin-top:8px">${md(e.description || '')}</div>
    <h3>Signal</h3><div class="chips">${chips.join('') || '<span class="lbl">—</span>'}</div>
    ${related.length ? `<h3>Related catalysts</h3><div class="chips">${related.map((r) => link('catalysts', r, r)).join('')}</div>` : ''}
    ${sectors.length ? `<h3>Drives sectors</h3><div class="chips">${sectors.map(sectorLink).join('')}</div>` : ''}
    ${e.detected_at || e.expires_at ? `<div class="lbl" style="margin-top:10px">detected ${e.detected_at ? String(e.detected_at).slice(0, 10) : '—'}${e.expires_at ? ' · expires ' + String(e.expires_at).slice(0, 10) : ''}</div>` : ''}
  </div>`;
}
function thesisDetailHTML(t) {
  if (!t) return '<p class="hint">Select a thesis.</p>';
  const sid = (t.sector || {}).sector_id, cid = (t.catalyst || {}).catalyst_event_id;
  const v = t.vehicle || {}, en = t.entry || {};
  const headLinks = [];
  if (sid) headLinks.push('sector ' + sectorLink(sid));
  if (cid) headLinks.push('catalyst ' + link('catalysts', cid, cid));
  if (v.primary_etf) headLinks.push(`<span class="pill b">${v.primary_etf}</span>`);
  const sec = (title, html) => html ? `<h3>${title}</h3>${html}` : '';
  const entryChips = [];
  if (en.conviction_tier) entryChips.push(`<span class="pill">conviction tier ${en.conviction_tier}</span>`);
  if (en.position_size_pct_portfolio) entryChips.push(`<span class="pill">size ${en.position_size_pct_portfolio}%</span>`);
  if (en.entry_price_limit) entryChips.push(`<span class="pill">limit ${en.entry_price_limit}</span>`);
  if (en.trigger_type) entryChips.push(`<span class="pill">${escapeHtml(String(en.trigger_type))}</span>`);
  return `<div class="card">
    ${catHeader(`${t.id} · thesis`, t.id, 'status', null)}
    <div style="margin-top:2px"><span class="pill ${t.status === 'open' ? 'g' : ''}">${t.status || ''}</span> ${headLinks.join(' · ')}</div>
    ${sec('Catalyst', (t.catalyst || {}).catalyst_summary ? `<div class="md">${md(t.catalyst.catalyst_summary)}</div>` : '')}
    ${sec('Sector rationale', (t.sector || {}).rationale ? `<div class="md">${md(t.sector.rationale)}</div>` : '')}
    ${sec('Vehicle', v.vehicle_selection_rationale ? `<div class="md">${md(v.vehicle_selection_rationale)}</div>` : '')}
    ${sec('Entry', (entryChips.length ? `<div class="chips">${entryChips.join('')}</div>` : '') + (en.trigger_description ? `<div class="md">${md(en.trigger_description)}</div>` : ''))}
    ${sec('Assumptions', Array.isArray(t.assumptions) && t.assumptions.length ? `<ul>${t.assumptions.map((a) => `<li>${fmtMeta(a)}</li>`).join('')}</ul>` : '')}
    ${sec('Invalidation conditions', Array.isArray(t.invalidation_conditions) && t.invalidation_conditions.length ? `<ul>${t.invalidation_conditions.map((a) => `<li>${fmtMeta(a)}</li>`).join('')}</ul>` : '')}
  </div>`;
}

// ── PORTFOLIOS ─────────────────────────────────────────────────────────────────
let curPf = null, lineageBuilt = false;
function holdingsForRun(pid) {
  const cur = (CUR_DATA.holdings || {})[pid];
  if (cur && cur.length) return { rows: cur, runId: CUR_RUN, fallback: false };
  const lh = OV.latest_holdings || {};
  return { rows: (lh.by_pid || {})[pid] || [], runId: lh.run_id, fallback: true };
}
function renderPortfolios(pid) {
  $('pf-cards').innerHTML = (OV.portfolios || []).map(portfolioCard).join('') || '<p class="hint">No NAV yet.</p>';
  const sel = pid || curPf || ((OV.portfolios || [])[0] || {}).portfolio_id;
  if (sel) selectPortfolio(sel);
  if (!lineageBuilt) initLineage();
}
function selectPortfolio(pid) {
  curPf = pid;
  document.querySelectorAll('#pf-cards .card').forEach((el) => el.classList.toggle('sel', el.getAttribute('href') === `#/portfolios/${pid}`));
  const p = (OV.portfolios || []).find((x) => x.portfolio_id === pid) || {};
  const c = p.construction || {}, m = p.metrics || {}, bm = p.bench_metrics || {};
  const beat = (p.vs_benchmark_pct ?? 0) >= 0;
  const mc = (l, v, cls, sub) => `<div class="card"><div class="lbl">${l}</div><div class="big ${cls || ''}">${v}</div>${sub ? `<div class="lbl">${sub}</div>` : ''}</div>`;

  // methodology chips
  const chips = [`<span class="pill b">weighting: ${c.weighting || '—'}</span>`,
    `<span class="pill">max ${c.max_positions} positions</span>`,
    `<span class="pill">min composite ${c.min_composite}</span>`];
  if (c.min_momentum) chips.push(`<span class="pill">min momentum ${c.min_momentum}</span>`);
  if (c.max_crowding != null && c.max_crowding < 100) chips.push(`<span class="pill">max crowding ${c.max_crowding}</span>`);
  chips.push(`<span class="pill">cap ${c.max_position_pct}%/position</span>`);
  if ((c.exclude_narrative_maturity || []).length) chips.push(`<span class="pill r">exclude ${c.exclude_narrative_maturity.join(', ')}</span>`);

  const { rows, runId, fallback } = holdingsForRun(pid);
  const holdHead = `<div class="barrow" style="grid-template-columns:minmax(150px,1.5fr) 1fr 1fr 1fr;color:var(--muted);font-size:11px">
    <span>sector</span><span>weight</span><span>composite</span><span>momentum</span></div>`;
  const mx = Math.max(...rows.map((r) => r.weight_pct), 1);
  const holdRows = rows.map((r) => `
    <div class="barrow" style="grid-template-columns:minmax(150px,1.5fr) 1fr 1fr 1fr">
      <a class="nm" href="#/sectors/${r.sector_id}" style="color:inherit">${r.sector_id} <span class="pill b">${r.primary_etf}</span></a>
      <span style="display:grid;grid-template-columns:1fr 36px;gap:6px;align-items:center">${bar(r.weight_pct, mx, 'var(--accent)')}<span class="v">${num(r.weight_pct)}%</span></span>
      ${metricBar(r.composite)}${metricBar(r.momentum)}
    </div>`).join('');

  $('pf-detail').innerHTML = `
    <h2 style="margin-top:0">${escapeHtml(p.name || pid)} <span class="lbl" style="font-weight:400">${p.kind || ''} · vs ${p.benchmark_etf || 'SPY'} · ${p.n_days || '—'}d backtest</span></h2>
    <div class="strip">
      ${mc('total return', signed(p.return_pct) + '%', (p.return_pct ?? 0) >= 0 ? 'pos' : 'neg')}
      ${mc('vs ' + (p.benchmark_etf || 'SPY'), signed(p.vs_benchmark_pct) + 'pp', beat ? 'pos' : 'neg', beat ? 'beat market' : 'below market')}
      ${mc('volatility (ann.)', num(m.vol_pct) + '%', '', 'SPY ' + num(bm.vol_pct) + '%')}
      ${mc('Sharpe', num(m.sharpe, 2), '', 'SPY ' + num(bm.sharpe, 2))}
      ${mc('max drawdown', num(m.max_drawdown_pct) + '%', 'neg', 'SPY ' + num(bm.max_drawdown_pct) + '%')}
    </div>
    <div class="card" style="margin-top:16px">
      <h3 style="margin-top:0">How the weights are built</h3>
      ${p.description ? `<div class="md">${md(p.description)}</div>` : ''}
      <div class="chips" style="margin-top:8px">${chips.join('')}</div>
      <p class="hint" style="margin-top:8px">Selected sectors are ranked by <b>${c.weighting === 'momentum' ? 'momentum' : 'composite'}</b>;
        each weight is proportional to that score, then capped at ${c.max_position_pct}% per position
        (excess water-fills to the rest; if all positions hit the cap, the remainder is held as cash).</p>
    </div>
    <h3>Holdings ${fallback ? `<span class="pill a">from run ${runId} (latest build)</span>` : `<span class="lbl">run ${runId}</span>`}</h3>
    ${rows.length ? `<div class="card">${holdHead}${holdRows}</div>` : '<p class="hint">No holdings built for this run.</p>'}`;
}
async function initLineage() {
  lineageBuilt = true;
  try {
    await ensureDuckDB();
    if (!tables.has('portfolio_trade')) { $('pf-lineage').innerHTML = '<p class="hint">No real trades logged yet — these are model (backtested) portfolios.</p>'; return; }
    const trades = await q(`SELECT trade_id FROM portfolio_trade ORDER BY date DESC`);
    if (!trades.length) { $('pf-lineage').innerHTML = '<p class="hint">No trades.</p>'; return; }
    $('pf-lineage').innerHTML = `<div class="row"><label>Trade: <select id="trade-select">${trades.map((t) => `<option>${t.trade_id}</option>`).join('')}</select></label></div><div id="lineage-out"></div>`;
    $('trade-select').addEventListener('change', renderLineage);
    renderLineage();
  } catch (e) { err('pf-lineage', e); }
}
async function renderLineage() {
  try {
    const tid = $('trade-select').value;
    const trade = (await q(`SELECT * FROM portfolio_trade WHERE trade_id = ?`, [tid]))[0];
    let html = '<h3>Trade</h3>' + tableHTML([trade]);
    if (trade && trade.run_id) {
      if (tables.has('report')) html += '<h3>Run reports</h3>' + tableHTML(await q(`SELECT report_type, report_date, path FROM report WHERE run_id = ?`, [trade.run_id]));
      if (tables.has('sector_snapshot') && trade.etf) html += '<h3>Sector scores in that run</h3>' + tableHTML(await q(`SELECT sector_id, rank, composite, momentum, catalyst_alignment FROM sector_snapshot WHERE run_id = ? AND primary_etf = ?`, [trade.run_id, trade.etf]));
    }
    $('lineage-out').innerHTML = html;
  } catch (e) { err('lineage-out', e); }
}

// ── DATA (runs catalogue + report, lazy) ────────────────────────────────────────
function runDigest(s) {
  if (!s) return '';
  const parts = [];
  (s.new_catalysts || []).forEach((c) => {
    const cid = typeof c === 'string' ? c : c.id;
    const rel = (c && typeof c === 'object' && c.relation_to_structural) ? ` (${c.relation_to_structural})` : '';
    parts.push(`<span class="pill b" title="new event catalyst detected in this run's window">+ ${cid}${rel}</span>`);
  });
  (s.entered || []).forEach((x) => parts.push(`<span class="pill g" title="entered top-10">▲ ${x}</span>`));
  (s.exited || []).forEach((x) => parts.push(`<span class="pill r" title="dropped out of top-10">▼ ${x}</span>`));
  (s.movers_up || []).forEach((m) => parts.push(`<span class="pill" title="climbed ${m.delta} ranks"><span class="pos">▲${m.delta}</span> ${m.sector_id}</span>`));
  (s.movers_down || []).forEach((m) => parts.push(`<span class="pill" title="fell ${-m.delta} ranks"><span class="neg">▼${-m.delta}</span> ${m.sector_id}</span>`));
  const reg = s.regime || {};
  if (reg.contested) parts.push(`<span class="pill a" title="sectors with a live contradiction (regime contested)">${reg.contested} contested</span>`);
  if (reg.breaking) parts.push(`<span class="pill r" title="sectors breaking (permanent rotation)">${reg.breaking} breaking</span>`);
  const b = s.breadth;
  if (b) parts.push(`<span class="pill" title="composite breadth vs previous run (mean Δ ${b.mean_delta})"><span class="pos">▲${b.up}</span>/<span class="neg">▼${b.down}</span> breadth</span>`);
  return parts.length ? `<div class="chips" style="margin-top:8px">${parts.join('')}</div>`
    : '<div class="lbl" style="margin-top:6px">No material change vs the previous run.</div>';
}
async function renderData() {
  const runs = OV.runs || [];
  $('data-runs').innerHTML = runs.map((r, i) => `
    <div class="card ${r.run_id === CUR_RUN ? 'sel' : ''}" style="margin-bottom:10px">
      <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start">
        <div style="min-width:0">
          <div style="font-weight:600">${r.ts} ${r.run_id === OV.latest_run_id ? '<span class="pill g">latest</span>' : ''} ${r.run_id === CUR_RUN ? '<span class="pill b">viewing</span>' : ''}</div>
          <div class="lbl">${r.run_id} · ${r.sector_count} sectors · scoring ${(r.scoring_version || '').slice(0, 8)} · git ${r.git_commit || '—'}</div>
          <div style="margin-top:6px;font-size:13px">${escapeHtml(r.notes || '')}</div>
          <div class="lbl" style="margin-top:8px;text-transform:uppercase;letter-spacing:.5px;font-size:10px">What changed ${i + 1 < runs.length ? 'vs ' + runs[i + 1].ts.slice(0, 10) : '(first run)'}</div>
          ${runDigest(r.summary)}
        </div>
        <button class="btn" data-run="${r.run_id}" style="white-space:nowrap">${r.run_id === CUR_RUN ? '✓ viewing' : 'View this run'}</button>
      </div>
    </div>`).join('') || '<p class="hint">No runs.</p>';
  $('data-runs').onclick = (ev) => { const b = ev.target.closest('button[data-run]'); if (b) setRun(b.dataset.run); };
  try {
    await ensureDuckDB();
    if (!tables.has('report')) { $('report-out').innerHTML = '<p class="hint">(no reports table)</p>'; return; }
    const reps = await q(`SELECT report_type, content_md FROM report WHERE run_id = ? ORDER BY report_type`, [CUR_RUN]);
    $('report-out').innerHTML = (reps.length && reps.some((x) => x.content_md))
      ? reps.map((x) => `<h3>${x.report_type}</h3>` + md(x.content_md)).join('')
      : `<p class="hint">No text report associated with run ${CUR_RUN}.</p>`;
  } catch (e) { err('report-out', e); }
}

// ── router ──────────────────────────────────────────────────────────────────────
const RENDER = { overview: renderOverview, sectors: renderSectors, catalysts: renderCatalysts, portfolios: renderPortfolios, data: renderData };
function applyRoute(section, id) {
  LAST = { section, id };
  document.querySelectorAll('.navlink').forEach((el) => el.classList.toggle('active', el.dataset.route === section));
  document.querySelectorAll('.section').forEach((el) => el.classList.toggle('active', el.id === `s-${section}`));
  try { (RENDER[section] || RENDER.overview)(id); } catch (e) { console.error(e); }
}
function route() {
  const m = (location.hash || '').replace(/^#\/?/, '').split('/');
  const section = RENDER[m[0]] ? m[0] : 'overview';
  applyRoute(section, m[1] ? decodeURIComponent(m[1]) : null);
  window.scrollTo(0, 0);
}

// ── boot ────────────────────────────────────────────────────────────────────────
(async () => {
  document.querySelectorAll('.navlink').forEach((el) => el.addEventListener('click', (ev) => { ev.preventDefault(); location.hash = '#/' + el.dataset.route; }));
  window.addEventListener('hashchange', route);
  status('loading data…');
  try {
    const [ov, docs] = await Promise.all([
      fetch('overview.json' + V).then((r) => r.json()),
      fetch('docs.json' + V).then((r) => r.json()).catch(() => DOCS),
    ]);
    OV = ov; DOCS = docs;
    CUR_RUN = OV.latest_run_id; CUR_DATA = OV.latest || { ranking: [], rank_moves: [], holdings: {} };
    const g = $('gen'); if (g && OV.generated_at) g.textContent = 'built ' + OV.generated_at.slice(0, 16).replace('T', ' ');
    renderRunCurrent(CUR_RUN);
    status('ready · historical runs load on demand');
  } catch (e) { status('error'); console.error(e); }
  if (!location.hash) location.hash = '#/overview';
  route();
})();
