"""Best moves — every candidate trade priced in ONE currency (v15.3). A COLUMN: it moves nothing.

WHY. Every rule in the rebalance table is a cliff on a noisy variable — the 6/7 cut, the 2pp
deadband, the 4pp trim, the 2-cycle rank streak — and none of them can see the others: the rank
rule cannot see risk, the risk budget cannot see cost, the cost columns cannot see expected
return. Each patch therefore opens a new hole (v15.2's hold zone froze a book carrying 73.6% of
its variance on one driver). This module asks the single question all of them approximate:

    net_eur(move) = Δ E[r]·€  −  δ/2 · Δ Var·€  −  cost_eur          over the calibration horizon

and ranks every move by it. There is no threshold to choose. Every input is measured or derived:

  E[r] = β·ERP + IC·z·σ_cs
         β·ERP is the EQUILIBRIUM premium (Black-Litterman): a high-beta sector is PAID for its
         market risk, so cutting beta is not diversification, it is less exposure. Without this
         term the utility rewards every move toward low beta — the first draft of this did, and
         ranked semis→pharma +€10 when, priced honestly, it is −€11.
         IC·z·σ_cs is the rank alpha, the v13.3 bridge, with IC = λ·tilt_ic_target — the skill
         the book is already SIZED on. It grows by itself as the measured IC earns λ.
  δ    = ERP / σ²_market — the market's own implied risk aversion, not a preference. The
         beta-vs-variance trade-off is therefore INDEPENDENT of the ERP chosen (both scale with
         it); the ERP only sets how large that trade-off is against alpha and cost.
  Σ    = the weekly EUR Ledoit-Wolf covariance `covariance.budget_sigma` already builds.
  cost = Spanish CGT on the realized gain (tax_engine, YTD-sequential) + spread on each leg.

THE MOVES. A swap (sell a held line, buy a candidate with the same euros) and, while the book has
a free line under `n_target`, a buy from cash at the neutral weight. Moves are chosen GREEDILY:
take the best positive one, apply it, re-price everything against the new book, repeat — so two
moves that fix the same problem are never both counted. A swap spends two of the review's trade
slots and a buy one; the plan stops when no move is positive or the slots run out, and the first
move left outside is reported as the CLEARING PRICE of a slot. Exits to cash are not offered: how
much is at work is the deploy rule's question, and the fundamental exits stay in the rule table.

Candidates are the names the model could buy today: investable, buyable vehicle, through the CA
entry gate (an imputed CA is not judged, as everywhere else), regime and maturity the BUY rule
admits. The gate is the mandate — catalysts first — and this column only chooses inside it.
"""
from __future__ import annotations

import math

ANNUALIZE = 52.0


def implied_delta(var_mkt_annual: float, erp: float) -> float | None:
    """Black-Litterman implied risk aversion: the δ at which the market portfolio is optimal."""
    if not var_mkt_annual or var_mkt_annual <= 0:
        return None
    return erp / var_mkt_annual


def expected_returns(betas: dict, z: dict, erp: float, ic: float, sigma_cs: float,
                     horizon_years: float) -> dict:
    """Per sector E[r] over the horizon, split so the column can show WHY a move pays."""
    out = {}
    for s, b in betas.items():
        prem = float(b) * erp * horizon_years
        alpha = ic * float(z.get(s) or 0.0) * sigma_cs
        out[s] = {"premium": prem, "alpha": alpha, "total": prem + alpha}
    return out


def utility(w: dict, mu: dict, sigma: dict, delta: float, horizon_years: float,
            capital: float) -> dict:
    """€ utility of a book `w` ({sector: fraction of total capital}) over the horizon."""
    names = [s for s, x in w.items() if x]
    prem = sum(w[s] * mu[s]["premium"] for s in names)
    alpha = sum(w[s] * mu[s]["alpha"] for s in names)
    var = sum(w[a] * w[b] * sigma[a][b] for a in names for b in names)
    risk = delta / 2.0 * var * horizon_years
    return {"premium": prem * capital, "alpha": alpha * capital, "risk": risk * capital,
            "total": (prem + alpha - risk) * capital, "vol_pct": math.sqrt(max(var, 0.0)) * 100}


