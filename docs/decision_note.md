# What I decided, and what I left out

**The bet.** The retyping is the visible pain; the real cost is that a buyer can't trust a number without re-checking it. So I built for **trust per number**, not extraction coverage. Every cell carries four things: where it came from (file + cell, paragraph, page or photo region, with the verbatim text), how it was converted, a confidence score, and who confirmed it.

**Decisions**

1. **The LLM reads; code calculates.** Claude maps each vendor's mess onto the RFx lines and copies numbers exactly as written: per 100, per kg, per metre, USD, "same as item 6", "same as last year". Python does every conversion, and prints its working (e.g. "USD 0.3647 × 88.20 + freight ₹14,500/7 MT × 0.74 kg").
2. **Flag, don't guess.** A blurred digit, a handwritten correction, an ambiguous "rest same as last year", or a new item with no history goes to a review queue. It is never silently filled. Unresolvable lines are excluded from totals, and the system says which.
3. **Check claims against evidence.** The gate reads the attached certificate itself. Pacific answers "ISO: Yes", but its certificate expired in March 2026, so Pacific fails, and the screen shows why.
4. **Every citation is checked against the file.** If the quoted text isn't in the file, or the number isn't in the quoted text, confidence drops and a human sees it. Numbers read from photos are capped at medium confidence.
5. **Compare on landed cost, with assumptions switchable.** Freight for "extra, amount unknown" is estimated at a buyer-set ₹/kg and marked with ~. Conditional rebates and cash discounts are shown as conditions, never baked into prices.
6. **The analyst uses the same engine as the screen.** It gets numbers only from read-only SQL and the award engine. It must state exclusions, estimates and unconfirmed prices, and every answer has "How I got this".
7. **The human stays the decider.** Confirms, corrections, exclusions and gate overrides are one click each, need a reason, and go to the audit log and the Excel export.

**Deliberately left out**

- Real email/SMTP, vendor portal and auth: stubbed, as the brief allows.
- Negotiation rounds, e-auction, PO/ERP push.
- Multi-RFx and multi-user.
- Tooling-cost amortisation and cost-of-capital valuation of payment terms: shown as deviations, not priced in.
- Should-cost modelling: the weight model exists only to convert per-kg quotes.
- OCR fallback: Claude vision reads photos directly, and those values are trusted less.

**The problem I think is actually bigger:** the **clarification loop**. On this data, extraction gets you about 80% of the way. The remaining 20% (Om Sai's freight, Rathi's blurred price, Deccan's missing certificate) takes days of back-and-forth. That's why the system drafts vendor-specific clarification emails from its own flags. The next product is a round-2 loop: send the questions, read the answers, and update only those cells.
