# Azure OpenAI

**Status: experimental provider path with a standalone tutorial.**

Azure OpenAI can emit standard GenAI OpenTelemetry attributes or use the generic callable wrapper. Haystack generator identity containing `AzureOpenAI` is recognized as OpenAI.

## Run the tutorial

```bash
cd examples/cloud/azure
cp .env.example .env
uv sync
uv run python sdk_enriched.py
```

Configure:

```text
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_ENDPOINT=https://YOUR-RESOURCE.openai.azure.com
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_OPENAI_DEPLOYMENT=YOUR-DEPLOYMENT
WITDEM_ENDPOINT=http://localhost:4318
```

The tutorial wraps one existing `AzureOpenAI.chat.completions.create` workload with `generic.instrument(provider="azure.openai", model=deployment)`.

`azure.openai`, `azure_openai`, and `azure` normalize to the distinct `azure_openai` provider. Witdem does not reuse direct OpenAI rates. The bundled catalog includes selected Azure model IDs; custom deployment names and negotiated or region-specific rates belong in `WITDEM_PRICING_FILE`.

## Cost limitation

Azure deployment names do not necessarily equal public OpenAI model names. Witdem cannot safely price a deployment from its name alone. Cost is measured only when the observed response model matches the catalog, the provider reports money, or you configure a catalog entry for the deployment/model evidence you emit.
