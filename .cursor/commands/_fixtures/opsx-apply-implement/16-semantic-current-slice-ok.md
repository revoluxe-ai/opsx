# 16 — Authorized path + current-slice behavior → allowed

Theme: semantic scope (not path-alone).

Given:

- path is in `expected_product_write_surfaces`
- candidate implements only current-slice `required_behavior` /
  `responsibility` / `completion_contract`

Expected:

```text
classifySemanticSliceScope → ok: true
BLOCKED_SCOPE_EXPANSION: no
```

Structural host-adapter registration may remain without filling later-slice
gate/apply semantics.
