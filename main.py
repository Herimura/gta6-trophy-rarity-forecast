#!/usr/bin/env python3
"""GTA VI trophy earn-rate forecaster (make.fun gta6-trophy-rarity-forecast-01).

Reads one case per *.in file in $MAKEFUN_INPUT_DIR (else /input) and writes
<id>.out to $MAKEFUN_OUTPUT_DIR (else /output): for each trophy, five
quantiles (5/25/50/75/95%) of its Exophase earn rate at the case target.

Method (stdlib only, deterministic, no network):
  A case's history is a sequence of reads of one web page. Between Exophase
  refreshes the page is frozen, so the history is first reduced to its
  distinct snapshots. Everything is then modelled in earners space:
  earners_i = rate_i / 100 * players_tracked, rate_i = 100 * earners_i /
  players. A rate moves because the tracked pool grows (dilution) and
  because players earn more trophies (progression); the two are forecast
  separately and recombined.

  * Tracked pool (frozen branch): grown from the last read by a
    horizon-scaled factor, since a frozen page's pool is the stale part.
  * Earners per trophy (trend branch): a least-squares line of the logit
    rate on log-time over the distinct snapshots; the move it implies from
    the last rate to the target is shrunk toward the trophy's class move
    (story / completion / collectible / grind / activity classes read off
    the trophy's name and description), capped, and damped, because
    attainment saturates while a fitted line does not.
  * Frozen history (one distinct snapshot — the page has not refreshed in
    the case's window, as on launch night): a refresh before the target is
    expected. The pool is forecast to grow by a horizon-scaled factor and
    the new cohort is credited with earners concentrated on the trophies
    players earn first (pass-through share (rate/100)**0.5), so medians
    dilute instead of pretending the frozen page is live.
  * Structure: the platinum's earners cannot exceed any other trophy's,
    and the chapter chain (Chapter 1..4, then the final mission) is
    non-increasing in earners.
  * Spread: logit-normal around the median, widening with the horizon,
    the trophy's own fit residuals, refresh risk, and small-count noise —
    calibrated so the 90% band is honest (the house baselines' practice
    band covered none of the refreshed rates).
"""

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0, 0.674, 1.645]
EPOCH = datetime(2026, 11, 19, 0, 0, tzinfo=timezone.utc)

# Frozen-page (single snapshot) calibration, fitted on the launch-night
# practice cut the house published with the kit.
FLAT_POOL_GROWTH_90M = 4.2   # pool multiplier over a 90-minute horizon
FLAT_PASS_GAMMA = 0.5        # new-cohort earners concentrate on common trophies
FLAT_SIGMA = 0.85            # logit spread when no refresh has been seen

# Trend-branch spread constants.
RW_PER_SQRT_DAY = 0.42
TREND_DAMP = 0.35  # minimax across dilution/progression/mixed backtests
REFRESH_RISK = 0.24
SIGMA_FLOOR, SIGMA_CAP = 0.35, 2.0


def clamp_rate(x: float) -> float:
    return min(99.99, max(0.01, x))


def logit(pct: float) -> float:
    p = clamp_rate(pct) / 100.0
    return math.log(p / (1.0 - p))


def inv_logit(lg: float) -> float:
    if lg >= 0:
        return 100.0 / (1.0 + math.exp(-lg))
    e = math.exp(lg)
    return 100.0 * e / (1.0 + e)


def round4(x: float):
    """Round to 4 decimals, half up, as JavaScript's Math.round does."""
    scaled = x * 10000
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 10000
    return int(value) if float(value).is_integer() else value


def parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def x_of(t: datetime) -> float:
    """ln(1 + hours since the epoch); the launch curve's time base."""
    hours = (t - EPOCH).total_seconds() / 3600.0
    return math.log(max(1.0 + hours, 0.05))


