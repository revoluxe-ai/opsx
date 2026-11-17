# OPSX bootstrap profile — greenfield target

## Purpose

Seed only the minimum process plane required to understand and projectize a new repository safely.

For `stroy-system`, bootstrap does NOT authorize copying donor DEFAULT_INSTALL or the full donor command surface before projectization.

## CORE_REQUIRED

- `.cursor/commands/opsx-meta-bootstrap.md`
- `.cursor/commands/opsx-meta-import-command.md`
- `.cursor/commands/opsx-projectize.md`
- `opsx/opsx.mdc`
- `.cursor/commands/opsx-meta.md`
- `opsx/shared/bootstrap-profile.md`
- `opsx/shared/meta-orientation.md`
- `opsx/shared/command-discovery.md`
- `opsx/shared/lifecycle.md`
- `opsx/shared/evidence-contract.md`
- `opsx/sources.yaml`
- `opsx/projectization.yaml`
- `opsx/lock.yaml`
- `opsx/manifest.yaml`
- `opsx_tools/registry/command_activation.ndjson`
- `opsx_tools/registry/provenance.ndjson`
- `opsx/project/manual_review.md`
- `opsx/adapters/opsx-lite/README.md`

## Greenfield rule

Before projectization COMPLETE:
- install CORE only;
- do not import donor stack adapters;
- do not activate normal product mutation commands;
- do not install Temporal merely because the donor has it;
- do not copy donor product OpenSpec/runtime trees;
- derive the next install set from `.project/PROJECT_PROFILE.yaml` plus actual repo facts.

After projectization, additional OPSX capabilities may be reconciled/materialized under their own evidence.