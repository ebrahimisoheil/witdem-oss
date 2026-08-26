# Ollama

**Status: experimental local-provider path with a standalone tutorial.**

Ollama does not need a cloud API key. Witdem wraps the existing call and records local model identity plus prompt/output evaluation counts when returned.

## Run the tutorial

Start Ollama and pull the selected model, then:

```bash
cd examples/ollama/basic
cp .env.example .env
uv sync
uv run python sdk_enriched.py
```

```text
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.2
WITDEM_ENDPOINT=http://localhost:4318
```

## Cost limitation

Witdem can compare local latency and tokens. The bundled catalog does not assign API spend to Ollama models, so cost remains unavailable unless your application explicitly reports an internal cost or you provide a custom catalog.
