---
name: /opsx-admin-command-commit
id: opsx-admin-command-commit
category: Workflow
description: "Deprecated alias — use /opsx-meta-commit for all process-surface commits"
runtime_class: REDIRECT
---

# /opsx-admin-command-commit

**Deprecated.** Sole process-commit front-door is **`/opsx-meta-commit`**
(allowlist SoT: `opsx/sources.yaml`).

Do not implement a second commit contract here. When this command is PRIMARY or
referenced by redirect text, treat as:

```text
STATUS: OUT_OF_SCOPE_REDIRECT
NEXT_ACTION: /opsx-meta-commit ALL GO
```

For mixed product + process trees after a session, use **`/opsx-commit-all ALL GO`**
instead of invoking this alias.
