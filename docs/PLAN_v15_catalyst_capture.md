# PLAN v15 — Que el pipeline capte los catalizadores macro sin que se los dicte el usuario

> Escrito 2026-10-17 para que lo ejecute **otra sesión** (Sonnet). Está pensado para seguirse
> en orden y al pie de la letra. Si algo de lo que dice no coincide con lo que encuentras en el
> código, **para y díselo al usuario** — no improvises un arreglo alternativo.

## 0. Por qué existe este plan (hechos verificados, no hipótesis)

La subida de la Fed del **2026-09-16** (y el régimen de tipos largos que la siguió) llegó al
registro de catalizadores el **2026-10-17**, y solo porque el usuario preguntó. Cuatro fallos
encadenados, cada uno comprobado:

| # | Fallo | Evidencia |
|---|---|---|
| F1 | **El heartbeat lleva roto desde ≥2026-09-04 y dice "quiet".** `scripts/pre_run.sh` invoca `uv` sin ruta; el `PATH` del plist de launchd (`/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin`) no contiene `~/.local/bin`, que es donde está `uv`. `pre_run --check` sale con **127**; `heartbeat.sh` solo avisa con rc **10**, así que cualquier otro código se imprime como `· quiet — nothing needs a review`. | Reproducido: `env -i HOME=$HOME PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin bash scripts/pre_run.sh --check` → `RC=127`, `uv: command not found`. 11/11 logs `pre_run_*.log` desatendidos desde 09-04 tienen 3 líneas. Caché de precios parada en 2026-10-09. |
| F2 | **Nadie hace seguimiento de decisiones con fecha conocida.** `cat_20260910_ecb_rate_hike.json` (escrito el 09-14) dice textualmente "Fed (decisión 15-16/09, 85-90% priced), BoJ (17-18/09)". Ningún mecanismo volvió a mirar tras esas fechas. | `user_notes` de ese fichero. No hubo ningún scan entre 09-01 y 10-06. |
| F3 | **El scan encontró la subida y la usó como evidencia de OTRO catalizador.** El refresh de `struct_cb_gold_accumulation` en `scan_deltas_20261006.json` dice "Gold fell ~6%… after the 2026-09-16 Fed hike". Ningún paso obliga a decidir si un hecho de C0 es un evento. Además C3 dice "si se solapa con un catalizador existente, descártalo", lo que confunde *mismo hecho* con *mismo driver*. | `catalyx-scan.md:41-42` (C0 sin salida a eventos) y `:228` (regla de novedad). `review_20261006.md:20` cita la subida. |
| F4 | **Un evento registrado no llega solo al score.** `catalyst_alignment` ve un evento si (a) el estudio del sector lo lista en `active_catalyst_ids`, o (b) el evento nombra en `related_catalyst_ids` un estructural que el estudio lista (`catalyst_scorer.py:484-499`). `affected_sectors` NO se lee. Nadie avisa del hueco. | `catalyst_scorer.py:377`, `:407-440`, `:484-499`. |

Y uno que no es fallo sino límite: el decay se ancla en `event_date` (`catalyst_scorer.py:192`),
así que un `review_log` "strengthening" no resucita un evento — `cat_20260617_fed_hawkish_pivot`
pesaba ~6% con el régimen en su punto más duro.

## 1. Reglas para quien ejecute esto — LEER ANTES DE TOCAR NADA

1. **Rama y git.** La rama actual (`v10-conviction-risk-units`) tiene **decenas de ficheros
   modificados que NO son de este plan** (configs, catalizadores, lake). **Nunca** `git add -A`,
   `git add .` ni `git commit -a`. No hagas commit salvo que el usuario lo pida; si lo pide, un
   commit por fase y `git add <ruta> <ruta>` explícito solo con los ficheros que tocaste.
2. **Línea base de tests (2026-10-17): 958 passed, 3 failed.** Los tres fallos son previos:
   - `test_catalyst_score_realism.py::test_audit_names_every_live_event_as_a_bare_cardinal` —
     deriva de datos vivos causada HOY: el test afirma que ningún evento vivo tiene rúbrica, y el
     scan de hoy rellenó 4. Se arregla en la Fase 0 (paso 0.6).
   - `test_conviction_and_risk_budget.py::test_the_live_profile_gate_does_not_re_cut_todays_book`
   - `test_position_cap_basis.py::test_a_cap_on_the_wrong_base_would_flatten_the_book`
   Los dos últimos **no son de este plan: no los toques**. Al cerrar cada fase el resultado debe
   ser `N passed, 2 failed` (esos dos), con N creciendo. Si aparece un fallo nuevo, es tuyo.
   Comando: `uv run pytest tests/unit -q`.
