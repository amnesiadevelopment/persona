"""Drive the CONDITIONALLY-REVEALED custom-resolution pair in the real UI.

This is ticket PS-219. ``test_ui_driven.py`` (PS-71) established that a button
can be pressed and a field typed into; ``test_ui_driven_dropdowns.py`` (PS-74)
established that an ``ft.Dropdown`` can be opened and chosen from. Every
control either of them drives is **present from the moment the dialog opens**.

``custom_w`` / ``custom_h`` (``src/ui/dialogs/profile.py:373,379``) are the
tier's **first controls that do not exist until another control is operated**.
They carry no visibility flag of their own; they sit inside ``custom_row``
(``:385``), declared ``visible=res_value == "custom"`` (``:387``) and
re-toggled by ``on_res_change`` (``:391-393``), which is wired to
``resolution_dropdown.on_select`` at ``:415``.

WHY THAT DIFFERENCE IS WORTH A TEST
-----------------------------------
PS-71's own history is the argument. The multiline Notes field was recorded as
reachable while ``type_into`` queried ``input`` only — the ``<textarea>`` was
invisible to the driver and **nothing failed**. A control that appears *late*
is exactly where that silence recurs: a driver that never reveals the field
sees "no such field", which reads identically to "the framework cannot render
it". So the reveal is not assumed here, it is **inverted and proven**: the
fields are censused as ABSENT with the dialog open and the dropdown untouched,
the dropdown is then operated, and they are censused again as PRESENT.

WHAT IS ASSERTED
----------------
The product's own persisted state, through ``ProfileManager`` — never the DOM
and never the dialog's own widget value. The stored string is load-bearing
rather than cosmetic: ``resolve_resolution``
(``src/services/browser/resolution.py:97-99``) honours an explicit ``WxH``
as-is, so what lands in the profile is what the browser is later launched at.

COST: each test boots a real persona and a real browser, and that fixed boot —
not the interaction — is what you pay for. Every assertion that can share a
boot does. Measured figures live in ``UI_DRIVING.md#cost``, which owns them.
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


#: The dropdown's addressable caption. NOT "Resolution" — the control is
#: wrapped by ``labeled("Screen resolution", resolution_dropdown, ...)``
#: (``src/ui/dialogs/profile.py:442-447``), and ``FletDriver.find_dropdown``
#: locates a dropdown by the caption node directly above and left-aligned with
#: it, so the exact string is load-bearing.
_RESOLUTION_CAPTION = "Screen resolution"

#: The option's VISIBLE text, which is not its stored key: the option keyed
#: ``custom`` reads ``Custom…`` with a real ellipsis character
#: (``src/ui/dialogs/profile.py:406``). ``select_option`` picks by visible
#: text — the same label≠key gap the Engine dropdown exercises.
_CUSTOM_OPTION = "Custom…"

#: Every option ``resolution_dropdown`` declares, in source order
#: (``src/ui/dialogs/profile.py:361-369, 397-407``). Pinned as a census so an
#: option added to or removed from the shipped control fails here rather than
#: silently going uncovered.
_RESOLUTION_OPTIONS = (
    "Auto (random)",
    "2560 x 1440  (2K QHD)",
    "1920 x 1080  (Full HD)",
    "1600 x 900  (HD+)",
    "1536 x 864  (HD+)",
    "1440 x 900  (WXGA+)",
    "1366 x 768  (HD)",
    "1280 x 800  (WXGA)",
    _CUSTOM_OPTION,
)

#: The hint text of each revealed field, which is also its address while it is
#: still EMPTY. ``_field_index`` resolves a field by its hint and the hint is
#: dropped once the field holds a value, so these address the pair on FIRST
#: fill and never again — after typing, address them by index.
_WIDTH, _HEIGHT = "width", "height"

#: Verbatim from ``src/ui/dialogs/profile.py:923``. Pinned so a reworded error
#: fails here rather than letting the invalid-branch check pass on any red text.
_INVALID_RES_ERROR = "Enter a valid custom resolution (e.g. 1920 x 1080)"

#: A value a user would plausibly type that the product must nonetheless
#: reject: 640x480 parses as two integers and fails ``parse_resolution``'s
#: sanity floor (``_MIN_W, _MIN_H = 800, 600``,
#: ``src/services/browser/resolution.py:58``). Deliberately preferred over
#: garbage input — it proves the branch rejects on SEMANTICS, not merely on a
#: failed ``int()``.
_TOO_SMALL = ("640", "480")

#: A valid custom pair that is deliberately NOT one of the presets, so a
#: profile carrying it cannot be the result of a preset being chosen by
#: accident or of a default leaking through.
_VALID_CUSTOM = ("1728", "1117")


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
            {{"name": p.name, "resolution": getattr(p, "resolution", None)}}
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
        encoding="utf-8",
    )
    if out.returncode != 0:
        raise AssertionError(f"service-layer read failed:\n{out.stdout}\n{out.stderr}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def _resolution_by_name(home: str) -> dict[str, str | None]:
    return {p["name"]: p["resolution"] for p in _saved_profiles(home)}


def _dismiss_onboarding(drv: FletDriver) -> None:
    if drv.has_button("Skip"):
        drv.press("Skip")


def _field_labels(drv: FletDriver) -> list[str | None]:
    """The census this whole module turns on: which fields exist RIGHT NOW."""
    return [f.label for f in drv.fields()]


def _assert_custom_pair_absent(drv: FletDriver, when: str) -> None:
    labels = _field_labels(drv)
    assert _WIDTH not in labels and _HEIGHT not in labels, (
        f"the custom width/height fields are already on screen {when}, so this "
        f"test cannot prove the dropdown REVEALED them — it would pass against "
        f"a build where the reveal does nothing. Fields present: {labels}"
    )


def _assert_custom_pair_present(drv: FletDriver, when: str) -> None:
    labels = _field_labels(drv)
    missing = [f for f in (_WIDTH, _HEIGHT) if f not in labels]
    assert not missing, (
        f"{missing} did not appear {when}. The option was clicked and the menu "
        f"closed, so either on_res_change never ran or the revealed row is not "
        f"reaching the DOM. Fields present: {labels}"
    )


def _open_custom_resolution(drv: FletDriver) -> str:
    """Operate the dropdown, PROVING it was hit before anything is concluded.

    A negative reach result is a claim about the DRIVER at least as much as
    about the framework (PS-74's recorded lesson), so ``aria-expanded``
    flipping is asserted here rather than inferred from a later absence.
    Returns the text of the option actually clicked.
    """
    assert not drv.find_dropdown(_RESOLUTION_CAPTION).is_open, (
        "the resolution dropdown reports itself already open before it was "
        "touched; aria-expanded cannot then be used as proof of a hit."
    )

    # ``options()`` leaves the menu OPEN, which is what makes the flip
    # observable on the control itself.
    offered = drv.options(_RESOLUTION_CAPTION)
    assert drv.find_dropdown(_RESOLUTION_CAPTION).is_open, (
        "the resolution dropdown listed options without aria-expanded going "
        "true — the control was not actually hit, so nothing measured after "
        "this point is evidence about the product."
    )
    assert offered == list(_RESOLUTION_OPTIONS), (
        "the resolution dropdown does not offer what the dialog declares. "
        f"Offered: {offered}"
    )
    drv.page.keyboard.press("Escape")
    drv.page.wait_for_timeout(1000)

    picked = drv.select_option(_RESOLUTION_CAPTION, _CUSTOM_OPTION)
    assert picked == _CUSTOM_OPTION, (
        f"asked for {_CUSTOM_OPTION!r} but the option clicked was {picked!r}"
    )
    drv.page.wait_for_timeout(1500)
    return picked


# --------------------------------------------------------------------------
# 1. The reveal, inverted — and what it reveals reaches the saved profile
# --------------------------------------------------------------------------


def test_the_dropdown_reveals_the_custom_pair_and_the_typed_size_is_stored(
    _requirements,
):
    """The tier's first conditionally-revealed control, end to end.

    Three claims in ONE boot, because the price is almost entirely fixed
    startup:

    1. **Premise inversion.** With the dialog open and the dropdown untouched,
       no ``width``/``height`` field exists. Without this the test would pass
       just as well against a build whose reveal does nothing at all — it
       would be asserting that two always-present fields are present.
    2. **The reveal is caused.** After the dropdown is operated (and proven
       hit), both fields exist.
    3. **What is typed reaches the product.** Not the widget and not the
       screen: ``ProfileManager`` is read in a subprocess and must carry the
       exact ``WxH`` string. ``1728x1117`` is not a preset, so it cannot be
       the signature of a default leaking through.
    """
    name = "ps219-custom-resolution"
    width, height = _VALID_CUSTOM
    want = f"{width}x{height}"

    with serve_app(REPO_ROOT) as app:
        assert _saved_profiles(app.home) == [], "isolated home was not empty to start"

        with FletDriver(app.url) as drv:
            _dismiss_onboarding(drv)
            drv.press("+ new")

            # (1) The fields must not be there yet.
            _assert_custom_pair_absent(drv, "before the dropdown was operated")

            # (2) Operate the control, then re-census.
            _open_custom_resolution(drv)
            _assert_custom_pair_present(drv, "after choosing Custom…")

            # Addressed by HINT, which is legitimate only because both fields
            # are still empty — ``custom_w``/``custom_h`` are constructed with
            # value="" whenever the dialog did not open on a custom resolution
            # (``src/ui/dialogs/profile.py:375,381``), which is the case for a
            # fresh create dialog. After this, they are addressable by index
            # only.
            assert drv.type_into(_WIDTH, width) == width
            assert drv.type_into(_HEIGHT, height) == height

            drv.type_into(0, name)
            drv.press("create")
            drv.page.wait_for_timeout(4000)

        saved = _resolution_by_name(app.home)

    assert name in saved, (
        f"the profile was not created at all. Service layer holds {saved!r}."
    )
    assert saved[name] == want, (
        f"the custom resolution typed into the revealed fields did not reach "
        f"the saved profile: got {saved[name]!r}, wanted {want!r}. The fields "
        f"accepted the keystrokes, so the break is between the control and "
        f"what gets persisted. Note a value of 'auto' here is the signature of "
        f"a custom selection that was discarded."
    )


# --------------------------------------------------------------------------
# 2. The invalid branch: rejected on SEMANTICS, and nothing is stored
# --------------------------------------------------------------------------


def test_an_undersized_custom_resolution_is_refused_and_nothing_is_stored(
    _requirements,
):
    """``640x480`` parses fine and is still not a screen. Both halves asserted.

    The error being *shown* is not enough on its own: a dialog that complains
    and saves anyway is precisely the defect worth catching, so the service
    layer is read too and must hold nothing.

    The second half of the test then creates a VALID custom profile in the
    same boot. That is the control for the first: without it, a red-and-empty
    result could equally mean the dialog had simply stopped working, and the
    rejection would prove nothing about the floor at
    ``src/services/browser/resolution.py:58``.
    """
    rejected = "ps219-too-small"
    accepted = "ps219-recovered"
    small_w, small_h = _TOO_SMALL
    good_w, good_h = _VALID_CUSTOM

    with serve_app(REPO_ROOT) as app:
        with FletDriver(app.url) as drv:
            _dismiss_onboarding(drv)
            drv.press("+ new")
            drv.type_into(0, rejected)
            _open_custom_resolution(drv)
            _assert_custom_pair_present(drv, "after choosing Custom…")
            assert drv.type_into(_WIDTH, small_w) == small_w
            assert drv.type_into(_HEIGHT, small_h) == small_h

            drv.press("create")
            drv.page.wait_for_timeout(3000)

            shown = [n.text for n in drv.nodes() if n.text == _INVALID_RES_ERROR]
            assert shown, (
                f"submitting {small_w}x{small_h} — below the {800}x{600} floor — "
                f"surfaced no error. The exact string expected is "
                f"{_INVALID_RES_ERROR!r}; if the wording changed, this pin needs "
                f"updating, and if nothing was shown the user was told nothing."
            )
            assert drv.has_button("create"), (
                "the dialog closed on an invalid resolution, so the user has no "
                "chance to correct it."
            )

            after_refusal = _resolution_by_name(app.home)
            assert rejected not in after_refusal, (
                f"an undersized resolution was REFUSED on screen and the "
                f"profile was persisted anyway: {after_refusal!r}. Showing an "
                f"error while saving is worse than either alone."
            )

            # The control: the same dialog, same path, a legal value.
            drv.press("cancel")
            drv.page.wait_for_timeout(1500)
            drv.press("+ new")
            drv.type_into(0, accepted)
            _assert_custom_pair_absent(drv, "in a freshly reopened dialog")
            _open_custom_resolution(drv)
            _assert_custom_pair_present(drv, "after choosing Custom… again")
            assert drv.type_into(_WIDTH, good_w) == good_w
            assert drv.type_into(_HEIGHT, good_h) == good_h
            drv.press("create")
            drv.page.wait_for_timeout(4000)

        saved = _resolution_by_name(app.home)

    assert saved.get(accepted) == f"{good_w}x{good_h}", (
        f"the recovery half failed: a LEGAL custom resolution did not save "
        f"either ({saved!r}). Without this the refusal above is uninformative — "
        f"it could just mean the dialog stopped working."
    )
    assert rejected not in saved, (
        f"the refused profile appeared in the service layer after all: {saved!r}"
    )


# --------------------------------------------------------------------------
# 3. Falsification A: break the REVEAL, and the premise inversion must go RED
# --------------------------------------------------------------------------

#: Pin ``custom_row`` hidden IN THE SERVED CHILD ONLY. Everything else about
#: the control is left intact: the resolution dropdown still opens, still
#: lists every option, and ``Custom…`` is still clickable and still selected.
#: Only the row that reveals the two fields never becomes visible.
#:
#: This models the exact failure a conditionally-revealed control invites — the
#: field never appears, which to a driver is indistinguishable from "the
#: framework cannot render it" — and it is what
#: ``_assert_custom_pair_present`` exists to catch.
#:
#: TWO measured traps had to be cleared to make this sabotage actually
#: sabotage, and both produced a green negative control that certified nothing:
#:
#: 1. **``visible`` is a ``Prop`` DESCRIPTOR**, not an ordinary attribute — the
#:    same lesson ``test_ui_driven_dropdowns.py`` records for ``ft.Dropdown``,
#:    recurring here on ``ft.Row``. A ``__setattr__`` wrapper FIRES and
#:    sabotages nothing, because the descriptor's ``__set__`` is what the
#:    assignment goes through.
#: 2. **``controls`` is NOT in ``_values``.** ``Prop`` stores only non-default
#:    values there; ``controls`` lives in the instance ``__dict__``. A detector
#:    reading ``obj._values["controls"]`` matches NO row, so the patch installs
#:    cleanly, runs on every row, and changes nothing at all — measured, and
#:    green. The detector below reads the attribute.
#:
#: Scoped by CONTENT rather than by identity: only a row containing fields
#: labelled ``width`` and ``height`` is frozen, which is ``custom_row`` and
#: nothing else. The surgical half of the test below proves the rest of the
#: dialog — including the resolution dropdown's own preset path — still works.
_FREEZE_THE_REVEAL = textwrap.dedent(
    """
    import flet as ft

    _orig_visible = next(
        k.__dict__["visible"] for k in ft.Row.__mro__ if "visible" in k.__dict__
    )

    def _is_custom_row(obj):
        labels = {
            getattr(c, "label", None)
            for c in (getattr(obj, "controls", None) or [])
        }
        return {"width", "height"} <= labels

    class _FrozenReveal:
        def __get__(self, obj, objtype=None):
            if obj is None:
                return self
            if _is_custom_row(obj):
                return False
            return _orig_visible.__get__(obj, objtype)

        def __set__(self, obj, value):
            if value and _is_custom_row(obj):
                return
            _orig_visible.__set__(obj, value)

    ft.Row.visible = _FrozenReveal()
    """
)


def test_the_reveal_check_fails_when_the_row_can_never_be_shown(_requirements):
    """Break the reveal on purpose; the reveal check must go RED.

    A reveal observed only on a working build has not been observed. This is
    what separates a real check from one that would pass on a build where
    choosing ``Custom…`` does nothing whatsoever.

    The SURGICAL half runs in the same boot: under the identical patch, the
    resolution dropdown's PRESET path must still reach the saved profile. That
    forecloses the reading that a red result above merely means the patch broke
    the dialog wholesale — which would prove nothing about the reveal.
    """
    name = "ps219-surgical-preset"

    with serve_app(REPO_ROOT, patch=_FREEZE_THE_REVEAL) as app:
        with FletDriver(app.url) as drv:
            _dismiss_onboarding(drv)
            drv.press("+ new")
            _assert_custom_pair_absent(drv, "before the dropdown was operated")

            # The control is still fully operable — that is the point. Nothing
            # about the screen betrays the break.
            picked = _open_custom_resolution(drv)
            assert picked == _CUSTOM_OPTION, (
                "the sabotaged build should still OPEN the dropdown and still "
                "select Custom…; if it does not, this negative control is "
                "proving the wrong thing."
            )

            labels = _field_labels(drv)
            assert _WIDTH not in labels and _HEIGHT not in labels, (
                "the negative control FAILED TO FAIL: custom_row is pinned "
                "hidden in this build, and the width/height fields appeared "
                f"anyway ({labels}). Either the patch did not install — see the "
                "two measured traps recorded above it — or the reveal check in "
                "this module cannot detect a reveal that never happens, and its "
                "green is worthless."
            )

            # --- surgical control, same boot, same patch ---
            drv.press("cancel")
            drv.page.wait_for_timeout(1500)
            drv.press("+ new")
            drv.type_into(0, name)
            preset = drv.select_option(_RESOLUTION_CAPTION, "1920 x 1080")
            assert preset == "1920 x 1080  (Full HD)", (
                f"the preset option should still be clickable, got {preset!r}"
            )
            drv.press("create")
            drv.page.wait_for_timeout(4000)

        saved = _resolution_by_name(app.home)

    assert saved.get(name) == "1920x1080", (
        f"under the custom-row-only sabotage a PRESET resolution should still "
        f"reach the saved profile, but it is {saved.get(name)!r}. The negative "
        f"control is not surgical, so a red result from it would be "
        f"uninformative."
    )


# --------------------------------------------------------------------------
# 4. Falsification B: break the SUBMIT ASSEMBLY, and the stored-state check
#    must go RED
# --------------------------------------------------------------------------

#: Discard what was TYPED into the two fields, IN THE SERVED CHILD ONLY,
#: reporting a fixed value to whoever reads ``.value``. The fields still
#: appear, still accept keystrokes, and still SHOW what was typed — the DOM
#: element is real and untouched. Only the value the submit handler reads at
#: ``src/ui/dialogs/profile.py:920-921`` is replaced.
#:
#: This is the counterpart of the reveal sabotage and it guards a different
#: assertion: the reveal patch falsifies the CENSUS, this one falsifies the
#: STORED-STATE check. A build could reveal both fields perfectly and still
#: persist something the user never typed, and no census would notice.
#:
#: Installed on the ``value`` DESCRIPTOR for the reason recorded above and in
#: ``test_ui_driven_dropdowns.py``: ``ft.TextField.value`` is a ``Prop``, so
#: intercepting ``__get__`` is what reaches the read the handler performs.
#:
#: ``1234x1234`` is deliberately a LEGAL resolution — it clears the 800x600
#: floor — so the sabotaged build takes the success path and stores something
#: wrong, rather than tripping the invalid branch and storing nothing. A patch
#: that merely blocked creation would let a check that never looks at the
#: stored VALUE come back green.
_DISCARD_THE_TYPED_SIZE = textwrap.dedent(
    """
    import flet as ft

    _orig_value = ft.TextField.__dict__["value"]

    class _DiscardedSize:
        # Report a fixed size no matter what the user typed -- but ONLY for
        # the two custom-resolution fields, identified by their label.
        def __get__(self, obj, objtype=None):
            if obj is None:
                return self
            if obj._values.get("label") in ("width", "height"):
                return "1234"
            return _orig_value.__get__(obj, objtype)

        def __set__(self, obj, value):
            _orig_value.__set__(obj, value)

    ft.TextField.value = _DiscardedSize()
    """
)


def test_the_stored_size_check_fails_when_the_typed_size_is_discarded(
    _requirements,
):
    """Break the submit assembly on purpose; the stored-state check must go RED.

    Same gestures, same assertion, one deliberate defect — the revealed fields
    render, accept typing and display it, while the handler reads something
    else. That is the shape a screen-level assertion cannot see and a
    handler-level unit test cannot see either, because the handler is untouched
    and still perfectly correct.

    The profile NAME is asserted to survive in the same read: it comes from a
    ``TextField`` too, so a patch that had clobbered every field would have
    lost it, and this test would be measuring a broken dialog rather than a
    discarded size.
    """
    name = "ps219-discarded-size"
    width, height = _VALID_CUSTOM

    with serve_app(REPO_ROOT, patch=_DISCARD_THE_TYPED_SIZE) as app:
        with FletDriver(app.url) as drv:
            _dismiss_onboarding(drv)
            drv.press("+ new")
            drv.type_into(0, name)
            _open_custom_resolution(drv)
            _assert_custom_pair_present(drv, "after choosing Custom…")

            # The fields are still real and still show what was typed. Nothing
            # on the screen betrays the break.
            assert drv.type_into(_WIDTH, width) == width
            assert drv.type_into(_HEIGHT, height) == height

            drv.press("create")
            drv.page.wait_for_timeout(4000)

        saved = _resolution_by_name(app.home)

    assert name in saved, (
        f"the size-discarding patch is not surgical — the profile itself was "
        f"not created, so this proves nothing about the resolution. Service "
        f"layer holds {saved!r}."
    )
    assert saved[name] == "1234x1234", (
        f"the negative control FAILED TO FAIL: {width}x{height} was typed into "
        f"a build that discards it, and the saved profile holds "
        f"{saved[name]!r} rather than the patched '1234x1234'. That means the "
        f"stored-size check in this module cannot detect a discarded value and "
        f"its green is worthless."
    )
