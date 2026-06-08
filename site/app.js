// CATALYX dashboard — Fase F v5 (entity-centric, cross-linked, run-aware, precompute-bounded).
//
// ⚠ LANGUAGE RULE: ALL user-facing copy in the dashboard (this file's strings + comments,
//   site/index.html, scripts/build_site.py baked text) MUST be in ENGLISH. The user works in
//   Spanish in chat, but the dashboard is English-only. Don't leak Spanish into rendered text.
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
let DOCS = { catalysts_structural: [], catalysts_event: [], studies: [], movements: [] };
let CUR_RUN = null;          // currently-viewed run_id
let CUR_DATA = { ranking: [], rank_moves: [], holdings: {} };  // its snapshot
const RUNCACHE = {};         // run_id → snapshot (dynamically loaded historical runs)
let LAST = { section: 'overview', id: null };

// ── lazy DuckDB-WASM (booted only when a non-latest run / history / report is needed) ───
// IMPORTANT: `conn` is published (and dbReady set) ONLY after every view is created. Earlier
// this assigned `conn` mid-boot (right after connect, before the CREATE VIEW loop), so a
// second query landing in that window got the connection with no views yet → "Table
// sector_snapshot does not exist". The dbReady gate + returning the resolved connection from
// the single dbPromise removes that race.
let conn = null, dbPromise = null, dbReady = false;
const tables = new Set();
async function ensureDuckDB() {
  if (dbReady) return conn;
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
    const c = await db.connect();
    for (const [table, files] of Object.entries(manifest)) {
      const names = [];
      for (const f of files) {
        const name = f.replaceAll('/', '_');
        await db.registerFileURL(name, new URL(f + V, document.baseURI).href, duckdb.DuckDBDataProtocol.HTTP, false);
        names.push(`'${name}'`);
      }
      if (!names.length) continue;
      await c.query(`CREATE OR REPLACE VIEW "${table}" AS SELECT * FROM read_parquet([${names.join(',')}], union_by_name=true)`);
      tables.add(table);
    }
    conn = c; dbReady = true;            // publish only when fully ready
    status(`${tables.size} lake tables ready`);
    return c;
  })();
  return dbPromise;
}
function norm(v) { if (typeof v === 'bigint') return Number(v); if (v && typeof v === 'object' && !Array.isArray(v) && 'toString' in v) return v.toString(); return v; }
function sqlLiteral(v) { if (v === null || v === undefined) return 'NULL'; if (typeof v === 'number') return String(v); return "'" + String(v).replace(/'/g, "''") + "'"; }
async function q(sql, params) {
  const c = await ensureDuckDB();
  let final = sql;
  if (params && params.length) { let i = 0; final = sql.replace(/\?/g, () => sqlLiteral(params[i++])); }
  const res = await c.query(final);
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
// ── entry-timing helpers (the execution WINDOW — recommend-only) ──
function timingFor(sid) { const t = OV.entry_timing; return (t && t.by_sector && t.by_sector[sid]) || null; }
const VERDICT_LABEL = { enter_now: 'enter now', scale_in: 'scale-in', wait_stabilize: 'wait — unstable', wait_event: 'wait — event' };
function verdictPill(t) {
  if (!t) return '';
  const v = t.suggested_verdict, c = { enter_now: 'g', scale_in: 'a', wait_stabilize: 'r', wait_event: 'r' }[v] || '';
  const lbl = (VERDICT_LABEL[v] || v) + (v === 'wait_event' && t.wait_until ? ' @' + t.wait_until : '');
  return `<span class="pill ${c}" title="entry timing">${lbl}</span>`;
}
function statePill(t) { if (!t) return ''; const c = { neutral: 'g', basing: 'a', overbought: 'a', falling: 'r' }[t.micro_timing_state] || ''; return `<span class="pill ${c}">${t.micro_timing_state}</span>`; }
// one-line facts behind the timing verdict, incl. a near-term event overhang if any
function timingFacts(t) {
  if (!t) return '';
  const oh = t.has_upcoming_overhang ? ` · <span class="neg">⚠ ${t.nearest_overhang_id} in ${t.nearest_overhang_days_until}d</span>` : '';
  return `RSI ${num(t.rsi_14, 0)} · vol×${num(t.vol_ratio_10_90, 2)} · 5d ${num(t.return_5d_pct)}% · drawdown ${num(t.drawdown_from_20d_high_pct)}%${t.stabilizing ? ' · basing' : ''}${oh}`;
}
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
// A line chart with axes (default 0–100 scale; pass o.maxY for a custom top, e.g. exposure %),
// gridlines, x date ticks, legend.
function lineChart(series, dates, o = {}) {
  const n = dates.length;
  if (n < 2) return '<p class="hint">Only one run so far — no trend to chart yet.</p>';
  const W = o.w || 560, H = o.h || 210, padL = 28, padR = 12, padT = 10, padB = 28;
  const maxY = o.maxY || 100;
  const X = (i) => padL + (i / (n - 1)) * (W - padL - padR);
  const Y = (v) => padT + (1 - v / maxY) * (H - padT - padB);
  let grid = '';
  [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(f * maxY)).forEach((g) => {
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
    `SELECT sector_id, rank, composite, catalyst_alignment, momentum, flow_confirmation, flow_data_quality, flow_source, flow_proxy_ticker, flow_proxy_used, flow_carried_from, flow_volume_cmf, flow_window_days, flow_days_covered, crowding_risk, narrative_maturity, primary_etf, regime_state
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
const movementsForSector = (sid) => (DOCS.movements || []).filter((m) => m.sector_id === sid);
const movementsForCatalyst = (cid) => (DOCS.movements || []).filter((m) => (m.attribution || []).some((a) => a.catalyst_id === cid));
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
// label the book's tracking mode: a genuine live curve vs the hypothetical backtest while accruing
function trackPill(p) {
  if (p.track_mode === 'live') return `<span class="pill g" title="walk-forward live track record since ${p.inception}">live</span>`;
  if (p.track_mode === 'accruing') return `<span class="pill a" title="live track record starts ${p.inception}; showing the hypothetical backtest until it accrues">accruing</span>`;
  return '';
}
function portfolioCard(p) {
  const beat = (p.vs_benchmark_pct ?? 0) >= 0;
  const sp = spark([{ values: p.nav, color: 'var(--accent)' }, { values: p.benchmark_nav, color: '#8b949e' }], { w: 230, h: 44 });
  const m = p.metrics || {};
  return `<a class="card click ${p.portfolio_id === curPf ? 'sel' : ''}" href="#/portfolios/${p.portfolio_id}">
    <div class="lbl">${escapeHtml(p.name || p.portfolio_id)} ${trackPill(p)}</div>
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

  // alerts — ONE unified ticket per opportunity sector: dislocation (WHY/IS-IT-CHEAP) + regime +
  // entry-timing (WHEN). Timing lives INSIDE each ticket (a separate list repeated the same sectors).
  // Full per-sector timing for every sector lives on the dedicated Timing page.
  const d = OV.dislocation;
  let alerts = '';
  if (d) {
    const opp = (d.opportunities || []).slice(0, 6);
    if (opp.length) alerts += `<h3 style="margin-top:0">Opportunities — panic dips</h3>
      <p class="hint" style="margin:-2px 0 10px">Fell hard but fundamentals intact & catalyst-confirmed. The chip is <i>when</i> to enter — click for <a href="#/timing">full timing →</a>.</p>`
      + opp.map((o) => {
        const t = timingFor(o.sector_id);
        const r5 = t && t.return_5d_pct != null ? t.return_5d_pct : o.drawdown_pct;  // the −5D% move
        return `<a class="rowlink" style="display:flex;justify-content:space-between;align-items:center;gap:8px;text-decoration:none;color:inherit;padding:8px 6px;border-top:1px solid var(--border)" href="#/timing/${o.sector_id}">
          <span class="nm"><b>${o.sector_id}</b> <span class="pill b">${o.primary_etf || '—'}</span></span>
          <span style="display:flex;gap:8px;align-items:center;flex-shrink:0"><span class="neg">${num(r5)}% 5d</span> ${verdictPill(t)}</span>
        </a>`;
      }).join('');
    const div = (d.diversifiers || []).slice(0, 4);
    if (div.length) alerts += '<h3>Diversifiers — rotation targets</h3>'
      + '<p class="hint" style="margin:-2px 0 8px">Healthy & LOW correlation to the stressed cluster — where to rotate without re-buying the same bet.</p>'
      + div.map((g) => `<a class="rowlink barrow" style="grid-template-columns:minmax(120px,1fr) 56px 64px;text-decoration:none;color:inherit" href="#/sectors/${g.sector_id}">
        <span class="nm">${g.sector_id} <span class="pill b">${g.primary_etf || '—'}</span></span>
        <span class="v">ρ ${num(g.mean_corr_to_stressed, 2)}</span><span class="v">${num(g.composite, 0)}</span></a>`).join('');
  }
  // exit watch — positions whose pre-committed stop fired / regime broke / assumption violated
  const exAll = (OV.exit_signal && OV.exit_signal.by_etf) ? Object.values(OV.exit_signal.by_etf) : [];
  const exAct = exAll.filter((e) => e.suggested_action === 'exit' || e.suggested_action === 'reduce')
    .sort((a, b) => (a.suggested_action === 'exit' ? 0 : 1) - (b.suggested_action === 'exit' ? 0 : 1));
  if (exAct.length) {
    alerts += '<h3>Exit watch — positions needing action</h3>'
      + '<p class="hint" style="margin:-2px 0 8px">A pre-committed stop fired, the regime is breaking, or an assumption was violated. Recommend-only — review on <a href="#/positions">Positions →</a>.</p>'
      + exAct.map((e) => `<a class="rowlink" style="display:flex;justify-content:space-between;align-items:center;gap:8px;text-decoration:none;color:inherit;padding:8px 6px;border-top:1px solid var(--border)" href="#/positions">
        <span class="nm"><b>${e.sector_id}</b> <span class="pill b">${e.etf}</span></span>
        <span style="display:flex;gap:8px;align-items:center;flex-shrink:0">${e.loudest_fired_id ? `<span class="lbl">${e.loudest_fired_id}</span>` : ''}${exitBadge(e)}</span>
      </a>`).join('');
  }
  $('ov-alerts').innerHTML = alerts ? `<div class="card">${alerts}</div>` : '<p class="hint">No dislocation / timing analysis yet.</p>';

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
// Heatmap columns (higher = better). valuation_relative was REMOVED from the model in schema
// 1.2 (it was a constant-50 placeholder that only diluted the ranking; no price-derived metric
// earned its weight — see the backtest). crowding is shown as a categorical label (derives from
// narrative_maturity).
const SEC_COLS = [
  { k: 'composite', label: 'composite', bold: true, tip: 'Blend used for the ranking (higher = better)' },
  { k: 'catalyst_alignment', label: 'catalyst', tip: 'How strongly active catalysts support the sector' },
  { k: 'momentum', label: 'momentum', tip: 'Cross-sectional price-momentum percentile' },
  { k: 'flow_confirmation', label: 'flow', tip: 'ETF net share-flow (creation/redemption), as a moving average over the last 7 days. ᴾ = real flow via same-theme proxy ETF; ↻ = carried from last reading; ⚠ = NOT real flow, price+volume approximation; ~ = no reading (neutral 50)' },
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
// ── flow provenance: flag when flow_confirmation is a proxy or a no-data placeholder
//    rather than a clean own-vehicle reading (see catalyx/data/flow_data.py FLOW_PROXY).
function flowMark(row) {
  const dq = row.flow_data_quality;
  if (dq === 'computed') return '';
  const src = row.flow_source ? ` [${row.flow_source}]` : '';
  if (dq === 'proxy_computed') {
    const tk = row.flow_proxy_ticker || 'proxy';
    return `<sup style="color:var(--accent,#3b82f6);font-weight:700;cursor:help" title="Real share-flow via sibling ETF ${tk}${src} — the tradeable vehicle exposes no share data">ᴾ</sup>`;
  }
  if (dq === 'carried') {
    const from = row.flow_carried_from ? ` (from ${String(row.flow_carried_from).slice(0, 10)})` : '';
    return `<sup style="color:var(--accent,#3b82f6);font-weight:700;cursor:help" title="Carried forward${from} — no fresh reading this run (market closed), last genuine value reused">↻</sup>`;
  }
  if (dq === 'volume_proxy') {
    // small red symbol — distinct (NOT real flow, a price+volume proxy) but unobtrusive
    const cmf = row.flow_volume_cmf != null ? ` (CMF ${(+row.flow_volume_cmf).toFixed(2)})` : '';
    return `<sup style="color:var(--red,#dc2626);font-weight:700;cursor:help" title="⚠ NOT real flow — price+volume approximation${cmf} (no share data from any source). Diverges from true creation/redemption; treat with caution.">⚠</sup>`;
  }
  // 'estimated' OR null/undefined → neutral 50, not a real reading
  return '<sup style="color:var(--amber);font-weight:700;cursor:help" title="No flow reading available — neutral 50 placeholder, not a real value">~</sup>';
}
function flowHeatCell(s) {
  const v = s.flow_confirmation;
  if (v == null || v === '') return '<td style="text-align:center"><span class="lbl">—</span></td>';
  return `<td style="text-align:center"><span class="score" style="background:${heatColor(v)}">${num(v, 0)}</span>${flowMark(s)}</td>`;
}
function flowProvNote(row) {
  const dq = row.flow_data_quality;
  if (dq === 'computed') return '';
  let msg;
  if (!dq || dq === 'estimated')
    msg = '⚠ no flow reading available — shown as a neutral <b>50</b> placeholder, not a real value';
  else if (dq === 'carried') {
    const from = row.flow_carried_from ? ` from <b>${String(row.flow_carried_from).slice(0, 10)}</b>` : '';
    msg = `↻ carried forward${from} — this run had no fresh reading (market closed → no share data), so the last genuine value is reused (a closed market has no new flow)`;
  } else if (dq === 'volume_proxy') {
    const cmf = row.flow_volume_cmf != null ? ` (raw CMF ${(+row.flow_volume_cmf).toFixed(2)})` : '';
    msg = `<b style="color:var(--red,#dc2626)">⚠ NOT real flow</b> — price+volume approximation${cmf}: no share-count data from any source (yfinance/stockanalysis/iShares), so Chaikin Money Flow stands in. It DIVERGES from true creation/redemption — treat with caution and review the sources`;
  } else {
    const tk = row.flow_proxy_ticker || 'proxy';
    const src = row.flow_source ? ` [${row.flow_source}]` : '';
    msg = `ᴾ real share-flow via sibling <b>${tk}</b>${src} — the tradeable vehicle exposes no share data, so the same-theme proxy stands in`;
  }
  return `<div class="hint" style="grid-column:1/-1;margin-top:-2px">${msg}</div>`;
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
      ${SEC_COLS.map((c) => c.k === 'flow_confirmation' ? flowHeatCell(s) : heatCell(s[c.k], c.bold)).join('')}
      <td>${crowdLabel(s.crowding_risk)}</td>
      <td>${regimePill(s.regime_state)}</td>
      <td>${moveBadge(s.sector_id)}</td>
      <td class="go" title="open sector report">${chevron}</td>
    </tr>`).join('') || `<tr><td colspan="${SEC_COLS.length + 6}" class="lbl" style="padding:14px">no match</td></tr>`;
  $('sec-table').innerHTML = `<div class="cmp"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`
    + `<p class="hint" style="margin-top:8px">Cell colour: <b style="color:var(--green)">green</b> high · <b style="color:var(--amber)">amber</b> mid · <b style="color:var(--red)">red</b> low (higher = better for all shown). <b>crowding</b> low is better. On <b>flow</b>: plain = real share-flow · <sup style="color:var(--accent,#3b82f6);font-weight:700">ᴾ</sup> = real flow via same-theme proxy ETF · <sup style="color:var(--accent,#3b82f6);font-weight:700">↻</sup> = carried from last reading (market closed) · <sup style="color:var(--red,#dc2626);font-weight:700">⚠</sup> = NOT real flow, price+volume approximation (review) ·<sup style="color:var(--amber);font-weight:700">~</sup> = no reading, neutral 50. <code>valuation</code> was removed from the model (schema 1.2) — it never moved the ranking and no price-derived metric earned its weight.</p>`;
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
  const movs = movementsForSector(sid);
  const cats = (study && study.active_catalyst_ids) || [];
  const holders = holdingPortfolios(sid);
  const chips = [];
  cats.forEach((c) => chips.push(link('catalysts', c, c)));
  movs.forEach((m) => chips.push(`<span class="pill g" title="${escapeHtml(m.id)}">${m.action}: €${m.amount_eur ?? '—'}</span>`));
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
        <div class="barrow" style="grid-template-columns:130px 1fr 40px"><span class="lbl">flow${flowMark(row)}</span>${bar(row.flow_confirmation || 0)}<span class="v">${num(row.flow_confirmation, 0)}</span></div>
        ${flowProvNote(row)}
        <div class="barrow" style="grid-template-columns:130px 1fr"><span class="lbl">crowding</span><span>${crowdLabel(row.crowding_risk)} <span class="lbl">(${num(row.crowding_risk, 0)})</span></span></div>
      </div>
      ${(() => { const t = timingFor(sid); return t ? `<h3>Entry timing <span class="lbl" style="text-transform:none;letter-spacing:0">— when to enter (<a href="#/timing">all sectors →</a>)</span></h3>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:4px">${verdictPill(t)} ${statePill(t)}</div>
        <div class="lbl">${timingFacts(t)}</div>` : ''; })()}
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

// ── ENTRY TIMING (dedicated page — full per-sector detail, sortable) ──────────────
let TIM_FILTER = '', TIM_CAVEAT = false, TIM_SORT = { k: 'opp', dir: 1 }, timWired = false;
// sectors flagged as dislocation opportunities (panic dips worth buying) → float to the top
function oppScores() { const m = {}; ((OV.dislocation || {}).opportunities || []).forEach((o, i) => { m[o.sector_id] = o.opportunity_score != null ? o.opportunity_score : (1e6 - i); }); return m; }
// two opportunity classes in the timing table:
//   'dip'    = a dislocation panic dip (fell hard, intact, catalyst-confirmed, composite floor)
//   'strong' = NOT a dip, but high composite AND neutral timing → a clean "buy-ready" entry (scores
//              high on our strategy with no near-term tension). The user wanted these marked too.
const STRONG_COMPOSITE = 66;   // green zone — "scores high on our strategy"
function oppClass(r, opp) {
  if (r.sector_id in opp) return 'dip';
  if ((r.composite || 0) >= STRONG_COMPOSITE && r.micro_timing_state === 'neutral'
      && !r.has_upcoming_overhang) return 'strong';
  return null;
}
const TIM_COLS = [
  { k: 'tension_score', label: 'tension', tip: 'higher = more tense to enter now (0–100)' },
  { k: 'rsi_14', label: 'RSI', tip: 'Wilder RSI(14): >70 overbought (chasing), <30 oversold (knife)' },
  { k: 'vol_ratio_10_90', label: 'vol×', tip: '10d / 90d realized-vol ratio (>1.5 = elevated tension)' },
  { k: 'stretch_vs_ma20_pct', label: 'stretch%', tip: '% distance from the 20-day MA' },
  { k: 'return_5d_pct', label: '5d%', tip: '5-day return' },
  { k: 'trend_deadband_pct', label: 'band±', tip: 'noise band on the 5d gate (k·σ·√5, vol-scaled): a 5d move smaller than this reads flat → neutral, not falling. So |5d%| > band± is what makes a name "falling".' },
  { k: 'drawdown_from_20d_high_pct', label: 'draw%', tip: 'drawdown from the 20-day high' },
];
const _CHEV = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>';
function timingRows() { const t = OV.entry_timing; return (t && t.by_sector) ? Object.values(t.by_sector) : []; }
function renderTiming(id) {
  if (id) TIM_FILTER = id;  // deep-linked from an opportunity card → focus that sector
  const t = OV.entry_timing, m = (t && t.meta) || {};
  $('timing-market').innerHTML = !t ? '' : [
    ['VIX', m.vix != null ? num(m.vix, 1) : '—', m.vix_5d_change != null ? `Δ5d ${signed(m.vix_5d_change, 1)}` : 'volatility'],
    ['S&P 500', m.spy_5d_pct != null ? signed(m.spy_5d_pct) + '%' : '—', '5-day · market backdrop'],
    ['as of', m.as_of || '—', t ? t.run_id : ''],
  ].map(([l, v, s]) => `<div class="card"><div class="lbl">${l}</div><div class="big">${v}</div><div class="lbl">${s}</div></div>`).join('');
  if (!timWired) {
    const si = $('tim-search'), cb = $('tim-caveat');
    if (si) si.oninput = (e) => { TIM_FILTER = e.target.value; drawTimTable(); };
    if (cb) cb.onchange = (e) => { TIM_CAVEAT = e.target.checked; drawTimTable(); };
    timWired = true;
  }
  const si = $('tim-search'), cb = $('tim-caveat'); if (si) si.value = TIM_FILTER; if (cb) cb.checked = TIM_CAVEAT;
  drawTimTable();
}
function drawTimTable() {
  if (!OV.entry_timing) { $('timing-table').innerHTML = '<p class="hint">No timing run yet — run <code>entry_timing --all</code> in the heatmap pipeline.</p>'; return; }
  const f = TIM_FILTER.toLowerCase();
  const opp = oppScores();
  // attach the sector's composite (the full blend) so it sorts/shows next to the opportunity flag —
  // the combined score is the philosophy anchor: a dip only matters if we'd own the sector at all.
  let rows = timingRows()
    .map((r) => ({ ...r, composite: (rankingRow(r.sector_id) || {}).composite }))
    .filter((r) => (!f || r.sector_id.toLowerCase().includes(f)) && (!TIM_CAVEAT || r.micro_timing_state !== 'neutral'));
  const k = TIM_SORT.k, dir = TIM_SORT.dir;
  if (k === 'opp') {
    // THREE tiers: dislocation dips → strong+neutral (buy-ready) → the rest; WITHIN each tier by
    // composite desc (the philosophy anchor). dir just toggles (dir=-1 reverses the whole list).
    const tier = (r) => ({ dip: 0, strong: 1 }[oppClass(r, opp)] ?? 2);
    rows = rows.slice().sort((a, b) => {
      const ta = tier(a), tb = tier(b);
      if (ta !== tb) return ta - tb;
      return (b.composite || 0) - (a.composite || 0);
    });
    if (dir < 0) rows.reverse();
  } else {
    rows = rows.slice().sort((a, b) => { const av = a[k], bv = b[k]; if (av == null) return 1; if (bv == null) return -1; return (av < bv ? -1 : av > bv ? 1 : 0) * dir; });
  }
  const arrow = (c) => TIM_SORT.k === c ? `<span class="ar">${TIM_SORT.dir > 0 ? '▲' : '▼'}</span>` : '';
  const head = `<th data-sort="sector_id">sector${arrow('sector_id')}</th>`
    + `<th class="num" data-sort="composite" title="composite — the full blend (catalyst + momentum + flow + crowding). Our philosophy anchor: a dip is only an opportunity if the combined score is one we'd own.">composite${arrow('composite')}</th>`
    + `<th data-sort="opp" title="dislocation opportunity — fell hard but intact & catalyst-confirmed AND composite above the floor (panic dip). These float to the top.">opp${arrow('opp')}</th>`
    + `<th data-sort="micro_timing_state">state${arrow('micro_timing_state')}</th>`
    + `<th data-sort="suggested_verdict">verdict${arrow('suggested_verdict')}</th>`
    + TIM_COLS.map((c) => `<th class="num" data-sort="${c.k}" title="${c.tip}">${c.label}${arrow(c.k)}</th>`).join('')
    + `<th title="near-term event overhang (discrete catalyst in the window)">overhang</th><th></th>`;
  const mkt = (OV.entry_timing && OV.entry_timing.meta) || {};
  const oppCell = (cls, r) => cls === 'dip'
    ? '<span class="pill g" title="dislocation opportunity — panic dip">opportunity</span>'
    : cls === 'strong'
      // substantiate the claim with the raw micro-numbers (not a bald "buy-ready") + the macro
      // backdrop — a neutral ETF in a risk-off tape still warrants a human check, so the verdict is a
      // suggestion, not a vetted call.
      ? `<span class="pill b" title="high composite (${num(r.composite, 0)}) + neutral micro-timing: RSI ${num(r.rsi_14, 0)}, ${signed(r.stretch_vs_ma20_pct, 1)}% vs MA20, vol× ${num(r.vol_ratio_10_90, 2)} — not extended. Suggests buy-ready; verify backdrop (VIX ${mkt.vix != null ? num(mkt.vix, 1) : '—'}, S&P 5d ${mkt.spy_5d_pct != null ? signed(mkt.spy_5d_pct, 1) + '%' : '—'}).">strong · neutral</span>`
      : '<span class="lbl">—</span>';
  const edge = (cls) => cls === 'dip' ? ' style="box-shadow:inset 3px 0 0 var(--green)"'
    : cls === 'strong' ? ' style="box-shadow:inset 3px 0 0 var(--accent)"' : '';
  const body = rows.map((r) => { const cls = oppClass(r, opp); return `<tr data-sid="${r.sector_id}"${edge(cls)}>
      <td><b>${r.sector_id}</b> <span class="pill b">${r.primary_etf || '—'}</span></td>
      <td class="num"><b style="color:${scoreColor(r.composite || 0)}">${num(r.composite, 0)}</b></td>
      <td>${oppCell(cls, r)}</td>
      <td>${statePill(r)}</td>
      <td>${verdictPill(r)}</td>
      <td class="num">${num(r.tension_score, 0)}</td>
      <td class="num">${num(r.rsi_14, 0)}</td>
      <td class="num">${num(r.vol_ratio_10_90, 2)}</td>
      <td class="num">${num(r.stretch_vs_ma20_pct)}</td>
      <td class="num ${r.return_5d_pct < 0 ? 'neg' : ''}">${num(r.return_5d_pct)}</td>
      <td class="num lbl">±${num(r.trend_deadband_pct)}</td>
      <td class="num neg">${num(r.drawdown_from_20d_high_pct)}</td>
      <td>${r.has_upcoming_overhang ? `<span class="pill r" title="${r.nearest_overhang_date || ''}">⚠ ${r.nearest_overhang_id} · ${r.nearest_overhang_days_until}d</span>` : '<span class="lbl">—</span>'}</td>
      <td class="go" title="open sector detail">${_CHEV}</td>
    </tr>`; }).join('') || '<tr><td colspan="14" class="lbl" style="padding:14px">no match</td></tr>';
  $('timing-table').innerHTML = `<div class="cmp"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  $('timing-table').querySelector('thead').onclick = (ev) => {
    const th = ev.target.closest('th[data-sort]'); if (!th) return; const col = th.dataset.sort;
    if (TIM_SORT.k === col) TIM_SORT.dir *= -1; else TIM_SORT = { k: col, dir: (col === 'sector_id' || col === 'opp') ? 1 : -1 };
    drawTimTable();
  };
  $('timing-table').querySelector('tbody').onclick = (ev) => {
    const tr = ev.target.closest('tr[data-sid]'); if (!tr) return; location.hash = '#/sectors/' + tr.dataset.sid;
  };
}

// ── CATALYSTS & POSITIONS (sub-tabbed: structural / event / movement, all rich) ──
let CAT_KIND = 'structural', curCat = null, catWired = false;
const structuralDoc = (id) => (DOCS.catalysts_structural || []).find((c) => c.id === id);
const eventDoc = (id) => (DOCS.catalysts_event || []).find((c) => c.id === id);
const movementDoc = (id) => (DOCS.movements || []).find((m) => m.id === id);
function catKindOf(id) {
  if (structuralDoc(id)) return 'structural';
  if (eventDoc(id)) return 'event';
  if (movementDoc(id)) return 'movement';
  return null;
}
function catItems(kind) {
  if (kind === 'event') return (DOCS.catalysts_event || []).map((c) => ({ id: c.id, label: c.id, score: c.strength_score }));
  if (kind === 'movement') return (DOCS.movements || []).map((m) => ({ id: m.id, label: m.id, status: m.action }));
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
  else if (kind === 'movement') el.innerHTML = movementDetailHTML(movementDoc(id));
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
  const sectors = sectorsForCatalyst(c.id), movs = movementsForCatalyst(c.id);
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
    ${movs.length ? `<h3>Positions</h3><div class="chips">${movs.map((m) => link('catalysts', m.id, m.id)).join('')}</div>` : ''}
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
function movementDetailHTML(m) {
  if (!m) return '<p class="hint">Select a position.</p>';
  const sid = m.sector_id, v = m.vehicle || {}, sc = m.score_context || {}, rd = m.risk_discipline || {};
  const headLinks = [];
  if (sid) headLinks.push('sector ' + sectorLink(sid));
  if (v.etf) headLinks.push(`<span class="pill b">${escapeHtml(v.etf)}</span>`);
  (m.attribution || []).forEach((a) => headLinks.push('catalyst ' + link('catalysts', a.catalyst_id, `${a.catalyst_id} (${Math.round((a.weight || 0) * 100)}%)`)));
  const sec = (title, html) => html ? `<h3>${title}</h3>${html}` : '';
  const chips = [];
  if (m.conviction) chips.push(`<span class="pill">conviction ${m.conviction}</span>`);
  if (m.trigger) chips.push(`<span class="pill">${escapeHtml(String(m.trigger))}</span>`);
  if (m.amount_eur != null) chips.push(`<span class="pill b">€${m.amount_eur}</span>`);
  if (m.price != null) chips.push(`<span class="pill">@ ${m.price} ${v.currency || ''}</span>`);
  const scChips = [];
  if (sc.composite != null) scChips.push(`<span class="pill">composite ${sc.composite}</span>`);
  if (sc.catalyst_alignment != null) scChips.push(`<span class="pill">catalyst ${sc.catalyst_alignment}</span>`);
  if (sc.regime_state) scChips.push(`<span class="pill ${sc.regime_state === 'intact' ? 'g' : 'r'}">${sc.regime_state}</span>`);
  return `<div class="card">
    ${catHeader(`${m.id} · ${m.action} · ${String(m.executed_at || '').slice(0, 10)}`, m.id, null, null)}
    <div style="margin-top:2px"><span class="pill g">${m.action}</span> ${headLinks.join(' · ')}</div>
    ${sec('Trade', `<div class="chips">${chips.join('')}</div>`)}
    ${sec('Score context at entry (point-in-time)', scChips.length ? `<div class="chips">${scChips.join('')}</div>` : '<span class="lbl">not yet ingested</span>')}
    ${sec('Note', rd.note ? `<div class="md">${md(rd.note)}</div>` : '')}
    ${sec('Assumptions', Array.isArray(rd.assumptions) && rd.assumptions.length ? `<ul>${rd.assumptions.map((a) => `<li>${fmtMeta(a)}</li>`).join('')}</ul>` : '')}
    ${sec('Invalidation conditions', Array.isArray(rd.invalidation) && rd.invalidation.length ? `<ul>${rd.invalidation.map((a) => `<li>${fmtMeta(a)}</li>`).join('')}</ul>` : '')}
  </div>`;
}

// ── exit-watch helpers (shared by Positions panel/inline badge + Overview alert) ──
const _EXIT_BADGE = { exit: ['🔴', 'EXIT', 'r'], reduce: ['🟠', 'REDUCE', 'a'], watch: ['🟡', 'WATCH', 'a'], hold: ['🟢', 'HOLD', 'g'] };
function exitSigFor(etf) { return ((OV.exit_signal || {}).by_etf || {})[etf] || null; }
function exitBadge(es) {
  if (!es || !_EXIT_BADGE[es.suggested_action]) return '';
  const b = _EXIT_BADGE[es.suggested_action];
  const why = es.loudest_fired_id ? `stop ${es.loudest_fired_id} fired`
    : es.assumptions_violated ? 'an assumption is violated'
      : (es.regime_state && es.regime_state !== 'intact') ? `regime ${es.regime_state}`
        : (es.n_approaching ? 'a stop is approaching' : (es.assumptions_weakening ? 'an assumption is weakening' : 'no signal fired'));
  return `<span class="pill ${b[2]}" title="${why} — recommend-only">${b[0]} ${b[1]}</span>`;
}

// Assumption statements + statuses live on the movement's risk_discipline — gather them per vehicle
// so the exit-watch row can EXPAND into "what's holding / weakening / violated", not just counts.
const _ASM_ST = {
  holding: ['g', '✓', 'holding'], monitoring: ['b', '◔', 'monitoring'],
  weakening: ['a', '~', 'weakening'], violated: ['r', '✗', 'violated'], unverified: ['', '?', 'unverified'],
};
function assumptionsForEtf(etf) {
  const out = [];
  for (const mv of (DOCS.movements || [])) {
    if (!mv || (mv.vehicle || {}).etf !== etf) continue;
    if (mv.action && !['open', 'add'].includes(mv.action)) continue;
    for (const a of ((mv.risk_discipline || {}).assumptions || [])) out.push(a);
  }
  return out;
}
function assumptionsCell(etf, fallback) {
  const list = assumptionsForEtf(etf);
  if (!list.length) return fallback;
  const c = { holding: 0, monitoring: 0, weakening: 0, violated: 0, unverified: 0 };
  list.forEach((a) => { const s = a.current_status || 'unverified'; c[s] = (c[s] || 0) + 1; });
  const ok = c.holding + c.monitoring + c.unverified;
  const summary = `${ok}✓${c.weakening ? ` <span class="neg">${c.weakening}~</span>` : ''}${c.violated ? ` <span class="neg">${c.violated}✗</span>` : ''}`;
  const items = list.map((a) => {
    const st = _ASM_ST[a.current_status] || _ASM_ST.unverified;
    return `<div class="asm-item"><span class="pill ${st[0]}">${st[1]} ${st[2]}</span> ${a.statement || a.assumption || ''}`
      + `${a.status_note ? `<div class="lbl">${a.status_note}</div>` : ''}</div>`;
  }).join('');
  return `<details class="asm" onclick="event.stopPropagation()"><summary>${summary}`
    + `<span class="asm-chev">${_CHEV}</span></summary><div class="asm-detail">${items}</div></details>`;
}

// ── POSITIONS (the real book — its own page, portfolio-style + full ledger) ──────
function renderPositions() {
  const pos = OV.positions || { holdings: [], total_invested_eur: 0, realized_eur: 0 };
  const book = OV.positions_book;                 // kind='real' portfolio_nav (NAV + metrics)
  const led = OV.catalyst_ledger || [];
  const movs = (DOCS.movements || []).slice().sort((a, b) => String(b.executed_at).localeCompare(String(a.executed_at)));
  const inv = pos.total_invested_eur || 0;
  const m = (book && book.metrics) || {}, bm = (book && book.bench_metrics) || {};
  const mc = (l, v, cls, sub) => `<div class="card"><div class="lbl">${l}</div><div class="big ${cls || ''}">${v}</div>${sub ? `<div class="lbl">${sub}</div>` : ''}</div>`;
  const beat = (book && (book.vs_benchmark_pct ?? 0) >= 0);

  // ── summary strip — headline is MARK-TO-MARKET vs cost (your real unrealized P&L), not the
  //    entry-date-indexed NAV (which is ~flat for a young book and never marks against avg cost) ──
  const mv = pos.market_value_eur;            // qty × last price, summed (best-effort fetch)
  const up = pos.unrealized_eur, upct = pos.unrealized_pct;
  const cap = pos.total_capital_eur, cash = pos.cash_eur;
  const cards = [];
  // committed capital + dry powder — the book is funded up front, deployed as catalysts fire
  if (cap != null) cards.push(mc('committed capital', '€' + num(cap, 0), '', pos.deployed_pct != null ? num(pos.deployed_pct, 0) + '% deployed' : 'allocated to this book'));
  cards.push(mc('invested', '€' + num(inv, 0), '', `${pos.holdings.length} position${pos.holdings.length === 1 ? '' : 's'}`));
  if (cash != null) cards.push(mc('cash', '€' + num(cash, 0), '', 'dry powder · awaiting catalysts'));
  if (mv != null) {
    cards.push(mc('current value', '€' + num(mv, 0), (up ?? 0) >= 0 ? 'pos' : 'neg', signed(upct) + '% vs cost'));
    cards.push(mc('unrealized P&L', (up >= 0 ? '+' : '−') + '€' + num(Math.abs(up), 0), up >= 0 ? 'pos' : 'neg', 'marked at last close'));
  } else {
    cards.push(mc('current value', '—', '', 'price fetch unavailable'));
  }
  cards.push(mc('realized P&L', '€' + num(pos.realized_eur, 0), (pos.realized_eur ?? 0) >= 0 ? 'pos' : 'neg', 'closed legs'));
  if (book && book.vs_benchmark_pct != null) cards.push(mc('vs ' + (book.benchmark_etf || 'SPY'), signed(book.vs_benchmark_pct) + 'pp', beat ? 'pos' : 'neg', beat ? 'beating market' : 'below market · since inception'));
  $('pos-summary').innerHTML = cards.join('');

  // ── NAV vs SPY ──
  $('pos-nav').innerHTML = (book && book.nav && book.nav.length > 1)
    ? `<div class="card"><div class="lbl" style="margin-bottom:6px">Book NAV vs ${book.benchmark_etf || 'SPY'} — indexed 100 · ${book.n_days}d ${book.n_days < 5 ? '<span class="pill a">young book</span>' : ''}</div>${spark([{ values: book.nav, color: 'var(--accent)' }, { values: book.benchmark_nav, color: '#8b949e' }], { w: 600, h: 90 })}</div>`
    : '<p class="hint">NAV curve appears once the book has ≥2 daily points. Buy-and-hold from entry; ETFs without yfinance history are held as cash.</p>';

  // ── holdings (marked to market vs avg cost) ──
  const hrows = (pos.holdings || []).map((h) => `<tr data-sid="${h.sector_id}">
      <td><b>${h.sector_id}</b> <span class="pill b">${h.etf}</span></td>
      <td class="num">${num(h.qty)}</td>
      <td class="num">€${num(h.invested_eur, 0)}</td>
      <td class="num">${num(h.avg_cost, 2)}</td>
      <td class="num">${h.last_price != null ? num(h.last_price, 2) : '—'}</td>
      <td class="num">${h.market_value_eur != null ? '€' + num(h.market_value_eur, 0) : '—'}</td>
      <td class="num ${(h.unrealized_eur ?? 0) >= 0 ? 'pos' : 'neg'}">${h.unrealized_eur != null ? (h.unrealized_eur >= 0 ? '+' : '−') + '€' + num(Math.abs(h.unrealized_eur), 0) + ' (' + signed(h.unrealized_pct) + '%)' : '—'}</td>
      <td class="num">${num(h.weight_pct)}%</td>
      <td class="go">${_CHEV}</td>
    </tr>`).join('') || '<tr><td colspan="9" class="lbl" style="padding:14px">no open positions</td></tr>';
  $('pos-holdings').innerHTML = `<div class="cmp"><table><thead><tr>
      <th>position</th><th class="num">qty</th><th class="num">invested</th><th class="num">avg cost</th>
      <th class="num">last</th><th class="num">mkt value</th><th class="num">unrealized P&L</th>
      <th class="num">weight</th><th></th></tr></thead><tbody>${hrows}</tbody></table></div>`;
  $('pos-holdings').querySelector('tbody').onclick = (ev) => { const tr = ev.target.closest('tr[data-sid]'); if (tr) location.hash = '#/sectors/' + tr.dataset.sid; };

  // ── exit watch (Family 1: stops + assumptions + regime + after-tax) — recommend-only ──
  const exHas = OV.exit_signal && OV.exit_signal.by_etf;
  const exrows = (pos.holdings || []).map((h) => {
    const e = exitSigFor(h.etf);
    if (!e) return `<tr data-sid="${h.sector_id}"><td><b>${h.sector_id}</b> <span class="pill b">${h.etf}</span></td><td colspan="5" class="lbl">no exit signal this run</td></tr>`;
    const clear = (e.n_stops || 0) - (e.n_fired || 0) - (e.n_approaching || 0);
    const stops = [];
    if (e.n_fired) stops.push(`<span class="pill r" title="${e.fired_ids || ''}">✅ ${e.n_fired} fired${e.fired_ids ? ': ' + e.fired_ids : ''}</span>`);
    if (e.n_approaching) stops.push(`<span class="pill a" title="${e.approaching_ids || ''}">⚠ ${e.n_approaching} near</span>`);
    if (clear > 0) stops.push(`<span class="pill g">· ${clear} clear</span>`);
    if (e.n_claude_check) stops.push(`<span class="pill b" title="${e.claude_check_ids || ''}">🔍 ${e.n_claude_check} check</span>`);
    const okA = (e.assumptions_total || 0) - (e.assumptions_violated || 0) - (e.assumptions_weakening || 0);
    const asmFallback = e.assumptions_total ? `${okA}✓${e.assumptions_weakening ? ` <span class="neg">${e.assumptions_weakening}~</span>` : ''}${e.assumptions_violated ? ` <span class="neg">${e.assumptions_violated}✗</span>` : ''}` : '<span class="lbl">—</span>';
    const asm = assumptionsCell(h.etf, asmFallback);
    const reg = e.regime_state && e.regime_state !== 'intact' ? `<span class="pill a">${e.regime_state}</span>` : '<span class="lbl">intact</span>';
    const taxc = (e.harvestable_loss_eur != null && e.harvestable_loss_eur > 0)
      ? `loss €${num(e.harvestable_loss_eur, 0)} <span class="lbl">harvestable</span>`
      : (e.tax_due_eur != null ? `net €${num(e.net_proceeds_eur, 0)} <span class="lbl">after €${num(e.tax_due_eur, 0)} CGT</span>` : '—');
    return `<tr data-sid="${h.sector_id}">
      <td><b>${h.sector_id}</b> <span class="pill b">${h.etf}</span></td>
      <td>${exitBadge(e)}</td>
      <td>${stops.join(' ') || '—'}</td>
      <td>${asm}</td>
      <td>${reg}</td>
      <td class="num">${taxc}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="6" class="lbl" style="padding:14px">no open positions</td></tr>';
  $('pos-exit').innerHTML = exHas
    ? `<div class="cmp"><table><thead><tr><th>position</th><th>signal</th><th>stops</th><th>assumptions</th><th>regime</th><th class="num">exit after-tax</th></tr></thead><tbody>${exrows}</tbody></table></div>`
    : '<p class="hint">No exit-watch run yet — run <code>uv run python -m catalyx.scorer.exit_watcher</code> (persists per run).</p>';
  const ext = $('pos-exit').querySelector('tbody');
  if (ext) ext.onclick = (ev) => { const tr = ev.target.closest('tr[data-sid]'); if (tr) location.hash = '#/sectors/' + tr.dataset.sid; };

  // ── movements (buys & sells) — references catalyst(s) + dates, no catalyst detail duplicated ──
  const actCls = (a) => a === 'open' ? 'g' : (a === 'close' || a === 'trim') ? 'r' : '';
  const mrows = movs.map((mv) => {
    const v = mv.vehicle || {}, sc = mv.score_context || {};
    const cats = (mv.attribution || []).map((a) =>
      `<a class="pill b" style="text-decoration:none" href="#/catalysts/${encodeURIComponent(a.catalyst_id)}">${a.catalyst_id} ${Math.round((a.weight || 0) * 100)}%</a>`).join(' ');
    const score = [sc.composite != null ? `comp ${num(sc.composite, 0)}` : '', sc.rank != null ? `#${sc.rank}` : '',
      sc.regime_state ? sc.regime_state : ''].filter(Boolean).join(' · ');
    return `<tr>
      <td>${String(mv.executed_at || '').slice(0, 10)}</td>
      <td><span class="pill ${actCls(mv.action)}">${mv.action}</span></td>
      <td><a href="#/sectors/${mv.sector_id}" style="color:inherit"><b>${mv.sector_id}</b></a> <span class="pill b">${v.etf || ''}</span></td>
      <td class="num">€${num(mv.amount_eur, 0)}</td>
      <td class="num">${num(mv.qty)} @ ${num(mv.price, 2)}</td>
      <td>${mv.conviction ? `<span class="pill">${mv.conviction}</span> ` : ''}${mv.trigger ? `<span class="pill">${mv.trigger}</span>` : ''}</td>
      <td>${cats || '<span class="lbl">—</span>'}</td>
      <td class="lbl">${score || '—'}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="8" class="lbl" style="padding:14px">no movements yet</td></tr>';
  $('pos-movements').innerHTML = `<div class="cmp"><table><thead><tr>
      <th>date</th><th>action</th><th>position</th><th class="num">amount</th><th class="num">qty @ price</th>
      <th>tags</th><th>catalyst(s)</th><th>score@entry</th></tr></thead><tbody>${mrows}</tbody></table></div>`;

  // ── catalyst exposure (the ledger) ──
  const lrows = led.map((l) => `<tr>
      <td><a href="#/catalysts/${encodeURIComponent(l.catalyst_id)}" style="color:inherit"><b>${l.catalyst_id}</b></a></td>
      <td class="num">€${num(l.invested_eur, 0)}</td>
      <td class="num">${num(inv ? l.invested_eur / inv * 100 : 0, 0)}%</td>
      <td>${(l.sectors || []).map(sectorLink).join(', ')}</td>
      <td class="num">${l.n_movements}</td>
    </tr>`).join('') || '<tr><td colspan="5" class="lbl" style="padding:14px">no catalyst attribution yet</td></tr>';
  $('pos-catalysts').innerHTML = `<div class="cmp"><table><thead><tr>
      <th>catalyst</th><th class="num">invested</th><th class="num">% of book</th><th>sectors</th>
      <th class="num">movements</th></tr></thead><tbody>${lrows}</tbody></table></div>`;

  // ── experiment ledger (closed positions scored as experiments) ──
  const xp = OV.experiment_ledger || [];
  const vClr = { skill: '#16a34a', luck: '#d97706', variance: '#2563eb', correct_invalidation: '#ea580c', indeterminate: '#6b7280' };
  const xrows = xp.map((e) => {
    const at = e.after_tax_pnl_eur;
    const flags = (e.behavioral_flags || '').split(',').filter(Boolean);
    const fpills = flags.map((f) => `<span class="pill r" title="behavioral deviation">${f.split(':')[0]}</span>`).join(' ');
    const note = e.exit_note ? `<div class="lbl" style="margin-top:3px">“${e.exit_note}”</div>` : '';
    const cf = e.verdict_confidence === 'low' ? ' <span class="lbl">(low conf)</span>' : '';
    return `<tr>
      <td>${String(e.executed_at || '').slice(0, 10)}</td>
      <td><a href="#/sectors/${e.sector_id}" style="color:inherit"><b>${e.sector_id}</b></a> <span class="pill b">${e.etf || ''}</span></td>
      <td><b style="color:${vClr[e.verdict_label] || '#6b7280'}">${(e.verdict_label || '—').replace(/_/g, '-')}</b>${cf}</td>
      <td class="num" style="color:${at != null && at < 0 ? '#dc2626' : '#16a34a'}">€${num(at, 0)}</td>
      <td class="num">${e.return_pct != null ? num(e.return_pct, 1) + '%' : '—'}</td>
      <td class="num">${e.holding_days != null ? e.holding_days + 'd' : '—'}</td>
      <td>${fpills || '<span class="lbl">—</span>'}${note}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="7" class="lbl" style="padding:14px">no closed experiments yet — they appear here after /catalyx-close</td></tr>';
  $('pos-ledger').innerHTML = `<div class="cmp"><table><thead><tr>
      <th>closed</th><th>position</th><th>verdict</th><th class="num">after-tax</th>
      <th class="num">return</th><th class="num">held</th><th>behavior &amp; note</th></tr></thead><tbody>${xrows}</tbody></table></div>`;

  // ── rotation targets (anchored to the book's holdings) ──
  const rot = OV.positions_rotation || [];
  const rrows = rot.map((d) => `<tr data-sid="${d.sector_id}">
      <td><b>${d.sector_id}</b> <span class="pill b">${d.primary_etf || '—'}</span></td>
      <td class="num"><b style="color:${scoreColor(d.composite || 0)}">${num(d.composite, 0)}</b></td>
      <td class="num">${num(d.corr_to_book, 2)}</td>
      <td class="num">${num(d.diversifier_score, 0)}</td>
      <td class="go">${_CHEV}</td>
    </tr>`).join('') || '<tr><td colspan="5" class="lbl" style="padding:14px">no rotation run yet — run dislocation --anchor-sectors with your holdings</td></tr>';
  $('pos-rotation').innerHTML = `<div class="cmp"><table><thead><tr>
      <th>sector</th><th class="num">composite</th><th class="num">corr to book</th>
      <th class="num">fit score</th><th></th></tr></thead><tbody>${rrows}</tbody></table></div>`;
  const rt = $('pos-rotation').querySelector('tbody');
  if (rt) rt.onclick = (ev) => { const tr = ev.target.closest('tr[data-sid]'); if (tr) location.hash = '#/sectors/' + tr.dataset.sid; };
}

// ── PORTFOLIOS ─────────────────────────────────────────────────────────────────
let curPf = null;
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

  const trackLine = p.track_mode === 'live'
    ? `live track record · since ${p.inception} · vs ${p.benchmark_etf || 'SPY'} · ${p.n_days || '—'}d`
    : p.track_mode === 'accruing'
      ? `live track record · since ${p.inception} · <b>accruing</b> · vs ${p.benchmark_etf || 'SPY'}`
      : `${p.kind || ''} · vs ${p.benchmark_etf || 'SPY'} · ${p.n_days || '—'}d backtest`;
  const accruingNote = p.track_mode === 'accruing'
    ? `<p class="hint" style="margin:-6px 0 14px">⏳ The live walk-forward curve starts at inception (${p.inception}) and grows one run at a time — not enough history yet. The chart below is the <b>hypothetical single-snapshot backtest</b> (today's holdings projected back), kept for reference only until the live record accrues.</p>`
    : '';
  $('pf-detail').innerHTML = `
    <h2 style="margin-top:0">${escapeHtml(p.name || pid)} ${trackPill(p)} <span class="lbl" style="font-weight:400">${trackLine}</span></h2>
    ${accruingNote}
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
  renderCatalystExposure(pid);
}
// Catalyst exposure of the SELECTED portfolio over time. We assume a fixed notional (€1000)
// split across the holdings; each holding's weight is divided equally among the catalysts that
// drive its sector → the % of the book exposed to each catalyst. Recorded at every rebalance
// (lake table portfolio_catalyst_exposure, computed by portfolio.py), so we can chart how the
// book's catalyst mix shifts over time + a time-weighted average. Baked into overview.json by
// build_site → zero WASM. The label for a catalyst id comes from docs.json (catalystDoc).
function catalystLabel(cid) {
  if (cid === 'cash') return 'Cash (undeployed)';
  if (cid === 'uncatalyzed') return 'Uncatalyzed';
  return (catalystDoc(cid) || {}).title || cid;
}
function renderCatalystExposure(pid) {
  const box = $('pf-lineage');
  if (!box) return;
  const p = (OV.portfolios || []).find((x) => x.portfolio_id === pid) || {};
  const ce = p.catalyst_exposure || {};
  const ts = ce.timeseries || [], avg = ce.average || [];
  if (!ts.length) { box.innerHTML = '<p class="hint">No catalyst exposure recorded for this strategy yet — it is written at each portfolio build.</p>'; return; }
  const notional = ce.notional_eur || 1000;
  const COLOR = (cid) => cid === 'cash' ? '#8b949e' : 'var(--accent)';

  // current composition (latest rebalance): one bar per catalyst, € on the notional
  const latest = ts[ts.length - 1];
  const comp = Object.entries(latest.by_catalyst).sort((a, b) => b[1] - a[1]);
  const mx = Math.max(...comp.map(([, v]) => v), 1);
  const compBars = comp.map(([cid, v]) => `
    <div class="barrow" style="grid-template-columns:minmax(150px,1.6fr) 1fr 60px 64px">
      <span class="nm">${escapeHtml(catalystLabel(cid))} <span class="pill b">${cid}</span></span>
      ${bar(v, mx, COLOR(cid))}
      <span class="v">${num(v, 1)}%</span>
      <span class="lbl">€${num(v / 100 * notional, 0)}</span></div>`).join('');
  const composition = `<div class="card" style="margin-bottom:14px">
    <div class="lbl" style="text-transform:uppercase;letter-spacing:.5px;font-size:10px">Current composition — €${num(notional, 0)} notional · rebalance ${latest.date}</div>
    ${compBars}</div>`;

  // exposure over time: one line per catalyst (auto-scaled). Only meaningful with ≥2 rebalances.
  let chart;
  if (ts.length > 1) {
    const cats = [...new Set(ts.flatMap((t) => Object.keys(t.by_catalyst)))];
    const dates = ts.map((t) => t.date);
    const PAL = ['#58a6ff', '#d29922', '#3fb950', '#a371f7', '#f85149', '#56d4dd', '#db61a2', '#c9d1d9', '#e3b341', '#79c0ff'];
    const series = cats.map((cid, i) => ({ label: catalystLabel(cid), color: cid === 'cash' ? '#8b949e' : PAL[i % PAL.length], values: ts.map((t) => t.by_catalyst[cid] || 0) }));
    const maxY = Math.max(10, Math.ceil(Math.max(...series.flatMap((s) => s.values), 0) / 10) * 10);
    chart = `<div class="card" style="margin-bottom:14px"><h3 style="margin-top:0">Exposure over time
      <span class="lbl" style="font-weight:400">— each rebalance is a point</span></h3>${lineChart(series, dates, { maxY })}</div>`;
  } else {
    chart = `<p class="hint">Only one rebalance so far (${latest.date}) — the over-time chart and the time-weighted average populate from the next recompute.</p>`;
  }

  // time-weighted average (weighted by how long each allocation was live) — only with ≥2 rebalances
  let avgTbl = '';
  if (ts.length > 1 && avg.length) {
    const amx = Math.max(...avg.map((a) => a.avg_pct), 1);
    avgTbl = `<div class="card"><h3 style="margin-top:0">Time-weighted average exposure
      <span class="lbl" style="font-weight:400">— weighted by how long each allocation was held</span></h3>`
      + avg.map((a) => `<div class="barrow" style="grid-template-columns:minmax(150px,1.6fr) 1fr 60px 64px">
        <span class="nm">${escapeHtml(catalystLabel(a.catalyst_id))} <span class="pill b">${a.catalyst_id}</span></span>
        ${bar(a.avg_pct, amx, COLOR(a.catalyst_id))}
        <span class="v">${num(a.avg_pct, 1)}%</span>
        <span class="lbl">€${num(a.avg_eur, 0)}</span></div>`).join('') + '</div>';
  }

  box.innerHTML = composition + chart + avgTbl;
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
const RENDER = { overview: renderOverview, sectors: renderSectors, timing: renderTiming, catalysts: renderCatalysts, positions: renderPositions, portfolios: renderPortfolios, data: renderData };
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