3. **Los tests no escriben en `data/`.** Usa `tmp_path` y parámetros de directorio. Las variables
   de entorno nuevas que crees para tests deben poder fijarse con `monkeypatch.setenv`; si una
   puede filtrarse desde el entorno del desarrollador, añádela a la lista de `tests/conftest.py`
   (`_isolate_runtime_env`).
4. **Fallar hacia el aviso.** Todo lo que este plan añade es vigilancia. Una comprobación que no
   puede calcularse **avisa** ("X no se pudo comprobar: <exc>"); nunca devuelve `[]`, `0` o
   silencio. Es la doctrina del proyecto ("silence is a RESULT", "None, never a comforting zero")
   y F1 es exactamente su violación. No envuelvas nada nuevo en `try/except: pass`.
5. **Nada de esto toca el score.** Ni pesos, ni umbrales congelados (`rebalance_rules.frozen`), ni
   `catalyst_scorer.py`, ni `sector_scorer.py`. Si crees que hace falta, para y pregunta.
6. **El dashboard es inglés; el chat es español.** Los mensajes de CLI y de alerta que añadas,
   en inglés (como los existentes en `pre_run.sh`).
7. **Fechas de bancos centrales: solo verificadas.** No escribas en el calendario una fecha que no
   hayas leído en la web oficial del banco en esta sesión. Cita la URL en el YAML.
8. **Documentación al cerrar** (Fase 6): `CHANGELOG.md` (entrada v15.0), línea en "Recent
   Changes" de `CLAUDE.md`, módulos nuevos en `docs/MODULES.md`.

---

## Fase 0 — Reparar el heartbeat (F1). Bug; va primero porque todo lo demás depende de él

### 0.1 `uv` con ruta en los tres scripts del pipeline
En `scripts/pre_run.sh`, `scripts/score_run.sh` y `scripts/post_run.sh`, justo después del `cd`
inicial (o al principio si no hay `cd`), añade lo mismo que ya hace `heartbeat.sh:35`:

```bash
UV="$(command -v uv || echo "$HOME/.local/bin/uv")"
if [ ! -x "$UV" ]; then
  echo "⚠ uv not found (PATH=$PATH, fallback $HOME/.local/bin/uv) — cannot run" >&2
  exit 3
fi
```
y sustituye **cada invocación** `uv run` por `"$UV" run`. En `pre_run.sh` son las líneas 37
(`quiet()`), 41 (`show()`) y 85 (`uv run python - "$LOG" <<'PY'`). El texto que solo se
**imprime** (p. ej. `echo "\$ uv run $*"`) puede quedarse como está. Revisa con
`grep -n "uv run" scripts/*.sh` que no quede ninguna invocación desnuda.

Exit **3** a propósito: no es 0 ni 10, así que el heartbeat lo tratará como roto (paso 0.3).

### 0.2 PATH del plist
En `scripts/launchd/com.catalyx.heartbeat.plist`, el `PATH` pasa a
`__HOME__/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin`, y en el bloque `--install`
de `heartbeat.sh` el `sed` sustituye también `__HOME__`:
`sed -e "s|__REPO__|$REPO|g" -e "s|__HOME__|$HOME|g" "$PLIST_SRC" > "$PLIST_DST"`.
Es redundante con 0.1 a propósito: dos defensas independientes.

**No ejecutes `bash scripts/heartbeat.sh --install`.** Recarga un agente de launchd en la máquina
del usuario: díselo al final y que lo lance él (o pídele permiso explícito).

### 0.3 El heartbeat avisa cuando su propia comprobación falla
En `scripts/heartbeat.sh`:

- Haz el comando de verificación y el log sustituibles para poder testearlo:
  ```bash
  LOG="${CATALYX_HEARTBEAT_LOG:-data/reports/heartbeat_$(date +%Y%m%d).log}"
  CHECK_CMD="${CATALYX_CHECK_CMD:-bash scripts/pre_run.sh --check}"
  ```
- Si `CATALYX_HEARTBEAT_SKIP_DATA=1`, sáltate los pasos 1-3 (COT, valuation, trends,
  `indicator_sources --apply`, write-back) y deja `RULE5=0`. Solo para tests.
- Si `CATALYX_NO_NOTIFY=1`, no llames a `osascript`. Solo para tests.
- Paso 4: `CHECK_OUT=$(bash -c "$CHECK_CMD" 2>>"$LOG"); CHECK_RC=$?`
- Sustituye `[ "$CHECK_RC" -eq 10 ] && MSG="$CHECK_OUT"` por:
  ```bash
  case "$CHECK_RC" in
    0)  ;;
    10) MSG="$CHECK_OUT" ;;
    *)  MSG="  • HEARTBEAT BROKEN: pre_run --check exited $CHECK_RC — no verdict was computed. Log: $LOG" ;;
  esac
  ```
  El resto (añadir Rule 5, notificar, `exit 10`) no cambia: un heartbeat roto **notifica y sale 10**.

