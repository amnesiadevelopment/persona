"""Pin PS-402's corrections — and pin the MEASURED FACT that makes them true.

⭐ WHY THIS FILE EXISTS AT ALL, since it adds no check and gates no behaviour.
PS-402 corrected five prose sites that asserted a persona firefox launch is "a
direct, single-process launch [that] does not leak on `terminate()` at all, so a
measurement taken there would be vacuous". That claim was false, it had been
copied to five places, and NOTHING IN THE REPO COULD NOTICE — a code comment is
the only assertion surface here that no import, call, type check or test
validates, so it rots silently and is then quoted as authority. This file is the
missing validator for the ONE claim that was measured, so the correction cannot
rot back into the sentence it replaced.

⛔ WHAT IT DELIBERATELY DOES NOT ASSERT, so a green here is not over-read.
NOTHING HERE LAUNCHES A BROWSER. The firefox structure below — that the engine
tree runs in its OWN session and is therefore absent from the group the survivor
gate records — was measured on 8 real Personium firefox launches under Xvfb and
is recorded in `readings/ps402-2026-09-10/`. This file pins the CODE-LEVEL
premises that measurement rests on (which are checkable here, cheaply, on every
platform) and the PROSE that now states it. It does not re-measure the tree, and
a green here is NOT a claim that any browser was observed.

⚠️ AND THE PROSE ASSERTIONS ARE DELIBERATELY NEGATIVE-KEYED. Asserting that a
comment CONTAINS a sentence pins wording and turns every future rewrite red for
no reason. What these assert instead is that the FALSE CLAIM is never left
standing as an assertion — it may appear only as a quoted, marked error. That is
the property a reader is harmed by when it breaks, and it survives rewording.
"""

from __future__ import annotations

import inspect
import re

import pytest

from src.services.verify import behaviour, behaviour_checks

#: The claim PS-402 refuted, in the two phrasings the five sites used. Matched
#: case-insensitively over collapsed whitespace, because four of the five sites
#: are wrapped comments and the fifth is a wrapped string literal — a phrase
#: split across a line break is the same claim.
FALSE_CLAIMS = (
    "single-process launch does not leak",
    "would be vacuous",
    "not the arm ps-192 was measured on",
)

#: The marks a site uses to say "this is a quoted error, not an assertion". A
#: correction has to be allowed to QUOTE the sentence it corrects — deleting it
#: would leave a reader unable to tell a corrected claim from one nobody ever
#: made — so the rule is that a quotation must be accompanied by a repudiation.
REPUDIATIONS = (
    "was false",
    "were false",
    "it would not",
    "used to give",
    "used to end",
    "used to carry",
    "refuted",
    "corrected",
)


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def _source(obj) -> str:
    return inspect.getsource(obj)


def _workflow() -> str:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    return (root / ".github" / "workflows" / "behaviour-launch-lane.yml").read_text()


