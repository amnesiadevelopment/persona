"""PS-26: the Chromium engine's version is something persona DECIDED.

Two concerns, one path:

1. **The recorded-version bug.** ``_update_engine_async`` fetched a fresh tag,
   discarded it, and wrote ``self._engine_latest`` — the tag from the EARLIER
   background check. Normally identical, so this was latent; they diverge when
   upstream publishes between the check and the click, and then the bytes
   installed are the new release's while ``version.txt`` says the old tag.
   ``version.txt`` feeds ``current_version()`` → ``is_newer()`` and (since
   PS-19) every verification snapshot's ``engine_build`` header, so a stale
   value mislabels a snapshot and can make the next real update look
   already-installed.

2. **Governance.** Chromium had no known-bad list and no compatibility ceiling:
   "is it newer?" was the only question asked. Firefox has both. These pin that
   a refused build is refused, is REPORTED as a refusal rather than as a failed
   download, and — critically — that a normal newer build still installs exactly
   as it did before, because a guard that makes routine updating harder is a
   guard that gets routed around.
"""

import json
from types import SimpleNamespace

import src.ui.app as app_mod
from src.services.engine import policy, updater


class InlineThread:
    """Run the worker body synchronously so the assertions see its effects."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


def _stub(**over):
    """An App-shaped stub carrying only what the engine-update path touches.

    ``_refresh_engine_text`` RECORDS its calls into ``.renders`` rather than
    being a no-op. That distinction is load-bearing: a no-op stub made
    "_engine_status was assigned" assertable along a path where the value was
    never painted, which is exactly how a refusal that left the row wedged on
    "downloading..." passed a green suite. Assert the render, not the attribute.
    """
    renders: list = []
    base = dict(
        _engine_busy=False,
        _engine_latest="148.0.7778.215",
        _engine_status="",
        _engine_detail=SimpleNamespace(value=""),
        _engine_progress_start=lambda: None,
        _refresh_engine_text=lambda *a: renders.append(a[0] if a else ""),
        _log=lambda m: None,
        _engine_progress_cb=lambda d, t: None,
    )
    base.update(over)
    stub = SimpleNamespace(**base)
    stub.renders = renders
    return stub


def _render_stub(**over):
    """A stub wired to the REAL ``_refresh_engine_text``, so what the operator
    would actually see lands in ``.engine_text.value``.

    Everything the render touches is present and inert: ``_ui`` runs the closure
    inline instead of hopping to the UI thread, and there is no sidebar host to
    rebuild. This is the only way to assert that a computed status was PAINTED
    — the recording stub above proves the call happened, this proves the call
    produced the right text.
    """
    base = dict(
        engine_text=SimpleNamespace(value=""),
        _ui=lambda fn: fn(),
        _sidebar_host=None,
        _safe_update=lambda: None,
        _engine_update_available=lambda: False,
    )
    base.update(over)
    stub = _stub(**base)
    stub._refresh_engine_text = lambda status="": app_mod.App._refresh_engine_text(
        stub, status
    )
    return stub


# ---------------------------------------------------------------------------
# 1. The recorded-version bug
# ---------------------------------------------------------------------------


def test_records_the_tag_whose_bytes_were_installed_not_the_stale_check(monkeypatch):
    """THE bug: fetch returns a DIFFERENT tag than the earlier background check.

    This is the divergence the old code could not survive — upstream published
    between the hourly check (which set _engine_latest) and the click (which
    fetched fresh). The bytes on disk are 149's; version.txt must say 149.

    Asserting on the download URL as well as the written tag is what makes this
    test load-bearing: it pins that the version recorded and the bytes fetched
    came from the SAME fetch, which is the actual invariant. A test that only
    checked the written string could be satisfied by writing the right value for
    the wrong reason.
    """
    monkeypatch.setattr(
        app_mod.engine,
        "fetch_latest_checked",
        lambda timeout=20: (
            "149.0.8000.10",           # fresh: upstream published since the check
            "http://example/149",
            "sha256:new",
            policy.OK,
            "",
        ),
    )
    got = {}

    def fake_download(url, timeout=600, digest=None, progress=None):
        got["url"] = url
        return True

    monkeypatch.setattr(app_mod.engine, "download_engine", fake_download)
    written = []
    monkeypatch.setattr(app_mod.engine, "write_version", written.append)
    monkeypatch.setattr(app_mod.threading, "Thread", InlineThread)

    # the STALE tag from the earlier check — what the old code wrote
    stub = _stub(_engine_latest="148.0.7778.215")
    app_mod.App._update_engine_async(stub)

    assert got["url"] == "http://example/149", "downloaded the fresh release's bytes"
    assert written == ["149.0.8000.10"], (
        "version.txt must hold the tag whose bytes were installed, not the "
        "stale tag from the earlier background check"
    )
    # and the in-memory 'latest' is reconciled too, so the row and the next
    # is_newer() comparison both reason from what is actually on disk
    assert stub._engine_latest == "149.0.8000.10"


def test_a_failed_download_records_no_version(monkeypatch):
    """The tag is written only when bytes actually landed — otherwise
    version.txt would claim an engine that was never installed, which is the
    same provenance defect from the other direction."""
    monkeypatch.setattr(
        app_mod.engine,
        "fetch_latest_checked",
        lambda timeout=20: ("149.0.8000.10", "http://x/149", "sha256:n", policy.OK, ""),
    )
    monkeypatch.setattr(
        app_mod.engine, "download_engine", lambda *a, **k: False
    )
    written = []
    monkeypatch.setattr(app_mod.engine, "write_version", written.append)
    monkeypatch.setattr(app_mod.threading, "Thread", InlineThread)

    app_mod.App._update_engine_async(_stub())
    assert written == []


# ---------------------------------------------------------------------------
# 2a. policy: the known-bad list
# ---------------------------------------------------------------------------


def test_known_bad_build_is_refused_by_name(monkeypatch):
    monkeypatch.setattr(policy, "KNOWN_BAD_VERSIONS", frozenset({"148.0.7778.215"}))
    monkeypatch.setattr(policy, "POLICY_FILE", "/nonexistent/engine-policy.json")

    kind, message = policy.check("148.0.7778.215")
    assert kind == policy.KNOWN_BAD
    assert "148.0.7778.215" in message and "known-bad" in message
    assert policy.is_installable("148.0.7778.215") is False
    # a neighbouring build is untouched — the list refuses BY NAME, not by range
    assert policy.is_installable("148.0.7778.216") is True


def test_operator_can_add_a_known_bad_build_without_a_persona_update(tmp_path, monkeypatch):
    """The whole point of the local file: a build discovered to be bad can be
    refused today, not at the next release."""
    pf = tmp_path / "engine-policy.json"
    pf.write_text(json.dumps({"known_bad_versions": ["148.0.7778.215"]}))
    monkeypatch.setattr(policy, "POLICY_FILE", str(pf))
    monkeypatch.setattr(policy, "KNOWN_BAD_VERSIONS", frozenset())

    assert policy.is_installable("148.0.7778.215") is False
    assert policy.check("148.0.7778.215")[0] == policy.KNOWN_BAD


def test_local_file_cannot_unblock_a_shipped_known_bad(tmp_path, monkeypatch):
    """Local entries only ADD. A stale local file must not silently re-enable a
    build persona shipped knowing is broken."""
    pf = tmp_path / "engine-policy.json"
    pf.write_text(json.dumps({"known_bad_versions": []}))
    monkeypatch.setattr(policy, "POLICY_FILE", str(pf))
    monkeypatch.setattr(policy, "KNOWN_BAD_VERSIONS", frozenset({"148.0.7778.215"}))

    assert policy.is_installable("148.0.7778.215") is False


def test_a_corrupt_policy_file_falls_back_to_the_shipped_defaults(tmp_path, monkeypatch):
    """Fails OPEN to the committed defaults: a mangled file must not brick
    engine updating, and it must not drop the shipped known-bad list either."""
    pf = tmp_path / "engine-policy.json"
    pf.write_text("{not json at all")
    monkeypatch.setattr(policy, "POLICY_FILE", str(pf))
    monkeypatch.setattr(policy, "KNOWN_BAD_VERSIONS", frozenset({"148.0.7778.215"}))
    monkeypatch.setattr(policy, "MAX_TESTED_MAJOR", 148)

    assert policy.max_tested_major() == 148
    assert policy.is_installable("148.0.7778.215") is False   # shipped list survives
    assert policy.is_installable("148.0.7778.216") is True


# ---------------------------------------------------------------------------
# 2b. policy: the compatibility ceiling
# ---------------------------------------------------------------------------


def test_a_build_above_the_tested_major_is_not_installable(monkeypatch):
    monkeypatch.setattr(policy, "MAX_TESTED_MAJOR", 148)
    monkeypatch.setattr(policy, "POLICY_FILE", "/nonexistent/engine-policy.json")

    kind, message = policy.check("149.0.8000.10")
    assert kind == policy.ABOVE_CEILING
    # the message must say what to DO, and must not read as a download failure
    assert "update persona" in message.lower()
    assert "149.0.8000.10" in message and "148" in message


def test_the_ceiling_never_blocks_at_or_below_the_tested_major(monkeypatch):
    """It is a CEILING, not a floor. Routine updating inside a known-good major
    must be completely untouched — this is the "guards get routed around" clause
    of the ticket, stated as an assertion."""
    monkeypatch.setattr(policy, "MAX_TESTED_MAJOR", 148)
    monkeypatch.setattr(policy, "POLICY_FILE", "/nonexistent/engine-policy.json")

    for tag in ("148.0.7778.215", "148.0.9999.1", "147.0.1.1", "100.0.0.0"):
        assert policy.is_installable(tag) is True, tag


def test_an_operator_may_raise_the_ceiling_deliberately(tmp_path, monkeypatch):
    """The aim is that taking an untested engine is a DECISION with a visible
    consequence, not that it is impossible."""
    pf = tmp_path / "engine-policy.json"
    pf.write_text(json.dumps({"max_tested_major": 149}))
    monkeypatch.setattr(policy, "POLICY_FILE", str(pf))
    monkeypatch.setattr(policy, "MAX_TESTED_MAJOR", 148)

    assert policy.max_tested_major() == 149
    assert policy.is_installable("149.0.8000.10") is True
    assert policy.is_installable("150.0.1.1") is False   # still bounded


def test_a_typo_in_the_ceiling_is_ignored_rather_than_obeyed(tmp_path, monkeypatch):
    """A bad override must not accidentally block every update (or every
    update be blocked by someone fat-fingering a value)."""
    monkeypatch.setattr(policy, "MAX_TESTED_MAJOR", 148)
    pf = tmp_path / "engine-policy.json"
    for bad in ("abc", None, True, -5, [1]):
        pf.write_text(json.dumps({"max_tested_major": bad}))
        monkeypatch.setattr(policy, "POLICY_FILE", str(pf))
        assert policy.max_tested_major() == 148, bad


def test_major_reads_the_leading_number_and_reports_minus_one_for_junk():
    """What major() ACTUALLY does — no more.

    Renamed from "...does_not_sail_through_the_ceiling", which claimed a guard
    the module does not have: the ceiling test is ``num > ceiling``, under which
    -1 and 0 behave identically, so an unparseable tag IS installable. The old
    name asserted only these return values while promising a refusal, which is
    the failure mode this whole ticket exists to fix — a future reader trusting
    a guard that isn't there.
    """
    assert policy.major("148.0.7778.215") == 148
    assert policy.major("v149.0.1") == 149
    assert policy.major("nightly") == -1
    assert policy.major("") == -1


def test_an_unparseable_tag_is_installable_and_that_is_deliberate():
    """The behaviour the old test's NAME denied, pinned as intended.

    -1 does not refuse, and must not silently start to: an unparseable tag can
    never be OFFERED as an update anyway (is_newer parses it to () which
    compares below every installed version), so the only path it reaches is a
    FIRST install — where persona deliberately takes an untested build rather
    than leave the app with no browser at all. Refusing here would contradict
    the ABOVE_CEILING asymmetry rather than reinforce it.
    """
    assert policy.check("nightly") == (policy.OK, "")
    assert policy.is_installable("nightly") is True
    # ...and the reason that is safe, stated as an assertion rather than a claim
    # in a comment: junk can never present itself as an update.
    assert updater.is_newer("nightly", "148.0.7778.215") is False


def test_an_empty_tag_is_not_a_governance_refusal(monkeypatch):
    """No tag means the FETCH failed — the caller already reports that. Turning
    it into a policy refusal would mislabel a network problem as a decision."""
    assert policy.check("") == (policy.OK, "")


# ---------------------------------------------------------------------------
# 2c. the governed fetch fails closed
# ---------------------------------------------------------------------------


def test_fetch_latest_checked_blanks_the_url_on_a_refusal(monkeypatch):
    """The refusal is structural, not advisory: a caller that ignores the
    verdict STILL cannot install the build, because there is no URL to install
    from (download_engine("") is False)."""
    monkeypatch.setattr(
        updater,
        "fetch_latest_full",
        lambda timeout=20: ("149.0.8000.10", "http://x/149", "sha256:n"),
    )
    monkeypatch.setattr(
        updater.policy, "check", lambda tag: (policy.ABOVE_CEILING, "needs a persona update")
    )

    tag, url, digest, verdict, message = updater.fetch_latest_checked()
    assert tag == "149.0.8000.10"        # the version is still nameable
    assert url == "" and digest == ""    # ...but not installable
    assert verdict == policy.ABOVE_CEILING
    assert message == "needs a persona update"
    assert updater.download_engine(url) is False


def test_fetch_latest_checked_passes_a_good_build_through_untouched(monkeypatch):
    monkeypatch.setattr(
        updater,
        "fetch_latest_full",
        lambda timeout=20: ("148.0.7778.215", "http://x/148", "sha256:a"),
    )
    monkeypatch.setattr(updater.policy, "check", lambda tag: (policy.OK, ""))

    assert updater.fetch_latest_checked() == (
        "148.0.7778.215", "http://x/148", "sha256:a", policy.OK, "",
    )


# ---------------------------------------------------------------------------
# 2d. the UI reports a refusal as a refusal
# ---------------------------------------------------------------------------


def test_a_refused_build_is_not_downloaded_and_says_why(monkeypatch):
    """The ticket's sharpest requirement: the operator must be told persona
    declined the build, NOT that the download failed."""
    monkeypatch.setattr(
        app_mod.engine,
        "fetch_latest_checked",
        lambda timeout=20: (
            "149.0.8000.10", "", "", policy.ABOVE_CEILING,
            "Chromium engine 149.0.8000.10 is newer than persona has been "
            "tested against (Chromium 148) — update persona to get it.",
        ),
    )
    downloads = []
    monkeypatch.setattr(
        app_mod.engine, "download_engine",
        lambda *a, **k: downloads.append(1) or True,
    )
    written = []
    monkeypatch.setattr(app_mod.engine, "write_version", written.append)
    monkeypatch.setattr(app_mod.threading, "Thread", InlineThread)

    logs = []
    stub = _stub(_log=logs.append)
    app_mod.App._update_engine_async(stub)

    assert downloads == [], "a refused build must not be downloaded"
    assert written == [], "and must not be recorded as installed"
    joined = " ".join(logs).lower()
    assert "update persona" in joined
    assert "update failed" not in joined, (
        "a refusal must not be reported as a failed download — that blames the "
        "network for a decision persona made, and the operator retries forever"
    )
    # the row shows the same distinction the Firefox row shows
    assert stub._engine_status == "update persona for the newest engine"
    # ...and it is RENDERED, not merely assigned. Asserting the attribute alone
    # is what let the refusal path ship while leaving the row wedged on
    # "downloading..." — the string was computed and never painted.
    assert stub.renders and stub.renders[-1] == "", (
        "the refusal path must end with a bare _refresh_engine_text() so the "
        "row leaves 'downloading...' and shows the refusal"
    )
    assert stub._engine_detail.value == "", (
        "and the progress detail must be cleared — a refusal that leaves "
        "'189 MB of 189 MB' under the row is the leftover-progress bug again"
    )


def test_a_refusal_actually_paints_the_row_instead_of_wedging_on_downloading(
    monkeypatch,
):
    """The blocking defect from the PR #25 review, asserted end-to-end.

    The refusal returns from inside the ``try``; when the two render lines sat
    AFTER the try/finally, that return skipped both. ``_refresh_engine_text`` is
    the only thing that renders ``_engine_status``, so the operator kept looking
    at "downloading..." forever — nothing recomputes the Chromium row the way
    ``_build_sidebar`` recomputes the Firefox one, so it does not self-heal.

    This drives the REAL ``_refresh_engine_text`` and asserts on the text a user
    would read, which is the only assertion the no-op stub could not fake.
    """
    monkeypatch.setattr(
        app_mod.engine,
        "fetch_latest_checked",
        lambda timeout=20: (
            "149.0.8000.10", "", "", policy.ABOVE_CEILING,
            "Chromium engine 149.0.8000.10 is newer than persona has been "
            "tested against (Chromium 148) — update persona to get it.",
        ),
    )
    monkeypatch.setattr(app_mod.engine, "download_engine", lambda *a, **k: True)
    monkeypatch.setattr(app_mod.engine, "write_version", lambda t: None)
    monkeypatch.setattr(app_mod.engine, "current_version", lambda: "148.0.7778.215")
    monkeypatch.setattr(app_mod.threading, "Thread", InlineThread)

    stub = _render_stub()
    stub._engine_detail.value = "189 MB of 189 MB"
    app_mod.App._update_engine_async(stub)

    assert stub.engine_text.value == "update persona for the newest engine", (
        f"the row must show the refusal; it read {stub.engine_text.value!r}"
    )
    assert stub.engine_text.value != "downloading...", "the row wedged on the spinner"
    assert stub._engine_detail.value == ""


def test_the_ordinary_update_path_still_paints_the_row(monkeypatch):
    """The control for the test above: hoisting the render into `finally` must
    not change what a SUCCESSFUL update leaves on screen."""
    monkeypatch.setattr(
        app_mod.engine,
        "fetch_latest_checked",
        lambda timeout=20: ("149.0.8000.10", "http://x/149", "sha256:n", policy.OK, ""),
    )
    monkeypatch.setattr(app_mod.engine, "download_engine", lambda *a, **k: True)
    monkeypatch.setattr(app_mod.engine, "write_version", lambda t: None)
    monkeypatch.setattr(app_mod.engine, "current_version", lambda: "149.0.8000.10")
    monkeypatch.setattr(app_mod.threading, "Thread", InlineThread)

    stub = _render_stub()
    stub._engine_detail.value = "189 MB of 189 MB"
    app_mod.App._update_engine_async(stub)

    assert stub.engine_text.value == "149.0.8000.10", "settles on the installed version"
    assert stub._engine_detail.value == ""


def test_a_raising_update_still_paints_the_row(monkeypatch):
    """The third exit from that try/finally. A raise must not wedge the row on
    the spinner either — the `finally` is what guarantees this for ALL exits,
    which is why the fix went there rather than duplicating two lines."""
    def boom(timeout=20):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(app_mod.engine, "fetch_latest_checked", boom)
    monkeypatch.setattr(app_mod.engine, "current_version", lambda: "148.0.7778.215")
    monkeypatch.setattr(app_mod.threading, "Thread", InlineThread)

    logs = []
    stub = _render_stub(_log=logs.append)
    app_mod.App._update_engine_async(stub)

    assert stub.engine_text.value == "148.0.7778.215"
    assert stub._engine_busy is False
    assert any("update failed" in m.lower() for m in logs)


def test_a_known_bad_refusal_reads_differently_from_a_ceiling_refusal(monkeypatch):
    """Two different situations must not collapse into one message."""
    monkeypatch.setattr(
        app_mod.engine,
        "fetch_latest_checked",
        lambda timeout=20: (
            "148.0.7778.215", "", "", policy.KNOWN_BAD,
            "Chromium engine 148.0.7778.215 is on persona's known-bad list — "
            "not installing it.",
        ),
    )
    monkeypatch.setattr(app_mod.engine, "download_engine", lambda *a, **k: True)
    monkeypatch.setattr(app_mod.engine, "write_version", lambda t: None)
    monkeypatch.setattr(app_mod.threading, "Thread", InlineThread)

    logs = []
    stub = _stub(_log=logs.append)
    app_mod.App._update_engine_async(stub)

    assert stub._engine_status == "engine update blocked"
    assert stub._engine_status != "update persona for the newest engine"
    assert "known-bad" in " ".join(logs)


def test_a_normal_newer_build_still_installs_exactly_as_before(monkeypatch):
    """The guard must not make routine updating harder. Same shape as the
    pre-existing test_manual_engine_update_downloads_with_digest."""
    monkeypatch.setattr(
        app_mod.engine,
        "fetch_latest_checked",
        lambda timeout=20: (
            "148.0.7778.215", "http://example/asset", "sha256:abc123", policy.OK, "",
        ),
    )
    calls = {}

    def fake_download(url, timeout=600, digest=None, progress=None):
        calls["url"] = url
        calls["digest"] = digest
        return True

    monkeypatch.setattr(app_mod.engine, "download_engine", fake_download)
    written = []
    monkeypatch.setattr(app_mod.engine, "write_version", written.append)
    monkeypatch.setattr(app_mod.threading, "Thread", InlineThread)

    stub = _stub()
    app_mod.App._update_engine_async(stub)

    assert calls["url"] == "http://example/asset"
    assert calls["digest"] == "sha256:abc123"
    assert written == ["148.0.7778.215"]
    assert stub._engine_status == ""
    assert stub._engine_busy is False


# ---------------------------------------------------------------------------
# 2e. a refused build is never ADVERTISED as an available update
# ---------------------------------------------------------------------------


def test_a_refused_build_is_not_offered_as_an_available_update(monkeypatch):
    """If the row kept offering it, the operator would click, be refused, and
    the row would offer it again — forever."""
    monkeypatch.setattr(app_mod.engine, "current_version", lambda: "148.0.7778.215")
    monkeypatch.setattr(app_mod.engine_policy, "MAX_TESTED_MAJOR", 148)
    monkeypatch.setattr(
        app_mod.engine_policy, "POLICY_FILE", "/nonexistent/engine-policy.json"
    )

    stub = SimpleNamespace(_engine_latest="149.0.8000.10")
    assert app_mod.App._engine_update_available(stub) is False

    # ...while a good newer build IS offered
    stub2 = SimpleNamespace(_engine_latest="148.0.9999.1")
    assert app_mod.App._engine_update_available(stub2) is True


def test_a_declined_build_does_not_read_as_up_to_date(monkeypatch):
    """A refusal must be visible in the row. Falling through to the installed
    version would make a declined upstream build indistinguishable from being
    current — the operator would never learn a persona update is needed."""
    monkeypatch.setattr(app_mod.engine, "current_version", lambda: "148.0.7778.215")
    monkeypatch.setattr(app_mod.engine_policy, "MAX_TESTED_MAJOR", 148)
    monkeypatch.setattr(
        app_mod.engine_policy, "POLICY_FILE", "/nonexistent/engine-policy.json"
    )

    logs = []
    stub = SimpleNamespace(
        _engine_latest="", _engine_status="", _log=logs.append
    )
    # the helper consults the real availability predicate — bind it so the
    # policy gate under test actually runs rather than being stubbed away
    stub._engine_update_available = lambda: app_mod.App._engine_update_available(stub)
    line = app_mod.App._record_engine_check(stub, "149.0.8000.10")

    assert stub._engine_status == "update persona for the newest engine"
    assert "update persona" in line.lower()

    # and the row renders that status rather than the installed version
    ui_stub = SimpleNamespace(
        _engine_latest="149.0.8000.10",
        _engine_status="update persona for the newest engine",
        engine_text=SimpleNamespace(value=""),
        _sidebar_host=None,
        _safe_update=lambda: None,
        _ui=lambda fn: fn(),
    )
    ui_stub._engine_update_available = (
        lambda: app_mod.App._engine_update_available(ui_stub)
    )
    app_mod.App._refresh_engine_text(ui_stub)
    assert ui_stub.engine_text.value == "update persona for the newest engine"
    assert ui_stub.engine_text.value != "148.0.7778.215"


def test_an_ordinary_up_to_date_check_clears_a_stale_status(monkeypatch):
    """A status left over from a previous refusal must not stick around after
    the situation resolves (e.g. persona was updated and the ceiling rose)."""
    monkeypatch.setattr(app_mod.engine, "current_version", lambda: "149.0.8000.10")
    monkeypatch.setattr(app_mod.engine_policy, "MAX_TESTED_MAJOR", 149)
    monkeypatch.setattr(
        app_mod.engine_policy, "POLICY_FILE", "/nonexistent/engine-policy.json"
    )

    stub = SimpleNamespace(
        _engine_latest="",
        _engine_status="update persona for the newest engine",
        _log=lambda m: None,
    )
    stub._engine_update_available = lambda: app_mod.App._engine_update_available(stub)
    line = app_mod.App._record_engine_check(stub, "149.0.8000.10")
    assert stub._engine_status == ""
    assert line == ""


# ---------------------------------------------------------------------------
# 2f. the FIRST install answers the ceiling differently — on purpose
# ---------------------------------------------------------------------------


def test_first_install_refuses_a_known_bad_build(monkeypatch):
    """No engine at all beats an engine known to be broken — and the operator
    gets the reason instead of a bare "download failed"."""
    monkeypatch.setattr(updater, "is_installed", lambda: False)
    monkeypatch.setattr(
        updater, "fetch_latest_full",
        lambda *a, **k: ("148.0.7778.215", "http://x/148", "sha256:a"),
    )
    monkeypatch.setattr(
        updater.policy, "check",
        lambda tag: (policy.KNOWN_BAD, "on persona's known-bad list"),
    )
    downloads = []
    monkeypatch.setattr(
        updater, "download_engine", lambda *a, **k: downloads.append(1) or True
    )

    logs = []
    ok, msg = updater.ensure_engine(attempts=3, log=logs.append)
    assert ok is False
    assert downloads == []
    assert "known-bad" in msg
    assert msg != "download failed"
    # THE OPERATOR-VISIBLE CHANNEL, not just the return value. This is the
    # blocking finding from the PR #25 review: the reason travelled solely in
    # the tuple, and the onboarding caller (app.py, `ok, _msg = ...`) discards
    # it — so a known-bad build was refused in total silence on the one path
    # where the operator has no engine and no explanation. Asserting `msg`
    # alone passes either way, which is why it missed.
    assert any("known-bad" in m for m in logs), (
        "the refusal must reach the operator through `log`, not only through "
        "the return value that one caller discards"
    )
    assert not any("download failed" in m.lower() for m in logs), (
        "a governance refusal must never be worded as a failed download — "
        "retrying cannot change persona's decision"
    )


def test_a_known_bad_first_install_is_not_silent_on_the_onboarding_path(monkeypatch):
    """The exact call shape onboarding uses (``ok, _msg =`` — message
    DISCARDED), driven through the real ``ensure_engine``.

    This pins the fix at the level that matters: the reason has to be logged by
    ``ensure_engine`` itself, because the only caller-side variable carrying it
    is thrown away. If the logging moves back to the call sites, this goes red.
    """
    monkeypatch.setattr(updater, "is_installed", lambda: False)
    monkeypatch.setattr(
        updater, "fetch_latest_full",
        lambda *a, **k: ("148.0.7778.215", "http://x/148", "sha256:a"),
    )
    monkeypatch.setattr(
        updater.policy, "check",
        lambda tag: (
            policy.KNOWN_BAD,
            "Chromium engine 148.0.7778.215 is on persona's known-bad list — "
            "not installing it.",
        ),
    )
    monkeypatch.setattr(updater, "download_engine", lambda *a, **k: True)

    operator_sees = []
    # onboarding discards the message — everything the user learns is via log
    ok, _msg = updater.ensure_engine(attempts=1, log=operator_sees.append)

    assert ok is False
    assert operator_sees, "the operator was told nothing at all"
    joined = " ".join(operator_sees)
    assert "known-bad" in joined and "148.0.7778.215" in joined, (
        "the refusal must name the build and the reason"
    )


def test_a_transient_failure_is_still_reported_as_a_download_failure(monkeypatch):
    """The counterpart to the test above, and the reason the two are worded in
    ONE place: a network failure genuinely IS a download failure and keeps that
    prefix. Only persona's own refusals lose it."""
    monkeypatch.setattr(updater, "is_installed", lambda: False)
    monkeypatch.setattr(
        updater, "fetch_latest_full",
        lambda *a, **k: ("148.0.7778.215", "http://x/148", "sha256:a"),
    )
    monkeypatch.setattr(updater.policy, "check", lambda tag: (policy.OK, ""))
    monkeypatch.setattr(updater, "download_engine", lambda *a, **k: False)

    logs = []
    ok, msg = updater.ensure_engine(attempts=1, log=logs.append)

    assert ok is False
    assert msg == "download failed"
    assert any("Engine download failed" in m for m in logs), (
        "a real transfer failure keeps the retryable wording"
    )


