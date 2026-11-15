# Adapter: project-generic

## Purpose

Provide a stack-agnostic realization of OPSX for a greenfield project whose product stack may still be unresolved.

This adapter separates OPSX availability from product capability availability.

## Canonical capability source

`opsx/project/capabilities.yaml`

Every product-specific command must resolve the capability it needs from that file and current repository facts before executing a product command.

## Rules

1. A missing product capability does not make OPSX globally unavailable.
2. A command that requires an unresolved capability MUST fail closed locally.
3. Literal donor paths or package-manager commands embedded in inherited command text are non-authoritative compatibility examples.
4. Never execute a donor literal unless it matches the resolved target capability.
5. Product roots, test roots, package manager, build, test, typecheck, browser, deploy and service commands are target capabilities, not hard-coded OPSX truths.
6. Runtime/control-plane capabilities may be installed independently from product capabilities when their authority and runtime are isolated.

## Required failure contract

When a required capability is unresolved:

STATUS: FAIL_CLOSED
REASON: TARGET_CAPABILITY_UNRESOLVED
CAPABILITY: <id>
NEXT_ACTION: define or discover the capability in opsx/project/capabilities.yaml

## Authority

This adapter may read `.project/**` and repository facts. It may update only `opsx/project/capabilities.yaml` through explicit adaptation/projectization flows. It never rewrites `.project/**`.
