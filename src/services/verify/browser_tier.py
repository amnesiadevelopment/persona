"""Drive persona's OWN engine through the exit and read the prose checkers.

The browser tier is the half of the matrix that needs a page to actually run.
Three things about it are settled by measurement, not by preference, and each
one is a way this module could have been quietly wrong.

1. It must be persona's engine, not stock Chromium
--------------------------------------------------
Stock Chromium ``151`` is present in this container and is NOT the product. A
reading taken under it describes stock Chromium and answers nothing about
persona. This module launches ``invisible_playwright`` — the patched Firefox
the app itself launches (``services/browser/invisible_launch.py``) — pinned in
``pyproject.toml``.

2. The engine authenticates to the SOCKS5 proxy ITSELF
-------------------------------------------------------
The knowledge article and the ticket both expect a local ``ProxyBridge`` here,
on the grounds that Chromium cannot carry SOCKS5 credentials on
``--proxy-server``. That is true OF CHROMIUM and it is why the bridge exists —
but this tier runs the FIREFOX engine, and ``invisible_launch._proxy_dict``
records the difference in one line: *"invisible_playwright does SOCKS5-with-auth
natively, so no local bridge is needed (unlike Camoufox)"*. Measured here: the
engine reached the Polish exit with a ``{server, username, password}`` proxy
dict and no relay in the picture.

So no bridge is started. That is not a shortcut around PS-25's hardened
listener — it is declining to stand up a *second* local listener that nothing
would connect to. The rule the article is really protecting ("do not write a
weaker relay beside the hardened one") is honoured by starting NO relay at all.
The credential still never touches a command line: it goes in the proxy dict.

3. ``page.evaluate`` is not available, so the page is read as TEXT
------------------------------------------------------------------
Measured: ``Page.evaluate: call to eval() blocked by CSP`` on a real checker
page under the engine's context, and ``bypass_csp`` in the context kwargs does
not lift it. Every prose checker in the catalogue is therefore read through
``inner_text``, which is also what the catalogue's patterns are written
against. This is a real constraint on what this tier can answer and it is
recorded rather than worked around.

And the settle time is load-bearing
------------------------------------
``--dump-dom``-style "load and read" returns before the verdict exists;
Pixelscan and CreepJS take 45-60 seconds to settle. Each checker carries its
own ``settle_seconds`` and this module waits it out. A page read too early
records an empty verdict as though it were the answer — the same class of
defect as a skipped test reporting green.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable

from .checkers import BROWSER_CHECKERS, Checker, TextItem
from .matrix import Reading, extract_text_item, readings_for_unread_checker

# A page that has not loaded in this long is not going to. Generous because it
# is a mobile exit carrying a full page load, and because the alternative to
# waiting is recording an unobtainable reading — the wait is cheaper than the
# lost row.
NAVIGATION_TIMEOUT_MS = 90_000


class EngineUnavailable(RuntimeError):
    """persona's engine could not be launched here.

    Its own class so a caller can tell "the browser tier did not run" from "a
    checker did not answer". The first makes every browser-tier row
    unobtainable for one shared reason; the second is per-checker.
    """


def _proxy_dict(proxy_url: str) -> dict:
    """Turn the credential into the engine's proxy dict.

    Mirrors ``services/browser/invisible_launch._proxy_dict`` — the launch path
    the app itself uses — rather than inventing a second shape. The ``socks5h``
    scheme this project reads its credential in is normalised back to
    ``socks5`` for the engine, which takes remote resolution from the
    ``network.proxy.socks_remote_dns`` pref instead of from the scheme (set in
    :func:`_prefs`); ``socks5h`` in a ``server=`` value is a curl-ism the
    browser would reject outright.
    """
    match = re.match(r"socks5h?://(?:([^:]+):([^@]+)@)?(.+)", proxy_url)
    if not match:
        raise EngineUnavailable(
            "the proxy credential is not in a form the engine can take"
        )
    user, password, hostport = match.groups()
    out = {"server": f"socks5://{hostport}"}
    if user:
        out["username"] = user
        out["password"] = password
    return out


def _prefs() -> dict:
    """Prefs the reading depends on.

    ``socks_remote_dns`` is the browser-side counterpart of ``socks5h``: with
    it off, Firefox resolves the checker's hostname LOCALLY and a DNS query
    naming the checker leaves this machine on the operator's own resolver,
    while the page itself still loads through the exit. The reading would look
    perfect and would have leaked exactly what the product forbids. The app's
    own launch path sets the same pref (``invisible_launch.py``).
    """
    return {"network.proxy.socks_remote_dns": True}


def read_page_texts(
    proxy_url: str,
    *,
    checkers: "tuple[Checker, ...]" = BROWSER_CHECKERS,
    seed: int = 0,
    sleep: Callable[[float], None] = time.sleep,
) -> "dict[str, dict]":
    """Load every browser-tier checker once and return its visible text.

    Returns ``{checker_id: {"text": str} | {"error": str}}``. Never raises for
    ONE checker's failure — a checker that refuses the connection is a reading
    about that checker, and it must not take the other five down with it.

    Raises :class:`EngineUnavailable` only when the ENGINE itself could not be
    started, which is the one failure that really is shared by every row.
    """
    try:
        from invisible_playwright import InvisiblePlaywright
    except Exception as exc:  # pragma: no cover - import shape varies by env
        raise EngineUnavailable(
            f"persona's engine (invisible_playwright) is not importable: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    out: "dict[str, dict]" = {}
    kwargs: "dict[str, Any]" = {
        "headless": True,
        "humanize": False,
        "proxy": _proxy_dict(proxy_url),
        "extra_prefs": _prefs(),
    }
    if seed:
        kwargs["seed"] = seed
    try:
        engine = InvisiblePlaywright(**kwargs)
    except Exception as exc:
        raise EngineUnavailable(
            f"could not construct persona's engine: {type(exc).__name__}: {exc}"
        ) from exc

    try:
        with engine as live:
            for checker in checkers:
                try:
                    page = live.new_page()
                except Exception as exc:
                    out[checker.id] = {
                        "error": f"{type(exc).__name__}: {exc}"
                    }
                    continue
                try:
                    page.goto(
                        checker.url,
                        timeout=NAVIGATION_TIMEOUT_MS,
                        wait_until="domcontentloaded",
                    )
                    # The verdict does not exist at load time. Waiting is the
                    # whole difference between a reading and an empty page
                    # recorded as one.
                    sleep(checker.settle_seconds)
                    out[checker.id] = {"text": page.inner_text("body")}
                except Exception as exc:
                    out[checker.id] = {"error": f"{type(exc).__name__}: {exc}"}
                finally:
                    try:
                        page.close()
                    except Exception:
                        pass
    except EngineUnavailable:
        raise
    except Exception as exc:
        raise EngineUnavailable(
            f"persona's engine failed during the run: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return out


def readings_from_texts(
    pages: "dict[str, dict]",
    *,
    checkers: "tuple[Checker, ...]" = BROWSER_CHECKERS,
) -> "list[Reading]":
    """Turn captured page texts into readings.

    Pure: no network, no browser. This is the half of the browser tier the test
    suite drives against the committed page fixtures, which is how a pattern is
    proven to read a REAL page the way the catalogue claims.

    A checker missing from ``pages`` is unobtainable with a reason, not
    skipped — the record keeps its full width whatever the run did.
    """
    out: "list[Reading]" = []
    for checker in checkers:
        page = pages.get(checker.id)
        if page is None:
            out.extend(
                readings_for_unread_checker(
                    checker, "the run produced no result for this checker"
                )
            )
            continue
        if "error" in page or "text" not in page:
            reason = page.get("error", "the run captured no text")
            note = checker.note_unreachable
            if note:
                reason = f"{reason} — {note}"
            out.extend(readings_for_unread_checker(checker, reason))
            continue
        text = page["text"]
        if not text.strip():
            out.extend(
                readings_for_unread_checker(
                    checker,
                    "the page rendered no visible text (it may not have "
                    "settled); recorded as unobtainable rather than as a page "
                    "that said nothing adverse",
                )
            )
            continue
        for item in checker.items:
            out.append(extract_text_item(checker, item, text))
    return out


def read_browser_tier(
    proxy_url: str,
    *,
    checkers: "tuple[Checker, ...]" = BROWSER_CHECKERS,
    seed: int = 0,
) -> "list[Reading]":
    """The whole browser tier: launch, load, settle, read, extract.

    An engine that will not start makes every row unobtainable WITH THE REASON
    — the run continues and records that, rather than dying and recording
    nothing, because "we could not run a browser here" is itself a result.
    """
    try:
        pages = read_page_texts(proxy_url, checkers=checkers, seed=seed)
    except EngineUnavailable as exc:
        out: "list[Reading]" = []
        for checker in checkers:
            out.extend(readings_for_unread_checker(checker, str(exc)))
        return out
    return readings_from_texts(pages, checkers=checkers)


__all__ = [
    "EngineUnavailable",
    "NAVIGATION_TIMEOUT_MS",
    "read_browser_tier",
    "read_page_texts",
    "readings_from_texts",
]
