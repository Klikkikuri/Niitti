"""
Niitti 🪡

Shared package for logging, OpenTelemetry tracing, Sentry, and configuration models.

The exports below load on first use, not at import time. This keeps `niitti.paths` usable from a package that has
only `platformdirs` installed, without structlog, pydantic or the OpenTelemetry stack.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from niitti.logging import get_logger, setup_logging
    from niitti.settings import (
        LoggingSettings,
        Settings,
        SettingsProxy,
        TelemetrySettings,
    )
    from niitti.tracing import flush_tracing, setup_tracing, shutdown_tracing

_LAZY_EXPORTS: dict[str, str] = {
    "Settings": "niitti.settings",
    "SettingsProxy": "niitti.settings",
    "LoggingSettings": "niitti.settings",
    "TelemetrySettings": "niitti.settings",
    "setup_logging": "niitti.logging",
    "get_logger": "niitti.logging",
    "setup_tracing": "niitti.tracing",
    "flush_tracing": "niitti.tracing",
    "shutdown_tracing": "niitti.tracing",
}

__all__ = [
    "Settings",
    "SettingsProxy",
    "LoggingSettings",
    "TelemetrySettings",
    "setup_logging",
    "get_logger",
    "setup_tracing",
    "flush_tracing",
    "shutdown_tracing",
]


def __getattr__(name: str) -> Any:
    """
    Resolve a top-level export from its subpackage on first use.

    :param name: Attribute name.
    :return: The requested object.
    :raises AttributeError: The name is not a niitti export.
    """
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(import_module(module_name), name)
    globals()[name] = value  # Cache it, so later reads skip this function.
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
