"""Both engines, more than one declared machine — and the record saying which.

Every prior checker reading was Firefox, on one seed, on one undeclared
machine. This file covers the selection that widens that, and it is written
around the two ways widening it could go quietly wrong:

* **A fabricated machine.** The Firefox engine cannot be asked for an OS —
  ``InvisiblePlaywright`` has no such parameter — and presents Windows
  regardless. A record that echoed the REQUEST would name a machine that was
  never presented, and a later comparison would read that difference as a
  product coupling rather than as an engine that ignored the question. The
  asymmetry is asserted here in both directions.

* **Two engines that drift into two dialects.** A Chromium-specific copy of
  the read loop is how one engine quietly acquires a reading path the other
  lacks — and a record built from two dialects is not comparable, which is the
  entire purpose of producing it. ``test_both_engines_reach_the_same_loop``
  drives the SHARED loop with a fake session and pins that both engines' page
  handling comes out of one function.

The pages here are the same captured fixtures the rest of the suite uses; this
file adds no new reader and asserts no new pattern.
"""

from __future__ import annotations

import os

import pytest

from src.services.verify import browser_tier as bt
from src.services.verify import checker_cli as cli
from src.services.verify.checkers import BROWSER_CHECKERS, ENGINE_EXIT_CHECKER
from src.services.verify.exit_guard import Exit
from src.services.verify.matrix import UNOBTAINABLE, build_record


# --- the engine asymmetry, stated in both directions ------------------------


def test_chromium_declares_the_machine_it_was_asked_for():
    assert bt.declared_machine_for(bt.CHROMIUM, "macos") == "macos"
    assert bt.declared_machine_for(bt.CHROMIUM, "linux") == "linux"
    assert bt.honours_declared_machine(bt.CHROMIUM) is True


def test_firefox_declares_windows_however_it_was_asked():
    """Not a default — an inability.

    ``InvisiblePlaywright.__init__`` takes no OS/platform argument at all, so
    there is nothing to pass and the engine presents Windows regardless (the
    behaviour the product records as #211). Recording the REQUEST here would
    put a machine in the header that the engine never declared.
    """
    for requested in ("macos", "linux", "windows", ""):
        assert bt.declared_machine_for(bt.FIREFOX, requested) == "windows"
    assert bt.honours_declared_machine(bt.FIREFOX) is False


def test_the_firefox_constructor_really_has_no_os_parameter():
    """The claim the whole asymmetry rests on, checked against the installed
    engine rather than trusted.

    If a future engine build grows an OS parameter, this fails and tells the
    next reader to widen the Firefox path instead of leaving a capability
    unused behind a comment that has silently gone stale.
    """
    inv = pytest.importorskip(
        "invisible_playwright",
        reason="persona's firefox engine is not installed here",
    )
    import inspect

    params = set(inspect.signature(inv.InvisiblePlaywright.__init__).parameters)
    assert not params & {"os", "os_type", "platform", "fingerprint_platform"}


# --- the record header ------------------------------------------------------


def _record(**kwargs):
    return build_record(
        [],
        exit_=Exit(ip="1.2.3.4", country="PL"),
        observed_at="2026-08-22T10:00:00Z",
        **kwargs,
    )


def test_the_record_header_carries_the_declared_machine_beside_the_seed():
    """The same argument that put the seed in the header.

    The declared OS constrains GPU strings, voices, fonts, screen conventions,
    platform flags and the user agent, so two records taken on different
    machines differ for a reason that is not a coupling. Without it in the
    header that distinction cannot be drawn at all.
    """
    record = _record(engine="fingerprint-chromium/148", seed=4242,
                     declared_machine="macos", declared_machine_honoured=True)
    assert record["declared_machine"] == "macos"
    assert record["declared_machine_honoured"] is True
    assert record["seed"] == 4242


