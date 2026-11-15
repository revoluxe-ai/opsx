# Fixture 16 — focused/adjacent checks remain bounded

## Given

After frozen RED PASS, GREEN derives adjacent checks from the slice.

## Expected

```text
ALLOWED: node --test tests/unit/<directly related file>
FORBIDDEN: node --test tests/unit/** | npm test | bug hunt | Bugbot | /opsx-verify | browser matrix
```
