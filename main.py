"""GTA VI trophy rarity forecaster (v2).

Reads make.fun trophy cases (JSON) and, for every trophy in a case,
emits five quantiles of its Exophase earn rate at the case target.

The model works on the logit scale throughout.  When the recorded
history never changes it assumes the page is stale and decays the last
rates towards zero; when the history does move it fits a shrunk
log-time trend per trophy, borrowing strength from a pooled slope
across all trophies, and sizes the spread from the fit residuals.

Standard library only, fully deterministic, no network access.
"""

import glob
import json
import math
import os
from datetime import datetime, timezone

# Normal quantiles for the 5 / 25 / 50 / 75 / 95 percent levels.
Z_TABLE = [-1.645, -0.674, 0.0, 0.674, 1.645]

RATE_FLOOR = 0.01
RATE_CEIL = 99.99
SATURATED = 99.99
SATURATED_SPREAD = 0.15


# ---------------------------------------------------------------- times

def read_instant(text):
    """Parse an ISO-8601 timestamp into an aware UTC datetime."""
    cleaned = text.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    moment = datetime.fromisoformat(cleaned)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def read_release_day(text):
    """Parse a YYYY-MM-DD release date as midnight UTC that day."""
    day = datetime.strptime(text.strip(), "%Y-%m-%d")
    return day.replace(tzinfo=timezone.utc)


def gap_hours(earlier, later):
    return (later - earlier).total_seconds() / 3600.0


# ------------------------------------------------------- percent helpers

def bound_rate(value):
    if value < RATE_FLOOR:
        return RATE_FLOOR
    if value > RATE_CEIL:
        return RATE_CEIL
    return value


def to_logit(pct):
    p = bound_rate(pct)
    return math.log(p / (100.0 - p))


def from_logit(score):
    clipped = score
    if clipped < -30.0:
        clipped = -30.0
    elif clipped > 30.0:
        clipped = 30.0
    return 100.0 / (1.0 + math.exp(-clipped))


def round4(value):
    """Round half up to four decimals; whole numbers stay integers."""
    scaled = value * 10000.0
    units = math.floor(scaled)
    if scaled - units >= 0.5:
        units += 1
    result = units / 10000.0
    if result == int(result):
        return int(result)
    return result


# ------------------------------------------------------------ statistics

def avg(numbers):
    return sum(numbers) / len(numbers)


def middle(numbers):
    ordered = sorted(numbers)
    count = len(ordered)
    half = count // 2
    if count % 2:
        return ordered[half]
    return (ordered[half - 1] + ordered[half]) / 2.0


def limit_slope(slope):
    if slope < -6.0:
        return -6.0
    if slope > 6.0:
        return 6.0
    return slope


def least_squares_slope(points):
    """Slope of the OLS line through (x, y) points, on their own means."""
    centre_x = avg([pt[0] for pt in points])
    centre_y = avg([pt[1] for pt in points])
    numerator = 0.0
    denominator = 0.0
    for px, py in points:
        offset = px - centre_x
        numerator += offset * (py - centre_y)
        denominator += offset * offset
    if denominator == 0.0:
        return 0.0
    return numerator / denominator


# -------------------------------------------------------------- the model

def forecast_case(case):
    trophies = case["trophies"]
    n_trophies = len(trophies)

    history = sorted(
        case["history"], key=lambda rec: read_instant(rec["slot"])
    )
    checkpoint = read_instant(case["at"])
    target = read_instant(case["target"])
    horizon_minutes = case["horizon_minutes"]

    # Time zero: the release day's midnight when the case names one,
    # otherwise the moment of the earliest recorded read.
    game_info = case.get("game") or {}
    release_text = game_info.get("release_date")
    if release_text:
        origin = read_release_day(release_text)
    else:
        origin = read_instant(history[0]["slot"])

    # Collapse runs of identical reads: only changes carry information.
    unique_reads = []
    previous_key = None
    for rec in history:
        key = (rec.get("players_tracked"), tuple(rec.get("rates") or []))
        if unique_reads and key == previous_key:
            continue
        unique_reads.append(rec)
        previous_key = key

    final_rates = history[-1].get("rates") or []

    def closing_rate(index):
        if index < len(final_rates):
            value = final_rates[index]
            if value is not None:
                return value
        return 0.5

    medians = [None] * n_trophies
    spreads = [None] * n_trophies

    if len(unique_reads) <= 1:
        _forecast_flat(
            history, checkpoint, horizon_minutes, n_trophies,
            closing_rate, medians, spreads,
        )
    else:
        _forecast_trending(
            unique_reads, origin, checkpoint, target, n_trophies,
            closing_rate, medians, spreads,
        )

    rows = []
    for index, trophy in enumerate(trophies):
        centre = to_logit(medians[index])
        band = []
        for z in Z_TABLE:
            quantile = round4(
                bound_rate(from_logit(centre + z * spreads[index]))
            )
            if band and quantile < band[-1]:
                quantile = band[-1]
            band.append(quantile)
        rows.append({"id": trophy["id"], "q": band})
    return {"trophies": rows}


