"""PS-296 — the exit country must be NAMED from the code when the provider gave
no name, and the row's flag and its text must never contradict each other.

WHY THESE ASSERT ON RENDERED OUTPUT
-----------------------------------
Every check here reads the STRING the operator sees — the ``ft.Text`` value
pulled out of the control tree ``build_network_page`` actually returns, and the
message string ``proxy_ok_message`` actually returns. None of them asserts that
a helper was called, because "``country_label`` was invoked" is true of a page
that then throws the result away.

THE FOUR RECORD SHAPES, and why all four are here
-------------------------------------------------
``_geo_fields_from_payload`` can produce four ``(code, name)`` combinations and
the two surfaces must agree about each one. PS-268 added the second provider
and with it the ``('PL', '')`` shape, which is the defect; the other three are
here so this change cannot become a rewrite of the shapes that were already
right. In particular ``('PL', 'Poland')`` — the ordinary ipwho.is answer, and
overwhelmingly the common case — is pinned to its EXACT pre-change string, and
``('', '')`` is pinned to rendering no country and no flag.

THE FLAG IS READ FROM THE SAME BUILT ROW as the text, not computed separately.
The whole defect is that the two disagreed within one row while each was
individually defensible: ``_flag_widget`` keys on the CODE and ``_meta_line``
used to key on the NAME. A test that asks ``flag_path('PL')`` directly would
have passed before this fix, because ``flag_path`` was never wrong.
"""

import re

import flet as ft

from src.models.proxy import Proxy
from src.ui.components.network_page import build_network_page
from src.utils.proxy_checker import proxy_ok_message

#: The exact strings the ipwho.is path produced before this change, quoted from
#: a transcript executed at the base commit (6222c47). They are literals rather
#: than anything derived, because their whole job is to catch a drift in the
#: common path — a value computed the way the code computes it would drift with
#: it.
IPWHO_ROW_SEGMENT = "[PL] Poland"
IPWHO_LOG_MESSAGE = "Proxy working. \U0001F1F5\U0001F1F1 [PL] Poland"


def _row(proxy: Proxy):
    """The built row for one proxy, as ``(meta line text, flag control)``.

    Walks the control tree ``build_network_page`` returns rather than calling
    ``_meta_line``/``_flag_widget`` directly, so a change that stops wiring
    either one into the row is caught here instead of passing.
    """
    page = build_network_page(
        [proxy],
        on_add=lambda _: None,
        on_edit=lambda n: None,
        on_delete=lambda n: None,
        on_check=lambda n: None,
        on_rotate=lambda n: None,
    )
    left = page.content.controls[2].controls[0].content.controls[0]
    flag, column = left.controls[0], left.controls[1]
    return column.controls[1].value, flag


def _flag_svg(flag_control) -> str | None:
    """The flag SVG this row paints, or None when it paints the empty box."""
    return flag_control.src if isinstance(flag_control, ft.Image) else None


def _proxy(code: str, name: str) -> Proxy:
    return Proxy(
        name="mob",
        url="socks5://u:p@5.5.5.5:1080",
        country_code=code,
        country_name=name,
        last_ip="5.5.5.5",
        checked_at=1.0,
        last_check_ok=True,
    )


# --- AC1: the ipinfo shape names its country on both surfaces ---------------


def test_row_names_the_country_from_the_code_when_the_name_is_absent():
    """AC1 — code without name renders a country segment on the network row."""
    meta, _flag = _row(_proxy("PL", ""))
    assert "[PL]" in meta, meta
    # And it is a segment of the meta line, not smuggled into another field.
    assert "[PL]" in [part.strip() for part in meta.split("  ·  ")], meta


def test_log_message_names_the_country_from_the_code_when_the_name_is_absent():
    """AC1 — and the Activity Log message names it, WITH the flag emoji."""
    msg = proxy_ok_message("PL", "")
    assert "[PL]" in msg, msg
    assert "\U0001F1F5\U0001F1F1" in msg, msg
    assert msg != "Proxy working.", msg


