// CATALYX dashboard — DuckDB-WASM over the parquet lake + Tier-1 docs (Fase F v3, visual).
// Scalable: parquet registered by URL (lazy HTTP range reads); docs.json + tabs load on demand.
import * as duckdb from 'https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.0/+esm';
import { marked } from 'https://cdn.jsdelivr.net/npm/marked@12/+esm';

const $ = (id) => document.getElementById(id);
const status = (m) => { $('status').textContent = m; };
let conn = null;
let DOCS = { catalysts_structural: [], catalysts_event: [], studies: [], theses: [] };
let docsLoaded = false;
const tables = new Set();

async function ensureDocs() {
  if (docsLoaded) return;
  try { DOCS = await (await fetch('docs.json')).json(); } catch (e) { console.warn('docs.json', e); }
  docsLoaded = true;
}

// ── DuckDB ───────────────────────────────────────────────────────────────────
async function initDuckDB() {
  const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
  const workerURL = URL.createObjectURL(new Blob([`importScripts("${bundle.mainWorker}");`], { type: 'text/javascript' }));
  const worker = new Worker(workerURL);
  const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(), worker);
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  URL.revokeObjectURL(workerURL);
  return db;
}
async function loadLake(db) {
  const manifest = await (await fetch('manifest.json')).json();
  conn = await db.connect();
  for (const [table, files] of Object.entries(manifest)) {
    const names = [];
    for (const f of files) {
      const name = f.replaceAll('/', '_');
      await db.registerFileURL(name, new URL(f, document.baseURI).href, duckdb.DuckDBDataProtocol.HTTP, false);
      names.push(`'${name}'`);
    }
    if (!names.length) continue;
    await conn.query(`CREATE OR REPLACE VIEW "${table}" AS SELECT * FROM read_parquet([${names.join(',')}], union_by_name=true)`);
    tables.add(table);
  }
}
function sqlLiteral(v) { if (v === null || v === undefined) return 'NULL'; if (typeof v === 'number') return String(v); return "'" + String(v).replace(/'/g, "''") + "'"; }
async function q(sql, params) {
  let final = sql;
  if (params && params.length) { let i = 0; final = sql.replace(/\?/g, () => sqlLiteral(params[i++])); }
  const res = await conn.query(final);
  return res.toArray().map((r) => Object.fromEntries(res.schema.fields.map((f) => [f.name, norm(r[f.name])])));
}
function norm(v) { if (typeof v === 'bigint') return Number(v); if (v && typeof v === 'object' && !Array.isArray(v) && 'toString' in v) return v.toString(); return v; }

// ── visual helpers ────────────────────────────────────────────────────────────
function escapeHtml(s) { return String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])); }
function md(t) { try { return marked.parse(t || ''); } catch (e) { return '<pre>' + escapeHtml(t || '') + '</pre>'; } }
function err(where, e) { console.error(e); $(where).innerHTML = `<div class="err">${(e && e.message) || e}</div>`; }
function scoreColor(v) { return v >= 66 ? 'var(--green)' : v >= 40 ? 'var(--amber)' : 'var(--red)'; }
function num(v, d = 1) { return (v === null || v === undefined) ? '—' : (Number.isInteger(v) ? v : Number(v).toFixed(d)); }
function maturityPill(m) { const c = { emerging: 'b', mainstream: 'a', crowded: 'r', exhausted: 'r' }[m] || ''; return m ? `<span class="pill ${c}">${m}</span>` : ''; }
function bar(v, max = 100, color) { const p = Math.max(0, Math.min(100, (v / max) * 100)); return `<div class="bar"><i style="width:${p}%;background:${color || scoreColor(v)}"></i></div>`; }
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
  const cols = Object.keys(rows[0]); const signed = new Set(opts.signed || []);
  const head = cols.map((c) => `<th>${c}</th>`).join('');
  const body = rows.map((r) => '<tr>' + cols.map((c) => {
    let v = r[c]; let cls = (typeof v === 'number') ? 'num' : '';
    if (signed.has(c) && typeof v === 'number') cls += v >= 0 ? ' pos' : ' neg';
    if (typeof v === 'number' && !Number.isInteger(v)) v = Number(v.toFixed(2));
    return `<td class="${cls}">${v ?? ''}</td>`;
  }).join('') + '</tr>').join('');
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}
// Structured doc render (no raw JSON): scalars as rows, long strings as markdown, arrays as lists.
function renderDoc(obj) {
  const scal = []; const blocks = [];
  for (const [k, v] of Object.entries(obj)) {
    if (k.startsWith('$') || k === 'schema_version' || v == null || v === '') continue;
    if (typeof v === 'string') { if (v.length > 80) blocks.push(`<h3>${k}</h3><div class="md" style="border:none;padding:0;max-width:none">${md(v)}</div>`); else scal.push([k, v]); }
    else if (typeof v === 'number' || typeof v === 'boolean') scal.push([k, v]);
    else if (Array.isArray(v)) {
      if (!v.length) continue;
      blocks.push(`<h3>${k}</h3><ul>${v.map((x) => `<li>${escapeHtml(typeof x === 'object' ? JSON.stringify(x) : String(x))}</li>`).join('')}</ul>`);
    } else if (typeof v === 'object') blocks.push(`<h3>${k}</h3>${renderDoc(v)}`);
  }
  const dl = scal.map(([k, v]) => `<div class="barrow" style="grid-template-columns:200px 1fr"><span class="lbl">${k}</span><span>${escapeHtml(String(v))}</span></div>`).join('');
  return `<div class="card">${dl}${blocks.join('')}</div>`;
}

