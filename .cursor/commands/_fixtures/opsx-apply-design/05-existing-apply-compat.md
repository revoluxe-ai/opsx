# Fixture 05 — existing `/opsx-apply` remains operational

## Given

`/opsx-apply-design` is installed. Owner runs current `/opsx-apply change=<id>`
on a change that has **no** `apply-design/` artifact.

## Expected

```text
APPLY_REQUIRES_APPLY_DESIGN_READY: no
APPLY_RUNTIME_CLASS: ACTIVE
APPLY_STILL_IMPLEMENTS_TASKS: yes
APPLY_DESIGN_OPTIONAL: yes
```

`/opsx-apply.md` MUST still describe implementing tasks from an OpenSpec
change. It MUST NOT contain a hard gate that refuses production writes solely
because `APPLY_DESIGN_READY` is absent.

This fixture is a contract assertion against command text + activation row;
it does not execute apply.