def plan(held: dict, candidates: dict, mu: dict, sigma: dict, delta: float,
         horizon_years: float, capital: float, cost_fn, slots: int, n_target: int,
         neutral_eur: float, cash_eur: float, max_steps: int = 6, se_fn=None,
         over_slot_fee_eur: float | None = None) -> dict:
    """Greedy best-move plan. `held` {sector: €}, `candidates` {sector: True}.

    `cost_fn(kind, sell_sector, buy_sector, eur) -> €` prices one move. `se_fn`, if given, returns
    the standard error of its net in € — printed beside it as `t`, never used to choose: the plan
    maximizes expected utility, the SE says how far to trust the estimate.

    `over_slot_fee_eur=None` keeps the slots a WALL (the review's free-trade budget). A number makes
    them a PRICE: every leg beyond the free ones costs that many euros and the plan may go on —
    the user's own call (2026-10-19): the 11th trade is cheap, a worse book is not. Returns the ranked first
    round (what every move is worth against TODAY's book), the greedy sequence, and the clearing
    price — the best move left out once the slots or the positive moves run out.
    """
    book = dict(held)
    cash = float(cash_eur)
    steps, first_round, left_out = [], None, None
    slots_left = int(slots)
    soft = over_slot_fee_eur is not None

    def _moves(book, cash):
        w0 = {s: e / capital for s, e in book.items()}
        u0 = utility(w0, mu, sigma, delta, horizon_years, capital)
        out = []
        for s_out, eur in book.items():
            if eur <= 0 or s_out not in mu:
                continue
            for s_in in candidates:
                if s_in in book or s_in not in mu:
                    continue
                w = dict(w0)
                w.pop(s_out)
                w[s_in] = eur / capital
                out.append(_priced("swap", s_out, s_in, eur, w, u0))
        if len(book) < n_target and cash > 0:
            eur = min(neutral_eur, cash)
            for s_in in candidates:
                if s_in in book or s_in not in mu:
                    continue
                w = dict(w0)
                w[s_in] = eur / capital
                out.append(_priced("buy", None, s_in, eur, w, u0))
        return sorted(out, key=lambda m: -m["net_eur"])

    def _priced(kind, s_out, s_in, eur, w, u0):
        u1 = utility(w, mu, sigma, delta, horizon_years, capital)
        cost = float(cost_fn(kind, s_out, s_in, eur))
        legs = 2 if kind == "swap" else 1
        if soft:
            cost += float(over_slot_fee_eur) * max(0, legs - max(slots_left, 0))
        se = float(se_fn(kind, s_out, s_in, eur)) if se_fn else None
        d = {k: round(u1[k] - u0[k], 2) for k in ("premium", "alpha", "risk", "total")}
        return {"kind": kind, "sell": s_out, "buy": s_in, "eur": round(eur, 2),
                "d_premium_eur": d["premium"], "d_alpha_eur": d["alpha"],
                "d_risk_eur": round(-d["risk"], 2),          # positive = risk REMOVED, in €
                "gross_eur": d["total"], "cost_eur": round(cost, 2),
                "net_eur": round(d["total"] - cost, 2),
                "net_se_eur": round(se, 2) if se is not None else None,
                "t": (round((d["total"] - cost) / se, 2) if se else None),
                "vol_before_pct": round(u0["vol_pct"], 2), "vol_after_pct": round(u1["vol_pct"], 2),
                "slots": 2 if kind == "swap" else 1}

    for _ in range(max_steps):
        moves = _moves(book, cash)
        if first_round is None:
            first_round = moves
        best = (moves[0] if moves else None) if soft else \
            next((m for m in moves if m["slots"] <= slots_left), None)
        if best is None or best["net_eur"] <= 0:
            left_out = best or (moves[0] if moves else None)
            break
        steps.append(best)
        slots_left -= best["slots"]
        if best["kind"] == "swap":
            book[best["buy"]] = book.pop(best["sell"])
        else:
            book[best["buy"]] = best["eur"]
            cash -= best["eur"]
    else:
        left_out = None
    return {"first_round": first_round or [], "steps": steps, "clearing": left_out, "book": book,
            "slots": int(slots), "slots_used": int(slots) - slots_left,
            "net_eur": round(sum(s["net_eur"] for s in steps), 2)}