// ── Ranking ──────────────────────────────────────────────────────────────────
async function renderRanking() {
  try {
    const rows = await q(`SELECT sector_id, rank, composite, momentum, crowding_risk, narrative_maturity, primary_etf
      FROM sector_snapshot WHERE run_id = (SELECT max(run_id) FROM sector_snapshot) ORDER BY rank LIMIT 25`);
    $('ranking-out').innerHTML = '<div class="card">' + rows.map((r) => `
      <div class="barrow" style="grid-template-columns:30px minmax(150px,240px) 1fr 64px 96px">
        <span class="v">#${r.rank}</span>
        <span class="nm" title="${r.sector_id}">${r.sector_id} <span class="pill b">${r.primary_etf || '—'}</span></span>
        ${bar(r.composite)}
        <span class="v">mom ${num(r.momentum, 0)}</span>
        <span>${maturityPill(r.narrative_maturity)}</span>
      </div>`).join('') + '</div>';
  } catch (e) { err('ranking-out', e); }
}

// ── Catalysts ────────────────────────────────────────────────────────────────
async function initCatalysts() {
  await ensureDocs();
  $('cat-select').innerHTML = (DOCS.catalysts_structural || []).map((c, i) => `<option value="${i}">${c.id} — ${c.title || ''}</option>`).join('') || '<option>(none)</option>';
  $('cat-select').addEventListener('change', renderCatalyst);
  renderCatEvents();
  await renderCatalyst();
}
async function renderCatalyst() {
  const c = (DOCS.catalysts_structural || [])[$('cat-select').value || 0];
  if (!c) { $('cat-detail').innerHTML = '<p class="hint">(no catalysts)</p>'; return; }
  const hist = {};
  if (tables.has('indicator_history')) {
    try {
      const h = await q(`SELECT indicator_id, value FROM indicator_history WHERE catalyst_id = ? ORDER BY indicator_id, date`, [c.id]);
      h.forEach((r) => { (hist[r.indicator_id] = hist[r.indicator_id] || []).push(r.value); });
    } catch (e) { /* ignore */ }
  }
  const intn = (c.intensity || {}).current_score;
  const inds = (c.indicators || []).map((i) => {
    const sp = (hist[i.id] && hist[i.id].length > 1) ? spark([{ values: hist[i.id], color: 'var(--accent)' }], { w: 140, h: 26 }) : '<span class="lbl">no history</span>';
    return `<div style="margin:10px 0;padding:11px 13px;border:1px solid var(--border);border-radius:9px">
      <div style="display:flex;justify-content:space-between;gap:12px">
        <b style="font-weight:600">${escapeHtml(i.name || i.id)}</b>
        <span style="color:${scoreColor(i.score || 0)};font-weight:600">${i.score ?? '—'}</span>
      </div>
      ${bar(i.score || 0)}
      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:6px">
        <span class="lbl">value: <b>${i.current_value ?? '—'}</b> ${i.unit || ''}</span>${sp}
      </div></div>`;
  }).join('');
  $('cat-detail').innerHTML = `<div class="card">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px">
      <div><div class="lbl">${c.id} · ${c.catalyst_type || ''} · ${c.status || ''}</div>
        <div style="font-size:17px;font-weight:600;margin-top:2px">${c.title || c.id}</div></div>
      <div style="text-align:right"><div class="lbl">intensity</div>
        <div class="big" style="color:${scoreColor(intn || 0)}">${intn ?? '—'}</div></div>
    </div>
    <div class="md" style="border:none;padding:10px 0 0;max-width:none">${md(c.description || '')}</div>
    <h3>Indicators</h3>${inds}</div>`;
}
function renderCatEvents() {
  $('cat-events').innerHTML = (DOCS.catalysts_event || []).map((e) => `<div class="card">
    <div class="lbl">${e.id} <span class="pill">${e.catalyst_type || ''}</span></div>
    <div style="margin:7px 0;font-size:13px">${escapeHtml((e.description || '').slice(0, 280))}${(e.description || '').length > 280 ? '…' : ''}</div>
    <div class="lbl">strength ${e.strength_score ?? '—'} · novelty ${e.novelty_score ?? '—'} · ${e.status || ''}</div>
  </div>`).join('') || '<p class="hint">(no events)</p>';
}

