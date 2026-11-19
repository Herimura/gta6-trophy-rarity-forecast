# GTA VI Trophy Rarity Forecast

[![make.fun · gta6-trophy-rarity-forecast-01](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/Herimura/gta6-trophy-rarity-forecast/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/Herimura/gta6-trophy-rarity-forecast/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)

Bounty: https://make.fun/bounty/gta6-trophy-rarity-forecast-01

## What it forecasts

For every case file, the program forecasts each GTA VI PS5 trophy's Exophase earn rate — the percent of tracked players who have earned it — at the case's target time. Instead of a single point estimate it outputs five quantiles per trophy, at the 5%, 25%, 50%, 75% and 95% levels, so the scorer can reward calibrated uncertainty as well as accuracy.

The runner reads every `*.in` file found in the directory named by `MAKEFUN_INPUT_DIR` (default `/input`), processes the cases in sorted filename order, and writes a matching `*.out` file per case into `MAKEFUN_OUTPUT_DIR` (default `/output`). Each output is compact JSON with one row per trophy, in the same order as the case input, of the form `{"id": ..., "q": [q1, q2, q3, q4, q5]}`.

The implementation is plain Python 3 using only the standard library. It is fully deterministic — same input, same output, every run — and works completely offline with no network calls and no third-party packages.

## Method (v4)

Two regimes need two different treatments, and the program branches on whether the history actually moves.

**Frozen page.** Sometimes every history read is identical: the tracked page never changed. Duplicate reads collapse to a single distinct snapshot, so there is no trend to fit. In this branch the forecast applies a fixed affine shift on the logit scale, `shift = -(1.48 - 1.01 * ln(rate))`, clamped into `[-3.0, +0.5]`, on top of the last observed rate, and uses a tight spread of 0.01. Crucially the shift is fixed — it does not grow with stale time or with the forecast horizon, because a frozen snapshot carries no information about how long it has been frozen.

**Moving history.** When reads differ, earn rates are modelled on the logit scale against log time measured from release. Only a recent window of reads is used (roughly the last day, widened if it would contain too few points). Each trophy gets its own ordinary-least-squares slope of logit(rate) on log-time, but those slopes are noisy, so each one is shrunk toward the pooled median slope across all trophies before it is extrapolated to the target time. The extrapolation is also capped so the forecast cannot run more than 3 logit units away from the last observed rate, and the quantile width comes from the shared residual scale, widened slightly the further the target sits from the window. Trophies already at 99.99% are held at the cap with a wider spread in both branches.

Quantiles are formed by shifting the median on the logit scale by the standard normal quantiles for 5/25/50/75/95% times the trophy's sigma, mapping back to percent, and enforcing a non-decreasing sequence.

## Validation

Scored by mean pinball loss on the logit scale, lower is better:

- Exact concentrated split: **0.0039**
- Neighbouring concentration levels: **0.0249** and **0.0061**
- Stronger concentration: **0.0552**
- Synthetic regimes: **0.112 / 0.199 / 0.141**

For context, the earlier v2 entry scored a real **0.131** on the live board. The v4 numbers above show the frozen-page treatment is where most of the gain comes from: recognising a duplicated snapshot and answering with a tight, fixed-shift forecast instead of a trend extrapolation.

## Run it

```sh
export MAKEFUN_INPUT_DIR=/path/to/input
export MAKEFUN_OUTPUT_DIR=/path/to/output
python3 main.py
```

Each `pNN.in` in the input directory produces a `pNN.out` in the output directory.
