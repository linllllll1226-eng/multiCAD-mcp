"""
AutoCAD adapter for multiCAD-MCP.

Implements CADInterface for AutoCAD using Windows COM.
Supports AutoCAD, ZWCAD, GstarCAD, and BricsCAD via factory pattern.

Refactored to use mixin classes for better organization and maintainability.
"""

import logging
import threading
from typing import Any, Dict

from core import (
    CADInterface,
    get_cad_config,
)
from mcp_tools.constants import COLOR_MAP

from .mixins import (
    BlockMixin,
    ConnectionMixin,
    DrawingMixin,
    EntityMixin,
    ExportMixin,
    FileMixin,
    LayerMixin,
    ManipulationMixin,
    SelectionMixin,
    SelectionSetManager,
    UtilityMixin,
    ViewMixin,
    com_safe,
    com_session,
)

logger = logging.getLogger(__name__)

# Export helpers for backward compatibility
__all__ = [
    "AutoCADAdapter",
    "com_session",
    "SelectionSetManager",
    "com_safe",
    "COLOR_MAP",
]


class AutoCADAdapter(
    UtilityMixin,
    ConnectionMixin,
    DrawingMixin,
    LayerMixin,
    FileMixin,
    ViewMixin,
    SelectionMixin,
    EntityMixin,
    ManipulationMixin,
    BlockMixin,
    ExportMixin,
    CADInterface,
):
    """Adapter for controlling AutoCAD via COM interface.

    [... docstring truncated for brevity ...]
    """

    def __init__(self, cad_type: str = "autocad", *, manage_com_lifecycle: bool = True):
        """Initialize AutoCAD adapter.

        Args:
            cad_type: Type of CAD (autocad, zwcad, gcad, bricscad).
            manage_com_lifecycle: Initialize and uninitialize COM inside connect/disconnect.
                Dedicated apartment owners such as the dashboard worker set this to False.
        """
        self.cad_type = cad_type.lower()
        self.config = get_cad_config(self.cad_type)
        self.manage_com_lifecycle = bool(manage_com_lifecycle)

        # Thread-local storage for COM objects to prevent cross-thread RPC errors
        self._local = threading.local()

        self._drawing_state: Dict[str, Any] = {
            "entities": [],
            "current_layer": "0",
        }

    @property
    def application(self) -> Any:
        """Get the thread-local application COM proxy."""
        return getattr(self._local, "application", None)

    @application.setter
    def application(self, value: Any):
        """Set the thread-local application COM proxy."""
        self._local.application = value

    @property
    def _com_initialized(self) -> bool:
        """Return whether this adapter initialized COM on the current thread."""
        return bool(getattr(self._local, "com_initialized", False))

    @_com_initialized.setter
    def _com_initialized(self, value: bool) -> None:
        self._local.com_initialized = bool(value)

    @property
    def document(self) -> Any:
        """Get the thread-local document COM proxy."""
        return getattr(self._local, "document", None)

    @document.setter
    def document(self, value: Any):
        """Set the thread-local document COM proxy."""
        self._local.document = value
