"""Errors shared across the consumers that route through a profile's proxy."""

from __future__ import annotations


class ProxyUnresolvedError(RuntimeError):
    """A profile has a proxy ASSIGNED but it could not be resolved to a usable
    URL (deleted/renamed proxy, or a stored value that lost its scheme/port).

    Every path that routes through a profile's exit IP — the browser launch and
    the SSH/SFTP/tmux session — must fail CLOSED on this: connecting anyway
    would go DIRECT from the operator's real IP, a de-anonymization.
    """
