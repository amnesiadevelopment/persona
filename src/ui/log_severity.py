"""What KIND of event a log line is — declared by its author, or inferred.

WHY THIS MODULE EXISTS, AND WHY IT IS SEPARATE FROM ``log_console``.

An Activity Log event's severity drives four consumers: the row's dot, the
collapsed dock's pulse, the fullscreen row, and — the one that costs the most
when it is wrong — the fullscreen view's severity FILTER. Until now all four
recovered it the same way: :func:`severity` substring-matched the rendered
English message against three hardcoded word bags. No call site could say what
its own event MEANT; the severity was decided later, from prose that was not
written for that purpose.

The measured cost of that, at the commit this module was added:

* ``"ready"`` is a substring of ``"not ready yet"``, so both launch REFUSALS in
  ``actions/browser.py`` painted the green SUCCESS dot — the product telling an
  operator the launch worked, on the ordinary click-before-the-download-finished
  path.
* ``delete_profile_failed`` — a destructive operation that did NOT happen —
  carries none of the twenty tokens, so it classified ``idle``. Not merely a dim
  dot: filtering the log to ``failures`` DROPPED it. The operator asks what went
  wrong and is told nothing did.
* ``app.py`` logs ``f"...couldn't read the build record ({e})"``. One authored
  line, whose colour was decided by whichever exception class got interpolated:
  ``OSError`` → fail, ``KeyboardInterrupt`` → idle.

So a call site may now DECLARE. :func:`declare` returns the message wearing its
severity; :func:`event_severity` prefers a declaration and falls back to
:func:`severity`.

THE DECLARATION RIDES ON THE MESSAGE, NOT ON THE SINK'S SIGNATURE, and that is
the load-bearing decision in this file. The obvious shape — ``log(msg,
severity=...)`` — has to be threaded through every sink on the path
(``actions/*`` ``log`` callables, :meth:`App._log`, :meth:`AppState.add_log`)
and through every TEST that passes ``logs.append`` or ``lambda m: None`` as that
callable. A ``str`` subclass changes no signature at all: every sink still takes
one string, every existing caller and fake keeps working, and ``logger.info``,
``"".join``, ``.lower()`` and the rest behave exactly as before.

It also cannot LEAK. Nothing is encoded in the TEXT, so no renderer can print a
marker, the fullscreen search cannot match one, and a line copied out of the log
is the line the operator saw. An in-band sentinel would have had to be stripped
correctly by four renderers and one search box to achieve what an out-of-band
attribute gets for free.

WHAT DEGRADES, DELIBERATELY. ``str`` operations return plain ``str``, so
``f"prefix {declared}"`` or ``declared.strip()`` is UNDECLARED again. That is
correct rather than lossy: an f-string is a NEW authored message, and its author
is the one who gets to say what it means.

:func:`severity` IS PERMANENT, not transitional. Two independent populations can
never declare anything:

1. Un-migrated call sites — 178 authored messages, migrated site by site.
2. Lines seeded from DISK. ``AppState`` fills its ring at startup from the
   persistent ``persona_*.log`` file, re-parsing text written by a PREVIOUS
   process. Those lines have no declaration to carry and never will, whatever
   the sink signature becomes.

FLET-FREE ON PURPOSE. ``state.py`` has to preserve a declaration through the
ring and must not grow a UI dependency to do it, so the vocabulary and the
classifier live here and ``log_console`` re-exports them. Every existing
``from src.ui.log_console import severity, SEV_*`` keeps working unchanged.
"""

from __future__ import annotations

from typing import Self

#: Severity vocabulary. Deliberately four values, not a colour per message:
#: the dot exists so FAILURE is findable in a peripheral glance, and a palette
#: with a dozen entries cannot do that.
SEV_FAIL = "fail"
SEV_OK = "ok"
SEV_INFO = "info"
SEV_IDLE = "idle"

SEVERITIES = (SEV_FAIL, SEV_OK, SEV_INFO, SEV_IDLE)

#: Where a declaration is kept. An ATTRIBUTE name rather than a text marker —
#: see the module docstring for why nothing is encoded in the message itself.
_DECLARED_ATTR = "declared_severity"


