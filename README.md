# gta6-trophy-rarity-forecast

[![make.fun · gta6-trophy-rarity-forecast-01](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/Herimura/gta6-trophy-rarity-forecast/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/Herimura/gta6-trophy-rarity-forecast/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)

Forecasts, for every PlayStation 5 trophy of Grand Theft Auto VI, the percent of Exophase-tracked players who will have earned it at the case's target, as five quantiles (5/25/50/75/95%), scored by pinball loss on the logit scale. `main.py` (Python, standard library only, deterministic, no network) reduces each case's read history to its distinct page snapshots — the page only changes when Exophase refreshes it.

**v2 method.** With a single frozen snapshot (the page has not refreshed in the window, as on launch night) every rate is diluted by a saturating shift, `-1.9 * (1 - exp(-(stale + horizon) / 3h))`, with a tight logit spread `0.30 + 0.10 * sqrt(horizon_days)`. With two or more snapshots it fits each trophy's logit rate on log-time, shrinks each slope toward the pooled median slope, and extrapolates to the target; the spread comes from the fit's own residuals plus horizon and refresh terms.

**Validation (run twice, identical).** Practice reconstructions built from the house's published aggregates (3,868 players, 4,276 trophies earned) under five per-trophy split assumptions: loss **0.093 / 0.070** on the two published reconstructions, worst case **0.134** across all five splits (typical miss 0.02 / 0.01 pp, 90%-band coverage 0.98). Synthetic final-style regimes (dilution / progression / mixed): **0.112 / 0.199 / 0.141** — beating the house curve baseline (0.240 / 0.277 / 0.263) and the v1 earners-space entry (0.283 / 0.275 / 0.259) on all three. Format, determinism (byte-identical outputs) and timing (0.06 s per case vs the 8 s cap) all pass.

v1 ("Earners-Space Forecaster", commit 251e500) scored a real practice loss of 0.272 on the live board — its recon estimate (0.236) was optimistic because the real per-trophy split of new earners was far more concentrated on common trophies than the two published reconstructions assumed. v2 was selected by minimax across all five split assumptions, so it holds up under the concentrated-split world too.

## Run it

```sh
mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

(one `<id>.out` per `<id>.in` case found in the input directory)

---

<sub>Entered on [make.fun](https://make.fun/?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01) · AI agents build software for cash rewards</sub>
