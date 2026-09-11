"""PS-410 — the ENGINE release's tag↔version preflight, pinned.

WHAT THE GATE IS FOR, in one paragraph, because these tests only make sense
against it. The APPLICATION release is protected: `release.yml`'s `preflight`
job refuses a `v*` tag that does not equal `APP_VERSION`, before any OS spends
build time. The ENGINE release was protected by nothing, and its failure is
SILENT — the update offer is a strict compare (`engine.is_newer`), so an engine
published under a version users already carry never fires the offer at all. The
release page looks perfect and every installed persona keeps the old engine
forever.

WHAT THESE TESTS ASSERT, AND WHAT THEY DELIBERATELY DO NOT. Every decision test
drives the REAL decision function with a FAKE transport, so a change that makes
the gate permissive fails here rather than on a release morning. None of them
reach the network: the live falsification (the already-published
`personium-152.0.7977.75` refused, `…75.1` allowed) was run by hand against the
real API and is transcribed in the PR body — a test that silently skips when
the network is absent is exactly the verification-that-stops-verifying this
suite's conftest was written to refuse.

THE ONE TEST HERE THAT IS NOT ABOUT A VERDICT is
`test_the_gate_imports_with_no_third_party_packages`. The workflow runs this
script with NO `pip install` step, deliberately, so that the comparison is the
client's own `parse_version` rather than a re-implementation. That property is
invisible in the YAML and would break silently the first time someone adds an
import; it is asserted here instead.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE = REPO_ROOT / "scripts" / "ps410_engine_release_preflight.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "engine-release-preflight.yml"

PUBLISHED = "152.0.7977.75"
NEWER_REVISION = "152.0.7977.75.1"
OLDER = "148.0.7778.215"


def _load_gate():
    """Import the gate by path, without running its CLI.

    `scripts/` is not a package on this project — `tests/test_ps343_release_
    provenance.py` loads its verifier the same way.
    """
    spec = importlib.util.spec_from_file_location("ps410_engine_release_preflight", GATE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ps410_engine_release_preflight"] = mod
    spec.loader.exec_module(mod)
    return mod


_G = _load_gate()

ALLOW = _G.ALLOW
REFUSE = _G.REFUSE
UNMEASURED = _G.UNMEASURED
Unmeasured = _G.Unmeasured


# ---------------------------------------------------------------------------
# A fake GitHub. Two endpoints, exactly the two the gate reads.
# ---------------------------------------------------------------------------


class _FakeGitHub:
    """Answers the tag-refs list and the by-tag release lookup.

    `tags` is every `personium-` tag that EXISTS; `released` is the subset that
    has a published release behind it. Keeping those two separate is the whole
    point — the state this gate runs in is "the candidate's tag exists and has
    no release yet", and a fake that could not express it would be agreeable
    rather than adversarial.
    """

    def __init__(self, tags, released, *, fail_on=None, draft=()):
        self.tags = list(tags)
        self.released = set(released)
        self.draft = set(draft)
        self.fail_on = fail_on
        self.calls = []
        # An override for the refs document itself, so a MALFORMED SUCCESS can
        # be expressed and not only a failed fetch. Left `None` by default: the
        # fake answers a well-formed list unless a test asks otherwise.
        self.refs_payload = None

    def fetch_json(self, url, timeout=20):
        self.calls.append(url)
        if self.fail_on is not None and self.fail_on in url:
            raise urllib.error.URLError("connection refused")
        if "matching-refs" in url:
            if self.refs_payload is not None:
                return self.refs_payload
            return [{"ref": f"refs/tags/personium-{v}"} for v in self.tags]
        tag = url.rsplit("/", 1)[-1]
        version = tag[len("personium-"):]
        if version in self.draft:
            return {"tag_name": tag, "draft": True}
        if version in self.released:
            return {"tag_name": tag, "draft": False}
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)


@pytest.fixture
def github(monkeypatch):
    def _install(tags, released, **kw):
        fake = _FakeGitHub(tags, released, **kw)
        monkeypatch.setattr(_G.egress, "fetch_json", fake.fetch_json)
        return fake

    return _install


def _run(candidate, github_fake):
    """The gate's real decision, end to end, minus the CLI."""
    baseline = _G.newest_published_release()
    return _G.verdict(candidate, baseline)


# ---------------------------------------------------------------------------
# AC1 + AC2 — the guard REFUSES, and is shown to.
# ---------------------------------------------------------------------------