def test_a_firefox_record_states_the_machine_was_not_honoured():
    """The half that keeps the header honest: the field says what was
    PRESENTED, and a second field says asking changed nothing."""
    record = _record(
        engine="invisible_playwright/firefox-20",
        seed=4242,
        declared_machine=bt.declared_machine_for(bt.FIREFOX, "macos"),
        declared_machine_honoured=bt.honours_declared_machine(bt.FIREFOX),
    )
    assert record["declared_machine"] == "windows"
    assert record["declared_machine_honoured"] is False


# --- one command surface, both engines --------------------------------------


def test_both_is_both_engines_and_a_list_may_be_repeated_or_comma_separated():
    assert cli._resolve_engines(["both"]) == list(bt.ENGINES)
    assert cli._resolve_engines(["firefox,chromium"]) == ["firefox", "chromium"]
    assert cli._resolve_engines(["firefox", "chromium"]) == [
        "firefox", "chromium"
    ]


def test_a_duplicated_configuration_is_run_once():
    """Two identical configurations produce two records differing only by
    timestamp — nothing for a comparator, and a full browser run each."""
    assert cli._resolve_engines(["both", "chromium"]) == list(bt.ENGINES)
    assert cli._resolve_seeds(["7", "7", "9"]) == [7, 9]
    assert cli._resolve_machines(["macos,macos"]) == ["macos"]


def test_an_unknown_engine_or_machine_is_refused_rather_than_defaulted():
    with pytest.raises(SystemExit):
        cli._resolve_engines(["webkit"])
    with pytest.raises(SystemExit):
        cli._resolve_machines(["freebsd"])
    with pytest.raises(SystemExit):
        cli._resolve_seeds(["not-a-number"])


def test_the_plan_is_the_cross_product_of_engines_machines_and_seeds():
    plan, _notes = cli._plan(["chromium"], ["windows", "macos"], [1, 2])
    assert plan == [
        ("chromium", "windows", 1),
        ("chromium", "windows", 2),
        ("chromium", "macos", 1),
        ("chromium", "macos", 2),
    ]


def test_more_than_one_seed_per_configuration_is_expressible():
    """Level 2 of the bar, observable from outside for the first time: two
    profiles differing ONLY by seed should read as two different machines."""
    plan, _ = cli._plan(["chromium"], ["windows"], [4242, 1337])
    assert [seed for _e, _m, seed in plan] == [4242, 1337]
    assert len({(e, m) for e, m, _s in plan}) == 1


def test_asking_firefox_for_several_machines_collapses_and_says_so():
    """A silently-dropped request is indistinguishable from one never made.

    Running Firefox once per machine would spend a full browser run producing
    records identical but for a header field the engine never honoured.
    """
    plan, notes = cli._plan(["firefox"], ["windows", "macos", "linux"], [1])
    assert plan == [("firefox", "windows", 1)]
    assert notes and "collapsed" in notes[0]
    assert "macos" in notes[0]


def test_the_collapse_does_not_touch_chromium():
    plan, notes = cli._plan(["chromium"], ["windows", "macos"], [1])
    assert len(plan) == 2
    assert notes == []


def test_a_multi_configuration_run_names_each_record_by_its_configuration():
    """The record's own filename carries the three variables, so a directory of
    readings is readable without opening every header."""
    name = cli._record_name("chromium", "macos", 4242)
    assert "chromium" in name and "macos" in name and "4242" in name
    assert name.endswith(".json")
    assert name != cli._record_name("firefox", "macos", 4242)
    assert name != cli._record_name("chromium", "windows", 4242)
    assert name != cli._record_name("chromium", "macos", 1337)


def test_a_firefox_run_carries_a_note_explaining_the_machine_it_declared():
    notes = cli._notes_for(bt.FIREFOX, "macos")
    assert any("macos" in n and "#211" in n for n in notes)


def test_a_chromium_run_records_that_the_relay_carried_the_credential():
    notes = cli._notes_for(bt.CHROMIUM, "windows")
    assert any("bridge" in n.lower() or "relay" in n.lower() for n in notes)


# --- the engines share one reading path -------------------------------------


