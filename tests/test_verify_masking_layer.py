"""The masking layer the checker harness installs — and the demonstration that it lands.

What these tests are shaped against
-----------------------------------
This ticket's defect was not a wrong value. It was a harness that measured **the
wrong subject** and looked perfectly healthy doing it: ``browser_tier`` launched
the packaged engines, read the checkers, parsed real verdicts and wrote real
records — with none of persona's own masking layer installed. Every reading
described the upstream engines rather than the product, and nothing in the
record said so.

So the failure mode a test suite has to be hostile to here is **a green
assertion that proves the code ran rather than that it did anything** —
knowledge article PS-11's subject, and the exact trap this ticket landed in.
Concretely, these two tests would both pass on the broken tree and are therefore
deliberately NOT written:

* ``assert "add_init_script" in inspect.getsource(...)`` — proves the text is
  present, not that a document carries it.
* ``assert install_firefox_layer(ctx, seed).installed`` against a mock that
  accepts everything — proves the function returns what it was told.

What is asserted instead: the layer is built by the SHIPPED builders (so it
cannot drift from the product), a fake context RECORDS WHAT IT WAS ACTUALLY
HANDED (so "installed" means scripts really arrived), a ``Browser``-shaped
object is REFUSED rather than silently installing nothing, and the differential's
reporting logic gives the right verdict for each of the outcomes it must tell
apart.

The one thing a unit test cannot do
-----------------------------------
None of this proves the spoof reaches a real page — only a live differential
does, and that is why :mod:`layer_differential` exists and was run for real. The
tests below cover the parts that can be settled offline; the PR records the live
run.
"""

from __future__ import annotations

import json

import pytest

from src.services.verify import local_probe, masking_layer
from src.services.verify.layer_differential import (
    AXIS_LAYER,
    AXIS_SEED,
    Arm,
    build_differential_record,
)
from src.services.verify.local_probe import ProbeReading, differential
from src.services.verify.masking_layer import (
    LayerReport,
    absent_layer,
    build_chromium_layer,
    context_for,
    firefox_layer_scripts,
    install_firefox_layer,
)

SEED = 4242
OTHER_SEED = 1337


# --- test doubles that RECORD, rather than accept -----------------------------


class FakeContext:
    """A BrowserContext-shaped double that keeps what it was handed.

    The point is that it RECORDS. A double that merely accepts every call would
    let ``install_firefox_layer`` report ten installed vectors while passing
    nothing at all, which is the shape of the defect being fixed.
    """

    def __init__(self, pages=None, fail_on: str = ""):
        self.scripts: "list[str]" = []
        self.pages = pages if pages is not None else []
        self._fail_on = fail_on

    def add_init_script(self, js: str) -> None:
        if self._fail_on and self._fail_on in js:
            raise RuntimeError("refused")
        self.scripts.append(js)


class FakePage:
    def __init__(self, raises: "Exception | None" = None):
        self.evaluated: "list[str]" = []
        self._raises = raises

    def evaluate(self, js: str):
        if self._raises is not None:
            raise self._raises
        self.evaluated.append(js)


class FakeBrowser:
    """A playwright ``Browser``: NO add_init_script, NO pages.

    This is the object ``InvisiblePlaywright.__enter__`` really returns when no
    ``profile_dir`` is set, and handing it to the installer was a measured
    defect rather than a hypothetical one.
    """

    def __init__(self):
        self.context = FakeContext()

    def new_context(self):
        return self.context


# --- the layer is BUILT BY THE SHIPPED BUILDERS ------------------------------


def test_the_firefox_scripts_are_the_shipped_builders_output_not_a_copy():
    """A second copy of the spoof set would reproduce this ticket's defect.

    If the harness carried its own spoof source, it could drift from the one the
    product launches and the harness would once again be measuring something
    that is not the product — subtler, and much harder to catch. So the scripts
    must be BYTE-IDENTICAL to what the shipped builders emit.
    """
    from src.services.browser.audio_ext import firefox_audio_init_script
    from src.services.browser.invisible_launch import _language_override_script
    from src.services.browser.webgl_ext import firefox_webgl_init_script

    by_vector = dict(firefox_layer_scripts(SEED, locale="en-US"))

    assert by_vector[masking_layer.WEBGL] == firefox_webgl_init_script(SEED)
    assert by_vector[masking_layer.AUDIO] == firefox_audio_init_script(SEED)
    assert by_vector[masking_layer.LOCALE] == _language_override_script("en-US")


def test_the_firefox_scripts_are_PER_SEED():
    """The whole point of the layer is a per-profile identity. Two seeds that
    produced identical spoof source would mean the harness installs a constant,
    and two profiles would be linkable on every vector it drives."""
    a = dict(firefox_layer_scripts(SEED))
    b = dict(firefox_layer_scripts(OTHER_SEED))

    assert a[masking_layer.WEBGL] != b[masking_layer.WEBGL]
    assert a[masking_layer.AUDIO] != b[masking_layer.AUDIO]
    # ...and the locale spoof is NOT seed-derived, so it must NOT move. Asserted
    # rather than ignored: a locale script that varied by seed would be a bug,
    # and the seed-axis differential relies on this vector staying still.
    assert a[masking_layer.LOCALE] == b[masking_layer.LOCALE]


def test_an_empty_locale_installs_NO_locale_vector():
    """``_language_override_script("")`` is a documented no-op. Reporting
    ``locale`` as installed for an empty script would put a vector in the record
    that delivered nothing — the record must never name a vector it did not
    install."""
    vectors = dict(firefox_layer_scripts(SEED, locale="")).keys()
    assert masking_layer.LOCALE not in vectors


# --- installation actually hands the scripts over ----------------------------


def test_installing_really_passes_every_script_to_the_context():
    """Asserted against what the context RECEIVED, not against the report.

    The report is the thing under test; believing it about itself is how a
    harness ends up claiming a layer it never installed.
    """
    ctx = FakeContext()
    report = install_firefox_layer(ctx, SEED, locale="en-US")

    expected = [js for _vector, js in firefox_layer_scripts(SEED, locale="en-US")]
    assert ctx.scripts == expected
    assert report.complete
    assert set(report.installed) == {
        masking_layer.WEBGL, masking_layer.AUDIO, masking_layer.LOCALE
    }
    assert report.route == "init_scripts"


def test_the_layer_is_REPLAYED_into_tabs_that_already_exist():
    """PS-78's rule, and it is the difference between a spoof that holds and one
    that silently vanishes.

    ``add_init_script`` reaches only documents created AFTER registration. On a
    restore launch the tabs already exist, and PS-78 MEASURED the override
    present on launch 1 and absent on every launch after. A test that only
    checked registration would pass through that entire defect.
    """
    page = FakePage()
    ctx = FakeContext(pages=[page])

    install_firefox_layer(ctx, SEED, locale="en-US")

    expected = [js for _vector, js in firefox_layer_scripts(SEED, locale="en-US")]
    assert page.evaluated == expected, (
        "every registered spoof must also be replayed into the open tab"
    )


def test_a_context_less_tab_is_skipped_without_failing_the_run():
    """The fx-19 dead default tab raises on any eval and has nothing to patch —
    the next document in it comes from the init script. That known case must not
    turn a good run into a failed one."""
    ctx = FakeContext(pages=[FakePage(raises=RuntimeError("no browsingContext"))])

    report = install_firefox_layer(ctx, SEED, locale="en-US")

    assert report.complete, "a context-less tab is expected, not a failure"
    assert report.installed


def test_an_UNEXPECTED_replay_failure_is_RECORDED_not_swallowed():
    """The symptom of a silent swallow is a tab quietly keeping host values —
    precisely the defect this module exists to close. So anything that is not
    the known context-less case lands in the report."""
    ctx = FakeContext(pages=[FakePage(raises=RuntimeError("something else"))])

    report = install_firefox_layer(ctx, SEED, locale="en-US")

    assert not report.complete
    assert report.failed, "an unexpected replay failure must reach the record"


def test_a_per_vector_install_failure_is_recorded_and_the_REST_still_install():
    """One vector failing must not take down a run that can still honestly
    report the others. A record's job is to say what was measured."""
    from src.services.browser.audio_ext import firefox_audio_init_script

    # Fail only the audio script, by matching a fragment unique to it.
    ctx = FakeContext(fail_on=firefox_audio_init_script(SEED)[:200])
    report = install_firefox_layer(ctx, SEED, locale="en-US")

    assert masking_layer.AUDIO in report.failed
    assert masking_layer.WEBGL in report.installed
    assert masking_layer.LOCALE in report.installed
    assert not report.complete


# --- the Browser-vs-Context defect, pinned so it cannot come back ------------


