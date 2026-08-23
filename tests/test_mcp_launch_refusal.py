"""PS-82: the MCP launch tool must report a REFUSED launch, not `launched: true`.

The launcher already computes this verdict — ``start_thread`` catches a
fail-closed guard's exception, classifies it, records it under the profile name,
and returns the same ``None`` a successful launch returns. Before this slice this
tool composed ``{"launched": True}`` over that ``None``: the profile never
opened, the guard fired, and the connected LLM client was told it worked, with no
follow-up call able to recover the reason. That audience is the worst one for the
misreading — most likely to retry in a loop, least able to see the server-side
log the sentence went to.

Every assertion below binds to the REPORTED VERDICT (the tool's own payload),
never to "the tool is able to call the accessor" — an assertion that a mechanism
EXISTS passes against an implementation that does not work (PS-11). Delete the
``last_refusal`` read from ``mcp_server.py`` and these go red on the response.
"""

import asyncio
import json
import time

from src.api.mcp_server import build_mcp
from src.core.container import Container
from src.models.profile import Profile
from src.services.browser.refusal import classify_refusal
from src.services.proxy.errors import (
    GeographyDisprovenError,
    GeographyUnknownError,
    ProxyUnresolvedError,
)


class FakeLauncher:
    """Reproduces the launcher's contract for a refused launch: swallow the
    guard's exception, classify it through the SHIPPED ``classify_refusal``,
    record it, and return ``None`` — indistinguishable, to the caller, from a
    launch that worked. That indistinguishability IS the defect.
    """

    def __init__(self):
        self.launched = []
        self._running = set()
        self.refuse_next_with = None
        self._last_refusal = {}
        # None = real clock, matching the launcher's own time.time() stamp. A
        # frozen default would make every refusal look stale to the staleness
        # check and mask the behaviour under test.
        self.now = None
        # Names a CONCURRENT launch reserved after this call's is_running check
        # but before start_thread took the lock. The real launcher keeps those
        # in _starting, and its duplicate-return branch tests
        # `_active_sessions or _starting` (launcher.py) — so a name can be
        # absent from is_running() at the tool's check and still be a duplicate
        # by the time start_thread runs. Kept OUT of _running deliberately:
        # that gap is the only way to reach the launcher-level duplicate
        # return, and it is the case AC4(a) is actually about.
        self.claimed_by_racer = set()

    def is_running(self, name):
        return name in self._running

    def start_thread(self, profile, log_callback=None, **kwargs):
        if profile.name in self._running or profile.name in self.claimed_by_racer:
            # The real launcher returns HERE, before the pop below: a duplicate
            # is not an attempt and must not erase the verdict from the attempt
            # that did run (launcher.py). `claimed_by_racer` is the arm the
            # tool's is_running check does NOT see, which is what makes the
            # launcher-level duplicate reachable.
            return
        self._last_refusal.pop(profile.name, None)
        exc = self.refuse_next_with
        if exc is not None:
            self.refuse_next_with = None
            at = time.time() if self.now is None else self.now
            refusal = classify_refusal(exc, at)
            if refusal is not None:
                self._last_refusal[profile.name] = refusal
            return
        self.launched.append(profile)
        self._running.add(profile.name)

    def last_refusal(self, name):
        return self._last_refusal.get(name)

    def stop_profile(self, name, timeout=2):
        self._running.discard(name)
        return True


class FakePM:
    def __init__(self, names):
        self.profiles = {n: Profile(name=n) for n in names}

    def list_profiles(self):
        return list(self.profiles.values())


def _harness():
    c = Container()
    pm = FakePM(["acct-7"])
    bl = FakeLauncher()
    c._instances["pm"] = pm
    c._instances["bl"] = bl
    return build_mcp(c), bl


def _launch(mcp, name="acct-7"):
    result = asyncio.run(mcp.call_tool("launch_profile", {"name": name}))
    # The tool's dict, as the client actually receives it.
    return json.loads(result[0].text)


def test_launch_refused_by_unresolved_proxy_is_not_reported_as_launched():
    # AC1/AC3. On origin/main this answers {"launched": true, "name": "acct-7"}
    # for a profile that never opened.
    mcp, bl = _harness()
    bl.refuse_next_with = ProxyUnresolvedError(
        "Profile 'acct-7' has proxy 'home' assigned but it could not be "
        "resolved (deleted/renamed?). Refusing to launch DIRECT."
    )

    body = _launch(mcp)

    assert body["launched"] is False, "a refused launch must not report success"
    assert body["kind"] == "proxy_unresolved"
    assert "Refusing to launch DIRECT" in body["detail"]
    # Nothing started. Asserting only the flag would pass even if it had.
    assert bl.launched == []
    assert bl.is_running("acct-7") is False


def test_geography_unknown_and_disproven_are_distinguishable_to_a_caller():
    # AC2. GeographyDisprovenError is a SUBCLASS of GeographyUnknownError, so a
    # lane that collapses them tells a caller the proxy was "never checked" when
    # it WAS checked and the check FAILED — sending it to re-run a check that
    # already ran. Assert on `kind`, never on prose.
    mcp, bl = _harness()

    bl.refuse_next_with = GeographyUnknownError(
        "Profile 'acct-7' has proxy 'home' assigned but its geography could not "
        "be established (the proxy has never been checked successfully)."
    )
    unknown = _launch(mcp)

    bl.refuse_next_with = GeographyDisprovenError(
        "Profile 'acct-7' has proxy 'home' assigned, but that proxy's LAST "
        "CHECK FAILED — the geography still on file is disproven."
    )
    disproven = _launch(mcp)

    assert unknown["launched"] is False
    assert disproven["launched"] is False
    assert unknown["kind"] == "geography_unknown"
    assert disproven["kind"] == "geography_disproven"
    assert unknown["kind"] != disproven["kind"], (
        "the two causes have different remedies and must not collapse"
    )