class _FakePage:
    def __init__(self, session, text):
        self._session = session
        self._text = text
        self.closed = False

    def goto(self, url, timeout=0, wait_until="load"):
        self._session.visited.append(url)
        if url in self._session.fail_urls:
            raise RuntimeError("refused")

    def inner_text(self, selector):
        return self._text

    def close(self):
        self.closed = True


class _FakeSession:
    """The whole contract both engines satisfy: ``new_page()`` and a page with
    ``goto`` / ``inner_text`` / ``close``."""

    def __init__(self, text, fail_urls=()):
        self.text = text
        self.visited = []
        self.pages = []
        self.fail_urls = set(fail_urls)

    def new_page(self):
        page = _FakePage(self, self.text)
        self.pages.append(page)
        return page


_EXIT_JSON = (
    '{"ip": "178.42.89.161", "city": "Warsaw", "country": "PL", '
    '"org": "AS5617 Orange Polska", "timezone": "Europe/Warsaw"}'
)


def test_both_engines_reach_the_same_loop():
    """The loop is engine-agnostic by construction.

    It is driven here through the same object shape both real sessions expose,
    which is what stops one engine acquiring a reading path the other lacks.
    """
    session = _FakeSession(_EXIT_JSON)
    pages = bt._read_open_session(
        session, checkers=BROWSER_CHECKERS, sleep=lambda _s: None
    )
    assert set(pages) == {c.id for c in BROWSER_CHECKERS}
    assert all(p.closed for p in session.pages)


def test_the_exit_checker_is_asked_exactly_once_whichever_engine_runs():
    """Asking twice would record a second, later address for one run and make
    the record self-contradicting on a rotating exit."""
    session = _FakeSession(_EXIT_JSON)
    bt._read_open_session(
        session, checkers=BROWSER_CHECKERS, sleep=lambda _s: None
    )
    assert session.visited.count(ENGINE_EXIT_CHECKER.url) == 1


def test_one_checker_refusing_does_not_take_the_others_down():
    target = next(c for c in BROWSER_CHECKERS if c.id != ENGINE_EXIT_CHECKER.id)
    session = _FakeSession(_EXIT_JSON, fail_urls=[target.url])
    pages = bt._read_open_session(
        session, checkers=BROWSER_CHECKERS, sleep=lambda _s: None
    )
    assert "error" in pages[target.id]
    assert any("text" in v for k, v in pages.items() if k != target.id)


def test_an_unprovable_exit_makes_every_row_unobtainable_not_absent():
    """The engine was fine; the exit could not be shown. Nothing about the
    identity may be inferred, so no row may read as a clean page."""
    session = _FakeSession("this page carries no country at all")
    pages = bt._read_open_session(
        session, checkers=BROWSER_CHECKERS, sleep=lambda _s: None
    )
    assert set(pages) == {c.id for c in BROWSER_CHECKERS}
    assert all("error" in v for v in pages.values())

    readings = bt.readings_from_texts(pages, checkers=BROWSER_CHECKERS)
    assert readings and all(r.state == UNOBTAINABLE for r in readings)


# --- refusals keep the record's full width ----------------------------------


def test_an_unknown_engine_is_refused_before_a_browser_is_launched():
    with pytest.raises(bt.EngineUnavailable):
        bt.read_page_texts("socks5h://u:p@host:1080", engine="webkit")


def test_an_engine_that_cannot_run_yields_full_width_unobtainable_rows(
    monkeypatch,
):
    """"We could not run a browser here" is itself a result.

    A narrower record on exactly the runs where something went wrong is the
    failure this whole subsystem exists to prevent — so the tier keeps every
    catalogued row and puts the reason on each.
    """
    def _boom(*_a, **_k):
        raise bt.EngineUnavailable("persona's chromium is not installed here")

    monkeypatch.setattr(bt, "read_page_texts", _boom)
    readings = bt.read_browser_tier(
        "socks5h://u:p@host:1080", engine=bt.CHROMIUM
    )
    expected = sum(len(c.items) for c in BROWSER_CHECKERS)
    assert len(readings) == expected
    assert all(r.state == UNOBTAINABLE for r in readings)
    assert all("not installed" in r.reason for r in readings)