### 0.4 Las comprobaciones internas de `--check` tampoco pueden callar
Dentro del heredoc Python de `pre_run.sh` (líneas ~85-160) hay tres `except` que tragan errores:
el del rebalance (deja `n_actions = 0` y `summary = "rebalance unavailable"` **sin alerta**), el de
`review_age` (`except: pass`) y el del VIX (`except: pass`). En los tres, añade una alerta:
`alerts.append(f"<check> could not be evaluated: {exc}")`. Si `ser` (VIX) llega vacío, también
es alerta, no silencio.

Añade además una comprobación de **caché de precios viva**: lee `SPY` con
`allow_fetch=False` sobre los últimos 10 días; si la última fecha con dato tiene más de **4 días
naturales** respecto a hoy, alerta `price cache stale: last SPY close <fecha>`. Eso habría
saltado desde el 2026-10-13 (caché parada el 10-09). 4 días cubre fin de semana + festivo.

### 0.5 El log de `--check` no lo pisa una sesión manual
`pre_run.sh` trunca `pre_run_<fecha>.log` al empezar, así que una sesión manual el mismo día
borra la evidencia del fallo de las 08:12 (pasó el 10-06 y el 10-09). En modo `--check`, usa
`LOG="data/reports/pre_run_check_$(date +%Y%m%d).log"`. El modo normal no cambia.

### 0.6 Tests de la Fase 0 — `tests/unit/test_heartbeat_scripts.py` (nuevo)
1. **Ningún `uv` desnudo:** para cada `scripts/*.sh`, quitando comentarios y líneas con `echo`,
   ninguna línea contiene `uv run` sin ir precedido de `"$UV" `. (Regex sobre texto; sin ejecutar.)
2. **Heartbeat con check roto avisa:** ejecuta `bash scripts/heartbeat.sh` con `cwd` = raíz del
   repo y env `CATALYX_HEARTBEAT_SKIP_DATA=1`, `CATALYX_NO_NOTIFY=1`,
   `CATALYX_HEARTBEAT_LOG=<tmp_path>/hb.log`, `CATALYX_CHECK_CMD="exit 127"` → `returncode == 10`
   y `"HEARTBEAT BROKEN"` en stdout.
3. Mismo con `CATALYX_CHECK_CMD="exit 0"` → `returncode == 0` y `"quiet"` en stdout.
4. Mismo con `CATALYX_CHECK_CMD="echo '  • x'; exit 10"` → `returncode == 10` y `"• x"` en stdout.
5. **Plist:** el fichero contiene `__HOME__/.local/bin` en el PATH.

Y arregla la deriva de 0-base: en
`test_catalyst_score_realism.py::test_audit_names_every_live_event_as_a_bare_cardinal`, el
`assert res["n_no_rubric"] == res["n_events"]` afirma un estado del mundo que el propio flujo de
v11 está diseñado para cambiar (la rúbrica se rellena scan a scan). Léelo entero, entiende qué
quería proteger y conviértelo en una aserción sobre un fixture en `tmp_path` (un evento con
rúbrica y uno sin ella → el audit nombra exactamente el segundo). **No** lo borres ni lo
conviertas en `<=` sin más.

### 0.7 Verificación manual de la Fase 0 (obligatoria, pega la salida al usuario)
```bash
env -i HOME=$HOME PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin \
  bash scripts/pre_run.sh --check; echo "RC=$?"
```
Esperado: ya no aparece `uv: command not found`; RC 0 o 10 (10 es lo probable: hay rule actions
y la caché estaba vieja), y `data/reports/pre_run_check_<hoy>.log` tiene más de 3 líneas.
**Esto hace llamadas de red (refresh de precios). Es esperado.**

---

## Fase 1 — Calendario de bancos centrales (F2). Determinista, sin LLM

