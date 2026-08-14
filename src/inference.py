"""Primary analysis: treatment effects on the pre-registered metrics.

    python -m src.inference

This module implements the estimator table in PRE_REGISTRATION.md section 5
and nothing else. No estimator is selected, no threshold adjusted and no
specification tuned after seeing an estimate. Where a choice of implementation
existed, the reasoning is recorded in a comment rather than left to inference
from the code.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import norm
from sqlalchemy import Engine
from statsmodels.stats.multitest import multipletests

from src.db import PROJECT_ROOT, get_engine

CONTROL = "No E-Mail"
TREATMENTS = ["Mens E-Mail", "Womens E-Mail"]

# PRE_REGISTRATION.md section 5, fixed. Continuous covariates are centred;
# categoricals are dummy-coded then centred.
CONTINUOUS = ["recency", "history"]
BINARY = ["mens", "womens", "newbie"]
CATEGORICAL = ["zip_code", "channel", "history_segment"]

# Section 6. Read from the pre-registration, never recomputed at analysis time.
WINSOR_CAP = 243.66

# Section 9. Two families, corrected separately -- never pooled into one call.
Q_PRIMARY = 0.05
Q_SECONDARY = 0.10

# Section 10.
DECISION_THRESHOLD_PP = 0.30

N_BOOTSTRAP = 10_000
SEED = 20080320  # the date the dataset was published; any fixed value would do


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

def load_analysis_frame(engine: Engine) -> pd.DataFrame:
    """Row-level pull.

    The primary estimator needs rows, not group means: no amount of SQL
    aggregation can produce a regression with treatment x covariate
    interactions. sql/05_primary_metrics.sql provides the descriptive table
    and the unadjusted sensitivity inputs; this provides the primary.
    """
    return pd.read_sql(
        """
        SELECT segment, visit, conversion, spend,
               recency, history, mens, womens, newbie,
               zip_code, channel, history_segment
        FROM customers
        """,
        engine,
    )


def load_arm_metrics(engine: Engine) -> pd.DataFrame:
    """The aggregate table, used to cross-check the row-level path."""
    sql = (PROJECT_ROOT / "sql" / "05_primary_metrics.sql").read_text()
    # psql's \set is a client-side directive the driver does not understand.
    sql = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("\\"))
    sql = sql.replace(":winsor_cap", str(WINSOR_CAP))
    return pd.read_sql(sql, engine).set_index("segment")


# --------------------------------------------------------------------------
# Primary estimator: Lin-adjusted OLS
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Effect:
    contrast: str
    metric: str
    estimator: str
    effect: float           # absolute difference, metric units
    se: float
    ci_low: float
    ci_high: float
    p_value: float
    control_mean: float

    @property
    def effect_pp(self) -> float:
        return self.effect * 100

    @property
    def relative_pct(self) -> float:
        return self.effect / self.control_mean * 100 if self.control_mean else float("nan")


def _design(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Mean-centred covariate matrix, per section 5.

    Centring is over the full analysis population (all 64,000, per section 3),
    which is what makes the treatment coefficient the average treatment effect
    rather than an effect at some arbitrary covariate value.
    """
    parts = [df[CONTINUOUS + BINARY].astype(float)]
    for col in CATEGORICAL:
        parts.append(pd.get_dummies(df[col], prefix=col, drop_first=True).astype(float))
    X = pd.concat(parts, axis=1)
    return X - X.mean(), list(X.columns)


