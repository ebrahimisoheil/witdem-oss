# Troubleshooting

Start with service health:

```bash
docker compose ps
curl -f http://localhost:4318/readiness
curl -f http://localhost:8501/health
docker compose logs --tail=200 witdem elt-worker dashboard
```

## No runs appear

**Likely causes:** wrong endpoint, receiver unavailable, authentication mismatch, no instrumentation, exporter not flushed, or ELT has not completed.

1. Confirm the application sees the expected value:

   ```bash
   printf '%s\n' "${WITDEM_ENDPOINT:-not set}"
   curl -f "${WITDEM_ENDPOINT:-http://localhost:4318}/readiness"
   ```

2. If `WITDEM_API_KEY` protects the receiver, use the same key in the application.
3. Use a high-level integration wrapper or explicitly open `witdem.execution(...)`.
4. Let the wrapper/context manager close normally. For a long-lived low-level client, call `witdem.flush()` and inspect `witdem.delivery_status()` before process exit.
5. Check `uv run witdem elt status` or the `elt-worker` logs.

## The root run appears but child steps do not

**Likely cause:** only a root span was exported, the framework callback was not attached, or the application uses an opaque custom component.

- Confirm the correct wrapper: `haystack.instrument(pipeline)`, `langgraph.instrument(compiled_graph)`, or `langchain.instrument(runnable)`.
- Do not wrap only a function that calls a framework unless that integration explicitly accepts functions.
- Inspect the run's **Technical records** for instrumentation scope and runtime metadata.
- For custom calls, use `witdem.operation`, `witdem.model`, or `witdem.tool` around the physical boundary.

## Token usage is missing

**Likely cause:** the provider/framework did not expose usage or a callback adapter could not read its response shape.

- Check the provider's response directly for input/output token fields.
- For the generic wrapper, pass `observe_result=` and map the real fields.
- For Haystack, ensure the generator returns provider metadata on its reply and is not hidden inside an opaque component.
- Do not estimate tokens in documentation or application glue merely to fill the dashboard.

## Cost is not measured

Cost requires provider-reported money or all of: provider, model, token usage, and a matching catalog entry.

Inspect the run for provider, model, input/output tokens, and cost-unavailable reason. Compare the model string with [`src/witdem/pricing/catalog.yaml`](../src/witdem/pricing/catalog.yaml). Configure `WITDEM_PRICING_FILE` for other models or negotiated rates.

Azure deployment names, Bedrock models, Vertex models, and Ollama models are not priced by the bundled catalog in this release.

## Haystack component or model not captured

- The high-level integration requires `haystack-ai>=3.0,<4`; Haystack 2 fails explicitly.
- Ensure the component actually executed; configured-but-unused components are intentionally absent.
- A custom generator should expose model and usage response metadata or create standard GenAI attributes on its active span.
- Do not install a second global Haystack tracer after `instrument(...)`; it can replace the tracer Witdem enabled.

## LangGraph branch or subgraph is missing

- The replay contains executed branches, not every static edge.
- Confirm the compiled graph—not the builder—was passed to `instrument`.
- Ensure your invocation did not replace the `callbacks` entry after the wrapper appended its handler.
- Some LangGraph versions expose fewer subgraph/edge fields. Inspect technical records for `langgraph_*`, namespace, task, `send_to`, interrupt, or retry metadata.

## YAML configuration is not detected

Run from the application directory:

```bash
witdem validate
```

The SDK searches the current directory and parents for `.witdem/witdem.yaml`. For workers launched elsewhere:

```bash
export WITDEM_CONFIG=/absolute/path/to/.witdem/witdem.yaml
witdem validate --config "$WITDEM_CONFIG"
```

If a path such as `$.answer` is wrong, validation still succeeds because the returned value is only available at runtime. Inspect the actual JSON-shaped result and correct the path.

## Runtime succeeds but product goal fails

This can be correct: execution health and product success are separate. Inspect artifact validity, expected/observed decision, required-path/evidence flags, and closest blocker. Fix the application or contract mapping only if those facts misrepresent the returned result.

## Async or streaming runs are incomplete

- Await `run_async`/`ainvoke` fully.
- Consume `run_async_generator`/`astream` to completion or close it cleanly.
- An abandoned stream has no authoritative final result, so Witdem records runtime telemetry without YAML contract completion.
- For manually managed tasks, keep the active OpenTelemetry context when spawning work so parent/child identity is preserved.

## Dashboard is unavailable or stale

```bash
curl -f http://localhost:8501/health
curl -f http://localhost:8501/api/v1/meta
docker compose logs --tail=200 dashboard elt-worker
```

Use the dashboard's global refresh after the ELT worker marks a new execution ready. If the API is healthy but assets look stale after source changes, rebuild the dashboard image:

```bash
docker compose up -d --build dashboard
```

## Dependency conflict

Use a clean virtual environment and install one framework extra. In particular, Witdem's high-level Haystack wrapper requires Haystack 3:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e "/path/to/Witdem-Analytics/witdem-sdk[haystack]"
python -m pip check
```

Do not force incompatible framework versions past the declared constraints. The SDK package has no dependency on the Witdem analytics server package.

## Authentication failures

An HTTP `401` means the receiver expects a bearer key. Set the same `WITDEM_API_KEY` in the receiver and application. Do not put it in `witdem.yaml`, logs, examples, or committed `.env` files.
