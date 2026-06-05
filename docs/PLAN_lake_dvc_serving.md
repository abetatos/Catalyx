# PLAN — Reorganización a lake parquet (en git) + serving en GitHub Pages

> Plan de arquitectura de datos para CATALYX. Cierra cómo se almacenan, versionan,
> consultan y publican los datos ahora que el proyecto incorpora parquet + versionado.
> Decisiones tomadas con el usuario (2026-06-05). Estado: **diseño aprobado, sin implementar.**

---

## 0. Objetivo

Tres metas de producto motivan esta reorganización:

1. **Medir rendimientos** a lo largo del tiempo (performance + atribución).
2. **Registrar la evolución de varias configuraciones de cartera** por perfil de riesgo —
   modelo (qué decía el sistema) **y** dinero real (qué se hizo), comparadas.
3. **Auditar el flujo:** desde una decisión poder consultar el informe y los scores que la provocaron.

Todo se monta sobre un único sustrato: un **data lake parquet append-only, versionado con DVC.**

---

## 1. Principio rector — tres tiers de datos

| Tier | Qué contiene | Formato | Versionado | Mutabilidad |
|---|---|---|---|---|
| **1 — Fuente** | config YAML, JSON schemas, documentos de inteligencia (theses, sector_studies, catalysts, structural_catalysts, taxonomy_proposals), **configs de cartera**, reports `.md` | YAML / JSON / MD | **git** | Editado a mano |
| **2 — Data lake** | series temporales y numéricos computados: momentum/flow snapshots, score_run / sector_snapshot / rank_event, indicator value_history, **portfolio nav/holding/trade**, forward-returns | **parquet append-only, particionado** | **git directo** (parquet commiteado al repo) | Inmutable (append, nunca overwrite) |

> **Actualización (2026-06-05): solo DOS tiers.** El Tier 3 era una caché SQLite (`catalyx.db`)
> reconstruible desde el lake. Se **eliminó del todo** — nunca fue fuente de verdad, y su única
> tabla propia (`llm_log`) quedó obsoleta al confirmar que CATALYX no aloja un LLM propio (es una
> skill sobre la sesión de Claude Code). Hoy: Tier 1 (ficheros) + Tier 2 (lake parquet). Sin DB.

**Cambio clave — parquet-first:** el lake parquet es la **única** verdad durable y versionable.
Los `*_repo.py` leen los documentos Tier 1 directamente; los computados se leen/escriben vía
`catalyx.store.lake`. Reproducir un análisis pasado = `git checkout <commit>` (código **y** datos
viajan juntos en el mismo commit), sin depender de ningún `.db` efímero de una sola máquina.

**Por qué parquet directo en git y no DVC:** los datos son KB–MB y cadencia mensual, lejísimos de
cualquier límite de GitHub. El miedo al *bloat* de binarios en git lo neutraliza el diseño
**append-only particionado**: cada run añade archivos nuevos pequeños y nunca reescribe los viejos →
git guarda cada partición **una sola vez** (archivos inmutables, no se duplican por commit). A cambio,
desaparece toda la fricción de DVC: sin remote, sin `dvc pull/checkout`, y el deploy a Pages se vuelve
trivial (GitHub Actions lee los parquet del propio repo). Si algún día el lake crece a cientos de MB /
GB (p.ej. histórico tick de muchos tickers), migrar con `dvc add data/lake` es barato.

**Acumulación, no sustitución:** el lake es append-only particionado. Cada mes *añade* una
partición y nunca toca las viejas. En HEAD tienes **todos los meses a la vez** y los consultas
juntos con una query. "Viajar entre commits" queda reservado para el caso raro de re-ejecutar
con el código/fórmula antiguos — no para acceder a los datos del día a día.

---

## 2. Layout de directorios objetivo

Solo se reorganiza el Tier 2. **Los documentos (Tier 1) no se mueven** — romper paths en código y
skills para mover 53 sector_studies + theses no compensa. Regla: *no muevas lo que funciona.*

```
data/
├── lake/                                  # ← NUEVO. Commiteado a git. Verdad durable, append-only.
│   ├── market/
│   │   ├── momentum/date=YYYY-MM-DD/part.parquet
│   │   └── flow/date=YYYY-MM-DD/part.parquet
│   ├── scores/
│   │   ├── score_run/run_id=.../part.parquet
│   │   ├── sector_snapshot/run_id=.../part.parquet
│   │   └── rank_event/run_id=.../part.parquet
│   ├── indicators/
│   │   └── indicator_history.parquet      # value_history externalizado de los YAML
│   ├── portfolio/
│   │   ├── nav/portfolio_id=.../date=.../part.parquet      # NAV, return_pct, cum_return, vs bench
│   │   ├── holding/portfolio_id=.../run_id=.../part.parquet # weights/shares/value_eur por sector
│   │   └── trade/portfolio_id=.../part.parquet             # fills reales: side, qty, price, fees, CGT
│   └── validation/
│       └── forward_returns.parquet
│
├── catalysts/  sector_studies/  theses/  taxonomy_proposals/   # ← SIN CAMBIOS (git, Tier 1)
└── reports/                                                     # ← SIN CAMBIOS (git, Tier 1)
#   (catalyx.db / SQLite: ELIMINADO 2026-06-05 — ya no existe Tier 3)

catalyx/config/
├── portfolios/                            # ← NUEVO (Tier 1, git, versionado por config_version)
│   ├── conservative.yaml
│   ├── balanced.yaml
│   └── aggressive.yaml
└── ... (resto sin cambios)
```