def lin_estimate(df: pd.DataFrame, outcome: str) -> list[Effect]:
    """Lin (2013) covariate-adjusted OLS with HC2 robust standard errors.

    Specification, fixed by section 5: outcome on treatment dummies, all eight
    mean-centred covariates, and treatment x covariate interactions.

    Three-arm implementation note. Section 5 says "treatment dummies", plural,
    so this fits ONE model containing both dummies and both interaction sets,
    with covariates centred over all 64,000. Fitting two separate two-arm
    models (each on its own subsample, centred within it) was considered and
    rejected: it is a defensible estimator, but it is not the one the
    pre-registration describes, and swapping it in after the fact is the exact
    move this project exists to rule out.

    With covariates centred, the coefficient on each treatment dummy IS that
    arm's average treatment effect against control.
    """
    Xc, cov_names = _design(df)

    design = pd.DataFrame(index=df.index)
    treat_cols = []
    for arm in TREATMENTS:
        col = f"T[{arm}]"
        design[col] = (df.segment == arm).astype(float)
        treat_cols.append(col)

    design = pd.concat([design, Xc], axis=1)
    for col in treat_cols:
        for cov in cov_names:
            design[f"{col}:{cov}"] = design[col] * Xc[cov]

    model = sm.OLS(df[outcome].astype(float), sm.add_constant(design))
    fit = model.fit(cov_type="HC2")

    control_mean = float(df.loc[df.segment == CONTROL, outcome].mean())
    out = []
    for arm, col in zip(TREATMENTS, treat_cols):
        ci = fit.conf_int().loc[col]
        out.append(
            Effect(
                contrast=f"{arm} vs Control",
                metric=outcome,
                estimator="Lin-adjusted OLS (HC2)",
                effect=float(fit.params[col]),
                se=float(fit.bse[col]),
                ci_low=float(ci.iloc[0]),
                ci_high=float(ci.iloc[1]),
                p_value=float(fit.pvalues[col]),
                control_mean=control_mean,
            )
        )
    return out


# --------------------------------------------------------------------------
# Sensitivity: unadjusted
# --------------------------------------------------------------------------

def two_proportion_z(df: pd.DataFrame, outcome: str) -> list[Effect]:
    """Unadjusted two-proportion z-test. Cross-check only.

    Section 5: material disagreement with the adjusted estimate is reported as
    a finding and CANNOT override the primary.
    """
    p_c = df.loc[df.segment == CONTROL, outcome].mean()
    n_c = int((df.segment == CONTROL).sum())

    out = []
    for arm in TREATMENTS:
        p_t = df.loc[df.segment == arm, outcome].mean()
        n_t = int((df.segment == arm).sum())
        diff = p_t - p_c
        # Unpooled SE for the interval; pooled SE for the test statistic, which
        # is the standard pairing.
        se = np.sqrt(p_t * (1 - p_t) / n_t + p_c * (1 - p_c) / n_c)
        p_pool = (p_t * n_t + p_c * n_c) / (n_t + n_c)
        se_pool = np.sqrt(p_pool * (1 - p_pool) * (1 / n_t + 1 / n_c))
        z = diff / se_pool
        out.append(
            Effect(
                contrast=f"{arm} vs Control",
                metric=outcome,
                estimator="Unadjusted two-proportion z",
                effect=float(diff),
                se=float(se),
                ci_low=float(diff - 1.96 * se),
                ci_high=float(diff + 1.96 * se),
                p_value=float(2 * (1 - norm.cdf(abs(z)))),
                control_mean=float(p_c),
            )
        )
    return out


def _bootstrap_means(values: np.ndarray, rng: np.random.Generator,
                     n_boot: int = N_BOOTSTRAP, chunk: int = 500) -> np.ndarray:
    """Bootstrap distribution of the mean, resampling with replacement.

    Chunked because the full index matrix (n_boot x n) would be several
    gigabytes at this sample size.
    """
    n = len(values)
    means = np.empty(n_boot)
    for start in range(0, n_boot, chunk):
        size = min(chunk, n_boot - start)
        idx = rng.integers(0, n, size=(size, n))
        means[start:start + size] = values[idx].mean(axis=1)
    return means


