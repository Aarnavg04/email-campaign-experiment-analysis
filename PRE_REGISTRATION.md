# Pre-Registration — Hillstrom Three-Arm E-Mail Experiment

**Author:** Aarnav Gandhi
**Status:** Committed before any outcome was computed by treatment arm.
**Verify:** this commit precedes every analysis commit in `git log`, and is tagged
`pre-registration-v1`.

Everything in this document is fixed. Where a later analysis departs from it, the
departure appears as a numbered amendment appended to §11, in its own commit, with the
original text left untouched.

---

## 1. Disclosures

Three things a reader is entitled to know before weighing anything below.

**1.1 The dataset is public and prior published analyses exist.** This plan was written
without consulting them. I have not read any published result, model, or leaderboard
entry for this dataset, and the metric, estimators, subgroups, and decision rule below
were chosen from the schema and from the pooled statistics in §1.2 alone. What this
project demonstrates is process, not discovery: the effects here were found by others
in 2008. The claim being made is about *how* the analysis is conducted, not about
novelty of the finding.

**1.2 Pooled statistics were computed before this document.** Power analysis requires a
baseline rate and a dispersion estimate, and both are functions of the outcomes. Those
inputs were computed **pooled across all three arms** in
[`sql/03_sanity_checks.sql`](sql/03_sanity_checks.sql), which is committed earlier in
this repository's history. A pooled moment is invariant to how the arms differ, so it
cannot reveal a treatment contrast and cannot have influenced any choice below.

Values known to me at the time of writing:

| Pooled quantity | Value |
|---|---|
| N | 64,000 (21,306 / 21,307 / 21,387 by arm) |
| `visit` rate | 0.146781 |
| `conversion` rate | 0.009031 |
| mean `spend` | $1.0509 |
| SD `spend` | $15.0364 |
| median `spend` | $0.00 |
| 99th pct `spend` | **$0.00** |
| 99.9th pct `spend` | $243.66 |
| max `spend` | $499.00 |
| customers with `spend` > 0 | 578 (0.90%) |
| mean `spend` given `conversion` = 1 | $116.36 |
| **ρ(`history`, `spend`)** | **0.0217** |

**1.3 I already know CUPED will not work here, and say so in advance.** ρ(`history`,
`spend`) = 0.0217 is a pooled quantity, so computing it was permitted — but it means
the theoretical ceiling on CUPED's variance reduction is ρ² ≈ **0.05%**. I am
pre-registering the CUPED analysis anyway, with the prediction that it will deliver
essentially nothing, because the honest demonstration of a method includes the case
where the method does not pay. Reporting a near-zero result that was predicted in
advance is a stronger claim than reporting one discovered afterwards.

---

## 2. Business question

Hillstrom's framing: **which audience should receive the Mens campaign, and which the
Womens campaign?**

This is a *targeting* question, not a ship / no-ship question. A ship decision needs
only an average treatment effect. A targeting recommendation requires effects to
**differ across subgroups**, which is a claim about an interaction — and interactions
are far harder to establish than main effects. That is precisely why the subgroups are
named in §7 before any result is seen: a targeting recommendation assembled after
looking is indistinguishable from noise-mining.

## 3. Analysis population

**All 64,000 customers. No exclusions.**

Every customer purchased within the prior twelve months, so the population is already
well defined by the original design. Trimming high-`history` customers was considered
and rejected: although `history` is pre-treatment and excluding on it would be
causally legitimate, any threshold would be arbitrary, would reduce power, and would
change the population the recommendation applies to. The spend skew is handled by the
winsorisation rule in §6 instead of by exclusion.

## 4. Primary metric

**Primary: `conversion`** (binary, purchased within two weeks).

The tension across the three candidates is real:

| Candidate | Base rate | Powered? | Link to revenue |
|---|---|---|---|
| `visit` | 14.68% | Best (MDE 0.96 pp) | Weakest — a visit is not money |
| `conversion` | 0.90% | Tight (MDE 0.257 pp) | Direct — a purchase is the decision |
| `spend` | mean $1.05, median $0 | Worst (MDE $0.41, 38.8% relative) | It *is* the money, but 99.1% zeros |

`conversion` is chosen because it is the decision-relevant binary outcome and it
remains estimable. `visit` is better powered but a marketing lead cannot act on visits;
optimising it risks recommending the campaign that generates the most curiosity rather
than the most revenue. `spend` is what the business ultimately cares about, but with
99.1% of the distribution at zero and a handful of customers dominating the mean, its
MDE is 38.8% of the base value — it cannot resolve a commercially interesting effect,
and building the primary decision on it would mean building it on the four or five
largest orders.