def ls_fit(xs, ys):
    """Least-squares y ~ a + b*x; returns (a, b, residual_std)."""
    n = len(xs)
    if n == 0:
        return 0.0, 0.0, 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 1e-12:
        return my, 0.0, 0.0
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    if n > 2:
        resid = math.sqrt(sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys)) / (n - 2))
    else:
        resid = 0.0
    return a, b, resid


def classify(trophy) -> str:
    if trophy["tier"] == "platinum":
        return "platinum"
    desc = trophy["description"].lower()
    if "attain 100% completion" in desc:
        return "completion"
    if desc.startswith("complete "):
        return "story"
    if any(k in desc for k in ("collect", "discover", "explore all", "every spot",
                               "every location", "locations", "meditate")):
        return "collectible"
    if any(k in desc for k in ("earn $", "spend $", "purchase all", "stunts",
                               "challenges", "gold medal in all", "rob 5", "win 20")):
        return "grind"
    return "activity"


def snapshots_of(case):
    """Distinct page states in the history, oldest first."""
    snaps = []
    prev = None
    for read in case["history"]:
        key = (read["players_tracked"], read["trophies_earned"], tuple(read["rates"]))
        if key != prev:
            snaps.append(read)
            prev = key
    return snaps


def forecast_flat(case, snaps, horizon_days):
    """One distinct snapshot: the page is frozen and a refresh is expected."""
    last = snaps[-1]
    players = float(last["players_tracked"])
    h_hours = horizon_days * 24.0
    # Pool growth: calibrated at 90 minutes, scaled by sqrt of horizon, capped.
    growth = math.exp(math.log(FLAT_POOL_GROWTH_90M) * math.sqrt(h_hours / 1.5))
    growth = min(growth, 14.0)
    players_hat = players * growth
    rows = []
    for i, trophy in enumerate(case["trophies"]):
        rate = last["rates"][i]
        rate = 0.0 if rate is None else rate
        earners = rate / 100.0 * players
        share = (rate / 100.0) ** FLAT_PASS_GAMMA if rate > 0 else 0.0
        earners_hat = earners * (1.0 + (growth - 1.0) * share)
        median = 100.0 * earners_hat / players_hat if players_hat > 0 else rate
        rows.append((trophy, median, FLAT_SIGMA))
    return rows


def forecast_trend(case, snaps, target_x):
    """Two or more distinct snapshots: damped log-time trend in logit space.

    The house curve baseline reads an undamped least-squares line at the
    target; on a launch curve that overshoots, because attainment saturates
    while the line does not. Here each trophy's fitted move from its last
    rate is shrunk toward its class's move, capped, and damped by a fixed
    factor before it is applied, and the spread is built from the fit's own
    residuals plus horizon, refresh and small-count terms.
    """
    xs = [x_of(parse_time(s["slot"])) for s in snaps]
    last = snaps[-1]
    x_last = xs[-1]
    dx = max(target_x - x_last, 0.0)
    n_t = len(case["trophies"])
    classes = [classify(t) for t in case["trophies"]]
    earners_last = []
    for i in range(n_t):
        r = last["rates"][i]
        earners_last.append(0.0 if r is None else r / 100.0 * float(last["players_tracked"]))

    moves, resids = [], []
    for i in range(n_t):
        pts = [(x, s["rates"][i]) for x, s in zip(xs, snaps) if s["rates"][i] is not None]
        if len(pts) >= 2 and pts[-1][0] > pts[0][0]:
            _a, b, resid = ls_fit([p[0] for p in pts], [logit(p[1]) for p in pts])
            moves.append(b * dx)
            resids.append(resid)
        else:
            moves.append(None)
            resids.append(None)

    class_move, class_resid = {}, {}
    for cls in set(classes):
        idx = [i for i in range(n_t) if classes[i] == cls and moves[i] is not None]
        if idx:
            w = [max(earners_last[i], 1.0) for i in idx]
            class_move[cls] = sum(m * wi for m, wi in zip([moves[i] for i in idx], w)) / sum(w)
            class_resid[cls] = sum(resids[i] for i in idx) / len(idx)
        else:
            class_move[cls] = 0.0
            class_resid[cls] = 0.45
    all_moves = sorted(m for m in moves if m is not None)
    if all_moves:
        lo = all_moves[max(0, int(0.05 * len(all_moves)))]
        hi = all_moves[min(len(all_moves) - 1, int(0.95 * len(all_moves)))]
    else:
        lo, hi = -1.0, 1.5

    rows = []
    for i, trophy in enumerate(case["trophies"]):
        cls = classes[i]
        own = moves[i]
        base = own if own is not None else class_move[cls]
        trust = min(earners_last[i], 50.0) / 10.0
        move = (trust * base + 3.0 * class_move[cls]) / (trust + 3.0) if own is not None else class_move[cls]
        move = min(max(move, lo - 0.25), hi + 0.25)
        move = min(max(move, -1.6), 2.4)
        last_rate = last["rates"][i]
        last_rate = 0.0 if last_rate is None else last_rate
        median = inv_logit(logit(last_rate) + TREND_DAMP * move)
        resid = resids[i] if resids[i] is not None else class_resid[cls]
        rows.append([trophy, median, resid, earners_last[i]])
    return rows, float(last["players_tracked"])


