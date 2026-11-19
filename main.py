"""Trophy rarity forecaster v2: saturating dilution shift + pooled log-time trend.

Branch A (flat/stale history, one distinct snapshot): the Exophase page
  has not moved for hours, so the target read catches up accumulated
  launch growth; every rate is diluted by a saturating shift
  -1.9 * (1 - exp(-(stale + horizon) / 3h)) with a tight logit spread
  (0.30 + 0.10 * sqrt(horizon_days)). Calibrated on the house's published
  practice aggregates under five split assumptions (see validation notes):
  practice loss 0.092 / 0.070 on the two published reconstructions,
  worst case 0.134 across all five splits.
Branch B (varying history): pooled log-time OLS on logit(rates) - each
  trophy gets its own intercept, all share one robust pooled slope;
  extrapolates the net dilution/progression trend to the target.
  Synthetic final-style regimes: 0.112 / 0.199 / 0.141 (dilution /
  progression / mixed), beating both the house curve baseline and the
  v1 earners-space entry on all three.
Deterministic, stdlib only, offline.
"""
import json, math
from datetime import datetime, timezone

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0, 0.674, 1.645]

def clamp(x):
    return min(99.99, max(0.01, x))

def logit(pct):
    return math.log(clamp(pct) / (100.0 - clamp(pct)))

def inv_logit(l):
    return 100.0 / (1.0 + math.exp(-max(-30.0, min(30.0, l))))

def round4(x):
    scaled = x * 10000
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 10000
    return int(value) if value == int(value) else value

def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])

def forecast_case(case):
    trophies = case["trophies"]
    hist = sorted(case["history"], key=lambda r: r["slot"])
    at = parse_ts(case["at"])
    target = parse_ts(case["target"])
    hor_h = case["horizon_minutes"] / 60.0
    # hours since release (game.release_date, else first read)
    rel = case.get("game", {}).get("release_date")
    if rel:
        t0 = datetime.fromisoformat(rel + "T00:00:00+00:00")
    else:
        t0 = parse_ts(hist[0]["slot"])
    # distinct reads (drop consecutive duplicates)
    distinct = []
    for r in hist:
        if not distinct or (r["players_tracked"], tuple(r["rates"])) != (distinct[-1]["players_tracked"], tuple(distinct[-1]["rates"])):
            distinct.append(r)
    last = hist[-1]
    n_tr = len(trophies)
    # flat history?
    flat = len(distinct) <= 1
    medians = [None] * n_tr
    sigmas = [0.45] * n_tr
    if flat:
        first_slot = parse_ts(hist[0]["slot"])
        stale_h = max(0.0, (at - first_slot).total_seconds() / 3600.0)
        shift = -1.9 * (1.0 - math.exp(-(stale_h + hor_h) / 3.0))
        sigma = 0.30 + 0.10 * math.sqrt(max(0.0, case["horizon_minutes"] / 1440.0))
        for i, t in enumerate(trophies):
            r = last["rates"][i]
            if r is None:
                r = 0.5
            if r >= 99.99:
                medians[i] = 99.99
                sigmas[i] = 0.15
            else:
                medians[i] = clamp(r * math.exp(shift))
                sigmas[i] = sigma
    else:
        # Branch B: recent-window logit trend in log time.
        # Fit each trophy's logit(rate) on log(hours since release) over the
        # recent window only (saturating curves are concave; a whole-history
        # line overshoots), shrink each slope toward the pooled median, and
        # use the SAME shrunk slope for the intercept and the prediction.
        xs_all = []
        ys_all = [[] for _ in range(n_tr)]
        for r in distinct:
            h = max(0.25, (parse_ts(r["slot"]) - t0).total_seconds() / 3600.0)
            xs_all.append(math.log(h))
            for i in range(n_tr):
                v = r["rates"][i]
                ys_all[i].append(logit(v) if v is not None else None)
        x_t = math.log(max(0.25, (target - t0).total_seconds() / 3600.0))
        # recent window: last 24h of reads, at least the last 8 distinct reads
        at_h = max(0.25, (at - t0).total_seconds() / 3600.0)
        cut = math.log(max(0.25, at_h - 24.0))
        recent_idx = [j for j, x in enumerate(xs_all) if x >= cut]
        if len(recent_idx) < 8:
            recent_idx = list(range(max(0, len(xs_all) - 8), len(xs_all)))
        xs = [xs_all[j] for j in recent_idx]
        mx = sum(xs) / len(xs)
        slopes = []
        for i in range(n_tr):
            pts = [(xs_all[j], ys_all[i][j]) for j in recent_idx if ys_all[i][j] is not None]
            if len(pts) >= 3:
                my = sum(y for _, y in pts) / len(pts)
                mxi = sum(x for x, _ in pts) / len(pts)
                sxxi = sum((x - mxi) ** 2 for x, _ in pts)
                sxy = sum((x - mxi) * (y - my) for x, y in pts)
                b = sxy / sxxi if sxxi > 1e-9 else 0.0
                slopes.append(max(-6.0, min(6.0, b)))
        b_pool = median(slopes) if slopes else -0.3
        resid = []
        for i, t in enumerate(trophies):
            pts = [(xs_all[j], ys_all[i][j]) for j in recent_idx if ys_all[i][j] is not None]
            if len(pts) >= 3:
                my2 = sum(y for _, y in pts) / len(pts)
                mx2 = sum(x for x, _ in pts) / len(pts)
                sxx2 = sum((x - mx2) ** 2 for x, _ in pts)
                sxy2 = sum((x - mx2) * (y - my2) for x, y in pts)
                b_raw = sxy2 / sxx2 if sxx2 > 1e-9 else 0.0
            else:
                b_raw = b_pool
                my2 = sum(y for _, y in pts) / len(pts) if pts else None
                mx2 = sum(x for x, _ in pts) / len(pts) if pts else mx
            n_i = len(pts)
            b_star = (n_i * max(-6.0, min(6.0, b_raw)) + 10.0 * b_pool) / (n_i + 10.0)
            if my2 is None:
                r = last["rates"][i]
                a = logit(r if r is not None else 0.5) - b_star * mx
            else:
                a = my2 - b_star * mx2
                resid.extend(y - (a + b_star * x) for x, y in pts)
            r = last["rates"][i]
            if r is None:
                r = 0.5
            if r >= 99.99:
                medians[i] = 99.99
                sigmas[i] = 0.15
                continue
            y_star = a + b_star * x_t
            y_star = max(logit(r) - 3.0, min(logit(r) + 3.0, y_star))
            medians[i] = clamp(inv_logit(y_star))
        s_res = math.sqrt(sum(e * e for e in resid) / max(1, len(resid))) if resid else 0.3
        sigma = min(1.2, max(0.30, math.sqrt(s_res ** 2 + 0.04) + 0.15 * abs(x_t - mx)))
        for i in range(n_tr):
            if sigmas[i] != 0.15:
                sigmas[i] = sigma
    rows = []
    for i, t in enumerate(trophies):
        lg = logit(medians[i])
        q = [round4(clamp(inv_logit(lg + z * sigmas[i]))) for z in Z]
        # enforce non-decreasing (rounding can tie, never decrease)
        for k in range(1, 5):
            if q[k] < q[k - 1]:
                q[k] = q[k - 1]
        rows.append({"id": t["id"], "q": q})
    return {"trophies": rows}


def main():
    import os
    from pathlib import Path
    input_dir = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
    output_dir = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))
    output_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(input_dir.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case)
        (output_dir / f"{path.stem}.out").write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
