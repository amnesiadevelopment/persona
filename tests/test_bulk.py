import pytest

from src.core.strings import get_string
from src.services.profile.bulk import bulk_create, duplicate_names, parse_names
from src.services.profile.manager import ProfileManager


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    pf = tmp_path / "profiles.json"
    dd = tmp_path / "data"
    monkeypatch.setenv("PERSONA_PROFILES_FILE", str(pf))
    monkeypatch.setenv("PERSONA_DATA_DIR", str(dd))
    import src.core.config as cfg
    import src.services.profile.manager as mod

    monkeypatch.setattr(cfg, "PROFILES_FILE", str(pf))
    monkeypatch.setattr(cfg, "DATA_DIR", str(dd))
    monkeypatch.setattr(mod, "PROFILES_FILE", str(pf))
    monkeypatch.setattr(mod, "DATA_DIR", str(dd))
    return ProfileManager()


def test_parse_names_splits_newlines():
    assert parse_names("a\nb\nc") == ["a", "b", "c"]


def test_parse_names_splits_commas():
    assert parse_names("a,b,c") == ["a", "b", "c"]


def test_parse_names_mixed_separators():
    assert parse_names("a, b\nc,d\n") == ["a", "b", "c", "d"]


def test_parse_names_strips_whitespace():
    assert parse_names("  a  \n\t b \n") == ["a", "b"]


def test_parse_names_drops_blanks():
    assert parse_names("a\n\n\n,,\nb") == ["a", "b"]


def test_parse_names_dedups_preserving_order():
    assert parse_names("a\nb\na\nc\nb") == ["a", "b", "c"]


def test_parse_names_empty_string():
    assert parse_names("") == []


def test_bulk_create_all_new(mgr):
    result = bulk_create(mgr, ["alpha", "beta", "gamma"])
    assert result["created"] == ["alpha", "beta", "gamma"]
    assert result["skipped"] == []
    assert set(mgr.profiles) == {"alpha", "beta", "gamma"}


def test_bulk_create_skips_existing(mgr):
    mgr.add_profile("alpha", "", "windows")
    result = bulk_create(mgr, ["alpha", "beta"])
    assert result["created"] == ["beta"]
    assert result["skipped"] == ["alpha"]


def test_bulk_create_skips_duplicates_within_list(mgr):
    result = bulk_create(mgr, ["alpha", "alpha", "beta"])
    assert result["created"] == ["alpha", "beta"]
    assert result["skipped"] == []
    assert set(mgr.profiles) == {"alpha", "beta"}


def test_bulk_create_skips_invalid_names(mgr):
    result = bulk_create(mgr, ["good", "bad/name", "also:bad"])
    assert result["created"] == ["good"]
    assert set(result["skipped"]) == {"bad/name", "also:bad"}


def test_bulk_create_skips_blank_names(mgr):
    result = bulk_create(mgr, ["good", "", "   "])
    assert result["created"] == ["good"]
    assert result["skipped"] == []


def test_bulk_create_passes_attributes(mgr):
    bulk_create(
        mgr, ["x"], proxy="", os_type="macos",
        search_engine="brave", tags=["work"],
    )
    p = mgr.profiles["x"]
    assert p.os_type == "macos"
    assert p.search_engine == "brave"
    assert p.tags == ["work"]


def test_bulk_create_empty_list(mgr):
    # PS-273 widened this ONE assertion, and it is the only shipped assertion
    # the additive `reasons` key moves. It was a whole-dict equality; every
    # other assertion in this file and in test_ps187_os_type_write_doors.py
    # reads `result["skipped"]` / `result["created"]` BY KEY and is unaffected.
    #
    # WIDENED RATHER THAN OMITTING `reasons` WHEN EMPTY, deliberately: an
    # always-present key means a caller writes `result["reasons"]` once and it
    # works on every batch. A conditionally-present key would make the shape
    # depend on the outcome — so a caller that indexes it would work on the
    # failure path and raise KeyError on the SUCCESS path, which is the worst
    # possible place to put a crash. The `reasons == {}` assertion below is
    # what pins the empty case now, and it is a stronger claim than the old
    # equality made: not merely "the dict has this shape" but "nothing was
    # refused, so nothing is explained".
    result = bulk_create(mgr, [])
    assert result["created"] == []
    assert result["skipped"] == []
    assert result["reasons"] == {}