def test_the_engine_choice_reaches_the_reader(monkeypatch):
    seen = {}

    def _spy(proxy_url, **kwargs):
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(bt, "read_page_texts", _spy)
    bt.read_browser_tier(
        "socks5h://u:p@host:1080",
        engine=bt.CHROMIUM,
        declared_machine="macos",
        seed=4242,
    )
    assert seen["engine"] == bt.CHROMIUM
    assert seen["declared_machine"] == "macos"
    assert seen["seed"] == 4242


# --- the chromium launch ----------------------------------------------------


def _args(**kwargs):
    from src.services.verify import chromium_tier

    defaults = dict(
        seed=4242,
        declared_machine="macos",
        proxy_server="socks5://127.0.0.1:5555",
    )
    defaults.update(kwargs)
    return chromium_tier._launch_args("/engine/fpchrome", "/tmp/p", **defaults)


def test_the_declared_machine_is_what_chromium_is_actually_told():
    assert "--fingerprint-platform=macos" in _args()
    assert "--fingerprint=4242" in _args()


def test_the_credential_never_reaches_the_chromium_command_line():
    """Chromium cannot carry SOCKS5 credentials, and it must not be handed
    them anyway: a command line is readable by every process on the box."""
    args = _args(proxy_server="socks5://127.0.0.1:5555")
    joined = " ".join(args)
    assert "--proxy-server=socks5://127.0.0.1:5555" in joined
    assert "hunter2" not in joined and "@" not in joined


def test_the_anti_leak_switches_are_mirrored_from_the_products_own_launch():
    """A reading taken through an engine that resolves or streams past its
    proxy describes the operator's real address while looking perfect — and
    unlike the product, this tier has no window for a human to notice."""
    joined = " ".join(_args())
    assert "--dns-over-https-mode=off" in joined
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in joined
    assert "--disable-quic" in joined
    assert "--dns-prefetch-disable" in joined
    assert "DnsOverHttps" in joined and "EnableQuic" in joined


def test_the_debug_port_is_ephemeral_rather_than_guessable():
    """The CDP channel is unauthenticated, so a fixed or name-derived port is
    drivable by any co-resident process."""
    assert "--remote-debugging-port=0" in _args()


def test_headless_is_never_passed():
    """persona ships a HEADED browser under a display; --headless presents a
    different surface to precisely the checkers this tier reads."""
    assert not any("--headless" in a for a in _args())


def test_a_missing_chromium_refuses_and_never_falls_back_to_one_on_path(
    monkeypatch, tmp_path
):
    """Stock chromium exists on most machines and IS NOT THE PRODUCT.

    A fallback would launch happily and produce a complete-looking record of
    something persona does not ship.
    """
    from src.services.verify import chromium_tier

    monkeypatch.setattr(
        "src.core.config.ENGINE_DIR", str(tmp_path), raising=False
    )
    with pytest.raises(chromium_tier.ChromiumUnavailable) as exc:
        chromium_tier._engine_binary()
    assert "PATH" in str(exc.value)


def test_the_sandbox_is_never_waived_by_default():
    """persona's own launch path passes --no-sandbox NOWHERE.

    A verification tier that added it silently would run the engine with a
    security boundary the product keeps, and then record the result as though
    it were the product's behaviour.
    """
    assert "--no-sandbox" not in _args()
    assert "--no-sandbox" in _args(allow_unsandboxed=True)


def test_chromium_is_told_the_exits_timezone(): 
    """The defect PS-132 exists to fix.

    The product pins a concrete zone on every launch
    (``process.py``: ``--timezone={_profile_timezone(profile, proxy)}``).
    This tier pinned NONE, so the engine fell back to the HOST clock: a
    reading taken behind a proven Warsaw exit reported the container's own
    UTC+0, and the checker's free timezone-against-address cross-check
    called the product spoofed for it.
    """
    assert "--timezone=Europe/Warsaw" in _args(timezone="Europe/Warsaw")


