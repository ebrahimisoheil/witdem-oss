# Performance, lifecycle, and update implementation report

This report records the acceptance evidence for the coordinated performance,
lifecycle, update, and documentation release. The dashboard interaction and
visual design were intentionally preserved.

## Safety and performance

- Safety commit before implementation: `fbc84f6`.
- Workflow manifests are content-addressed by YAML hash and compiler version.
- Execution projections and per-node attribution are materialized during ELT.
- A copied 31-execution development corpus improved from approximately 5.5 s
  per workflow-detail request to approximately 18 ms after materialization.
- The same corpus returned execution replay in approximately 91 ms.
- The automated benchmark covers 10, 100, 1,000, and 10,000 executions and
  enforces the release thresholds.
- ECharts is route-split into a lazy chunk. The initial JavaScript payload fell
  from approximately 1.30 MB (421.5 KB gzip) to 702.6 KB (218.4 KB gzip).
- Workflow-level model, provider, and step charts now aggregate the matched
  materialized projections directly. They no longer depend on an optional
  application-supplied workflow-name telemetry field.

## Neutral analytics accuracy

- Runtime state, application outcome, goal assurance, cohort association, and
  direct operation attribution are now separate contracts.
- Provider, model, model vendor, runtime, and framework identities are retained
  only when explicitly reported. Model identities are provider-scoped, and the
  dashboard no longer infers providers or colors from model names.
- Cost and token coverage distinguishes complete, partial, missing, and
  not-applicable runs. Unknown values remain unavailable instead of becoming
  zero.
- The two pre-existing measured contract-review runs reconciled to DeepSeek
  (17 calls, 41.807081 s, USD 0.014018728, 23,989 tokens), GPT (19 calls,
  69.242292 s, USD 0.1479175, 26,617 tokens), and Mistral OCR (2 calls,
  1.578842 s, USD 0.008, tokens unreported). Their spend shares were 8.25%,
  87.04%, and 4.71%; cost coverage was complete for the 2 applicable runs out
  of 32 total runs.
- Conservation tests cover overlapping spans, parallel YAML nodes, same-label
  models through different providers, explicit vendor metadata, partial
  measurements, explicit zero cost, and mutually exclusive runtime states.

## Installation-path acceptance

Both release-candidate launchers were exercised with isolated ports and data:

- Built-wheel pipx/native start, status, ingestion, ELT, dashboard query, logs,
  shutdown, immediate restart, persistence, and orphan-process checks passed.
- Packed-NPM NPX/Docker start, status, ingestion, ELT, dashboard query, logs,
  shutdown, restart, persistence, and container-cleanup checks passed.
- No developer data directory or shared Docker volume was used by either gate.
- The native gate exposed and verified a same-port immediate-restart edge case;
  availability checks now mirror the server's address-reuse behavior.

## Documentation and distribution

- End-user documentation covers installation, first execution, configuration,
  SDK usage, YAML contracts, workflow replay, dashboard behavior, lifecycle
  operations, upgrades, troubleshooting, integrations, providers, examples,
  architecture, pricing, development, CLI usage, and release changes.
- Repository-only links were converted to canonical GitHub links, while all
  nine complete YAML contract examples remain checked in beside the tutorial.
- The platform source distribution was reduced from approximately 13 MB to
  664 KB by restricting it to the actual package, README, license, and build
  metadata. Both its source archive and wheel install successfully in clean
  environments.
- Every isolated example lockfile is current, and CI now runs all twelve
  standalone example suites in addition to Product Factory.

## External live contract-review acceptance

The external `haystack-cuad-contract-review-workflow-replay` application ran
without dependency-file edits. It imported candidate `witdem-sdk 0.1.0` through
a runtime editable override and sent telemetry to an isolated candidate
analytics installation.

| Field | Observed value |
|---|---|
| Execution ID | `a56b46f11aed45898b1b2f62cabc85f5` |
| Terminal status | Completed |
| Business outcome | `manual_review_required` |
| Product goal | Achieved |
| Providers | DeepSeek, Mistral, OpenAI |
| Models | `deepseek-v4-flash`, `mistral-ocr-latest`, `gpt-5.4` |
| Model calls | 17 |
| Tokens | 14,006 |
| Measured cost | USD 0.045883396 |
| End-to-end duration | 36.632194 s |
| Workflow replay | Attached, 41 declared nodes |
| Canonical workflow path | `/workflows/contract-review/executions/a56b46f11aed45898b1b2f62cabc85f5` |