### 1.1 `catalyx/config/macro_calendar.yaml` (nuevo)
```yaml
version: 1
# Decisions with a KNOWN date. A meeting that has happened and has no triage entry in any
# data/reports/scan_triage_*.json is "due": the heartbeat alerts and the scan must resolve it.
tracking_start: "2026-09-01"     # meetings before this date are never due
sources:
  fed: "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
  ecb: "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html"
  boj: "https://www.boj.or.jp/en/mopo/mpmsche_minu/index.htm"
  boe: "https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates"
meetings:
  - {bank: fed, date: "2026-09-16", kind: rate_decision}
  # … one row per meeting; `date` = the DECISION day (last day of a two-day meeting)
```
- Bancos: `fed`, `ecb`, `boj`, `boe`. Nada más en v15 (CPI, NFP, etc. quedan fuera; ver §7).
- Rango: todas las reuniones desde **2026-06-01** hasta la última que cada banco haya publicado.
- **Verifica cada fecha en la URL oficial** (regla 7). Como contraste, el repo ya contiene estas
  fechas de decisión: Fed 2026-06-17, 2026-09-16, 2026-10-28; BCE 2026-06-11, 2026-09-10,
  2026-10-29. Si la web oficial dice otra cosa, manda la web y avísalo.
- Ordenado por fecha, sin duplicados `(bank, date)`.

### 1.2 `catalyx/store/macro_calendar.py` (nuevo)
Funciones puras + CLI mínimo:
- `load(path=None) -> dict` — valida: `bank` ∈ {fed, ecb, boj, boe}; `date` ISO válida;
  `kind == "rate_decision"`; sin duplicados; `tracking_start` ISO. Error de validación → excepción
  con mensaje claro (no lista vacía).
- `meeting_key(m) -> str` — `"<bank>:<YYYY-MM-DD>"`, p. ej. `"fed:2026-09-16"`. Es la clave que
  usa el triaje (Fase 2).
- `resolved_keys(reports_dir) -> set[str]` — une `items[].key` de todos los
  `scan_triage_*.json` de `reports_dir` (formato en 2.1).
- `due(as_of, cal, resolved) -> list[dict]` — reuniones con `tracking_start <= date <= as_of` y
  clave no resuelta, ordenadas por fecha. Cada fila: `{key, bank, date, days_ago}`.
- `upcoming(as_of, cal, days=14) -> list[dict]` — reuniones en `(as_of, as_of+days]`.
- CLI: `uv run python -m catalyx.store.macro_calendar [--as-of YYYY-MM-DD] [--json]` imprime
  `due` y `upcoming`.

### 1.3 `run_state.build()` publica el calendario
En `catalyx/store/run_state.py`, dentro de `build()`:
- Calcula `calendar_due` y `calendar_upcoming` con el módulo anterior (`reports_dir` =
  `_REPORTS_DIR`). **No uses `_safe(..., [])` aquí**: si falla, deja `calendar_due = None` y
  `calendar_error = str(exc)`. `[]` significaría "nada pendiente", que es mentira.
- Añádelos a `work_list` (`calendar_due`, `calendar_upcoming`) y `calendar_error` al nivel raíz
  del digest.
- En `render()`, tras la cabecera WORK LIST: una línea `CALENDAR DUE  fed 2026-09-16 (31d ago) …`
  si hay pendientes, y `upcoming:` en una línea.
- `build()` acepta `as_of`; el cálculo debe usarlo (test de replay abajo).

### 1.4 El heartbeat avisa
En el heredoc de `pre_run.sh --check`, tras leer `st`:
```python
wl = st.get("work_list", {})
if st.get("calendar_error"):
    alerts.append(f"macro calendar could not be evaluated: {st['calendar_error']}")
due = wl.get("calendar_due") or []
if due:
    alerts.append(f"{len(due)} central-bank decision(s) not triaged by any scan: "
                  + ", ".join(d["key"] for d in due))
```

### 1.5 Tests — `tests/unit/test_macro_calendar.py` (nuevo)
1. **El fichero real es válido:** `load()` no lanza; hay al menos una reunión por banco; está
   ordenado; todas las fechas ≥ 2026-06-01.
2. **Replay del fallo** (con calendario fixture en `tmp_path`, no el real): reuniones
   `ecb:2026-09-10`, `fed:2026-09-16`, `fed:2026-10-28`; `tracking_start 2026-09-01`; sin triajes.
   `due(as_of=2026-09-18)` → exactamente `ecb:2026-09-10` y `fed:2026-09-16`.
   **Este test es la prueba de que el plan habría cazado la subida.**
3. Con un `scan_triage_20260918.json` en `tmp_path` que resuelve `fed:2026-09-16` → solo queda el BCE.
4. `tracking_start` excluye reuniones anteriores.
5. Validación: banco desconocido, fecha inválida o duplicado → excepción.
6. En `test_run_state.py`: un calendario ilegible produce `calendar_due is None` y
   `calendar_error` no vacío (nunca `[]`).

---

## Fase 2 — Triaje de C0 con comprobación automática (F3)