def test_a_venue_with_no_exit_is_told_no_timezone_rather_than_a_made_up_one():
    """Empty means pass no flag, and that is not the same as a default.

    The loopback differential serves its page from 127.0.0.1 and has no exit
    for a zone to agree with, so a zone asserted there would be a fact about
    nothing. The product makes the same distinction one axis over — it
    refuses rather than inventing geography for a proxy it cannot place.
    """
    assert not any(a.startswith("--timezone=") for a in _args())
    assert not any(a.startswith("--timezone=") for a in _args(timezone=""))


def test_the_zone_the_session_launches_with_is_the_one_it_was_given(
    monkeypatch,
):
    """The flag is only as good as the value that reaches it.

    ``_launch_args`` is asserted directly above, so this pins the OTHER half:
    that ``ChromiumSession`` carries the caller's zone down to the command
    line instead of dropping it between the two. A test of the formatter
    alone would pass with the parameter never threaded at all — which is the
    shape of the original defect, not a hypothetical.
    """
    from src.services.verify import chromium_tier

    seen = {}

    def _spy(binary, profile_dir, **kwargs):
        seen.update(kwargs)
        raise chromium_tier.ChromiumUnavailable("stop here: argv captured")

    monkeypatch.setattr(chromium_tier, "_engine_binary", lambda: "/engine/fp")
    monkeypatch.setattr(chromium_tier, "sandbox_available", lambda: True)
    monkeypatch.setattr(
        chromium_tier, "_ensure_display", lambda: (":99", None)
    )
    monkeypatch.setattr(
        chromium_tier,
        "_proxy_server_and_bridge",
        lambda url, allow_no_proxy=False: ("socks5://127.0.0.1:5555", None),
    )
    monkeypatch.setattr(chromium_tier, "_launch_args", _spy)

    with pytest.raises(chromium_tier.ChromiumUnavailable):
        chromium_tier.ChromiumSession(
            "socks5h://u:p@host:1080",
            timezone="Europe/Warsaw",
            install_layer=False,
        )
    assert seen["timezone"] == "Europe/Warsaw"


def test_the_browser_tier_hands_chromium_the_exits_zone_and_never_firefox(
    monkeypatch,
):
    """Chromium is raised to Firefox here, not the other way round.

    Firefox reads the zone correctly ALREADY: given none, its engine resolves
    geo from the egress IP itself (``invisible_launch.py``: "with no timezone
    it discovers the egress IP"). Chromium has no such fallback, which is why
    only one of the two engines ever showed this. Passing the zone to Firefox
    would be shared machinery that drags the working side onto the broken
    side's crutch, so the parameter must reach chromium and stop there.
    """
    from src.services.verify import browser_tier as bt

    seen = {}

    def _chromium(proxy_url, **kwargs):
        seen.update(kwargs)
        return {}

    def _firefox(proxy_url, **kwargs):
        seen.update(kwargs)
        return {}

    monkeypatch.setattr(bt, "_read_page_texts_chromium", _chromium)
    monkeypatch.setattr(bt, "_read_page_texts_firefox", _firefox)

    bt.read_page_texts(
        "socks5h://u:p@host:1080",
        engine=bt.CHROMIUM,
        timezone="Europe/Warsaw",
    )
    assert seen["timezone"] == "Europe/Warsaw"

    seen.clear()
    bt.read_page_texts(
        "socks5h://u:p@host:1080",
        engine=bt.FIREFOX,
        timezone="Europe/Warsaw",
    )
    assert "timezone" not in seen, (
        "firefox must not be handed the zone: it resolves geo from the egress "
        "IP itself, and this ticket raises chromium to firefox rather than "
        "adding shared machinery"
    )


