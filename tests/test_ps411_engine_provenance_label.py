"""PS-411: a reading records WHICH PROJECT built the engine it was taken on.

THE EVENT THIS CLOSES. ``checker_cli._chromium_label``'s docstring recorded a
deliberate deferral AND NAMED THE CONDITION UNDER WHICH IT WOULD EXPIRE — *"when
we ship a binary we built ourselves, the thing being measured genuinely stops
being ``fingerprint-chromium`` and the label becomes factually wrong"*. We ship
one (``engine/releases/personium-152.0.7977.75.json``, published 2026-09-06), so
a header reading ``fingerprint-chromium/152.0.7977.75`` names a project that did
not build the binary it measured. The verification corpus is this direction's
product, so a record that misnames the artifact it measured is an
evidence-hygiene defect in the thing we sell.

⛔ WHAT THIS IS NOT, AND THE COMMIT MESSAGE MUST NOT SAY OTHERWISE. Nothing a
page observes changes; no host fact escapes; no spoof is weakened. This is not
an Invariant #0 claim and the engine/masking vocabulary invites a drift upward
that is NOT warranted. It also ships no fix and closes no leak: ZERO committed
readings carry a mislabelled header today, because the newest chromium checker
reading in the tree is ``148.0.7778.215`` — an upstream build, correctly named.
Its case is the NEXT chromium reading.

⭐ AND THE PRECISE DEFECT IS NOT THE ONE THE TICKET PREDICTED. The ticket
expected ``fingerprint-chromium/personium-152.0.7977.75`` — the upstream project
and our release tag mashed into one string — and said two CI lanes were
"provisioned to produce it". MEASURED AT ``f33307d``: THEY ARE NOT, and that
string is UNREACHABLE. ``version.txt`` never holds the published tag:
``updater.version_from_tag`` strips the ``personium-`` prefix at the module's
API boundary, deliberately, because that file is the sole source of the Chromium
version an Android profile advertises and a prefixed value there would leak onto
the wire. Every one of the eight ``write_version`` call sites resolves to a bare
dotted version. ``test_the_ticket_s_predicted_malformed_header_is_unreachable``
below pins that, because a refuted premise left unpinned is one somebody
re-derives in six months.

The real defect is plainer and survives: the header calls a build WE published
by the UPSTREAM project's name.

THE CONSEQUENCE THAT SHAPED THE FIX, and it is why the label is not simply
renamed. Because the prefix is stripped, ``version.txt`` holds a bare dotted
version under BOTH regimes — an upstream ``148.0.7778.215`` and a persona-built
``152.0.7977.75`` are indistinguishable BY SHAPE. A machine that installed before
PS-305 moved engine releases into persona's own repository is still running the
upstream binary. An unconditional rename would therefore fix one false claim by
MINTING ANOTHER on exactly those machines — the same defect with the sign
flipped, and harder to notice. So the label is established from the committed
provenance record rather than assumed, and it fails toward the WEAKER claim.

THE FALSIFICATION IS IN TWO ARMS AND BOTH ARE HERE, because this project has
twice recorded a confident, completely false green — PS-299's rebase probe
printing "81/81 hunks, 0 rejects" against an EMPTY DIRECTORY, and PS-341's
``--dump-dom`` reading "8 of 8 moved". A red arm proves a guard can FIRE; only a
quiet arm proves the consumers still WORK.

* **RED** — ``test_a_label_that_still_claims_upstream_on_OUR_build_is_caught``
  and the substring arms in ``test_ps224_engine_name.py``. A label left
  unchanged, or one dropping ``"chromium"``, fails a test naming the reason.
* **QUIET** — ``test_every_consumer_reads_the_new_label_exactly_as_before``.
  ``matrix_diff``/``diff`` compare two same-build records WITHOUT refusing, and
  ``pool_depth.build_report`` still partitions correctly. This is the half that
  cannot be skipped.
"""

import json
import os
import tempfile

os.environ.setdefault("PERSONA_HOME", tempfile.mkdtemp())

import pytest  # noqa: E402

from src.services.verify import checker_cli  # noqa: E402


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The engine persona has actually PUBLISHED, and the one whose provenance
#: record is committed. Read from the tree rather than typed, so this suite
#: cannot quietly go on testing a release we no longer ship.
_RECORDS = os.path.join(REPO_ROOT, "engine", "releases")


def _published_versions():
    """Every version with a committed provenance record, bare (no tag prefix)."""
    from src.services.engine.updater import ENGINE_TAG_PREFIX

    out = []
    for name in os.listdir(_RECORDS):
        if name.endswith(".json") and name.startswith(ENGINE_TAG_PREFIX):
            out.append(name[len(ENGINE_TAG_PREFIX):-len(".json")])
    return sorted(out)


# ---------------------------------------------------------------------------
# The premise: a self-built engine exists, and it is read from the tree
# ---------------------------------------------------------------------------


