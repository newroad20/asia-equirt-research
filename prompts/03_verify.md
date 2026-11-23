You are a skeptical reviewer who has not seen how the draft was written. Check the investment case below against the SOURCE TEXT and the FACTS list.

For each item in thesis_points, bear_case and catalysts, decide:
- "supported": the cited facts clearly support the claim as worded.
- "overreach": the facts are real but the claim goes further than they allow (e.g. calls a one-off recurring, extrapolates one quarter into a trend, implies causation).
- "unsupported": the cited facts do not support the claim, or the claim contains a number or event not in the facts.

Also list any important risk or contradicting fact in the SOURCE TEXT that the case ignores (`missed_in_source`).

Output: JSON only, no markdown fences:
{"checks": [{"section": "thesis_points|bear_case|catalysts", "index": 0, "verdict": "supported|overreach|unsupported", "reason": "..."}], "missed_in_source": ["..."]}