# --- PS-273: the reason for each refusal reaches the caller -----------------
#
# Each of these asserts the REASON TEXT, not that a key exists. A test that
# only checked `name in result["reasons"]` would pass just as happily against
# an implementation that mapped every refusal to the empty string — which is
# the defect (an unexplained refusal) wearing the fix's shape.


def test_bulk_create_reason_for_an_invalid_name(mgr):
    result = bulk_create(mgr, ["good", "bad/name"])

    assert result["created"] == ["good"]
    assert result["skipped"] == ["bad/name"]
    # The sentence validate_profile_name computes, carried out intact — it
    # names the OFFENDING CHARACTER, which is what makes the paste fixable.
    assert result["reasons"]["bad/name"] == (
        "Name contains invalid characters: /"
    )
    # And the working name is not explained, because it was not refused.
    assert "good" not in result["reasons"]


def test_bulk_create_reason_for_a_name_that_already_exists(mgr):
    mgr.add_profile("alpha", "", "windows")

    result = bulk_create(mgr, ["alpha", "beta"])

    assert result["created"] == ["beta"]
    assert result["skipped"] == ["alpha"]
    # Routed through core/strings like the delete lane, so the service holds no
    # user-facing literal of its own.
    #
    # `bulk_create_exists`, NOT `profile_exists` — and the difference is
    # measured, not stylistic: this reason reaches the ACTIVITY LOG, and
    # `log_console.severity()` substring-matches "ready", which "already"
    # contains. "Profile already exists!" therefore classifies as SEV_OK and
    # paints the green SUCCESS dot beside a refusal. severity() is out of
    # scope for PS-273, so the wording avoids the token. Pinned below.
    assert result["reasons"]["alpha"] == get_string("bulk_create_exists")
    assert result["reasons"]["alpha"] == (
        "a profile with that name exists - the existing one was left unchanged"
    )
    from src.ui.log_console import SEV_IDLE, severity

    assert severity(f"Not created: alpha - {result['reasons']['alpha']}") == SEV_IDLE
    assert severity("Not created: alpha - Profile already exists!") != SEV_IDLE, (
        "if this ever passes, severity() stopped matching 'ready' inside "
        "'already' and this lane may go back to using `profile_exists`"
    )


def test_bulk_create_reason_for_an_incoherent_profile(mgr):
    # A non-canonical os_type is unstorable (PS-187), and the whole batch
    # shares one os_type — so EVERY name is refused. That whole-batch refusal
    # is exactly the case that used to reach the operator as a bare integer.
    names = ["one", "two", "three"]

    result = bulk_create(mgr, names, "", "win")

    assert result["created"] == []
    assert sorted(result["skipped"]) == sorted(names)
    # Every refused name carries the reason, and it is the coherence rule's own
    # sentence — the one that says WHICH spelling to use instead. Asserted on
    # content, not on presence.
    for name in names:
        why = result["reasons"][name]
        assert "os_type 'win'" in why
        assert "'windows'" in why, (
            f"the refusal must name the spelling that WOULD work; got {why!r}"
        )


def test_bulk_create_every_skipped_name_is_explained(mgr):
    """The invariant, across all three causes in ONE batch.

    This is the claim the dialog rests on: it renders a line per skipped name,
    so a skipped name with no reason would render a blank explanation.
    """
    mgr.add_profile("exists", "", "windows")

    result = bulk_create(mgr, ["fresh", "exists", "bad/name"])

    assert sorted(result["skipped"]) == ["bad/name", "exists"]
    assert set(result["reasons"]) == set(result["skipped"]), (
        "reasons must cover exactly the skipped names — no gaps, no extras"
    )
    assert all(r.strip() for r in result["reasons"].values()), (
        "an empty reason is an unexplained refusal wearing the fix's shape"
    )


def test_duplicate_names_reports_repeats_in_the_paste(mgr):
    # Repeats are dropped BEFORE the loop, so they land in neither list and
    # `created + skipped` is fewer than the rows pasted. This is what lets the
    # dialog account for the difference instead of leaving it unexplained.
    assert duplicate_names("a\nb\na\nc\nb\nb") == ["a", "b"]
    assert duplicate_names("a\nb\nc") == []
    # A blank row is not a repeat and is not reported — it is not a name the
    # operator asked for.
    assert duplicate_names("a\n\n\n,,\nb") == []