def test_a_host_without_the_sandbox_refuses_rather_than_dying_obscurely(
    monkeypatch,
):
    """Measured: chromium dies rc=133 'No usable sandbox!' before opening a
    debug port, which reads as "the browser is broken" rather than as "this
    host forbids the sandbox". The refusal names the real cause and the waiver.
    """
    from src.services.verify import chromium_tier

    monkeypatch.setattr(chromium_tier, "sandbox_available", lambda: False)
    monkeypatch.setattr(
        chromium_tier, "_engine_binary", lambda: "/engine/fpchrome"
    )

    def _never(*_a, **_k):  # the refusal must cost no browser launch
        raise AssertionError("a display was started before the refusal")

    monkeypatch.setattr(chromium_tier, "_ensure_display", _never)

    with pytest.raises(chromium_tier.SandboxUnavailable) as exc:
        chromium_tier.ChromiumSession("socks5h://u:p@host:1080")
    assert "--no-sandbox" in str(exc.value)


def _record_from_read(
    monkeypatch,
    argv: "list[str]",
    engine: str = bt.CHROMIUM,
    browser_stub=None,
) -> dict:
    """Drive ``_read_one`` through the REAL parser, with the tiers stubbed.

    The point of going through ``build_parser`` rather than constructing a
    Namespace is that the operator types a FLAG, and the defect this file
    guards was a flag that parsed correctly and then reached nothing: a
    hand-built Namespace (or a hand-passed kwarg) asserts the helper's
    ability rather than the shipped path's behaviour, and stays green while
    the record discloses nothing.

    Nothing here touches the network, an engine, or the exit: the exit is
    proven by a stub, so this asserts the RECORD ONLY and no part of it can
    be mistaken for a reading.
    """
    args = cli.build_parser().parse_args(argv)

    monkeypatch.setattr(
        cli,
        "prove_exit",
        lambda **_k: (
            "socks5h://u:p@host:1080",
            Exit(ip="192.0.2.1", country="PL", city="Warsaw", org="stub"),
        ),
    )
    monkeypatch.setattr(cli, "read_json_tier", lambda *_a, **_k: [])
    monkeypatch.setattr(cli, "read_unreadable_tier", lambda *_a, **_k: [])
    # Deferred import inside _read_one, so it resolves off the module. A caller
    # that wants to inspect what the tier was HANDED passes its own stub here:
    # patching it outside this helper would be clobbered by this line.
    monkeypatch.setattr(
        bt,
        "read_browser_tier",
        browser_stub if browser_stub is not None else lambda *_a, **_k: [],
    )

    record = cli._read_one(
        args, engine, "windows", seed=4242
    )
    assert record is not None
    return record


def test_the_waiver_is_recorded_in_the_reading_it_produced(monkeypatch):
    """An unsandboxed reading is not the product's surface, so a record taken
    that way must say so rather than looking like every other record.

    Asserted on the RECORD, from the operator's actual command, because the
    disclosure is a promise made by three shipped surfaces
    (``SandboxUnavailable``, the flag's help text, and the note itself) and
    ALL of them are kept by the record carrying the note — not by
    ``_notes_for`` being capable of emitting it. This is also the host where
    the waiver is REQUIRED, so the deferred through-the-exit reading is taken
    on this path: if it went unrecorded, that record would be
    indistinguishable from a sandboxed one and PS-67 would compare the
    difference as a product change.
    """
    record = _record_from_read(
        monkeypatch,
        [
            "read", "--engine", "chromium", "--declared-machine", "windows",
            "--seed", "4242", "--allow-unsandboxed-chromium",
        ],
    )
    assert any("--no-sandbox" in n for n in record["notes"])


def test_a_sandboxed_reading_does_not_carry_the_waiver_note(monkeypatch):
    """The other direction, so the assertion above cannot be satisfied by a
    note that is simply always present — which would make every sandboxed
    record falsely confess a waived sandbox."""
    record = _record_from_read(
        monkeypatch,
        [
            "read", "--engine", "chromium", "--declared-machine", "windows",
            "--seed", "4242",
        ],
    )
    assert not any("--no-sandbox" in n for n in record["notes"])