def test_duplicate_launch_does_not_re_report_an_earlier_refusal():
    # AC4(a) — THE TRAP. _last_refusal is keyed by profile name and dropped at
    # the START of an attempt, but a duplicate-launch call returns BEFORE that
    # drop, deliberately. A lane reading the dict unconditionally would hand
    # that older refusal to this caller as its own verdict.
    mcp, bl = _harness()
    bl.refuse_next_with = ProxyUnresolvedError("first attempt refused")
    first = _launch(mcp)
    assert first["launched"] is False, "precondition: the first attempt was refused"

    bl._running.add("acct-7")
    assert bl.last_refusal("acct-7") is not None, (
        "precondition: the earlier verdict is still on record"
    )

    second = _launch(mcp)

    assert second["launched"] is False
    # Refused as a DUPLICATE, not as the earlier proxy failure.
    assert second["error"] == "already running", (
        "the second call re-reported a refusal that belonged to the first attempt"
    )
    assert "kind" not in second


def test_launcher_level_duplicate_does_not_re_report_an_earlier_refusal():
    # AC4(a), THE CASE THE TEST ABOVE CANNOT REACH — and the one the staleness
    # rule exists for. The test above puts the profile in _running, so the
    # TOOL's own is_running check answers {"error": "already running"} before
    # attempt_at is ever stamped and before the last_refusal read runs at all.
    # Its `"kind" not in second` is trivially true of a payload the new code
    # never touched: it passes identically with refusal_report.py deleted from
    # the repo, so it binds nothing.
    #
    # The duplicate return the ticket points at (launcher.py, placed BEFORE the
    # verdict pop on purpose) is only reachable when is_running() was False at
    # the tool's check and the attempt is a duplicate by the time start_thread
    # takes the lock — the concurrent case, where another launch reserved the
    # slot in between. Then, and only then, does the tool stamp attempt_at, call
    # through, get the same None a success returns, and read a dict that still
    # holds the EARLIER attempt's verdict.
    #
    # Delete the staleness check and this goes red: the second caller is told
    # its launch was refused for a proxy failure that was someone else's.
    mcp, bl = _harness()
    bl.refuse_next_with = ProxyUnresolvedError("first attempt refused")
    first = _launch(mcp)
    assert first["launched"] is False, "precondition: the first attempt was refused"
    assert first["kind"] == "proxy_unresolved"

    # A concurrent launch reserves the slot AFTER this call's is_running check
    # and BEFORE its start_thread — invisible to the tool's own guard.
    bl.claimed_by_racer.add("acct-7")
    assert bl.is_running("acct-7") is False, (
        "precondition: the tool's own is_running check must NOT fire, or this "
        "test re-tests a guard already on main instead of the staleness rule"
    )
    assert bl.last_refusal("acct-7") is not None, (
        "precondition: the earlier attempt's verdict is still on record"
    )

    second = _launch(mcp)

    # A launcher-level duplicate is not a refusal of THIS call, so this lane
    # answers exactly what it answered before the read existed.
    assert second["launched"] is True, (
        "a launcher-level duplicate re-reported an earlier attempt's refusal "
        "as this call's own verdict"
    )
    # The decisive assertion: the stale verdict never reached this caller.
    assert "kind" not in second, (
        "the first attempt's refusal kind leaked into a later call's response"
    )
    assert "detail" not in second
    # And the verdict is still on record — a duplicate must not erase it.
    assert bl.last_refusal("acct-7") is not None


def test_a_successful_launch_after_a_refused_one_reports_success():
    # AC4(b). Once a real attempt succeeds, the stale verdict must not leak.
    mcp, bl = _harness()
    bl.refuse_next_with = ProxyUnresolvedError("refused once")
    refused = _launch(mcp)
    assert refused["launched"] is False, "precondition: the first attempt was refused"

    ok = _launch(mcp)

    assert ok["launched"] is True
    assert ok["name"] == "acct-7"
    assert bl.last_refusal("acct-7") is None
    assert [p.name for p in bl.launched] == ["acct-7"]


def test_ordinary_spawn_failure_is_not_reported_as_a_refusal():
    # AC6. classify_refusal returns None for anything outside the three guard
    # classes, so the payload must not change. Routine noise stays quiet enough
    # that a refusal reads as loud.
    mcp, bl = _harness()
    bl.refuse_next_with = RuntimeError("engine binary exploded")

    body = _launch(mcp)

    assert body["launched"] is True, "an ordinary failure must not become a refusal"
    assert "kind" not in body
    assert bl.last_refusal("acct-7") is None


def test_refusal_payload_carries_no_proxy_endpoint_data():
    # Exposure boundary. `detail` names the profile and the proxy NAME, both
    # already on ProfileResponse. It must never widen into the exit IP or the
    # proxy host/port that this lane's other tools deliberately withhold from an
    # off-machine caller (see list_proxies).
    mcp, bl = _harness()
    bl.refuse_next_with = ProxyUnresolvedError(
        "Profile 'acct-7' has proxy 'home' assigned but it could not be "
        "resolved (deleted/renamed?). Refusing to launch DIRECT."
    )

    blob = json.dumps(_launch(mcp))

    assert "home" in blob, "the proxy NAME is the point of the sentence"
    for secret in ("156.243.150.219", "1.2.3.4", ":1080", "socks5://"):
        assert secret not in blob, f"{secret!r} must not leave the machine"
