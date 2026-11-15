# OPSX target tools

## Capabilities export (W1)

Authoritative source: `opsx/project/capabilities.yaml`.

```bash
poetry run python tools/opsx/capabilities_export.py export   # regenerate JSON
poetry run python tools/opsx/capabilities_export.py verify     # fail on drift
```

CI: `.github/workflows/opsx-target-config-ci.yml`.

After editing YAML, always run `export` then commit both files.
