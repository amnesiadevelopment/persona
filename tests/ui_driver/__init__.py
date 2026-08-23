"""Drive persona's real flet UI — press actual controls, observe real effects.

WHY THIS EXISTS
---------------
Roughly ten test files touch the UI by calling handlers and inspecting state
directly. That is legitimate unit testing, and it is NOT the same as operating
a widget: it verifies the function behind a control, never that the control is
wired to it, enabled, visible, or that pressing it does anything. Until this
package, nothing in the project could press a button.

THE MECHANISM, AND WHY IT IS NOT OBVIOUS
----------------------------------------
flet 0.85 is Flutter underneath, and Flutter web paints to **canvaskit** — a
canvas. Point a browser at the served app and the DOM is 32 elements with an
empty ``innerText``: no buttons, no fields, nothing addressable. A driver that
stopped there would correctly conclude the UI is undrivable.

What makes it drivable is Flutter's **semantics (accessibility) tree**, which
mirrors every widget into real DOM — ``<flt-semantics role="button">`` with
real text and real bounding boxes. It ships DORMANT behind a single element,
``<flt-semantics-placeholder role="button" aria-label="Enable accessibility">``.

That placeholder is the whole trick, and it has a trap in it: it is positioned
at ``(-1, -1, 1, 1)`` — deliberately off-viewport, so a screen reader finds it
and a user never does. Playwright's ``click()`` therefore REFUSES it with
"Element is outside of the viewport", and ``force=True`` does not help either.
:func:`wake_semantics` dispatches the pointer sequence onto the element
directly, which is the one activation path that does not need a viewport hit.

So the driver reuses machinery already in the tree (playwright, the same
library ``src/services/verify/transport.py`` drives a live page with) rather
than inventing a second one, and it adds NOTHING to the shipped application:
no test ids, no debug affordances, no hooks. It operates the controls a user
operates, found by the text a user reads.

WHAT IT OBSERVES
----------------
Deliberately not the DOM. A repaint can flip text; only the product's own
persisted state proves the handler behind the control actually ran. Tests here
drive through the widgets and then read the result back through the SERVICE
LAYER against the same isolated ``PERSONA_HOME`` the served app writes to.
"""

from __future__ import annotations

from .driver import FletDriver, SemanticNode, TextField
from .server import ServedApp, serve_app
from .watchdog import (
    DEFAULT_OP_TIMEOUT,
    ChildWatchdog,
    UiDriverTimeout,
    child_pids,
    reap_process_tree,
    survivors,
)

__all__ = [
    "FletDriver",
    "SemanticNode",
    "TextField",
    "ServedApp",
    "serve_app",
    "ChildWatchdog",
    "UiDriverTimeout",
    "DEFAULT_OP_TIMEOUT",
    "child_pids",
    "reap_process_tree",
    "survivors",
]
