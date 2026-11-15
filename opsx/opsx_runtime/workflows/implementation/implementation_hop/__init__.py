"""DI implementation lane: hop-агенты и сборщики subgraph рядом с ``implementation.graph``."""

from __future__ import annotations

from .container import (
    ImplementationHopWorkflowContainer,
    assemble_implementation_hop_agents,
)

__all__ = [
    "ImplementationHopWorkflowContainer",
    "assemble_implementation_hop_agents",
]