def test_the_name_is_never_invented_from_the_code():
    """The ticket's explicit non-goal: render the code, do not synthesize a
    name for it. A code->name table would be a second source of truth, which
    ``_geo_fields_from_payload``'s docstring argues against directly."""
    meta, _flag = _row(_proxy("PL", ""))
    assert "Poland" not in meta, meta
    assert "Poland" not in proxy_ok_message("PL", "")


# --- AC3: flag and text agree inside one row --------------------------------


def test_flag_and_text_agree_for_every_reachable_record_shape():
    """AC3 — no state renders a country flag beside a line naming no country.

    This is the defect stated as an invariant over all four shapes rather than
    as a check on the one that broke, so a future provider dialect that lands
    in a different corner cannot reintroduce it quietly.
    """
    for code, name in (("PL", "Poland"), ("PL", ""), ("", "Poland"), ("", "")):
        meta, flag = _row(_proxy(code, name))
        segments = [part.strip() for part in meta.split("  ·  ")]
        # The country segment is the one that is neither the scheme, the IP,
        # nor the clock — identified positionally, since it sits second when
        # present.
        names_a_country = any(
            seg for seg in segments[1:]
            if seg == name or seg.startswith("[")
        )
        has_flag = _flag_svg(flag) is not None
        assert not (has_flag and not names_a_country), (
            f"code={code!r} name={name!r} paints {_flag_svg(flag)!r} beside "
            f"a line that names no country: {meta!r}"
        )


# --- AC4: the healthy ipwho path is untouched -------------------------------


def test_ipwho_record_renders_byte_identically_to_before():
    """AC4 — both fields populated: the common path must not move an inch.

    The WHOLE line is pinned, not just the country segment, so a change that
    "fixes" the country by rearranging the row around it still goes red. The
    clock segment is the one part that cannot be a literal (it counts from
    ``checked_at`` to now), so it is matched by shape and everything else by
    exact equality.
    """
    meta, flag = _row(_proxy("PL", "Poland"))
    segments = meta.split("  ·  ")
    assert segments[:3] == ["socks5", IPWHO_ROW_SEGMENT, "5.5.5.5"], meta
    assert len(segments) == 4, meta
    assert re.fullmatch(r"checked \d+[mhd] ago", segments[3]), meta
    assert _flag_svg(flag) is not None and _flag_svg(flag).endswith("pl.svg")
    assert proxy_ok_message("PL", "Poland") == IPWHO_LOG_MESSAGE


# --- AC5: the zone-only partial still says nothing --------------------------


def test_record_with_neither_field_renders_no_country_and_no_flag():
    """AC5 — the pre-PS-268 partial. Unchanged, and it must stay that way:
    saying nothing is CORRECT when nothing is known."""
    meta, flag = _row(_proxy("", ""))
    segments = meta.split("  ·  ")
    assert segments[:2] == ["socks5", "5.5.5.5"], meta
    assert len(segments) == 3, meta
    assert re.fullmatch(r"checked \d+[mhd] ago", segments[2]), meta
    assert "[" not in meta, meta
    assert _flag_svg(flag) is None
    assert proxy_ok_message("", "") == "Proxy working."


# --- the degraded name-only body, which reaches the UI via the partial -------


def test_name_without_code_renders_the_name_and_no_empty_marker():
    """A body carrying a NAME and no code reaches the row through
    ``_resolve_geo``'s partial fallback. The log used to print ``[] Poland``
    for it — an empty marker asserting a code that does not exist. Same defect
    from the other side, and it falls out of the same rule."""
    meta, flag = _row(_proxy("", "Poland"))
    assert "Poland" in meta, meta
    assert "[]" not in meta, meta
    assert _flag_svg(flag) is None
    msg = proxy_ok_message("", "Poland")
    assert "Poland" in msg and "[]" not in msg, msg


# --- the privacy invariant this message has always carried ------------------


def test_the_new_segment_still_never_carries_the_exit_ip():
    """``proxy_ok_message`` reaches a disk-backed, UI-visible log. Widening
    what it says about the country must not widen what it says about the exit,
    so the IP assertion from tests/test_proxy_log_privacy.py is re-made against
    the shape that changed."""
    msg = proxy_ok_message("PL", "")
    assert not re.search(r"\d{1,3}(?:\.\d{1,3}){3}", msg), msg
    assert ":" not in msg, msg
