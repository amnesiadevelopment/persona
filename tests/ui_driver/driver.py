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

_FIELDS_JS = """() => [...document.querySelectorAll('input, textarea')].map(e => ({
  tag: e.tagName,
  label: e.getAttribute('aria-label'),
  value: e.value,
}))"""

_SCRAPE_JS = """() => [...document.querySelectorAll('flt-semantics')].map(e => {
  const r = e.getBoundingClientRect();
  return {
    role: e.getAttribute('role'),
    label: e.getAttribute('aria-label'),
    text: (e.textContent || '').trim(),
    leaf: e.children.length === 0,
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

    def __str__(self) -> str:  # pragma: no cover - diagnostic only
        return f"[{self.role or '-'}] {self.text[:60]!r} box={self.box}"


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
            )
            for n in self.page.evaluate(_SCRAPE_JS)
        ]

    def controls(self) -> list[SemanticNode]:
        """Every addressable control, for diagnosis and for the reach map."""
        return [n for n in self.nodes() if n.role or n.label]

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
