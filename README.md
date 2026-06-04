<p align="center">
  <img src="assets/logo.png" alt="Catalyx" width="280"/>
</p>

<p align="center">
  <strong>Macro catalyst intelligence for conviction-based ETF investing</strong>
</p>

<p align="center">
  <code>Catalyst Detection → Thesis Formulation → Execution Tracking → Validation & Feedback</code>
</p>

---

## What it is

CATALYX is a personal analysis platform that translates macro catalysts into structured, falsifiable ETF theses — and then measures whether you were right, and whether you were right *for the right reasons*.

Most investment platforms track what happened. CATALYX tracks whether your reasoning was correct.

## Pipeline

```
MACRO SCAN          Detect and score catalysts before they are priced in
      ↓
SECTOR SCORING      Map catalysts to granular sectors. Score: alignment, momentum, flows, valuation, crowding
      ↓
THESIS FORMULATION  Structured, machine-readable thesis with explicit assumptions and invalidation conditions
      ↓
EXECUTION           Log trades. Track P&L gross and net of Spanish CGT (progressive brackets)
      ↓
ATTRIBUTION         Decompose returns: catalyst alpha / sector beta / market beta / timing luck
      ↓
FEEDBACK LOOP       Closed theses become training data. Update prior hit rates per catalyst-sector pair
```

## Catalyst model

Two types of catalyst, not one:

| Type | Example | Signal | Decays? |
|---|---|---|---|
| **Event** | NATO 3.5% GDP commitment | News, announcement | Yes — exponential decay (~46 day half-life) |
| **Structural** | Central banks systematically buying gold | Persistent indicators (WGC, COT, IMF) | No — intensity score updated quarterly |

Structural catalysts are the floor signal. Event catalysts are the spike. Both feed into sector scoring simultaneously.

Event catalysts interact with structural catalysts via a typed relationship:

| relation_to_structural | Effect |
|---|---|
| `confirms` | Amplifies structural by up to +12%, scaled by event strength |
| `contradicts` | Dampens structural by -18% |
| `independent` | Additive: structural × 0.45 + event × 0.55 |

## Granularity requirement

Sectors are maximally granular. Adjacent sectors are never collapsed:

- **Metals:** gold physical ≠ gold miners ≠ silver ≠ copper ≠ uranium ≠ lithium
- **Defense:** EU prime contractors ≠ US defense ≠ cybersecurity ≠ space ≠ drones
- **Energy:** oil majors ≠ LNG ≠ nuclear operators ≠ uranium miners ≠ grid infrastructure
- **Tech:** semiconductor design ≠ semiconductor equipment ≠ foundries ≠ AI infrastructure

60+ sectors defined in [`catalyx/config/sector_taxonomy.yaml`](catalyx/config/sector_taxonomy.yaml), including futuristic watch-only sectors (quantum computing, nuclear fusion, BCI) that track investability triggers.

## Scoring stability

LLM-assigned scores drift across sessions. CATALYX enforces reproducibility:

| Dimension | Method |
|---|---|
| Structural catalyst intensity | Computed from indicator semaphores via formula — never free-assigned |
| Narrative maturity | 5-level categorical enum (`ignored/emerging/mainstream/crowded/exhausted`) with observable criteria |
| Is-priced-in | 5 stepped levels (0 / 0.25 / 0.50 / 0.75 / 1.0) with threshold criteria |
| Novelty score | 5 binary rubric questions × 20 — never a free float |
| Momentum score | Cross-sectional percentile rank across the scored universe |
| ClosedThesis quality scores | Anchored 0-10 rubrics; `right_reason_score` is computed from a defined formula |

## Current state (Phase 0)

Phase 0 uses Claude Code as the interface with no Python infrastructure. All data is stored as JSON/YAML files.

**Structural catalysts active (5):**
- `struct_cb_gold_accumulation` — CB reserve dedollarization (intensity 90, user_rank 1)
- `struct_nato_rearmament` — NATO multi-year rearmament cycle
- `struct_ai_capex_supercycle` — AI infrastructure capex cycle
- `struct_energy_transition_grid` — Grid as binding constraint of energy transition
- `struct_copper_datacenter_demand` — AI data center copper demand mispricing

