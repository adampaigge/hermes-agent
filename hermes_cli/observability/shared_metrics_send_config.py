"""Shared-metrics transmission is removed from this build: nothing is ever sent."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SendConfig:
    """Resolved transmission settings. Always off, with no destination."""

    enabled: bool
    send: bool
    endpoint: str


def resolve_send_config(config: dict | None = None) -> SendConfig:
    """Always ``SendConfig(enabled=False, send=False, endpoint="")``, whatever ``config`` says."""
    return SendConfig(enabled=False, send=False, endpoint="")