Attribution was present on the expected workflow steps: Mistral on scanned
document OCR, DeepSeek on normalization/extraction/generation and obligation
work, and OpenAI on extraction, risk, fallback, and final review gates.

The local acceptance dashboard used
`http://127.0.0.1:8501/workflows/contract-review/executions/a56b46f11aed45898b1b2f62cabc85f5`.
The address is intentionally local and only remains available while the
isolated acceptance stack is running.

## Vendor-neutral operations acceptance

The external scanned-contract scenario was run again after introducing the
operation taxonomy, typed measurement facts, contextual Operations and
Evaluations views, and evaluator/application separation. The candidate SDK was
again supplied at runtime without changing the example's dependency files.

| Field | Observed value |
|---|---|
| Execution ID | `b718b4f218f04e3caf4fb6a129aa70fa` |
| Terminal status | Completed |
| Business outcome | `approved_with_exceptions` |
| Providers | DeepSeek, Mistral, OpenAI |
| Models | `deepseek-v4-flash`, `mistral-ocr-latest`, `gpt-5.4-2026-03-05` |
| Model calls | 18 |
| Tokens | 15,174 |
| Measured cost | USD 0.04438446 |
| Canonical workflow path | `/workflows/contract-review/executions/b718b4f218f04e3caf4fb6a129aa70fa` |
| Acceptance dashboard | `http://127.0.0.1:28501/workflows/contract-review/executions/b718b4f218f04e3caf4fb6a129aa70fa` (isolated stack now stopped) |

The OCR call is classified as `family=media`, `type=ocr`, and
`interface=model_api`. It reports one processed page, 279,523 input bytes, and
USD 0.004 of measured cost. Token measurements are not applicable rather than
zero. DeepSeek and OpenAI generation calls retain their reported token meters.
Every inference and OCR operation is attributed to its declared YAML workflow
node, and three evaluation results are attached separately from evaluator
economics.

The preserved development corpus was rebuilt in place under the maintenance
lock: Duckle normalized 445 immutable ingest batches covering 33 executions,
then materialized 32 workflow projections, 616 operation classifications, and
1,474 typed measurement facts. The rebuilt Docker dashboard served the new
asset bundle and exposed the populated Operations view without browser console
errors.

The isolated native stack passed shutdown, immediate restart, persisted-query,
and health checks. Measured local API timings were approximately 159 ms first
request / 132 ms median for execution replay, 57 ms first request / 54 ms
median for workflow detail, and 54 ms first request / 54 ms median for the
workflow operations view.

A subsequent live run against the rebuilt shared dashboard produced execution
`5fb97bb383684a9a8236f4c62e52a150`: completed in 36.833724 s with outcome
`approved_with_exceptions`, 18 model calls, 13,802 tokens, and USD 0.044025656
measured cost. OCR again reported one page, 279,523 bytes, and USD 0.004 with
tokens not applicable. The run also exposed and verified a direction-vocabulary
fix: the two target-based evaluations now pass under `higher_is_better`, while
the label-only validity evaluation deliberately remains unassessed.

## Verification

- Analytics: Ruff, MyPy (67 source files), and 188 tests passed.
- SDK: Ruff, MyPy (24 source files), and 129 tests passed.
- Dashboard: 28 tests, typecheck, production build, and live browser checks passed.
- NPM launcher: five tests and syntax checks passed.
- GitHub Actions workflow YAML and repository whitespace checks passed.
- Python and NPM dependency audits reported no known vulnerabilities. The
  vulnerable `cryptography 46.0.7` constraint was replaced with patched
  `cryptography 50.0.1` and its signature-verification tests passed.

Release publication intentionally requires the
`WITDEM_RELEASE_SIGNING_KEY` CI secret. The release workflow fails closed if
that production Ed25519 key is not provisioned and publishes the authoritative
manifest only after the wheel, SDK, image, and NPM launcher are available.