**Particionado por `date=` / `run_id=`:** cada run solo añade una partición → inmutabilidad
natural, git guarda cada archivo una sola vez (sin bloat), predicate-pushdown gratis, y se evita que
parquet (no append-in-place) tenga que reescribir el archivo entero.

---

## 3. Modelo de versionado

Cada fila computada del lake carga un **triplete de procedencia**:

1. **`git_commit`** — versión del código  *(✅ ya en snapshot_repo)*
2. **`scoring_version`** — `md5(scoring_weights.yaml)[:12]`  *(✅ ya en snapshot_repo)*
3. **`input_snapshot`** — qué snapshot de mercado alimentó el score  *(la columna `momentum_snapshot` ya existe; se formaliza como id/hash de partición)*

Las carteras añaden un cuarto eje propio: **`config_version`** = `md5(portfolios/<perfil>.yaml)`.
Una cartera es una función determinista de `(score_run × risk_config)` → el mismo `run_id` mensual
alimenta N carteras, y queda registrado qué versión de la config produjo qué evolución.

Los parquet **se commitean al repo** junto al código y los documentos → un solo commit pinea
simultáneamente código + fórmula + dataset.

**Workflow mensual:**
```
# tras /catalyx-monthly-review (el pipeline añade particiones al lake)
git add data/lake data/catalysts data/theses data/reports catalyx/config/portfolios
git commit -m "Monthly review 2026-07"
```
**Reproducir:** `git checkout <commit>` (código y datos juntos, sin pasos extra).

---

## 4. Lineage — auditar decisión → informe

`snapshot_repo` **ya guarda el markdown completo del informe** (`ReportRecord.content_md`) ligado a
su `run_id`. Solo falta que las decisiones carguen el puntero de origen:

- `thesis` → `origin_run_id` + `origin_report`
- `trade` → `thesis_id` + `run_id`

Camino de auditoría completo = **un join**:

```
trade ──> thesis ──> run_id ──┬──> report(content_md)       ← el informe exacto que lo justificó
                              └──> sector_snapshot(run_id)  ← los scores de ese momento
```

"Validar todo el flujo" = clicar una decisión y ver el informe + los scores que la causaron, en el
estado en que estaban. Los datos están en HEAD; no hace falta viajar entre commits.

---

## 5. Performance & carteras (modelo + real)

- **Modelo/paper:** cartera derivada determinísticamente de `(run × risk_config)` → cómo HABRÍA
  evolucionado. Se computa, no se introduce a mano.
- **Real:** trades ejecutados reales (fechas, precios, fees, CGT española vía `tax_engine` ✅) → P&L real.
- **Comparadas:** superponer NAV modelo vs real = **alpha de ejecución** (qué decía el sistema vs qué
  se hizo). Atribución (catalizador vs beta del sector) vía el `return_decomposer` planificado,
  escribiendo a `validation/`.

Conecta con módulos ya planificados: `trade_logger`, `pnl_engine`, `return_decomposer`, `prior_repo`.

---

## 6. Serving — GitHub Pages + DuckDB-WASM (estático, sin backend)

La página es **read-only sobre parquet** → no necesita servidor. **DuckDB-WASM** corre en el navegador
y lee los `.parquet` por HTTP range requests (solo descarga los bytes que necesita).

- **Frontend estático** (JS) en GitHub Pages, público (el usuario acepta exponer la cartera real).
- **Vistas:** comparador de perfiles de riesgo (curvas NAV modelo vs real), drill-down sector →
  informe que lo justificó (lineage), tabla de acierto del scoring (rank IC — `validate_run` ✅ ya existe).
- **Nunca muta la fuente de verdad**, solo lee.

**Deploy (trivial, porque los parquet están en el repo):** GitHub Actions construye el frontend y
copia `data/lake/**/*.parquet` al directorio publicado; Pages lo sirve. DuckDB-WASM los lee de la propia
URL del sitio por HTTP range requests. Flujo completo:

```
git push origin main  →  Actions: build frontend + copia parquet  →  deploy a Pages
```
Sin DVC remote, sin `dvc pull`, sin rama `gh-pages` con datos horneados. `git push` y listo.

