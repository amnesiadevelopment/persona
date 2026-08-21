"""How ``ProfileManager.update_profile`` decides what an edit means about a
profile's proxy.

A profile's proxy is the single field standing between the operator's real IP
and every site the profile visits, so "the caller said nothing about the proxy"
and "the caller wants no proxy" must be different statements. They used to be
the same one: ``update_profile`` took the proxy as a required argument and did
``profile.proxy = new_proxy or None``, so **absence and emptiness both cleared
it**. An edit made for an unrelated reason — a rename, a note, a device type —
could therefore un-assign a proxy as a side effect, and the launch guard could
not object, because a profile with no proxy is a legitimate configuration and
there was nothing left to guard (see ``_require_proxy_resolved`` in
``services/browser/process.py``: it keys on the assignment being PRESENT).

Neither ``""`` nor ``None`` could carry the new meaning, because both were
already spoken on this path — ``""`` is what the profile dialog sends, and
``None`` is what gets stored. Hence two explicit directives:

``PROXY_UNCHANGED``
    Leave the stored proxy exactly as it is. The DEFAULT, so a caller that
    says nothing changes nothing.

``PROXY_NONE``
    Clear the assignment. The operator deliberately chose DIRECT.

An empty value (``""`` or ``None``) is deliberately read as UNCHANGED rather
than as a clear. That is the whole point of the fix: clearing a proxy is now
something a caller has to SAY, never something it can do by omitting a value or
by passing a falsy one. A caller that means DIRECT passes ``PROXY_NONE`` — the
profile dialog does, and the REST lane translates an explicitly-supplied empty
``proxy`` field into it, because a route is the one layer that can tell an
omitted key from a supplied empty one.

The failure mode this trades for is loud, not silent: a caller that meant to
clear and forgot to say so leaves the profile PROXIED, which refuses nothing and
leaks nothing. The mode it removes is the reverse, and it launches direct on the
operator's real IP.
"""

from __future__ import annotations


class ProxyDirective:
    """An instruction about the proxy field that is not a proxy name.

    A distinct class rather than a sentinel string: a directive can then never
    compare equal to a proxy name, never be stored as one, and never survive a
    round-trip through JSON pretending to be one. Compared with ``is``.
    """

    __slots__ = ("_label",)

    def __init__(self, label: str) -> None:
        self._label = label

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self._label}>"


#: Leave the stored proxy alone. The default for every update.
PROXY_UNCHANGED = ProxyDirective("PROXY_UNCHANGED")

#: Clear the assignment — the operator deliberately chose DIRECT.
PROXY_NONE = ProxyDirective("PROXY_NONE")


def resolve_proxy_assignment(
    new_proxy: str | ProxyDirective | None,
    stored: str | None,
) -> str | None:
    """The proxy an update should RESULT IN, given what the caller supplied.

    ``stored`` is the profile's current proxy, returned untouched whenever the
    caller did not clearly ask for something else.
    """
    if new_proxy is PROXY_NONE:
        return None
    if new_proxy is PROXY_UNCHANGED:
        return stored
    # A directive that is neither of the two above is not a name and must never
    # be stored as one; fall back to the safe reading.
    if isinstance(new_proxy, ProxyDirective):
        return stored
    # Falsy means the caller supplied no value. Preserving here is what makes
    # "clear it" something a caller has to say explicitly.
    if not new_proxy:
        return stored
    return new_proxy


def proxy_for_new_profile(proxy: str | ProxyDirective | None) -> str | None:
    """The proxy a NEWLY CREATED profile should carry.

    Creation has no stored value to preserve, so both directives mean the same
    thing here — no proxy. Present so a caller (the profile dialog, which sends
    ``PROXY_NONE`` for a deliberate DIRECT on both paths) cannot accidentally
    store a directive object as if it were a proxy name.
    """
    if isinstance(proxy, ProxyDirective) or not proxy:
        return None
    return proxy
