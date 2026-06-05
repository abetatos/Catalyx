// CATALYX dashboard — DuckDB-WASM over the parquet lake + Tier-1 docs (Fase F v2).
//
// Scalability: parquet files are registered by URL (registerFileURL), so DuckDB reads them
// LAZILY via HTTP range requests — only the row-groups/columns a query needs are fetched,
// never the whole lake. GitHub Pages supports ranges. Boot is instant and this scales to
// years of monthly data. Tabs render on first open (lazy UI), not all upfront.
import * as duckdb from 'https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.0/+esm';
import { marked } from 'https://cdn.jsdelivr.net/npm/marked@12/+esm';

const $ = (id) => document.getElementById(id);
const status = (m) => { $('status').textContent = m; };

let conn = null;
let DOCS = { catalysts_structural: [], catalysts_event: [], studies: [], theses: [] };
let docsLoaded = false;
const tables = new Set();

// Lazy: docs.json (Tier-1 documents) can be large as studies accrue — only fetch it the
// first time a doc-backed tab (Catalysts/Studies) is opened, never at boot.
async function ensureDocs() {
  if (docsLoaded) return;
  try { DOCS = await (await fetch('docs.json')).json(); } catch (e) { console.warn('docs.json', e); }
  docsLoaded = true;
}

// ── init ─────────────────────────────────────────────────────────────────────
async function initDuckDB() {
  const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
  const workerURL = URL.createObjectURL(
    new Blob([`importScripts("${bundle.mainWorker}");`], { type: 'text/javascript' }));
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
      // registerFileURL = lazy range reads (NOT a full download). The scalable path.
      await db.registerFileURL(name, new URL(f, document.baseURI).href, duckdb.DuckDBDataProtocol.HTTP, false);
      names.push(`'${name}'`);
    }
    if (!names.length) continue;
    await conn.query(
      `CREATE OR REPLACE VIEW "${table}" AS SELECT * FROM read_parquet([${names.join(',')}], union_by_name=true)`);
    tables.add(table);
  }
}

// ── query + helpers ──────────────────────────────────────────────────────────
function sqlLiteral(v) {
  if (v === null || v === undefined) return 'NULL';
  if (typeof v === 'number') return String(v);
  return "'" + String(v).replace(/'/g, "''") + "'";  // values come from controlled dropdowns
}
async function q(sql, params) {
  let final = sql;
  if (params && params.length) { let i = 0; final = sql.replace(/\?/g, () => sqlLiteral(params[i++])); }
  const res = await conn.query(final);
  return res.toArray().map((r) => Object.fromEntries(
    res.schema.fields.map((f) => [f.name, normalize(r[f.name])])));
}
function normalize(v) {
  if (typeof v === 'bigint') return Number(v);
  if (v && typeof v === 'object' && !Array.isArray(v) && 'toString' in v) return v.toString();
  return v;
}
function md(text) { try { return marked.parse(text || ''); } catch (e) { return '<pre>' + escapeHtml(text || '') + '</pre>'; } }
function escapeHtml(s) { return String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c])); }
function err(where, e) { console.error(e); $(where).innerHTML = `<div class="err">${(e && e.message) || e}</div>`; }

function tableHTML(rows, opts = {}) {
  if (!rows || !rows.length) return '<p class="hint">(sin datos)</p>';
  const cols = Object.keys(rows[0]);
  const signed = new Set(opts.signed || []);
  const head = cols.map((c) => `<th>${c}</th>`).join('');
  const body = rows.map((r) => '<tr>' + cols.map((c) => {
    let v = r[c];
    let cls = (typeof v === 'number') ? 'num' : '';
    if (signed.has(c) && typeof v === 'number') cls += v >= 0 ? ' pos' : ' neg';
    if (typeof v === 'number' && !Number.isInteger(v)) v = Number(v.toFixed(2));
    return `<td class="${cls}">${v ?? ''}</td>`;
  }).join('') + '</tr>').join('');
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}
const heading = (t) => `<h3 style="font-size:13px;color:var(--muted);margin:18px 0 6px">${t}</h3>`;

// ── Ranking (default tab) ─────────────────────────────────────────────────────
async function renderRanking() {
  try {
    const rows = await q(`SELECT sector_id, rank, composite, momentum, catalyst_alignment,
      crowding_risk, narrative_maturity, primary_etf FROM sector_snapshot
      WHERE run_id = (SELECT max(run_id) FROM sector_snapshot) ORDER BY rank LIMIT 25`);
    $('ranking-out').innerHTML = tableHTML(rows);
  } catch (e) { err('ranking-out', e); }
}

