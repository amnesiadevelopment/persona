"""Press real buttons in the real persona UI and check the product changed.

This is the demonstration ticket PS-71 exists to produce. Every other UI test
in this repo calls a handler and inspects state; these tests operate the
CONTROL — find it by the text a user reads, click it, type into the field — and
then observe the result through the SERVICE LAYER, not through the screen.

The distinction is the whole point. A handler test passes when the function is
correct even if the button was never wired to it, is disabled, or is not on the
screen at all. These fail in every one of those cases, which
``test_driven_test_fails_when_the_button_is_unwired`` proves by breaking the
wiring on purpose and watching the same path go red.

COST: each test boots a real persona and a real browser, and that fixed boot —
not the interaction — is what you pay for, so driving more controls inside one
test is close to free. Measured figures live in ``tests/UI_DRIVING.md#cost``,
which owns them; they are deliberately not restated here, because a copied
number goes stale the moment this module gains a test. These are marked
``ui_driver`` and skipped unless the environment can supply both.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

from tests.ui_driver import FletDriver, serve_app
from tests.ui_driver.driver import SYSTEM_CHROMIUM

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pytestmark = [
    pytest.mark.ui_driver,
    pytest.mark.requires_capability("ui_driver"),
    pytest.mark.timeout(600),
]


@pytest.fixture(scope="module")
def _requirements() -> None:
    """Refuse to pretend. Each guard names precisely what is missing."""
    pytest.importorskip(
        "flet", reason="flet not installed: the UI cannot be served to drive"
    )
    pytest.importorskip(
        "playwright.sync_api",
        reason="playwright not installed: nothing can drive the UI",
    )
    if not os.path.exists(SYSTEM_CHROMIUM):
        pytest.skip(f"chromium not runnable here: {SYSTEM_CHROMIUM} is absent")


def _saved_profiles(home: str) -> list[dict]:
    """Read the product's own state through the SERVICE LAYER.

    In a subprocess because ``src.core.config`` resolves ``PERSONA_HOME`` at
    import time; the test interpreter has already imported it against a
    different home, and re-importing in-process is unreliable.

    Returns the fields the driven tests assert on, not just names: a field is
    only proven reachable if what was TYPED lands in the persisted product.
    """
    script = textwrap.dedent(
        f"""
        import os, sys, json
        os.environ["PERSONA_HOME"] = {home!r}
        sys.path.insert(0, {REPO_ROOT!r})
        from src.services.profile.manager import ProfileManager
        print(json.dumps([
            {{"name": p.name, "notes": p.notes, "tags": list(p.tags)}}
            for p in ProfileManager().list_profiles()
        ]))
        """
    )
    out = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=120,
     encoding="utf-8")
    if out.returncode != 0:
        raise AssertionError(f"service-layer read failed:\n{out.stdout}\n{out.stderr}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def _profiles_in(home: str) -> list[str]:
    """Just the names — the shape most assertions want."""
    return [p["name"] for p in _saved_profiles(home)]


def _dismiss_onboarding(drv: FletDriver) -> None:
    """First run shows the welcome dialog; a re-run against the same home does
    not. Handle both rather than assuming either."""
    if drv.has_button("Skip"):
        drv.press("Skip")


def _create_profile_through_the_ui(drv: FletDriver, name: str) -> str:
    """The user's actual gestures: [ + new ] -> type a name -> [ create ]."""
    _dismiss_onboarding(drv)
    drv.press("+ new")
    typed = drv.type_into(0, name)
    drv.press("create")
    drv.page.wait_for_timeout(4000)
    return typed


#: The create-profile dialog's three VISIBLE text fields, in DOM order, with
#: the tag Flutter web backs each one with. The third is the one that matters:
#: ``notes_field`` is ``multiline=True`` (``src/ui/dialogs/profile.py:705``),
#: and Flutter backs a multiline field with a ``<textarea>``, not an
#: ``<input>``. A driver querying only ``input`` reaches the first two and is
#: blind to the third while reporting success — a whole control class missing
#: with no signal. That is why the census below is asserted by TAG.
_EXPECTED_FIELDS = (
    ("INPUT", "e.g. Amazon US Shopper"),  # name
    ("INPUT", "shopping, us, amazon"),  # tags
    ("TEXTAREA", "optional"),  # notes -- multiline
)