def cluster_shares(book: dict, sigma: dict, capital: float, drivers: dict) -> dict:
    """Share of book variance carried by each driver cluster — the Euler contribution the risk
    budget already reads (`covariance.cluster_risk_for`), recomputed for a hypothetical book so the
    plan can be judged against the budget it is meant to meet. `drivers` {sector: {cluster, …}}."""
    names = [s for s, e in book.items() if e > 0]
    w = {s: book[s] / capital for s in names}
    var = sum(w[a] * w[b] * sigma[a][b] for a in names for b in names)
    if var <= 0:
        return {}
    mc = {a: sum(w[b] * sigma[a][b] for b in names) for a in names}
    out: dict = {}
    for a in names:
        for c in drivers.get(a) or ():
            out[c] = out.get(c, 0.0) + w[a] * mc[a] / var * 100.0
    return {c: round(v, 1) for c, v in out.items()}


def risk_price(held, candidates, mu, sigma, delta, horizon_years, capital, cost_fn, slots,
               n_target, neutral_eur, cash_eur, drivers, budget_pct, k_grid, over_slot_fee_eur,
               ) -> dict:
    """The declared cluster-variance budget, turned from a limit into a PRICE.

    The market's own risk aversion δ prices a unit of variance at what the market pays for it —
    and at that price almost nothing is worth trading, which is why a book carrying 73% of its
    variance on one driver reads as "fine". The operator's declared budget (35% per driver) is a
    statement that his price is higher. Rather than invent a second number, solve for it: run the
    SAME utility at δ·k for a grid of k and report, per k, the best plan, what it costs in expected
    return and friction, and what it leaves the worst cluster at. `k_star` is the first k whose
    plan puts every cluster inside the budget — the shadow price of the constraint, in euros of
    return given up. Nothing blocks and nothing is imposed: it is the price list, and the choice
    of point on it stays with whoever pays.
    """
    base_w = {s: e / capital for s, e in held.items()}
    u0 = utility(base_w, mu, sigma, delta, horizon_years, capital)
    c0 = cluster_shares(held, sigma, capital, drivers)
    rows, k_star = [], None
    for k in k_grid:
        p = plan(held, candidates, mu, sigma, delta * k, horizon_years, capital, cost_fn, slots,
                 n_target, neutral_eur, cash_eur, over_slot_fee_eur=over_slot_fee_eur)
        b = p["book"]
        u1 = utility({s: e / capital for s, e in b.items()}, mu, sigma, delta * k,
                     horizon_years, capital)
        cl = cluster_shares(b, sigma, capital, drivers)
        worst = max(cl.items(), key=lambda kv: kv[1]) if cl else (None, None)
        ret = (u1["premium"] + u1["alpha"]) - (u0["premium"] + u0["alpha"])
        legs = sum(2 if s["kind"] == "swap" else 1 for s in p["steps"])
        row = {"k": k, "moves": [{"sell": s["sell"], "buy": s["buy"], "kind": s["kind"],
                                   "eur": s["eur"]} for s in p["steps"]],
               "legs": legs, "d_return_eur": round(ret, 1),
               "cost_eur": round(sum(s["cost_eur"] for s in p["steps"]), 1),
               "vol_after_pct": round(u1["vol_pct"], 2), "worst_cluster": worst[0],
               "worst_share_pct": worst[1],
               "within_budget": (worst[1] is not None and budget_pct is not None
                                 and worst[1] <= budget_pct)}
        rows.append(row)
        if k_star is None and row["within_budget"]:
            k_star = k
    # When no plan meets the budget, the useful fact is how near the frontier gets. A cluster's
    # SHARE of variance can rise while its absolute variance falls (a lower-vol line inside the same
    # driver shrinks the denominator too), so the share is not monotone in k — the closest point is
    # reported, not assumed to be the last one.
    scored = [r for r in rows if r["worst_share_pct"] is not None]
    closest = min(scored, key=lambda r: (r["worst_share_pct"], r["k"]))["k"] if scored else None
    return {"budget_pct": budget_pct, "vol_before_pct": round(u0["vol_pct"], 2),
            "clusters_before": c0, "frontier": rows, "k_star": k_star, "closest_k": closest,
            "unreachable": k_star is None}


