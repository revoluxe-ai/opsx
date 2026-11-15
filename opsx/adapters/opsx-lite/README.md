# Adapter: opsx-lite

Minimal stack-agnostic OPSX process-plane adapter.

This adapter authorizes only bootstrap, orientation, projectization and bounded process metadata needed to establish the target project profile.

It does not define product language/framework, package manager, source roots, test runner, database/cache/queue implementation, deployment runtime, or Temporal requirement.

Those are derived from `.project/**` and later Owner decisions.