def test_persona_actually_publishes_an_engine_so_the_deferral_really_expired():
    """THE PREMISE, ASSERTED RATHER THAN ASSUMED.

    Every other test here rests on "we ship a binary we built ourselves". If
    that ever stopped being true the right answer would be to revert this whole
    change, not to keep testing a distinction with nothing on one side of it —
    so the premise gets its own arm instead of being inherited from a ticket.
    """
    published = _published_versions()
    assert published, (
        "no committed provenance record under engine/releases/ — persona "
        "publishes no engine, so the PS-411 deferral has NOT expired and this "
        "whole change rests on nothing"
    )

    # And a record must actually name its tag, or the filename is the only
    # evidence and a stray file would read as a published release.
    from src.services.engine.updater import ENGINE_TAG_PREFIX

    for version in published:
        path = os.path.join(_RECORDS, f"{ENGINE_TAG_PREFIX}{version}.json")
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
        assert record.get("tag") == f"{ENGINE_TAG_PREFIX}{version}", (
            f"{path} does not name its own tag: {record.get('tag')!r}"
        )


# ---------------------------------------------------------------------------
# RED ARM
# ---------------------------------------------------------------------------


def test_a_label_that_still_claims_upstream_on_OUR_build_is_caught(monkeypatch):
    """RED ARM. The defect PS-411 exists to remove, stated as an executable
    fact so that reverting the fix turns THIS red with the reason attached.

    A reading taken on an engine persona BUILT must not record itself as having
    been taken against ``fingerprint-chromium`` — a third-party project that did
    not produce the binary and has not shipped since 2026-06-21.
    """
    from src.services.engine import updater

    for version in _published_versions():
        monkeypatch.setattr(updater, "current_version", lambda v=version: v)
        label = checker_cli._chromium_label()

        assert not label.startswith("fingerprint-chromium"), (
            f"the reading header {label!r} claims the measurement was taken "
            f"against fingerprint-chromium, but {version} is a build PERSONA "
            f"published (engine/releases/personium-{version}.json). The "
            "record's own account of itself is false."
        )
        assert label == f"persona-chromium/{version}", label


def test_the_version_is_carried_through_unaltered(monkeypatch):
    """The provenance half must not cost the VERSION half.

    The label answers two questions — who built it, and which build — and a fix
    that got the first right by dropping or rewriting the second would trade one
    unusable header for another. ``policy.KNOWN_BAD_VERSIONS`` is keyed by the
    exact version string, so a header that cannot be matched against it is a
    finding nobody can act on.
    """
    from src.services.engine import updater

    for version in ("152.0.7977.75", "148.0.7778.215", "1.2.3.4"):
        monkeypatch.setattr(updater, "current_version", lambda v=version: v)
        assert checker_cli._chromium_label().endswith(f"/{version}")


# ---------------------------------------------------------------------------
# The refuted premise, pinned so it is not re-derived
# ---------------------------------------------------------------------------


def test_the_ticket_s_predicted_malformed_header_is_unreachable():
    """⛔ A REFUTATION, PINNED. Not a test of the fix — a test of a claim about
    the fix that was measured FALSE, kept because an unpinned refutation gets
    re-derived.

    PS-411 predicted the header ``fingerprint-chromium/personium-152.0.7977.75``
    and said two CI lanes were provisioned to produce it. They are not.
    ``_release_asset`` — the ONE selection path both the latest-release fetch
    and the by-tag fetch share — returns ``version_from_tag(tag)``, so every
    value any caller can hand ``write_version`` is already bare.

    This drives the REAL ``_release_asset`` against a release document shaped
    like the shipped one, rather than restating the claim in a comment.
    """
    from src.services.engine import updater

    for version in _published_versions():
        tag = f"{updater.ENGINE_TAG_PREFIX}{version}"
        document = {
            "tag_name": tag,
            "draft": False,
            "assets": [
                {
                    "name": f"{tag}-linux-x86_64.AppImage",
                    "browser_download_url": "https://example.invalid/a",
                    "digest": "sha256:" + "ab" * 32,
                },
                {
                    "name": f"{tag}-windows-x86_64.zip",
                    "browser_download_url": "https://example.invalid/w",
                    "digest": "sha256:" + "ab" * 32,
                },
                {
                    "name": f"{tag}-macos-universal.dmg",
                    "browser_download_url": "https://example.invalid/m",
                    "digest": "sha256:" + "ab" * 32,
                },
            ],
        }
        resolved, url, _digest = updater._release_asset(document)

        assert url, "the fixture must resolve an asset or this proves nothing"
        assert resolved == version, resolved
        assert not resolved.startswith(updater.ENGINE_TAG_PREFIX), (
            "the personium- prefix escaped the module's API boundary; "
            "version_from_tag's docstring forbids exactly this"
        )