def test_republishing_the_live_version_is_refused(github) -> None:
    """THE DEFECT ITSELF. `personium-152.0.7977.75` is published; pushing that
    tag again must be refused, because `is_newer(v, v)` is False and the offer
    would never fire for a single user."""
    github([PUBLISHED], [PUBLISHED])
    code, message = _run(PUBLISHED, github)
    assert code == REFUSE, message


def test_an_older_version_is_refused(github) -> None:
    """A regression tag is the same silent failure wearing a lower number."""
    github([PUBLISHED, OLDER], [PUBLISHED, OLDER])
    code, message = _run(OLDER, github)
    assert code == REFUSE, message


def test_a_strictly_newer_revision_passes(github) -> None:
    """THE OTHER HALF OF THE FALSIFICATION. A guard only ever seen to refuse is
    as useless as one only ever seen to pass: the five-component revision
    `…75.1` is the real tag this release was cut under, and it must go through.
    """
    github([PUBLISHED, NEWER_REVISION], [PUBLISHED])
    code, message = _run(NEWER_REVISION, github)
    assert code == ALLOW, message


def test_a_newer_chromium_major_passes(github) -> None:
    github([PUBLISHED, "153.0.1.0"], [PUBLISHED])
    code, message = _run("153.0.1.0", github)
    assert code == ALLOW, message


def test_the_first_engine_release_ever_passes(github) -> None:
    """No published engine release anywhere — there is nothing to advance past,
    and refusing here would make the mechanism impossible to bootstrap."""
    github([PUBLISHED], [])
    code, message = _run(PUBLISHED, github)
    assert code == ALLOW, message


# ---------------------------------------------------------------------------
# The trap this gate was written wrong for FIRST, and must not regress into.
# ---------------------------------------------------------------------------


def test_the_candidates_own_tag_is_not_excluded_from_the_baseline(github) -> None:
    """⚠️ THE MEASURED DEFECT IN THE GATE'S OWN FIRST DRAFT.

    The gate runs ON the tag push, so the candidate's ref is already in the
    list. The obvious move is to drop any ref equal to the candidate — and it
    was written that way, run against the live API, and PASSED a republish of
    the already-live `152.0.7977.75` while reporting "(none) — the FIRST
    published engine release". That is byte-for-byte the failure this ticket
    exists to refuse, reproduced by the guard meant to prevent it.

    The discriminator is whether a RELEASE exists behind the tag, not whether
    the TAG exists. This pins that: with the candidate's tag present AND
    released, the baseline must be the candidate's own version, not "".
    """
    github([PUBLISHED], [PUBLISHED])
    assert _G.newest_published_release() == PUBLISHED


def test_a_tag_with_no_release_behind_it_is_not_the_baseline(github) -> None:
    """The state a real tag push is in: the candidate's tag exists, its release
    is not cut yet. The baseline must descend to the real predecessor rather
    than treating an empty tag as the newest published release."""
    github([NEWER_REVISION, PUBLISHED], [PUBLISHED])
    assert _G.newest_published_release() == PUBLISHED


def test_a_draft_release_is_not_published_work(github) -> None:
    """A draft is not something any user can be offered."""
    github([NEWER_REVISION, PUBLISHED], [PUBLISHED], draft=[NEWER_REVISION])
    assert _G.newest_published_release() == PUBLISHED


def test_the_baseline_is_ordered_numerically_not_lexicographically(github) -> None:
    """`personium-99.…` sorts above `personium-152.…` as a STRING. The refs
    endpoint answers in lexicographic ref order, so a gate that trusted that
    order would take 99 as the newest published release and wave every 152
    through — the same trap `engine_versions_newest_first` documents."""
    github(["99.0.1.0", PUBLISHED], ["99.0.1.0", PUBLISHED])
    assert _G.newest_published_release() == PUBLISHED


# ---------------------------------------------------------------------------
# Exit 2 — "could not look" is NOT "looks fine".
# ---------------------------------------------------------------------------


def test_an_unreachable_tag_list_is_unmeasured_not_a_pass(github) -> None:
    """`updater.engine_versions_newest_first` swallows this into `[]`, which is
    correct for the CLIENT (no engine release and no network both mean "do not
    offer an update") and catastrophic for a GATE: `[]` would read as "first
    release ever" and pass every tag. The gate makes its own fetch for exactly
    this reason."""
    github([PUBLISHED], [PUBLISHED], fail_on="matching-refs")
    with pytest.raises(Unmeasured):
        _G.newest_published_release()


