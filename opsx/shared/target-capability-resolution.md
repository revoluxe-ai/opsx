# Target capability resolution

This contract has higher precedence than inherited donor-specific realization details in transferred commands.

Before executing any product-specific path, tool, build, test, typecheck, browser, deploy, service, or package-manager instruction:

1. Read `opsx/project/capabilities.yaml`.
2. Confirm the capability is resolved and consistent with current repository facts.
3. Use the resolved target value, not a donor literal.
4. If unresolved, fail closed with `TARGET_CAPABILITY_UNRESOLVED`.
5. Do not infer a product stack from the OPSX source repository.

The following donor literals are never target authority by themselves:

- `adv-bot`, `WB Sky`
- `poetry`, `pyproject.toml`
- `app/**`, `tests/**`
- `npm`, `package.json`, `src/**`, `public/**`

They may remain as historical text or compatibility examples, but execution must resolve the corresponding TARGET capability first.