### 2.1 Fichero nuevo `data/reports/scan_triage_<date>.json`
**Va en un fichero aparte, NO dentro de `scan_deltas`.** `scan_deltas` es una lista que consumen
`indicator_update batch`, `catalyst_review batch`, `catalyst_lifecycle --deltas` y `run_digest`;
meterle filas de otro tipo rompe los cuatro.

```json
{
  "scan_date": "2026-10-28",
  "items": [
    {
      "key": "fed:2026-10-28",
      "source": "calendar",
      "fact": "FOMC held at 3.75-4.00%, 10-2 vote",
      "outcome": "registered",
      "catalyst_id": "cat_20261028_fed_hold",
      "reason": "…"
    },
    {
      "key": "c0:oil-brent-above-100",
      "source": "c0",
      "fact": "Brent above $100 on Hormuz traffic <50%",
      "outcome": "refresh_of",
      "catalyst_id": "cat_20260817_hormuz_reescalation",
      "reason": "same fact the Hormuz event already records; refreshed in scan_deltas"
    }
  ]
}
```
- `source`: `calendar` (una fila por cada `calendar_due` del state del día; `key` = la del
  calendario) o `c0` (una fila por cada hecho **decidido u oficial** de los bullets de C0; `key` =
  `c0:<slug-corto>`).
- `outcome` ∈ `registered` · `refresh_of` · `below_threshold` · `not_an_event`.
  - `registered` y `refresh_of` exigen `catalyst_id` existente en `data/catalysts/` o en
    `catalyx/config/structural_catalysts/`.
  - `below_threshold` exige `reason` con el `strength_score` calculado (rúbrica) que no llegó a 55.
  - `not_an_event` exige `reason`. **Usar el hecho como evidencia de otro catalizador NO es
    `refresh_of` de ese otro**: `refresh_of` es solo si ese catalizador ya registra *ese mismo
    hecho*. Es exactamente el error de F3 (la subida de la Fed metida en el refresh del oro).
  - Una decisión de "mantener tipos" es un hecho decidido: se triaja igual. Puede ser
    `below_threshold` si no sorprende; lo que no puede es faltar.

### 2.2 `catalyx/store/scan_triage.py` (nuevo) — el validador
`uv run python -m catalyx.store.scan_triage check --date YYYY-MM-DD [--json]` → exit 0 si todo
está bien, **exit 1** y lista de problemas si:
- no existe `scan_triage_<date>.json`;
- alguna clave de `work_list.calendar_due` del `state_<date>.json` del mismo día no aparece en
  `items` (si no hay `state_<date>.json`, es error: el pipeline exige `pre_run` primero);
- hay cero filas `source: c0` (C0 siempre produce bullets; cero filas = no se triajó);
- un `outcome` no está en el enum, o falta `catalyst_id`/`reason` según 2.1;
- un `catalyst_id` no existe.
Funciones puras `validate(triage: dict, state: dict, catalyst_ids: set[str]) -> list[str]` +
wrapper de disco, para que los tests no toquen `data/`.

### 2.3 El skill del scan — `.claude/commands/catalyx-scan.md`
1. **Nuevo paso "C0b — Triage (MANDATORY)"** justo después de "Output of C0" (línea ~43). Debe
   decir, con estas ideas y en el tono del fichero:
   - Lee `work_list.calendar_due` y `work_list.calendar_upcoming` de `state_<date>.json`. Por cada
     `calendar_due`, busca el resultado de la decisión (una query, en C0 si ya cubre ese banco).
   - Por cada fila de `calendar_due` y por cada bullet de C0 que sea un hecho **decidido u
     oficial** (decisión de banco central, ley aprobada, arancel en vigor, cuota ejecutada,
     movimiento de mercado ya ocurrido), escribe una fila en `scan_triage_<date>.json` con uno de
     los cuatro outcomes. Los candidatos a `registered` pasan por C3/C4 como cualquier evento.
   - Explica F3 en dos frases (la subida del 09-16 usada como evidencia del oro y nunca
     registrada), igual que C2c explica su ratio 31/2. Es lo que hace que la regla se respete.
   - "Usar un hecho como evidencia de otro catalizador no lo triaja".
2. **Sección "Output"/"Machine-readable output"**: añade `scan_triage_<date>.json` con el formato
   de 2.1 y, como último paso del scan,
   `uv run python -m catalyx.store.scan_triage check --date <date>` — si sale 1, corrige y repite;
   el scan no está terminado hasta que salga 0.
3. En el bloque de C0, añade una línea: si `calendar_upcoming` tiene una decisión en los próximos
   días, nómbrala en los bullets (el próximo scan la tendrá como `due`).

