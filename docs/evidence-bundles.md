# Evidence bundles

Witdem can export the canonical OSS evidence for one execution as a portable,
versioned JSON document. The export is domain-neutral and contains the existing
execution, operation, link, event, evaluation, and outcome records together
with existing per-execution diagnostics.

The evidence-bundle schema has its own version. Version `1.0` is independent of
the semantic ingestion protocol and the dashboard API version.

## HTTP export

```http
GET /api/v1/runs/{execution_id}/evidence-bundle
```

An unknown execution returns `404`. The endpoint is read-only and does not
materialize new analytics or modify persisted records.

The response content type is `application/json`. The endpoint returns one
complete execution bundle and is not paginated, so response size grows with the
number of canonical records attached to that execution. It has the same access
boundary as the dashboard read API: `WITDEM_API_KEY` protects ingestion, not
this endpoint. Keep it on loopback or behind an authenticated reverse proxy.

## Python export

```python
from witdem.analytics.repository import AnalyticsRepository

repository = AnalyticsRepository("analytics.duckdb")
try:
    bundle = repository.export_evidence_bundle("execution-id")
    payload = bundle.model_dump_json(indent=2)
finally:
    repository.close()
```

An unknown execution raises `KeyError`.

## Contents

The top-level document contains:

- `schema_version`
- one canonical `execution`
- canonical `operations`, `links`, `events`, `evaluations`, and `outcomes`
- `diagnostics` derived by existing OSS analytics

The canonical entity fields in schema `1.0` are:

| Entity | Fields |
| --- | --- |
| `execution` | `execution_id`, `runtime_id`, `started_at`, `ended_at`, `status`, `schema_version`, `attributes` |
| `operation` | `operation_id`, `execution_id`, `trace_id`, `span_id`, `parent_span_id`, `kind`, `name`, `status`, `started_at`, `ended_at`, `attempt`, `attributes` |
| `link` | `link_id`, `execution_id`, `source_id`, `target_id`, `relation`, `attributes` |
| `event` | `event_id`, `execution_id`, `trace_id`, `span_id`, `timestamp`, `type`, `name`, `payload`, `schema_version` |
| `evaluation` | `evaluation_id`, `execution_id`, `subject_id`, `name`, `value`, `label`, `score`, `source`, `confidence`, `definition_version`, `attributes` |
| `outcome` | `outcome_id`, `execution_id`, `name`, `status`, `value`, `timestamp`, `attributes` |

Nullable fields remain JSON `null`; absent observations are not converted to
empty strings, zeroes, or synthetic values. Entity-specific extension data
remains in the canonical `attributes` or `payload` mapping rather than adding
unversioned top-level sections.

Diagnostics include deterministic failure attribution, operation measurement
coverage, explicit evaluation assessments, and persisted workflow differences
when those diagnostics are available. Missing diagnostics remain empty or
`null`; the export does not invent observations.

| Diagnostic | Meaning |
| --- | --- |
| `failure_explanation` | Existing failure attribution for the execution; empty when unavailable |
| `operation_summary` | Existing counts and grouped operation measurements |
| `operation_measurements` | Existing typed measurement rows associated with exported operations |
| `measurement_coverage` | Counts of measured, missing, not-applicable, and applicable measurements plus coverage when defined |
| `evaluation_assessments` | Explicit pass, fail, or unassessed (`null`) result linked by `evaluation_id` |
| `workflow_discrepancies` | Persisted unexpected operations and transitions, or `null` when no workflow diagnostic is available |

Canonical collections have stable ordering, and the export has no generated
timestamp. Repeated exports from an unchanged analytics snapshot therefore
serialize deterministically.

## Minimal response shape

```json
{
  "schema_version": "1.0",
  "execution": {
    "execution_id": "execution-123",
    "runtime_id": "haystack",
    "started_at": "2026-09-02T10:00:00Z",
    "ended_at": "2026-09-02T10:00:03Z",
    "status": "completed",
    "schema_version": "0.1.0",
    "attributes": {}
  },
  "operations": [],
  "links": [],
  "events": [],
  "evaluations": [],
  "outcomes": [],
  "diagnostics": {
    "failure_explanation": {},
    "operation_summary": {},
    "operation_measurements": [],
    "measurement_coverage": {},
    "evaluation_assessments": [],
    "workflow_discrepancies": null
  }
}
```

The installed Python model can produce the exact JSON Schema used by that
release:

```python
from witdem.analytics import EvidenceBundle

schema = EvidenceBundle.model_json_schema()
```

## Compatibility

Consumers must inspect `schema_version` before interpreting a bundle. Schema
`1.0` is the currently supported evidence-bundle contract; it is separate from
the package version, semantic-record protocol, canonical entity schema values,
and dashboard API version. Consumers should reject or quarantine unsupported
evidence-bundle versions rather than guessing their meaning.

Within a supported version, consumers should preserve unknown keys carried in
canonical `attributes` and `payload` mappings. The versioned top-level model is
strict, so a change to its named sections requires an explicit compatibility
decision and regression fixture.

## Scope

The bundle does not include raw corpus payloads, dashboard presentation
projections, ambient workflow definitions, or organizational metadata. It does
not assert that instrumentation captured every event. Receiver acceptance means
the submitted batch is durable, but evidence that was never delivered cannot be
reconstructed by the export.

The bundle also does not assert legal or regulatory compliance, identity,
retention, signing, or organizational policy. Those meanings are outside this
neutral OSS export.
