"""Press real controls in a served persona UI and read what is on screen.

The whole reason this is not three lines of playwright is
:meth:`FletDriver.wake_semantics`. See the package docstring for why the
semantics tree is dormant and why the placeholder that wakes it cannot be
clicked normally.

Controls are addressed by the TEXT A USER READS — ``"[ create ]"``,
``"[ + new ]"``, ``"Skip"`` — never by a test id, because adding test ids to
the shipped application is explicitly out of bounds. That is a real constraint
and it is the honest one: if a control cannot be found by its visible label, a
user cannot describe it either.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

#: Path to a chromium the driver can launch. The playwright browser cache in
#: this project's container carries firefox only, so the system binary is used
#: explicitly rather than relying on a bundled download.
SYSTEM_CHROMIUM = "/usr/bin/chromium"

#: Sandboxing is unavailable in the agent container (no user namespaces, no
#: chromium-sandbox package). These flags are for driving OUR OWN UI on
#: localhost and are not a statement about how the product launches browsers.
CHROMIUM_ARGS = ("--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage")

#: The real app paints a splash, loads a container and runs an async startup
#: before the UI settles. Measured at ~15s on this container; 25s is headroom.
SETTLE_MS = 25_000

#: Dispatch the pointer sequence straight onto the placeholder. Playwright's
#: click() refuses it ("outside of the viewport") because Flutter parks it at
#: (-1,-1,1,1) so a screen reader finds it and a user does not; force=True does
#: not help. Dispatching on the element is the activation path that needs no
#: viewport hit.
_WAKE_JS = """() => {
  const el = document.querySelector('flt-semantics-placeholder');
  if (!el) return 'absent';
  for (const t of ['pointerdown', 'pointerup', 'click']) {
    el.dispatchEvent(new PointerEvent(t, {bubbles: true, cancelable: true,
      pointerType: 'mouse', clientX: 0, clientY: 0, button: 0}));
  }
  el.click();
  return 'woken';
}"""

#: Flutter web backs a single-line field with <input> and a MULTILINE one with
#: <textarea>. Querying only 'input' skips every multiline field on the screen
#: while reporting success on the rest — a whole control class missing with no
#: signal. Both tags carry data-semantics-role="text-field"; the tag union is
#: used rather than that attribute so the selector still works if Flutter
#: changes its private attribute names.
_FIELD_SELECTOR = "input, textarea"

#: How much clear space a control needs INSIDE a scrolling region before a
#: click on it is honoured. Not a guess and not defensive padding — measured:
#: the Engine dropdown spans y 743-788 in a band ending at 788, so it is
#: "fully visible" by any containment test, and clicking it does NOTHING
#: (``aria-expanded`` stays false, zero option nodes appear). Scrolling it
#: inward by 200px and clicking the SAME control opens it immediately. A
#: control flush against the fold is therefore not reliably hit-testable, and
#: bare containment is the wrong question to ask.
_FOLD_MARGIN_PX = 12

_FIELDS_JS = """() => [...document.querySelectorAll('input, textarea')].map(e => ({
  tag: e.tagName,
  label: e.getAttribute('aria-label'),
  value: e.value,
}))"""

#: ``id``, ``expanded`` and ``tappable`` are scraped alongside the rest because
#: they are what makes a DROPDOWN addressable at all. A dropdown does not look
#: like a button here: it carries ``aria-expanded`` and its text is EMPTY (the
#: selected value is painted to canvas, never mirrored into the DOM), while a
#: real button carries ``flt-tappable`` and its label as text. See
#: :meth:`FletDriver.select_option` for why both flags are needed.
_SCRAPE_JS = """() => [...document.querySelectorAll('flt-semantics')].map(e => {
  const r = e.getBoundingClientRect();
  return {
    role: e.getAttribute('role'),
    label: e.getAttribute('aria-label'),
    text: (e.textContent || '').trim(),
    leaf: e.children.length === 0,
    box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
    node_id: e.getAttribute('id'),
    expanded: e.getAttribute('aria-expanded'),
    tappable: e.hasAttribute('flt-tappable'),
  };
})"""

#: A dialog body taller than its box scrolls, and a control below the fold has
#: a real bounding box at coordinates NOBODY CAN CLICK. Finding the scrollers
#: is what makes "scroll it into view first" possible — see
#: :meth:`FletDriver._scroll_into_view` for why that is not optional.
_SCROLLERS_JS = """() => [...document.querySelectorAll('flt-semantics')]
  .filter(e => e.scrollHeight > e.clientHeight + 4)
  .map(e => {
    const r = e.getBoundingClientRect();
    return {
      top: e.scrollTop,
      scroll_height: e.scrollHeight,
      client_height: e.clientHeight,
      box: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)],
    };
  })"""


@dataclass(frozen=True)
class SemanticNode:
    """One widget as Flutter's accessibility tree exposes it."""

    role: str | None
    label: str | None
    text: str
    leaf: bool
    box: tuple[int, int, int, int]
    node_id: str | None = None
    expanded: str | None = None
    tappable: bool = False

    @property
    def is_dropdown(self) -> bool:
        """A closed-or-open dropdown, told apart from a button.

        ``aria-expanded`` is the ONLY thing that distinguishes them here: both
        carry ``role="button"``, and a dropdown's text is empty because its
        selected value is painted to canvas rather than mirrored into the DOM.
        Addressing one by the value it displays therefore matches nothing at
        all — which is exactly how this control came to be recorded as
        unreachable.
        """
        return self.expanded is not None

    @property
    def is_open(self) -> bool:
        return self.expanded == "true"

    def __str__(self) -> str:  # pragma: no cover - diagnostic only
        kind = "dropdown" if self.is_dropdown else (self.role or "-")
        return f"[{kind}] {self.text[:60]!r} box={self.box}"


