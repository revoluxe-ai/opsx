"""Provider-neutral capability manifest для model hops."""

from agents.capabilities.errors import (
    AgentContractFailure,
    MachineGateFailure,
    ProviderTransportFailure,
    ToolCapabilityViolation,
)
from agents.capabilities.manifest import (
    AgentCapabilityManifest,
    DesignSubmission,
    PriorDesignDescriptor,
    ReadDomain,
    SourceDescriptor,
)

__all__ = [
    "AgentCapabilityManifest",
    "AgentContractFailure",
    "DesignSubmission",
    "MachineGateFailure",
    "PriorDesignDescriptor",
    "ProviderTransportFailure",
    "ReadDomain",
    "SourceDescriptor",
    "ToolCapabilityViolation",
]
