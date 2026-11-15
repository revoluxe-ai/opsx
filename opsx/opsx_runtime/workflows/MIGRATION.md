# Workflow DI (pilot)

Colocated `dependency_injector` контейнеры для LangGraph workflow lanes — по образцу
`agents/verify/verify_hop/container.py` и `agents/adapters/<provider>/containers/`.

## Дерево (pilot)

```text
workflows/
  implementation/implementation_hop/
    __init__.py
    container.py          # ImplementationHopWorkflowContainer
  verify/verify_workflow/
    __init__.py
    container.py          # VerifyWorkflowContainer
  main_graph_container/
    __init__.py
    container.py          # MainGraphWorkflowContainer
```

## Контейнеры

| Класс | Путь | Назначение |
|--------|------|------------|
| `ImplementationHopWorkflowContainer` | `workflows/implementation/implementation_hop/` | Six split-APPLY hop factories + `build_implementation_graph` / `build_main_implementation_only_graph` |
| `VerifyWorkflowContainer` | `workflows/verify/verify_workflow/` | `build_verify_graph`; hop via `verify_hop` DependenciesContainer |
| `MainGraphWorkflowContainer` | `workflows/main_graph_container/` | `build_main_graph`; provider adapters, `verify_hop`, `implementation_hop` cross-links |

## Composition root

1. **Bridge / adapters** — provider `BridgeContainer` + `AdaptersContainer`
   (или `opsx.di.MainContainer` фасад). Единственный shared bridge.
2. **Agent hops** — `CommonAgentsContainer`, `VerifyHopContainer` (adapter + `common` link).
3. **Implementation lane** — `ImplementationHopWorkflowContainer` + `assemble_implementation_hop_agents(...)`.
4. **Verify lane** (опционально отдельно) — `VerifyWorkflowContainer` + gate/review `Dependency` overrides.
5. **Parent graph** — `MainGraphWorkflowContainer`: wire `verify_hop`, `implementation_hop_agents`,
   gate-repair + review agents, `repair_adapter`; вызов `build_main_graph()`.

`run_verify_impl` / `run_implement_then_verify_workflow` **не** переведены — checkpoint lifecycle
остаётся в `workflows/main_graph.py`.

## Snippet

```python
from agents.common.container import CommonAgentsContainer
from agents.verify.verify_hop import VerifyHopContainer
from workflows.implementation.implementation_hop import (
    ImplementationHopWorkflowContainer,
    assemble_implementation_hop_agents,
)
from workflows.main_graph_container import MainGraphWorkflowContainer

common = CommonAgentsContainer()
common.adapter.override(apply_adapter)

verify_hop = VerifyHopContainer()
verify_hop.adapter.override(review_adapter)
verify_hop.common.override(common)

impl_lane = ImplementationHopWorkflowContainer()
impl_lane.adapter.override(apply_adapter)
hop_agents = assemble_implementation_hop_agents(
    apply_design=impl_lane.apply_design_agent(),
    apply_design_review=impl_lane.apply_design_review_agent(),
    apply_scaffold=impl_lane.apply_scaffold_agent(),
    apply_red=impl_lane.apply_red_agent(),
    apply_implement=impl_lane.apply_implement_agent(),
    apply_green=impl_lane.apply_green_agent(),
)

parent = MainGraphWorkflowContainer()
parent.verify_hop.override(verify_hop)
parent.implementation_hop_agents.override(hop_agents)
parent.repair_adapter.override(apply_adapter)
# parent.group_error_agent.override(...)  # и остальные gate/review Dependency

compiled_parent = parent.build_main_graph()
```