@dataclass(frozen=True)
class TextField:
    """One text field as the served page really exposes it.

    ``label`` is the field's HINT text, which Flutter drops once the field
    holds a value — so it is a usable address for an empty field and useless
    for a filled one. Recorded as measured rather than smoothed over.
    """

    tag: str
    label: str | None
    value: str

    @property
    def multiline(self) -> bool:
        return self.tag == "TEXTAREA"

    def describe(self) -> str:  # pragma: no cover - diagnostic only
        kind = "multiline" if self.multiline else "single-line"
        return f"{kind} label={self.label!r} value={self.value!r}"


class SemanticsNotAvailable(RuntimeError):
    """The accessibility tree could not be woken, so nothing is addressable.

    Raised rather than returning an empty tree: an empty tree is
    indistinguishable from "the screen is legitimately blank", and silently
    reporting "no controls found" is exactly the always-passing failure this
    harness exists to make impossible.
    """


class FletDriver:
    """Drives a served persona UI through its real controls.

    Synchronous by design so tests read as a sequence of user gestures. Use as
    a context manager.
    """

    def __init__(self, url: str, width: int = 1500, height: int = 1000) -> None:
        self._url = url
        self._size = {"width": width, "height": height}
        self._pw = None
        self._browser = None
        self.page = None

    # ---- lifecycle ---------------------------------------------------

    def __enter__(self) -> "FletDriver":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def start(self) -> "FletDriver":
        # Function-local, matching src/services/verify/transport.py: playwright
        # is not importable in every environment, and a module-level import
        # would make this package unimportable where its own guard should fire.
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            executable_path=SYSTEM_CHROMIUM,
            headless=True,
            args=list(CHROMIUM_ARGS),
        )
        self.page = self._browser.new_page(viewport=self._size)
        self.page.goto(self._url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(SETTLE_MS)
        self.wake_semantics()
        return self

    def close(self) -> None:
        for obj, meth in ((self._browser, "close"), (self._pw, "stop")):
            if obj is not None:
                with contextlib.suppress(Exception):
                    getattr(obj, meth)()
        self._browser = self._pw = self.page = None

    # ---- the crux ----------------------------------------------------

    def wake_semantics(self, timeout_ms: int = 15_000) -> None:
        """Turn Flutter's dormant accessibility tree on. Idempotent.

        Verifies the result instead of assuming it: the tree either contains
        addressable nodes afterwards or this raises. A driver that quietly
        proceeded with a dead tree would report "control not found" for every
        control on a perfectly healthy screen.
        """
        result = self.page.evaluate(_WAKE_JS)
        if result == "absent":
            raise SemanticsNotAvailable(
                "no <flt-semantics-placeholder> in the served page — flet did "
                "not render a Flutter view here, so no control is addressable."
            )
        deadline = timeout_ms
        while deadline > 0:
            if any(n.role or n.label for n in self.nodes()):
                return
            self.page.wait_for_timeout(500)
            deadline -= 500
        raise SemanticsNotAvailable(
            "the accessibility tree stayed empty after activation; the UI is "
            "painting to canvas with no semantics, so nothing can be pressed."
        )

    # ---- reading the screen ------------------------------------------

    def nodes(self) -> list[SemanticNode]:
        return [
            SemanticNode(
                role=n["role"],
                label=n["label"],
                text=n["text"],
                leaf=n["leaf"],
                box=tuple(n["box"]),
                node_id=n["node_id"],
                expanded=n["expanded"],
                tappable=n["tappable"],
            )
            for n in self.page.evaluate(_SCRAPE_JS)
        ]

    def controls(self) -> list[SemanticNode]:
        """Every addressable control, for diagnosis and for the reach map."""
        return [n for n in self.nodes() if n.role or n.label]

    def dropdowns(self) -> list[SemanticNode]:
        """Every ``ft.Dropdown`` on screen, open or closed."""
        return [n for n in self.nodes() if n.is_dropdown]

    def scrollers(self) -> list[dict]:
        """Regions whose content is taller than their box, so they scroll."""
        return self.page.evaluate(_SCROLLERS_JS)

    def describe(self) -> str:
        """A readable dump of the current screen — used in failure messages."""
        return "\n".join(f"  {n}" for n in self.controls()) or "  (no controls)"

    # ---- gestures ----------------------------------------------------

    def _button(self, text: str):
        return self.page.locator('flt-semantics[role="button"]').filter(has_text=text)

    def has_button(self, text: str) -> bool:
        return self._button(text).count() > 0

    def press(self, text: str, settle_ms: int = 2500) -> None:
        """Press the button showing ``text``. Raises if it is not there.

        Deliberately loud. A press that silently no-ops when the control is
        missing turns every driven test into one that passes on a blank screen.
        """
        loc = self._button(text)
        if loc.count() == 0:
            raise AssertionError(
                f"no button matching {text!r} on screen. Present controls:\n"
                f"{self.describe()}"
            )
        # Innermost match: filter() also matches ancestors that merely CONTAIN
        # the text, and pressing an ancestor is not pressing the button.
        loc.last.click(timeout=10_000)
        self.page.wait_for_timeout(settle_ms)

    # ---- dropdowns ---------------------------------------------------
    #
    # A dropdown needs THREE things a button does not, and PS-71 recorded the
    # control as unreachable because two of them were missing at once. Each is
    # a measured fact about how flet 0.85 renders, not a guess:
    #
    # 1. ADDRESS IT BY ITS LABEL, NOT ITS VALUE. A dropdown surfaces as
    #    <flt-semantics role="button" aria-expanded="false"> with EMPTY text --
    #    the selected value is painted to canvas and never mirrored into the
    #    DOM. So filtering on the displayed value ("windows") matches ZERO
    #    nodes and the control is never clicked at all.
    #
    # 2. SCROLL IT INTO VIEW FIRST. The create-profile dialog body scrolls
    #    (scrollHeight 1248 vs clientHeight 592). A control below the fold
    #    still reports a real bounding box -- at coordinates nobody can click.
    #    Clicking there hits empty space and silently does nothing, which reads
    #    exactly like "the framework will not open this menu".
    #
    # 3. CLICK BY COORDINATE. A dropdown node carries no `flt-tappable` and no
    #    `tabindex` (a real button carries both), so Flutter does not accept a
    #    synthesised DOM click on it -- it hit-tests real pointer events
    #    against the canvas.

    def find_dropdown(self, label: str) -> SemanticNode:
        """The dropdown sitting directly under the caption ``label``.

        Raises with the full dropdown census rather than returning ``None``: a
        lookup that silently finds nothing is how a selection test comes to
        pass without ever selecting anything.
        """
        nodes = self.nodes()
        captions = [n for n in nodes if n.text == label and not n.is_dropdown]
        best: SemanticNode | None = None
        best_gap = 10**9
        for cap in captions:
            cx, cy, _cw, ch = cap.box
            for cand in (n for n in nodes if n.is_dropdown):
                dx, dy, _dw, _dh = cand.box
                gap = dy - (cy + ch)
                # Directly below and left-aligned: the label of a flet field
                # sits immediately above it and shares its left edge.
                if -2 <= gap < 60 and abs(dx - cx) < 80 and gap < best_gap:
                    best, best_gap = cand, gap
        if best is None:
            raise AssertionError(
                f"no dropdown captioned {label!r}. Dropdowns on screen: "
                f"{[(d.box, d.expanded) for d in self.dropdowns()]}.\n"
                f"Captions seen: {sorted({n.text for n in nodes if n.text})}"
            )
        return best

    def _scroll_into_view(self, node: SemanticNode, label: str) -> SemanticNode:
        """Centre ``node`` in whichever region scrolls it, and re-read its box.

        MEASURED: mere containment is not enough. The Engine dropdown spans
        y 743-788 inside a band ending at 788 — "fully visible" by any
        containment test, and clicking it does NOTHING (``aria-expanded``
        stays false, zero option nodes). Scrolling it inward and clicking the
        same control opens it. So a control FLUSH against the fold is not
        reliably hit-testable, and the check below demands a real margin
        (:data:`_FOLD_MARGIN_PX`) rather than bare containment.

        The box is RE-READ rather than adjusted arithmetically: a scroller
        clamps at its end, so assuming the requested delta was applied is how a
        click ends up a few dozen pixels off the control it is aiming at.

        SINGLE-SHOT BY DESIGN — do not "fix" this into a retry loop. It
        scrolls once, re-reads, and returns WITHOUT re-checking the margin or
        trying again, so a clamped scroll can still leave the control against
        the fold. That is deliberate: :meth:`_open` then asserts
        ``aria-expanded`` actually flipped, so the failure surfaces LOUDLY as
        "the dropdown did not open" instead of being papered over. A retry
        loop here would convert that loud failure into a silent stall and,
        worse, into a driver that reports "unreachable" for a control it
        simply never managed to aim at — which is the exact class of bug this
        whole ticket existed to overturn.
        """
        nx, ny, _nw, nh = node.box
        # Only a region that could actually hold this control can scroll it
        # into view; the dialog's own dropdowns report a few px of overflow
        # each, and treating one of those as the scroller scrolls nothing.
        usable = [
            r
            for r in self.scrollers()
            if r["scroll_height"] > r["client_height"] + 4
            and r["client_height"] > nh * 2
            and r["box"][0] - 4 <= nx <= r["box"][0] + r["box"][2] + 4
        ]
        if not usable:
            return node
        region = max(usable, key=lambda r: r["client_height"])
        rx, ry, rw, rh = region["box"]

        if (
            ny >= ry + _FOLD_MARGIN_PX
            and ny + nh <= ry + rh - _FOLD_MARGIN_PX
        ):
            return node  # comfortably inside the band, not merely contained

        delta = ny - (ry + (rh - nh) // 2)
        self.page.mouse.move(rx + rw / 2, ry + rh / 2)
        self.page.mouse.wheel(0, delta)
        self.page.wait_for_timeout(800)
        return self.find_dropdown(label)

    def options(self, label: str, settle_ms: int = 2500) -> list[str]:
        """Open the dropdown captioned ``label`` and return every option's text.

        Leaves the menu OPEN so a caller can pick from it. Use
        :meth:`select_option` for the whole gesture.
        """
        return [o.text for o in self._open(label, settle_ms)[1]]

    def _open(
        self, label: str, settle_ms: int = 2500
    ) -> tuple[SemanticNode, list[SemanticNode]]:
        """Open the dropdown and return it plus the option nodes it revealed.

        Verifies the menu actually opened instead of assuming the click landed:
        ``aria-expanded`` must flip to ``true`` AND real option nodes must
        appear. A silent no-op here is precisely the failure that made this
        control look unreachable, so it raises loudly and says what it saw.
        """
        node = self._scroll_into_view(self.find_dropdown(label), label)
        # An ALREADY-OPEN menu must be closed first, or the click below lands
        # on the control and shuts it — yielding zero new option nodes and a
        # "did not open" failure on a dropdown that opens perfectly well.
        # ``options()`` deliberately leaves the menu open, so composing it with
        # ``select_option`` hits this immediately; handling it here rather than
        # asking every caller to remember an Escape.
        if node.is_open:
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(800)
            node = self._scroll_into_view(self.find_dropdown(label), label)
        before = {n.node_id for n in self.nodes()}

        x, y, w, h = node.box
        self.page.mouse.click(x + w / 2, y + h / 2)
        self.page.wait_for_timeout(settle_ms)

        after = self.nodes()
        reopened = next((n for n in after if n.node_id == node.node_id), None)
        # Option nodes are the newly-arrived tappable ones carrying text. The
        # full-viewport wrappers Flutter adds around an open menu are excluded
        # by requiring a box smaller than the page.
        page_w = self._size["width"]
        opts = [
            n
            for n in after
            if n.node_id not in before
            and n.tappable
            and n.text
            and n.box[2] < page_w
        ]
        if not opts or (reopened is not None and not reopened.is_open):
            raise AssertionError(
                f"the dropdown captioned {label!r} did not open. "
                f"aria-expanded={reopened.expanded if reopened else 'GONE'!r}, "
                f"clicked at ({x + w / 2}, {y + h / 2}), box={node.box}, "
                f"{len(opts)} option nodes appeared.\n"
                f"Scrollers: {self.scrollers()}"
            )
        return node, opts

    def select_option(self, label: str, value: str, settle_ms: int = 2000) -> str:
        """Open the dropdown captioned ``label`` and choose ``value``.

        Returns the text of the option actually clicked, so a caller can assert
        on what was picked rather than on what it asked for.

        ``value`` matches an option's visible text exactly, or — when that is
        unambiguous — as a case-insensitive substring. The fallback exists
        because an option's VISIBLE text is not always its stored key: the
        engine dropdown shows ``Chrome ("fingerprint-chromium")`` for the key
        ``chromium`` (``src/ui/theme/page.py:150``). Ambiguity raises rather
        than guessing.
        """
        _node, opts = self._open(label)
        texts = [o.text for o in opts]

        hits = [o for o in opts if o.text == value]
        if not hits:
            hits = [o for o in opts if value.lower() in o.text.lower()]
        if len(hits) != 1:
            raise AssertionError(
                f"cannot pick {value!r} from the dropdown captioned {label!r}: "
                f"{'no option matches' if not hits else f'{len(hits)} options match'}. "
                f"Options on screen: {texts}"
            )

        chosen = hits[0]
        ox, oy, ow, oh = chosen.box
        self.page.mouse.click(ox + ow / 2, oy + oh / 2)
        self.page.wait_for_timeout(settle_ms)

        still = [n for n in self.nodes() if n.node_id == chosen.node_id]
        if still and still[0].text == chosen.text and still[0].tappable:
            raise AssertionError(
                f"clicked option {chosen.text!r} in {label!r} but the menu is "
                f"still open — the selection did not take."
            )
        return chosen.text

    def fields(self) -> list[TextField]:
        """Every text field on screen, in DOM order, with what it holds.

        Exists because the alternative — querying a locator inline — is how a
        whole field CLASS went missing without a signal. ``type_into`` indexes
        into exactly this list, so a census and an address can never disagree.
        """
        return [
            TextField(tag=f["tag"], label=f["label"], value=f["value"])
            for f in self.page.evaluate(_FIELDS_JS)
        ]

    def type_into(self, target: int | str, value: str, settle_ms: int = 1200) -> str:
        """Type into a real text field and return what it holds afterwards.

        ``target`` is either a DOM-order index or the field's visible hint text
        (``type_into("optional", ...)``). Real typing through a real element —
        a click, then keystrokes — not a state poke.

        Both single-line and multiline fields are reached: Flutter web backs a
        focused field with a genuine ``<input>``, EXCEPT a multiline one, which
        it backs with a ``<textarea>``. Querying only ``input`` silently skips
        every multiline field on the screen, which is precisely the kind of
        omission this harness must not make.
        """
        found = self.fields()
        index = target if isinstance(target, int) else self._field_index(target, found)
        if index >= len(found):
            raise AssertionError(
                f"no text field at index {index} (found {len(found)}: "
                f"{[f.describe() for f in found]}).\nPresent controls:\n"
                f"{self.describe()}"
            )
        field = self.page.locator(_FIELD_SELECTOR).nth(index)
        field.click()
        self.page.wait_for_timeout(400)
        self.page.keyboard.type(value)
        self.page.wait_for_timeout(settle_ms)
        return field.input_value()

    @staticmethod
    def _field_index(label: str, found: list[TextField]) -> int:
        """Resolve a field by its visible hint text. Loud on 0 or >1 matches.

        MEASURED CAVEAT, and the reason this is offered beside indexing rather
        than instead of it: the ``aria-label`` Flutter exposes is the field's
        HINT, and the hint is dropped once the field holds a value. So a label
        addresses an EMPTY field reliably and a filled one not at all. Ambiguity
        raises rather than guessing, because silently typing into the wrong
        field is the failure this whole harness exists to prevent.
        """
        hits = [i for i, f in enumerate(found) if f.label == label]
        if len(hits) == 1:
            return hits[0]
        problem = "no text field" if not hits else f"{len(hits)} text fields"
        raise AssertionError(
            f"{problem} labelled {label!r}. Fields on screen: "
            f"{[f.describe() for f in found]}. Note a field's label is its "
            f"hint text and disappears once it holds a value — address a "
            f"filled field by index."
        )

    def screenshot(self, path: str) -> str:
        self.page.screenshot(path=path)
        return path
