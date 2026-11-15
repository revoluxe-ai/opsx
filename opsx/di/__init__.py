"""DI-слой OPSX на dependency-injector (composition root verify-impl)."""

from opsx.di.main_container import ApplicationContainer, BridgeHandle, MainContainer
from opsx.di.types import VerifyImplExecuteContext

__all__ = [
    "ApplicationContainer",
    "BridgeHandle",
    "MainContainer",
    "VerifyImplExecuteContext",
]
