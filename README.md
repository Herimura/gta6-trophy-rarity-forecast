# GTA VI Trophy Rarity Forecast

![Bounty badge](https://make.fun/badge/gta6-trophy-rarity-forecast-01.svg)

Bounty: [gta6-trophy-rarity-forecast-01](https://make.fun/bounty/gta6-trophy-rarity-forecast-01)

## What this forecasts

For every PlayStation 5 trophy of Grand Theft Auto VI, this program
predicts the earn rate the trophy will show on Exophase at the case
target — the percentage of tracked players who have earned it — and
reports that prediction as five quantiles (5%, 25%, 50%, 75% and 95%)
per trophy. It reads each case file (`*.in`) from `$MAKEFUN_INPUT_DIR`
(default `/input`) and writes one compact JSON forecast (`<stem>.out`)
per case into `$MAKEFUN_OUTPUT_DIR` (default `/output`). The program
is Python 3, standard library only, deterministic and fully offline.

## Method (v2)

All modelling happens on the logit scale, where a move from 1% to 2%
counts about as much as a move from 33% to 50%. Two regimes are
handled separately:

- **Flat history.** If the recorded reads never change (consecutive
  duplicate reads collapse into one), the page is treated as stale:
  a refresh is expected to dilute the rates, so each last rate is
  scaled down in rate space by a factor that deepens with how long
  the history has been frozen plus the forecast horizon, saturating
  at a shift of −1.9. The spread starts at 0.30 on the logit scale
  and widens slowly with the horizon. A trophy already at 99.99%
  stays pinned there with a narrow spread.
- **Moving history.** Rates are regressed against the logarithm of
  hours since release (release-day midnight, or the first read when
  no release date is given), using only the last 24 hours of distinct
  reads (or the last eight reads when that window is thinner). Each
  trophy's own least-squares slope is shrunk towards a pooled slope —
  the median slope across all trophies with enough points — with the
  trophy's point count weighed against ten pooled pseudo-points, and
  slopes are capped at ±6. The projection to the target is never
  allowed to stray more than 3 logit units from the last observed
  rate. The shared spread combines the fit residuals with a term that
  grows with the distance from the window's centre to the target,
  floored at 0.30 and capped at 1.2; saturated trophies keep their
  narrow 0.15 spread instead.

Quantiles are formed by shifting the median's logit by the standard
normal quantiles (−1.645, −0.674, 0, 0.674, 1.645) times the spread,
converting back to percentages, rounding half-up to four decimals,
and enforcing a non-decreasing sequence.

## Validation

Reconstructed checks of this method, scored by mean pinball loss on
the logit scale (lower is better):

- Practice reconstructions of the two published cases: **0.093** and
  **0.070**.
- Worst case across five split assumptions: **0.134**.
- Synthetic regimes: **0.112 / 0.199 / 0.141**, against the house
  curve baseline's 0.240 / 0.277 / 0.263 and the earlier v1 entry's
  0.283 / 0.275 / 0.259 on the same regimes.
- The v1 entry scored a real **0.272** on the live board.
