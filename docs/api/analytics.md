# Analytics API

The analytics API is the public read surface used by the Witdem dashboard. It exposes runs, workflow replay, issues, comparisons, and evidence without requiring callers to import storage internals.

The schema below is generated from `witdem.dashboard.create_dashboard_app`. It does not imply that your deployment is publicly reachable from this documentation site.

## Incremental product-goal projections

Python serving adapters can use
`witdem.analytics.goal_metrics.project_goal_contribution(bundle)` to obtain a
versioned, content-free `GoalContribution` for one canonical Evidence Bundle.
`summarize_goal_contributions(contributions)` returns the same
`ProductGoalSummary` used by the OSS repository reader. This keeps the definition
of goal achievement, decision correctness and measurement completeness in OSS.

Persist counters and sums, not averages. Corrected live observations must replace
their prior contribution atomically; retries must not increment it again. Keep a
projection-version/coverage marker: an execution that has not been projected is
not evidence of zero reported goals. Reject unsupported versions and incomplete
populations rather than presenting false zeros. A missing goal never inherits
runtime success. Complete measured zero is distinct from a missing or partial
cost/token total.

Run bundle projection before database write locks. This is a per-execution Python
projection, not a request-time scan of historical bundles, a storage backend, or
a guarantee of dashboard performance. It does not add percentile, portfolio or
time-series projections. It is included in source here; downstream packages must
wait for a matching released analytics version before depending on it.

## Shared goal portfolio and assurance calculations

`witdem.analytics.assurance.summarize_goal_assurance` accepts selected execution
facts, latest evaluation facts grouped by execution, and contract definitions.
The OSS repository uses this same storage-independent calculation. Selection,
latest-evaluation deduplication, tenant authorization and storage remain the
caller's responsibility. Preserve input order for the existing first-metadata
and contract-order behavior.

Adapters building incremental read models can use `accumulate_goal_assurance`
for counts and score sums, then `finalize_goal_assurance` for rates, score means
and the public portfolio shape. Do not add identifiers or metadata like numeric
counters: preserve ordered metadata selection, distinct contract memberships,
and the single-execution link rule. These are in-process APIs, not a new persisted
wire contract. A storage adapter must version and validate its records, reject
incomplete coverage, and update replacements transactionally. Do not invoke a
full historical fact scan on each dashboard request.

`goal_assurance_state` and `evaluation_met_target` preserve existing OSS rules:
explicit assurance takes precedence over the evidence-sufficient fallback;
passing evaluations alone do not establish assurance. The existing portfolio
classifies a reported goal without explicit true achievement as not achieved;
that is distinct from the explicit-false-only execution filter. This extraction
does not change either rule, create regulatory conclusions, or itself implement
an incremental database backend. Downstream builds must wait for a matching
analytics release; no application SDK change is required.

<swagger-ui src="../openapi/analytics.json" tryItOutEnabled="false" supportedSubmitMethods="[]"/>
