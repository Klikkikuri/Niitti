"""
Niitti 🪡

Shared package for logging, OpenTelemetry tracing, Sentry, and configuration models.
"""

from niitti.logging import get_logger, setup_logging
from niitti.settings import LoggingSettings, Settings, SettingsProxy, TelemetrySettings
from niitti.tracing import flush_tracing, setup_tracing, shutdown_tracing

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