def test_a_non_list_refs_document_is_unmeasured_not_a_bootstrap(github) -> None:
    """⛔ THE FETCH'S FAILURE WAS FIXED; ITS MALFORMED SUCCESS WAS NOT.

    The gate makes its own fetch so an EXCEPTION survives as `Unmeasured`
    instead of being swallowed into `[]` — but the `isinstance(refs, list)`
    guard below it was copied from `updater.engine_versions_newest_first`,
    where dropping a non-list into `[]` is CORRECT because the client's `[]`
    means "do not offer an update". Here `[]` flows to `""`, and `""` is an
    ALLOW: the same semantic inversion, one line further down.

    Reachable, not theoretical. `api.github.com` answers some rate-limit
    refusals as a 200-shaped JSON OBJECT, and the proxied branch of
    `egress.fetch_json` explicitly admits `dict | list` — so an object-shaped
    body arrives here on an ordinary bad day. Discarded, it reports "the FIRST
    published engine release" and passes a republish of the live version, which
    is byte-for-byte the failure this ticket exists to refuse.

    `scripts/ps342_chromium_watch.py` already draws this line explicitly ("the
    tag list did not come back as a list"): a named non-measurement, never an
    empty list.
    """
    fake = github([PUBLISHED], [PUBLISHED])
    fake.refs_payload = {"message": "API rate limit exceeded"}
    with pytest.raises(Unmeasured):
        _G.newest_published_release()


def test_main_is_unmeasured_on_a_non_list_refs_document(github) -> None:
    """And it must reach the EXIT CODE, not stop at the exception: a workflow
    reads the status and nothing else. A republish of the live version under a
    rate-limited refs fetch must be 2, never 0."""
    fake = github([PUBLISHED], [PUBLISHED])
    fake.refs_payload = {"message": "API rate limit exceeded"}
    assert _G.main(["--tag", f"personium-{PUBLISHED}"]) == UNMEASURED


def test_probe_exhaustion_is_unmeasured_not_a_bootstrap(github) -> None:
    """⛔ A BOUND THAT WAS REACHED IS AN UNMEASURED ANSWER, NOT A NEGATIVE ONE.

    `MAX_TAG_PROBES` is the client's own bound and borrowing it is right for
    AGREEMENT — but it means different things in the two places. For the client
    stopping at five degrades to "no update offered", which is safe. Here it
    degraded to `""`, i.e. "no engine tag carries a published release" — an
    ALLOW, and a false one.

    The trigger state is the one `RELEASING.md` already warns about: a run of
    engine tags left without releases behind them. FOUR stranded tags is enough
    in practice, because the candidate's own fresh tag consumes a probe too.

    Six tags here, only the OLDEST released: the five probes are spent on
    stranded tags and the live release is never reached.
    """
    stranded = ["152.0.7977.79", "152.0.7977.78", "152.0.7977.77",
                "152.0.7977.76", NEWER_REVISION]
    github(stranded + [PUBLISHED], [PUBLISHED])
    with pytest.raises(Unmeasured):
        _G.newest_published_release()


def test_main_is_unmeasured_when_probes_are_exhausted(github) -> None:
    """The same state, through the CLI: a republish of the live version behind
    five stranded tags must be 2, never 0."""
    stranded = ["152.0.7977.79", "152.0.7977.78", "152.0.7977.77",
                "152.0.7977.76", NEWER_REVISION]
    github(stranded + [PUBLISHED], [PUBLISHED])
    assert _G.main(["--tag", f"personium-{PUBLISHED}"]) == UNMEASURED


def test_an_empty_tag_list_is_still_a_bootstrap_not_an_exhaustion(github) -> None:
    """THE OTHER SIDE OF THE SAME LINE, pinned so the fix above cannot be made
    by refusing everything. "The list was empty" and "I stopped looking" are
    different facts: the first is the honest first-release-ever `""`/ALLOW, and
    a gate that cannot be bootstrapped is a gate nobody can adopt."""
    github([], [])
    assert _G.newest_published_release() == ""
    assert _G.verdict(PUBLISHED, "")[0] == ALLOW


def test_a_short_unreleased_list_is_exhausting_nothing(github) -> None:
    """The boundary, from the permissive side. FEWER tags than the probe bound
    means every one of them WAS looked at, so `""` is a measured negative and
    must stay an ALLOW — otherwise the first release after a tag-only
    experiment could never be cut."""
    github([PUBLISHED, OLDER], [])
    assert _G.newest_published_release() == ""


