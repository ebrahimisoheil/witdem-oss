# Security policy

## Supported versions

Security fixes are applied to the current `main` branch and, when appropriate,
the latest published release. Older releases may not receive backports.

| Version | Supported |
| --- | --- |
| `main` | Yes |
| Latest published release | Yes |
| Older releases | No |

## Reporting a vulnerability

Please do not report suspected vulnerabilities in a public issue, discussion,
or pull request.

Use GitHub's private vulnerability reporting for this repository:

<https://github.com/ebrahimisoheil/witdem-oss/security/advisories/new>

Include enough detail to reproduce and assess the issue:

- Affected version or commit.
- Deployment and configuration context.
- Reproduction steps or a minimal proof of concept.
- Expected and observed impact.
- Any suggested mitigation, if known.

The maintainers will acknowledge a complete report as soon as practical,
investigate it, and coordinate disclosure and remediation with the reporter.

## Sensitive data

Witdem handles telemetry that may contain application metadata. Prompt and
response capture is disabled by default. Security reports must not include real
provider keys, customer telemetry, production database files, or other secrets;
use synthetic reproductions instead.
