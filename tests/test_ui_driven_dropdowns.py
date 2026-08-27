"""Choose from real dropdowns in the real persona UI and check what got saved.

This is ticket PS-74. ``test_ui_driven.py`` (PS-71) established that a button
can be pressed and a field typed into; it recorded ``ft.Dropdown`` as the one
control class it could not operate, which mattered because the two settings
that decide what a profile *is* — the declared **OS** and the **engine** — are
both behind that control.

WHY PS-71 CONCLUDED "UNREACHABLE", AND WHAT WAS ACTUALLY WRONG
--------------------------------------------------------------
The options do enter the semantics tree. Two independent addressing faults
stacked on top of each other made it look as though they did not, and fixing
either one alone still fails:

1. **The control was never clicked.** A dropdown surfaces as
   ``<flt-semantics role="button" aria-expanded="false">`` with **empty text** —
   its selected value is painted to canvas and never mirrored into the DOM. So
   addressing it by the value it displays (``has_text="windows"``) matches
   *zero* nodes.

2. **The dialog body scrolls, and the OS dropdown starts below the fold.** The
   scroller measures ``scrollHeight 1248`` against ``clientHeight 592``; the OS
   dropdown sits at ``y=833``, outside the visible band. A control below the
   fold still reports a real bounding box, at coordinates nobody can click, so
   the click lands on empty space and silently does nothing.

A third fact explains why the keyboard route failed too: a dropdown node
carries no ``flt-tappable`` and no ``tabindex`` (a real button carries both),
so Flutter hit-tests it by **coordinate** — it needs a real mouse press at a
genuinely visible position, which is exactly what (1) and (2) prevented.

WHAT IS ASSERTED
----------------
The product's own persisted state, through ``ProfileManager`` — never the DOM
and never the dialog's own widget value. A widget that shows the right label
while the handler writes the default is precisely the defect this guards
against, and reading the widget back would report that defect as a pass.

COST: each test boots a real persona and a real browser. Every option of a
dropdown is exercised inside ONE boot, because the price is almost entirely
fixed startup — see ``UI_DRIVING.md``.
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
    pytest.mark.timeout(900),
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


#: Every option ``build_os_dropdown`` declares, in source order
#: (``src/ui/theme/page.py:130-136``). Pinned as a census so that an option
#: added to or removed from the shipped control fails here rather than
#: silently going uncovered — "every option" is the requirement, so the SET is
#: part of what is being checked, not just one member of it.
_OS_OPTIONS = ("windows", "macos", "linux", "android", "ios")

#: ``build_engine_dropdown`` (``src/ui/theme/page.py:150-151``). The visible
#: text is deliberately NOT the stored key: the option keyed ``chromium``
#: reads ``Chrome ("fingerprint-chromium")``. That gap is the point of driving
#: the second dropdown — a technique that assumed label == key would pass on
#: the OS control and break here.
_ENGINE_OPTIONS = (
    ('Chrome ("fingerprint-chromium")', "chromium"),
    ('Firefox ("invisible_playwright")', "firefox"),
)


def _saved_profiles(home: str) -> list[dict]:
    """Read the product's own state through the SERVICE LAYER.

    In a subprocess because ``src.core.config`` resolves ``PERSONA_HOME`` at
    import time; the test interpreter has already imported it against a
    different home.
    """
    script = textwrap.dedent(
        f"""
        import os, sys, json
        os.environ["PERSONA_HOME"] = {home!r}
        sys.path.insert(0, {REPO_ROOT!r})
        from src.services.profile.manager import ProfileManager
        print(json.dumps([
            {{"name": p.name, "os_type": p.os_type,
              "engine": getattr(p, "engine", None)}}
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


def _os_by_name(home: str) -> dict[str, str]:
    return {p["name"]: p["os_type"] for p in _saved_profiles(home)}


def _dismiss_onboarding(drv: FletDriver) -> None:
    if drv.has_button("Skip"):
        drv.press("Skip")


def _create_with_os(drv: FletDriver, name: str, os_value: str) -> str:
    """The user's gestures: [ + new ] → name it → choose an OS → [ create ]."""
    drv.press("+ new")
    drv.type_into(0, name)
    picked = drv.select_option("Operating system", os_value)
    drv.press("create")
    drv.page.wait_for_timeout(3500)
    return picked


# --------------------------------------------------------------------------
# 1. The control opens, and every declared option is really there
# --------------------------------------------------------------------------


def test_every_declared_option_of_both_dropdowns_is_reachable(_requirements):
    """The census — asserted before anything is selected.

    Two dropdowns built by two different functions, in two different sections
    of the dialog. A technique tuned to one that fails on the other has solved
    an instance, not the control, so both are opened here and both are pinned
    to the full option set their builder declares.

    This is the check that makes "every option" true rather than asserted: if
    a sixth OS ships, or one is dropped, this fails instead of the suite
    quietly covering four of five.
    """
    with serve_app(REPO_ROOT) as app, FletDriver(app.url) as drv:
        _dismiss_onboarding(drv)
        drv.press("+ new")

        assert drv.options("Operating system") == list(_OS_OPTIONS), (
            "the OS dropdown does not offer what build_os_dropdown declares. "
            "Either the shipped options changed, or the menu did not open."
        )
        drv.page.keyboard.press("Escape")
        drv.page.wait_for_timeout(1000)

        assert drv.options("Engine") == [text for text, _key in _ENGINE_OPTIONS], (
            "the Engine dropdown does not offer what build_engine_dropdown "
            "declares."
        )
        drv.page.keyboard.press("Escape")
        drv.page.wait_for_timeout(1000)


# --------------------------------------------------------------------------
# 2. Selecting reaches the product — for EVERY option, not just the second
# --------------------------------------------------------------------------


def test_selecting_each_os_option_reaches_the_saved_profile(_requirements):
    """Choose each of the five OS values and read every one back out.

    All five inside ONE boot: the cost of these tests is almost entirely fixed
    startup, so covering the whole option set costs barely more than covering
    one member of it — and covering only one member is exactly the gap the
    ticket names ("a mechanism that reaches the adjacent item and not the last
    one is a partial answer").

    ``windows`` is included even though it is the default. That looks
    redundant and is not: it is the control for the other four, proving the
    assertion can distinguish a real selection from an untouched dialog.
    """
    expected = {f"os-{value}": value for value in _OS_OPTIONS}

    with serve_app(REPO_ROOT) as app:
        with FletDriver(app.url) as drv:
            _dismiss_onboarding(drv)
            for name, value in expected.items():
                picked = _create_with_os(drv, name, value)
                assert picked == value, (
                    f"asked for OS {value!r} but the option clicked was {picked!r}"
                )

        saved = _os_by_name(app.home)

    missing = [n for n in expected if n not in saved]
    assert not missing, (
        f"these profiles were never created at all: {missing}. Service layer "
        f"holds {saved!r}."
    )
    wrong = {n: (saved[n], want) for n, want in expected.items() if saved[n] != want}
    assert not wrong, (
        "the OS chosen in the dropdown did not reach the saved profile "
        f"(name: got, wanted): {wrong}. The option was clicked and the menu "
        "closed, so the break is between the control and what gets persisted. "
        "Note a default of 'windows' here is the signature of a selection "
        "that was discarded."
    )


def test_selecting_in_the_engine_dropdown_reaches_the_saved_profile(_requirements):
    """The second dropdown, built by a different function, driven the same way.

    Also the axis the Firefox audio defect lives on: a driver that cannot
    switch engines can never exercise both arms of it.

    Asserted through the SERVICE LAYER by stored key (``firefox``), while the
    option was chosen by its visible text (``Firefox ("invisible_playwright")``).
    Those two differing is the whole reason this dropdown is a real second
    case and not a copy of the first.
    """
    name = "engine-firefox"
    label, key = _ENGINE_OPTIONS[1]

    with serve_app(REPO_ROOT) as app:
        with FletDriver(app.url) as drv:
            _dismiss_onboarding(drv)
            drv.press("+ new")
            drv.type_into(0, name)
            picked = drv.select_option("Engine", label)
            assert picked == label
            drv.press("create")
            drv.page.wait_for_timeout(3500)

        saved = {p["name"]: p["engine"] for p in _saved_profiles(app.home)}

    assert name in saved, f"the profile was not created. Service layer: {saved!r}"
    assert saved[name] == key, (
        f"the engine chosen in the dropdown did not reach the saved profile: "
        f"got {saved[name]!r}, wanted {key!r}."
    )


def test_choosing_a_mobile_os_narrows_the_engine_choice(_requirements):
    """Driving one dropdown changes another — proof the real handler ran.

    ``on_os_change`` (``src/ui/dialogs/profile.py:512``) drops the Firefox
    option when a mobile OS is picked, because invisible_playwright is desktop
    Firefox with no mobile mode. Observing that from the outside is evidence
    the selection reached the application's own logic, not merely the widget:
    a driver that faked a selection by poking widget state would leave the
    engine list untouched.
    """
    with serve_app(REPO_ROOT) as app, FletDriver(app.url) as drv:
        _dismiss_onboarding(drv)
        drv.press("+ new")

        before = drv.options("Engine")
        drv.page.keyboard.press("Escape")
        drv.page.wait_for_timeout(1000)
        assert len(before) == 2, f"expected both engines before, got {before}"

        drv.select_option("Operating system", "ios")

        after = drv.options("Engine")
        drv.page.keyboard.press("Escape")
        drv.page.wait_for_timeout(1000)

    assert after == [_ENGINE_OPTIONS[0][0]], (
        f"picking the mobile OS 'ios' should have left chromium as the only "
        f"engine, but the list is {after}. Either the selection never reached "
        f"on_os_change, or the constraint stopped being applied."
    )


# --------------------------------------------------------------------------
# 3. The negative control: does this detect a discarded selection?
# --------------------------------------------------------------------------

#: Throw the OS selection away IN THE SERVED CHILD ONLY, leaving everything
#: else about the control intact: it still opens, still lists all five
#: options, still highlights the one clicked. Only the value the submit
#: handler later reads is pinned to the default.
#:
#: This models the exact defect the ticket names — "a widget that shows the
#: right label while the handler writes the default" — and it is the shape a
#: handler-level unit test cannot see, because the handler is untouched and
#: still perfectly correct.
#:
#: Scoped by OPTION SET rather than by identity: only a dropdown offering
#: ``macos`` and ``ios`` is frozen, which is the OS control and nothing else.
#: ``test_negative_control_is_surgical`` proves the engine dropdown beside it
#: still works, so a red result below cannot be the patch breaking the dialog
#: wholesale.
#:
#: The break is installed on flet's ``value`` DESCRIPTOR, and it has to be.
#: ``ft.Dropdown`` is a dataclass whose ``value`` is a ``Prop`` descriptor
#: backed by an internal ``_values`` store — NOT an ordinary instance
#: attribute. An earlier version of this patch wrapped ``__setattr__``
#: instead; it fired, and the sabotage still did nothing, so the negative
#: control came back green and would have certified a check that cannot fail.
#: Overriding the descriptor's ``__get__`` is what actually intercepts the
#: read the submit handler performs at ``src/ui/dialogs/profile.py:733``.
_DISCARD_OS_SELECTION = textwrap.dedent(
    """
    import flet as ft

    _orig_value = ft.Dropdown.__dict__["value"]

    class _DiscardedSelection:
        # Report the default no matter what the user picked -- but ONLY for
        # the OS dropdown, identified by the options it offers.
        def __get__(self, obj, objtype=None):
            if obj is None:
                return self
            keys = {getattr(o, "key", None)
                    for o in (obj.__dict__.get("options") or [])}
            if {"macos", "ios"} <= keys:
                return "windows"
            return _orig_value.__get__(obj, objtype)

        def __set__(self, obj, value):
            _orig_value.__set__(obj, value)

    ft.Dropdown.value = _DiscardedSelection()
    """
)


def test_driven_selection_fails_when_the_selection_is_discarded(_requirements):
    """Break it on purpose; the selection check must go RED.

    A selection check observed only on a working build has not been observed.
    This is what separates a real check from one that passes because it reads
    back the same widget it just wrote to.
    """
    name = "discarded-selection"
    with serve_app(REPO_ROOT, patch=_DISCARD_OS_SELECTION) as app:
        with FletDriver(app.url) as drv:
            _dismiss_onboarding(drv)
            drv.press("+ new")
            drv.type_into(0, name)

            # The control is still fully operable — that is the point. Nothing
            # about the screen betrays the break.
            assert drv.options("Operating system") == list(_OS_OPTIONS), (
                "the sabotaged build should still OPEN and still list every "
                "option; if it does not, this negative control is proving the "
                "wrong thing."
            )
            picked = drv.select_option("Operating system", "linux")
            assert picked == "linux", "the option should still be clickable"

            drv.press("create")
            drv.page.wait_for_timeout(3500)

        saved = _os_by_name(app.home)

    assert saved.get(name) == "windows", (
        "the negative control FAILED TO FAIL: 'linux' was selected in a build "
        "that discards the selection, and the saved profile does not show the "
        f"default. Service layer holds {saved!r}. If this is not 'windows', "
        "the check above cannot detect a discarded selection and its green is "
        "worthless."
    )


def test_negative_control_is_surgical(_requirements):
    """The sabotage hits the OS dropdown and nothing else.

    Without this, a red negative control could just mean the patch broke the
    dialog wholesale — which would prove nothing about the OS check. The
    engine dropdown is driven under the SAME patch and must still reach the
    product.
    """
    name = "surgical-engine"
    label, key = _ENGINE_OPTIONS[1]

    with serve_app(REPO_ROOT, patch=_DISCARD_OS_SELECTION) as app:
        with FletDriver(app.url) as drv:
            _dismiss_onboarding(drv)
            drv.press("+ new")
            drv.type_into(0, name)
            drv.select_option("Engine", label)
            drv.press("create")
            drv.page.wait_for_timeout(3500)

        saved = {p["name"]: p["engine"] for p in _saved_profiles(app.home)}

    assert saved.get(name) == key, (
        f"under the OS-only sabotage the ENGINE dropdown should still work, "
        f"but the saved engine is {saved.get(name)!r}. The negative control is "
        f"not surgical, so a red result from it would be uninformative."
    )