def test_a_credentialled_upstream_gets_persona_s_hardened_relay(monkeypatch):
    """Not a second listener beside the hardened one — THE hardened one.

    PS-25 hardened ``ProxyBridge``'s local access control; a weaker relay
    written here would hand any co-resident process free authenticated egress
    through the operator's paid exit.
    """
    from src.services.verify import chromium_tier

    started = {}

    class _FakeBridge:
        def __init__(self, upstream):
            started["upstream"] = upstream

        def start(self):
            return 5555

    monkeypatch.setattr(
        "src.services.proxy.bridge.ProxyBridge", _FakeBridge
    )
    server, bridge = chromium_tier._proxy_server_and_bridge(
        "socks5h://user:hunter2@exit.example:1080"
    )
    assert server == "socks5://127.0.0.1:5555"
    assert bridge is not None
    assert started["upstream"] == "socks5h://user:hunter2@exit.example:1080"


def test_an_uncredentialled_upstream_needs_no_relay(monkeypatch):
    from src.services.verify import chromium_tier

    server, bridge = chromium_tier._proxy_server_and_bridge(
        "socks5h://exit.example:1080"
    )
    assert server == "socks5://exit.example:1080"
    assert bridge is None


def test_socks5h_is_normalised_for_chromium_which_rejects_it():
    """Chromium's SOCKS5 client already resolves at the proxy; ``socks5h`` is a
    curl-ism it rejects with ERR_NO_SUPPORTED_PROXIES. The remote-DNS property
    is preserved, not dropped."""
    from src.services.verify import chromium_tier

    server, _ = chromium_tier._proxy_server_and_bridge(
        "socks5h://exit.example:1080"
    )
    assert server.startswith("socks5://")
    assert "socks5h" not in server


# --- the subtraction flags, and the engine that cannot honour them ----------
#
# `--layer-vectors` / `--drop-layer-vector` narrow the masking layer to a
# SUBSET so a subtraction arm can name which spoof a checker reacts to. Only
# the firefox layer can be narrowed: `build_chromium_layer` takes no vectors
# parameter and assembles the full extension set, and `read_browser_tier` does
# not forward the subset down the chromium branch.
#
# The defect these tests pin is not "the flag did nothing". It is that the flag
# did nothing WHILE THE RECORD SAID IT HAD: `_notes_for` stamped
# "REMOVED: locale" on a reading taken with locale installed. An adverse row on
# such an arm exonerates the very vector the operator meant to remove — the one
# inference the subtraction method exists to make safely, inverted silently.
#
# Asserted through the REAL parser and on the RECORD, for the reason the
# helper's own docstring gives: the shipped path is what can be wrong.


def test_a_subtraction_arm_is_refused_on_chromium(monkeypatch):
    """The blocking case: a CORRECTLY-SPELLED vector on the engine that cannot
    remove it.

    An unknown NAME was already refused; this is the same false exoneration
    reached by spelling the name right, which is strictly the more likely
    operator error and was the one left open.
    """
    with pytest.raises(SystemExit) as exc:
        _record_from_read(
            monkeypatch,
            [
                "read", "--engine", "chromium", "--seed", "4242",
                "--drop-layer-vector", "locale",
            ],
            engine=bt.CHROMIUM,
        )
    message = str(exc.value)
    assert "chromium" in message
    assert bt.FIREFOX in message


def test_the_keep_spelling_is_refused_on_chromium_too(monkeypatch):
    """Both spellings name a subset, so both must be refused — a guard that
    caught only ``--drop-layer-vector`` would leave the identical falsified
    record reachable by the other flag."""
    with pytest.raises(SystemExit):
        _record_from_read(
            monkeypatch,
            [
                "read", "--engine", "chromium", "--seed", "4242",
                "--layer-vectors", "webgl",
            ],
            engine=bt.CHROMIUM,
        )


def test_chromium_still_reads_when_no_subset_is_named(monkeypatch):
    """Guard the guard, in the direction that matters: the refusal must be
    about the SUBSET FLAGS, not about chromium.

    Without this, a guard that refused every chromium reading outright would
    satisfy the tests above while removing the engine from the matrix.
    """
    record = _record_from_read(
        monkeypatch,
        ["read", "--engine", "chromium", "--seed", "4242"],
        engine=bt.CHROMIUM,
    )
    assert not any("DELIBERATELY INCOMPLETE" in n for n in record["notes"])


