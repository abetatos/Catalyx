// CATALYX dashboard — DuckDB-WASM over the parquet lake (Fase F).
// Loads the baked parquet (manifest.json), registers one view per table, and runs the
// same analytical SQL as catalyx/store/lake_query.py — entirely in the browser, no backend.
import * as duckdb from 'https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.0/+esm';

const $ = (id) => document.getElementById(id);
const status = (msg) => { $('status').textContent = msg; };

let conn = null;
let tables = new Set();

async function initDuckDB() {
  const bundles = duckdb.getJsDelivrBundles();
  const bundle = await duckdb.selectBundle(bundles);
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
      const buf = new Uint8Array(await (await fetch(f)).arrayBuffer());
      const name = f.replaceAll('/', '_');
      await db.registerFileBuffer(name, buf);
      names.push(`'${name}'`);
    }
    if (!names.length) continue;
    await conn.query(
      `CREATE OR REPLACE VIEW "${table}" AS SELECT * FROM read_parquet([${names.join(',')}], union_by_name=true)`);
    tables.add(table);
  }
}

function sqlLiteral(v) {
  if (v === null || v === undefined) return 'NULL';
  if (typeof v === 'number') return String(v);
  return "'" + String(v).replace(/'/g, "''") + "'";  // safe: values come from controlled dropdowns
}
async function q(sql, params) {
  // Inline literals instead of prepared statements — DuckDB-WASM's prepare/bind path
  // is brittle (it was silently breaking the parameterised queries: sector, holdings, lineage).
  let finalSQL = sql;
  if (params && params.length) {
    let i = 0;
    finalSQL = sql.replace(/\?/g, () => sqlLiteral(params[i++]));
  }
  const res = await conn.query(finalSQL);
  return res.toArray().map((r) => Object.fromEntries(
    res.schema.fields.map((f) => [f.name, normalize(r[f.name])])));
}
function normalize(v) {
  if (typeof v === 'bigint') return Number(v);
  if (v && typeof v === 'object' && 'toString' in v && !(Array.isArray(v))) {
    // Arrow date/timestamp values stringify cleanly
    const s = v.toString();
    return s;
  }
  return v;
}

// ── rendering ────────────────────────────────────────────────────────────────
function tableHTML(rows, opts = {}) {
  if (!rows || !rows.length) return '<p class="hint">(sin datos)</p>';
  const cols = Object.keys(rows[0]);
  const num = new Set(opts.num || []);
  const signed = new Set(opts.signed || []);
  const head = cols.map((c) => `<th>${c}</th>`).join('');
  const body = rows.map((r) => '<tr>' + cols.map((c) => {
    let v = r[c];
    const isNum = num.has(c) || (typeof v === 'number');
    let cls = isNum ? 'num' : '';
    if (signed.has(c) && typeof v === 'number') cls += v >= 0 ? ' pos' : ' neg';
    if (typeof v === 'number') v = Number.isInteger(v) ? v : Number(v.toFixed(2));
    return `<td class="${cls}">${v ?? ''}</td>`;
  }).join('') + '</tr>').join('');
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}
function err(where, e) {
  console.error(e);
  $(where).innerHTML = `<div class="err">${(e && e.message) || e}</div>`;
}

// ── panels ───────────────────────────────────────────────────────────────────
async function renderRanking() {
  try {
    const rows = await q(`SELECT sector_id, rank, composite, momentum, catalyst_alignment,
      crowding_risk, narrative_maturity, primary_etf FROM sector_snapshot
      WHERE run_id = (SELECT max(run_id) FROM sector_snapshot) ORDER BY rank LIMIT 25`);
    $('ranking-out').innerHTML = tableHTML(rows, { signed: [] });
  } catch (e) { err('ranking-out', e); }
}

async function fillSectorSelect() {
  const rows = await q(`SELECT DISTINCT sector_id FROM sector_snapshot ORDER BY sector_id`);
  $('sector-select').innerHTML = rows.map((r) => `<option>${r.sector_id}</option>`).join('');
}
async function renderSector() {
  try {
    const sid = $('sector-select').value;
    // substr(...::VARCHAR) is tz-safe: the lake mixes tz-aware and tz-naive snapshot_at,
    // so a direct CAST(... AS DATE) fails on TIMESTAMP WITH TIME ZONE in DuckDB-WASM.
    const rows = await q(`SELECT run_id, substr(CAST(snapshot_at AS VARCHAR),1,10) AS date, rank,
      composite, momentum, catalyst_alignment, crowding_risk FROM sector_snapshot
      WHERE sector_id = ? ORDER BY run_id`, [sid]);
    $('sector-out').innerHTML = tableHTML(rows);
  } catch (e) { err('sector-out', e); }
}

async function renderPortfolios() {
  try {
    if (!tables.has('portfolio_nav')) {
      $('portfolios-cards').innerHTML =
        '<p class="hint">Aún no hay NAV calculado. Corre <code>nav_engine model &lt;cartera&gt;</code> ' +
        'o <code>nav_engine real &lt;cartera&gt;</code> para generar las curvas. ' +
        'Las <b>holdings</b> sí están disponibles abajo.</p>';
      $('portfolios-out').innerHTML = '';
      return;
    }
    const rows = await q(`SELECT portfolio_id, kind, date, nav, return_pct, benchmark_etf,
      vs_benchmark_pct FROM portfolio_nav
      QUALIFY row_number() OVER (PARTITION BY portfolio_id ORDER BY date DESC) = 1
      ORDER BY return_pct DESC`);
    $('portfolios-cards').innerHTML = rows.map((r) => `<div class="card">
      <div class="lbl">${r.portfolio_id} <span class="pill">${r.kind}</span></div>
      <div class="big ${r.return_pct >= 0 ? 'pos' : 'neg'}">${r.return_pct >= 0 ? '+' : ''}${Number(r.return_pct).toFixed(2)}%</div>
      <div class="lbl">NAV ${Number(r.nav).toFixed(1)} · vs ${r.benchmark_etf ?? '—'} ${r.vs_benchmark_pct ?? '—'}</div>
    </div>`).join('') || '<p class="hint">(aún no hay NAV calculado — corre nav_engine)</p>';
    $('portfolios-out').innerHTML = tableHTML(rows, { signed: ['return_pct', 'vs_benchmark_pct'] });
  } catch (e) { err('portfolios-cards', e); }
}
async function fillPfSelect() {
  if (!tables.has('portfolio_holding')) { $('pf-select').innerHTML = ''; return; }
  const rows = await q(`SELECT DISTINCT portfolio_id FROM portfolio_holding ORDER BY portfolio_id`);
  $('pf-select').innerHTML = rows.map((r) => `<option>${r.portfolio_id}</option>`).join('');
}
async function renderHoldings() {
  try {
    if (!tables.has('portfolio_holding') || !$('pf-select').value) { $('holdings-out').innerHTML = ''; return; }
    const pid = $('pf-select').value;
    const rows = await q(`SELECT rank_in_portfolio AS n, sector_id, primary_etf, weight_pct,
      composite, momentum, narrative_maturity FROM portfolio_holding
      WHERE portfolio_id = ? AND run_id = (SELECT max(run_id) FROM portfolio_holding WHERE portfolio_id = ?)
      ORDER BY rank_in_portfolio`, [pid, pid]);
    $('holdings-out').innerHTML = tableHTML(rows);
  } catch (e) { err('holdings-out', e); }
}

async function renderMoves() {
  try {
    if (!tables.has('rank_event')) { $('moves-out').innerHTML = '<p class="hint">(sin rank events todavía)</p>'; return; }
    const rows = await q(`SELECT sector_id, event_type, from_rank, to_rank, delta FROM rank_event
      WHERE run_id = (SELECT max(run_id) FROM rank_event) ORDER BY abs(coalesce(delta,99)) DESC`);
    $('moves-out').innerHTML = tableHTML(rows, { signed: ['delta'] });
  } catch (e) { err('moves-out', e); }
}

async function fillTradeSelect() {
  if (!tables.has('portfolio_trade')) { $('trade-select').innerHTML = '<option>(sin trades)</option>'; return; }
  const rows = await q(`SELECT trade_id FROM portfolio_trade ORDER BY date DESC`);
  $('trade-select').innerHTML = rows.map((r) => `<option>${r.trade_id}</option>`).join('')
    || '<option>(sin trades)</option>';
}
async function renderLineage() {
  try {
    if (!tables.has('portfolio_trade') || !$('trade-select').value || $('trade-select').value.startsWith('(')) {
      $('lineage-out').innerHTML = '<p class="hint">No hay trades registrados. Usa trade_logger para añadirlos.</p>'; return;
    }
    const tid = $('trade-select').value;
    const trade = (await q(`SELECT * FROM portfolio_trade WHERE trade_id = ?`, [tid]))[0];
    let html = '<h3 style="font-size:13px;color:var(--muted)">Trade</h3>' + tableHTML([trade]);
    if (trade && trade.run_id) {
      if (tables.has('report')) {
        const reps = await q(`SELECT report_type, report_date, path FROM report WHERE run_id = ?`, [trade.run_id]);
        html += '<h3 style="font-size:13px;color:var(--muted)">Informes del run</h3>' + tableHTML(reps);
      }
      if (tables.has('sector_snapshot') && trade.etf) {
        const snap = await q(`SELECT sector_id, rank, composite, momentum, catalyst_alignment
          FROM sector_snapshot WHERE run_id = ? AND primary_etf = ?`, [trade.run_id, trade.etf]);
        html += '<h3 style="font-size:13px;color:var(--muted)">Scores del sector en ese run</h3>' + tableHTML(snap);
      }
    }
    $('lineage-out').innerHTML = html;
  } catch (e) { err('lineage-out', e); }
}

async function runSQL() {
  try {
    const rows = await q($('sql-input').value.replace(/;\s*$/, ''));
    $('sql-out').innerHTML = `<p class="hint">${rows.length} filas</p>` + tableHTML(rows);
  } catch (e) { err('sql-out', e); }
}

// ── tabs ─────────────────────────────────────────────────────────────────────
function setupTabs() {
  $('tabs').addEventListener('click', (ev) => {
    const b = ev.target.closest('button'); if (!b) return;
    document.querySelectorAll('#tabs button').forEach((x) => x.classList.toggle('active', x === b));
    document.querySelectorAll('.panel').forEach((p) => p.classList.toggle('active', p.id === `panel-${b.dataset.tab}`));
  });
  $('sector-select').addEventListener('change', renderSector);
  $('pf-select').addEventListener('change', renderHoldings);
  $('trade-select').addEventListener('change', renderLineage);
  $('sql-run').addEventListener('click', runSQL);
}

// ── boot ─────────────────────────────────────────────────────────────────────
(async () => {
  try {
    setupTabs();
    status('iniciando DuckDB-WASM…');
    const db = await initDuckDB();
    status('cargando lake…');
    await loadLake(db);
    status(`${tables.size} tablas cargadas`);
    await renderRanking();
    await fillSectorSelect(); await renderSector();
    await renderPortfolios(); await fillPfSelect(); await renderHoldings();
    await renderMoves();
    await fillTradeSelect(); await renderLineage();
  } catch (e) {
    status('error');
    err('ranking-out', e);
  }
})();
