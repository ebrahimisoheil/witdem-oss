"""Business-role prompts shared verbatim across physical runtimes."""

RESEARCH_PROMPT = """Review the supplied company evidence. Return JSON with `relevant_evidence_ids`.
Do not invent claims and do not use information outside the evidence pack."""

CRITIQUE_PROMPT = """Critique whether evidence covers catalog_complexity, market_scale,
data_fragmentation, and operational_pain. Return JSON with `missing_dimensions`, `conflicts`,
and `research_queries`. A query must be exactly one missing or weak dimension name."""

PROFILE_PROMPT = """Extract a company qualification profile using only supplied evidence.
Return JSON: company_name, summary, evidence_ids, completeness, and dimensions mapping each
required dimension to a 0..1 proposed strength. Preserve uncertainty; never fabricate evidence."""

QUALIFICATION_PROMPT = """Review the extracted profile and source evidence. Return JSON with
`dimensions`, mapping each required dimension to a 0..1 proposed score. This is advisory:
application code, not the model, owns the final threshold and decision."""