def line_risk_shares(book: dict, sigma: dict, capital: float) -> dict:
    """Each held line's Euler share of book variance, in %. A line is a cluster of one."""
    names = [s for s, e in book.items() if e > 0 and s in sigma]
    w = {s: book[s] / capital for s in names}
    var = sum(w[a] * w[b] * sigma[a][b] for a in names for b in names)
    if var <= 0:
        return {}
    return {a: round(w[a] * sum(w[b] * sigma[a][b] for b in names) / var * 100.0, 1)
            for a in names}


def variance_split(w: dict, betas: dict, sigma: dict, var_mkt: float) -> dict:
    """Book variance split into MARKET (β²σ²_m) and RESIDUAL. A driver can carry most of the
    book's variance because it carries most of its BETA — which is paid — or because it carries
    the idiosyncratic risk — which is not. The risk budget reads the total and cannot tell."""
    names = [s for s, x in w.items() if x]
    var = sum(w[a] * w[b] * sigma[a][b] for a in names for b in names)
    beta = sum(w[s] * float(betas.get(s) or 0.0) for s in names)
    sys_var = beta * beta * var_mkt
    return {"book_beta": round(beta, 3), "vol_pct": round(math.sqrt(max(var, 0)) * 100, 2),
            "systematic_pct": round(sys_var / var * 100, 1) if var > 0 else None,
            "residual_vol_pct": round(math.sqrt(max(var - sys_var, 0)) * 100, 2)}


def diversifier_health(book: dict, sigma: dict, capital: float, sectors, weeks: int) -> list[dict]:
    """Is each declared diversifier still doing its job? A COLUMN: it sells nothing.

    Per line, against the REST of the book: ρ, the beta on the rest (Cov(i,rest)/Var(rest)), and
    the line's share of book variance next to its share of the euros. A line that lowers risk
    carries LESS variance than capital (ratio < 1); one that has stopped diversifying carries more.
    `se_rho` = (1-ρ²)/√T, so a ρ drifting within its error is not read as a verdict. No threshold
    is applied: the plan already replaces a diversifier the moment a better one pays its cost.
    """
    names = [s for s, e in book.items() if e > 0]
    out = []
    tot = sum(book[s] for s in names) or 1.0
    w = {s: book[s] / capital for s in names}
    var = sum(w[a] * w[b] * sigma[a][b] for a in names for b in names)
    for s in sorted(x for x in sectors if x in w):
        rest = [r for r in names if r != s]
        if not rest or var <= 0:
            out.append({"sector_id": s, "rho_rest": None, "beta_rest": None, "se_rho": None,
                        "risk_share_pct": None, "capital_share_pct": None, "ratio": None})
            continue
        wr = {r: book[r] / sum(book[x] for x in rest) for r in rest}
        cov = sum(wr[r] * sigma[s][r] for r in rest)
        vr = sum(wr[a] * wr[b] * sigma[a][b] for a in rest for b in rest)
        rho = cov / math.sqrt(sigma[s][s] * vr) if sigma[s][s] > 0 and vr > 0 else None
        mc = sum(w[b] * sigma[s][b] for b in names)
        risk = w[s] * mc / var * 100.0
        cap_sh = book[s] / tot * 100.0
        out.append({"sector_id": s,
                    "rho_rest": round(rho, 2) if rho is not None else None,
                    "beta_rest": round(cov / vr, 2) if vr > 0 else None,
                    "se_rho": round((1 - rho * rho) / math.sqrt(max(weeks, 2)), 2)
                    if rho is not None else None,
                    "risk_share_pct": round(risk, 1), "capital_share_pct": round(cap_sh, 1),
                    "ratio": round(risk / cap_sh, 2) if cap_sh > 0 else None})
    return out


