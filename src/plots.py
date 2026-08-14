"""Figures.

    python -m src.plots

Writes light and dark variants into figures/ so the README can serve the right
one per GitHub theme via a <picture> element. Dark is a deliberate re-step from
the same palette, not an automatic inversion.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.balance import (
    SMD_THRESHOLD,
    arm_counts,
    load_balance,
    smd_noise_calibration,
    standardised_mean_differences,
)
from src.db import PROJECT_ROOT, get_engine

FIGURES = PROJECT_ROOT / "figures"

# Categorical slots 1 and 2 from the validated palette. Verified with
# scripts/validate_palette.js: all six checks pass in both modes, worst
# adjacent CVD separation dE 24.7 (protan), normal-vision dE 33.6.
SERIES_LIGHT = {"Mens E-Mail vs Control": "#2a78d6", "Womens E-Mail vs Control": "#eb6834"}
SERIES_DARK = {"Mens E-Mail vs Control": "#3987e5", "Womens E-Mail vs Control": "#d95926"}


@dataclass(frozen=True)
class Theme:
    name: str
    surface: str
    text_primary: str
    text_secondary: str
    grid: str
    band: str
    series: dict


LIGHT = Theme("light", "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1", "#f0efec", SERIES_LIGHT)
DARK = Theme("dark", "#1a1a19", "#ffffff", "#c3c2b7", "#333330", "#262624", SERIES_DARK)

# Fixed display order, matching the covariate list in PRE_REGISTRATION.md §5.
COVARIATE_ORDER = [
    "recency",
    "history",
    "history_segment",
    "mens",
    "womens",
    "newbie",
    "zip_code",
    "channel",
]


def _ordered(smd: pd.DataFrame) -> pd.DataFrame:
    df = smd.copy()
    df["_cov"] = pd.Categorical(df.covariate, categories=COVARIATE_ORDER, ordered=True)
    return df.sort_values(["_cov", "level"])


def love_plot(smd: pd.DataFrame, se: float, theme: Theme, out_path):
    """Love plot: standardised mean difference per covariate level, per contrast.

    The x-axis is scaled in standard errors rather than to the conventional
    +/-0.10 threshold. Drawing that threshold would compress every point onto
    the zero line and hide the actual structure -- at n≈21,300 per arm it sits
    more than ten SE out. The bands show +/-1 and +/-2 SE, which is the scale
    on which randomisation noise actually lives, and the caption states where
    the 0.10 rule would fall.
    """
    df = _ordered(smd)
    labels = df[["covariate", "level"]].drop_duplicates()

    # Multi-level covariates get "family - level" so a row is unambiguous on
    # its own; single-level ones just use the covariate name. Dollar signs are
    # escaped because matplotlib parses a $...$ pair as mathtext, which
    # silently swallowed the "$" in the history bands.
    multi = {"zip_code", "channel", "history_segment"}
    short = {"history_segment": "hist. band", "zip_code": "zip", "channel": "channel"}
    label_text = [
        (f"{short[cov]} · {lvl}" if cov in multi else cov).replace("$", r"\$")
        for cov, lvl in zip(labels.covariate, labels.level)
    ]
    y_pos = {(c, l): i for i, (c, l) in enumerate(zip(labels.covariate, labels.level))}
    n_rows = len(labels)

    fig, ax = plt.subplots(figsize=(9.5, 8.4))
    fig.patch.set_facecolor(theme.surface)
    ax.set_facecolor(theme.surface)

    # Noise bands, most recessive layer first. The two need visibly different
    # tones or the +/-1 band reads as part of the +/-2 band.
    ax.axvspan(-2 * se, 2 * se, color=theme.band, zorder=0)
    ax.axvspan(-se, se, color=theme.grid, zorder=0)
    ax.axvline(0, color=theme.text_secondary, lw=1.0, zorder=1)

    for contrast, color in theme.series.items():
        sub = df[df.contrast == contrast]
        ys = [y_pos[(c, l)] for c, l in zip(sub.covariate, sub.level)]
        ax.scatter(
            sub.smd, ys,
            s=64, color=color, label=contrast, zorder=3,
            edgecolors=theme.surface, linewidths=2.0,  # 2px surface ring
        )

    # Separators between covariate blocks, so grouping reads without boxes.
    boundaries = np.where(labels.covariate.values[1:] != labels.covariate.values[:-1])[0]
    for b in boundaries:
        ax.axhline(b + 0.5, color=theme.grid, lw=1.0, zorder=0)

    ax.set_yticks(range(n_rows))
    ax.set_yticklabels(label_text, fontsize=10, color=theme.text_primary)
    ax.invert_yaxis()
    ax.set_xlim(-3.2 * se, 3.2 * se)
    ax.set_xlabel(
        "Standardised mean difference vs control", fontsize=11, color=theme.text_primary
    )
    ax.tick_params(axis="x", colors=theme.text_secondary, labelsize=10)
    ax.tick_params(axis="y", length=0)

    # np.asarray keeps these well-typed: matplotlib declares the transform
    # arguments as ArrayLike, which does not support bare arithmetic operators.
    def to_se(x):
        return np.asarray(x) / se

    def from_se(x):
        return np.asarray(x) * se

    sec = ax.secondary_xaxis("top", functions=(to_se, from_se))
    sec.set_xlabel("standard errors", fontsize=9.5, color=theme.text_secondary)
    sec.tick_params(colors=theme.text_secondary, labelsize=9)

    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(theme.grid)

    ax.set_title(
        "Covariate balance across treatment arms",
        fontsize=13.5, color=theme.text_primary, loc="left", pad=72,
    )
    # Sits above the secondary axis label rather than on top of it.
    # Two lines: as one line this overflows the figure width and is clipped.
    ax.text(
        0, 1.100,
        f"Shaded bands ±1 and ±2 SE (SE = {se:.4f}).\n"
        f"The conventional |SMD| > {SMD_THRESHOLD} threshold lies at "
        f"±{SMD_THRESHOLD / se:.1f} SE — far off this scale.",
        transform=ax.transAxes, fontsize=9.5, color=theme.text_secondary,
        linespacing=1.45, va="bottom",
    )

    # Below the axes: at this scale points reach into every corner of the
    # plotting area, so an inset legend would cover data.
    leg = ax.legend(
        loc="upper center", bbox_to_anchor=(0.5, -0.075), ncol=2,
        frameon=False, fontsize=10,
    )
    for txt in leg.get_texts():
        txt.set_color(theme.text_primary)  # text tokens, never the series color

    fig.tight_layout()
    fig.savefig(out_path, dpi=200, facecolor=theme.surface)
    plt.close(fig)
    print(f"  wrote {out_path.relative_to(PROJECT_ROOT)}")


def bootstrap_plot(boots: list[dict], theme: Theme, out_path):
    """Bootstrapped difference distributions, one panel per contrast.

    Shows the whole sampling distribution rather than an interval alone, which
    makes the distance from zero legible at a glance -- the thing a reader
    actually wants to judge.

    The x-axis is in percentage points and shared across panels, because the
    comparison between the two contrasts is the point; letting each panel
    autoscale would make a smaller effect look identical to a larger one.
    """
    fig, axes = plt.subplots(
        len(boots), 1, figsize=(9.0, 2.7 * len(boots)), sharex=True
    )
    fig.patch.set_facecolor(theme.surface)
    colors = list(theme.series.values())

    all_draws = np.concatenate([b["draws"] * 100 for b in boots])
    lo, hi = all_draws.min(), all_draws.max()
    pad = 0.12 * (hi - lo)
    xlim = (min(0.0, lo) - pad, hi + pad)

    for ax, b, color in zip(np.atleast_1d(axes), boots, colors):
        draws = b["draws"] * 100
        ci_low, ci_high = b["ci_low"] * 100, b["ci_high"] * 100
        ax.set_facecolor(theme.surface)

        # Shared bin edges for both layers. Letting each call pick its own
        # bins misaligns the solid subset against the pale full distribution
        # and reads as noise rather than as a highlighted interval.
        edges = np.histogram_bin_edges(draws, bins=70)
        ax.hist(draws, bins=edges, color=color, alpha=0.30, zorder=2)
        inside = (draws >= ci_low) & (draws <= ci_high)
        ax.hist(draws[inside], bins=edges, color=color, alpha=0.95, zorder=3)

        ax.axvline(0, color=theme.text_primary, lw=1.4, zorder=4)
        ax.axvline(b["effect"] * 100, color=theme.text_primary, lw=1.4,
                   ls="--", zorder=4)

        ax.set_title(b["contrast"], fontsize=11.5, color=theme.text_primary,
                     loc="left", pad=8)
        ax.text(
            0.995, 0.90,
            f"{b['effect'] * 100:+.3f} pp   95% CI [{ci_low:+.3f}, {ci_high:+.3f}]",
            transform=ax.transAxes, ha="right", fontsize=10,
            color=theme.text_secondary,
        )
        ax.set_yticks([])
        ax.set_xlim(*xlim)
        ax.tick_params(axis="x", colors=theme.text_secondary, labelsize=10)
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.spines["bottom"].set_color(theme.grid)

    axes_list = np.atleast_1d(axes)
    axes_list[-1].set_xlabel(
        "Difference in conversion rate, percentage points",
        fontsize=11, color=theme.text_primary,
    )
    fig.suptitle(
        "Bootstrapped treatment effects (10,000 resamples)",
        fontsize=13.5, color=theme.text_primary, x=0.011, ha="left", y=0.988,
    )
    # Solid bars mark the 95% interval; the dashed line is the point estimate.
    fig.text(
        0.011, 0.930,
        "Solid = inside the 95% interval · dashed = point estimate · "
        "solid vertical = zero",
        fontsize=9.5, color=theme.text_secondary, ha="left",
    )

    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(out_path, dpi=200, facecolor=theme.surface)
    plt.close(fig)
    print(f"  wrote {out_path.relative_to(PROJECT_ROOT)}")


def main() -> int:
    FIGURES.mkdir(exist_ok=True)
    engine = get_engine()

    smd = standardised_mean_differences(load_balance(engine))
    counts = arm_counts(engine)
    cal = smd_noise_calibration(smd, int(counts.min()))
    se = cal["theoretical_se"]

    print("Rendering figures...")
    for theme in (LIGHT, DARK):
        suffix = "" if theme.name == "light" else "-dark"
        love_plot(smd, se, theme, FIGURES / f"covariate_balance{suffix}.png")

    # Imported here rather than at module scope: inference imports plots for
    # nothing, but keeping the dependency one-directional avoids a cycle if
    # that ever changes.
    from src.inference import TREATMENTS, bootstrap_diff, load_analysis_frame

    df = load_analysis_frame(engine)
    boots = [bootstrap_diff(df, "conversion", arm) for arm in TREATMENTS]
    for theme in (LIGHT, DARK):
        suffix = "" if theme.name == "light" else "-dark"
        bootstrap_plot(boots, theme, FIGURES / f"bootstrap_conversion{suffix}.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
