# Case Study — A Production Mortgage Pricing Engine at a Major Australian Retail Bank

**Prepared:** 2026-04-07
**Role:** Sole developer and architect
**Tenure:** ~2 years in production
**Scope:** Full mortgage pricing strategy execution for a Big 4 Australian retail bank

---

## The headline

One architect, two years, two hundred products, two thousand logical rules. The replacement system, from a global pricing-decisioning vendor, required **five hundred million** enumerated rules to cover the same pricing space — a **250,000× compression difference**. The replacement vendor's own lead engineer openly acknowledged the earlier architecture was mathematically superior; they could not reproduce it in their own product and chose physical enumeration instead.

## The problem

Mortgage pricing at scale is not "apply rate X to product Y". It is the combinatorial interaction of base rates, risk adjustments (LVR, aggregate limit, net utilisation, risk weight, loan amount), customer attributes (segment, staff, foreign resident, industry), channel (branch, broker, mobile banker), competitor pressure, promotional cycles, banker discretion, and desk-level overrides — across a product catalogue of ~200 mortgage variants, each with their own rate structure. A banker writing a loan needs a price in seconds; the pricing team needs to push a rate change to every banker in the country within an hour; the risk team needs an audit trail that explains every decision.

Before this engine, the bank managed pricing in Excel. Changes took days. Audits were forensic. Every product had its own spreadsheet, and cross-product consistency was checked by eye.

## The approach

The engine treated pricing as a **rule combination problem**, not a rule lookup problem. Rather than enumerate every possible customer × product × channel × banding combination (the approach the replacement system later took), the engine represented the pricing space as a small set of **partial rules**, each describing a constraint on one dimension and a marginal adjustment. The engine then built — recursively, via a self-joining CTE — the **lattice of all mutually consistent combinations of those rules**, filtered to the *outermost* combinations that dominated their inner scaffolding under a prime-factorisation subset test.

The key insight was that the maximal consistent combination of matching rules is the *correct* answer to a pricing query, not one-rule-wins-by-priority. A customer who simultaneously qualifies for a broker discount, a high-LVR premium, and a first-home-buyer promotion should receive the compounded effect of all three, not the single-highest-priority one. The earlier generation of rule engines (salience-based production systems) cannot express this; the accumulator architecture can, cleanly.

The implementation was in T-SQL on SQL Server, with a web-based rule management interface on top. Rule changes made by the pricing team propagated to production bankers in under an hour. The system ran unattended for two years, producing every mortgage quote the bank issued.

## The result

- **~200 products** represented.
- **~2,000 logical rules** covering the entire pricing space — additive risk adjustments, discretionary margin cells, promotional overlays, competitive responses, banker and desk-floor authority levels.
- **< 1 hour** from rule-team approval to live price on banker terminals.
- **Full audit trail** for every quote: which rules contributed, in what order, with what combined effect, down to prime-factorisation proof of the combination's uniqueness within its constraint namespace.
- **2 years** in production, zero architectural rewrites.

The bank later migrated to a global pricing-decisioning vendor's platform. The replacement system covers the same pricing space as **approximately 500 million enumerated rules** — a physically materialised base matrix of ~1,000 reference points multiplied across every dimensional combination. The replacement vendor's lead engineer, when challenged on the architectural choice, openly acknowledged the earlier system's compression was superior; they had evaluated the accumulator approach and chosen physical enumeration because their platform's architecture could not cleanly host it¹.

## What this means for a modern small-to-mid lender

Small-to-mid lenders — credit unions, mutuals, non-bank lenders, fintech mortgage originators — face the same combinatorial problem as the Big 4, with three differences:

1. **They cannot afford the enterprise platforms.** FICO Decision Central, Experian PowerCurve, Earnix, and SAS Intelligent Decisioning are priced for banks with billion-dollar mortgage books.
2. **They need the audit story more, not less.** APRA and ASIC expect responsible-lending decisions to be defensible, and the regulator's questions are easier to answer against 2,000 logical rules than against 500 million enumerated ones.
3. **They compete on specialisation.** Their pricing needs to be *more* sophisticated than a Big 4's, not less, to justify their market position against the majors' scale.

The accumulator engine pattern, rebuilt on modern open-source Python/polars infrastructure (no SQL Server license required), is a direct match for this segment. The original system ran a major bank's entire mortgage book on a single SQL Server instance; a modern Python port will run comfortably on a laptop-class development machine and scale trivially in production. The engine is under active rebuild now, with the algorithmic core preserved, the 2016 organisational quirks discarded, and three concerns cleanly separated into data preparation, rules evaluation, and governance/lifecycle layers.

## Next step

The engine pattern, the architectural principles, and the historical case study are available for technical due diligence with qualified lenders exploring risk-based pricing options. Reference conversations and architecture reviews are available on request.

---

¹ *Direct engineering-honesty quote from the replacement vendor's lead engineer is available with appropriate attribution permissions, which the author has not yet sought. Paraphrased above to preserve the substantive claim while respecting the original context of the conversation.*
