"""Variance reduction: CUPED on `spend`, regression adjustment on the binaries.

    python -m src.variance_reduction

Both techniques are one idea -- use pre-treatment information to remove
variance that treatment cannot have caused. CUPED is the special case where
the covariate is the pre-period value of the outcome itself. PRE_REGISTRATION.md
section 5.1 reports them as one family applied to whichever metric each suits,
rather than as two separate accomplishments.

The scoping commitment from section 5.1, restated because it is the whole
point of separating these:

    CUPED is applied to `spend` only. The primary decision on `conversion`
    uses regression adjustment. Any sample-size saving reported in this
    project is attributed to the specific metric and estimator that produced
    it. The CUPED result does not bear on the power of the primary decision.

Section 5.1 also predicted, before any of this ran, that CUPED would deliver
under 0.1% variance reduction because rho(history, spend) = 0.0217. This
module tests that prediction rather than discovering the answer.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from src.db import get_engine
from src.inference import (
    CONTROL,
    TREATMENTS,
    Effect,
    lin_estimate,
    load_analysis_frame,
    two_proportion_z,
)

# --------------------------------------------------------------------------
# CUPED
# --------------------------------------------------------------------------

def cuped_theta(y: np.ndarray, x: np.ndarray) -> float:
    """theta = Cov(Y, X) / Var(X), estimated POOLED across arms.

    Pooling is what keeps the adjustment from becoming a back door for the
    treatment effect. A theta fitted within each arm separately would absorb
    part of the very difference being measured; pooled, theta is a function of
    pre-treatment information and the overall outcome distribution only.
    """
    return float(np.cov(y, x, ddof=1)[0, 1] / np.var(x, ddof=1))


def cuped_transform(y: np.ndarray, x: np.ndarray, theta: float,
                    x_mean: float) -> np.ndarray:
    """Y_cuped = Y - theta * (X - E[X]).

    Subtracting a mean-zero quantity leaves the expectation untouched, which
    is why CUPED reduces variance without introducing bias.
    """
    return y - theta * (x - x_mean)


def cuped_analysis(df: pd.DataFrame) -> dict:
    """CUPED on `spend` with X = `history`, per section 5."""
    y = df["spend"].to_numpy(dtype=float)
    x = df["history"].to_numpy(dtype=float)

    rho = float(np.corrcoef(y, x)[0, 1])
    theta = cuped_theta(y, x)
    y_cuped = cuped_transform(y, x, theta, float(x.mean()))

    var_before = float(np.var(y, ddof=1))
    var_after = float(np.var(y_cuped, ddof=1))
    realised = 1 - var_after / var_before

    # Unbiasedness check.
    #
    # The drift is not exactly zero, and that is expected rather than a
    # defect. CUPED is unbiased in expectation; in any single sample it also
    # removes the part of the raw difference attributable to CHANCE IMBALANCE
    # in X. The randomisation checks found `history` very slightly higher in
    # the Mens arm (SMD +0.0076), and theta > 0, so the adjustment shaves a
    # small amount off that arm's mean. The drift should therefore be tiny,
    # and signed against the direction of the covariate imbalance -- which is
    # exactly what a correction looks like, not what leakage looks like.
    effects = []
    y_c, yc_c = y[df.segment == CONTROL], y_cuped[df.segment == CONTROL]
    for arm in TREATMENTS:
        mask = (df.segment == arm).to_numpy()
        raw_ate = float(y[mask].mean() - y_c.mean())
        cuped_ate = float(y_cuped[mask].mean() - yc_c.mean())

        n_t, n_c = int(mask.sum()), len(y_c)
        se_raw = float(np.sqrt(np.var(y[mask], ddof=1) / n_t
                               + np.var(y_c, ddof=1) / n_c))
        se_cuped = float(np.sqrt(np.var(y_cuped[mask], ddof=1) / n_t
                                 + np.var(yc_c, ddof=1) / n_c))
        effects.append({
            "contrast": f"{arm} vs Control",
            "ate_raw": raw_ate,
            "ate_cuped": cuped_ate,
            "ate_drift": cuped_ate - raw_ate,
            "se_raw": se_raw,
            "se_cuped": se_cuped,
            "se_ratio": se_cuped / se_raw,
            "var_reduction": 1 - (se_cuped / se_raw) ** 2,
        })

    return {
        "rho": rho,
        "rho_squared": rho ** 2,
        "theta": theta,
        "var_before": var_before,
        "var_after": var_after,
        "realised_reduction": realised,
        "theory_predicts": rho ** 2,
        "effects": pd.DataFrame(effects),
    }


# --------------------------------------------------------------------------
# Regression adjustment on the binary outcomes
# --------------------------------------------------------------------------

def regression_adjustment_gain(df: pd.DataFrame, outcome: str) -> pd.DataFrame:
    """Variance reduction the Lin adjustment achieves, adjusted vs unadjusted.

    CUPED is awkward for a binary outcome -- there is no pre-period version of
    `conversion` in this dataset -- so section 5 pre-registered ANCOVA-style
    adjustment for these instead.
    """
    adj: list[Effect] = lin_estimate(df, outcome)
    unadj: list[Effect] = two_proportion_z(df, outcome)
    return pd.DataFrame([{
        "metric": outcome,
        "contrast": a.contrast,
        "se_unadjusted": u.se,
        "se_adjusted": a.se,
        "se_ratio": a.se / u.se,
        "var_reduction": 1 - (a.se / u.se) ** 2,
    } for a, u in zip(adj, unadj)])


# --------------------------------------------------------------------------
# Business translation
# --------------------------------------------------------------------------

def sample_size_saving(var_reduction: float, n_per_arm: int) -> dict:
    """For a FIXED MDE and power, how much smaller could the sample be?

    Required n scales linearly with the variance of the metric, so a variance
    reduction of r lets n shrink by exactly r. This is the number that
    justifies running CUPED at all: not "the interval got tighter" but "the
    next experiment can be smaller and still answer the question."
    """
    n_needed = n_per_arm * (1 - var_reduction)
    return {
        "var_reduction": var_reduction,
        "n_per_arm_now": n_per_arm,
        "n_per_arm_with_adjustment": n_needed,
        "customers_saved_per_arm": n_per_arm - n_needed,
        "customers_saved_total": (n_per_arm - n_needed) * 3,
    }


def rho_required_for(target_saving: float) -> float:
    """What correlation would a given sample-size saving require?

    Variance reduction is rho^2, so the answer is sqrt(target). This is the
    "when would CUPED pay off" half of the question -- without it, a near-zero
    result reads as a failed technique rather than as a technique correctly
    applied to a covariate that had nothing to offer.
    """
    return float(np.sqrt(target_saving))


def mde_effect(var_reduction: float) -> float:
    """MDE scales with the SD, so it shrinks by sqrt(1 - r) at fixed n."""
    return float(np.sqrt(1 - var_reduction))


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def main() -> int:
    engine = get_engine()
    df = load_analysis_frame(engine)
    n_per_arm = int(df.segment.value_counts().min())

    print("=" * 78)
    print("CUPED on `spend`   (X = `history`, theta pooled across arms)")
    print("=" * 78)
    c = cuped_analysis(df)
    print(f"  rho(history, spend)        {c['rho']:.6f}")
    print(f"  rho^2  (theoretical max)   {c['rho_squared']:.6f}  "
          f"= {c['rho_squared'] * 100:.4f}%")
    print(f"  theta                      {c['theta']:.6f}")
    print(f"  Var(spend) before          {c['var_before']:.4f}")
    print(f"  Var(spend) after CUPED     {c['var_after']:.4f}")
    print(f"  realised reduction         {c['realised_reduction'] * 100:.4f}%")
    print(f"  theory predicted           {c['theory_predicts'] * 100:.4f}%")
    print(f"  agreement                  "
          f"{abs(c['realised_reduction'] - c['theory_predicts']) * 100:.6f} pp apart")

    print("\n  Unbiasedness check -- the ATE must be unchanged, not merely close:")
    for _, r in c["effects"].iterrows():
        print(f"    {r.contrast:<26} raw ${r.ate_raw:+.6f}   "
              f"CUPED ${r.ate_cuped:+.6f}   drift ${r.ate_drift:+.2e}")

    print("\n  Per-contrast standard errors:")
    for _, r in c["effects"].iterrows():
        print(f"    {r.contrast:<26} SE ${r.se_raw:.6f} -> ${r.se_cuped:.6f}   "
              f"variance reduction {r.var_reduction * 100:+.4f}%")

    print("\n" + "=" * 78)
    print("REGRESSION ADJUSTMENT on the binary outcomes (section 5)")
    print("=" * 78)
    gains = pd.concat([regression_adjustment_gain(df, m)
                       for m in ("conversion", "visit")], ignore_index=True)
    print(f"  {'metric':<12}{'contrast':<26}{'SE ratio':>10}{'var reduction':>16}")
    for _, r in gains.iterrows():
        print(f"  {r.metric:<12}{r.contrast:<26}{r.se_ratio:>10.4f}"
              f"{r.var_reduction * 100:>15.4f}%")

    print("\n" + "=" * 78)
    print("BUSINESS TRANSLATION -- for a FIXED MDE, how much smaller a sample?")
    print("=" * 78)
    print("  Attribution matters here. Each row is the saving for THAT metric")
    print("  under THAT estimator. None of them transfers to the primary")
    print("  decision unless the row says `conversion`.\n")

    rows = [("spend", "CUPED", c["realised_reduction"])]
    for _, r in gains.iterrows():
        rows.append((r.metric, "Lin adjustment", r.var_reduction))

    print(f"  {'metric':<12}{'estimator':<18}{'var red.':>10}"
          f"{'n/arm needed':>14}{'saved/arm':>11}")
    for metric, est, red in rows:
        s = sample_size_saving(red, n_per_arm)
        print(f"  {metric:<12}{est:<18}{red * 100:>9.4f}%"
              f"{s['n_per_arm_with_adjustment']:>14,.0f}"
              f"{s['customers_saved_per_arm']:>11,.0f}")

    print("\n" + "=" * 78)
    print("WHEN WOULD CUPED ACTUALLY PAY?")
    print("=" * 78)
    print("  Variance reduction is rho^2, so the required correlation is sqrt(r).")
    print(f"  Observed here: rho = {c['rho']:.4f}\n")
    print(f"  {'to cut n by':>12}{'needs rho of':>15}{'vs observed':>14}")
    for target in (0.01, 0.05, 0.10, 0.25, 0.50):
        need = rho_required_for(target)
        print(f"  {target * 100:>11.0f}%{need:>15.4f}{need / c['rho']:>13.0f}x")

    print("\n  Even a 1% sample-size saving would need rho = 0.10, roughly five")
    print("  times the correlation this dataset has. Prior-year spend and a")
    print("  two-week spend window are close to unrelated, and no estimator")
    print("  recovers information that is not in the covariate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