### 2.4 El skill del review — `.claude/commands/catalyx-review.md`
- **Step 0/1:** el prompt de la subagente del scan debe incluir literalmente la lista
  `calendar_due` del state y el recordatorio de que el scan termina con
  `scan_triage check` en verde.
- Inmediatamente después del scan y **antes del Step 2**, el orquestador ejecuta
  `uv run python -m catalyx.store.scan_triage check --date $(date +%F)`. Si sale 1, devuelve la
  salida a la subagente (SendMessage) para que complete el triaje. No se aplica ningún delta con
  el triaje en rojo.
- Ajusta el presupuesto de búsquedas (≈15) a "+1 por cada `calendar_due`".

### 2.5 Tests — `tests/unit/test_scan_triage.py` (nuevo)
Sobre `validate()` con dicts en memoria: falta un `calendar_due` → error; cero filas c0 → error;
`refresh_of` sin `catalyst_id` → error; `catalyst_id` inexistente → error; outcome fuera del enum
→ error; `not_an_event` sin `reason` → error; un triaje completo → `[]`.
**Replay de F3:** state con `calendar_due = [fed:2026-09-16]` y un triaje que solo tiene
`c0:gold-down-6pct` → `refresh_of struct_cb_gold_accumulation` → error que nombra `fed:2026-09-16`.

---

## Fase 3 — Regla de novedad: mismo hecho ≠ mismo driver (F3, segunda mitad)

### 3.1 Texto de C3 — `.claude/commands/catalyx-scan.md:228`
Sustituye la viñeta "Novelty check" por (adapta el estilo, conserva el contenido):

> **Novelty check — a duplicate is the SAME FACT, not the same driver.** Skip only if an existing
> event already records this specific dated fact (same decision, same date). A *decided* action
> — a rate decision, an enacted law, an executed quota — of a driver that an earlier event
> recorded as guidance or expectation is a **new event**: its own id and `event_date`, the earlier
> event in `related_catalyst_ids`, its own rubric and anchors. Do not edit the earlier event's
> status or strength: decay and `catalyst_lifecycle` retire it. Why: on 2026-10-06 the 09-16 Fed
> hike "overlapped" the June hawkish-pivot event, which by then weighed ~6% of its strength
> because decay is anchored on `event_date` — a strengthening regime vanished from the score
> while every scan stamped its event "strengthening".

Añade también, en C3/C4, la decisión de **enlace al score** al registrar un evento: además de
`affected_sectors`, decide si el evento debe nombrar en `related_catalyst_ids` algún estructural
(entra por él en todos los sectores que lo listan). **Cuidado con `contradicts`:** dos
`contradicts` activos ponen el estructural en `breaking` y eso puede acabar en una venta. Un
viento en contra macro (tipos más altos) **no** invalida la tesis de demanda de un estructural;
para eso usa `sector_impact: adverse` sobre el sector, no `contradicts` sobre el estructural.

### 3.2 Coherencia con la regla "un catalizador = un driver" de `CLAUDE.md`
Esa regla prohíbe modelar el mismo driver **dos veces a la vez**. Una guía de junio al ~6% y su
realización en septiembre son **secuenciales**, y el precedente ya existe
(`cat_20260910_ecb_rate_hike` tras la subida de junio). Decisión de este plan: se permite, y lo
que vigila el doble conteo es el lint 3.3(b). Añade **una frase** a la regla de `CLAUDE.md`
diciendo esto; no reescribas la regla.

### 3.3 Dos avisos nuevos en `catalyx/scorer/catalyst_audit.py`
Son secciones **informativas** del resultado de `audit()` y de `render()`. **No cambian el
`verdict` de ningún evento ni el exit code de `--strict`** (ya hay ~14 FAIL preexistentes;
cambiar `--strict` rompería quien lo use).
- (a) `reinforced_but_decayed`: eventos `active` con `decayed_strength / strength_score < 0.25`
  cuya entrada **más reciente por fecha** de `review_log` tiene `verdict == "strengthening"`.
  Ojo: `review_log` está ordenado **del más nuevo al más viejo** en los ficheros actuales, pero no
  te fíes del orden — usa `max(fecha)`. Calcula el decay con las mismas funciones que el scorer
  (`_anchor_date`, `_decayed_strength`, `_halflife_for`); no reimplementes la fórmula.
  Mensaje: "candidate for a new dated event (C3 novelty rule), not for a status edit".
- (b) `same_driver_stack`: pares de eventos `active` enlazados entre sí por
  `related_catalyst_ids` (en cualquier dirección) que comparten al menos un sector en
  `affected_sectors` y que **ambos** retienen ≥ 50% de su fuerza decaída. Es el doble conteo
  real; el review decide.
Expón ambas en `--json`.