def test_a_Browser_is_turned_into_a_CONTEXT_before_anything_installs():
    """THE REGRESSION TEST FOR A DEFECT THAT REALLY HAPPENED.

    ``InvisiblePlaywright.__enter__`` returns a ``Browser`` when no
    ``profile_dir`` is set — which is the harness's case — and a playwright
    ``Browser`` has NO ``add_init_script`` and NO ``pages``. The first wiring of
    this ticket registered the spoofs on it, installed exactly nothing, and
    reported ``installed: []``. It was caught only because the differential was
    RUN FOR REAL and came back ``unmoved``.
    """
    browser = FakeBrowser()

    ctx, note = context_for(browser)

    assert ctx is browser.context
    assert "Browser" in note, "the record must be able to say which case it took"

    report = install_firefox_layer(ctx, SEED, locale="en-US")
    assert report.installed
    assert browser.context.scripts, "the scripts must reach the real context"


def test_a_context_is_used_directly():
    ctx = FakeContext()
    got, note = context_for(ctx)
    assert got is ctx
    assert "BrowserContext" in note


def test_installing_onto_a_BROWSER_REFUSES_LOUDLY_rather_than_installing_nothing():
    """The failure that shipped silently must now be impossible to miss.

    An installer handed the wrong object used to return a report full of ten
    per-vector failures, which reads like ten unrelated problems. It now returns
    ONE absent-layer report naming the real cause.
    """
    report = install_firefox_layer(FakeBrowser(), SEED)

    assert not report.complete
    assert report.installed == ()
    assert "add_init_script" in json.dumps(report.as_record())


def test_context_for_refuses_an_object_that_is_neither():
    with pytest.raises(TypeError):
        context_for(object())


# --- the Chromium arm --------------------------------------------------------


def test_the_chromium_layer_builds_real_extension_directories(tmp_path):
    """Built by the shipped builders, into real directories with real manifests.

    Asserted by reading the DIRECTORY THAT EXISTS, not by trusting the returned
    list: a builder that returned a path it never created would satisfy a
    length check and nothing else.
    """
    dirs, report = build_chromium_layer(str(tmp_path), SEED, os_type="windows")

    assert report.route == "extensions"
    assert report.complete, report.failed
    assert dirs
    for d in dirs:
        manifest = tmp_path / d.split("/")[-1] / "manifest.json"
        assert manifest.is_file(), f"{d} has no manifest.json"
        json.loads(manifest.read_text(encoding="utf-8"))


def test_the_chromium_layer_omits_geo_unless_asked(tmp_path):
    """Default OFF, so an existing reading cannot move under a caller.

    Asserts the DIRECTORY IS ABSENT, not merely that the vector is missing from
    the report: a builder that ran but went unreported would still have changed
    the surface the browser presents, which is the thing that must not happen.
    """
    dirs, report = build_chromium_layer(str(tmp_path), SEED, os_type="windows")

    assert masking_layer.GEO not in report.installed
    assert not (tmp_path / ".persona-geo-ext").exists()
    assert not any(".persona-geo-ext" in d for d in dirs)


def test_the_chromium_layer_builds_geo_in_DENY_mode_when_asked(tmp_path):
    """``include_geo`` closes the measured tier-versus-product gap (PS-150).

    The generated source is read to confirm it is the coordinate-less DENY
    build the product uses for an exit with no usable lat/lon — the ``null``
    literals ``process.py`` produces when it passes ``None, None``. Asserting
    only that a directory appeared would equally pass for a build that PINNED
    coordinates, which is the opposite behaviour.
    """
    dirs, report = build_chromium_layer(
        str(tmp_path), SEED, os_type="windows", include_geo=True
    )

    assert report.complete, report.failed
    assert masking_layer.GEO in report.installed
    assert any(".persona-geo-ext" in d for d in dirs)

    ext = tmp_path / ".persona-geo-ext"
    json.loads((ext / "manifest.json").read_text(encoding="utf-8"))
    source = (ext / "geo.js").read_text(encoding="utf-8")
    assert "null" in source, "DENY mode passes no coordinates"


def test_the_chromium_geo_vector_is_appended_LAST_like_the_product(tmp_path):
    """Ordered as ``process.py`` appends them, so a diff reads straight down.

    The order is load-bearing for the record's own diff-against-the-product
    argument, so it is asserted rather than left to the reading eye.
    """
    _, report = build_chromium_layer(
        str(tmp_path), SEED, os_type="windows", include_geo=True
    )

    assert report.installed[-1] == masking_layer.GEO


