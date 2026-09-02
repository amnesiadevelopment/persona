"""How ``ProfileManager.update_profile`` decides what an edit means about a
profile's mTLS certificate.

The third member of the profile dialog's reference-field family, after
``proxy_assignment.py`` (PS-44) and ``pool_assignment.py`` (PS-157). Both of
those modules exist because ``update_profile`` read **absence** as a clear:
their fields defaulted such that saying nothing about them un-assigned them.

**This field's defect is a different one, and the fix therefore is too — read
this before assuming the shipped idiom transfers.** ``new_certificate`` was
already ``str | None = None`` guarded by ``if new_certificate is not None:``, so
absence ALREADY preserved the stored assignment. Measured at ``a1cc9d4``::

    after omitting new_certificate  -> corp-ca     # absence already unchanged
    after new_certificate=""        -> None        # "" is the clear

So on THIS field ``""`` is a real, load-bearing instruction — "clear it" —
pinned by ``tests/test_profile_certificate_persist.py::test_update_can_clear_certificate``.
Copying ``resolve_proxy_assignment``'s final ``if not new_proxy: return stored``
would silently repeal that instruction and break that test. It is not done.

What was genuinely missing is the *other* end of the chain: a way for the
**profile dialog** to say "I could not account for this assignment". The dialog
computed its selection as "the profile's certificate if that name appears in
``cert_names``, otherwise ``(none)``" and, on submit, mapped that display
fallback to ``""`` — promoting an accident into the explicit clear the model is
obliged to honour. An edit made for an unrelated reason (a rename, a note) then
un-assigned the certificate, and took the recorded ``cert_trust_status`` verdict
with it as collateral.

The list can legitimately be missing a name the profile still references, and
both routes are deliberate protections that must not be weakened
(``services/cert/store.py``): one unparseable record is SKIPPED and the load
continues (populated dropdown, one name absent), and an unreadable
``certificates.json`` is QUARANTINED with the store left empty (every name
absent). Neither reaches this dialog in any form.

Hence ONE directive:

``CERT_UNCHANGED``
    Leave the stored certificate exactly as it is. What the dialog sends from
    the unresolved state, so an operator who opened the dialog to rename a
    profile comes out with the same assignment they went in with.

**There is deliberately no ``CERT_NONE``.** Its proxy/pool counterparts exist
because on those fields ``""`` had to STOP meaning "clear", leaving them with no
way to say it; here ``""`` still means clear, it is the dialog's own "(none)"
value, and a test pins it. Adding a second spelling for an instruction that is
already expressible would give this path two ways to say one thing and would
tempt exactly the edit to that test which this ticket forbids. The asymmetry is
the point: the three modules share an IDIOM, not a policy, and this field's
policy is not the other two's.

A SIBLING rather than a generalisation of the other two, following the
precedent ``pool_assignment.py``'s docstring records: those modules are shipped
and correct, each carries field-specific reasoning (real-IP exposure for the
proxy, restore-recoverability for the pool) that does not describe a
certificate, and rewriting them to serve three fields would put two working
protections at risk to save a few lines. What a preserved certificate protects
is neither of those things: it is the operator's *intent*. A profile whose
certificate could not be found is not a profile that has no certificate — and
the second is a legitimate configuration nothing will ever flag, so when the
store recovers (malformed record repaired, quarantined file restored,
certificate put back by ``restore_certificate``) the proxy and the pool come
back attached and the certificate does not.

The failure mode this trades for is loud and harmless: a preserved-but-
unresolvable assignment is already handled correctly at launch
(``services/browser/process.py`` sweeps the key material and launches without a
client certificate), so nothing is broken by keeping it.
"""

from __future__ import annotations


class CertDirective:
    """An instruction about the certificate field that is not a certificate name.

    A distinct class rather than a sentinel string: a directive can then never
    compare equal to a certificate name, never be stored as one, and never
    survive a round-trip through JSON pretending to be one. Compared with
    ``is``.
    """

    __slots__ = ("_label",)

    def __init__(self, label: str) -> None:
        self._label = label

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self._label}>"


#: Leave the stored certificate alone. What the dialog sends when it could not
#: account for the profile's assigned certificate.
CERT_UNCHANGED = CertDirective("CERT_UNCHANGED")


def resolve_cert_assignment(
    new_certificate: str | CertDirective | None,
    stored: str | None,
) -> str | None:
    """The certificate an update should RESULT IN, given what the caller
    supplied.

    ``stored`` is the profile's current certificate, returned untouched
    whenever the caller did not clearly ask for something else.

    Note the deliberate difference from ``resolve_proxy_assignment`` and
    ``resolve_pool_assignment``: ``""`` is NOT read as "unchanged" here. On this
    field an empty string is the operator's explicit "no certificate" — it is
    what the dialog's "(none)" option sends and what the REST lane forwards for
    a supplied-empty ``certificate`` key — so it clears, exactly as it always
    has. ``None`` (the caller said nothing at all) preserves, exactly as it
    always has. Only the directive is new.
    """
    if new_certificate is CERT_UNCHANGED:
        return stored
    # A directive that is not the one above is not a name and must never be
    # stored as one; fall back to the safe reading.
    if isinstance(new_certificate, CertDirective):
        return stored
    if new_certificate is None:
        # The caller supplied nothing. This already preserved before the
        # directive existed (`if new_certificate is not None:` in
        # update_profile) and must keep doing so.
        return stored
    # "" reaches here and returns None: the explicit clear, unchanged.
    return new_certificate or None


def cert_for_new_profile(certificate: str | CertDirective | None) -> str | None:
    """The certificate a NEWLY CREATED profile should carry.

    Creation has no stored value to preserve, so the directive means "no
    certificate" here — exactly as ``proxy_for_new_profile`` documents for the
    proxy. The unresolved state cannot arise on create (there is no profile, so
    the dialog's ``current_cert`` is ``""`` and the option is never built), but
    the dialog shares ONE value across create and edit, so this guard is what
    keeps a directive object from ever being stored as if it were a certificate
    name should that ever change.
    """
    if isinstance(certificate, CertDirective) or not certificate:
        return None
    return certificate
