"""Power analysis and minimum detectable effects.

This module reproduces the numbers published in PRE_REGISTRATION.md section 8.
It is committed *after* the pre-registration, which is deliberate and safe: it
adds no new commitment, it only makes an existing one reproducible. Every
input it reads is pooled across arms, so running it reveals no treatment
contrast.

    python -m src.power

If any figure here disagrees with the pre-registration, the pre-registration
does NOT get edited. The discrepancy is reported and recorded as a numbered
amendment in its section 11.3. Quietly correcting a pre-registered number is
the exact failure this repository exists to rule out.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import norm
from sqlalchemy import Engine, text

from src.db import get_engine

ALPHA = 0.05
POWER = 0.80


def _k(alpha: float = ALPHA, power: float = POWER) -> float:
    """z_{alpha/2} + z_{power}, the constant in every formula below."""
    return norm.ppf(1 - alpha / 2) + norm.ppf(power)


def mde_proportion(
    p: float, n_per_arm: int, alpha: float = ALPHA, power: float = POWER
) -> float:
    """Minimum detectable difference between two proportions, in percentage
    points, for a two-sided test with equally sized arms."""
    return _k(alpha, power) * np.sqrt(2 * p * (1 - p) / n_per_arm) * 100


def mde_mean(
    sd: float, n_per_arm: int, alpha: float = ALPHA, power: float = POWER
) -> float:
    """Minimum detectable difference between two means, in metric units."""
    return _k(alpha, power) * sd * np.sqrt(2 / n_per_arm)


def n_required_proportion(
    p: float, mde_pp: float, alpha: float = ALPHA, power: float = POWER
) -> float:
    """Per-arm N needed to detect `mde_pp` percentage points at `power`."""
    return 2 * p * (1 - p) * (_k(alpha, power) / (mde_pp / 100)) ** 2


@dataclass(frozen=True)
class PooledInputs:
    """Outcome moments pooled across all three arms.

    Pooling is what makes these safe to compute before the primary analysis: a
    pooled moment is invariant to how the arms differ, so it cannot expose a
    treatment effect. See PRE_REGISTRATION.md section 1.2.
    """

    n_total: int
    n_per_arm: int
    p_visit: float
    p_conversion: float
    mean_spend: float
    sd_spend: float


def load_pooled_inputs(engine: Engine) -> PooledInputs:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT COUNT(*)            AS n_total,
                       AVG(visit)          AS p_visit,
                       AVG(conversion)     AS p_conversion,
                       AVG(spend)          AS mean_spend,
                       STDDEV_SAMP(spend)  AS sd_spend
                FROM customers
                """
            )
        ).one()
        # Smallest arm, so every MDE quoted is the conservative one.
        n_per_arm = conn.execute(
            text("SELECT MIN(n) FROM (SELECT COUNT(*) n FROM customers GROUP BY segment) t")
        ).scalar_one()

    return PooledInputs(
        n_total=int(row.n_total),
        n_per_arm=int(n_per_arm),
        p_visit=float(row.p_visit),
        p_conversion=float(row.p_conversion),
        mean_spend=float(row.mean_spend),
        sd_spend=float(row.sd_spend),
    )


def overall_mde_table(inp: PooledInputs) -> pd.DataFrame:
    """PRE_REGISTRATION.md section 8.1."""
    rows = [
        ("conversion", mde_proportion(inp.p_conversion, inp.n_per_arm), "pp",
         inp.p_conversion * 100),
        ("visit", mde_proportion(inp.p_visit, inp.n_per_arm), "pp", inp.p_visit * 100),
        ("spend", mde_mean(inp.sd_spend, inp.n_per_arm), "$", inp.mean_spend),
    ]
    df = pd.DataFrame(rows, columns=["metric", "mde", "unit", "base"])
    df["mde_relative_pct"] = df.mde / df.base * 100
    return df


# Subgroup definitions, fixed by PRE_REGISTRATION.md section 7. Four families,
# eleven subgroups. history_segment is deliberately excluded -- see that
# section for why.
SUBGROUP_QUERIES: dict[str, str] = {
    "prior purchase": """
        SELECT CASE WHEN mens = 1 AND womens = 1 THEN 'both'
                    WHEN mens = 1                THEN 'mens only'
                    ELSE                              'womens only' END AS level,
               COUNT(*) AS n
        FROM customers GROUP BY 1
    """,
    "newbie": """
        SELECT CASE WHEN newbie = 1 THEN 'newbie' ELSE 'established' END AS level,
               COUNT(*) AS n
        FROM customers GROUP BY 1
    """,
    "channel": "SELECT channel AS level, COUNT(*) AS n FROM customers GROUP BY 1",
    "recency band": """
        SELECT CASE WHEN recency <= 3 THEN '1-3 months'
                    WHEN recency <= 6 THEN '4-6 months'
                    ELSE                   '7-12 months' END AS level,
               COUNT(*) AS n
        FROM customers GROUP BY 1
    """,
}