# --------------------------------------------------------------------------
# 1. The mechanism itself: are the shipped controls addressable at all?
# --------------------------------------------------------------------------


def test_real_controls_are_addressable_in_the_shipped_ui(_requirements):
    """The premise everything else rests on, asserted rather than assumed.

    Not "the window opened" — that proves nothing. This asserts that specific
    SHIPPED controls, named by the text a user reads, are present as real
    addressable nodes with non-zero size.
    """
    with serve_app(REPO_ROOT) as app, FletDriver(app.url) as drv:
        _dismiss_onboarding(drv)
        controls = drv.controls()
        assert controls, f"no addressable controls at all:\n{drv.describe()}"

        # Main-window controls a user can point at.
        for label in ("+ new", "profiles", "network", "bookmarks", "certificates", "trash"):
            assert drv.has_button(label), (
                f"shipped control {label!r} is not addressable:\n{drv.describe()}"
            )

        buttons = [n for n in controls if n.role == "button"]
        assert buttons, "semantics tree has no buttons"
        assert all(n.box[2] > 0 and n.box[3] > 0 for n in buttons if n.text), (
            "addressable buttons report zero size, so they cannot be pressed"
        )


# --------------------------------------------------------------------------
# 2. The real path, driven through the controls, observed in the service layer
# --------------------------------------------------------------------------


def test_creating_a_profile_through_the_controls_persists_it(_requirements):
    """Drive the create-profile path and observe the PRODUCT, not the screen.

    The name is typed into the real field and the real ``[ create ]`` button is
    pressed; the assertion then reads ProfileManager. A repaint cannot satisfy
    this, and neither can a correct handler that nothing is wired to.
    """
    name = "ps71-driven-profile"
    with serve_app(REPO_ROOT) as app:
        assert _profiles_in(app.home) == [], "isolated home was not empty to start"

        with FletDriver(app.url) as drv:
            typed = _create_profile_through_the_ui(drv, name)
            assert typed == name, f"the field did not accept the typed name: {typed!r}"

        after = _profiles_in(app.home)

    assert name in after, (
        f"pressing [ create ] did not create the profile. Service layer holds "
        f"{after!r}. The control was found and pressed, so the failure is in "
        f"the path behind it."
    )


def test_every_visible_text_field_is_reachable_including_the_multiline_one(
    _requirements,
):
    """The reach map's text-field row, asserted instead of claimed.

    This exists because the first version of that row was WRONG: it called text
    fields reachable while the driver queried ``input`` only, so the multiline
    Notes field — a ``<textarea>`` — was silently unreachable and the map said
    otherwise. A prose claim about reach is worth nothing; this drives it.

    Two halves, and the census is the half that matters. Asserting only that
    typing works would still pass a driver blind to a whole control class, so
    the census pins the exact tags the dialog renders: if a future flet backs a
    field with something new, this fails and the map gets corrected rather than
    quietly drifting out of true.
    """
    with serve_app(REPO_ROOT) as app, FletDriver(app.url) as drv:
        _dismiss_onboarding(drv)
        drv.press("+ new")

        found = drv.fields()
        assert [(f.tag, f.label) for f in found] == list(_EXPECTED_FIELDS), (
            "the create-profile dialog does not render the fields this map "
            f"claims. Found: {[f.describe() for f in found]}"
        )
        assert sum(f.multiline for f in found) == 1, (
            "expected exactly one multiline field (Notes); a driver querying "
            f"only 'input' would miss it. Found: {[f.describe() for f in found]}"
        )

        # Every field accepts real typing through a real element.
        for index, (tag, _label) in enumerate(_EXPECTED_FIELDS):
            value = f"reach-{index}"
            got = drv.type_into(index, value)
            assert got == value, (
                f"field {index} ({tag}) did not accept typing: {got!r}. This is "
                f"the multiline case if tag is TEXTAREA."
            )