---

## 7. Migración por fases (sin romper nada en cada paso)

**Fase A — Setup, cero cambio de comportamiento**
- `.gitignore`: **trackear** `data/lake/` (parquet commiteados); sacar `data/history/` del limbo
  untracked (se reubica/renombra como semilla del lake); `catalyx.db` sigue ignorado (caché)
- Deps: añadir `pandas` (se usa pero no está pineado) y `duckdb`; `pyarrow` ✅ ya está
- Limpieza: `after_scores.json` (raíz, temporal) → reubicar a `data/lake/scores/` o borrar

**Fase B — Invertir la fuente de verdad**
- Nuevo `catalyx/store/lake.py`: `append_partition(table, df, keys)` + `read_table(table)` sobre parquet
- `snapshot_repo.record_run` escribe al lake (no solo SQLite); `lake rebuild` reconstruye SQLite
- `market_data.py` / `flow_data.py`: escriben particiones `date=`; el JSON por-fecha se mantiene **una
  versión** como read-path de compat (Schema Change Protocol), luego se deprecia

**Fase C — Externalizar `indicator value_history`** *(mayor ganancia de limpieza)*
- Mover `value_history[]` de los YAML a `lake/indicators/indicator_history.parquet`
  clave `(catalyst_id, indicator_id, date, value, source)`. El YAML conserva solo el último valor
- Aplicar Schema Change Protocol: bump `schema_version` de `structural_catalyst.json`, campo `deprecated`
- `backfill_history.py` escribe al lake

**Fase D — Carteras + performance**
- `config/portfolios/*.yaml` (perfiles de riesgo + constraints)
- Tablas `portfolio/{nav,holding,trade}`; `trade_logger` + `pnl_engine` + `tax_engine` ✅ alimentan trade/nav
- Lineage: añadir `origin_run_id`/`origin_report` a thesis y `thesis_id`/`run_id` a trade

**Fase E — Unificar read-path**
- DuckDB sobre el lake; queries de `snapshot_repo` (`history`/`runs`/`events`/`validate`) leen del lake
- `return_decomposer` → `validation/`

**Fase F — Serving** ✅ HECHA — **live: https://abetatos.github.io/Catalyx/**
- Frontend estático + DuckDB-WASM en `site/` (lee el parquet horneado en el navegador)
- `scripts/build_site.py` hornea parquet + `manifest.json` en `dist/`; `.github/workflows/pages.yml`
  (build + `actions/deploy-pages`) publica en cada push a `main`. Sin DVC: el lake va en git.
- Sustituye al dashboard Evidence.dev previo (`dashboard/`); su workflow `deploy-dashboard.yml` se eliminó
  para que un solo dashboard sea dueño de la URL de Pages.

**Orden:** A → B → C → D → E → F — **TODAS COMPLETADAS.** A/B desbloquean parquet-first; C es la limpieza
de mayor impacto; D/E habilitan performance+lineage; F publica.

---

## 8. Qué NO se toca (deliberado)
- Config YAML, schemas, documentos JSON (theses/studies/catalysts), reports `.md` → git, paths intactos
- `structural_catalysts/` se queda en `config/`
- El contrato de las skills `uv run python -m catalyx.*` no cambia — solo cambia dónde persisten los módulos
- ~~Postgres futuro (Phase 2)~~ **descartado (2026-06-05):** existía para escalar la capa relacional
  SQLite; al eliminar SQLite del todo, no hay nada que migrar. CATALYX es skill-permanente sobre Claude Code.

## 9. Riesgos / gotchas
- **Bloat de git:** neutralizado por el diseño append-only particionado (archivos inmutables → git los
  guarda una vez). Vigilar solo si el lake crece a cientos de MB; entonces migrar a DVC (`dvc add data/lake`)
- **Límites de GitHub:** 100MB/archivo, ~1GB recomendado de repo. Con KB–MB mensuales, sin riesgo
- **Privacidad:** Pages es público; el usuario acepta exponer cartera real (decisión registrada)
- **Schema Change Protocol** aplica en Fase C (externalizar value_history)
- **`data/history/*.parquet` actuales** (untracked) → semilla del lake en Fase A, o se regeneran con `snapshot_repo export`

---

## Decisiones tomadas (2026-06-05)
- Versionado: **parquet directo en git** (commiteado al repo; append-only particionado evita bloat).
  DVC descartado por innecesario al tamaño actual y porque complicaba el deploy a Pages
- Fuente de verdad: **parquet-first** (SQLite eliminado del todo el 2026-06-05; dos tiers, sin DB)
- Carteras: **modelo + real, comparadas**
- Página: **GitHub Pages + DuckDB-WASM estático** (público, sin backend, deploy vía Actions)
- Alcance de esta sesión: **solo el plan** (sin implementar)