`visit` is designated a **well-powered directional check**: if the treatment effect on
`conversion` is positive but the effect on `visit` is null or negative, that is a
coherence problem worth reporting, not a result to average away.

**Had I chosen `spend` as primary**, I would have been obliged to accept intervals wide
enough that almost any decision would be "inconclusive." That is a defensible choice
too, but it answers a different question.

## 5. Estimation strategy — fixed here, before any result

Each metric is bound to exactly one primary estimator, specified numerically. Vagueness
is what permits a post-hoc choice, so nothing below is left to be settled at analysis
time. **The analysis code implements this table and nothing else.**

| Metric | Role | Estimator (fully specified) | Rationale |
|---|---|---|---|
| `conversion` | **PRIMARY** | Lin-adjusted OLS (linear probability model): outcome on treatment dummies + all 8 mean-centred pre-treatment covariates + treatment×covariate interactions; **HC2** robust SE | Lin (2013): asymptotically no worse than unadjusted even under misspecification, so committing in advance carries no risk of an inefficient estimator |
| `conversion` | Sensitivity | Unadjusted two-proportion z-test + 10,000-resample bootstrap CI | Cross-check. Material disagreement is reported as a finding and **cannot override the primary** |
| `visit` | Secondary | Identical Lin specification | Directional coherence check |
| `spend` | Secondary | **CUPED**, X = `history`, θ = Cov(Y,X)/Var(X), θ estimated **pooled across arms** | `history` is the pre-period value of the same metric — the one place CUPED genuinely applies |
| `spend` | Secondary | Raw mean, bootstrapped (10,000 resamples) | Skew-robust interval without distributional assumptions |
| `spend` | Secondary | Winsorised at **$243.66** (see §6) | Fixed now so it can never be tuned to a result |
| `spend` | Secondary | Decomposition `P(conversion) × E[spend \| conversion]` | Separates "more people bought" from "buyers spent more" — different product stories |

**Covariates, fixed:** `recency`, `history`, `mens`, `womens`, `newbie`, `zip_code`,
`channel`, `history_segment`. Categoricals dummy-coded, continuous covariates
mean-centred. No additions, no removals, no other transformations.

`history_segment` is a banded version of `history` and is therefore collinear with it
by construction. Both are retained because the Lin estimator tolerates redundant
covariates without bias, and dropping one after seeing standard errors would be exactly
the kind of post-hoc choice this document exists to prevent.

### 5.1 The variance-reduction claim, scoped

> **CUPED is applied to `spend` only.** The primary decision on `conversion` uses
> regression adjustment. Any sample-size saving reported in this project is attributed
> to the specific metric and estimator that produced it. The CUPED result does not bear
> on the power of the primary decision, and no statement in the final memo will imply
> that it does.

Both techniques are the same idea — use pre-treatment information to remove variance
that treatment cannot have caused. CUPED is the special case where the covariate is the
pre-period value of the outcome itself. They are reported as one family, applied to
whichever metric each suits, rather than as two separate accomplishments.

**Committed in advance:** report ρ(`history`, `spend`), the realised variance reduction,
and whether it matches the theoretical ≈ ρ². Given ρ = 0.0217 (§1.3), the prediction is
a reduction under 0.1%. Separately, report the variance reduction the Lin adjustment
achieves on `conversion`. It is entirely possible that the pre-treatment covariates
predict `conversion` better than `history` predicts `spend`, making the *primary* metric
the larger variance-reduction win. Whichever way it lands is reported as found.

## 6. Outlier handling for `spend`

**The conventional 99th-percentile winsorisation is degenerate on this dataset and is
not used.** Only 578 of 64,000 customers (0.90%) spend anything, so the 99th percentile
of `spend` is **$0.00**. Winsorising there would cap every purchaser at zero and delete
the metric entirely.

**Rule:** winsorise `spend` at the **99.9th percentile of the pooled unconditional
distribution = $243.66**, which caps **64 customers**. The cap is computed pooled across
arms and applied identically to all arms.

Raw and winsorised results are both reported, always side by side, regardless of whether
they agree.

**Median tests are meaningless here** and will not be used: the median of `spend` is
zero in every arm and every subgroup, so a median comparison has no power by
construction. Differences in *means* are what is bootstrapped.

## 7. Pre-registered subgroups

Pre-treatment covariates only. Four families, 11 subgroups. The list is capped: every
subgroup added spends multiplicity budget and lowers the power of every other test.