**Event catalysts (5):**
- `cat_20260603_nato_hague_5pct_gdp` — Hague Summit: 5% GDP by 2035 (strength 82)
- `cat_20260603_nato_defense_gdp` — NATO 3.5% GDP floor by 2028 (strength 91)
- `cat_20260603_copper_supply_deficit_2026` — Multi-mine disruptions, 600k-tonne deficit (strength 78)
- `cat_20260601_us_ai_chip_export_controls` — BIS closes China subsidiary loophole (strength 62)
- `cat_20260228_hormuz_closure` — Strait of Hormuz closure (strength 94, fully priced)

**Sector studies (4):** copper_miners, grid_infrastructure_utilities, gold_physical, gold_miners

**Theses in draft (2):**
- `thesis_20260603_copper_miners_datacenter_alpha` — Copper miners, AI DC demand mispricing, COPX.L, Tier 2 (6%)
- `thesis_20260603_grid_infrastructure_utilities_bindingconstraint` — Grid infrastructure, order-book play, IQQH.DE, Tier 2 (4%)

**Sector snapshots (2):** copper_miners (composite 70.9), grid_infrastructure_utilities (composite 74.5)

## Project structure

```
catalyx/
├── CLAUDE.md                          ← Project intelligence (read first every session)
├── schemas/                           ← JSON Schemas for all data objects (v1.2)
│   ├── catalyst_event.json            ← v1.2
│   ├── structural_catalyst.json       ← v1.2
│   ├── sector_snapshot.json           ← v1.1
│   ├── sector_study.json              ← v1.2 (narrative_maturity replaces analyst_narrative_score)
│   ├── thesis.json                    ← v1.1 (stop_price_level added to invalidation_conditions)
│   ├── closed_thesis.json             ← v1.1 (rubric descriptions added to quality scores)
│   └── taxonomy_gap_proposal.json     ← v1.0
├── catalyx/config/
│   ├── sector_taxonomy.yaml           ← 60+ sectors (canonical sector_id source)
│   ├── catalyst_taxonomy.yaml         ← Catalyst types, subtypes, decay parameters
│   ├── scoring_weights.yaml           ← v1.3: composite formula, stability rubrics, confirms fix
│   └── structural_catalysts/          ← One file per active structural catalyst (all v1.2)
│       ├── cb_gold_accumulation.yaml
│       ├── ai_capex_supercycle.yaml
│       ├── nato_rearmament.yaml
│       ├── energy_transition_grid.yaml
│       └── copper_datacenter_demand.yaml
└── data/                              ← Runtime data (catalysts, snapshots, theses, studies)
    ├── catalysts/                     ← 5 event catalysts
    ├── sector_studies/                ← 4 sector studies
    ├── snapshots/                     ← 2 sector snapshots
    ├── theses/                        ← 2 draft theses
    └── taxonomy_proposals/            ← Gap proposals from Discovery Pass
```

## Phases

| Phase | Status | Description |
|---|---|---|
| **0 — Skill prototype** | Current | Claude Code as interface. JSON files. Validates workflow. |
| **1 — Python CLI** | Next | `catalyx` CLI with Typer. SQLite. Full pipeline automated. |
| **2 — Automation** | Planned | APScheduler, FastAPI, Postgres. LLM-assisted monitoring. |
| **3 — ML scoring** | Planned | XGBoost on closed thesis data. Bayesian priors. |
| **4 — Backtesting** | Planned | Historical catalyst reconstruction. Walk-forward validation. |

## Model versions (pinned)

| Use case | Model |
|---|---|
| Thesis drafting, deep analysis | `claude-opus-4-8` |
| Sector scoring, monitoring | `claude-sonnet-4-6` |
| Bulk classification | `claude-haiku-4-5-20251001` |
| OpenAI bulk classification | `gpt-4o-mini-2024-07-18` |
| OpenAI analysis | `gpt-4o-2024-08-06` |

## Tax model

Spanish CGT, progressive, all capital gains regardless of holding period:
`≤ €6k → 19%` · `≤ €50k → 21%` · `≤ €200k → 23%` · `> €200k → 27%`

---

<p align="center">
  Built for investors who want to know if their edge is real.
</p>