def build(rows: list[dict], snapshot: dict, cfg: dict, *, capital: float, cash_eur: float,
          n_target: int, slots: int, neutral_eur: float, ic: float, sigma_cs: float,
          horizon_days: int, realized_ytd: float, spread_bps: dict, default_bps: float,
          eligible: set, vehicle_fn, sigma_fn=None, ic_se: float | None = None,
          drivers: dict | None = None, budget_pct: float | None = None,
          min_z: float | None = None, diversifiers: set | None = None) -> dict:
    """Wire the rebalance table's facts into `plan`. Any missing input is an ERROR the column
    prints, never an empty list that reads as "nothing pays"."""
    from catalyx.execution import tax_engine
    from catalyx.scorer import covariance

    bm = cfg.get("best_moves", {}) or {}
    erp = float(bm.get("equilibrium_erp", 0.05))
    market = str(bm.get("market_proxy", "SPY"))

    held, etf_of, gain_of = {}, {}, {}
    for r in rows:
        eur = float(r.get("actual_eur") or 0.0)
        if eur > 0:
            held[r["sector_id"]] = eur
            etf_of[r["sector_id"]] = r.get("etf")
            gain_of[r["sector_id"]] = r.get("unrealized_pct")
    # Destinations must clear the book's own entry floor (`min_composite_z`): a risk-driven swap
    # buys a name that can still rise, not merely the quietest one. Without it a high risk price
    # picks rank-21 consumer_india for its volatility alone.
    zsnap = lambda s: (snapshot.get(s) or {}).get("composite_z")
    cands = {s for s in eligible if s not in held and vehicle_fn(s)
             and (min_z is None or (zsnap(s) is not None and float(zsnap(s)) >= float(min_z)))}
    for s in cands:
        etf_of[s] = vehicle_fn(s)
    names = sorted(set(held) | cands)
    tickers = [etf_of[s] for s in names]
    if not held:
        return {"error": "no held lines — nothing to swap"}
    if None in tickers:
        return {"error": f"no vehicle for {[s for s in names if not etf_of.get(s)]}"}

    sig = (sigma_fn or covariance.budget_sigma)(tickers + [market],
                                                int(bm.get("cov_weeks", 56)),
                                                int(bm.get("min_weeks", 40)))
    if sig.get("error"):
        return {"error": f"covariance unavailable: {sig['error']}"}
    S = [[float(v) * ANNUALIZE for v in row] for row in sig["sigma"]]
    k = len(names)
    var_mkt = S[k][k]
    delta = implied_delta(var_mkt, erp)
    if delta is None:
        return {"error": f"market proxy {market} has no variance in the window"}
    sigma = {a: {b: S[i][j] for j, b in enumerate(names)} for i, a in enumerate(names)}
    betas = {a: S[i][k] / var_mkt for i, a in enumerate(names)}
    H = horizon_days / 252.0
    z = {s: (snapshot.get(s) or {}).get("composite_z") for s in names}
    mu = expected_returns(betas, z, erp, ic, sigma_cs, H)

    def cost(kind, s_out, s_in, eur):
        c = eur * float(spread_bps.get(str(etf_of.get(s_in)), default_bps)) / 1e4
        if kind == "swap":
            c += eur * float(spread_bps.get(str(etf_of.get(s_out)), default_bps)) / 1e4
            g = gain_of.get(s_out)
            if g is not None and g > 0:
                realized = eur - eur / (1.0 + g / 100.0)
                c += tax_engine.compute_tax(gross_gain=realized,
                                            ytd_prior=max(0.0, realized_ytd)).tax_due
        return c

    # Standard error of a move's net. Two legs carry estimation error that a point estimate hides:
    # the BETA (premium = β·ERP — its se is √(resid var / market var) / √T per name) and the IC
    # (alpha = IC·Δz·σ_cs — its se is the measured one, not the declared sizing skill). Added in
    # quadrature and treated as independent across names. The risk leg is not given an SE: Σ is
    # already Ledoit-Wolf shrunk, and the legs above are the ones that dominate by an order.
    T = max(int(sig.get("weeks") or bm.get("cov_weeks", 56)), 2)
    se_beta = {a: math.sqrt(max(S[i][i] - betas[a] ** 2 * var_mkt, 0.0) / var_mkt) / math.sqrt(T)
               for i, a in enumerate(names)}

    def se(kind, s_out, s_in, eur):
        w = eur / capital
        b2 = se_beta[s_in] ** 2 + (se_beta[s_out] ** 2 if s_out else 0.0)
        prem = w * erp * H * math.sqrt(b2) * capital
        dz = abs((z.get(s_in) or 0.0) - ((z.get(s_out) or 0.0) if s_out else 0.0))
        alpha = (float(ic_se) * dz * sigma_cs * w * capital) if ic_se is not None else 0.0
        return math.hypot(prem, alpha)

    fee = bm.get("extra_trade_fee_eur")
    fee = float(fee) if fee is not None else None
    p = plan(held, {s: True for s in cands}, mu, sigma, delta, H, capital, cost, slots,
             n_target, neutral_eur, cash_eur, se_fn=se, over_slot_fee_eur=fee)
    rp = None
    if drivers is not None and budget_pct is not None:
        rp = risk_price(held, {s: True for s in cands}, mu, sigma, delta, H, capital, cost, slots,
                        n_target, neutral_eur, cash_eur, drivers, float(budget_pct),
                        [float(k) for k in bm.get("risk_price_k", [1, 1.5, 2, 3, 4, 6, 10])], fee)
    # The vehicles each leg would trade, stamped on the move: the 63-day scorer has to price what
    # would have been BOUGHT, and the vehicle map changes with the universe (v14.x).
    for m in list(p["first_round"]) + list(p["steps"]):
        m["sell_etf"] = etf_of.get(m["sell"]) if m.get("sell") else None
        m["buy_etf"] = etf_of.get(m["buy"])
    split = variance_split({s: e / capital for s, e in held.items()}, betas, sigma, var_mkt)
    dh = diversifier_health(held, sigma, capital, diversifiers or set(), T) if diversifiers else []
    return {"column": True, "delta": round(delta, 3), "diversifier_health": dh, "erp": erp, "market": market, "ic": ic,
            "ic_se": ic_se, "sigma_cs": sigma_cs, "horizon_days": horizon_days, "n_candidates": len(cands),
            "n_moves": len(p["first_round"]),
            "n_positive": sum(1 for m in p["first_round"] if m["net_eur"] > 0),
            "betas": {s: round(b, 2) for s, b in betas.items()},
            "variance_split": split, "risk_price": rp,
            "line_risk": line_risk_shares(held, sigma, capital),
            "min_z": min_z, "extra_trade_fee_eur": fee,
            **{k: v for k, v in p.items() if k != "book"},
            "top": p["first_round"][:5], "first_round": None,
            "imputed": [n for n, i in zip(names, sig.get("imputed") or []) if i]}


