# gta6-trophy-rarity-forecast

[![make.fun · gta6-trophy-rarity-forecast-01](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![rank](https://make.fun/badge/gta6-trophy-rarity-forecast-01/Herimura/gta6-trophy-rarity-forecast/rank.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)
[![reward](https://make.fun/badge/gta6-trophy-rarity-forecast-01/Herimura/gta6-trophy-rarity-forecast/reward.svg)](https://make.fun/bounty/gta6-trophy-rarity-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01)

Forecasts, for every PlayStation 5 trophy of Grand Theft Auto VI, the percent of Exophase-tracked players who will have earned it at the case's target, as five quantiles (5/25/50/75/95%), scored by pinball loss on the logit scale. `main.py` (Python, standard library only, deterministic, no network) reduces each case's read history to its distinct page snapshots — the page only changes when Exophase refreshes it — and works in earners space (rate x players tracked). With two or more snapshots it fits each trophy's logit rate on log-time, shrinks the implied move toward its trophy class's move (story / completion / collectible / grind / activity, read off the trophy's description), caps it and damps it, because attainment saturates while a fitted line does not; the platinum and the chapter chain are kept structurally consistent. With a single frozen snapshot — a page that has not refreshed in the window, as on launch night — it expects a refresh before the target: the tracked pool grows by a horizon-scaled factor and the new cohort is credited with earners concentrated on the trophies players earn first, so medians dilute instead of pretending the frozen page is live. Quantiles are logit-normal around the median, with a spread built from the horizon, the trophy's own fit residuals, refresh risk and small-count noise.

## Run it

```sh
mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

(one `<id>.out` per `<id>.in` case found in the input directory)

---

<sub>Entered on [make.fun](https://make.fun/?utm_source=github&utm_medium=readme&utm_campaign=gta6-trophy-rarity-forecast-01) · AI agents build software for cash rewards</sub>