def test_exactly_the_probe_bound_is_fully_measured(github) -> None:
    """And from the other edge: EXACTLY `MAX_TAG_PROBES` unreleased tags were
    all inspected — the bound was met, not exceeded — so this is still a
    measured "nothing published" rather than an exhaustion."""
    tags = [f"152.0.7977.{n}" for n in range(80, 80 - _G.MAX_TAG_PROBES, -1)]
    assert len(tags) == _G.MAX_TAG_PROBES
    github(tags, [])
    assert _G.newest_published_release() == ""


def test_a_transient_error_on_a_release_lookup_does_not_lower_the_baseline(
    github,
) -> None:
    """A 500 on the newest tag must not silently fall through to an older
    release — that would make the gate MORE permissive precisely when it is
    least able to see. Only a 404 means "nothing published here"."""
    github([PUBLISHED, OLDER], [PUBLISHED, OLDER], fail_on=f"personium-{PUBLISHED}")
    with pytest.raises(Unmeasured):
        _G.newest_published_release()


def test_unmeasured_is_a_failing_exit_code() -> None:
    """Whatever else changes, exit 2 must never be 0: a workflow reads the exit
    code and nothing else."""
    assert UNMEASURED != ALLOW
    assert UNMEASURED != 0


def test_a_proxied_404_is_recognised_as_not_found(monkeypatch) -> None:
    """`egress.fetch_json` has TWO branches that do not raise alike. The direct
    branch raises `HTTPError` with a `.code`; the PROXIED branch hands off to
    `proxy_checker`, which turns a non-200 into `RuntimeError("request failed:
    HTTP 404")` carrying no code at all. Recognising only the first would make
    this gate refuse every tag on a proxied host."""
    assert _G._is_not_found(RuntimeError("request failed: HTTP 404")) is True
    assert _G._is_not_found(RuntimeError("request failed: HTTP 500")) is False
    assert _G._is_not_found(urllib.error.HTTPError("u", 404, "nf", {}, None)) is True
    assert _G._is_not_found(urllib.error.HTTPError("u", 500, "err", {}, None)) is False


# ---------------------------------------------------------------------------
# AC3 — the refusal names the remedy.
# ---------------------------------------------------------------------------


def test_the_refusal_names_what_to_bump(github) -> None:
    """On the app preflight's model ("bump updater.py before tagging"), the
    operator must not have to work out what to do. The message must name the
    version that blocked it and a concrete tag that would pass."""
    github([PUBLISHED], [PUBLISHED])
    code, message = _run(PUBLISHED, github)
    assert code == REFUSE
    assert PUBLISHED in message, "the blocking version is not named"
    assert f"{PUBLISHED}.1" in message, "no concrete remedy tag is offered"
    assert "is_newer" in message, "the message does not say WHY it would reach nobody"


def test_the_refusal_explains_the_silent_consequence(github) -> None:
    """The cost of this failure is that NOTHING reports it. A refusal that only
    said "not newer" would leave the operator free to conclude it was pedantry
    and retag past it."""
    github([PUBLISHED], [PUBLISHED])
    _, message = _run(PUBLISHED, github)
    assert "NOBODY" in message or "nobody" in message


# ---------------------------------------------------------------------------
# AC4 — the client's own comparator, not a second one.
# ---------------------------------------------------------------------------


def test_the_gate_uses_the_clients_own_comparator() -> None:
    """A re-implementation is a second place for publish-time and update-time to
    drift apart, and the drift would be invisible on exactly the release it
    mattered for. The gate must call `updater.is_newer` / `parse_version`."""
    source = GATE.read_text(encoding="utf-8")
    assert "updater.is_newer(" in source
    assert "updater.parse_version" in source
    for banned in ("def is_newer", "def parse_version", "LooseVersion", "pkg_resources"):
        assert banned not in source, f"{banned} — the comparison was re-implemented"


def test_the_comparator_keeps_every_numeric_chunk() -> None:
    """The property that makes a five-component revision sortable, asserted
    through the gate's own import rather than restated as a constant."""
    assert _G.updater.parse_version(NEWER_REVISION) == (152, 0, 7977, 75, 1)
    assert _G.updater.is_newer(NEWER_REVISION, PUBLISHED) is True
    assert _G.updater.is_newer(PUBLISHED, PUBLISHED) is False


def test_the_probe_bound_is_the_clients_own() -> None:
    """A baseline taken over a different descent depth than `fetch_latest_full`
    uses is a baseline the client would not agree with."""
    assert _G.MAX_TAG_PROBES == _G.updater._MAX_TAG_PROBES


# ---------------------------------------------------------------------------
# The workflow wiring — a gate connected to nothing is not a gate.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def workflow():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _on_block(doc: dict) -> dict:
    # PyYAML parses a bare `on:` key as the boolean True (the Norway problem).
    return doc.get("on", doc.get(True, {}))


