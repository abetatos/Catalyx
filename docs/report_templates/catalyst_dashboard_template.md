# CATALYX — Catalyst Dashboard
**Report type:** catalyst_dashboard
**Period:** {{YYYY-MM-DD}}
**Generated:** {{datetime}}
**Event catalysts active:** {{N}} · **Structural catalysts active:** {{N}}

> Ranked by `display_priority = intensity_score × user_rank_multiplier`.
> Indicators: 🟢 strong · 🟡 monitoring · 🔴 alert

---

## Structural Catalysts

### {{RANK}}. {{title}}
`{{id}}` · `{{catalyst_type}} / {{catalyst_subtype}}` · Onset: {{onset_period}} · Geography: {{geography}}

**Intensity:** {{current_score}}/100 · Trend: {{trend_arrow}} {{trend_direction}} · Display priority: {{display_priority}}

```
{{sparkline_history}}
```

**Thesis in one line:** {{one_line_thesis}}

**Indicators**

| # | Indicator | Current | vs Prior | Status | Next check |
|---|---|---|---|---|---|
| {{id}} | {{name}} | {{current_value}} {{unit}} | {{delta_arrow}} {{delta}} | {{status_emoji}} | {{check_frequency}} |

**Sectors directly impacted**

| Sector | Alignment rationale |
|---|---|
| `{{sector_id}}` | {{rationale}} |

**Deactivation risk:** {{deactivation_risk_level}} — {{nearest_deactivation_condition}}

**User notes:** {{user_notes}}

---

## Event Catalysts

### {{RANK}}. {{title}}
`{{id}}` · `{{catalyst_type}} / {{catalyst_subtype}}` · Detected: {{detected_at}}

**Strength:** {{strength_score}}/100 · Days active: {{days_active}} · Remaining relevance: {{remaining_pct}}% (half-life {{decay_halflife_days}}d)

**Description:** {{description}}

**Sectors impacted:** {{sector_list}}

**Priced-in estimate:** {{is_priced_in_estimate_pct}}%

---

## Alerts

### 🔴 Critical
{{alert_list_or_"None"}}

### 🟡 Monitoring
{{monitoring_list_or_"None"}}

---

## Changes vs Prior Report

| Catalyst | Change | Detail |
|---|---|---|
| {{id}} | {{change_type}} | {{detail}} |

---

## Next Review Dates

| Catalyst | Indicator | Due |
|---|---|---|
| {{id}} | {{indicator_name}} | {{due_date}} |

---

*Indicators updated manually after source release. Run `catalyx catalyst update <id> --indicator <ind_id> --value <val>` to refresh.*
