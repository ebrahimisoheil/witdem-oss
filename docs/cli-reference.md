# CLI reference

NPX and pipx expose the same lifecycle vocabulary. Use `witdem` below for a
pipx installation or replace it with `npx -y witdem@latest` for Docker.

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
| `witdem eval validate <campaign.jsonl>` | Validate an offline evaluation campaign without writes |
| `witdem eval import <campaign.jsonl> [--db PATH\|--data-dir PATH]` | Import framework-neutral campaign results |
| `witdem workflow rebuild` | Rebuild serving projections under maintenance lock |
| `witdem dev` | Run foreground contributor mode |

Common options are `--receiver-port`, `--dashboard-port`, and `--data-dir`.
NPX additionally accepts `--project-name` and `--image`. `--help` on any
command is the authoritative option reference.

CI verifies that these documented command names are present in both launchers'
generated help output.