// ── Catalysts ─────────────────────────────────────────────────────────────────
async function initCatalysts() {
  await ensureDocs();
  const cats = DOCS.catalysts_structural || [];
  $('cat-select').innerHTML = cats.map((c, i) => `<option value="${i}">${c.id} — ${c.title || ''}</option>`).join('')
    || '<option>(ninguno)</option>';
  $('cat-select').addEventListener('change', renderCatalyst);
  renderCatEvents();
  await renderCatalyst();
}
async function renderCatalyst() {
  const c = (DOCS.catalysts_structural || [])[$('cat-select').value || 0];
  if (!c) { $('cat-detail').innerHTML = '<p class="hint">(sin catalizadores)</p>'; return; }
  const intn = c.intensity || {};
  $('cat-meta').textContent = `intensity ${intn.current_score ?? '—'} · ${c.catalyst_type || ''} · status ${c.status || '—'}`;
  const inds = (c.indicators || []).map((i) => ({
    id: i.id, name: i.name, value: i.current_value, unit: i.unit,
    strong: i.threshold_strong, weak: i.threshold_weak, score: i.score, color: i.semaphore,
  }));
  $('cat-detail').innerHTML =
    `<div class="md">${md('**' + (c.title || c.id) + '**\n\n' + (c.description || ''))}</div>`
    + heading('Indicadores') + tableHTML(inds);
  if (tables.has('indicator_history')) {
    try {
      const h = await q(`SELECT indicator_id, date, value, source FROM indicator_history
        WHERE catalyst_id = ? ORDER BY indicator_id, date`, [c.id]);
      $('cat-history').innerHTML = tableHTML(h);
    } catch (e) { err('cat-history', e); }
  } else { $('cat-history').innerHTML = '<p class="hint">(sin historia en el lake)</p>'; }
}
function renderCatEvents() {
  const ev = (DOCS.catalysts_event || []).map((e) => ({
    id: e.id, type: e.catalyst_type, strength: e.strength ?? e.strength_score,
    status: e.status, date: e.event_date ?? e.detected_at,
    summary: e.catalyst_summary ?? e.summary ?? e.headline,
  }));
  $('cat-events').innerHTML = tableHTML(ev);
}

// ── Sector studies ─────────────────────────────────────────────────────────────
async function initStudies() {
  await ensureDocs();
  const st = DOCS.studies || [];
  $('study-select').innerHTML = st.map((s, i) => `<option value="${i}">${s.sector_id || s.id || ('study ' + i)}</option>`).join('')
    || '<option>(ninguno)</option>';
  $('study-select').addEventListener('change', renderStudy);
  renderStudy();
}
function renderStudy() {
  const s = (DOCS.studies || [])[$('study-select').value || 0];
  if (!s) { $('study-detail').innerHTML = '<p class="hint">(sin estudios)</p>'; return; }
  const key = {
    sector_id: s.sector_id, narrative_maturity: s.narrative_maturity, study_type: s.study_type,
    analyst_narrative_score: s.analyst_narrative_score,
    updated_at: s.updated_at || s.last_updated || s.created_at,
  };
  $('study-detail').innerHTML = tableHTML([key]) + heading('JSON completo')
    + `<pre>${escapeHtml(JSON.stringify(s, null, 2))}</pre>`;
}

// ── Runs & reports ─────────────────────────────────────────────────────────────
let RUNS = [];
async function initReports() {
  if (!tables.has('score_run')) { $('run-meta').innerHTML = '<p class="hint">(sin runs)</p>'; return; }
  RUNS = await q(`SELECT run_id, substr(CAST(run_at AS VARCHAR),1,19) AS at, scoring_version,
    git_commit, sector_count FROM score_run ORDER BY run_id DESC`);
  $('run-select').innerHTML = RUNS.map((r) => `<option>${r.run_id}</option>`).join('');
  $('run-select').addEventListener('change', renderRun);
  await renderRun();
}
async function renderRun() {
  const rid = $('run-select').value; if (!rid) return;
  const r = RUNS.find((x) => x.run_id === rid) || {};
  $('run-meta').innerHTML =
    `<div class="card"><div class="lbl">run · ${r.at || ''}</div><div>${r.run_id}</div></div>`
    + `<div class="card"><div class="lbl">scoring_version</div><div>${r.scoring_version || '—'}</div></div>`
    + `<div class="card"><div class="lbl">git</div><div>${r.git_commit || '—'}</div></div>`
    + `<div class="card"><div class="lbl">sectores</div><div class="big">${r.sector_count ?? '—'}</div></div>`;
  if (tables.has('report')) {
    try {
      const reps = await q(`SELECT report_type, content_md FROM report WHERE run_id = ? ORDER BY report_type`, [rid]);
      $('report-out').innerHTML = reps.length && reps.some((x) => x.content_md)
        ? reps.map((x) => heading(x.report_type) + md(x.content_md)).join('')
        : '<p class="hint">No hay informe de texto asociado a este run.</p>';
    } catch (e) { err('report-out', e); }
  } else { $('report-out').innerHTML = '<p class="hint">(no hay tabla de informes)</p>'; }
}

