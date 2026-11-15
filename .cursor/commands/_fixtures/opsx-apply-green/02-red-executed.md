# Fixture 02 — frozen RED tests are executed

## Given

Eligible SLICE-001 with frozen `test_commands`.

## Expected

```text
EXECUTION: runFrozenRedCommand / exact RED test_commands
NOT: agent claim of PASS
CAPTURE: command, exit_code, bounded output, timestamp
```
