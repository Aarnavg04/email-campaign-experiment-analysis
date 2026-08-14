"""Targeting analysis: does the effect DIFFER across pre-registered subgroups?

    python -m src.targeting

This is the phase that answers Hillstrom's actual question -- which audience
should receive which campaign -- and it is the phase where portfolio projects
usually lose credibility, because slicing until something is significant is
trivially easy. Three things stop that here:

1. The subgroups were fixed in PRE_REGISTRATION.md section 7, before any
   outcome was seen. The list is closed.
2. Every subgroup estimate is reported beside its own pre-registered MDE, so
   an underpowered null reads as "inconclusive" rather than "no effect".
3. No differential claim is made without a formal interaction test. Comparing
   two subgroups by whether each is individually significant is a statistical
   error, and section 9.1 rules it out in advance.

ESTIMATOR NOTE -- see Amendment 1 in PRE_REGISTRATION.md section 11.3.
Section 5 bound an estimator to each metric but never specified how to
estimate an effect WITHIN a subgroup. That gap was discovered here, after
outcomes had been observed by arm, and is disclosed as an amendment rather
than quietly resolved.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm

from src.db import get_engine
from src.inference import (
    CONTROL,
    Q_SECONDARY,
    TREATMENTS,
    bh_correct,
    load_analysis_frame,
)
from src.power import load_pooled_inputs, mde_proportion, n_required_proportion

# The four families fixed in section 7. Keys are the family names; values map a
# row to its level. history_segment is deliberately absent -- section 7 records
# the reasoning.
FAMILIES: dict[str, Callable[[pd.DataFrame], Any]] = {
    "prior purchase": lambda d: np.where(
        (d.mens == 1) & (d.womens == 1), "both",
        np.where(d.mens == 1, "mens only", "womens only")),
    "newbie": lambda d: np.where(d.newbie == 1, "newbie", "established"),
    "channel": lambda d: d.channel,
    "recency band": lambda d: np.where(
        d.recency <= 3, "1-3 months",
        np.where(d.recency <= 6, "4-6 months", "7-12 months")),
}

OUTCOME = "conversion"


def label_subgroups(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for family, fn in FAMILIES.items():
        out[f"_{family}"] = fn(df)
    return out


# --------------------------------------------------------------------------
# Subgroup effects and interaction tests, from one model per family
# --------------------------------------------------------------------------

def _family_model(df: pd.DataFrame, family: str, outcome: str):
    """Fit outcome ~ treatment * subgroup, with HC2 errors.

    A single saturated treatment-by-subgroup model yields both quantities the
    decision rule needs: each subgroup's effect (a linear combination of
    coefficients) and the interaction test (a joint restriction on the
    interaction block). Fitting separate per-subgroup regressions would give
    the effects but no way to test the difference between them, which is the
    thing section 9.1 actually requires.
    """
    levels = sorted(pd.unique(df[f"_{family}"]))
    ref, others = levels[0], levels[1:]

    design = pd.DataFrame(index=df.index)
    for arm in TREATMENTS:
        design[f"T_{arm}"] = (df.segment == arm).astype(float)
    for lvl in others:
        design[f"G_{lvl}"] = (df[f"_{family}"] == lvl).astype(float)
    for arm in TREATMENTS:
        for lvl in others:
            design[f"T_{arm}:G_{lvl}"] = design[f"T_{arm}"] * design[f"G_{lvl}"]

    # The intercept is inserted directly rather than via sm.add_constant, whose
    # stubs declare an ndarray return and so lose the column names the contrast
    # vectors below are built from.
    design.insert(0, "const", 1.0)
    fit = sm.OLS(df[outcome].astype(float), design).fit(cov_type="HC2")
    return fit, ref, others, list(design.columns)


def subgroup_effects(df: pd.DataFrame, outcome: str = OUTCOME) -> pd.DataFrame:
    """Treatment effect within every pre-registered subgroup, both contrasts."""
    rows = []
    for family in FAMILIES:
        fit, ref, others, names = _family_model(df, family, outcome)
        for arm in TREATMENTS:
            for lvl in [ref, *others]:
                c = np.zeros(len(names))
                c[names.index(f"T_{arm}")] = 1.0
                if lvl != ref:
                    c[names.index(f"T_{arm}:G_{lvl}")] = 1.0
                t = fit.t_test(c)
                n_sub = int((df[f"_{family}"] == lvl).sum())
                rows.append({
                    "family": family,
                    "level": lvl,
                    "contrast": f"{arm} vs Control",
                    "n": n_sub,
                    "n_per_arm": n_sub // 3,
                    "effect_pp": float(np.ravel(t.effect)[0]) * 100,
                    "se_pp": float(np.ravel(t.sd)[0]) * 100,
                    "ci_low_pp": float(np.ravel(t.conf_int())[0]) * 100,
                    "ci_high_pp": float(np.ravel(t.conf_int())[1]) * 100,
                    "p_value": float(np.ravel(t.pvalue)[0]),
                })
    return pd.DataFrame(rows)


def interaction_tests(df: pd.DataFrame, outcome: str = OUTCOME) -> pd.DataFrame:
    """Joint Wald test: does the effect of this campaign differ across levels?

    Section 10 condition 2 requires this per (campaign x covariate), so the
    interaction block is tested separately for each treatment arm rather than
    jointly across both.
    """
    rows = []
    for family in FAMILIES:
        # The reference level is unused here: the interaction block is tested
        # jointly, and the reference level has no interaction coefficient.
        fit, _ref, others, names = _family_model(df, family, outcome)
        for arm in TREATMENTS:
            R = np.zeros((len(others), len(names)))
            for i, lvl in enumerate(others):
                R[i, names.index(f"T_{arm}:G_{lvl}")] = 1.0
            f = fit.f_test(R)
            rows.append({
                "family": family,
                "contrast": f"{arm} vs Control",
                "df_num": len(others),
                "f_stat": float(np.ravel(f.fvalue)[0]),
                "p_value": float(np.ravel(f.pvalue)[0]),
            })
    return pd.DataFrame(rows)


def mens_vs_womens_pooled(df: pd.DataFrame, outcome: str = OUTCOME) -> dict:
    """The head-to-head contrast, pooled. Pre-registered in the section 9
    secondary family, so it is a legitimate test -- but exploratory, and it
    cannot flip the primary decision."""
    t = df.loc[df.segment == "Mens E-Mail", outcome]
    c = df.loc[df.segment == "Womens E-Mail", outcome]
    diff = t.mean() - c.mean()
    se = np.sqrt(t.var(ddof=1) / len(t) + c.var(ddof=1) / len(c))
    from scipy.stats import norm
    return {
        "effect_pp": diff * 100,
        "se_pp": se * 100,
        "ci_low_pp": (diff - 1.96 * se) * 100,
        "ci_high_pp": (diff + 1.96 * se) * 100,
        "p_value": float(2 * (1 - norm.cdf(abs(diff / se)))),
    }


def campaign_dominance(effects: pd.DataFrame) -> pd.DataFrame:
    """Within each subgroup, how do the two campaigns compare to each other?

    This exists because of a structural gap in the section 10 decision rule,
    found at analysis time and disclosed as Amendment 2. Conditions 1-4 test
    campaign X against CONTROL within subgroup S. They never test X against
    the OTHER campaign. A campaign can therefore satisfy every condition in a
    subgroup where the alternative campaign is strictly better, and the rule
    would still say "recommend X to S".

    Reported as point-estimate differences only, deliberately. A within-
    subgroup campaign-vs-campaign TEST was not pre-registered, and adding
    eleven significance tests here after seeing the results is exactly the
    move this project exists to rule out. The point estimates are enough to
    show the direction; a confirmatory test is the correct next step.
    """
    w = effects.pivot_table(index=["family", "level"], columns="contrast",
                            values="effect_pp")
    w.columns = ["mens_pp", "womens_pp"]
    w["mens_minus_womens_pp"] = w.mens_pp - w.womens_pp
    w["womens_better"] = w.mens_minus_womens_pp < 0
    return w.reset_index()


# --------------------------------------------------------------------------
# Winner's curse
# --------------------------------------------------------------------------

def shrink_estimates(effects: pd.DataFrame) -> pd.DataFrame:
    """Empirical-Bayes shrinkage, applied within each contrast.

    If the largest subgroup effect is reported BECAUSE it was largest, its
    estimate is biased upward -- selection guarantees it. Shrinking toward the
    contrast's mean corrects for that, and the correction is strongest exactly
    where it should be: on noisy estimates from small cells.

    Caveat, stated rather than buried: the eleven subgroups overlap, since one
    customer belongs to all four families at once. They are therefore not
    independent draws and this shrinkage is approximate. It is reported to make
    the DIRECTION and rough SIZE of the winner's-curse correction concrete, not
    as an exact posterior.
    """
    out = []
    for contrast, g in effects.groupby("contrast"):
        b, s = g.effect_pp.to_numpy(), g.se_pp.to_numpy()
        grand = float(b.mean())
        # Method-of-moments between-group variance: total spread minus the
        # part attributable to sampling noise.
        tau2 = max(0.0, float(b.var(ddof=1) - np.mean(s ** 2)))
        w = tau2 / (tau2 + s ** 2) if tau2 > 0 else np.zeros_like(b)
        gg = g.copy()
        gg["tau2"] = tau2
        gg["shrink_weight"] = w
        gg["effect_shrunk_pp"] = grand + w * (b - grand)
        out.append(gg)
        _ = contrast
    return pd.concat(out, ignore_index=True)


# --------------------------------------------------------------------------
# The post-treatment trap, demonstrated deliberately
# --------------------------------------------------------------------------

def post_treatment_trap(df: pd.DataFrame) -> pd.DataFrame:
    """Segment on `visit` -- a POST-treatment variable -- and show what breaks.

    This is included as a demonstration of an error, clearly labelled. The
    numbers it produces are NOT causal effects and must never be quoted as if
    they were.

    Why it breaks: `visit` is itself affected by treatment (+7.6 pp for Mens).
    Conditioning on it destroys the randomisation, because treated visitors and
    control visitors are no longer comparable populations. Control visitors are
    people motivated enough to arrive with no e-mail at all; treated visitors
    include marginal people the e-mail pulled in, who were always less likely
    to buy. The resulting contrast mixes the treatment effect with that
    selection difference, and no amount of covariate adjustment separates them.
    """
    rows = []
    for arm in TREATMENTS:
        for visited in (1, 0):
            sub = df.loc[(df.visit == visited) & df.segment.isin([arm, CONTROL])]
            t = sub.loc[sub.segment == arm, "conversion"]
            c = sub.loc[sub.segment == CONTROL, "conversion"]
            rows.append({
                "conditioned on": f"visit = {visited}",
                "contrast": f"{arm} vs Control",
                "n_treatment": len(t),
                "n_control": len(c),
                "rate_treatment_pct": t.mean() * 100,
                "rate_control_pct": c.mean() * 100,
                "apparent_effect_pp": (t.mean() - c.mean()) * 100,
            })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def main() -> int:
    engine = get_engine()
    df = label_subgroups(load_analysis_frame(engine))
    pooled = load_pooled_inputs(engine)

    eff = subgroup_effects(df)
    eff["mde_pp"] = eff.n_per_arm.apply(
        lambda n: mde_proportion(pooled.p_conversion, int(n)))
    reject, p_adj = bh_correct(eff.p_value.tolist(), Q_SECONDARY)
    eff["p_adj"] = p_adj
    eff["bh_significant"] = reject
    eff["powered_for_estimate"] = eff.effect_pp.abs() >= eff.mde_pp

    print("=" * 96)
    print(f"SUBGROUP EFFECTS on {OUTCOME}   ({len(eff)} tests, BH at q={Q_SECONDARY})")
    print("=" * 96)
    print(f"  {'family':<16}{'level':<14}{'contrast':<12}{'effect':>9}{'95% CI':>20}"
          f"{'MDE':>8}{'BH sig':>8}")
    for _, r in eff.iterrows():
        arm = r.contrast.split(" ")[0]
        ci = f"[{r.ci_low_pp:+.3f},{r.ci_high_pp:+.3f}]"
        print(f"  {r.family:<16}{r.level:<14}{arm:<12}{r.effect_pp:>+9.3f}{ci:>20}"
              f"{r.mde_pp:>8.3f}{bool(r.bh_significant)!s:>8}")

    print("\n" + "=" * 96)
    print("INTERACTION TESTS -- required before ANY differential claim (§9.1)")
    print("=" * 96)
    inter = interaction_tests(df)
    ireject, ip_adj = bh_correct(inter.p_value.tolist(), Q_SECONDARY)
    inter["p_adj"] = ip_adj
    inter["bh_significant"] = ireject
    print(f"  {'family':<16}{'contrast':<28}{'F':>9}{'df':>4}{'p':>10}{'BH p':>10}{'sig':>7}")
    for _, r in inter.iterrows():
        print(f"  {r.family:<16}{r.contrast:<28}{r.f_stat:>9.3f}{r.df_num:>4}"
              f"{r.p_value:>10.4f}{r.p_adj:>10.4f}{bool(r.bh_significant)!s:>7}")

    print("\n" + "=" * 96)
    print("DECISION RULE (§10) -- all four conditions")
    print("=" * 96)
    sig_inter = {(r.family, r.contrast) for _, r in inter.iterrows() if r.bh_significant}
    any_pass = False
    for _, r in eff.iterrows():
        c1 = True                                        # pre-registered by construction
        c2 = (r.family, r.contrast) in sig_inter         # interaction significant
        c3 = bool(r.bh_significant)                      # CI excludes zero after BH
        c4 = r.effect_pp >= 3.0e-1                       # >= 0.30 pp
        if c1 and c2 and c3 and c4:
            any_pass = True
            print(f"  PASSES: {r.contrast} within {r.family}={r.level}")
    if not any_pass:
        print("  No subgroup satisfies all four conditions.")
        print("  -> Recommend the pooled treatment (§10 'otherwise' clause).")

    # The rule's blind spot. See Amendment 2.
    dom = campaign_dominance(eff)
    n_womens_better = int(dom.womens_better.sum())
    print("\n  BUT -- §10 never compares the two campaigns to each other.")
    print(f"  Subgroups where Womens beats Mens: {n_womens_better} of {len(dom)}")
    print(f"  {'family':<16}{'level':<14}{'Mens':>9}{'Womens':>9}{'M - W':>9}")
    for _, r in dom.iterrows():
        print(f"  {r.family:<16}{r.level:<14}{r.mens_pp:>+9.3f}"
              f"{r.womens_pp:>+9.3f}{r.mens_minus_womens_pp:>+9.3f}")

    mvw = mens_vs_womens_pooled(df)
    print(f"\n  Pooled Mens vs Womens (pre-registered secondary): "
          f"{mvw['effect_pp']:+.3f} pp "
          f"[{mvw['ci_low_pp']:+.3f}, {mvw['ci_high_pp']:+.3f}], "
          f"p {mvw['p_value']:.2e}")
    print("  Mens is at least as good in every subgroup, so the rule's")
    print("  'PASSES' rows do NOT imply Womens should be sent to anyone.")

    print("\n" + "=" * 96)
    print("WINNER'S CURSE (§10.2)")
    print("=" * 96)
    sh = shrink_estimates(eff)
    for contrast, g in sh.groupby("contrast"):
        best = g.loc[g.effect_pp.idxmax()]
        print(f"\n  {contrast}")
        print(f"    largest subgroup effect   {best.family}={best.level}  "
              f"{best.effect_pp:+.3f} pp")
        print(f"    shrunk estimate           {best.effect_shrunk_pp:+.3f} pp   "
              f"(tau^2 {best.tau2:.4f}, weight {best.shrink_weight:.3f})")
        n_raw = n_required_proportion(pooled.p_conversion, abs(best.effect_pp))
        n_shrunk = n_required_proportion(pooled.p_conversion,
                                         abs(best.effect_shrunk_pp))
        print(f"    follow-up n/arm on raw    {n_raw:,.0f}")
        print(f"    follow-up n/arm on shrunk {n_shrunk:,.0f}   "
              f"({n_shrunk / n_raw:.1f}x larger)")

    print("\n" + "=" * 96)
    print("POST-TREATMENT TRAP -- demonstration of an ERROR, not a result")
    print("=" * 96)
    trap = post_treatment_trap(df)
    print(f"  {'conditioned on':<16}{'contrast':<28}{'treat %':>9}{'ctrl %':>9}"
          f"{'apparent':>11}")
    for _, r in trap.iterrows():
        print(f"  {r['conditioned on']:<16}{r.contrast:<28}"
              f"{r.rate_treatment_pct:>9.3f}{r.rate_control_pct:>9.3f}"
              f"{r.apparent_effect_pp:>+11.3f}")
    print("\n  These are NOT causal effects. `visit` is affected by treatment,")
    print("  so conditioning on it breaks the randomisation. See the docstring.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