### 3.4 Tests — en `tests/unit/test_catalyst_score_realism.py` o un fichero nuevo
Fixtures en `tmp_path` (si `audit()` lee de `data/` fijo, añade un parámetro de directorio con el
valor por defecto actual): un evento de 120 días, halflife 30, último log "strengthening" →
aparece en (a); el mismo con "intact" → no; dos eventos enlazados y frescos con sector común →
(b); uno fresco y otro decaído → no. Y uno que compruebe que `--strict` da el mismo exit code con
y sin estas secciones.

---

## Fase 4 — Que un evento registrado llegue al score (F4)

### 4.1 Aviso `event_not_linked` en `catalyst_audit.py`
Para cada evento `active` y cada sector de su `affected_sectors`:
- estudio = `data/sector_studies/study_<sector>.json`;
- **enlazado** si el id del evento está en `active_catalyst_ids`, **o** si algún id de su
  `related_catalyst_ids` que sea un estructural existente está en `active_catalyst_ids` y la
  `relation_to_structural` del evento es `confirms`/`contradicts`/`independent` (es la misma
  condición que `catalyst_scorer.py:484-489`; impórtala o replica exactamente esa lista);
- si no existe estudio → fila con `reason: no_study`;
- si el estudio lo declinó por escrito (4.2) → no se lista.
Sección informativa, mismas reglas que 3.3 (no toca `verdict` ni `--strict`). Marca en cada fila
si el sector es `investable` (de `sector_taxonomy.yaml`).

### 4.2 Poder declinar sin que el aviso grite para siempre — cambio de schema
Doctrina del proyecto (`not_attributed[]`): una pregunta contestada debe constar como tal.
Añade a `schemas/sector_study.json` un campo **opcional**:
```json
"declined_catalyst_ids": [{"catalyst_id": "cat_…", "date": "YYYY-MM-DD", "reason": "…"}]
```
Sigue el **Schema Change Protocol** de `CLAUDE.md` entero: bump de `schema_version`, nota de
migración en `CHANGELOG.md` (campo opcional; los estudios viejos se leen sin él), lector
actualizado si hay modelo Pydantic, y comprobación de que todos los `data/sector_studies/*.json`
siguen validando. Ningún estudio necesita migración.

### 4.3 Skills
- `.claude/commands/catalyx-review.md`, Step 3: correr
  `uv run python -m catalyx.scorer.catalyst_audit --active --json` y, para cada fila
  `event_not_linked` de un sector que se va a estudiar, **o** añadir el id a `active_catalyst_ids`
  **o** escribirlo en `declined_catalyst_ids` con motivo.
- `.claude/commands/catalyx-sector-study.md`: la misma instrucción para su sector.

### 4.4 Tests
Fixture con evento `affected_sectors: [a, b]`, estudio `a` que lo lista, estudio `b` que no →
solo `b`. Estudio `b` con el id en `declined_catalyst_ids` → nada. Evento enlazado vía estructural
listado (`relation: independent`) → no se lista. Sector sin estudio → `no_study`.

**Lo que NO se hace en esta fase:** que el scorer lea `affected_sectors` directamente. Cambiaría
el CA de muchos sectores a la vez y moverlo exige medirlo antes (re-score con y sin, tabla
delante, decisión del usuario).

---

## Fase 5 — Disparadores de mercado en el heartbeat (shocks sin fecha)

La Fase 1 solo caza decisiones con fecha. Un shock sin fecha (Hormuz, un desplome de bonos) solo
lo detecta el mercado.

### 5.1 Series nuevas en la caché de precios
En `catalyx/data/prices.py`: `MACRO_TICKERS = ("^TNX", "BZ=F", "^STOXX50E")`, añadido en
`universe_tickers()` igual que `BENCHMARK_TICKERS`. `universe_tickers()` solo lo usa el `refresh`
(`prices.py:487`), así que no entra en paneles de retornos — **compruébalo con grep antes**, por si
algo nuevo lo usa.
- **`^TNX` es un rendimiento, no un precio.** No pasa nunca por `to_eur`/`read_eur`. Lee con
  `prices.read(..., allow_fetch=False)` (nativo). Antes de fijar umbrales, mira 5 valores en la
  caché y confirma la unidad: un 10 años de EE. UU. al 5,2% debe leerse `5.2`, no `52`.
- `BZ=F` es un futuro continuo: los saltos de roll existen. Usa cambios sobre 20 sesiones, nunca
  diarios.

