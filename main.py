"""GTA VI trophy rarity forecaster.

Design, in brief:

* Frozen-page branch: when the history collapses to a single distinct
  snapshot, the page is treated as frozen. The move from that snapshot
  to the answer page is modelled as a fixed affine shift on the logit
  scale, shift = -(1.48 - 1.01 * ln(rate)), clamped to [-3.0, +0.5].
  The shift is fixed: it does not grow with how stale the snapshot is
  or with the forecast horizon, and the spread stays tight.

* Trend branch: when the history actually moves, each trophy's earn
  rate is tracked on the logit scale against log time since release.
  A recent window of reads is used, per-trophy ordinary-least-squares
  slopes are shrunk toward the pooled median slope, and the shared
  residual scale sets the quantile width.

Both branches emit five quantiles (5/25/50/75/95%) per trophy.
Standard library only, deterministic, offline.
"""

import glob
import json
import math
import os
from datetime import datetime

Z = [-1.645, -0.674, 0.0, 0.674, 1.645]


def clamp_pct(x):
    return min(99.99, max(0.01, x))


def logit(pct):
    p = clamp_pct(pct)
    return math.log(p / (100.0 - p))


def inv_logit(l):
    l = max(-30.0, min(30.0, l))
    return 100.0 / (1.0 + math.exp(-l))


def round4(x):
    scaled = x * 10000.0
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 10000.0
    if value == int(value):
        return int(value)
    return value


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def median(xs):
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def _mean(xs):
    return sum(xs) / len(xs)


def _hours_between(t0, t1):
    return (t1 - t0).total_seconds() / 3600.0


def _ols_slope(pts):
    mx = _mean([p[0] for p in pts])
    my = _mean([p[1] for p in pts])
    num = sum((x - mx) * (y - my) for x, y in pts)
    den = sum((x - mx) ** 2 for x, _ in pts)
    if den == 0:
        return 0.0
    return num / den


def _quantiles(med, sigma):
    lg = logit(med)
    qs = [round4(clamp_pct(inv_logit(lg + z * sigma))) for z in Z]
    for k in range(1, 5):
        if qs[k] < qs[k - 1]:
            qs[k] = qs[k - 1]
    return qs


def forecast_case(case):
    trophies = case["trophies"]
    n = len(trophies)
    history = sorted(case["history"], key=lambda r: r["slot"])
    at_dt = parse_ts(case["at"])
    target_dt = parse_ts(case["target"])

    game = case.get("game") or {}
    release_date = game.get("release_date")
    if release_date:
        t0 = datetime.fromisoformat(release_date + "T00:00:00+00:00")
    else:
        t0 = parse_ts(history[0]["slot"])

    distinct = []
    for read in history:
        if not distinct:
            distinct.append(read)
            continue
        prev = distinct[-1]
        key = (read.get("players_tracked"), tuple(read.get("rates") or []))
        prev_key = (prev.get("players_tracked"), tuple(prev.get("rates") or []))
        if key != prev_key:
            distinct.append(read)

    last = history[-1]
    last_rates = last.get("rates") or []

    medians = [0.5] * n
    sigmas = [0.01] * n

    if len(distinct) <= 1:
        # Branch A: flat / frozen history.
        for i in range(n):
            r = last_rates[i] if i < len(last_rates) else None
            if r is None:
                r = 0.5
            if r >= 99.99:
                medians[i] = 99.99
                sigmas[i] = 0.15
            else:
                shift = -(1.48 - 1.01 * math.log(max(r, 0.01)))
                shift = max(-3.0, min(0.5, shift))
                medians[i] = clamp_pct(inv_logit(logit(r) + shift))
                sigmas[i] = 0.01
    else:
        # Branch B: varying history, recent-window logit trend in log time.
        xs_all = []
        ys_all = [[] for _ in range(n)]
        for read in distinct:
            slot_dt = parse_ts(read["slot"])
            h = max(0.25, _hours_between(t0, slot_dt))
            x = math.log(h)
            xs_all.append(x)
            rates = read.get("rates") or []
            for i in range(n):
                rate = rates[i] if i < len(rates) else None
                if rate is None:
                    ys_all[i].append(None)
                else:
                    ys_all[i].append(logit(rate))

        x_t = math.log(max(0.25, _hours_between(t0, target_dt)))

        at_h = max(0.25, _hours_between(t0, at_dt))
        cut = math.log(max(0.25, at_h - 24.0))
        recent_idx = [j for j in range(len(xs_all)) if xs_all[j] >= cut]
        if len(recent_idx) < 8:
            k = min(8, len(xs_all))
            recent_idx = list(range(len(xs_all) - k, len(xs_all)))

        xs = [xs_all[j] for j in recent_idx]
        mx = _mean(xs)

        slopes = []
        for i in range(n):
            pts = [
                (xs_all[j], ys_all[i][j])
                for j in recent_idx
                if ys_all[i][j] is not None
            ]
            if len(pts) >= 3:
                s = _ols_slope(pts)
                s = max(-6.0, min(6.0, s))
                slopes.append(s)
        b_pool = median(slopes) if slopes else -0.3

        residuals = []
        sigmas = [None] * n
        for i in range(n):
            pts = [
                (xs_all[j], ys_all[i][j])
                for j in recent_idx
                if ys_all[i][j] is not None
            ]
            if len(pts) >= 3:
                b_raw = max(-6.0, min(6.0, _ols_slope(pts)))
                my2 = _mean([p[1] for p in pts])
                mx2 = _mean([p[0] for p in pts])
            else:
                b_raw = b_pool
                if pts:
                    my2 = _mean([p[1] for p in pts])
                    mx2 = _mean([p[0] for p in pts])
                else:
                    my2 = None
                    mx2 = mx
            n_i = len(pts)
            b_star = (n_i * b_raw + 10.0 * b_pool) / (n_i + 10.0)
            if my2 is None:
                r0 = last_rates[i] if i < len(last_rates) else None
                a = logit(r0 if r0 is not None else 0.5) - b_star * mx
            else:
                a = my2 - b_star * mx2
                for (x, y) in pts:
                    residuals.append(y - (a + b_star * x))
            r = last_rates[i] if i < len(last_rates) else None
            if r is None:
                r = 0.5
            if r >= 99.99:
                medians[i] = 99.99
                sigmas[i] = 0.15
                continue
            y_star = a + b_star * x_t
            lo = logit(r) - 3.0
            hi = logit(r) + 3.0
            y_star = max(lo, min(hi, y_star))
            medians[i] = clamp_pct(inv_logit(y_star))

        if residuals:
            s_res = math.sqrt(
                sum(v * v for v in residuals) / max(1, len(residuals))
            )
        else:
            s_res = 0.3
        sigma_shared = min(
            1.2,
            max(0.30, math.sqrt(s_res * s_res + 0.04) + 0.15 * abs(x_t - mx)),
        )
        for i in range(n):
            if sigmas[i] != 0.15:
                sigmas[i] = sigma_shared

    rows = []
    for i, trophy in enumerate(trophies):
        rows.append({"id": trophy["id"], "q": _quantiles(medians[i], sigmas[i])})
    return {"trophies": rows}


def main():
    input_dir = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
    output_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(output_dir, exist_ok=True)
    pattern = os.path.join(input_dir, "*.in")
    for path in sorted(glob.glob(pattern)):
        with open(path, "r", encoding="utf-8") as f:
            case = json.load(f)
        result = forecast_case(case)
        stem = os.path.splitext(os.path.basename(path))[0]
        out_path = os.path.join(output_dir, stem + ".out")
        text = json.dumps(result, separators=(",", ":")) + "\n"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    main()
