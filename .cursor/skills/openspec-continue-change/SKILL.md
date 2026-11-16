# PROJECT TARGET PROFILE — REQUIRED

Product-specific execution authority resolves only through:

  opsx/project/capabilities.yaml

Adapter-specific supplements apply only when that adapter is selected in
capabilities.yaml (see opsx/adapters/<adapter-id>/).

Donor literals in this command are historical compatibility text, not authority.
Before product-specific reads, writes, or commands, resolve the required capability.
If unresolved, fail closed with TARGET_CAPABILITY_UNRESOLVED.

Control-plane paths under tools/temporal and tools/cursor_executor are separate
from product runtime adoption.


---
name: openspec-continue-change
description: Continue working on an OpenSpec change by creating the next artifact. Use when the user wants to progress their change, create the next artifact, or continue their workflow.
allowed-tools: Bash(opsx/tools/openspec-cli:*)
license: MIT
compatibility: Requires TARGET-local OpenSpec CLI at `opsx_tools/openspec-cli`.
metadata:
  author: openspec
  version: "1.0"
  generatedBy: "1.6.0"
---