// ── Portfolios ─────────────────────────────────────────────────────────────────
async function initPortfolios() {
  await renderPortfolios();
  if (tables.has('portfolio_holding')) {
    const rows = await q(`SELECT DISTINCT portfolio_id FROM portfolio_holding ORDER BY portfolio_id`);
    $('pf-select').innerHTML = rows.map((r) => `<option>${r.portfolio_id}</option>`).join('');
    $('pf-select').addEventListener('change', renderHoldings);
  }
  await renderHoldings();
}
async function renderPortfolios() {
  try {
    if (!tables.has('portfolio_nav')) {
      $('portfolios-cards').innerHTML =
        '<p class="hint">Aún no hay NAV calculado. Corre <code>nav_engine model &lt;cartera&gt;</code> ' +
        'o <code>nav_engine real &lt;cartera&gt;</code>. Las <b>holdings</b> sí están abajo.</p>';
      $('portfolios-out').innerHTML = ''; return;
    }
    const rows = await q(`SELECT portfolio_id, kind, date, nav, return_pct, benchmark_etf, vs_benchmark_pct
      FROM portfolio_nav QUALIFY row_number() OVER (PARTITION BY portfolio_id ORDER BY date DESC) = 1
      ORDER BY return_pct DESC`);
    $('portfolios-cards').innerHTML = rows.map((r) => `<div class="card">
      <div class="lbl">${r.portfolio_id} <span class="pill">${r.kind}</span></div>
      <div class="big ${r.return_pct >= 0 ? 'pos' : 'neg'}">${r.return_pct >= 0 ? '+' : ''}${Number(r.return_pct).toFixed(2)}%</div>
      <div class="lbl">NAV ${Number(r.nav).toFixed(1)} · vs ${r.benchmark_etf ?? '—'} ${r.vs_benchmark_pct ?? '—'}</div></div>`).join('');
    $('portfolios-out').innerHTML = tableHTML(rows, { signed: ['return_pct', 'vs_benchmark_pct'] });
  } catch (e) { err('portfolios-cards', e); }
}
async function renderHoldings() {
  try {
    if (!tables.has('portfolio_holding') || !$('pf-select').value) { $('holdings-out').innerHTML = ''; return; }
    const pid = $('pf-select').value;
    const rows = await q(`SELECT rank_in_portfolio AS n, sector_id, primary_etf, weight_pct, composite,
      momentum, narrative_maturity FROM portfolio_holding WHERE portfolio_id = ?
      AND run_id = (SELECT max(run_id) FROM portfolio_holding WHERE portfolio_id = ?)
      ORDER BY rank_in_portfolio`, [pid, pid]);
    $('holdings-out').innerHTML = tableHTML(rows);
  } catch (e) { err('holdings-out', e); }
}

// ── Sector history + narrative ──────────────────────────────────────────────────
async function initSector() {
  const rows = await q(`SELECT DISTINCT sector_id FROM sector_snapshot ORDER BY sector_id`);
  $('sector-select').innerHTML = rows.map((r) => `<option>${r.sector_id}</option>`).join('');
  $('sector-select').addEventListener('change', renderSector);
  await renderSector();
}
async function renderSector() {
  try {
    const sid = $('sector-select').value;
    const rows = await q(`SELECT run_id, substr(CAST(snapshot_at AS VARCHAR),1,10) AS date, rank,
      composite, momentum, catalyst_alignment, crowding_risk FROM sector_snapshot
      WHERE sector_id = ? ORDER BY run_id`, [sid]);
    $('sector-out').innerHTML = tableHTML(rows);
    const nar = await q(`SELECT rationale_md FROM sector_snapshot WHERE sector_id = ?
      AND rationale_md IS NOT NULL ORDER BY run_id DESC LIMIT 1`, [sid]);
    $('sector-narrative').innerHTML = (nar.length && nar[0].rationale_md)
      ? md(nar[0].rationale_md) : '<p class="hint">(este sector no tiene narrativa en ningún run)</p>';
  } catch (e) { err('sector-out', e); }
}