def _forecast_flat(history, checkpoint, horizon_minutes, n_trophies,
                   closing_rate, medians, spreads):
    """Unchanging history: the page is stale, so shade every rate down."""
    first_slot = read_instant(history[0]["slot"])
    stale_hours = max(0.0, gap_hours(first_slot, checkpoint))
    horizon_hours = horizon_minutes / 60.0
    decay = -1.9 * (1.0 - math.exp(-(stale_hours + horizon_hours) / 3.0))
    width = 0.30 + 0.10 * math.sqrt(max(0.0, horizon_minutes / 1440.0))
    shrink_factor = math.exp(decay)
    for index in range(n_trophies):
        rate = closing_rate(index)
        if rate >= SATURATED:
            medians[index] = SATURATED
            spreads[index] = SATURATED_SPREAD
        else:
            medians[index] = bound_rate(rate * shrink_factor)
            spreads[index] = width


def _forecast_trending(unique_reads, origin, checkpoint, target,
                       n_trophies, closing_rate, medians, spreads):
    """Moving history: shrunk log-time trends with a pooled slope."""
    # Log-hours since the origin for every distinct read, floored so
    # reads at (or before) the origin still land on the axis.
    axis = []
    grid = []  # grid[read][trophy] -> logit rate or None
    for rec in unique_reads:
        elapsed = gap_hours(origin, read_instant(rec["slot"]))
        axis.append(math.log(max(0.25, elapsed)))
        rates = rec.get("rates") or []
        row = []
        for index in range(n_trophies):
            value = rates[index] if index < len(rates) else None
            row.append(to_logit(value) if value is not None else None)
        grid.append(row)

    target_axis = math.log(max(0.25, gap_hours(origin, target)))

    # Keep only the last day of reads before the checkpoint; fall back
    # to the last eight reads when that window holds too few of them.
    checkpoint_axis_hours = max(0.25, gap_hours(origin, checkpoint))
    cutoff = math.log(max(0.25, checkpoint_axis_hours - 24.0))
    window = [j for j in range(len(unique_reads)) if axis[j] >= cutoff]
    if len(window) < 8:
        window = list(range(max(0, len(unique_reads) - 8), len(unique_reads)))
    window_centre = avg([axis[j] for j in window])

    def window_points(index):
        return [
            (axis[j], grid[j][index])
            for j in window
            if grid[j][index] is not None
        ]

    # One pooled slope: the median per-trophy trend over the window.
    gathered = []
    for index in range(n_trophies):
        points = window_points(index)
        if len(points) >= 3:
            gathered.append(limit_slope(least_squares_slope(points)))
    pooled = middle(gathered) if gathered else -0.3

    leftovers = []
    for index in range(n_trophies):
        points = window_points(index)
        if len(points) >= 3:
            own_slope = least_squares_slope(points)
            mean_y = avg([pt[1] for pt in points])
            mean_x = avg([pt[0] for pt in points])
        else:
            own_slope = pooled
            if points:
                mean_y = avg([pt[1] for pt in points])
                mean_x = avg([pt[0] for pt in points])
            else:
                mean_y = None
                mean_x = window_centre
        count = len(points)
        blended = (
            count * limit_slope(own_slope) + 10.0 * pooled
        ) / (count + 10.0)
        if mean_y is None:
            intercept = to_logit(closing_rate(index)) - blended * window_centre
        else:
            intercept = mean_y - blended * mean_x
            for px, py in points:
                leftovers.append(py - (intercept + blended * px))
        rate = closing_rate(index)
        if rate >= SATURATED:
            medians[index] = SATURATED
            spreads[index] = SATURATED_SPREAD
        else:
            anchor = to_logit(rate)
            projected = intercept + blended * target_axis
            if projected < anchor - 3.0:
                projected = anchor - 3.0
            elif projected > anchor + 3.0:
                projected = anchor + 3.0
            medians[index] = bound_rate(from_logit(projected))
            spreads[index] = None  # shared spread, assigned below

    if leftovers:
        resid_scale = math.sqrt(
            sum(v * v for v in leftovers) / max(1, len(leftovers))
        )
    else:
        resid_scale = 0.3
    shared = math.sqrt(resid_scale * resid_scale + 0.04) \
        + 0.15 * abs(target_axis - window_centre)
    shared = min(1.2, max(0.30, shared))
    for index in range(n_trophies):
        if spreads[index] is None:
            spreads[index] = shared


# ------------------------------------------------------------ entrypoint

def process_directory(source_dir, sink_dir):
    os.makedirs(sink_dir, exist_ok=True)
    for path in sorted(glob.glob(os.path.join(source_dir, "*.in"))):
        with open(path, "r", encoding="utf-8") as handle:
            case = json.load(handle)
        forecast = forecast_case(case)
        filename = os.path.basename(path)
        if filename.endswith(".in"):
            stem = filename[:-3]
        else:
            stem = os.path.splitext(filename)[0]
        destination = os.path.join(sink_dir, stem + ".out")
        with open(destination, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(forecast, separators=(",", ":")))
            handle.write("\n")


def main():
    source_dir = os.environ.get("MAKEFUN_INPUT_DIR", "/input")
    sink_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    process_directory(source_dir, sink_dir)


if __name__ == "__main__":
    main()
