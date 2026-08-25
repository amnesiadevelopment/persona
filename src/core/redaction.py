"""Strip credential-shaped text out of a message before it is shown.

This is the ONE implementation of that rule. It lived in
``services.verify.exit_guard`` (which still re-exports it, so every existing
``from ...exit_guard import redact`` keeps working) until PS-160 needed the
same rule in ``services.proxy.bridge``. Two modules that both write
un-authored exception text to an operator-visible place must not each carry
their own regex: a redaction bug fixed in one copy and not the other is worse
than no redaction, because the second copy still looks guarded.

``proxy`` may not import ``verify`` — that is the wrong layering direction and
would drag the checker stack into the bridge — so the shared rule lives here,
beside the other cross-cutting primitives.
"""

from __future__ import annotations

import re

# scheme://user:pass@host -> scheme://***:***@host
_CREDENTIAL_URL = re.compile(r"(\w+://)[^/@\s]+:[^/@\s]+@")


def redact(text: str) -> str:
    """Strip anything credential-shaped out of a message before it is shown.

    The credential must not reach a log, a commit, a ticket, a test fixture or
    a captured artefact. Messages in the calling modules are built from
    exception text that can contain a proxy URL, so every one of them goes
    through here.

    Applied to the WHOLE message rather than to the parts believed to be
    risky: the risky part is the one nobody thought of.

    ⚠️ WHAT THIS DOES AND DOES NOT COVER, stated rather than assumed. It
    rewrites a credential in URL FORM — the shape a proxy URL arrives in, and
    the shape both callers actually hold (`exit_guard`'s `proxy_url`,
    `bridge`'s `upstream_url`). It does NOT recognise a bare username or
    password appearing on its own, because a bare secret is
    indistinguishable from any other word and a filter that tried would either
    miss it or eat the message. That residual is why this is a last line of
    defence and not a licence to put a credential into a message on purpose:
    the discipline is still "don't write the secret", and this catches the
    case where something else wrote it for you.
    """
    return _CREDENTIAL_URL.sub(r"\1***:***@", text)
