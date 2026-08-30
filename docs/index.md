# Understand how your AI workflow actually behaves

Witdem is local-first analytics for AI agents and multi-step AI applications. It connects runtime telemetry to the application outcomes you care about: which paths ran, what they cost, where they failed, and whether they achieved the product goal declared in YAML.

## Choose your launcher

=== "NPX and Docker"

    ```bash
    npx -y witdem@latest up
    ```

=== "pipx and native Python"

    ```bash
    pipx install witdem-analytics
    witdem up
    ```

Both paths start the receiver, ELT worker, and dashboard, wait for health, and preserve local data across restarts.

[Start with your first observed execution](getting-started.md){ .md-button .md-button--primary }
[Define a product goal](contract-tutorial.md){ .md-button }

## What Witdem gives you

- Actual execution paths, branches, loops, retries, tools, model calls, and failures
- Per-run latency, token usage, and attributable cost
- Product-goal outcomes declared by your application, rather than inferred from a successful model call
- Model, provider, step, and workflow comparisons
- Local DuckDB storage and a self-hosted dashboard

Prompt and response content capture is disabled by default. Applications remain minimally integrated: instrumentation observes runtime behavior, while YAML defines business meaning and workflow structure.

## Where to go next

- [Instrument an existing application](sdk.md)
- [Choose a framework integration](integrations/native-python.md)
- [Declare and compile a workflow](workflow-replay.md)
- [Operate, back up, and recover Witdem](operations.md)
- [Upgrade safely](upgrade.md)