def bootstrap_diff(df: pd.DataFrame, outcome: str, arm: str,
                   seed: int = SEED, transform=None) -> dict:
    """Stratified bootstrap of the difference in means, arm vs control.

    Stratified by arm: each arm is resampled to its own observed size, which
    is what the randomisation actually fixed. Resampling the pooled sample
    would let arm sizes vary and estimate the wrong quantity.
    """
    rng = np.random.default_rng(seed)
    y_t = df.loc[df.segment == arm, outcome].to_numpy(dtype=float)
    y_c = df.loc[df.segment == CONTROL, outcome].to_numpy(dtype=float)
    if transform is not None:
        y_t, y_c = transform(y_t), transform(y_c)

    diffs = _bootstrap_means(y_t, rng) - _bootstrap_means(y_c, rng)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return {
        "contrast": f"{arm} vs Control",
        "metric": outcome,
        "effect": float(y_t.mean() - y_c.mean()),
        "ci_low": float(lo),
        "ci_high": float(hi),
        "draws": diffs,
        "control_mean": float(y_c.mean()),
    }


# --------------------------------------------------------------------------
# Multiplicity
# --------------------------------------------------------------------------

def bh_correct(p_values, q: float) -> tuple[np.ndarray, np.ndarray]:
    """Benjamini-Hochberg within ONE family. Returns (reject, adjusted p).

    Section 9 defines two families with different q. They are corrected by
    separate calls; pooling them into a single correction would change both
    answers and is not what was pre-registered.
    """
    reject, p_adj, _, _ = multipletests(list(p_values), alpha=q, method="fdr_bh")
    return reject, p_adj


# --------------------------------------------------------------------------
# Spend
# --------------------------------------------------------------------------

