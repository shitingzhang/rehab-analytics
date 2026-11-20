# Sensitivity of Medicare payment estimates to the choice of model

Model A compared standardized Medicare payment per stay across 938 facilities in 51 state-level groups. Its payment residuals had a long right tail and greater spread at high fitted values. This post-results sensitivity analysis uses the same positive payments, facilities, state fixed effects and adjustment variables. A Gamma model with a log link allows proportional changes in mean payment. [Original residual diagnostics](model_a_diagnostics.md)

## Adjusted difference in dollars per stay

| Higher share of recorded therapy minutes | Original OLS [95% CI] | Gamma with log link [95% CI] |
|---|---:|---:|
| Group/concurrent (+10 pp) | -23 [-353, +306] | -17 [-324, +291] |
| Co-treatment (+5 pp) | +41 [-444, +525] | +34 [-430, +499] |
| PT share (+5 pp) | +89 [-459, +637] | +83 [-448, +614] |
| SLP share (+5 pp) | +246 [-256, +748] | +259 [-211, +729] |

Each row raises the named therapy share by the stated number of percentage points (pp), holds the other model columns fixed and averages the predicted dollar difference equally across the included facilities. For PT and SLP, occupational therapy is the reference discipline. These modeled contrasts can extend beyond observed exposure combinations and describe facility-level associations.

Intervals use state-clustered sandwich covariance with the CR1 correction. Gamma dollar contrasts use the delta method conditional on the observed facility profiles and a t critical value with 50 degrees of freedom. All four intervals are pointwise. The original BH correction covers the primary Model A family only.

## Reproduction

From the repository root run `python src/payment_gamma_sensitivity.py` after building Model A. The Gamma fit converged in 15 iterations. The script checks the OLS contrasts against the saved primary table, the Gamma covariance against the clustered score calculation and the dollar contrasts and gradients against a separate log-link calculation.
