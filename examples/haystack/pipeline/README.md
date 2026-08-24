# Haystack parallel branches

This example answers the “both nodes at the beginning” question with an observed concurrent execution:

```text
                    ┌─ keyword_retriever ─┐
execution → pipeline┤                     ├→ answer
                    └─ semantic_retriever ┘
```

Both retrievers are root components. They receive the same query and Haystack's `AsyncPipeline` schedules them concurrently with `concurrency_limit=2`. The `answer` component waits for both outputs, then calls OpenAI using Haystack's `OpenAIChatGenerator`. Witdem uses Haystack's native OpenTelemetry spans, so the two overlapping sibling components appear as parallel nodes in the replay.

## Run it

Start Witdem from the repository root:

```bash
witdem dev
```

Then, in another terminal:

```bash
cd examples/haystack/pipeline
cp .env.example .env
# Add OPENAI_API_KEY to .env
uv sync
uv pip install "../../../witdem-sdk[haystack]"
uv run --no-sync python sdk_enriched.py
```

Open the printed run in the Witdem dashboard. The replay should show `keyword_retriever` and `semantic_retriever` as sibling branches that converge on `answer`.
