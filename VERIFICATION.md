# Extraction verification

Source: `Geovip647/stroy-system`, local branch `opsx-implement`, revision
`94e5d7b1ecb3e83586c168fad570c2cba69792b5`. The donor checkout is unchanged.

## Verified without a model

- 16 distribution checks on Linux, Python 3.12: real Git submodules, independent
  consumers, repeat install, recovery after clone, upgrade rollback, conflicts,
  preservation of settings and receipts, uninstall, real HTTP start/stop,
  exclusive execution and release after process exit, managed-path containment.
- 73 core contract checks passed; 3 donor-product-dependent cases skipped.
  Run `python scripts/test_core_contracts.py` to reproduce the selected suite.
- Python compilation and all Node tool syntax checks passed.
- CLI wheel builds; the bootstrap CLI has no required model-provider dependency.
- The GitHub workflow runs distribution and core-contract checks on Linux and
  Windows. A submitted workflow is not itself evidence of a successful run.

The tests do not authorize a live model invocation or claim product SECURITY
PASS. Installing and adopting adv-bot is the next integration step.

## Inherited failures, not hidden

The donor test module
`agents/adapters/cursor_sdk/tests/test_abc_agent_stream_resume.py` has four
failures both before and after extraction, using the same installed dependencies:

- `test_stream_sink_logs_via_logger`: expects DEBUG while the implementation logs INFO.
- `test_run_turn_stream_drop_then_run_not_found_fresh_launch`: expects a fresh send
  although the adapter correctly refuses replay of an unknown run.
- `test_run_turn_one_stream_resume_then_success`: tries to instantiate a typing Any alias.
- `test_run_turn_stream_resume_cap_reraises`: expects a raw transport exception
  while the implementation returns the existing explicit workflow stop.

These failures were reproduced from the untouched source export in a separate
checkout. They are not fixed by weakening runtime behavior or changing the test
expectations in this extraction.

## Ownership and exclusions

Useful product artifacts stay in the consumer's `openspec/`; receipts, settings
and run data stay in that consumer's `opsx/`. The core does not carry the donor's
live changes, product tests, credentials, conversations or runtime databases.
Historical product conversations, pinned product-source snapshots and operational
adoption authorizers are explicitly excluded in `SOURCE_EXPORT.json`.

Legacy command resources are preserved for gradual migration. The stroy-system
cover integration is isolated under `opsx/integrations/stroy_system/` and disabled
unless the project explicitly selects it. New consumers start NEEDS_ADOPTION;
that is a setup requirement, not a passing project validation.