// ── Rank moves ──────────────────────────────────────────────────────────────────
async function renderMoves() {
  try {
    if (!tables.has('rank_event')) { $('moves-out').innerHTML = '<p class="hint">(sin rank events todavía)</p>'; return; }
    const rows = await q(`SELECT sector_id, event_type, from_rank, to_rank, delta FROM rank_event
      WHERE run_id = (SELECT max(run_id) FROM rank_event) ORDER BY abs(coalesce(delta,99)) DESC`);
    $('moves-out').innerHTML = tableHTML(rows, { signed: ['delta'] });
  } catch (e) { err('moves-out', e); }
}

// ── Lineage ─────────────────────────────────────────────────────────────────────
async function initLineage() {
  if (!tables.has('portfolio_trade')) {
    $('lineage-out').innerHTML = '<p class="hint">No hay trades registrados (trade_logger).</p>';
    $('trade-select').innerHTML = '<option>(sin trades)</option>'; return;
  }
  const rows = await q(`SELECT trade_id FROM portfolio_trade ORDER BY date DESC`);
  $('trade-select').innerHTML = rows.map((r) => `<option>${r.trade_id}</option>`).join('') || '<option>(sin trades)</option>';
  $('trade-select').addEventListener('change', renderLineage);
  await renderLineage();
}
async function renderLineage() {
  try {
    const tid = $('trade-select').value;
    if (!tables.has('portfolio_trade') || !tid || tid.startsWith('(')) {
      $('lineage-out').innerHTML = '<p class="hint">No hay trades registrados.</p>'; return;
    }
    const trade = (await q(`SELECT * FROM portfolio_trade WHERE trade_id = ?`, [tid]))[0];
    let html = heading('Trade') + tableHTML([trade]);
    if (trade && trade.run_id) {
      if (tables.has('report')) {
        const reps = await q(`SELECT report_type, report_date, path FROM report WHERE run_id = ?`, [trade.run_id]);
        html += heading('Informes del run') + tableHTML(reps);
      }
      if (tables.has('sector_snapshot') && trade.etf) {
        const snap = await q(`SELECT sector_id, rank, composite, momentum, catalyst_alignment
          FROM sector_snapshot WHERE run_id = ? AND primary_etf = ?`, [trade.run_id, trade.etf]);
        html += heading('Scores del sector en ese run') + tableHTML(snap);
      }
    }
    $('lineage-out').innerHTML = html;
  } catch (e) { err('lineage-out', e); }
}

// ── SQL console ──────────────────────────────────────────────────────────────────
async function runSQL() {
  try {
    const rows = await q($('sql-input').value.replace(/;\s*$/, ''));
    $('sql-out').innerHTML = `<p class="hint">${rows.length} filas</p>` + tableHTML(rows);
  } catch (e) { err('sql-out', e); }
}

// ── tabs (lazy render on first open) ──────────────────────────────────────────────
const TAB_INIT = {
  catalysts: initCatalysts, studies: initStudies, reports: initReports,
  portfolios: initPortfolios, sector: initSector, moves: renderMoves, lineage: initLineage,
};
const inited = new Set();
function setupTabs() {
  $('tabs').addEventListener('click', async (ev) => {
    const b = ev.target.closest('button'); if (!b) return;
    const tab = b.dataset.tab;
    document.querySelectorAll('#tabs button').forEach((x) => x.classList.toggle('active', x === b));
    document.querySelectorAll('.panel').forEach((p) => p.classList.toggle('active', p.id === `panel-${tab}`));
    if (TAB_INIT[tab] && !inited.has(tab)) {
      inited.add(tab);
      try { await TAB_INIT[tab](); } catch (e) { console.error(e); }
    }
  });
  $('sql-run').addEventListener('click', runSQL);
}

// ── boot ─────────────────────────────────────────────────────────────────────────
(async () => {
  try {
    setupTabs();
    status('iniciando DuckDB-WASM…');
    const db = await initDuckDB();
    status('registrando lake (lazy)…');
    await loadLake(db);
    status(`${tables.size} tablas (lazy) · docs on-demand`);
    await renderRanking();  // default tab; others (incl. docs.json) load on first open
  } catch (e) {
    status('error');
    err('ranking-out', e);
  }
})();