// ── Studies ──────────────────────────────────────────────────────────────────
async function initStudies() {
  await ensureDocs();
  $('study-select').innerHTML = (DOCS.studies || []).map((s, i) => `<option value="${i}">${s.sector_id || s.id || ('study ' + i)}</option>`).join('') || '<option>(none)</option>';
  $('study-select').addEventListener('change', renderStudy);
  renderStudy();
}
function renderStudy() {
  const s = (DOCS.studies || [])[$('study-select').value || 0];
  $('study-detail').innerHTML = s ? renderDoc(s) : '<p class="hint">(no studies)</p>';
}

// ── Runs & reports ────────────────────────────────────────────────────────────
let RUNS = [];
async function initReports() {
  if (!tables.has('score_run')) { $('run-meta').innerHTML = '<p class="hint">(no runs)</p>'; return; }
  RUNS = await q(`SELECT run_id, substr(CAST(run_at AS VARCHAR),1,19) AS at, scoring_version, git_commit, sector_count FROM score_run ORDER BY run_id DESC`);
  $('run-select').innerHTML = RUNS.map((r) => `<option>${r.run_id}</option>`).join('');
  $('run-select').addEventListener('change', renderRun);
  await renderRun();
}
async function renderRun() {
  const rid = $('run-select').value; if (!rid) return;
  const r = RUNS.find((x) => x.run_id === rid) || {};
  const card = (l, v) => `<div class="card"><div class="lbl">${l}</div><div style="font-weight:600">${v ?? '—'}</div></div>`;
  $('run-meta').innerHTML = card('run · ' + (r.at || ''), r.run_id) + card('scoring_version', r.scoring_version)
    + card('git', r.git_commit) + `<div class="card"><div class="lbl">sectors</div><div class="big">${r.sector_count ?? '—'}</div></div>`;
  if (tables.has('report')) {
    try {
      const reps = await q(`SELECT report_type, content_md FROM report WHERE run_id = ? ORDER BY report_type`, [rid]);
      $('report-out').innerHTML = (reps.length && reps.some((x) => x.content_md))
        ? reps.map((x) => `<h3>${x.report_type}</h3>` + md(x.content_md)).join('')
        : '<p class="hint">No text report associated with this run.</p>';
    } catch (e) { err('report-out', e); }
  } else { $('report-out').innerHTML = '<p class="hint">(no reports)</p>'; }
}

