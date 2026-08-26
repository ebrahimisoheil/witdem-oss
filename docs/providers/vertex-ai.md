# Google Vertex AI

**Status: experimental provider path with a standalone tutorial.**

Witdem has no dedicated `google-genai` proxy. Use standard GenAI OpenTelemetry attributes or the generic callable wrapper. Haystack components containing Google or Gemini identity are recognized as provider `google`.

## Run the tutorial

```bash
cd examples/cloud/vertex
cp .env.example .env
uv sync
uv run python sdk_enriched.py
```

Configure Application Default Credentials plus:

```text
GOOGLE_CLOUD_PROJECT=your-project
GOOGLE_CLOUD_LOCATION=us-central1
VERTEX_MODEL=gemini-2.0-flash-001
WITDEM_ENDPOINT=http://localhost:4318
```

The tutorial observes Vertex response model and prompt/candidate token counts.

## Cost coverage

The bundled catalog includes standard text-token rates for selected Gemini model IDs, including `gemini-2.0-flash-001`. Long-context thresholds, regional or priority inference, media-specific rates, grounding, and cache storage are outside the current schema. Use provider-reported money or a deployment-specific catalog when any of those apply.