### 5.2 Umbrales en config, con su tasa de disparo medida
Sección nueva en `catalyx/config/scoring_weights.yaml`:
```yaml
review_triggers:          # pull a review forward; NOT a score input, NOT a rule action
  us10y_change_bp_20d: 25
  brent_change_pct_20d: 15
  equity_drawdown_pct_from_20d_high: 5     # applied to SPY and ^STOXX50E
```
Accesor `weights.review_triggers()` siguiendo el patrón de `_section()` de `weights.py`.
**Antes de dejar los números**, calcula sobre toda la historia en caché cuántos días habría
disparado cada uno (script en el scratchpad, no en el repo). Objetivo orientativo: cada trigger
dispara en pocas semanas al año, no cada semana. Si un valor da >15% de días, súbelo; escribe en
el comentario del YAML y en el CHANGELOG el número medido ("dispara N días de M"). Es la regla de
"decide con la tabla delante", no un número elegido a ojo.

### 5.3 `catalyx/scorer/review_triggers.py` (nuevo)
- `evaluate(frame: pd.DataFrame, cfg: dict) -> list[str]` **pura**: recibe precios nativos ancho
  (fechas × tickers) y devuelve los mensajes de alerta (en inglés). Una serie ausente o con menos
  de 21 datos → mensaje `"<ticker> unavailable for review trigger"` (fallar hacia el aviso).
- CLI `uv run python -m catalyx.scorer.review_triggers [--json]` lee la caché
  (`allow_fetch=False`) e imprime.
- En el heredoc de `pre_run.sh --check`: llama a `evaluate()` y extiende `alerts`.
- Además, `run_state.build()` publica `market_moves` (la lista) para que el scan la vea: añade en
  C0b (2.3) que cada `market_moves` presente se triaja como fila `source: c0` con
  `key: market:<ticker>:<fecha>`.

### 5.4 Tests — `tests/unit/test_review_triggers.py`
DataFrames sintéticos: 10 años de +30pb en 20 sesiones → alerta; +10pb → nada; Brent −20% →
alerta; SPY −6% desde el máximo de 20 sesiones → alerta; ticker ausente → mensaje
"unavailable"; serie corta → "unavailable". Ningún test lee la caché real.

---

## Fase 6 — Cierre

1. `uv run pytest tests/unit -q` → `N passed, 2 failed` (los dos preexistentes de la regla 2).
2. Documentación: entrada **v15.0** en `CHANGELOG.md` con F1-F4 y lo hecho; una línea en "Recent
   Changes" de `CLAUDE.md`; en "Pipeline order" de `CLAUDE.md`, que el scan termina con
   `scan_triage check` en verde; módulos nuevos (`macro_calendar`, `scan_triage`,
   `review_triggers`) en `docs/MODULES.md` con su CLI.
3. Ejecuta `uv run python -m catalyx.store.macro_calendar` y
   `uv run python -m catalyx.scorer.catalyst_audit --active` y pega al usuario:
   - las reuniones `due` (esperado: las de septiembre y octubre que ningún triaje ha resuelto —
     el primer scan tras v15 tiene que triajarlas, y BoJ/BoE de septiembre **nunca se revisaron**);
   - las filas `reinforced_but_decayed`, `same_driver_stack` y `event_not_linked`.
4. Dile al usuario, en este orden: que reinstale el agente (`bash scripts/heartbeat.sh --install`),
   que el próximo heartbeat (mar/vie 08:12) debería **avisar** (calendario pendiente + rule
   actions), y que eso es lo esperado, no un fallo.

## 7. Fuera de alcance (a propósito)

- **Régimen de viento en contra persistente** (un estructural que reste). Es diseño de scorer y
  de schema; decisión del usuario. Los eventos `adverse` ya cubren el caso a corto plazo.
- **Que el scorer lea `affected_sectors`** (ver Fase 4).
- **Calendario de datos macro (CPI, NFP, PMI).** El CPI del 10-14 movió las probabilidades de la
  Fed, pero añadir datos multiplica las filas de triaje. Se valora tras un ciclo con v15.
- **Búsqueda adversarial de libro completo.** Descartada: en el caso real la búsqueda **sí**
  encontró la subida. El fallo fue de canalización y de vigilancia, no de recall.
- **La cadencia del review** (suelo 30d / techo 45d). Es doctrina del usuario; con el heartbeat
  vivo y los triggers nuevos, el techo deja de ser el único disparador real.

## 8. Orden y puntos de parada

Fases en orden 0 → 1 → 2 → 3 → 4 → 5 → 6. **Para y avisa al usuario** al terminar la Fase 0
(con la salida de 0.7) antes de seguir: es el único arreglo urgente y debe quedar confirmado
aislado. Después, las Fases 1-5 pueden ir seguidas, con la suite en verde (salvo los 2
preexistentes) al cerrar cada una.