// ── Portfolios ────────────────────────────────────────────────────────────────
async function initPortfolios() {
  if (tables.has('portfolio_nav')) {
    const pids = (await q(`SELECT DISTINCT portfolio_id FROM portfolio_nav ORDER BY portfolio_id`)).map((r) => r.portfolio_id);
    let html = '';
    for (const pid of pids) {
      const series = await q(`SELECT nav, benchmark_nav, return_pct, vs_benchmark_pct, benchmark_etf FROM portfolio_nav WHERE portfolio_id = ? ORDER BY date`, [pid]);
      const last = series[series.length - 1] || {};
      const sp = spark([
        { values: series.map((s) => s.nav), color: 'var(--accent)' },
        { values: series.map((s) => s.benchmark_nav), color: '#8b949e' },
      ], { w: 250, h: 50 });
      const beat = (last.vs_benchmark_pct ?? 0) >= 0;
      html += `<div class="card">
        <div class="lbl">${pid}</div>
        <div class="big ${(last.return_pct ?? 0) >= 0 ? 'pos' : 'neg'}">${(last.return_pct ?? 0) >= 0 ? '+' : ''}${num(last.return_pct)}%</div>
        <div style="margin:6px 0">${sp}</div>
        <div class="lbl">vs ${last.benchmark_etf || 'SPY'} <span class="pill ${beat ? 'g' : 'r'}">${beat ? '+' : ''}${num(last.vs_benchmark_pct)}pp</span> ${beat ? 'beat the market' : 'below market'}</div>
      </div>`;
    }
    $('pf-cards').innerHTML = html || '<p class="hint">(no NAV)</p>';
  } else { $('pf-cards').innerHTML = '<p class="hint">No NAV yet. Run <code>nav_engine model &lt;portfolio&gt; --backtest-days 180</code>.</p>'; }

  if (tables.has('portfolio_holding')) {
    const rows = await q(`SELECT DISTINCT portfolio_id FROM portfolio_holding ORDER BY portfolio_id`);
    $('pf-select').innerHTML = rows.map((r) => `<option>${r.portfolio_id}</option>`).join('');
    $('pf-select').addEventListener('change', renderHoldings);
  }
  await renderHoldings();
}
async function renderHoldings() {
  try {
    if (!tables.has('portfolio_holding') || !$('pf-select').value) { $('holdings-out').innerHTML = ''; return; }
    const pid = $('pf-select').value;
    const rows = await q(`SELECT rank_in_portfolio AS n, sector_id, primary_etf, weight_pct, composite, momentum, entry_price, narrative_maturity
      FROM portfolio_holding WHERE portfolio_id = ? AND run_id = (SELECT max(run_id) FROM portfolio_holding WHERE portfolio_id = ?) ORDER BY rank_in_portfolio`, [pid, pid]);
    const mx = Math.max(...rows.map((r) => r.weight_pct), 1);
    $('holdings-out').innerHTML = '<div class="card">' + rows.map((r) => `
      <div class="barrow" style="grid-template-columns:minmax(150px,250px) 1fr 52px 190px">
        <span class="nm">${r.sector_id} <span class="pill b">${r.primary_etf}</span></span>
        ${bar(r.weight_pct, mx, 'var(--accent)')}
        <span class="v">${num(r.weight_pct)}%</span>
        <span class="v">comp ${num(r.composite, 0)} · mom ${num(r.momentum, 0)} · entry ${r.entry_price ?? '—'}</span>
      </div>`).join('') + '</div>';
  } catch (e) { err('holdings-out', e); }
}