def subgroup_mde_table(engine: Engine, inp: PooledInputs) -> pd.DataFrame:
    """PRE_REGISTRATION.md section 8.3.

    Each subgroup's arm size is n/3, so its MDE is strictly larger than the
    overall MDE. Quantifying that in advance is what lets an underpowered null
    be reported as "inconclusive" rather than "no effect".
    """
    overall = mde_proportion(inp.p_conversion, inp.n_per_arm)
    frames = []
    for family, query in SUBGROUP_QUERIES.items():
        df = pd.read_sql(text(query), engine).sort_values("level")
        df.insert(0, "family", family)
        df["n_per_arm"] = df.n // 3
        df["mde_pp"] = df.n_per_arm.apply(
            lambda n: mde_proportion(inp.p_conversion, int(n))
        )
        df["vs_overall"] = df.mde_pp / overall
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


# Values as published in PRE_REGISTRATION.md section 8, with the tolerance
# implied by the precision each was printed at.
PREREGISTERED = {
    "conversion MDE (pp)": (0.257, 0.001),
    "visit MDE (pp)": (0.961, 0.001),
    "spend MDE ($)": (0.408, 0.001),
    "N per arm for 0.25pp": (22478, 1),
}


def verify_against_preregistration(inp: PooledInputs) -> list[str]:
    """Return a list of discrepancies. Empty means the module reproduces the
    pre-registered figures."""
    actual = {
        "conversion MDE (pp)": mde_proportion(inp.p_conversion, inp.n_per_arm),
        "visit MDE (pp)": mde_proportion(inp.p_visit, inp.n_per_arm),
        "spend MDE ($)": mde_mean(inp.sd_spend, inp.n_per_arm),
        "N per arm for 0.25pp": n_required_proportion(inp.p_conversion, 0.25),
    }
    problems = []
    for name, (expected, tol) in PREREGISTERED.items():
        got = actual[name]
        if abs(got - expected) > tol:
            problems.append(
                f"{name}: pre-registered {expected}, computed {got:.6f} "
                f"(tolerance {tol})"
            )
    return problems


def main() -> int:
    engine = get_engine()
    inp = load_pooled_inputs(engine)

    print("=" * 72)
    print("POOLED INPUTS (no arm-level outcome is read)")
    print("=" * 72)
    print(f"  N               {inp.n_total:,}  ({inp.n_per_arm:,} per arm, smallest)")
    print(f"  visit rate      {inp.p_visit:.6f}")
    print(f"  conversion rate {inp.p_conversion:.6f}")
    print(f"  mean spend      ${inp.mean_spend:.4f}")
    print(f"  sd spend        ${inp.sd_spend:.4f}")

    print(f"\nOVERALL MDE  (alpha={ALPHA}, power={POWER:.0%}, two-sided, per contrast)")
    overall = overall_mde_table(inp)
    for _, r in overall.iterrows():
        unit = f"{r.mde:.4f} pp" if r.unit == "pp" else f"${r.mde:.4f}"
        print(f"  {r.metric:<12}{unit:>14}   ({r.mde_relative_pct:.1f}% relative)")

    print("\nSAMPLE SIZE REQUIRED on conversion")
    for target in (0.25, 0.50, 1.00):
        need = n_required_proportion(inp.p_conversion, target)
        verdict = "UNDERPOWERED" if need > inp.n_per_arm else "ok"
        print(f"  {target:.2f} pp lift -> {need:>9,.0f} per arm   "
              f"(have {inp.n_per_arm:,}, {verdict})")

    print("\nPER-SUBGROUP MDE on conversion")
    sub = subgroup_mde_table(engine, inp)
    print(f"  {'family':<16}{'level':<14}{'n':>8}{'n/arm':>8}{'MDE pp':>10}{'vs all':>9}")
    for _, r in sub.iterrows():
        print(f"  {r.family:<16}{r.level:<14}{r.n:>8,}{r.n_per_arm:>8,}"
              f"{r.mde_pp:>10.4f}{r.vs_overall:>8.2f}x")

    n_tests = len(sub) * 2
    print(f"\n  subgroup family: {len(sub)} subgroups x 2 contrasts = {n_tests} tests")

    print("\n" + "=" * 72)
    problems = verify_against_preregistration(inp)
    if problems:
        print("MISMATCH vs PRE_REGISTRATION.md section 8:")
        for p in problems:
            print(f"  - {p}")
        print("\nDo NOT edit the pre-registration. Record an amendment in 11.3.")
        return 1
    print("Reproduces PRE_REGISTRATION.md section 8 exactly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
