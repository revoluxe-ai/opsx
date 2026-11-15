# OpenSpec CLI provider (TARGET-local)

Pinned Node environment for `@fission-ai/openspec@1.14.1`. Not a production app dependency.

## Bootstrap (explicit network)

```bash
opsx_tools/openspec-cli-node/setup.sh
```

Uses `npm ci` against committed `package-lock.json`. Do **not** invoke setup from ordinary `/opsx-*` or wrapper runs.

## Canonical invocation

From repository root:

```bash
opsx_tools/openspec-cli list --json
opsx_tools/openspec-cli status --change "<name>" --json
```

Fail-closed: no `npx`, no global `openspec` on PATH, no silent fallback.

## Integrity

- `provider.manifest.json` — auditable pin metadata
- `package.json` / `package-lock.json` — exact version and npm integrity hashes