// ── Opportunities & Rotation (dislocation lens) ────────────────────────────────
function regimePill(s) { const c = { intact: 'g', contested: 'a', breaking: 'r' }[s] || ''; return `<span class="pill ${c}">${s}</span>`; }
async function initOpportunities() {
  try {
    if (!tables.has('dislocation')) {
      $('opp-out').innerHTML = '<p class="hint">No dislocation analysis yet. Run <code>uv run python -m catalyx.scorer.dislocation --window 5</code> (persists to the lake), then rebuild.</p>';
      return;
    }
    const latest = (await q(`SELECT max(run_id) AS r FROM dislocation`))[0].r;
    const meta = (await q(`SELECT DISTINCT window_days, benchmark, market_window_pct FROM dislocation WHERE run_id = ?`, [latest]))[0] || {};
    $('opp-meta').innerHTML = `<p class="hint">run ${latest} · ${meta.window_days}-day window · market(${meta.benchmark}) <b class="${(meta.market_window_pct ?? 0) >= 0 ? 'pos' : 'neg'}">${num(meta.market_window_pct)}%</b></p>`;

    const opps = await q(`SELECT sector_id, primary_etf, drawdown_pct, contagion_explained_pct, idiosyncratic_pct, contagion_fraction, catalyst_alignment, opportunity_score
      FROM dislocation WHERE run_id = ? AND lens = 'opportunity' ORDER BY opportunity_score DESC`, [latest]);
    $('opp-out').innerHTML = opps.length ? '<div class="grid">' + opps.map((o) => {
      const frac = o.contagion_fraction == null ? null : Math.round(o.contagion_fraction * 100);
      const clean = frac != null && frac >= 70;
      return `<div class="card">
        <div class="lbl">${o.sector_id} <span class="pill b">${o.primary_etf}</span></div>
        <div class="big neg">${num(o.drawdown_pct)}%</div>
        <div class="lbl">contagion ${num(o.contagion_explained_pct)}% · idiosyncratic ${num(o.idiosyncratic_pct)}%</div>
        ${frac != null ? bar(frac, 100, clean ? 'var(--green)' : 'var(--amber)') : ''}
        <div class="lbl" style="margin-top:6px">${frac != null ? frac + '% contagion' : 'β n/a'} · catalyst ${num(o.catalyst_alignment, 0)}
          <span class="pill ${clean ? 'g' : 'a'}">${clean ? 'clean panic dip' : 'investigate residual'}</span></div>
      </div>`;
    }).join('') + '</div>' : '<p class="hint">No intact sector has a meaningful contagion-driven drawdown right now.</p>';

    const divs = await q(`SELECT sector_id, primary_etf, composite, mean_corr_to_stressed AS corr_to_stressed, diversifier_score
      FROM dislocation WHERE run_id = ? AND lens = 'diversifier' ORDER BY diversifier_score DESC`, [latest]);
    $('div-out').innerHTML = tableHTML(divs);

    const reg = await q(`SELECT sector_id, regime_state, drawdown_pct, catalyst_alignment, composite
      FROM dislocation WHERE run_id = ? AND regime_state <> 'intact' ORDER BY regime_state, drawdown_pct`, [latest]);
    $('regime-out').innerHTML = reg.length
      ? '<div class="card">' + reg.map((r) => `<div class="barrow" style="grid-template-columns:minmax(150px,250px) 110px 1fr 120px">
          <span class="nm">${r.sector_id} <span class="pill b">${r.primary_etf || ''}</span></span>
          ${regimePill(r.regime_state)}
          <span class="v ${(r.drawdown_pct ?? 0) >= 0 ? 'pos' : 'neg'}">${num(r.drawdown_pct)}%</span>
          <span class="v">catalyst ${num(r.catalyst_alignment, 0)} · comp ${num(r.composite, 0)}</span>
        </div>`).join('') + '</div>'
      : '<p class="hint">All sectors intact — no live contradictions.</p>';
  } catch (e) { err('opp-out', e); }
}

// ── Sector history ────────────────────────────────────────────────────────────
async function initSector() {
  const rows = await q(`SELECT DISTINCT sector_id FROM sector_snapshot ORDER BY sector_id`);
  $('sector-select').innerHTML = rows.map((r) => `<option>${r.sector_id}</option>`).join('');
  $('sector-select').addEventListener('change', renderSector);
  await renderSector();
}
async function renderSector() {
  try {
    const sid = $('sector-select').value;
    const rows = await q(`SELECT run_id, substr(CAST(snapshot_at AS VARCHAR),1,10) AS date, rank, composite, momentum, catalyst_alignment, crowding_risk
      FROM sector_snapshot WHERE sector_id = ? ORDER BY run_id`, [sid]);
    $('sector-out').innerHTML = tableHTML(rows);
    const nar = await q(`SELECT rationale_md FROM sector_snapshot WHERE sector_id = ? AND rationale_md IS NOT NULL ORDER BY run_id DESC LIMIT 1`, [sid]);
    $('sector-narrative').innerHTML = (nar.length && nar[0].rationale_md) ? md(nar[0].rationale_md) : '<p class="hint">(this sector has no narrative)</p>';
  } catch (e) { err('sector-out', e); }
}