def spend_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """Raw and winsorised spend, side by side, plus the decomposition.

    Section 6: both are reported regardless of whether they agree. Medians are
    not used -- spend is zero at the median in every arm, so a median test has
    no power by construction.
    """
    rows = []
    for arm in TREATMENTS:
        raw = bootstrap_diff(df, "spend", arm)
        wins = bootstrap_diff(df, "spend", arm,
                              transform=lambda y: np.minimum(y, WINSOR_CAP))
        sub_t, sub_c = df[df.segment == arm], df[df.segment == CONTROL]
        rows.append({
            "contrast": f"{arm} vs Control",
            "raw_diff": raw["effect"],
            "raw_ci_low": raw["ci_low"],
            "raw_ci_high": raw["ci_high"],
            "wins_diff": wins["effect"],
            "wins_ci_low": wins["ci_low"],
            "wins_ci_high": wins["ci_high"],
            "p_conv_t": sub_t.conversion.mean(),
            "p_conv_c": sub_c.conversion.mean(),
            "spend_given_conv_t": sub_t.loc[sub_t.conversion == 1, "spend"].mean(),
            "spend_given_conv_c": sub_c.loc[sub_c.conversion == 1, "spend"].mean(),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def _fmt_effects(effects: list[Effect], unit: str = "pp") -> pd.DataFrame:
    scale = 100 if unit == "pp" else 1
    return pd.DataFrame([{
        "contrast": e.contrast,
        "estimator": e.estimator,
        f"effect ({unit})": e.effect * scale,
        f"ci_low ({unit})": e.ci_low * scale,
        f"ci_high ({unit})": e.ci_high * scale,
        "relative %": e.relative_pct,
        "p": e.p_value,
    } for e in effects])


def main() -> int:
    engine = get_engine()
    df = load_analysis_frame(engine)
    print(f"Loaded {len(df):,} rows\n")

    # ---- PRIMARY -------------------------------------------------------
    print("=" * 78)
    print("PRIMARY: conversion, Lin-adjusted OLS with HC2 (PRE_REGISTRATION §5)")
    print("=" * 78)
    primary = lin_estimate(df, "conversion")
    reject, p_adj = bh_correct([e.p_value for e in primary], Q_PRIMARY)

    for e, rej, pa in zip(primary, reject, p_adj):
        print(f"\n  {e.contrast}")
        print(f"    effect        {e.effect_pp:+.4f} pp   "
              f"(95% CI {e.ci_low * 100:+.4f} to {e.ci_high * 100:+.4f})")
        print(f"    relative      {e.relative_pct:+.1f}%   "
              f"(control base rate {e.control_mean * 100:.4f}%)")
        print(f"    p             {e.p_value:.3e}   BH-adjusted {pa:.3e}   "
              f"significant at q={Q_PRIMARY}: {bool(rej)}")

    # ---- SENSITIVITY ---------------------------------------------------
    print("\n" + "=" * 78)
    print("SENSITIVITY: unadjusted (cannot override the primary)")
    print("=" * 78)
    unadj = two_proportion_z(df, "conversion")
    for a, u in zip(primary, unadj):
        boot = bootstrap_diff(df, "conversion", a.contrast.split(" vs ")[0])
        print(f"\n  {a.contrast}")
        print(f"    Lin-adjusted     {a.effect_pp:+.4f} pp  "
              f"[{a.ci_low * 100:+.4f}, {a.ci_high * 100:+.4f}]  SE {a.se * 100:.4f}")
        print(f"    unadjusted z     {u.effect_pp:+.4f} pp  "
              f"[{u.ci_low * 100:+.4f}, {u.ci_high * 100:+.4f}]  SE {u.se * 100:.4f}")
        print(f"    bootstrap        {boot['effect'] * 100:+.4f} pp  "
              f"[{boot['ci_low'] * 100:+.4f}, {boot['ci_high'] * 100:+.4f}]")
        print(f"    SE ratio adj/unadj  {a.se / u.se:.4f}")

    # ---- VISIT ---------------------------------------------------------
    print("\n" + "=" * 78)
    print("SECONDARY: visit, identical Lin specification (coherence check)")
    print("=" * 78)
    for e in lin_estimate(df, "visit"):
        print(f"  {e.contrast:<26}{e.effect_pp:+.4f} pp  "
              f"[{e.ci_low * 100:+.4f}, {e.ci_high * 100:+.4f}]  "
              f"({e.relative_pct:+.1f}%)  p {e.p_value:.3e}")

    # ---- SPEND ---------------------------------------------------------
    print("\n" + "=" * 78)
    print(f"SECONDARY: spend, raw and winsorised at ${WINSOR_CAP}")
    print("=" * 78)
    sp = spend_analysis(df)
    for _, r in sp.iterrows():
        print(f"\n  {r.contrast}")
        print(f"    raw          ${r.raw_diff:+.4f}  "
              f"[{r.raw_ci_low:+.4f}, {r.raw_ci_high:+.4f}]")
        print(f"    winsorised   ${r.wins_diff:+.4f}  "
              f"[{r.wins_ci_low:+.4f}, {r.wins_ci_high:+.4f}]")
        print(f"    decomposition  P(conv) {r.p_conv_c * 100:.4f}% -> "
              f"{r.p_conv_t * 100:.4f}%   |   "
              f"E[spend|conv] ${r.spend_given_conv_c:.2f} -> "
              f"${r.spend_given_conv_t:.2f}")

    # ---- DECISION RULE -------------------------------------------------
    print("\n" + "=" * 78)
    print("DECISION RULE (PRE_REGISTRATION §10) -- pooled clause only")
    print("=" * 78)
    print("  Conditions 1-3 require interaction tests, which are Phase 6. What")
    print("  Phase 4 can settle is the 'otherwise' clause: does either arm beat")
    print("  control on the primary family?\n")
    for e, rej in zip(primary, reject):
        beats = bool(rej) and e.effect_pp >= DECISION_THRESHOLD_PP
        print(f"    {e.contrast:<26}{e.effect_pp:+.4f} pp   "
              f"BH-significant {bool(rej)!s:<6} "
              f">= {DECISION_THRESHOLD_PP} pp {e.effect_pp >= DECISION_THRESHOLD_PP!s:<6} "
              f"-> {'PASSES' if beats else 'fails'}")

    best = max(primary, key=lambda e: e.effect)
    print(f"\n  Provisional pooled recommendation: {best.contrast.split(' vs ')[0]}"
          f"  ({best.effect_pp:+.4f} pp)")
    print("  Provisional pending the Phase 6 targeting overlay.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
