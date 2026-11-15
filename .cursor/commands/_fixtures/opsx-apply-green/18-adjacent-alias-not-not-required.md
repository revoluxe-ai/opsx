# Fixture 18 — relevant alias failure is not NOT_REQUIRED

## Given

A slice-relevant adjacent unit test exists. Process fixture:

```text
.cursor/commands/_fixtures/opsx-apply-green/alias-dependent-fixture.test.mjs
```

Plain:

```text
node --test .cursor/commands/_fixtures/opsx-apply-green/alias-dependent-fixture.test.mjs
```

fails with `ERR_MODULE_NOT_FOUND` / `Cannot find package '@/…'`.

The repository already has `tests/unit/softProdAliasLoader.mjs`.

## Expected

```text
applicability: REQUIRED (compositionSeamHints / surface overlap)
execution: REQUIRED_BUT_NOT_EXECUTABLE unless the supported loader is used
adjacent_final_status: NOT PASS-by-waiver
NOT_REQUIRED: forbidden (launcher failure is not semantic inapplicability)
GREEN_PROVEN: forbidden until the check runs via runBoundedGreenCheck
```

Alias-dependent fixture + supported loader → execute (`node_test_with_alias_loader`).