def render(bm: dict) -> list[str]:
    """Terminal block. English, like every rendered surface."""
    if not bm:
        return []
    if bm.get("error"):
        return ["", f"BEST MOVES (column)  unavailable — {bm['error']}"]
    vs = bm["variance_split"]
    out = ["", "BEST MOVES — one currency for every trade: Δpremium + Δalpha + Δrisk − cost, € over "
               f"{bm['horizon_days']}d (COLUMN — moves nothing)",
           f"  δ={bm['delta']:.2f} implied by {bm['market']} (ERP {bm['erp']:.0%}) · IC {bm['ic']:.3f}"
           f" (the declared sizing skill) · {bm['n_positive']} of {bm['n_moves']} moves pay",
           f"  book β {vs['book_beta']:.2f} · vol {vs['vol_pct']:.1f}% · "
           f"{vs['systematic_pct']}% of its variance is MARKET (paid), residual vol "
           f"{vs['residual_vol_pct']:.1f}%",
           f"  {'move':54} {'€':>7} {'Δprem':>7} {'Δalpha':>7} {'Δrisk':>7} {'cost':>6} "
           f"{'NET':>7} {'±se':>6} {'t':>5} {'vol':>11}"]
    for m in bm.get("top") or []:
        what = (f"{m['sell']} → {m['buy']}" if m["kind"] == "swap" else f"cash → {m['buy']}")
        out.append(f"  {what[:54]:54} {m['eur']:7.0f} {m['d_premium_eur']:7.1f} "
                   f"{m['d_alpha_eur']:7.1f} {m['d_risk_eur']:7.1f} {m['cost_eur']:6.1f} "
                   f"{m['net_eur']:7.1f} "
                   + (f"{m['net_se_eur']:6.1f} {m['t']:5.1f} " if m.get("net_se_eur") else f"{'—':>6} {'—':>5} ")
                   + f"{m['vol_before_pct']:5.1f}→{m['vol_after_pct']:4.1f}")
    if bm["steps"]:
        out.append(f"  PLAN  {len(bm['steps'])} move(s), {bm['slots_used']}/{bm['slots']} slots, "
                   f"net €{bm['net_eur']:.1f}: "
                   + " ; ".join((f"{s['sell']}→{s['buy']}" if s["kind"] == "swap"
                                 else f"cash→{s['buy']}") for s in bm["steps"]))
    else:
        out.append("  PLAN  no move pays its cost against this book — hold is the priced answer")
    out.extend(_render_diversifiers(bm.get("diversifier_health")))
    out.extend(_render_risk_price(bm.get("risk_price")))
    c = bm.get("clearing")
    if c:
        out.append(f"  CLEARING  best move left out: "
                   + (f"{c['sell']}→{c['buy']}" if c["kind"] == "swap" else f"cash→{c['buy']}")
                   + f" net €{c['net_eur']:.1f}")
    return out


