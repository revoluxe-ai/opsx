# OPSX dependency closure v0

Machine-checkable closure for activated OPSX commands binds to HEAD identity
(activation hashes, config/checker blobs, capabilities, predicate version).

## Run

```bash
python .cursor/pipeline/control-plane-tests/run_command_transfer_contract_tests.py
```

Stdout is JSON (`schema_version: opsx-dependency-closure-v0`). Exit `0` only
when `status` is `PASS`.

## Semantics

- Global `FAIL` closes projectization (`CLOSURE_BLOCKED`); `COMPLETE` cannot
  stand while mandatory dependencies are missing or foreign donor preconditions
  remain unconditional in active commands.
- Unresolved product capabilities block only mapped commands (`command_blocks`).
- Verifier is read-only; it does not edit `openspec/config.yaml` or commands.

Predicate version: `v0.1.4` (see `opsx_dependency_closure.py` and
`dependency_closure_predicate_version` in `opsx/projectization.yaml`).

## OpenSpec CLI provider

Canonical invocation: `opsx_tools/openspec-cli` (pinned
`@fission-ai/openspec@1.14.1` under `opsx_tools/openspec-cli-node/`).
Bootstrap only: `opsx_tools/openspec-cli-node/setup.sh` (explicit network).
Ordinary closure/openspec commands must not auto-install.

## OpenSpec guard (portable pre-write)

Active planning commands cite `python tools/openspec/openspec_guard.py
--scenario <opsx_continue|opsx_ff|opsx_sync|opsx_update> --strict`. Policy:
`tools/openspec/openspec_guard.yaml` (TARGET-sized thresholds; not donor copy).
Semantic config contract remains `openspec/tools/check_config_contract.py`.
