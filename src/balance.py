"""Randomisation checks: sample ratio mismatch and covariate balance.

Everything here reads pre-treatment covariates and assignment counts only. No
outcome is touched, so randomisation can be verified while the outcome blind
is still fully intact.

    python -m src.balance
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import chisquare, norm
from sqlalchemy import Engine

from src.db import PROJECT_ROOT, get_engine

CONTROL = "No E-Mail"
TREATMENTS = ["Mens E-Mail", "Womens E-Mail"]

# Conventional threshold from the clinical-trials literature. |SMD| below 0.10
# is treated as negligible imbalance.
SMD_THRESHOLD = 0.10


def arm_counts(engine: Engine) -> pd.Series:
    df = pd.read_sql(
        "SELECT segment, COUNT(*) AS n FROM customers GROUP BY segment ORDER BY segment",
        engine,
    )
    return df.set_index("segment")["n"]


def srm_test(counts: pd.Series) -> dict:
    """Chi-square goodness-of-fit against an equal three-way split.

    A significant result means the assignment mechanism is suspect. The correct
    response is to stop and investigate it -- not to adjust for the imbalance.
    No covariate adjustment repairs a broken randomiser, because whatever
    corrupted the assignment may equally have corrupted the outcomes.
    """
    observed = counts.to_numpy(dtype=float)
    expected = np.full_like(observed, observed.sum() / len(observed))
    stat, p = chisquare(f_obs=observed, f_exp=expected)
    return {
        "observed": counts.to_dict(),
        "expected_per_arm": expected[0],
        "chi2": float(stat),
        "df": len(observed) - 1,
        "p_value": float(p),
        "srm_detected": bool(p < 0.001),  # conventional SRM alarm threshold
    }


def load_balance(engine: Engine) -> pd.DataFrame:
    sql = (PROJECT_ROOT / "sql" / "04_covariate_balance.sql").read_text()
    df = pd.read_sql(sql, engine)
    for col in ("mean", "var", "n"):
        df[col] = df[col].astype(float)
    return df


def standardised_mean_differences(balance: pd.DataFrame) -> pd.DataFrame:
    """SMD of each treatment arm against control, per covariate level.

    SMD = (mean_t - mean_c) / sqrt((var_t + var_c) / 2)

    The pooled-SD denominator makes the statistic unit-free, which is the whole
    point: it puts `history` in dollars and `newbie` as a proportion on the
    same axis so a single plot can show them together.
    """
    wide = balance.pivot_table(
        index=["covariate", "level", "kind"],
        columns="segment",
        values=["mean", "var", "n"],
    )

    rows = []
    for (covariate, level, kind), r in wide.iterrows():
        m_c, v_c = r[("mean", CONTROL)], r[("var", CONTROL)]
        for arm in TREATMENTS:
            m_t, v_t = r[("mean", arm)], r[("var", arm)]
            pooled = np.sqrt((v_t + v_c) / 2)
            smd = (m_t - m_c) / pooled if pooled > 0 else 0.0

            # Nominal two-sample z on the difference in means. Reported only to
            # make the multiplicity point in the write-up -- see the note in
            # main(). It is not the primary balance statistic.
            se = np.sqrt(v_t / r[("n", arm)] + v_c / r[("n", CONTROL)])
            z = (m_t - m_c) / se if se > 0 else 0.0
            rows.append(
                {
                    "covariate": covariate,
                    "level": level,
                    "kind": kind,
                    "contrast": f"{arm} vs Control",
                    "mean_treatment": m_t,
                    "mean_control": m_c,
                    "smd": smd,
                    "abs_smd": abs(smd),
                    "nominal_p": 2 * (1 - norm.cdf(abs(z))),
                    "flagged": abs(smd) > SMD_THRESHOLD,
                }
            )

    return pd.DataFrame(rows).sort_values(["covariate", "level", "contrast"])


def smd_noise_calibration(smd: pd.DataFrame, n_per_arm: int) -> dict:
    """Is the spread of observed SMDs what randomisation predicts?

    This is the check that actually has teeth here, and the 0.10 rule of thumb
    does not. For two arms of size n, SE(SMD) is about sqrt(2/n). With n around
    21,300 that is roughly 0.0097 -- so the conventional 0.10 threshold sits
    more than ten standard errors out and could essentially never fire, no
    matter how the randomisation behaved. Reporting "no covariate exceeded
    0.10" would therefore be reporting nothing.

    The informative question is whether the SMDs scatter like noise of the
    predicted size. Note the observed spread is expected to run slightly
    *below* theory: one-hot levels within a categorical sum to one, so their
    SMDs are negatively correlated and the 36 comparisons are not independent.
    """
    theoretical_se = np.sqrt(2 / n_per_arm)
    observed_sd = float(smd.smd.std(ddof=1))
    max_abs = float(smd.abs_smd.max())
    return {
        "theoretical_se": theoretical_se,
        "observed_sd": observed_sd,
        "ratio": observed_sd / theoretical_se,
        "max_abs_smd": max_abs,
        "max_in_se_units": max_abs / theoretical_se,
        "threshold_in_se_units": SMD_THRESHOLD / theoretical_se,
    }


def main() -> int:
    engine = get_engine()

    print("=" * 78)
    print("SAMPLE RATIO MISMATCH")
    print("=" * 78)
    counts = arm_counts(engine)
    srm = srm_test(counts)
    for arm, n in srm["observed"].items():
        delta = n - srm["expected_per_arm"]
        print(f"  {arm:<16}{n:>8,}   ({delta:+.1f} vs equal split)")
    print(f"\n  chi2({srm['df']}) = {srm['chi2']:.4f},  p = {srm['p_value']:.4f}")
    print(f"  SRM detected: {srm['srm_detected']}")

    print("\n" + "=" * 78)
    print("COVARIATE BALANCE (SMD vs control)")
    print("=" * 78)
    smd = standardised_mean_differences(load_balance(engine))

    print(f"  {'covariate':<16}{'level':<18}{'contrast':<24}{'SMD':>9}{'':>3}")
    for _, r in smd.iterrows():
        mark = "  <-- FLAG" if r.flagged else ""
        print(f"  {r.covariate:<16}{r.level:<18}{r.contrast:<24}{r.smd:>+9.4f}{mark}")

    n_comparisons = len(smd)
    n_flagged = int(smd.flagged.sum())
    n_nominal_sig = int((smd.nominal_p < 0.05).sum())

    print("\n" + "-" * 78)
    print(f"  comparisons              {n_comparisons}")
    print(f"  |SMD| > {SMD_THRESHOLD}             {n_flagged}")
    print(f"  largest |SMD|            {smd.abs_smd.max():.4f} "
          f"({smd.loc[smd.abs_smd.idxmax(), 'covariate']}/"
          f"{smd.loc[smd.abs_smd.idxmax(), 'level']})")
    print(f"  nominal p < 0.05         {n_nominal_sig} "
          f"(expected by chance alone: {0.05 * n_comparisons:.1f})")
    cal = smd_noise_calibration(smd, int(counts.min()))
    print("-" * 78)
    print("  NOISE CALIBRATION -- the check that actually has teeth here")
    print(f"    SE(SMD) predicted by randomisation   {cal['theoretical_se']:.4f}")
    print(f"    observed SD of the {n_comparisons} SMDs           "
          f"{cal['observed_sd']:.4f}  ({cal['ratio']:.2f}x predicted)")
    print(f"    largest |SMD| in SE units            "
          f"{cal['max_in_se_units']:.2f} SE")
    print(f"    the 0.10 threshold, in SE units      "
          f"{cal['threshold_in_se_units']:.1f} SE  <-- unreachable by design")
    print("-" * 78)
    print(
        "  Two notes on how to read the table above.\n\n"
        "  1. SMD is the primary statistic, not the p-value. Under true\n"
        "     randomisation the null of balance is known to be true, so a\n"
        "     balance p-value tests nothing -- with enough rows any trivial\n"
        "     difference becomes 'significant'. The p-value column exists only\n"
        f"     to show that {n_nominal_sig} nominally significant result(s) across "
        f"{n_comparisons}\n     comparisons is what chance produces.\n\n"
        "  2. Zero covariates exceeding |SMD| > 0.10 is NOT impressive on its\n"
        "     own, and should not be reported as though it were. At this sample\n"
        f"     size that threshold is {cal['threshold_in_se_units']:.0f} standard errors away and could not\n"
        "     realistically fire. The meaningful finding is that the SMDs\n"
        "     scatter like noise of exactly the predicted size."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