def apply_structure(case, medians):
    """Cross-trophy structure on medians (rates): platinum <= every other
    trophy; the chapter chain is non-increasing. Returns adjusted medians."""
    med = list(medians)
    by_desc = {}
    for i, t in enumerate(case["trophies"]):
        by_desc.setdefault(t["description"], []).append(i)
    chain = []
    for label in ("Complete Chapter 1.", "Complete Chapter 2.", "Complete Chapter 3.",
                  "Complete Chapter 4.", "Complete the final mission."):
        if label in by_desc:
            chain.extend(by_desc[label])
    for prev, cur in zip(chain, chain[1:]):
        if med[cur] > med[prev]:
            med[cur] = med[prev]
    plat = [i for i, t in enumerate(case["trophies"]) if t["tier"] == "platinum"]
    if plat:
        others_min = min(med[i] for i in range(len(med)) if i not in plat)
        for i in plat:
            med[i] = min(med[i], others_min)
    return med


def forecast_case(case, name):
    if case["levels"] != LEVELS:
        raise ValueError(f"{name} asks for levels {case['levels']}, not {LEVELS}")
    if not case["history"]:
        raise ValueError(f"{name} has no history")
    horizon_days = case["horizon_minutes"] / 1440.0
    target_x = x_of(parse_time(case["target"]))
    snaps = snapshots_of(case)
    if len(snaps) >= 2:
        raw, _p_hat = forecast_trend(case, snaps, target_x)
        medians = apply_structure(case, [r[1] for r in raw])
        rows = []
        for (trophy, _m, resid, e_hat), median in zip(raw, medians):
            poisson = 0.6 / math.sqrt(max(e_hat, 1.0)) * min(math.sqrt(horizon_days), 1.0)
            sigma = math.sqrt((RW_PER_SQRT_DAY * math.sqrt(horizon_days)) ** 2
                              + (resid * (0.7 + 0.6 * max(target_x - x_of(parse_time(snaps[-1]["slot"])), 0.0))) ** 2
                              + REFRESH_RISK ** 2 + poisson ** 2)
            rows.append((trophy, median, min(max(sigma, SIGMA_FLOOR), SIGMA_CAP)))
    else:
        raw = forecast_flat(case, snaps, horizon_days)
        medians = apply_structure(case, [r[1] for r in raw])
        rows = [(trophy, median, sigma) for (trophy, _m, sigma), median in zip(raw, medians)]

    out_rows = []
    for trophy, median, sigma in rows:
        lg = logit(median)
        q = [round4(inv_logit(lg + z * sigma)) for z in Z]
        q = sorted(q)
        out_rows.append({"id": trophy["id"], "q": q})
    return {"trophies": out_rows}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case, path.name)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