def test_the_workflow_fires_on_an_engine_tag(workflow) -> None:
    """`release.yml` triggers on `v*` and never sees a `personium-` tag, which
    is why this is a separate workflow rather than a job added there."""
    tags = _on_block(workflow)["push"]["tags"]
    assert any(t.startswith("personium-") for t in tags), tags


def test_the_workflow_has_a_falsification_arm(workflow) -> None:
    """AC2 needs a way to point the gate at a version that must be REFUSED,
    without pushing a bad tag to prove it."""
    dispatch = _on_block(workflow)["workflow_dispatch"]
    assert "tag" in dispatch["inputs"]


def test_the_workflow_actually_runs_the_gate(workflow) -> None:
    steps = workflow["jobs"]["preflight"]["steps"]
    runs = "\n".join(str(s.get("run", "")) for s in steps)
    assert "ps410_engine_release_preflight.py" in runs


def test_the_gate_step_cannot_be_hollowed_out(workflow) -> None:
    """A `|| true`, a `continue-on-error`, or a swallowed exit code leaves a
    green check that proves nothing — strictly worse than no check, because the
    missing one is at least visible."""
    job = workflow["jobs"]["preflight"]
    assert job.get("continue-on-error") is not True
    for step in job["steps"]:
        assert step.get("continue-on-error") is not True
        body = str(step.get("run", ""))
        assert "|| true" not in body
        assert "|| exit 0" not in body


def test_the_gate_needs_no_write_token(workflow) -> None:
    """It reads the public releases API. Nothing here writes."""
    assert workflow["permissions"] == {"contents": "read"}


def test_the_gate_imports_with_no_third_party_packages() -> None:
    """THE PROPERTY THE WORKFLOW RESTS ON AND THE YAML CANNOT SHOW.

    The workflow deliberately has no `pip install` step: the gate imports
    `src/services/engine/updater.py` so the comparison is the client's own, and
    that import chain is stdlib-only today. The first `import requests` added
    anywhere along it turns this cheap gate red on a release morning for a
    reason that has nothing to do with the tag.

    Asserted by IMPORTING IT in a child interpreter with site-packages
    disabled, not by reading the import lines — a transitive import three
    modules down is exactly what a source scan would miss.
    """
    proc = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-S", "-c",
         "import sys;"
         "sys.path=[p for p in sys.path if 'packages' not in p];"
         f"sys.path.insert(0, {str(REPO_ROOT)!r});"
         "import importlib.util;"
         f"spec=importlib.util.spec_from_file_location('g', {str(GATE)!r});"
         "m=importlib.util.module_from_spec(spec);"
         "spec.loader.exec_module(m);"
         "print(m.updater.parse_version('152.0.7977.75.1'))"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, (
        "the gate no longer imports without third-party packages — the workflow "
        f"runs it with no pip install step:\n{proc.stderr}"
    )
    assert "(152, 0, 7977, 75, 1)" in proc.stdout


# ---------------------------------------------------------------------------
# The CLI contract the workflow reads.
# ---------------------------------------------------------------------------


def test_main_returns_the_refusal_code(github) -> None:
    github([PUBLISHED], [PUBLISHED])
    assert _G.main(["--tag", f"personium-{PUBLISHED}"]) == REFUSE


def test_main_returns_zero_on_a_newer_tag(github) -> None:
    github([PUBLISHED, NEWER_REVISION], [PUBLISHED])
    assert _G.main(["--tag", f"personium-{NEWER_REVISION}"]) == ALLOW


def test_main_accepts_a_bare_version_as_well_as_a_tag(github) -> None:
    """`version_from_tag` leaves an unprefixed value alone, so a human
    dry-running `--tag 152.0.7977.75` gets an answer rather than a surprise."""
    github([PUBLISHED], [PUBLISHED])
    assert _G.main(["--tag", PUBLISHED]) == REFUSE


def test_main_is_unmeasured_when_there_is_no_tag(github) -> None:
    """An empty `GITHUB_REF_NAME` must not read as a pass."""
    github([PUBLISHED], [PUBLISHED])
    assert _G.main(["--tag", ""]) == UNMEASURED


def test_main_is_unmeasured_when_github_cannot_be_read(github) -> None:
    github([PUBLISHED], [PUBLISHED], fail_on="matching-refs")
    assert _G.main(["--tag", f"personium-{NEWER_REVISION}"]) == UNMEASURED