def test_typing_into_the_multiline_field_reaches_the_saved_product(_requirements):
    """The multiline field is reachable *and the value lands in the product*.

    Reaching a control is not the same as operating it. The census test above
    proves the ``<textarea>`` accepts keystrokes; this proves those keystrokes
    survive ``[ create ]`` and appear in what ProfileManager persists — the
    only evidence that the field is wired to anything.
    """
    name = "ps71-multiline-profile"
    notes = "driven-notes-through-the-textarea"
    with serve_app(REPO_ROOT) as app:
        with FletDriver(app.url) as drv:
            _dismiss_onboarding(drv)
            drv.press("+ new")
            # Address the multiline field by its visible hint, the way a user
            # identifies it. Legitimate here because the field is still empty.
            assert drv.type_into("optional", notes) == notes
            drv.type_into(0, name)
            drv.press("create")
            drv.page.wait_for_timeout(4000)

        saved = _saved_profiles(app.home)

    match = [p for p in saved if p["name"] == name]
    assert match, f"the profile was not created at all. Service layer holds {saved!r}"
    assert match[0]["notes"] == notes, (
        f"the multiline Notes field did not reach the saved profile: "
        f"{match[0]['notes']!r}. The text was typed and accepted by the "
        f"element, so the break is between the control and the service layer."
    )


# --------------------------------------------------------------------------
# 3. The negative control: does this test actually detect a broken UI?
# --------------------------------------------------------------------------

#: Disconnect the create button from its handler IN THE SERVED CHILD ONLY, by
#: replacing the submit callback the dialog wires to ``ft.Button(on_click=...)``.
#: The button still renders, still reads "[ create ]", and still depresses —
#: exactly the defect a handler-level unit test cannot see, because the handler
#: it calls directly is untouched and still perfectly correct.
_UNWIRE_CREATE_BUTTON = textwrap.dedent(
    """
    import flet as ft
    _real_button = ft.Button

    class _DeadCreateButton(ft.Button):
        def __init__(self, *a, **kw):
            content = a[0] if a else kw.get("content")
            if isinstance(content, str) and "create" in content:
                kw.pop("on_click", None)
                if a:
                    a = (content,) + a[1:]
                kw["on_click"] = None
            super().__init__(*a, **kw)

    ft.Button = _DeadCreateButton
    """
)


def test_driven_test_fails_when_the_button_is_unwired(_requirements):
    """Break the wiring on purpose; the driven path must go RED.

    A UI driver observed only on a working screen has not been observed. This
    is the check that distinguishes a real driver from a test that always
    passes: same gestures, same assertion, one deliberate defect — the
    ``[ create ]`` button rendered but connected to nothing.
    """
    name = "ps71-should-not-exist"
    with serve_app(REPO_ROOT, patch=_UNWIRE_CREATE_BUTTON) as app:
        with FletDriver(app.url) as drv:
            _dismiss_onboarding(drv)
            drv.press("+ new")

            # The control is still THERE and still pressable. That is the
            # point: nothing about the screen betrays the break.
            assert drv.has_button("create"), (
                "the unwired build should still RENDER [ create ] — if it does "
                "not, this negative control is proving the wrong thing:\n"
                f"{drv.describe()}"
            )
            typed = drv.type_into(0, name)
            assert typed == name
            drv.press("create")
            drv.page.wait_for_timeout(4000)

        after = _profiles_in(app.home)

    assert name not in after, (
        "the negative control FAILED TO FAIL: the profile was created even "
        "though [ create ] was disconnected from its handler. That means the "
        "driven test above cannot detect an unwired button and its green is "
        f"worthless. Service layer holds {after!r}."
    )