| Family | Levels | Why |
|---|---|---|
| Prior purchase history | mens-only, womens-only, both | The natural targeting variable — it is the question being asked |
| `newbie` | new, established | Standard lifecycle split; plausible differential responsiveness |
| `channel` | Phone, Web, Multichannel | E-mail is a digital channel; web-native customers may respond differently |
| `recency` band | 1–3, 4–6, 7–12 months | Recency is the strongest single predictor in RFM |

**`history_segment` is deliberately excluded** as a subgroup despite being suggested in
the project brief. It has seven levels, which would raise the family from 22 to 36 tests
for a variable already collinear with the `history` covariate. The cost in power exceeds
the value.

**Structural note discovered in the pooled checks:** `mens = 0 AND womens = 0` never
occurs — every customer bought mens or womens merchandise in the prior year. The prior
purchase-history family therefore has **three** cells, not four. The "both" cell is small
(6,448 customers) and correspondingly underpowered, as §8 quantifies.

## 8. Power analysis

α = 0.05 two-sided, 80% power, per pairwise contrast, n = 21,306 per arm.

### 8.1 Overall

| Metric | MDE (absolute) | MDE (relative to base) |
|---|---|---|
| `conversion` | **0.257 pp** | 28.4% |
| `visit` | 0.961 pp | 6.5% |
| `spend` | $0.408 / customer | 38.8% |

The contrast across those three rows is the substantive point: the same 64,000 customers
support a 6.5% relative test on `visit` and only a 38.8% relative test on `spend`.
Statistical power is a property of the metric, not of the sample size alone.

### 8.2 Is the experiment adequately powered?

**To detect a 0.25 pp lift on `conversion` at 80% power requires 22,478 per arm. The
experiment has 21,306 — about 6% short.** So the experiment is marginally underpowered
for what is arguably the smallest commercially interesting effect. Larger effects are
comfortably detectable: a 0.5 pp lift needs 5,620 per arm, and a 1.0 pp lift needs 1,405.

This is stated in advance so that a null result on `conversion` is read correctly: as
"no effect detected at a resolution of roughly 0.26 pp," not as "no effect."

### 8.3 Per-subgroup MDE — the hard version

A subgroup is a fraction of the sample, so its MDE is strictly larger. Computed on
`conversion` at the pooled baseline, with n/3 per arm within each subgroup:

| Family | Subgroup | n | n / arm | MDE (pp) | vs overall |
|---|---|---|---|---|---|
| Prior purchase | mens only | 28,818 | 9,606 | 0.382 | 1.49× |
| Prior purchase | womens only | 28,734 | 9,578 | 0.383 | 1.49× |
| Prior purchase | **both** | 6,448 | 2,149 | **0.809** | **3.15×** |
| `newbie` | established | 31,856 | 10,618 | 0.364 | 1.42× |
| `newbie` | newbie | 32,144 | 10,714 | 0.362 | 1.41× |
| `channel` | Phone | 28,021 | 9,340 | 0.388 | 1.51× |
| `channel` | Web | 28,217 | 9,405 | 0.387 | 1.51× |
| `channel` | **Multichannel** | 7,762 | 2,587 | **0.737** | **2.87×** |
| `recency` | 1–3 months | 22,393 | 7,464 | 0.434 | 1.69× |
| `recency` | 4–6 months | 14,192 | 4,730 | 0.545 | 2.12× |
| `recency` | 7–12 months | 27,415 | 9,138 | 0.392 | 1.53× |

**Consequences accepted in advance:**

- The **"both" purchase-history cell** (MDE 0.809 pp) and **Multichannel** (0.737 pp)
  can only detect effects of roughly 80–90% relative lift. Nulls in these two cells are
  **uninterpretable** and will be reported as inconclusive, never as evidence of no
  effect.
- No subgroup can resolve an effect at the 0.257 pp overall MDE. Every subgroup analysis
  in this project is underpowered relative to the pooled test. That is the normal
  condition of subgroup analysis and it is why §9 requires an interaction test rather
  than a comparison of significance.
- MDEs above are **before** multiplicity correction; correcting across 22 tests raises
  them further. The table is therefore optimistic, and is labelled as such.

## 9. Comparisons, families, and multiplicity

Three arms give three pairwise contrasts. Two families:

**Primary family — 2 tests.** Mens vs Control and Womens vs Control, on `conversion`.
Benjamini–Hochberg at **q = 0.05**.

**Secondary / exploratory family — everything else.** Mens vs Womens, all secondary
metrics, and all 11 subgroups × 2 contrasts = 22 subgroup tests. Benjamini–Hochberg at
**q = 0.10** within the subgroup family (smallest threshold 0.00455).