def test_a_subtraction_arm_is_honoured_on_firefox_and_the_record_says_so(
    monkeypatch,
):
    """The other half of the guard-the-guard: the capability still WORKS where
    it is real, and the record discloses the arm.

    Paired with the chromium refusals this pins the actual rule — subsets are a
    firefox capability — rather than "subsets are refused", which a blanket
    refusal would also satisfy while quietly deleting the method PS-119 used to
    find its root cause.
    """
    record = _record_from_read(
        monkeypatch,
        [
            "read", "--engine", "firefox", "--seed", "4242",
            "--drop-layer-vector", "locale",
        ],
        engine=bt.FIREFOX,
    )
    note = next(n for n in record["notes"] if "DELIBERATELY INCOMPLETE" in n)
    assert "REMOVED: locale" in note
    assert "Installed: webgl, audio" in note


def test_the_subset_actually_reaches_the_engine_on_firefox(monkeypatch):
    """The record's claim and the arm that ran must be the SAME subset.

    The chromium defect was exactly this disagreement — a note asserting a
    removal the reader never performed — so the note alone is not evidence.
    This captures the ``layer_vectors`` handed to ``read_browser_tier`` and
    asserts it matches what the note claims.
    """
    seen = {}

    def _capture(*_a, **kw):
        seen.update(kw)
        return []

    record = _record_from_read(
        monkeypatch,
        [
            "read", "--engine", "firefox", "--seed", "4242",
            "--drop-layer-vector", "locale",
        ],
        engine=bt.FIREFOX,
        browser_stub=_capture,
    )
    assert seen["layer_vectors"] == ("webgl", "audio")
    assert "locale" not in seen["layer_vectors"]
    note = next(n for n in record["notes"] if "DELIBERATELY INCOMPLETE" in n)
    assert "REMOVED: locale" in note


def test_a_mixed_engine_run_is_refused_before_any_browser_starts(
    monkeypatch, tmp_path
):
    """``--engine both`` with a subset must be refused up front, not on the
    chromium leg after a full firefox reading has been taken.

    A subtraction arm is only meaningful beside its pair, so a run that
    produced the firefox half and then died would have spent a live browser run
    and a proven exit to produce half a comparison.

    ``--output`` is a directory here deliberately. Without it this test PASSED
    with the refusal removed, satisfied by the unrelated multi-configuration
    ``--output '-'`` rule — a false green caught by re-running it against a
    reverted fix. The message is asserted for the same reason: "some SystemExit
    was raised" is not evidence that THIS refusal fired.
    """
    def _never(*_a, **_k):
        raise AssertionError("a browser tier ran before the refusal")

    monkeypatch.setattr(bt, "read_browser_tier", _never)
    monkeypatch.setattr(cli, "read_json_tier", lambda *_a, **_k: [])
    monkeypatch.setattr(cli, "read_unreadable_tier", lambda *_a, **_k: [])
    monkeypatch.setattr(
        cli,
        "prove_exit",
        lambda **_k: (
            "socks5h://u:p@host:1080",
            Exit(ip="192.0.2.1", country="PL", city="Warsaw", org="stub"),
        ),
    )

    args = cli.build_parser().parse_args(
        [
            "read", "--engine", "both", "--seed", "4242",
            "--drop-layer-vector", "locale",
            "--output", str(tmp_path),
        ]
    )
    with pytest.raises(SystemExit) as exc:
        cli._cmd_read(args)
    assert "--drop-layer-vector" in str(exc.value)
    assert "chromium" in str(exc.value)


def test_the_incomplete_layer_note_names_both_spellings():
    """The note is the operator's record of what they typed. Naming only
    ``--layer-vectors`` on a run produced by ``--drop-layer-vector`` reads as
    though a different flag was passed than the one that produced it."""
    notes = cli._notes_for(
        bt.FIREFOX, "windows", install_layer=True, layer_vectors=("webgl",)
    )
    note = next(n for n in notes if "DELIBERATELY INCOMPLETE" in n)
    assert "--layer-vectors" in note
    assert "--drop-layer-vector" in note
