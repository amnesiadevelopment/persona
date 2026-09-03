"""PS-222: the MCP launch tool must CHECK engine readiness before start_thread.

``start_thread`` has three call sites in ``src/``. Two of them — the UI
(``ui/actions/browser.py``) and REST (``api/routes/browser.py``) — resolve the
effective engine and refuse a Firefox launch when the engine is not installed.
The MCP lane did not: ``grep -c is_invisible_installed src/api/mcp_server.py``
answered 0, and nothing between the tool's body and ``_spawn_invisible``
re-asked the question. The tree states the cost at both guarding doors —
"falling through would let the engine start its own blocking, non-resumable
download mid-launch" — and that cost was CONFIRMED REACHABLE from this lane,
by execution rather than by quotation: with no build installed
``_binary_path_override()`` returns None, so ``binary_path`` is omitted from
the engine kwargs, ``resolve_executable(None)`` takes its ``ensure_binary()``
arm, and that reaches ``invisible_core.download._download_file`` — one
``requests.get`` with no Range and no resume. The launch is then ALSO reported
as ``{"launched": True}``, because the child announces LAUNCH_FAILED on a pipe
rather than raising. Both harms are recorded at the decision site in
``mcp_server.py``.

EVERY ASSERTION BELOW BINDS TO WHETHER ``start_thread`` WAS CALLED, never to
"a helper exists" or "the response shape changed". That is what makes the
falsification meaningful: delete the guard from ``mcp_server.py``, keep
everything else, and ``test_firefox_launch_refused_when_engine_missing`` goes
red on ``bl.started is False`` — the observer, not the payload.

The ``_BL.started`` flag is lifted directly from ``tests/test_launch_guard.py``,
the UI door's guard suite, which is this file's template.
"""

import asyncio
import json

from src.api.mcp_server import build_mcp
from src.core.container import Container
from src.models.profile import Profile


class _BL:
    """Records whether the launch actually reached the launcher.

    ``started`` is the observer both the refusal tests and the falsification
    check read. ``last_refusal`` returns None so ``refusal_for_attempt`` finds
    no post-attempt verdict — keeping these tests bound to the PRE-attempt
    guard rather than to the PS-82 refusal-report path.
    """

    def __init__(self):
        self.started = False
        self.launched = []

    def is_running(self, name):
        return False

    def start_thread(self, profile, log_callback=None, **kwargs):
        self.started = True
        self.launched.append(profile)

    def last_refusal(self, name):
        return None


class _PM:
    def __init__(self, profile):
        self.profiles = {profile.name: profile}

    def list_profiles(self):
        return list(self.profiles.values())

    def set_cert_trust_status(self, name, status):
        pass


def _profile(name="acct-7", engine="firefox", os_type="windows"):
    p = Profile(name=name)
    p.engine = engine
    p.os_type = os_type
    return p


def _harness(profile):
    c = Container()
    bl = _BL()
    c._instances["pm"] = _PM(profile)
    c._instances["bl"] = bl
    return build_mcp(c), bl


def _launch(mcp, name="acct-7"):
    result = asyncio.run(mcp.call_tool("launch_profile", {"name": name}))
    return json.loads(result[0].text)


def test_firefox_launch_refused_when_engine_missing(monkeypatch):
    """AC1 + AC6. The engine is absent, so nothing may reach start_thread.

    Patched on the ``invisible_launch`` module object, which is the patch point
    that works given the guard's function-local import — the same point
    tests/test_launch_guard.py and tests/test_automation_api.py use.
    """
    from src.services.browser import invisible_launch as il

    monkeypatch.setattr(il, "is_invisible_installed", lambda: False)
    mcp, bl = _harness(_profile())

    body = _launch(mcp)

    # THE LOAD-BEARING ASSERTION: the launch did not happen. A fix that only
    # changed the payload would pass the next two and fail this one.
    assert bl.started is False, "a launch with no engine installed must not start"
    assert bl.launched == []
    assert body["launched"] is False
    assert "engine" in body["error"].lower()