def _survivor_section() -> str:
    """The survivor section's own prose, sliced from the module source.

    Sliced rather than read whole: `behaviour_checks.py` is 2,700 lines and the
    claim is about the survivor checks, so a whole-file scan would be satisfied
    or broken by unrelated text. The slice runs from the section marker to the
    degraded section's, which is the region PS-347/PS-383/PS-402 all edit.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    text = (root / "src" / "services" / "verify" / "behaviour_checks.py").read_text()
    start = text.index("SCOPE OF THIS FIRST SLICE")
    end = text.index("--- 9. every out-of-perimeter launch artifact")
    return text[start:end]


# --- 1. the five corrected sites ------------------------------------------


@pytest.mark.parametrize(
    "site",
    [
        "module_scope_note",
        "survivor_profile_docstring",
        "pass_message",
        "uncovered_surfaces",
        "workflow_header",
        "workflow_provisioning_step",
    ],
)
def test_the_refuted_claim_is_never_left_standing_as_an_assertion(site: str) -> None:
    """Each of the five sites may QUOTE the false claim, never assert it.

    ⚠️ THIS IS THE ASSERTION THAT WOULD HAVE CAUGHT THE ORIGINAL DEFECT, and it
    is worth saying why it is keyed on repudiation rather than on absence: PS-402
    was told to CORRECT the notes rather than delete them, precisely because a
    reader who finds a corrected sentence beside an uncorrected copy stops
    looking — the copy that agrees with the code wins. So a quotation is legal
    and a bare restatement is not.
    """
    if site == "module_scope_note":
        haystack = _survivor_section()
        haystack = haystack[: haystack.index("_MIN_LIVE_TREE = 3")]
    elif site == "survivor_profile_docstring":
        haystack = behaviour_checks._survivor_profile.__doc__ or ""
    elif site == "pass_message":
        haystack = _source(behaviour_checks._run_no_process_survives_a_closed_session)
    elif site == "uncovered_surfaces":
        haystack = "\n".join(
            f"{title}\n{body}" for title, body in behaviour.UNCOVERED_SURFACES
        )
    elif site == "workflow_header":
        text = _workflow()
        haystack = text[: text.index("runs-on: ubuntu-24.04")]
    else:
        text = _workflow()
        haystack = text[text.index("Provision the Personium chromium engine binary") :]

    flat = _collapse(haystack)
    quoted = [claim for claim in FALSE_CLAIMS if claim in flat]
    if not quoted:
        return  # the site does not mention it at all, which is also fine

    assert any(mark in flat for mark in REPUDIATIONS), (
        f"{site} still carries the refuted claim {quoted!r} with nothing marking "
        "it as an error. A persona firefox launch is a FORK launch above a "
        "6-to-12 process Gecko tree (readings/ps402-2026-09-10/ measured 11; "
        "readings/ps171-2026-08-25/ measured 6 and 11; "
        "readings/ps349-2026-09-09/ measured 10 and 12), so a "
        "measurement taken there would NOT be vacuous. Either repudiate the "
        "quotation or drop it — do not leave it standing as a reason."
    )


def test_the_corrections_cite_a_reading_rather_than_restating_a_claim() -> None:
    """AC7: each correction must point at evidence, not at another sentence.

    The original claim's whole failure mode was that it was ASSERTED and then
    copied — five sites deep, none of them citing anything. A correction that
    merely asserts the opposite is the same defect with the sign flipped.
    """
    sites = {
        "module scope note": _survivor_section(),
        "UNCOVERED_SURFACES": "\n".join(b for _, b in behaviour.UNCOVERED_SURFACES),
        "workflow": _workflow(),
    }
    for name, text in sites.items():
        flat = _collapse(text)
        assert "readings/ps402-2026-09-10" in flat, (
            f"{name} does not cite the reading its correction rests on"
        )
        assert sum(
            token in flat
            for token in ("ps171-2026-08-25", "ps349-2026-09-09")
        ) >= 1, (
            f"{name} cites neither of the two INDEPENDENT tree-size readings. "
            "PS-402's own measurement is one host and one container; the "
            "corrections are load-bearing because two earlier tickets measured "
            "the same tree for unrelated purposes."
        )


#: A ``readings/<dir>/<file>`` citation, with the optional ``:N`` or ``:N-M``
#: line anchor these notes use. Deliberately excludes whitespace, so a citation
#: WRAPPED ACROSS A LINE BREAK does not silently reassemble into a path that
#: resolves — it breaks into a fragment that does not, which is the honest
#: outcome: a reader cannot follow a citation they have to reassemble either.
_CITATION = re.compile(r"readings/[A-Za-z0-9._/-]+(?::\d+(?:-\d+)?)?")

#: Trailing sentence punctuation that is not part of the path.
_TRAILING = "`'\").,;:"


def _cited_paths(text: str) -> list[tuple[str, int | None]]:
    """Every ``readings/`` citation in ``text``, as ``(path, first_line)``."""
    found: list[tuple[str, int | None]] = []
    for raw in _CITATION.findall(text):
        token = raw.rstrip(_TRAILING)
        if not token:
            continue
        path, _, anchor = token.partition(":")
        path = path.rstrip("/")
        line = int(anchor.split("-")[0]) if anchor else None
        found.append((path, line))
    return found


def test_every_citation_in_the_corrections_resolves_on_disk() -> None:
    """⭐ THE GUARD THAT WOULD HAVE CAUGHT THIS PR'S OWN FIRST ROUND.

    ⛔ THE SIBLING TEST ABOVE IS NOT ENOUGH, AND THE GAP IS NOT HYPOTHETICAL.
    It asserts the TOKEN ``ps171-2026-08-25`` appears in the prose and never
    resolves the path — so it was GREEN on four citations that named
    ``readings/ps171-2026-08-25/REPORT.md``, a file that does not exist (the
    reading is ``REPRO.md``). Three of those four sat INSIDE the replacement
    sentences, in the clause doing the evidentiary work. A correction whose
    evidence 404s is the defect this whole file exists to close, reproduced one
    layer up: per PS-24, prose is the only unenforced assertion surface in the
    repo, and a citation nothing resolves is exactly the carrier that rots.

    So this resolves what the corrections cite, rather than pattern-matching it:

    1. the path exists (catches a rename, a deletion, and a typo'd filename);
    2. a ``:N`` anchor is inside the file (catches a citation that outlived the
       lines it points at);
    3. the ONE anchor carrying a quoted figure resolves to the line that
       actually carries it — because (1) and (2) both passed on the wrong line
       number this PR shipped (``:405`` exists; it is just not the figure).

    ⚠️ SCOPED TO THE CORRECTED SITES, deliberately. `behaviour_checks.py` cites
    four other readings this ticket did not author; widening the resolver to
    every citation in the repo is a strictly better guard and a separate slice,
    not something to smuggle in under a prose correction.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    sites = {
        "module scope note": _survivor_section(),
        "survivor_profile docstring": behaviour_checks._survivor_profile.__doc__ or "",
        "pass message": _source(
            behaviour_checks._run_no_process_survives_a_closed_session
        ),
        "UNCOVERED_SURFACES": "\n".join(b for _, b in behaviour.UNCOVERED_SURFACES),
        "workflow": _workflow(),
    }

    seen = 0
    for name, text in sites.items():
        # Un-wrap the comment/string prefixes so a citation split only by the
        # source's own line-leading `#` / quote noise is still one token.
        flat = re.sub(r"\n\s*(?:#|\"|')?\s*", " ", text)
        for path, line in _cited_paths(flat):
            seen += 1
            target = root / path
            assert target.exists(), (
                f"{name} cites {path!r}, which does not exist. A correction "
                "that points at a missing file is the same rot as the claim it "
                "replaced — a reader who checks the evidence gets a 404 and "
                "has no way to tell a mistyped path from a withdrawn reading."
            )
            if line is None:
                continue
            assert target.is_file(), (
                f"{name} cites {path!r} with a line anchor ({line}), but that "
                "path is a directory."
            )
            total = len(target.read_text(errors="replace").splitlines())
            assert 1 <= line <= total, (
                f"{name} cites {path}:{line}, but that file has {total} lines. "
                "The anchor has outlived the lines it points at."
            )

    assert seen >= 5, (
        "the corrected sites cite fewer readings than expected "
        f"({seen}); this resolver is now guarding almost nothing."
    )


def test_the_quoted_tree_size_anchor_lands_on_the_figure_it_quotes() -> None:
    """The anchor check one level sharper, on the citation that carries a number.

    ⛔ EXISTENCE IS NOT ENOUGH FOR A CITATION THAT QUOTES A FIGURE. This PR's
    first round cited ``REPRO.md:405``; line 405 exists, so both checks above
    would pass — the figure it attributes to that line (``proc_cmdline_n`` 6 at
    one tab, 11 at two) is three lines further down. A reader following the
    anchor lands on a different sentence and cannot tell whether the prose or
    the reading is wrong.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    reading = root / "readings" / "ps171-2026-08-25" / "REPRO.md"
    lines = reading.read_text().splitlines()

    anchors = {
        line
        for text in (
            _survivor_section(),
            behaviour_checks._survivor_profile.__doc__ or "",
            "\n".join(b for _, b in behaviour.UNCOVERED_SURFACES),
            _workflow(),
        )
        for path, line in _cited_paths(re.sub(r"\n\s*(?:#|\"|')?\s*", " ", text))
        if path == "readings/ps171-2026-08-25/REPRO.md" and line is not None
    }
    assert anchors, (
        "no corrected site anchors the PS-171 tree-size reading to a line. The "
        "figure the corrections quote lives at a specific line; cite it."
    )
    for line in anchors:
        assert "proc_cmdline_n" in lines[line - 1], (
            f"readings/ps171-2026-08-25/REPRO.md:{line} is "
            f"{lines[line - 1]!r}, which is not the line carrying the "
            "`proc_cmdline_n` figure the corrections quote."
        )


# --- 2. the code-level premises the measurement rests on ------------------


def test_os_type_selects_the_engine_and_linux_does_not_mean_firefox() -> None:
    """The trap that would have made a firefox arm silently measure chromium.

    ⛔ EXECUTED, NOT READ. `_survivor_profile` pins BOTH fields
    (`os_type="linux", engine="chromium"`), and a caller who copies it and edits
    only `engine=` gets chromium anyway — which would report the arm that is
    ALREADY gated under a firefox name. That is a false green of exactly the
    class PS-8 exists to catch, so the resolution rule is pinned rather than
    trusted to a docstring.
    """
    from src.services.profile.coherence import coherent_engine

    assert coherent_engine("windows", "firefox") == "firefox"
    for os_type in ("linux", "macos", "android"):
        assert coherent_engine(os_type, "firefox") == "chromium", (
            f"coherent_engine({os_type!r}, 'firefox') no longer resolves to "
            "chromium. Every scope note in the survivor section, and the "
            "reasoning in readings/ps402-2026-09-10/, rests on os_type being "
            "the field that selects the engine."
        )


def test_the_survivor_profile_still_pins_both_fields_together() -> None:
    """Half-changing the pair is the failure the test above describes."""
    source = _source(behaviour_checks._survivor_profile)
    assert 'os_type="linux"' in source and 'engine="chromium"' in source, (
        "the survivor profile no longer pins both fields. os_type is what "
        "selects the engine, so a lone `engine=` is not a chromium guarantee."
    )


def test_the_chromium_only_socket_guard_is_still_gated_on_a_posix_predicate() -> None:
    """§4b: why a firefox arm must NOT reuse `_survivor_profile`.

    `singleton_socket_is_bound()` is `not IS_WINDOWS`, so it is TRUE on Linux for
    a firefox launch — which binds no singleton socket at all. Reusing the helper
    would refuse a launchable session for a constraint that does not exist on
    that path, and a `BehaviourCheckError` lands as CANNOT_RUN (exit 2): a false
    void, not a pass. Pinned so a future firefox arm cannot inherit it by
    accident, and so the guard's SCOPE stays visible beside its arithmetic.
    """
    source = _source(behaviour_checks._survivor_profile)
    assert "singleton_socket_is_bound()" in source
    assert "chromium" in source.lower(), (
        "the refusal no longer names the engine its arithmetic describes, so a "
        "reader cannot tell it is chromium-scoped"
    )
    from src.core import platform as _platform

    assert behaviour.singleton_socket_is_bound() is (not _platform.IS_WINDOWS)


def test_firefox_binds_no_process_singleton_socket_on_the_launch_path() -> None:
    """The premise under the test above, checked rather than asserted in prose.

    The whole `SOCKET_BOUND_PROFILE_NAMES` budget exists because CHROMIUM's
    process singleton binds a UNIX socket and `sun_path` is 108 bytes. If the
    firefox launch path ever grew one, the "firefox needs no socket budget"
    reasoning in every corrected note would silently stop being true.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    text = (root / "src" / "services" / "browser" / "invisible_launch.py").read_text()
    for token in ("AF_UNIX", "sun_path", "SingletonSocket"):
        assert token not in text, (
            f"{token} now appears on the firefox launch path. The corrected "
            "scope notes and readings/ps402-2026-09-10/ both reason that "
            "firefox binds no process-singleton socket; re-derive them."
        )


def test_the_fork_path_records_its_group_and_carries_a_readable_stdout() -> None:
    """The two handle properties the survivor check's body needs.

    ⭐ AND THE ONE THAT EXPLAINS THE WHOLE PS-402 FINDING. The group IS recorded
    on the firefox handle — the check's mechanism is compatible with it, which is
    what the ticket established by reading. What the measurement added is that
    the recorded group is the group the FORKED PYTHON LEADER leads, and the
    ENGINE leaves it by calling setsid. Both facts are true at once, and the
    second is why a compatible mechanism still cannot see the browser.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    text = (root / "src" / "services" / "browser" / "invisible_launch.py").read_text()
    assert "record_group_by_construction(self, pid=" in text, (
        "the fork path no longer records its group ON THE HANDLE, which is what "
        "`process_group.recorded_group()` reads. PS-204's own comment records "
        "what breaks without it: the tree still dies, by polymorphism, while "
        "`reap_process_group` reports False on a call that DID deliver a group "
        "signal."
    )
    assert "self.stdout = os.fdopen(r" in text, (
        "the fork path no longer exposes a readable stdout, which `_drain` needs"
    )


# --- 3. the lane is untouched, which is the point -------------------------


def test_no_firefox_survivor_check_was_added_to_the_registry() -> None:
    """PS-402 deliberately added NO arm, and the reason must stay legible.

    ⛔ THIS IS NOT A PROHIBITION ON EVER ADDING ONE. It pins the decision's
    PREMISE: a check that reports CANNOT_RUN (exit 2) on every run would put a
    name in the lane's floor that can never certify anything, so the arm is
    blocked on a session-anchored counter rather than on a second profile. A
    future slice that ships that counter SHOULD add the arm — and should update
    this test in the same edit, deliberately, which is exactly the kind of edit
    that is meant to be noticed.
    """
    names = behaviour_checks.check_names()
    survivor = [n for n in names if n.startswith("no-process-survives")]
    assert survivor == [
        "no-process-survives-a-closed-session",
        "no-process-survives-a-degraded-session",
    ], (
        f"the survivor family changed: {survivor}. If a firefox arm was added, "
        "it needs a session-anchored counter — the group-anchored one cannot "
        "satisfy _MIN_LIVE_TREE on that engine (measured: counted group 2, real "
        "tree 10-11, readings/ps402-2026-09-10/) and reports CANNOT_RUN forever."
    )


def test_the_lane_constants_are_unchanged_and_the_omission_set_is_still_empty() -> None:
    """AC6: `DOCUMENTED_OMISSIONS` must not be re-grown, and was not.

    No check was added, so nothing needed omitting — which is the strictest
    available state rather than a carve-out. PS-383 took this mapping from 2
    entries to 1 to 0, and re-growing it in a later commit is the specific move
    its own header names as the one to refuse.
    """
    from tests import test_ps336_launch_behaviour_venue as venue

    assert venue.DOCUMENTED_OMISSIONS == {}, (
        "DOCUMENTED_OMISSIONS was re-grown. PS-402 added no check, so it has "
        "nothing to omit; an entry here now belongs to some other change and "
        "must carry its own distinct reason."
    )
