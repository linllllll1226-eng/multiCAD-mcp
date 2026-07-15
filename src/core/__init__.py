"""
Core modules for multiCAD-MCP.

Provides configuration, exception handling, and abstract interfaces.
"""

from .cad_interface import (
    CADInterface,
    Color,
    Coordinate,
    LineWeight,
    Point,
)
from .config import (
    CADConfig,
    ConfigManager,
    OutputConfig,
    ServerConfig,
    get_cad_config,
    get_config,
    get_supported_cads,
)
from .exceptions import (
    CADConnectionError,
    CADNotSupportedError,
    CADOperationError,
    ColorError,
    ConfigError,
    CoordinateError,
    InvalidParameterError,
    LayerError,
    MultiCADError,
)

__all__ = [
    # Config
    "ConfigManager",
    "ServerConfig",
    "CADConfig",
    "OutputConfig",
    "get_config",
    "get_cad_config",
    "get_supported_cads",
    # Exceptions
    "MultiCADError",
    "CADConnectionError",
    "CADOperationError",
    "InvalidParameterError",
    "CoordinateError",
    "ColorError",
    "LayerError",
    "CADNotSupportedError",
    "ConfigError",
    # Interfaces
    "CADInterface",
    "LineWeight",
    "Color",
    "Coordinate",
    "Point",
]