def test_firefox_launch_proceeds_when_engine_installed(monkeypatch):
    """AC4. With the engine present the lane behaves exactly as it did before:
    it launches. The guard adds a precondition, it removes no capability."""
    from src.services.browser import invisible_launch as il

    monkeypatch.setattr(il, "is_invisible_installed", lambda: True)
    mcp, bl = _harness(_profile())

    body = _launch(mcp)

    assert bl.started is True
    assert body == {"launched": True, "name": "acct-7"}


def test_mobile_firefox_profile_does_not_require_firefox_engine(monkeypatch):
    """AC3 — the criterion a fix that reads ``profile.engine`` fails.

    A legacy mobile profile stores engine="firefox" but effective_engine
    downgrades it to chromium, so demanding the Firefox engine here would
    refuse a launch that will never touch it. Modelled on the UI door's
    test_mobile_firefox_profile_does_not_require_firefox_engine.

    Asserts the LAUNCH, not merely a non-refusal: an assertion that the
    response was not a refusal would also pass against a lane that answered
    some other error.
    """
    from src.services.browser import invisible_launch as il

    monkeypatch.setattr(il, "is_invisible_installed", lambda: False)  # FF absent
    mcp, bl = _harness(_profile(engine="firefox", os_type="android"))

    body = _launch(mcp)

    assert bl.started is True, "launched as chromium, not blocked on the FF engine"
    assert body["launched"] is True


def test_guard_only_checks_never_downloads(monkeypatch):
    """The guard must CHECK for the engine, never fetch it —
    ensure_invisible_installed downloads ~118MB over Tor and blocks the launch
    for minutes. This is the MCP lane's copy of the UI door's
    test_firefox_launch_check_never_downloads, and of the shipped
    tests/test_process.py::test_needs_fetch_never_triggers_download.

    ⚠️ SCOPE — READ BEFORE TREATING THIS AS DOWNLOAD COVERAGE. What is patched
    here is persona's OWN ``ensure_invisible_installed``, the resumable wrapper,
    which the launch path genuinely never calls. It is NOT the download this
    lane could actually reach: that one comes from the VENDORED engine's
    ``invisible_core.download.ensure_binary``, entered via
    ``resolve_executable(None)`` when no build is installed, and nothing in this
    test observes it. So this asserts "the guard did not call persona's
    installer", not "no download is possible" — the reachable vendored fetch is
    traced at the decision site in mcp_server.py and is deliberately not
    exercised here (it would mean driving a real network fetch).
    """
    from src.services.browser import invisible_launch as il

    def _boom(*a, **k):
        raise AssertionError("launch path must not trigger the engine download")

    monkeypatch.setattr(il, "ensure_invisible_installed", _boom)
    monkeypatch.setattr(il, "is_invisible_installed", lambda: True)
    mcp, bl = _harness(_profile())

    body = _launch(mcp)

    assert bl.started is True
    assert body["launched"] is True


def test_refusal_does_not_restate_the_rest_lane_operator_sentence(monkeypatch):
    """The REST door's settled operator sentence is NOT restated in what this
    lane ANSWERS.

    services/browser/refusal.py records why: a settled sentence duplicated into
    a second module forks at the first edit. REST's remedy also addresses a
    human at the app, which is not this lane's caller.

    Asserted against the RESPONSE the client receives, not against module
    source — a source scan cannot tell a sentence this lane emits from one a
    comment merely cites, and would fail on a comment that explains the very
    rule it is checking.
    """
    from src.services.browser import invisible_launch as il

    monkeypatch.setattr(il, "is_invisible_installed", lambda: False)
    mcp, _bl = _harness(_profile())

    body = _launch(mcp)

    assert body["launched"] is False
    # The REST door's remedy sentence, verbatim from routes/browser.py.
    assert "download it from the app first" not in body["error"]
    # And no new response shape: the two-key precondition form the two
    # refusals above it already use, not the four-key {kind, detail} verdict.
    assert set(body) == {"launched", "error"}