def test_no_committed_reading_carries_a_mislabelled_header():
    """Honest bound: this ships no fix and closes no leak.

    Asserted rather than claimed, because "there is no live false reading" is
    the kind of sentence that is true when written and quietly stops being true.
    Every committed chromium header names ``fingerprint-chromium``, and every
    one of those readings WAS taken on an upstream build — so they are correct
    and are deliberately left untouched.
    """
    import subprocess

    listed = subprocess.run(
        ["git", "ls-files", "readings/"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert listed, "no committed readings found — this guard would be vacuous"

    offenders = []
    for rel in listed:
        path = os.path.join(REPO_ROOT, rel)
        try:
            with open(path, encoding="utf-8") as fh:
                body = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        if "fingerprint-chromium/personium" in body:
            offenders.append(rel)

    assert not offenders, (
        "a committed reading carries the two-project header PS-411 measured to "
        f"be unreachable — the measurement is stale, re-take it: {offenders}"
    )


# ---------------------------------------------------------------------------
# QUIET ARM — the half that cannot be skipped
# ---------------------------------------------------------------------------


def _record(engine, *, seed=4242):
    """A minimal checker-matrix record carrying `engine` as its header."""
    from src.services.verify.matrix import Exit, build_record

    return build_record(
        [],
        exit_=Exit(ip="1.2.3.4", country="PL"),
        engine=engine,
        observed_at="2026-09-11T10:00:00Z",
        seed=seed,
        declared_machine="windows",
        declared_machine_honoured=True,
    )


def test_every_consumer_reads_the_new_label_exactly_as_before():
    """⭐ QUIET ARM. The red arm above proves the guard can FIRE; this proves
    the consumers still WORK, and it is the half whose absence has twice let a
    confident false green ship on this project.

    Three consumers, each driven with the NEW label:

    1. ``matrix_diff.compare_records`` — two same-build records compare without
       raising ``ComparisonNotControlled``.
    2. ``diff.compare_profiles`` — the snapshot sibling of the same refusal.
    3. ``pool_depth.build_report`` — partitions on the RAW header, so the new
       spelling must still produce exactly one arm per build.

    ⚠️ AND THE REFUSALS THEMSELVES ARE ASSERTED STILL PRESENT, because "nothing
    refused" is also what a comparator with its guard deleted looks like. A
    quiet arm that only checks for silence cannot tell working from disabled.
    """
    from src.services.verify import diff, matrix_diff

    label = "persona-chromium/152.0.7977.75"

    # 1. Two records on the SAME new-label build compare cleanly.
    moved = matrix_diff.compare_records(_record(label), _record(label))
    assert isinstance(moved, list)

    # ...and the guard is NOT merely absent: a genuinely different build is
    # still refused, by name.
    with pytest.raises(matrix_diff.ComparisonNotControlled, match="different engine"):
        matrix_diff.compare_records(
            _record(label), _record("fingerprint-chromium/148.0.7778.215")
        )

    # 2. The snapshot comparator, same two directions. The `profile` header is
    # required by an EARLIER guard than the engine one (unlinkability is a
    # question about two identities), so both sides must name one or the
    # comparison never reaches the engine check this arm is about.
    def _snapshot(engine, profile, digest):
        return {
            "engine": engine,
            "profile": profile,
            "seed": 4242,
            "probes": {"window": {"canvas.readback": {"value": {"digest": digest}}}},
        }

    entries = diff.compare_profiles(
        _snapshot(label, "alpha", "a"), _snapshot(label, "beta", "b")
    )
    assert isinstance(entries, list)

    with pytest.raises(diff.ComparisonNotControlled, match="different engines"):
        diff.compare_profiles(
            _snapshot(label, "alpha", "a"),
            _snapshot("fingerprint-chromium/148.0.7778.215", "beta", "b"),
        )


def test_pool_depth_still_partitions_the_new_label_into_its_own_arm(tmp_path):
    """QUIET ARM, third consumer. ``build_report`` partitions on the RAW engine
    header with no normalisation, so this asks the question that matters: does
    the new spelling still produce ONE arm per build, and are the two builds
    still kept apart?

    Kept apart is the load-bearing half. If a normalisation ever folded
    ``persona-chromium/…`` and ``fingerprint-chromium/…`` into one arm, a
    distinctness count would blend two different engines — the exact corruption
    that module's header names.
    """
    from src.services.verify.pool_depth import Record, build_report

    def _snapshot(engine, seed):
        return {
            "engine": engine,
            "seed": seed,
            "probes": {
                "window": {
                    "canvas.readback": {"value": {"digest": seed}},
                    "webgl.readback": {"value": {"digest": seed}},
                },
                "worker": {"canvas.readback": {"value": {"digest": seed}}},
            },
        }

    ours = "persona-chromium/152.0.7977.75"
    theirs = "fingerprint-chromium/148.0.7778.215"

    records = [
        Record(source=f"r{i}", identity=f"id{i}", arm="product", is_rerun=False,
               engine=engine, snapshot=_snapshot(engine, i))
        for i, engine in enumerate([ours, ours, ours, theirs, theirs])
    ]

    report = build_report(records)
    arms = {e.engine: len(e.identities) for e in report.engines}

    assert arms == {ours: 3, theirs: 2}, (
        f"the two builds must stay in separate arms with no normalisation: {arms}"
    )
