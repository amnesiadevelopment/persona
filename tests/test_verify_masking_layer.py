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

    Every record written before this ticket describes the packaged engines. A
    consumer has to be able to tell that apart from a reading of the product
    without knowing this ticket exists.
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
    assert record["masking_layer"]["complete"] is True
    assert record["schema_version"] == matrix.SCHEMA_VERSION


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
    report = absent_layer("the engine never started")
    as_record = report.as_record()

    assert as_record["complete"] is False
    assert as_record["installed"] == []
    assert "never started" in json.dumps(as_record)


# --- the differential's reporting -------------------------------------------


def _arm(
    label: str, vectors: dict, error: str = "", sandbox_waived: bool = False
) -> Arm:
    return Arm(
        label=label,
        reading=ProbeReading(vectors=vectors),
        layer=absent_layer("n/a") if error else LayerReport(route="init_scripts"),
        seed=SEED,
        error=error,
        sandbox_waived=sandbox_waived,
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
    sees — which is exactly what PS-97's re-read looked like."""
    same = {"webgl_pixel_hash": "51df3565", "audio_digest": "35.749972"}
    record = build_differential_record(
        AXIS_LAYER, "firefox", _arm("off", same), _arm("on", dict(same))
    )
    assert record["verdict"] == "unmoved"
    assert record["diff"]["moved"] == {}


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
    claim a layer the launch never installed."""
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
    assert arm.as_record()["layer"]["complete"] is True


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