def _render_risk_price(rp: dict | None) -> list[str]:
    if not rp:
        return []
    b = rp["budget_pct"]
    out = ["", f"  RISK PRICE — the {b:.0f}% cluster-variance budget as a price (same utility at k× the "
               "market's risk aversion; nothing blocks, you pick the point)",
           "  before: " + ", ".join(f"{c} {v:.1f}%" for c, v in sorted(
               rp["clusters_before"].items(), key=lambda kv: -kv[1])[:3])
           + f" · vol {rp['vol_before_pct']:.1f}%",
           f"  {'k':>4} {'Δ return':>9} {'friction':>9} {'trades':>6} {'vol':>6} {'worst cluster':>40}  moves"]
    for r in rp["frontier"]:
        mv = "; ".join((f"{m['sell']}→{m['buy']}" if m["kind"] == "swap" else f"cash→{m['buy']}")
                       for m in r["moves"]) or "—"
        mark = ("  ← within budget" if r["k"] == rp["k_star"] else
                "  ← closest to the budget" if rp["unreachable"] and r["k"] == rp["closest_k"] else "")
        wc = f"{r['worst_cluster']} {r['worst_share_pct']:.1f}%" if r["worst_cluster"] else "—"
        out.append(f"  {r['k']:>4g} {r['d_return_eur']:9.1f} {r['cost_eur']:9.1f} {r['legs']:6d} "
                   f"{r['vol_after_pct']:5.1f}% {wc[:40]:>40}  {mv}{mark}")
        if r["k"] == rp["k_star"]:
            break
    if rp["unreachable"]:
        out.append("  no plan in the grid brings every cluster inside the budget with these candidates")
    return out


def _render_diversifiers(dh: list[dict] | None) -> list[str]:
    if not dh:
        return []
    out = ["", "  DIVERSIFIERS - held to lower book variance; exempt from the rank-out SELL "
               "(column - sells nothing)",
           f"  {'line':34} {'rho/rest':>9} {'±se':>5} {'beta/rest':>10} {'var share':>10} "
           f"{'capital':>8} {'ratio':>6}"]
    for d in dh:
        if d["rho_rest"] is None:
            out.append(f"  {d['sector_id'][:34]:34} unmeasured")
            continue
        out.append(f"  {d['sector_id'][:34]:34} {d['rho_rest']:9.2f} {d['se_rho']:5.2f} "
                   f"{d['beta_rest']:10.2f} {d['risk_share_pct']:9.1f}% {d['capital_share_pct']:7.1f}% "
                   f"{d['ratio']:6.2f}")
    out.append("  ratio < 1: carries less variance than capital (diversifying); > 1: no longer")
    return out
