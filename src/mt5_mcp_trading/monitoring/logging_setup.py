"""
Minimal structured logging setup. "Structured" here means: consistent format, timestamps,
levels, and logger names everywhere, via the stdlib logging module — not a hand-rolled
print()-based scheme like the legacy project used. A move to JSON/correlation-ID logging can
happen later once there is an actual pipeline run to correlate.

Never log secrets: callers must not pass Settings (or any field with repr=False) straight into
a log message. This module does not attempt to redact after the fact — it relies on
config.settings.Settings never exposing secret fields via repr/str in the first place.
"""

from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    """Idempotent: safe to call more than once (e.g. once per test), only configures once."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