def test_the_chromium_webgl_extension_is_PER_SEED(tmp_path):
    """Same argument as the Firefox scripts: a constant layer links profiles."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    build_chromium_layer(str(a), SEED)
    build_chromium_layer(str(b), OTHER_SEED)

    ja = (a / ".persona-webgl-ext" / "webgl.js").read_text(encoding="utf-8")
    jb = (b / ".persona-webgl-ext" / "webgl.js").read_text(encoding="utf-8")
    assert ja != jb


def test_the_launch_args_actually_LOAD_the_extensions():
    """The flag has to be on the command line, or the extensions built above are
    an elaborate way of writing files nobody reads.

    ``--disable-extensions-except`` is asserted alongside ``--load-extension``
    deliberately: without it chromium can drop an unpacked extension that was
    nonetheless named, and a layer that is silently not running while the flag
    says it is would be this ticket's defect in a new disguise.
    """
    from src.services.verify.chromium_tier import _launch_args

    args = _launch_args(
        "/bin/true", "/tmp/profile",
        seed=SEED, declared_machine="windows", proxy_server="socks5://127.0.0.1:1",
        extension_dirs=["/tmp/profile/.persona-webgl-ext",
                        "/tmp/profile/.persona-audio-ext"],
    )

    load = [a for a in args if a.startswith("--load-extension=")]
    disable_except = [a for a in args if a.startswith("--disable-extensions-except=")]
    assert len(load) == 1 and len(disable_except) == 1
    assert ".persona-webgl-ext" in load[0]
    assert ".persona-audio-ext" in load[0]
    assert load[0].split("=", 1)[1] == disable_except[0].split("=", 1)[1]


def test_no_extension_flag_when_the_layer_is_off():
    """The control arm must be the packaged engine and NOTHING else. An empty
    ``--load-extension=`` would be a different configuration than no flag."""
    from src.services.verify.chromium_tier import _launch_args

    args = _launch_args(
        "/bin/true", "/tmp/profile",
        seed=SEED, declared_machine="windows", proxy_server="socks5://127.0.0.1:1",
        extension_dirs=[],
    )
    assert not [a for a in args if a.startswith("--load-extension")]
    assert not [a for a in args if a.startswith("--disable-extensions-except")]


# --- the record states its SUBJECT ------------------------------------------


def test_the_record_header_carries_the_masking_layer():
    """The record must be able to say WHICH SUBJECT it describes.

    Every record written before PS-103 describes the packaged engines. A
    consumer has to be able to tell that apart from a reading of the product
    without knowing that ticket exists.

    INVERTED BY PS-242, and the inversion is the point rather than a casualty.
    This report names two of the three vectors persona's Firefox layer declares
    — ``locale`` is neither installed nor failed, i.e. NEVER ATTEMPTED. It used
    to read ``complete: true``, because ``complete`` was ``not failed`` and a
    vector nobody tried could not lower it. The header now reads ``complete:
    false`` and NAMES the missing vector, which is the whole deliverable: a
    short vector list can no longer produce a clean-looking record.

    The header's original claim — that it carries the layer at all — is asserted
    unchanged beside it, and the complete case is pinned right below.
    """
    from src.services.verify import matrix
    from src.services.verify.exit_guard import Exit

    report = LayerReport(route="init_scripts", installed=("webgl", "audio"))
    record = matrix.build_record(
        [],
        exit_=Exit(ip="1.2.3.4", country="PL"),
        engine="invisible_playwright/firefox-20",
        observed_at="2026-08-22T10:00:00Z",
        masking_layer=report.as_record(),
    )

    assert record["masking_layer"]["installed"] == ["audio", "webgl"]
    assert record["masking_layer"]["complete"] is False
    assert record["masking_layer"]["missing"] == ["locale"], (
        "the record must NAME what was never attempted, not merely go false"
    )
    assert record["schema_version"] == matrix.SCHEMA_VERSION


def test_a_header_whose_whole_declared_layer_LANDED_still_reads_complete():
    """AC4: a complete build's verdict is byte-identical to before PS-242.

    The tripwire above must not be bought by making every record incomplete —
    that would be as useless as the blindness it replaces, and much noisier. A
    report carrying the full declared set for its route reads exactly as it
    always did.
    """
    from src.services.verify import matrix
    from src.services.verify.exit_guard import Exit

    report = LayerReport(
        route="init_scripts", installed=masking_layer.FIREFOX_VECTORS
    )
    record = matrix.build_record(
        [],
        exit_=Exit(ip="1.2.3.4", country="PL"),
        engine="invisible_playwright/firefox-20",
        observed_at="2026-08-22T10:00:00Z",
        masking_layer=report.as_record(),
    )

    assert record["masking_layer"]["complete"] is True
    assert record["masking_layer"]["missing"] == []


def test_a_run_that_did_not_ask_records_NULL_not_an_empty_layer():
    """"No layer was installed" is a measurement; "this run did not ask" is not.

    Collapsing them would let an old engine-only record read as a deliberate
    control arm.
    """
    from src.services.verify import matrix
    from src.services.verify.exit_guard import Exit

    record = matrix.build_record(
        [],
        exit_=Exit(ip="1.2.3.4", country="PL"),
        engine="e",
        observed_at="2026-08-22T10:00:00Z",
    )
    assert record["masking_layer"] is None


def test_an_absent_layer_reports_incomplete_with_a_reason():
    """The `:183` contract, unchanged by PS-242.

    An arm that never ran declares NO expected set — its report already carries
    the real cause in ``failed``, and manufacturing ten "missing" vectors for a
    layer nobody attempted to install would bury that reason under noise about
    a configuration this run never had. ``complete`` stays false for exactly the
    reason it always was.
    """
    report = absent_layer("the engine never started")
    as_record = report.as_record()

    assert as_record["complete"] is False
    assert as_record["installed"] == []
    assert as_record["expected"] == [], (
        "an arm that never ran declares nothing; the reason is in `failed`"
    )
    assert as_record["missing"] == []
    assert "never started" in json.dumps(as_record)


# --- PS-242: a vector that was NEVER ATTEMPTED cannot read as complete -------


def test_a_NEVER_ATTEMPTED_vector_makes_the_RECORD_incomplete_and_is_NAMED():
    """AC1. Asserted on the record document, never on a helper call.

    ``installed`` and ``failed`` between them can only speak about vectors that
    were ATTEMPTED. A vector in neither is one the loop never reached — a short
    builder list — and before PS-242 nothing in the record could say a word
    about it: the header read ``complete: true`` over a layer with a hole in it.
    That is the PS-103 / PS-150 drift class, caught by hand both times.
    """
    report = LayerReport(
        route="extensions",
        installed=tuple(
            v for v in masking_layer.CHROMIUM_VECTORS if v != masking_layer.CANVAS_CTX
        ),
    )
    record = report.as_record()

    assert record["failed"] == {}, "the premise: nothing FAILED, one was skipped"
    assert record["complete"] is False
    assert record["missing"] == [masking_layer.CANVAS_CTX]
    assert masking_layer.CANVAS_CTX in record["expected"]
    assert masking_layer.CANVAS_CTX in json.dumps(record), (
        "the record must NAME what is missing, not merely go false"
    )


def test_the_chromium_PRODUCTION_PATH_reports_a_dropped_builder_as_missing(
    tmp_path, monkeypatch
):
    """AC3 — non-circularity, and this ticket's real test.

    THE FAILURE MODE THIS FORECLOSES: if ``expected`` were computed from the
    same ``builders`` list the loop iterates, then ``expected == installed |
    failed`` BY CONSTRUCTION, ``complete`` could never go false for a missing
    vector, and a builder dropped from the list would silently drop out of
    ``expected`` with it — today's blindness behind a new field name. The test
    above cannot catch that, because a hand-built ``LayerReport`` can be given
    any ``expected`` the test likes while the shipped builder stays circular.

    So this drives the PRODUCTION path. The hand-maintained builder list — the
    thing that actually drifts — is shortened by one entry, and everything after
    it is the shipped code: the real build loop, the real record construction,
    the real ``expected`` declaration. The dropped vector then lands in NEITHER
    ``installed`` NOR ``failed``, which is exactly what a list that has quietly
    diverged from ``process.py`` looks like, and the record must say so.

    A CIRCULAR IMPLEMENTATION FAILS THIS TEST, which is what earns it: an
    ``expected`` computed from ``builders`` would shrink in lockstep with the
    shortened list, ``missing`` would be empty, and ``complete`` would stay true.
    """
    real_builders = masking_layer._chromium_builders

    def short_builders(*args, **kwargs):
        # Not a raising builder — a builder that is NOT THERE. The distinction
        # is the whole ticket: a raiser lands in `failed` and always lowered
        # `complete`; one that was never enumerated landed nowhere at all.
        return [
            (vector, thunk)
            for vector, thunk in real_builders(*args, **kwargs)
            if vector != masking_layer.CANVAS_CTX
        ]

    # First: the real production call, unmodified, is COMPLETE — so the
    # assertion below is about the dropped vector and not about a build that
    # was broken to begin with.
    _dirs, healthy = build_chromium_layer(
        str(tmp_path / "healthy"), SEED, os_type="windows"
    )
    assert healthy.as_record()["complete"] is True, healthy.failed
    assert healthy.as_record()["missing"] == []

    monkeypatch.setattr(masking_layer, "_chromium_builders", short_builders)
    _dirs2, dropped = build_chromium_layer(
        str(tmp_path / "dropped"), SEED, os_type="windows"
    )
    record = dropped.as_record()

    assert masking_layer.CANVAS_CTX not in record["installed"]
    assert record["failed"] == {}, "a DROPPED builder is not a FAILED one"
    assert record["complete"] is False
    assert record["missing"] == [masking_layer.CANVAS_CTX]
    assert masking_layer.CANVAS_CTX in record["expected"], (
        "`expected` must NOT move with the builder list — that is the whole "
        "non-circularity requirement"
    )
    # ...and the extension really was not built, so the shortened list is a
    # genuine change to what the harness installs, not a bookkeeping trick.
    assert not (tmp_path / "dropped" / ".persona-canvas-ctx-ext").exists()


def test_the_chromium_expected_set_is_declared_INDEPENDENTLY_of_the_builders():
    """The structural half of AC3, asserted on the source rather than a run.

    ``CHROMIUM_VECTORS`` is a literal tuple of names. If someone later replaces
    it with something derived from ``build_chromium_layer``'s builder list, the
    tripwire silently becomes tautological while every behavioural test above
    keeps passing — because a derived set is still correct for every build that
    has NOT drifted. So the independence is pinned directly: the declared set
    must contain a vector that a deliberately shortened build does not.
    """
    assert isinstance(masking_layer.CHROMIUM_VECTORS, tuple)
    assert all(isinstance(v, str) for v in masking_layer.CHROMIUM_VECTORS)
    assert len(masking_layer.CHROMIUM_VECTORS) == 10, (
        "the ten base vectors; geo is configuration, see chromium_expected_vectors"
    )
    # The declared set is not a function of any report: an empty report must
    # still be measured against all ten.
    assert set(
        masking_layer.LayerReport(route="extensions").missing
    ) == set(masking_layer.CHROMIUM_VECTORS)


def test_a_DEGENERATE_empty_layer_is_the_same_defect_at_its_limit(tmp_path):
    """A layer that installed NOTHING and failed NOTHING used to read
    ``complete: true``. That is the never-attempted defect taken to its limit —
    every vector missing, and a header claiming the whole layer landed.

    Pinned as its own test because it is the case a reader is most likely to
    reach for as "surely that already works".
    """
    record = LayerReport(route="extensions").as_record()

    assert record["installed"] == []
    assert record["failed"] == {}
    assert record["complete"] is False
    assert record["missing"] == sorted(masking_layer.CHROMIUM_VECTORS)


def test_an_ATTEMPTED_AND_FAILED_vector_STILL_reports_incomplete_with_its_reason(
    tmp_path,
):
    """AC4. ``failed`` semantics are JOINED, never replaced.

    The expected-set check must not become the only thing that can lower
    ``complete``: a builder that RAISED is a different fact from a builder that
    was never reached, and the record has to keep carrying its reason.
    """
    report = LayerReport(
        route="extensions",
        installed=tuple(
            v for v in masking_layer.CHROMIUM_VECTORS if v != masking_layer.AUDIO
        ),
        failed={masking_layer.AUDIO: "RuntimeError: the builder raised"},
    )
    record = report.as_record()

    assert record["complete"] is False
    assert record["missing"] == [], "attempted-and-failed is not MISSING"
    assert "the builder raised" in record["failed"][masking_layer.AUDIO]


def test_a_default_chromium_run_does_NOT_expect_geo_and_stays_complete(tmp_path):
    """AC7. ``--match-product-geo`` is off by default, so a default run
    genuinely should not install ``geo`` — and must not start reading
    ``complete: false`` for not having been asked. An existing reading cannot
    move underneath a caller that did not ask for anything."""
    _dirs, report = build_chromium_layer(str(tmp_path), SEED, os_type="windows")
    record = report.as_record()

    assert masking_layer.GEO not in record["expected"]
    assert record["missing"] == []
    assert record["complete"] is True, record["failed"]


def test_a_match_product_geo_run_EXPECTS_geo_and_reports_it(tmp_path):
    """AC7, the other arm: when the configuration asks for ``geo`` the expected
    set names it, so a ``--match-product-geo`` run whose geo builder went
    missing would be caught rather than shrugged off."""
    _dirs, report = build_chromium_layer(
        str(tmp_path), SEED, os_type="windows", include_geo=True
    )
    record = report.as_record()

    assert masking_layer.GEO in record["expected"]
    assert masking_layer.GEO in record["installed"]
    assert record["complete"] is True, record["failed"]


def test_the_documented_EXCLUSIONS_do_not_make_a_record_incomplete(tmp_path):
    """AC6. ``search`` and ``mobile`` are deliberately not installed — a
    settings override and a mobile profile, neither of which a desktop checker
    run is. Their absence is a property of the CONFIGURATION, not a gap, so the
    expected set must not name them and a clean build must stay complete.

    Firefox's ``outer-size`` is the same argument on the other engine: the
    product installs it only when a resolution was explicitly chosen, and a
    harness profile chooses none.
    """
    _dirs, report = build_chromium_layer(str(tmp_path), SEED, os_type="windows")

    assert "search" not in report.as_record()["expected"]
    assert "mobile" not in report.as_record()["expected"]
    assert "outer-size" not in list(masking_layer.FIREFOX_VECTORS)
    assert report.as_record()["complete"] is True, report.failed


def test_a_firefox_SUBTRACTION_ARM_narrows_expected_and_stays_complete():
    """AC8, and the PS-119 arm this must not break.

    ``vectors=`` deliberately narrows the installed set so "WHICH spoof did the
    checker see?" can be answered by measurement. A subtraction arm is NOT an
    incomplete layer — it is a complete layer of a deliberately smaller
    configuration. If ``expected`` ignored the narrowing, every differential arm
    would start reporting ``complete: false`` and the differential's own records
    would become the thing that is wrong.
    """
    for narrowed in (
        (masking_layer.WEBGL,),
        (masking_layer.WEBGL, masking_layer.AUDIO),
        (masking_layer.LOCALE,),
    ):
        ctx = FakeContext()
        report = install_firefox_layer(
            ctx, SEED, locale="en-US", vectors=narrowed
        )
        record = report.as_record()

        assert record["expected"] == sorted(narrowed), narrowed
        assert record["missing"] == [], narrowed
        assert record["complete"] is True, (narrowed, record["failed"])


def test_a_firefox_arm_that_DROPPED_a_declared_vector_reads_incomplete():
    """The Firefox production path's half of AC1/AC3: a vector the declared set
    names, which the install never attempted, is reported missing.

    Driven through ``install_firefox_layer`` rather than hand-built, so a
    circular ``expected`` (derived from the pairs it was handed) would fail
    here: the pairs are short by one and the declared set is not.
    """
    ctx = FakeContext()
    short_pairs = [
        (v, js)
        for v, js in masking_layer.firefox_layer_scripts(SEED, locale="en-US")
        if v != masking_layer.AUDIO
    ]
    report = install_firefox_layer(ctx, SEED, locale="en-US", scripts=short_pairs)

    # `scripts=` states its own pair list, so the report declares nothing —
    # the caller is not asking about the declared configuration.
    assert report.as_record()["expected"] == []

    # The CONFIGURED path, by contrast, declares all three and catches the hole.
    declared = masking_layer.firefox_expected_vectors(locale="en-US")
    holed = LayerReport(
        route="init_scripts",
        installed=tuple(v for v, _ in short_pairs),
        expected=declared,
    )
    assert holed.as_record()["complete"] is False
    assert holed.as_record()["missing"] == [masking_layer.AUDIO]


def test_an_EMPTY_LOCALE_run_narrows_expected_rather_than_reading_incomplete():
    """THE DECISION POINT, DECIDED EXPLICITLY (and stated in the PR).

    ``firefox_layer_scripts`` keeps ``locale`` out of the pairs when the locale
    is empty, because ``_language_override_script("")`` is a documented no-op
    and ``installed`` must never name a vector that delivered nothing. If
    ``expected`` named ``locale`` unconditionally, such a call would newly read
    ``complete: false``.

    DECIDED: an empty locale is a CONFIGURATION CHOICE of the same class as
    ``include_geo=False`` — the caller did not ask for a locale override — so
    ``expected`` narrows with it and the run stays complete. Every in-tree
    caller passes ``DEFAULT_LOCALE`` (``browser_tier.py:591``), so this is
    reachable only by a caller passing ``""`` deliberately.

    The narrowing keys off the ARGUMENT, not off the produced script, which is
    what keeps it a tripwire: a builder that started returning empty for a
    NON-empty locale would still be caught.
    """
    ctx = FakeContext()
    report = install_firefox_layer(ctx, SEED, locale="")
    record = report.as_record()

    assert masking_layer.LOCALE not in record["installed"]
    assert masking_layer.LOCALE not in record["expected"]
    assert record["missing"] == []
    assert record["complete"] is True

    # ...and a NON-empty locale still expects it, so the narrowing above is a
    # statement about the configuration and not a blanket exemption.
    assert masking_layer.LOCALE in masking_layer.firefox_expected_vectors(
        locale="en-US"
    )


# --- the differential's reporting -------------------------------------------


def _arm(
    label: str,
    vectors: dict,
    error: str = "",
    sandbox_waived: bool = False,
    dev_shm_waived: bool = False,
    dev_shm_bytes: "int | None" = None,
) -> Arm:
    return Arm(
        label=label,
        reading=ProbeReading(vectors=vectors),
        layer=absent_layer("n/a") if error else LayerReport(route="init_scripts"),
        seed=SEED,
        error=error,
        sandbox_waived=sandbox_waived,
        dev_shm_waived=dev_shm_waived,
        dev_shm_bytes=dev_shm_bytes,
    )


def test_a_moved_vector_is_the_demonstration():
    record = build_differential_record(
        AXIS_LAYER, "firefox",
        _arm("off", {"webgl_pixel_hash": "aaaa", "intl_locale": "de-DE"}),
        _arm("on", {"webgl_pixel_hash": "bbbb", "intl_locale": "en-US"}),
    )
    assert record["verdict"] == "moved"
    assert set(record["diff"]["moved"]) == {"webgl_pixel_hash", "intl_locale"}


def test_identical_readings_report_UNMOVED_which_is_the_PS97_shape():
    """The reading that must not be mistaken for success. On the layer axis it
    means the code the layer was supposed to change did not change what the page
    sees — which is exactly what PS-97's re-read looked like.

    THE PROSE IS PINNED BY EQUALITY, not by substring. The layer-axis clause is
    the one that was always correct, and making the sentence axis-conditional
    put it at risk of collateral damage — a substring assertion would survive a
    reworded or truncated sentence, so it cannot detect that. The literal below
    is the string this branch produced BEFORE the axis conditional existed,
    captured from a run against the pre-fix code rather than retyped from it.
    """
    same = {"webgl_pixel_hash": "51df3565", "audio_digest": "35.749972"}
    record = build_differential_record(
        AXIS_LAYER, "firefox", _arm("off", same), _arm("on", dict(same))
    )
    assert record["verdict"] == "unmoved"
    assert record["diff"]["moved"] == {}
    assert record["detail"] == (
        "all 2 comparable vectors read IDENTICALLY when layer changed. On the "
        "layer axis this is the PS-97 shape: the code the layer was supposed "
        "to change did not change what the page sees."
    )


def test_an_unmoved_SEED_control_names_the_SEED_axis_not_the_layer():
    """THE DEFECT PS-116 WAS FILED FOR: the state where this misleads is a real
    finding, not an error path.

    An unmoved reading on the SEED axis means two different seeds produced
    identical values — the compared vectors are not seed-derived. That is the
    PS-97-shaped alarm this harness exists to raise, and the branch used to
    hand the reader a sentence about the LAYER, sending them to look for a
    cause that is not there. The reader most likely to hit it is the one who
    most needs the reading to be right.

    Asserted on the ``detail`` of a real ``build_differential_record`` — the
    string a person actually reads off the regression-gate artifact — never on
    a helper having been called.
    """
    same = {"webgl_pixel_hash": "51df3565", "audio_digest": "35.749972"}
    record = build_differential_record(
        AXIS_SEED, "firefox", _arm("seed4242", same), _arm("seed1337", dict(same))
    )

    assert record["verdict"] == "unmoved"
    # AC1's negative half: the wrong-axis pointer must be gone.
    assert "On the layer axis" not in record["detail"]
    # AC1's positive half: deleting the sentence would also satisfy the line
    # above, so pin that the seed-axis MEANING is actually stated.
    assert "seed changed" in record["detail"]
    assert "not seed-derived" in record["detail"]


def test_vectors_the_PAGE_COULD_NOT_READ_are_never_counted_as_unmoved():
    """THE FALSE-NEGATIVE THIS INSTRUMENT MUST NOT PRODUCE.

    Two sides agreeing on ``unavailable:no-webgl-context`` is not evidence that
    a spoof failed to land — it is the page saying it never measured anything.
    Counting it as an unmoved vector would manufacture the precise wrong verdict
    this whole ticket is about.
    """
    unreadable = {
        "webgl_pixel_hash": "unavailable:no-webgl-context",
        "audio_digest": "error:TypeError",
    }
    record = build_differential_record(
        AXIS_LAYER, "firefox", _arm("off", unreadable), _arm("on", dict(unreadable))
    )

    assert record["verdict"] == "inconclusive"
    assert record["comparable_vectors"] == []
    assert "NOT evidence" in record["detail"]


def test_an_arm_that_did_not_run_is_INCONCLUSIVE_never_a_failure():
    """"The engine would not start here" and "the layer does not reach the page"
    are different findings. A run that collapsed them would report an
    unprovisioned container as a masking defect."""
    record = build_differential_record(
        AXIS_LAYER, "firefox",
        _arm("off", {}, error="persona's engine is not importable"),
        _arm("on", {"webgl_pixel_hash": "bbbb"}),
    )
    assert record["verdict"] == "inconclusive"
    assert "not importable" in record["detail"]


def test_a_partially_readable_pair_still_compares_what_it_CAN():
    """One dead vector must not make the whole differential inconclusive when
    another vector answered perfectly well."""
    record = build_differential_record(
        AXIS_LAYER, "firefox",
        _arm("off", {"webgl_pixel_hash": "aaaa",
                     "audio_digest": "unavailable:no-offline-audio-context"}),
        _arm("on", {"webgl_pixel_hash": "bbbb",
                    "audio_digest": "unavailable:no-offline-audio-context"}),
    )
    assert record["verdict"] == "moved"
    assert record["comparable_vectors"] == ["webgl_pixel_hash"]


# --- the local page ----------------------------------------------------------


def test_the_probe_page_reads_the_vectors_through_REAL_WORK():
    """A probe that asked ``typeof window.__personaWebglPatched`` would pass on a
    page the spoof never reached. Every vector must be computed from work the
    page actually does."""
    html = local_probe.probe_page_html()

    assert "readPixels" in html, "the webgl vector must render and read back"
    assert "startRendering" in html, (
        "the audio vector must RENDER — an unrendered graph reads -Infinity in "
        "every bin and produces a constant, which is a dead vector reporting a "
        "plausible number"
    )
    assert "getChannelData" in html
    assert "resolvedOptions" in html, (
        "the locale vector must read a REQUESTED locale back; a bare "
        "navigator.language read cannot tell a working spoof from a leak on a "
        "host whose own locale already matches"
    )


def test_the_page_is_parsed_the_same_way_a_real_checker_is():
    """The browser tier reads pages through ``inner_text`` because
    ``page.evaluate`` is blocked by CSP on real checker pages. The local page
    must be read the same way, or the demonstration succeeds through a route the
    real run does not have."""
    reading = local_probe.parse_probe_text(
        'noise before {"webgl_pixel_hash": "abcd1234"} noise after'
    )
    assert reading.vectors == {"webgl_pixel_hash": "abcd1234"}


@pytest.mark.parametrize("text,expected_note", [
    ("", "no text"),
    ("still reading...", "no JSON object"),
    ("{not json}", "did not parse"),
])
def test_an_unreadable_page_is_reported_with_a_REASON_not_as_empty_vectors(
    text, expected_note
):
    """An unobtainable reading must never read as a reading that said nothing
    adverse — the vocabulary the whole verify package is built on."""
    reading = local_probe.parse_probe_text(text)
    assert reading.vectors == {}
    assert expected_note in reading.note


def test_the_probe_server_serves_the_page_on_LOOPBACK_with_no_exit():
    """The venue requirement: no credential, no proxy, no exit. PS-10 records an
    explicit instruction not to re-introduce that dependency."""
    import urllib.request

    with local_probe.ProbeServer() as server:
        assert server.url.startswith("http://127.0.0.1:")
        body = urllib.request.urlopen(server.url, timeout=5).read().decode("utf-8")

    assert "readPixels" in body and "startRendering" in body


def test_appeared_and_vanished_are_reported_APART_from_moved():
    """"This reading did not exist" and "this reading changed" are different
    findings. Collapsing them would let a probe that stopped working read as a
    spoof that started working."""
    diff = differential(
        ProbeReading(vectors={"a": "1", "gone": "x"}),
        ProbeReading(vectors={"a": "2", "new": "y"}),
    )
    assert set(diff["moved"]) == {"a"}
    assert diff["vanished"] == ["gone"]
    assert diff["appeared"] == ["new"]


# --- the no-proxy venue (B1) --------------------------------------------------
#
# The differential's chromium arm was structurally dead: it constructed the
# session with an empty proxy URL, and `_proxy_server_and_bridge("")` refused
# that unconditionally on every host. The CLI advertised `--engine chromium`
# regardless, so it was a documented, reachable, always-failing route — and it
# failed with a message about *proxy credentials*, pointing a reader at the
# credential wall rather than at the real cause.
#
# The fix has to hold BOTH directions, and the second is the one worth guarding:
# a no-proxy launch must be possible for a venue that has no exit, and must stay
# REFUSED for one that should have had a credential.


def test_a_no_proxy_launch_is_possible_for_a_venue_that_has_NO_exit():
    """The loopback differential has no exit and must be able to say so.

    Asserted through the value that actually reaches the command line rather
    than through the flag alone: an empty ``--proxy-server=`` would be a
    different configuration than no proxy, and the bridge must not be started
    for an upstream that does not exist.
    """
    from src.services.verify.chromium_tier import NO_PROXY, _proxy_server_and_bridge

    proxy_server, bridge = _proxy_server_and_bridge("", allow_no_proxy=True)

    assert proxy_server == NO_PROXY
    assert bridge is None, "no upstream exists, so no relay may be started"


def test_a_no_proxy_launch_is_REFUSED_when_the_run_did_not_ask():
    """The default must stay hostile: a checker run whose credential went
    missing has to fail, not quietly read the operator's REAL address while
    every verdict parses and every row lands as READ.

    The message is asserted too, because the old one blamed the credential's
    *form* for a value that was simply absent — sending a reader to the
    credential wall instead of to the missing opt-in.
    """
    from src.services.verify.chromium_tier import (
        ChromiumUnavailable,
        _proxy_server_and_bridge,
    )

    with pytest.raises(ChromiumUnavailable) as caught:
        _proxy_server_and_bridge("")

    message = str(caught.value)
    assert "no proxy credential was given" in message
    assert "did not ask" in message
    assert "not in a form" not in message, (
        "an ABSENT credential must not be reported as a malformed one"
    )


def test_the_no_proxy_venue_puts_an_EXPLICIT_flag_on_the_command_line():
    """``--no-proxy-server`` is stated rather than left off.

    Chromium with no proxy flag at all falls back to the SYSTEM proxy, which is
    neither "no proxy" nor a proxy this run chose. So the absence of
    ``--proxy-server`` is not enough; the refusal has to be explicit.
    """
    from src.services.verify.chromium_tier import NO_PROXY, _launch_args

    args = _launch_args(
        "/bin/true", "/tmp/profile",
        seed=SEED, declared_machine="windows", proxy_server=NO_PROXY,
    )

    assert "--no-proxy-server" in args
    assert not [a for a in args if a.startswith("--proxy-server=")]
    assert NO_PROXY not in " ".join(args), (
        "the sentinel is internal and must never reach the command line"
    )


def test_a_real_credential_still_reaches_the_command_line_unchanged():
    """The waiver must not have moved the normal path: a proxied checker run is
    what the tier exists for, and NO_PROXY must not leak into it."""
    from src.services.verify.chromium_tier import _launch_args

    args = _launch_args(
        "/bin/true", "/tmp/profile",
        seed=SEED, declared_machine="windows",
        proxy_server="socks5://127.0.0.1:1080",
    )

    assert "--proxy-server=socks5://127.0.0.1:1080" in args
    assert "--no-proxy-server" not in args


# --- ONE copy of the launch-and-install wiring (B3) ---------------------------
#
# `masking_layer` records that a second copy of the SPOOF SET, drifting from the
# one the product launches, would reproduce the very defect it exists to close.
# The INSTALL wiring can drift the same way one level up: a differential with
# its own launch keeps passing while the harness's own path loses the layer,
# proving something about a path no real run has.


def test_the_differential_and_the_real_run_share_ONE_firefox_launch():
    """Both callers must reach the page through ``firefox_session``.

    Asserted on the CALL rather than on source text: the double records what it
    was handed, so this fails if either caller grows its own launch again.
    """
    import contextlib

    from src.services.verify import browser_tier, layer_differential

    calls: "list[dict]" = []

    @contextlib.contextmanager
    def recording_session(proxy_url, *, seed, install_layer=True, layer_sink=None):
        calls.append({
            "proxy_url": proxy_url,
            "seed": seed,
            "install_layer": install_layer,
        })
        if layer_sink is not None:
            layer_sink(LayerReport(route="init_scripts", installed=("webgl",)))
        yield object()

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(browser_tier, "firefox_session", recording_session)
        monkey.setattr(
            layer_differential, "_load_and_read", lambda *a, **k: '{"webgl": "1"}'
        )
        layer_differential.read_probe_once(
            "http://127.0.0.1:1/", seed=SEED, engine="firefox", install_layer=False
        )
    finally:
        monkey.undo()

    assert len(calls) == 1, "the differential must not construct its own engine"
    assert calls[0]["seed"] == SEED
    assert calls[0]["install_layer"] is False, (
        "the control arm's request has to survive to the shared launch"
    )
    assert calls[0]["proxy_url"] == "", "the loopback venue passes no credential"


def test_the_layer_report_from_the_shared_launch_reaches_the_arm():
    """The differential's record must carry what the SHARED launch reported,
    not a report the differential composed for itself — otherwise the arm can
    claim a layer the launch never installed.

    The fake launch here reports a TWO-vector Firefox layer, so ``locale`` was
    never attempted. Since PS-242 that arm reads ``complete: false`` and names
    it, where it used to read ``complete: true``. Inverted deliberately: the
    property under test is that the arm carries THE LAUNCH'S report verbatim,
    and a report that under-states its layer must keep saying so all the way
    into the differential record rather than being laundered clean on the way.
    """
    import contextlib

    from src.services.verify import browser_tier, layer_differential

    @contextlib.contextmanager
    def session(proxy_url, *, seed, install_layer=True, layer_sink=None):
        if layer_sink is not None:
            layer_sink(
                LayerReport(route="init_scripts", installed=("audio", "webgl"))
            )
        yield object()

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(browser_tier, "firefox_session", session)
        monkey.setattr(
            layer_differential, "_load_and_read", lambda *a, **k: '{"webgl": "1"}'
        )
        arm = layer_differential.read_probe_once(
            "http://127.0.0.1:1/", seed=SEED, engine="firefox"
        )
    finally:
        monkey.undo()

    assert arm.layer.installed == ("audio", "webgl")
    assert arm.as_record()["layer"]["complete"] is False
    assert arm.as_record()["layer"]["missing"] == ["locale"]


def test_a_FULL_layer_from_the_shared_launch_reaches_the_arm_as_complete():
    """The other half of the assertion above: an arm whose launch reported the
    whole declared set still reads ``complete: true`` in the differential
    record. Without this the test above could be satisfied by an arm that
    reports incomplete unconditionally."""
    import contextlib

    from src.services.verify import browser_tier, layer_differential

    @contextlib.contextmanager
    def session(proxy_url, *, seed, install_layer=True, layer_sink=None):
        if layer_sink is not None:
            layer_sink(
                LayerReport(
                    route="init_scripts",
                    installed=masking_layer.FIREFOX_VECTORS,
                )
            )
        yield object()

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(browser_tier, "firefox_session", session)
        monkey.setattr(
            layer_differential, "_load_and_read", lambda *a, **k: '{"webgl": "1"}'
        )
        arm = layer_differential.read_probe_once(
            "http://127.0.0.1:1/", seed=SEED, engine="firefox"
        )
    finally:
        monkey.undo()

    assert arm.as_record()["layer"]["complete"] is True
    assert arm.as_record()["layer"]["missing"] == []


def test_a_loopback_launch_carries_no_proxy_and_no_prefs():
    """The prefs are the CREDENTIAL's browser-side half (remote DNS, no direct
    failover). On a page with no exit there is no resolution to leak and nothing
    to fail over to — and asking the engine for a proxy it does not have is how
    the chromium arm died."""
    from src.services.verify import browser_tier

    captured: "list[dict]" = []

    class FakeEngine:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        def __enter__(self):
            return FakeContext()

        def __exit__(self, *exc):
            return False

    monkey = pytest.MonkeyPatch()
    try:
        import sys
        import types

        module = types.ModuleType("invisible_playwright")
        module.InvisiblePlaywright = FakeEngine
        monkey.setitem(sys.modules, "invisible_playwright", module)
        with browser_tier.firefox_session("", seed=SEED, install_layer=False):
            pass
    finally:
        monkey.undo()

    assert len(captured) == 1
    assert "proxy" not in captured[0]
    assert "extra_prefs" not in captured[0]
    assert captured[0]["seed"] == SEED


def test_a_credentialled_run_still_gets_its_proxy_AND_its_prefs():
    """The counterpart, and the one that actually protects a real reading: the
    checker run's credential and the prefs that stop a silent direct failover
    must both survive the shared launch."""
    from src.services.verify import browser_tier

    captured: "list[dict]" = []

    class FakeEngine:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        def __enter__(self):
            return FakeContext()

        def __exit__(self, *exc):
            return False

    monkey = pytest.MonkeyPatch()
    try:
        import sys
        import types

        module = types.ModuleType("invisible_playwright")
        module.InvisiblePlaywright = FakeEngine
        monkey.setitem(sys.modules, "invisible_playwright", module)
        with browser_tier.firefox_session(
            "socks5h://user:pass@host:1080", seed=SEED, install_layer=False
        ):
            pass
    finally:
        monkey.undo()

    kwargs = captured[0]
    assert kwargs["proxy"]["server"] == "socks5://host:1080"
    assert kwargs["proxy"]["username"] == "user"
    assert kwargs["extra_prefs"]["network.proxy.failover_direct"] is False
    assert kwargs["extra_prefs"]["network.proxy.socks_remote_dns"] is True


# --- the differential record DISCLOSES ITS OWN CONDITIONS (C1) ----------------
#
# `--allow-unsandboxed-chromium`'s help text promised "a reading taken with it
# is not the product's surface AND THE RECORD SAYS SO". The record did not say
# so: no sandbox/waiver/no-sandbox key appeared anywhere in it, while the
# sibling `read` path tagged the identical condition correctly via `_notes_for`.
#
# That is this ticket's own thesis landing on the artifact this ticket adds — a
# record describing a subject (an engine running without the security boundary
# the product keeps) that a consumer cannot tell apart from a clean one. So the
# tests below are hostile in the direction that matters: not only "does a waived
# run say so", but "can a record be made to claim a waiver that never happened".


def test_a_waived_chromium_differential_SAYS_SO_in_the_record():
    """The promise the help text makes, asserted on the record itself.

    Checked on the DOCUMENT rather than on a log line or a stderr banner,
    because the record is the durable thing: it outlives the terminal it was
    printed in, and it is what a future reader diffs against.
    """
    record = build_differential_record(
        AXIS_LAYER, "chromium",
        _arm("off", {"audio_digest": "124.036605"}, sandbox_waived=True),
        _arm("on", {"audio_digest": "124.036578"}, sandbox_waived=True),
    )

    assert record["sandbox_waived"] is True
    assert record["before"]["sandbox_waived"] is True
    assert record["after"]["sandbox_waived"] is True

    note = "\n".join(record["notes"])
    assert "--no-sandbox" in note
    assert "NOT the surface the product presents" in note, (
        "the note must say what the reading is NOT, which is the whole point"
    )


def test_an_UNWAIVED_run_never_claims_a_waiver_it_did_not_take():
    """The direction that makes the disclosure worth anything.

    A flag that is always on discloses nothing. If a clean sandboxed reading
    also carried the note, a consumer would learn to ignore it — and the tag
    would be decoration rather than information.
    """
    record = build_differential_record(
        AXIS_LAYER, "firefox",
        _arm("off", {"audio_digest": "35.749972"}),
        _arm("on", {"audio_digest": "35.749964"}),
    )

    assert record["sandbox_waived"] is False
    assert record["notes"] == []


def test_a_firefox_arm_is_never_tagged_with_a_flag_firefox_IGNORES():
    """TRYING TO MAKE THE RECORD LIE.

    ``--allow-unsandboxed-chromium`` is accepted on the CLI whatever
    ``--engine`` says, and is documented as "Ignored on firefox". A record that
    echoed the REQUEST would therefore tag a firefox reading with a waiver that
    was never applied to it — a record claiming a condition its subject never
    ran under, which is precisely the defect class PS-103 exists to close.

    The guard is structural, not textual: the flag is reported by the SESSION
    that launched (read back off chromium's command line), and the firefox
    launch has no such flag to report.
    """
    from src.services.verify import browser_tier, layer_differential

    import contextlib

    @contextlib.contextmanager
    def session(proxy_url, *, seed, install_layer=True, layer_sink=None):
        if layer_sink is not None:
            layer_sink(LayerReport(route="init_scripts", installed=("webgl",)))
        yield object()

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(browser_tier, "firefox_session", session)
        monkey.setattr(
            layer_differential, "_load_and_read", lambda *a, **k: '{"webgl": "1"}'
        )
        # The flag is passed, and the engine is one that cannot honour it.
        arm = layer_differential.read_probe_once(
            "http://127.0.0.1:1/", seed=SEED, engine="firefox",
            allow_unsandboxed=True,
        )
    finally:
        monkey.undo()

    assert arm.sandbox_waived is False, (
        "firefox has no sandbox flag to waive; reporting the REQUEST here "
        "would tag a reading with a condition it was never taken under"
    )
    assert arm.as_record()["sandbox_waived"] is False


def test_the_waiver_is_read_off_the_COMMAND_LINE_not_off_the_request():
    """What the process REALLY ran with, not what the caller asked for.

    These are the same value only as long as nothing between the request and
    the launch changes its mind. Asserting on the request would make the record
    a restatement of the caller's intent; asserting on argv makes it a fact
    about the process that produced the reading.
    """
    from src.services.verify.chromium_tier import _launch_args

    waived = _launch_args(
        "/bin/chrome", "/tmp/p", seed=SEED, declared_machine="windows",
        proxy_server="socks5://127.0.0.1:1080", allow_unsandboxed=True,
    )
    sandboxed = _launch_args(
        "/bin/chrome", "/tmp/p", seed=SEED, declared_machine="windows",
        proxy_server="socks5://127.0.0.1:1080", allow_unsandboxed=False,
    )

    assert "--no-sandbox" in waived
    assert "--no-sandbox" not in sandboxed, (
        "persona's own launch path passes this NOWHERE; it must never be a "
        "default or a fallback"
    )


def test_a_HALF_waived_pair_warns_that_a_SECOND_AXIS_moved():
    """One axis at a time, or the difference is attributable to neither.

    If only one arm dropped the sandbox, the sandbox moved alongside the axis
    under test. The record must not present that as a clean single-axis result
    — this is the method QA imposed on PS-69 for exactly this reason.
    """
    record = build_differential_record(
        AXIS_LAYER, "chromium",
        _arm("off", {"audio_digest": "1"}, sandbox_waived=False),
        _arm("on", {"audio_digest": "2"}, sandbox_waived=True),
    )

    assert record["sandbox_waived"] is True
    note = "\n".join(record["notes"])
    assert "SECOND axis" in note
    assert "attributable to neither" in note


def test_the_disclosure_SURVIVES_to_the_artifact_a_reader_actually_opens():
    """A tag that exists only in memory discloses nothing to the next reader.

    ``dumps`` is what the CLI writes to stdout and what ``matrix.write`` puts on
    disk, so the round trip through JSON is the last place the disclosure could
    be lost.
    """
    from src.services.verify.layer_differential import dumps

    record = build_differential_record(
        AXIS_LAYER, "chromium",
        _arm("off", {"audio_digest": "124.036605"}, sandbox_waived=True),
        _arm("on", {"audio_digest": "124.036578"}, sandbox_waived=True),
    )

    reloaded = json.loads(dumps(record))

    assert reloaded["sandbox_waived"] is True
    assert "--no-sandbox" in "\n".join(reloaded["notes"])
    assert reloaded["before"]["sandbox_waived"] is True


# --- PS-133: the /dev/shm waiver on the DIFFERENTIAL path -------------------
#
# The `read` path already discloses this waiver
# (`test_the_dev_shm_waiver_is_recorded_in_the_reading_it_produced`). These are
# its counterparts on the OTHER record path, and the gap they close is not
# hypothetical: `--allow-small-dev-shm` was accepted on the differential
# subparser and threaded all the way down to the launch, and then disclosed
# NOWHERE — a differential taken on the workaround surface was byte-identical
# to one taken on a healthy host.
#
# That matters most here of all places. AXIS_SEED defaults to 4242 vs 1337 —
# the PS-97 two-seed comparison this ticket exists to unblock — so the reading
# taken on the strength of this work runs on exactly this path.


def test_a_dev_shm_waived_differential_SAYS_SO_in_the_record():
    """The same promise the `read` path already keeps, on the other path.

    Asserted on the DOCUMENT for the same reason the sandbox twin above is: the
    record is the durable thing a future reader diffs against, and an
    undisclosed environmental condition riding along with a reading is how a
    host limit comes to be read as a property of the product.
    """
    record = build_differential_record(
        AXIS_SEED, "chromium",
        _arm("seed4242", {"audio_digest": "124.036605"},
             dev_shm_waived=True, dev_shm_bytes=64 * 1024 * 1024),
        _arm("seed1337", {"audio_digest": "124.036578"},
             dev_shm_waived=True, dev_shm_bytes=64 * 1024 * 1024),
    )

    assert record["dev_shm_waived"] is True
    assert record["before"]["dev_shm_waived"] is True
    assert record["after"]["dev_shm_waived"] is True

    note = "\n".join(record["notes"])
    assert "--disable-dev-shm-usage" in note
    assert "NOT the surface the product presents" in note, (
        "the note must say what the reading is NOT, which is the whole point"
    )


def test_the_record_states_the_NUMBER_not_merely_the_verdict():
    """"64 MiB" tells a reader what to change; "too small" does not.

    ``dev_shm_bytes`` was built with that comment on it and then read by
    nothing — an attribute serving a consumer that was never written. This is
    that consumer.
    """
    record = build_differential_record(
        AXIS_SEED, "chromium",
        _arm("seed4242", {"audio_digest": "1"},
             dev_shm_waived=True, dev_shm_bytes=64 * 1024 * 1024),
        _arm("seed1337", {"audio_digest": "2"},
             dev_shm_waived=True, dev_shm_bytes=64 * 1024 * 1024),
    )

    assert record["dev_shm_bytes"] == 64 * 1024 * 1024
    assert record["before"]["dev_shm_bytes"] == 64 * 1024 * 1024

    note = "\n".join(record["notes"])
    assert "64 MiB" in note, "the measured size must appear in the prose"
    assert "256 MiB" in note, (
        "the floor must appear beside it, or the number has nothing to be "
        "read against"
    )


def test_an_unwaived_differential_never_claims_a_dev_shm_waiver():
    """The direction that makes the disclosure worth anything.

    A note that is always present discloses nothing — a consumer would learn to
    ignore it, and the tag would be decoration rather than information. This is
    the exact counterpart of
    ``test_a_normal_reading_does_not_carry_the_dev_shm_waiver_note`` on the
    `read` path.
    """
    record = build_differential_record(
        AXIS_SEED, "chromium",
        _arm("seed4242", {"audio_digest": "1"}),
        _arm("seed1337", {"audio_digest": "2"}),
    )

    assert record["dev_shm_waived"] is False
    assert record["dev_shm_bytes"] is None
    assert not any("--disable-dev-shm-usage" in n for n in record["notes"])


def test_a_HALF_waived_dev_shm_pair_warns_that_a_SECOND_AXIS_moved():
    """One axis at a time, or the difference is attributable to neither.

    The same method the sandbox twin enforces, and it applies here verbatim: if
    only one arm ran its renderer transport off disk, that moved alongside the
    axis under test. On the SEED axis — the PS-97 comparison — presenting such
    a pair as a clean single-axis result is how a wrong answer gets recorded.
    """
    record = build_differential_record(
        AXIS_SEED, "chromium",
        _arm("seed4242", {"audio_digest": "1"},
             dev_shm_waived=True, dev_shm_bytes=64 * 1024 * 1024),
        _arm("seed1337", {"audio_digest": "2"}),
    )

    assert record["dev_shm_waived"] is True
    note = "\n".join(record["notes"])
    assert "--disable-dev-shm-usage" in note
    assert "SECOND axis" in note
    assert "attributable to neither" in note


def test_the_two_waivers_are_disclosed_INDEPENDENTLY():
    """A host can forbid the sandbox and have a healthy /dev/shm, or the
    reverse. Collapsing them into one flag would let a reader conclude the
    wrong thing about which surface actually moved.

    This is also the guard against the shape that caused the rework: threading
    one waiver and leaving the other silently False.
    """
    sandbox_only = build_differential_record(
        AXIS_LAYER, "chromium",
        _arm("off", {"audio_digest": "1"}, sandbox_waived=True),
        _arm("on", {"audio_digest": "2"}, sandbox_waived=True),
    )
    assert sandbox_only["sandbox_waived"] is True
    assert sandbox_only["dev_shm_waived"] is False
    assert not any(
        "--disable-dev-shm-usage" in n for n in sandbox_only["notes"]
    )

    shm_only = build_differential_record(
        AXIS_LAYER, "chromium",
        _arm("off", {"audio_digest": "1"},
             dev_shm_waived=True, dev_shm_bytes=64 * 1024 * 1024),
        _arm("on", {"audio_digest": "2"},
             dev_shm_waived=True, dev_shm_bytes=64 * 1024 * 1024),
    )
    assert shm_only["dev_shm_waived"] is True
    assert shm_only["sandbox_waived"] is False
    assert not any("--no-sandbox" in n for n in shm_only["notes"])

    # Both at once: two notes, not one merged sentence.
    both = build_differential_record(
        AXIS_LAYER, "chromium",
        _arm("off", {"audio_digest": "1"}, sandbox_waived=True,
             dev_shm_waived=True, dev_shm_bytes=64 * 1024 * 1024),
        _arm("on", {"audio_digest": "2"}, sandbox_waived=True,
             dev_shm_waived=True, dev_shm_bytes=64 * 1024 * 1024),
    )
    assert both["sandbox_waived"] is True and both["dev_shm_waived"] is True
    assert len(both["notes"]) == 2


def test_a_firefox_arm_is_never_tagged_with_the_dev_shm_flag_either():
    """TRYING TO MAKE THE RECORD LIE, on the second waiver.

    ``--allow-small-dev-shm`` is accepted on the CLI whatever ``--engine``
    says. A record that echoed the REQUEST would tag a firefox reading with a
    workaround that was never applied to it. The guard is structural, exactly
    as it is for the sandbox: the waiver is reported by the SESSION that
    launched, and the firefox launch reports nothing.
    """
    import contextlib

    from src.services.verify import browser_tier, layer_differential

    @contextlib.contextmanager
    def session(proxy_url, *, seed, install_layer=True, layer_sink=None):
        if layer_sink is not None:
            layer_sink(LayerReport(route="init_scripts", installed=("webgl",)))
        yield object()

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(browser_tier, "firefox_session", session)
        monkey.setattr(
            layer_differential, "_load_and_read", lambda *a, **k: '{"webgl": "1"}'
        )
        arm = layer_differential.read_probe_once(
            "http://127.0.0.1:1/", seed=SEED, engine="firefox",
            allow_small_dev_shm=True,
        )
    finally:
        monkey.undo()

    assert arm.dev_shm_waived is False, (
        "firefox has no such flag; reporting the REQUEST here would tag a "
        "reading with a condition it was never taken under"
    )
    assert arm.dev_shm_bytes is None, (
        "a launch that never asked the question must not state a number"
    )
    assert arm.as_record()["dev_shm_waived"] is False


def test_the_dev_shm_disclosure_SURVIVES_the_round_trip_to_JSON():
    """A tag that exists only in memory discloses nothing to the next reader.

    ``dumps`` is what the CLI writes to stdout and what ``matrix.write`` puts
    on disk, so this is the last place the disclosure could be lost.
    """
    from src.services.verify.layer_differential import dumps

    record = build_differential_record(
        AXIS_SEED, "chromium",
        _arm("seed4242", {"audio_digest": "124.036605"},
             dev_shm_waived=True, dev_shm_bytes=64 * 1024 * 1024),
        _arm("seed1337", {"audio_digest": "124.036578"},
             dev_shm_waived=True, dev_shm_bytes=64 * 1024 * 1024),
    )

    reloaded = json.loads(dumps(record))

    assert reloaded["dev_shm_waived"] is True
    assert reloaded["dev_shm_bytes"] == 64 * 1024 * 1024
    assert "--disable-dev-shm-usage" in "\n".join(reloaded["notes"])
    assert reloaded["before"]["dev_shm_waived"] is True
    assert reloaded["after"]["dev_shm_bytes"] == 64 * 1024 * 1024


# --- PS-119: the harness must declare the locale the way the product does ----
#
# The defect these guard was NOT in the spoof. A live pixelscan reading through
# a proven exit returned `masking_detected` with the layer installed and ABSENT
# with the locale vector subtracted — while a 314-observable integrity sweep
# found the patched JS surface structurally IDENTICAL to the unpatched engine.
#
# The cause was one missing launch argument. `invisible_launch` sets
# `kwargs["locale"]` on every product launch, and that value drives Firefox's
# `intl.accept_languages` — the ACCEPT-LANGUAGE HEADER. The harness passed none,
# so the header carried the HOST OS locale while the layer pinned
# navigator.language/Intl to DEFAULT_LOCALE. Header de-DE + JS en-US is exactly
# the "internal contradiction a scanner flags as masking" that
# `_language_override_script`'s own docstring exists to prevent.
#
# Asserted on the kwargs REALLY handed to the engine constructor, not on source
# text: a source assertion would pass on a tree where the value never reached
# the launch.


def _kwargs_handed_to_the_engine(**session_kwargs):
    """Run ``firefox_session`` against a stub engine and return its kwargs."""
    import sys as _sys
    import types

    from src.services.verify import browser_tier as bt

    seen: "list[dict]" = []

    class _Engine:
        def __enter__(self):
            return _Ctx()

        def __exit__(self, *a):
            return False

    class _Ctx:
        pages = ()

        def add_init_script(self, js):
            return None

    def _construct(**kw):
        seen.append(kw)
        return _Engine()

    fake = types.SimpleNamespace(InvisiblePlaywright=_construct)
    saved = _sys.modules.get("invisible_playwright")
    _sys.modules["invisible_playwright"] = fake
    try:
        with bt.firefox_session("", seed=SEED, **session_kwargs):
            pass
    finally:
        if saved is None:
            _sys.modules.pop("invisible_playwright", None)
        else:
            _sys.modules["invisible_playwright"] = saved
    assert seen, "the engine constructor was never called"
    return seen[0]


def test_the_harness_declares_a_locale_to_the_engine_like_the_product_does():
    """Without this the Accept-Language header carries the HOST locale.

    The header is what the engine derives from this argument; the masking layer
    pins the JS side to the SAME constant. Passing nothing here is what made the
    two disagree, which is the tell pixelscan named.
    """
    kwargs = _kwargs_handed_to_the_engine()

    assert "locale" in kwargs, (
        "firefox_session handed the engine no locale, so Accept-Language falls "
        "back to the HOST OS locale while the masking layer pins JS to "
        "DEFAULT_LOCALE — the header/JS contradiction PS-119 measured as "
        "pixelscan's masking_detected"
    )
    assert kwargs["locale"] == masking_layer.DEFAULT_LOCALE


def test_the_engine_locale_is_the_SAME_constant_the_layer_pins_the_js_side_to():
    """One constant on both sides, or the two can drift back apart.

    This is the invariant that actually matters: it is not that a locale is
    passed, it is that the header and the JS getters carry the same value. Two
    independently-chosen literals would satisfy the test above and still be
    detectable.
    """
    kwargs = _kwargs_handed_to_the_engine()

    installed = firefox_layer_scripts(SEED, locale=masking_layer.DEFAULT_LOCALE)
    locale_js = dict(installed)[masking_layer.LOCALE]

    assert kwargs["locale"] == masking_layer.DEFAULT_LOCALE
    # the value the layer actually writes into the page realm
    assert json.dumps(masking_layer.DEFAULT_LOCALE) in locale_js


def test_the_control_arm_ALSO_declares_the_locale():
    """The engine-only arm needs it too, and for a subtler reason.

    ``--no-masking-layer`` installs no spoof, so nothing pins the JS side — but
    the header still comes from this argument. If the control arm took the host
    locale while the product arm took DEFAULT_LOCALE, the two arms would differ
    by the HOST's identity as well as by the layer, and the differential would
    be attributing a difference the layer did not cause.
    """
    kwargs = _kwargs_handed_to_the_engine(install_layer=False)

    assert kwargs["locale"] == masking_layer.DEFAULT_LOCALE