def test_first_install_takes_an_above_ceiling_build_but_says_so(monkeypatch):
    """Deliberately NOT symmetric with the update path. On an update, declining
    costs nothing — a working engine is already installed. On a FIRST install
    refusing would leave the app with no browser at all over a build that is
    merely untested, and Chromium has no drivable older build to fall back to
    the way Firefox has its package-pinned one. So: install, loudly."""
    monkeypatch.setattr(updater, "is_installed", lambda: False)
    monkeypatch.setattr(
        updater, "fetch_latest_full",
        lambda *a, **k: ("149.0.8000.10", "http://x/149", "sha256:n"),
    )
    monkeypatch.setattr(
        updater.policy, "check",
        lambda tag: (policy.ABOVE_CEILING, "newer than persona has been tested against"),
    )
    monkeypatch.setattr(updater, "download_engine", lambda *a, **k: True)
    written = []
    monkeypatch.setattr(updater, "write_version", written.append)

    logs = []
    ok, msg = updater.ensure_engine(attempts=1, log=logs.append)

    assert ok is True
    assert written == ["149.0.8000.10"]
    joined = " ".join(logs).lower()
    assert "untested" in joined, "the untested state must be LOUD, not silent"
    assert "tested against" in joined


def test_first_install_of_a_good_build_is_unchanged_and_silent(monkeypatch):
    """No new noise on the ordinary path."""
    monkeypatch.setattr(updater, "is_installed", lambda: False)
    monkeypatch.setattr(
        updater, "fetch_latest_full",
        lambda *a, **k: ("148.0.7778.215", "http://x/148", "sha256:a"),
    )
    monkeypatch.setattr(updater.policy, "check", lambda tag: (policy.OK, ""))
    monkeypatch.setattr(updater, "download_engine", lambda *a, **k: True)
    written = []
    monkeypatch.setattr(updater, "write_version", written.append)

    logs = []
    ok, msg = updater.ensure_engine(attempts=1, log=logs.append)
    assert (ok, msg) == (True, "148.0.7778.215")
    assert written == ["148.0.7778.215"]
    assert logs == []


# ---------------------------------------------------------------------------
# 2g. the shipped defaults are what this build claims to know
# ---------------------------------------------------------------------------


def test_the_shipped_ceiling_matches_the_major_the_masking_layer_is_written_against():
    """MAX_TESTED_MAJOR is a claim about testing, and the one mechanical anchor
    it has is the Chrome major persona's own masking layer emits. If someone
    bumps the UA client hints without revisiting the ceiling (or vice versa),
    the two silently disagree and the ceiling is the one that is wrong — this
    turns that into a red test instead of a quiet mismatch.
    """
    from src.services.browser import device_presets, mobile_ext

    assert policy.MAX_TESTED_MAJOR == 148

    src = mobile_ext.__file__ and open(mobile_ext.__file__, encoding="utf-8").read()
    assert f"version: '{policy.MAX_TESTED_MAJOR}'" in src, (
        "mobile_ext's Chrome-brand client hints and policy.MAX_TESTED_MAJOR "
        "disagree — bump them together"
    )
    presets = open(device_presets.__file__, encoding="utf-8").read()
    assert f"Chrome/{policy.MAX_TESTED_MAJOR}.0.0.0" in presets
