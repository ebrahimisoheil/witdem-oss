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

- The Material for MkDocs site builds in strict mode and is configured for
  `https://docs.witdem.com/` with a custom-domain file.
- Documentation CI validates every pull request and main-branch change.
  GitHub Pages deployment is deliberately manual through `workflow_dispatch`.
- Repository-only links were converted to canonical GitHub links, while all
  nine YAML contract files remain downloadable from the documentation site.
- The platform source distribution was reduced from approximately 13 MB to
  664 KB by restricting it to the actual package, README, license, and build
  metadata. Both its source archive and wheel install successfully in clean
  environments.
- Every isolated example lockfile is current, and CI now runs all twelve
  standalone example suites in addition to Product Factory.

## External live contract-review acceptance

The external `haystack-cuad-contract-review-workflow-replay` application ran
without dependency-file edits. It imported candidate `witdem-sdk 0.3.0` through
a runtime editable override and sent telemetry to an isolated candidate
analytics installation.

| Field | Observed value |
|---|---|
| Execution ID | `5ac6c70da21c41c9bccda0d59e98265a` |
| Terminal status | Completed |
| Business outcome | `approved_with_exceptions` |
| Product goal | Achieved |
| Providers | DeepSeek, Mistral, OpenAI |
| Models | `deepseek-v4-flash`, `mistral-ocr-latest`, `gpt-5.4` |
| Model calls | 18 |
| Tokens | 14,957 |
| Measured cost | USD 0.04439714 |
| End-to-end duration | 40.217789 s |
| Workflow replay | Attached, 41 declared nodes |
| Canonical workflow path | `/workflows/contract-review/executions/5ac6c70da21c41c9bccda0d59e98265a` |

Attribution was present on the expected workflow steps: Mistral on scanned
document OCR, DeepSeek on normalization/extraction/generation and obligation
work, and OpenAI on extraction, risk, fallback, and final review gates.

The local acceptance dashboard used
`http://127.0.0.1:18571/workflows/contract-review/executions/5ac6c70da21c41c9bccda0d59e98265a`.
The address is intentionally local and only remains available while the
isolated acceptance stack is running.

## Verification

- Analytics: Ruff, MyPy (65 source files), and 176 tests passed.
- SDK: Ruff, MyPy (24 source files), and 126 tests passed.
- Dashboard: 24 tests and production build passed.
- NPM launcher: five tests and syntax checks passed.
- GitHub Actions workflow YAML and repository whitespace checks passed.
- Python and NPM dependency audits reported no known vulnerabilities. The
  vulnerable `cryptography 46.0.7` constraint was replaced with patched
  `cryptography 50.0.1` and its signature-verification tests passed.

Release publication intentionally requires the
`WITDEM_RELEASE_SIGNING_KEY` CI secret. The release workflow fails closed if
that production Ed25519 key is not provisioned and publishes the authoritative
manifest only after the wheel, SDK, image, and NPM launcher are available.
