"""The one shared-metrics consent answer, and the terminal's first-run offer.

Every surface reads and writes the same two keys in the profile's config.yaml:
``telemetry.shared_metrics.enabled`` (collect) and ``.send`` (upload daily). The offer has two
answers, and each sets both keys: "Share with Nous" turns both on, "No thanks" turns both off. The
question counts as answered once either key is written explicitly; the shipped defaults are not an
answer. Every answer also writes ``offer_version``. A "No thanks" without it predates the type-ahead
fix (an Enter pressed during startup could save it unseen), so that profile is asked once more, with
the reason, and any response (Esc or dismiss included) settles it for good. A collect-only answer
from before the offer dropped that choice stays decided: nothing is sent, and it is not asked again.
Desktop paints the offer as a composer strip (``consent-strip.tsx`` over the ``shared_metrics.*``
RPCs). The terminal asks at the end of every ``hermes setup`` flow and, for a profile that never
answered, once before an interactive ``hermes`` / ``hermes --tui`` chat (which also covers the
dashboard's Chat tab, a PTY-hosted TUI). The messaging gateway never asks: a chat participant is
not the install owner.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)

# (label, enabled, send): the offer's two answers, in the order every surface shows them.
OFFER_CHOICES = (
    ("Share with Nous", True, True),
    ("No thanks", False, False),
)
_NO_THANKS = len(OFFER_CHOICES) - 1
DOCS_URL = "https://hermes-agent.nousresearch.com/docs/developer-guide/relay-shared-metrics"
_OFFER_DESCRIPTION = "\n".join((
    "Shared metrics are bounded counters: activity, outcomes, error classes (with a",
    "fixed-list reason when a memory write, compression, update or install fails),",
    "model routes, token totals, feature use and coarse machine facts. Never prompts,",
    "files, paths, setting values or error text. Sharing sends them to Nous once a",
    "day. Apart from a one-time install note, data from before you opt in is never",
    "sent.",
    f"Details: {DOCS_URL}",
    "Change it any time: hermes setup telemetry",
))
_REASK_NOTE = "Asking once more: an earlier version could save \"No thanks\" before you saw this question."


def _section(cfg: Any) -> dict:
    telemetry = cfg.get("telemetry") if isinstance(cfg, dict) else None
    section = telemetry.get("shared_metrics") if isinstance(telemetry, dict) else None
    return section if isinstance(section, dict) else {}


# Bumped only when every earlier answer must be asked again; 2 = after the type-ahead fix.
OFFER_VERSION = 2


def consent_state(raw_cfg: Any) -> dict:
    """``{enabled, send, decided, reask}`` from a RAW config mapping (no defaults merged). ``reask``:
    an "off" answer from before ``OFFER_VERSION`` that may never have been seen; it reads undecided
    so every surface offers it once more. An opt-in was always a deliberate pick and stays, including
    a collect-only answer from before the offer dropped that choice."""
    # Removed from this build: always decided and off, so no surface offers or shows sharing.
    return {"enabled": False, "send": False, "decided": True, "reask": False}


def _current_state() -> dict:
    from hermes_cli.config import read_raw_config

    return consent_state(read_raw_config())


def set_answer(target: dict, enabled: bool, send: bool) -> None:
    """Write one answer into a config mapping: both opt-ins plus the offer version that settles it."""
    telemetry = target.get("telemetry")
    if not isinstance(telemetry, dict):
        telemetry = target["telemetry"] = {}
    section = telemetry.get("shared_metrics")
    if not isinstance(section, dict):
        section = telemetry["shared_metrics"] = {}
    # Shared-metrics collection and upload are removed from this build: every answer is "off".
    section["enabled"], section["send"] = False, False
    section["offer_version"] = OFFER_VERSION


def save_consent(enabled: bool, send: bool, config: dict | None = None) -> None:
    """Write both keys into the user's raw config.yaml now. ``save_config`` strips values equal
    to the shipped defaults, so a "No thanks" (both false) saved through it vanished and the
    profile was asked again on every surface. ``config`` (a caller's in-memory copy, e.g. the
    wizard's) gets the same answer so its later save agrees. Sending cannot outlive collection."""
    from hermes_cli.config import (
        _write_user_config, get_config_path, is_managed, managed_error, require_readable_config_before_write,
    )
    from hermes_cli.setup import _record_send_consent_change

    if is_managed():
        managed_error("save configuration")
        return
    send = enabled and send
    config_path = get_config_path()
    user_config = require_readable_config_before_write(config_path)
    set_answer(user_config, enabled, send)
    _write_user_config(config_path, user_config)
    if config is not None:
        set_answer(config, enabled, send)
    # Unconditional: a send key already false may still have an open consent window.
    _record_send_consent_change(enabled=send)
    if not enabled:
        from hermes_constants import get_hermes_home

        from .shared_metrics_process import purge_pending_receipts

        purge_pending_receipts(get_hermes_home())


def offer_consent(config: dict | None = None, *, reask: bool = False) -> bool:
    """No-op: this build never asks anyone to share metrics. Always answered."""
    return True


def offer_consent_if_undecided(config: dict | None = None) -> None:
    """No-op: this build never asks anyone to share metrics."""


def offer_consent_before_chat(args: Any) -> None:
    """No-op: this build never asks anyone to share metrics."""
