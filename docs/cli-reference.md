# CLI command reference

NPX and pipx expose the same service-lifecycle commands. Use `witdem` below for
a pipx installation or replace it with `npx -y witdem@latest` for Docker.

## Shared lifecycle commands

| Command | Purpose |
| --- | --- |
| `witdem up [--open\|--no-open]` | Start receiver, worker, and dashboard; wait for health |
| `witdem open` | Open the healthy dashboard |
| `witdem status [--json]` | Validate service/process identity and endpoint health |
| `witdem logs [--follow] [service]` | Read receiver, worker, or dashboard logs |
| `witdem doctor` | Validate prerequisites, ports, storage, and compatibility |
| `witdem version` | Print installed component versions |
| `witdem update --check [--refresh\|--offline]` | Verify releases and print guidance; never mutate |
| `witdem down` | Stop only validated services and preserve data |
| `witdem workflow compile [--check\|--force]` | Validate/materialize workflow YAML |
| `witdem workflow rebuild` | Rebuild serving projections under maintenance lock |
| `witdem dev` | Run foreground contributor mode |

Common options include `--receiver-port`, `--dashboard-port`, and `--data-dir`.
NPX additionally accepts `--project-name` and `--image`.

## Native administrative commands

The pipx/native backend also exposes lower-level administration commands. They
are not NPX launcher commands; with NPX, perform equivalent operations inside
the version-matched stack rather than assuming the launcher accepts them.

| Command | Purpose and safety |
| --- | --- |
| `witdem serve` | Run only the OTLP/SDK receiver |
| `witdem dashboard` | Run only the dashboard and read API |
| `witdem elt run` | Process currently pending corpus batches |
| `witdem elt worker` | Continuously process committed corpus batches |
| `witdem elt status` | Show corpus and transformation status |
| `witdem eval validate <campaign.jsonl>` | Validate an offline evaluation campaign without writes |
| `witdem eval import <campaign.jsonl> [--db PATH\|--data-dir PATH]` | Import a validated framework-neutral campaign |
| `witdem taxonomy reprocess` | Reclassify derived operation facts from preserved raw telemetry |
| `witdem inspect` | Inspect database tables and row counts |
| `witdem prune --older-than 30d` | Preview time-based corpus retention; add `--yes` to delete |
| `witdem reset --live --yes` | Reset explicitly targeted mutable local state; destructive and disabled without confirmation |

Run `witdem <command> --help` or `npx -y witdem@latest <command> --help` for
the exact options supported by the installed launcher version. Do not assume a
native-only command exists in NPX merely because both paths share lifecycle
commands.

CI verifies that every shared lifecycle command above is present in both
launchers' generated help output.

## Application SDK commands

These commands are provided by the separately installed `witdem-sdk` package
and run from the instrumented application's repository.

| Command | Purpose and safety |
| --- | --- |
| `witdem-sdk init [--directory PATH] [--service-name NAME]` | Create the v2 project index, starter contract, and canonical `.witdem/skills/witdem` coding-agent skill; refuses existing generated files |
| `witdem-sdk init --expose-agent-skill` | Also link `.agents/skills/witdem` to the canonical skill for discovery |
| `witdem-sdk init --force` | Explicitly replace existing generated project, contract, and skill files |
| `witdem-sdk validate [--config PATH]` | Validate the project and every referenced contract and workflow without writes |
| `witdem-sdk run -- <command>` | Validate the discovered project, then run the application command |

The generated skill is guidance for coding agents, not executable business
logic. `.witdem` remains its source of truth; the optional `.agents` entry is a
relative symbolic link.
