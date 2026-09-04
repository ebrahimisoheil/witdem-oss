# Witdem documentation

Instrument your AI application, define the product result you expect, and investigate what each run actually delivered.

[Get started](getting-started.md){ .md-button .md-button--primary }
[API reference](api/index.md){ .md-button }

## Start here

<div class="docs-card-grid">

<a class="docs-card" href="getting-started/">
  <span class="docs-card__label">SETUP</span>
  <h3>Install Witdem</h3>
  <p>Start the services and connect your application with the Python SDK.</p>
  <strong>Open guide →</strong>
</a>

<a class="docs-card" href="contract-tutorial/">
  <span class="docs-card__label">CONFIGURATION</span>
  <h3>Define a contract</h3>
  <p>Create the YAML definitions that connect runtime evidence to product goals.</p>
  <strong>Open guide →</strong>
</a>

<a class="docs-card" href="integrations/haystack/">
  <span class="docs-card__label">INTEGRATION</span>
  <h3>Connect Haystack</h3>
  <p>Add Witdem to an existing Haystack pipeline without changing orchestration ownership.</p>
  <strong>Open guide →</strong>
</a>

</div>

## Run Witdem

=== "Docker with npx"

    ```bash
    npx -y witdem@latest up
    ```

=== "Native Python"

    ```bash
    pipx install witdem-analytics
    witdem up
    ```

Then add the SDK and initialize the `.witdem` configuration:

```bash
python -m pip install "witdem-sdk[haystack]"
witdem-sdk init
```

## Reference

The [Python SDK](api/python-sdk.md), [ingestion API](api/ingestion.md), and [analytics API](api/analytics.md) are documented from the implementation. The HTTP schemas are generated from Witdem's FastAPI applications.