// ── Rank moves / lineage / sql ────────────────────────────────────────────────
async function renderMoves() {
  try {
    if (!tables.has('rank_event')) { $('moves-out').innerHTML = '<p class="hint">(no rank events; needs more than one distinct run)</p>'; return; }
    const rows = await q(`SELECT sector_id, event_type, from_rank, to_rank, delta FROM rank_event WHERE run_id = (SELECT max(run_id) FROM rank_event) ORDER BY abs(coalesce(delta,99)) DESC`);
    $('moves-out').innerHTML = tableHTML(rows, { signed: ['delta'] });
  } catch (e) { err('moves-out', e); }
}
async function initLineage() {
  if (!tables.has('portfolio_trade')) { $('lineage-out').innerHTML = '<p class="hint">No trades (use trade_logger).</p>'; $('trade-select').innerHTML = '<option>(no trades)</option>'; return; }
  const rows = await q(`SELECT trade_id FROM portfolio_trade ORDER BY date DESC`);
  $('trade-select').innerHTML = rows.map((r) => `<option>${r.trade_id}</option>`).join('') || '<option>(no trades)</option>';
  $('trade-select').addEventListener('change', renderLineage);
  await renderLineage();
}
async function renderLineage() {
  try {
    const tid = $('trade-select').value;
    if (!tables.has('portfolio_trade') || !tid || tid.startsWith('(')) { $('lineage-out').innerHTML = '<p class="hint">No trades.</p>'; return; }
    const trade = (await q(`SELECT * FROM portfolio_trade WHERE trade_id = ?`, [tid]))[0];
    let html = '<h3>Trade</h3>' + tableHTML([trade]);
    if (trade && trade.run_id) {
      if (tables.has('report')) { const reps = await q(`SELECT report_type, report_date, path FROM report WHERE run_id = ?`, [trade.run_id]); html += '<h3>Run reports</h3>' + tableHTML(reps); }
      if (tables.has('sector_snapshot') && trade.etf) { const snap = await q(`SELECT sector_id, rank, composite, momentum, catalyst_alignment FROM sector_snapshot WHERE run_id = ? AND primary_etf = ?`, [trade.run_id, trade.etf]); html += '<h3>Sector scores in that run</h3>' + tableHTML(snap); }
    }
    $('lineage-out').innerHTML = html;
  } catch (e) { err('lineage-out', e); }
}
async function runSQL() {
  try { const rows = await q($('sql-input').value.replace(/;\s*$/, '')); $('sql-out').innerHTML = `<p class="hint">${rows.length} rows</p>` + tableHTML(rows); } catch (e) { err('sql-out', e); }
}

// ── tabs (lazy) ───────────────────────────────────────────────────────────────
const TAB_INIT = { catalysts: initCatalysts, studies: initStudies, reports: initReports, portfolios: initPortfolios, opportunities: initOpportunities, sector: initSector, moves: renderMoves, lineage: initLineage };
const inited = new Set();
function setupTabs() {
  $('tabs').addEventListener('click', async (ev) => {
    const b = ev.target.closest('button'); if (!b) return;
    const tab = b.dataset.tab;
    document.querySelectorAll('#tabs button').forEach((x) => x.classList.toggle('active', x === b));
    document.querySelectorAll('.panel').forEach((p) => p.classList.toggle('active', p.id === `panel-${tab}`));
    if (TAB_INIT[tab] && !inited.has(tab)) { inited.add(tab); try { await TAB_INIT[tab](); } catch (e) { console.error(e); } }
  });
  $('sql-run').addEventListener('click', runSQL);
}

// ── boot ──────────────────────────────────────────────────────────────────────
(async () => {
  try {
    setupTabs();
    status('starting DuckDB-WASM…');
    const db = await initDuckDB();
    status('registering lake (lazy)…');
    await loadLake(db);
    status(`${tables.size} tables (lazy) · docs on-demand`);
    await renderRanking();
  } catch (e) { status('error'); err('ranking-out', e); }
})();
