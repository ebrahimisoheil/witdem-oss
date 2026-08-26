# Amazon Bedrock

**Status: experimental provider path with a standalone tutorial.**

Witdem has no dedicated boto3/Bedrock proxy. Use standard GenAI OpenTelemetry attributes or wrap the existing provider function with `generic.instrument`.

## Run the tutorial

```bash
cd examples/cloud/bedrock
cp .env.example .env
uv sync
uv run python sdk_enriched.py
```

Configure AWS credentials through the normal AWS chain or the example variables:

```text
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
BEDROCK_MODEL=amazon.nova-lite-v1:0
WITDEM_ENDPOINT=http://localhost:4318
```

The tutorial observes `inputTokens` and `outputTokens` returned by `bedrock-runtime.converse`.

## Cost coverage

The bundled catalog includes standard rates for selected Amazon Nova, Cohere Command, and Mistral model IDs, including the tutorial's `amazon.nova-lite-v1:0`. Bedrock pricing can vary by model, region, inference tier, and throughput mode. Unlisted IDs remain unavailable; use provider-reported money or a deployment-specific `WITDEM_PRICING_FILE` rather than aliasing them to a possibly different rate.