**Secondary results are exploratory and cannot flip the primary decision.** They can
motivate a follow-up test; they cannot substitute for one.

### 9.1 Interaction tests are required for any differential claim

Concluding that an effect differs between two subgroups because one is individually
significant and the other is not **is a statistical error**, and this project will not
make it. Any claim that the treatment works better for one group than another must be
supported by a formal **treatment × covariate interaction term**, tested on the
difference itself. Interaction tests carry their own multiplicity correction within the
secondary family.

Interaction tests are substantially less powered than main-effect tests — typically
requiring around four times the sample to detect an interaction of the same magnitude.
It is therefore expected in advance that most interaction tests here will be
inconclusive.

## 10. Decision rule

Written as if-then, before results.

> **Recommend campaign X to subgroup S** if and only if all four hold:
>
> 1. S was pre-registered in §7;
> 2. the **interaction** test for (campaign X × the covariate defining S) is significant
>    after BH correction at q = 0.10 within the secondary family;
> 3. the 95% CI for the treatment effect within S excludes zero after BH correction; and
> 4. the point estimate for S is **at least 0.30 pp** in absolute conversion lift.
>
> **Otherwise, recommend the pooled treatment** — the campaign with the better
> average effect across everyone — or no campaign, if neither treatment arm beats
> control on the primary family.
>
> If a subgroup satisfies (1), (3) and (4) but fails the interaction test (2), the
> recommendation is **a confirmatory test sized for that subgroup**, not a targeting
> change.

### 10.1 Defending Y = 0.30 pp

The threshold is set by **statistical resolvability, not economics** — and it is worth
being explicit that these point in opposite directions here.

*Economics argue for a much lower threshold.* Mean spend among converters is $116.36, so
a 0.30 pp lift is about **$0.35 of incremental revenue per customer e-mailed**. Marginal
cost per e-mail is on the order of a cent. Purely commercially, a lift an order of
magnitude smaller would still be worth having.

*Resolvability argues for a higher one.* The overall MDE is 0.257 pp and every subgroup
MDE lies between 0.36 and 0.81 pp. A threshold below ~0.3 pp would mean acting on
estimates the experiment cannot distinguish from zero.

Y = 0.30 pp sits just above the overall MDE and below most subgroup MDEs. Setting it
here means **accepting in advance that some genuinely profitable effects will be missed**
because this experiment cannot see them. That is the correct trade when the alternative
is re-targeting a campaign on noise, and the honest framing for the memo is that the
binding constraint is measurement resolution rather than commercial value.

### 10.2 Winner's curse

If the largest subgroup effect is selected *because* it is the largest, its point
estimate is biased upward — the selection itself guarantees it. Any subgroup
recommendation surviving §10 will be reported with this caveat stated explicitly, and
the recommended follow-up test will be sized on a **shrunk** estimate rather than the
observed one.

## 11. Guardrails, limitations, and amendments

### 11.1 No true guardrail metric exists

This dataset contains **no unsubscribe, complaint, spam-report, or fatigue measure**. A
real version of this experiment must not ship on conversion alone: an e-mail campaign
that lifts purchases while driving list attrition destroys long-run value, and nothing
here would detect that.

Were I instrumenting this test for real, the guardrails would be: unsubscribe rate,
spam-complaint rate, e-mail engagement decay over subsequent sends, and 90-day revenue
per customer to catch purchase pull-forward. **All conclusions in this project are
conditional on guardrails that were never measured.**

### 11.2 Other limitations, stated up front

- **No time dimension.** Outcomes are a single two-week aggregate, so there is no
  peeking analysis, no sequential testing, and no novelty-effect decomposition. None
  of those techniques can be demonstrated on this data and none will be claimed.
- **Two-week window.** Purchases displaced beyond fourteen days are invisible; a
  measured lift could be pull-forward rather than incremental demand.
- **2008 retail e-mail.** Not a mobile product, and 2008 e-mail response behaviour does
  not transfer directly to a modern product surface.
- **Spend concentration.** 578 customers account for all spend; the top 50 account for
  29.4% of the total. The mean is fragile by construction, which is why §6 fixes the
  winsorisation rule in advance.
- **Scale.** 64,000 rows is not big data, and the SQL layer here demonstrates schema
  design and correct aggregation, nothing more.

### 11.3 Amendments

None. Any future amendment is appended here as `Amendment 1`, `Amendment 2`, … each in
its own commit, each stating what changed, why, and whether any outcome had been
observed by arm at the time it was written.
