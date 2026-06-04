"""Spanish CGT (Capital Gains Tax) engine — 2026 brackets.

Tax law reference: IRPF — rendimientos del capital mobiliario (Ley 35/2006).
All capital gains are taxed progressively regardless of holding period.
No distinction between short-term and long-term gains (unlike US system).
Tax year is the calendar year. Brackets applied sequentially across all
realised gains YTD.

2026 Brackets (base imponible del ahorro):
  19%  on the first €6,000
  21%  on €6,001 – €50,000
  23%  on €50,001 – €200,000
  27%  on the remainder above €200,000

All inputs and outputs are in EUR. Non-EUR positions must be converted at
execution date before passing to this engine.

Usage (callable from skills):
    uv run python -m catalyx.execution.tax_engine --gain 25000
    uv run python -m catalyx.execution.tax_engine --gain 25000 --ytd-prior 8000
    uv run python -m catalyx.execution.tax_engine --gain 25000 --ytd-prior 8000 --loss 3000

    # Simulate incremental tax on a new gain given prior YTD gains:
    #   ytd_prior = realised gains already recorded in the tax year
    #   loss = capital losses to offset (reduces taxable base)
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass

# 2026 Spanish CGT brackets — list of (upper_bound, rate)
# The last bracket has no upper bound (represented as infinity).
_BRACKETS_2026: list[tuple[float, float]] = [
    (6_000.0,   0.19),
    (50_000.0,  0.21),
    (200_000.0, 0.23),
    (float("inf"), 0.27),
]


@dataclass
class TaxResult:
    gross_gain: float       # gain before tax (EUR)
    taxable_gain: float     # after offsetting losses
    ytd_prior: float        # prior YTD gains going into this calculation
    tax_due: float          # tax owed on this specific gain only (incremental)
    effective_rate: float   # tax_due / gross_gain
    bracket_breakdown: list[dict]  # per-bracket detail
    net_gain: float         # gross_gain - tax_due


def _tax_on_cumulative(cumulative_gain: float) -> float:
    """Total tax on a cumulative gain from zero (pre-offset by prior YTD gains)."""
    total_tax = 0.0
    remaining = cumulative_gain
    lower = 0.0

    for upper, rate in _BRACKETS_2026:
        if remaining <= 0:
            break
        bracket_width = upper - lower
        taxable_in_bracket = min(remaining, bracket_width)
        total_tax += taxable_in_bracket * rate
        remaining -= taxable_in_bracket
        lower = upper

    return round(total_tax, 2)


def compute_tax(
    gross_gain: float,
    ytd_prior: float = 0.0,
    losses: float = 0.0,
) -> TaxResult:
    """Compute incremental CGT on a new gain given prior YTD gains.

    Args:
        gross_gain: Capital gain in EUR from the trade being closed (positive).
        ytd_prior: Realised gains already taxed / accounted for this calendar year (EUR).
        losses: Capital losses to offset against this gain (EUR, positive number).

    Returns:
        TaxResult with incremental tax_due and breakdown.
    """
    if gross_gain < 0:
        raise ValueError(f"gross_gain must be non-negative. Got: {gross_gain}")
    if losses < 0:
        raise ValueError(f"losses must be non-negative. Got: {losses}")

    # Offset losses against this gain (cannot create a net negative taxable)
    taxable_gain = max(0.0, gross_gain - losses)

    # Incremental tax = tax on (ytd_prior + taxable_gain) minus tax already owed on ytd_prior
    tax_before = _tax_on_cumulative(ytd_prior)
    tax_after = _tax_on_cumulative(ytd_prior + taxable_gain)
    tax_due = round(tax_after - tax_before, 2)

    effective_rate = round(tax_due / gross_gain, 6) if gross_gain > 0 else 0.0
    net_gain = round(gross_gain - tax_due, 2)

    # Bracket breakdown: show only brackets that contribute to the incremental tax
    bracket_breakdown = []
    lower = 0.0
    for upper, rate in _BRACKETS_2026:
        bracket_start = max(lower, ytd_prior)
        bracket_end = min(upper, ytd_prior + taxable_gain)
        if bracket_end <= bracket_start:
            lower = upper
            continue
        amount_in_bracket = bracket_end - bracket_start
        tax_in_bracket = round(amount_in_bracket * rate, 2)
        bracket_breakdown.append({
            "bracket_lower": lower,
            "bracket_upper": upper if upper != float("inf") else None,
            "rate_pct": rate * 100,
            "amount_taxed": round(amount_in_bracket, 2),
            "tax": tax_in_bracket,
        })
        lower = upper
        if bracket_end >= ytd_prior + taxable_gain:
            break

    return TaxResult(
        gross_gain=round(gross_gain, 2),
        taxable_gain=round(taxable_gain, 2),
        ytd_prior=round(ytd_prior, 2),
        tax_due=tax_due,
        effective_rate=effective_rate,
        bracket_breakdown=bracket_breakdown,
        net_gain=net_gain,
    )


def compute_ytd_tax(realised_gains: list[float]) -> dict:
    """Compute total tax and per-trade tax for a sequence of gains in the tax year.

    Applies the Spanish CGT brackets cumulatively across all gains in order.
    Losses (negative values) offset subsequent gains.

    Args:
        realised_gains: List of EUR gains/losses in chronological order within the tax year.

    Returns:
        Dict with total_tax, net_gains, effective_rate, and per_trade breakdown.
    """
    ytd_positive = 0.0
    ytd_loss_carry = 0.0  # losses not yet offset (carry into next positive gain)
    total_tax = 0.0
    per_trade: list[dict] = []

    for i, pnl in enumerate(realised_gains):
        if pnl < 0:
            ytd_loss_carry += abs(pnl)
            per_trade.append({
                "index": i,
                "pnl": round(pnl, 2),
                "loss_carried": round(abs(pnl), 2),
                "tax_due": 0.0,
            })
        else:
            result = compute_tax(gross_gain=pnl, ytd_prior=ytd_positive, losses=ytd_loss_carry)
            ytd_positive += result.taxable_gain
            ytd_loss_carry = 0.0
            total_tax += result.tax_due
            per_trade.append({
                "index": i,
                "pnl": round(pnl, 2),
                "taxable": result.taxable_gain,
                "ytd_prior": result.ytd_prior,
                "tax_due": result.tax_due,
                "effective_rate_pct": round(result.effective_rate * 100, 2),
            })

    total_gross = sum(p for p in realised_gains if p > 0)
    return {
        "total_gross_gains": round(total_gross, 2),
        "total_tax": round(total_tax, 2),
        "effective_rate_pct": round(total_tax / total_gross * 100, 2) if total_gross > 0 else 0.0,
        "net_gains": round(total_gross - total_tax, 2),
        "per_trade": per_trade,
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="CATALYX tax engine — Spanish CGT 2026 (no holding-period distinction)"
    )
    parser.add_argument("--gain", type=float, required=True,
                        help="Capital gain in EUR (positive).")
    parser.add_argument("--ytd-prior", type=float, default=0.0,
                        help="Prior YTD gains already realised in this tax year (EUR). Default: 0.")
    parser.add_argument("--loss", type=float, default=0.0,
                        help="Capital losses to offset against this gain (EUR). Default: 0.")
    parser.add_argument("--json", action="store_true", help="Output raw JSON only.")
    args = parser.parse_args()

    result = compute_tax(
        gross_gain=args.gain,
        ytd_prior=args.ytd_prior,
        losses=args.loss,
    )

    if args.json:
        print(json.dumps({
            "gross_gain": result.gross_gain,
            "taxable_gain": result.taxable_gain,
            "ytd_prior": result.ytd_prior,
            "tax_due": result.tax_due,
            "effective_rate_pct": round(result.effective_rate * 100, 2),
            "net_gain": result.net_gain,
            "bracket_breakdown": result.bracket_breakdown,
        }, indent=2, ensure_ascii=False))
        return

    print("CATALYX — Spanish CGT Engine (2026 brackets)\n")
    print(f"  Gross gain        : €{result.gross_gain:>12,.2f}")
    if args.loss > 0:
        print(f"  Loss offset       : €{args.loss:>12,.2f}")
    print(f"  Taxable gain      : €{result.taxable_gain:>12,.2f}")
    if args.ytd_prior > 0:
        print(f"  Prior YTD gains   : €{result.ytd_prior:>12,.2f}")
    print()
    print(f"  {'Bracket':<25} {'Rate':>6}  {'Taxed amount':>14}  {'Tax':>10}")
    print(f"  {'-'*25} {'-'*6}  {'-'*14}  {'-'*10}")
    for b in result.bracket_breakdown:
        upper = f"€{b['bracket_upper']:,.0f}" if b["bracket_upper"] is not None else "∞"
        bracket_label = f"€{b['bracket_lower']:,.0f} – {upper}"
        print(f"  {bracket_label:<25} {b['rate_pct']:>5.0f}%  €{b['amount_taxed']:>13,.2f}  €{b['tax']:>9,.2f}")
    print(f"  {'':<25} {'':>6}  {'':>14}  {'-'*10}")
    print(f"  {'Tax due':<25} {'':>6}  {'':>14}  €{result.tax_due:>9,.2f}")
    print()
    print(f"  Effective rate    : {result.effective_rate * 100:.2f}%")
    print(f"  Net gain (after tax): €{result.net_gain:>12,.2f}")


if __name__ == "__main__":
    main()