class DeclaredMessage(str):
    """A log message that STATES what kind of event it is.

    A ``str`` in every respect — pass it to any sink, log it, join it, compare
    it — carrying one extra attribute that :func:`event_severity` reads.

    Constructed through :func:`declare`, which is where the value is validated.
    """

    __slots__ = (_DECLARED_ATTR,)

    def __new__(cls, message: str, severity_value: str) -> Self:
        obj = super().__new__(cls, message)
        object.__setattr__(obj, _DECLARED_ATTR, severity_value)
        return obj


def declare(message: str, severity_value: str) -> DeclaredMessage:
    """State this event's severity at the site that KNOWS it.

    ``log(declare(get_string("delete_profile_failed", name=n), SEV_FAIL))``

    An unknown severity is a hard error rather than a silent fallback: a typo
    that quietly reverted a site to prose-matching would be the defect this
    module exists to close, wearing the declaration as a costume.
    """
    if severity_value not in SEVERITIES:
        raise ValueError(
            f"unknown severity {severity_value!r}; expected one of "
            f"{', '.join(SEVERITIES)}"
        )
    return DeclaredMessage(message, severity_value)


def declared_severity(message: object) -> str | None:
    """The severity this message DECLARES, or ``None`` if it declares nothing.

    ``None`` is the answer for every plain string — an un-migrated call site, a
    line seeded from a previous session's log file, or engine text passed
    through verbatim. It means "ask :func:`severity`", never "idle".
    """
    value = getattr(message, _DECLARED_ATTR, None)
    return value if value in SEVERITIES else None


def severity(message: str) -> str:
    """Classify one event message BY ITS PROSE — the fallback.

    Kept alongside (not merged into) ``log_format.log_message_color``: that one
    answers "what colour is this text", this one answers "what KIND of event is
    this", which the dot and the collapsed strip's pulse both need as a value
    rather than as a hex string.

    THIS FUNCTION IS FROZEN, and the reason is its blast radius: every token in
    it is matched against EVERY message in the app, so adding or narrowing one
    re-classifies already-shipped lines that nobody was thinking about.
    ``tests/test_ps272_trash_expiry_signal.py::test_widening_severity_reclassified_nothing_else``
    exists to catch exactly that. A site whose classification is wrong is fixed
    by DECLARING it (see :func:`declare`), never by editing this bag — that
    workaround is what the declaration retired.
    """
    low = message.lower()
    if (
        "fail" in low
        or "error" in low
        or "refused" in low
        or "missing" in low
        or "LAUNCH_FAILED" in message
        or low.startswith("session ended")
    ):
        return SEV_FAIL
    if (
        "started" in low
        or "installed" in low
        or "imported" in low
        or "exported" in low
        or "ready" in low
        or "reached" in low
        or "updated to" in low
        or "synced" in low
        or "frozen" in low
    ):
        return SEV_OK
    if (
        "available" in low
        or "downloading" in low
        or "update" in low
        or "launching" in low
        # A start-up purge DESTROYED key material. It is not a failure — the
        # retention floor working exactly as designed — but it is not idle
        # housekeeping either, and SEV_IDLE gave a permanent destruction the
        # dimmest dot in the console. "purged" is deliberately the whole token:
        # it is the only word in the app's messages that carries it (the two
        # purge lines in trash/service.py and main.py), so nothing unrelated is
        # reclassified by adding it.
        or "purged" in low
    ):
        return SEV_INFO
    return SEV_IDLE


def event_severity(carrier: object, message: str) -> str:
    """What this event IS: its declaration if it made one, else its prose.

    ``carrier`` is whatever object the declaration could be riding on — the
    stored ring line, which :meth:`AppState.add_log` builds as a
    :class:`DeclaredMessage` when the message that reached it declared one.
    ``message`` is the PARSED text the row renders.

    The two are separate arguments because they are genuinely different values:
    the carrier is ``"10:00:04  > Session ended: mail-us-011"`` and the message
    is ``"Session ended"``. Classifying the carrier's raw text instead of the
    parsed message is a defect this codebase has already shipped once — see
    ``log_dock._paint_stream_state`` — so the fallback is given the message and
    only the DECLARATION is read off the carrier.

    ONE SEAM FOR ALL FOUR CONSUMERS. The dot, the collapsed pulse, the
    fullscreen row and the fullscreen severity FILTER all reach this through
    :func:`~src.ui.log_console.parse_event`, so a declaration cannot reach one
    of them and miss another.
    """
    return declared_severity(carrier) or severity(message)
