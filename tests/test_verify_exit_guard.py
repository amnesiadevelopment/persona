"""The proxy guard, shown REFUSING — the outcome everything else depends on.

Direct egress works from this container: ``ipinfo.io`` and the whole JSON tier
answer 200 with no proxy at all. So a run that silently failed to attach to the
exit still looks like a complete, successful reading — pages render, verdicts
parse, the file is written, the exit code is 0. It would simply be a reading of
the OPERATOR'S REAL ADDRESS taken against a dozen fingerprinting services that
log it. That is an Invariant #0 problem, not a data-quality one.

This file exists because a guard observed only agreeing has not been observed.
Each way a run can fail to reach the exit is pointed at the guard here, and the
guard is asserted to REFUSE:

* the credential file is absent, or empty, or not a socks5 URL;
* the relay/proxy is stopped — nothing is listening;
* the connection times out;
* the exit answers, but from the WRONG COUNTRY;
* the observation itself is not a verdict (not JSON, no address).

And the two properties that must hold on the refusing paths too: no fallback to
a direct connection exists anywhere in the module, and no message ever carries
the credential.
"""

from __future__ import annotations

import builtins
import json
import os
import subprocess
import sys

import pytest

from src.services.verify import exit_guard
from src.services.verify.exit_guard import (
    EXPECTED_COUNTRY,
    Exit,
    ExitNotProven,
    load_credential,
    observe_exit,
    prove_exit,
    redact,
)
from src.services.verify.socks_fetch import (
    FetchFailed,
    ProxyRefused,
    fetch_json,
    parse_socks5h,
)

CRED = "socks5://alice:s3cr3t@gate.example.com:10000"

# A DIFFERENT credential, for the tests about two channels disagreeing. Its
# host differs visibly from CRED's so a mix-up is legible in a failure message.
OTHER_CRED = "socks5://bob:0th3r@relay.example.net:20000"


@pytest.fixture(autouse=True)
def _no_ambient_credential(monkeypatch):
    """Unset ``PERSONA_TEST_PROXY`` for EVERY test in this file.

    ⚠️ NOT tidiness — this fixture is load-bearing, and it was written after
    watching its absence break three tests that had passed for months.

    The guard reads two channels now (PS-145), and the CI/dev container this
    suite runs in EXPORTS a real, working credential in this variable. So a
    test that writes a bad file and asserts the guard refuses would be handed
    the ambient variable as a fallback, prove the exit through it, and NOT
    refuse. Measured, not theorised: with the variable live,
    ``test_a_missing_credential_file_refuses_the_run``,
    ``test_an_empty_credential_file_refuses_the_run`` and
    ``test_a_credential_that_is_not_socks5_refuses_the_run`` all failed with
    "DID NOT RAISE ExitNotProven".

    Those tests are about THE FILE's disposition, so the other channel is
    removed and each test that wants it puts it back explicitly. Without this,
    the file-half assertions would quietly become assertions about whichever
    container the suite happened to run in — green here, green there, and
    testing nothing in either place.
    """
    monkeypatch.delenv(exit_guard.ENVIRONMENT_CREDENTIAL_VAR, raising=False)


# --- the credential ---------------------------------------------------------


def test_a_missing_credential_file_refuses_the_run(tmp_path):
    missing = str(tmp_path / "nope.txt")
    with pytest.raises(ExitNotProven) as exc:
        load_credential(missing)
    assert "refusing to fall back" in str(exc.value).lower()


def test_an_empty_credential_file_refuses_the_run(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("   \n", encoding="utf-8")
    with pytest.raises(ExitNotProven) as exc:
        load_credential(str(path))
    assert "empty" in str(exc.value).lower()


def test_a_credential_that_is_not_socks5_refuses_the_run(tmp_path):
    path = tmp_path / "http.txt"
    path.write_text("http://user:pass@proxy.example:8080", encoding="utf-8")
    with pytest.raises(ExitNotProven):
        load_credential(str(path))


def test_the_credential_scheme_is_rewritten_to_socks5h(tmp_path):
    """socks5 resolves the checker's hostname LOCALLY, leaking a DNS query
    naming the checker being read. socks5h sends the name for the exit."""
    path = tmp_path / "cred.txt"
    path.write_text(CRED + "\n", encoding="utf-8")
    assert load_credential(str(path)).startswith("socks5h://")


def test_an_already_socks5h_credential_is_left_alone(tmp_path):
    path = tmp_path / "cred.txt"
    path.write_text("socks5h://alice:s3cr3t@gate.example.com:10000", encoding="utf-8")
    assert load_credential(str(path)).count("socks5h") == 1


# --- PS-145: the credential arrives on TWO channels -------------------------
#
# One channel failing must not stop a live reading. Both have been observed
# failing INDEPENDENTLY on this fleet within one hour, almost exactly
# complementarily — `persona-planner` had the file and an EMPTY variable,
# `persona-reviewer` (freshly recreated) had the variable and NO file. Whoever
# looked had one of them and nobody had both, which is why it went unnoticed.
#
# The tests below are about the SECOND channel, so each one sets the variable
# explicitly — the autouse fixture at the top of this file removes the
# container's ambient credential, without which every assertion here would be
# about whichever machine the suite happened to run on.


def _set_env(monkeypatch, value):
    monkeypatch.setenv(exit_guard.ENVIRONMENT_CREDENTIAL_VAR, value)


def test_the_environment_supplies_the_credential_when_there_is_NO_file(
    tmp_path, monkeypatch
):
    """THE TICKET'S HEADLINE BEHAVIOUR: no file at all, and the run proceeds.

    This is the `persona-reviewer` case measured on 2026-08-24 — a freshly
    recreated container whose file had died with the overlay while the
    variable survived. Before this change the guard read one source, so this
    container could not take a reading at all.

    Asserted on the RESULT, not on the code path: the credential comes back,
    in socks5h form, from a directory that demonstrably contains no file.
    """
    missing = str(tmp_path / "definitely-not-here.txt")
    assert not os.path.exists(missing)
    _set_env(monkeypatch, CRED)

    assert load_credential(missing) == (
        "socks5h://alice:s3cr3t@gate.example.com:10000"
    )


def test_the_environment_credential_is_rewritten_to_socks5h_TOO(
    tmp_path, monkeypatch
):
    """The rewrite is one rule, not one rule per channel.

    The ticket says to CONFIRM this rather than assume it, because the two
    channels are populated by different mechanisms and could in principle
    carry different shapes. Measured in the container: file and variable were
    byte-identical (sha256 matched), both plain `socks5://`.

    A second source must not become a second place that guesses at schemes —
    plain `socks5` resolves the checker's hostname locally and leaks a DNS
    query naming the checker being read.
    """
    _set_env(monkeypatch, CRED)
    resolved = load_credential(str(tmp_path / "nope.txt"))
    assert resolved.startswith("socks5h://")
    assert resolved.count("socks5h") == 1


def test_an_EMPTY_environment_variable_refuses_rather_than_going_direct(
    tmp_path, monkeypatch
):
    """⚠️ THE MOST IMPORTANT TEST IN THIS FILE'S PS-145 SECTION.

    An empty value is treated as ABSENT, never as "unset means no proxy is
    needed". The variable HAS been observed empty in production containers, so
    this is the EXPECTED case rather than an edge one.

    It matters because an empty proxy value does not error — `curl -x ""`
    connects DIRECTLY and returns perfectly parseable JSON of the operator's
    real address. That is exactly the wrong-but-plausible reading this whole
    module exists to prevent: a wrong result looks like data.
    """
    _set_env(monkeypatch, "")
    with pytest.raises(ExitNotProven) as exc:
        load_credential(str(tmp_path / "nope.txt"))
    assert "refusing to fall back" in str(exc.value).lower()


def test_a_WHITESPACE_environment_variable_is_also_absent(
    tmp_path, monkeypatch
):
    """"Set to nothing" is not a weaker "set" — same refusal as empty."""
    _set_env(monkeypatch, "   \n\t ")
    with pytest.raises(ExitNotProven):
        load_credential(str(tmp_path / "nope.txt"))


def test_a_credential_in_NEITHER_source_still_ends_the_run(
    tmp_path, monkeypatch
):
    """The refusal this module already had, preserved exactly.

    The ticket is explicit that adding a second SOURCE must not add a second
    OUTCOME. With both channels unusable the run ends the same way it always
    did, with the same recorded reason.
    """
    monkeypatch.delenv(exit_guard.ENVIRONMENT_CREDENTIAL_VAR, raising=False)
    with pytest.raises(ExitNotProven) as exc:
        load_credential(str(tmp_path / "nope.txt"))
    assert "refusing to fall back to a direct connection" in str(exc.value).lower()


def test_the_refusal_NAMES_BOTH_CHANNELS_and_how_each_failed(
    tmp_path, monkeypatch
):
    """Because this ticket exists precisely because nobody had both halves.

    "The file is missing" sends an operator to fix a file when the variable
    was the empty one. With two channels the refusal must account for both,
    or it points at the wrong half of a complementary failure.
    """
    _set_env(monkeypatch, "")
    missing = str(tmp_path / "nope.txt")
    with pytest.raises(ExitNotProven) as exc:
        load_credential(missing)
    message = str(exc.value)
    assert missing in message
    assert exit_guard.ENVIRONMENT_CREDENTIAL_VAR in message
    # And it says the variable was EMPTY, not merely that it did not work —
    # an empty variable and an unset one need different fixes.
    assert "empty" in message.lower()


# --- which source won is VISIBLE -------------------------------------------


def test_the_file_WINS_when_both_channels_hold_a_credential(
    tmp_path, monkeypatch
):
    """Precedence is the file, and it is not arbitrary.

    The file is bind-mounted from the host, so the operator's rotation reaches
    a running container immediately. The variable is fixed when the container
    is created and cannot be updated in place, so it goes stale SILENTLY — and
    a stale credential is exactly the thing that looks like it is working.
    When they disagree, believe the channel that can be rotated.
    """
    path = tmp_path / "cred.txt"
    path.write_text(CRED, encoding="utf-8")
    _set_env(monkeypatch, OTHER_CRED)

    resolved = exit_guard.resolve_credential(str(path))
    assert resolved.source == exit_guard.SOURCE_FILE
    assert "gate.example.com" in resolved.proxy_url
    assert "relay.example.net" not in resolved.proxy_url


def test_a_DISAGREEMENT_between_the_two_channels_is_REPORTED(
    tmp_path, monkeypatch
):
    """Silent precedence between two live credentials is how an operator ends
    up debugging a reading taken through a proxy they thought they'd replaced.

    With a host mount on one side and a container-frozen variable on the
    other, the two can genuinely diverge — so this is a real state, not a
    defensive branch.
    """
    path = tmp_path / "cred.txt"
    path.write_text(CRED, encoding="utf-8")
    _set_env(monkeypatch, OTHER_CRED)

    resolved = exit_guard.resolve_credential(str(path))
    assert resolved.diverged is True
    assert "DISAGREE" in resolved.detail
    # It names the winner, so the report is actionable rather than just alarming.
    assert exit_guard.SOURCE_FILE in resolved.detail


def test_two_channels_that_AGREE_are_not_reported_as_a_disagreement(
    tmp_path, monkeypatch
):
    """The container measured for this ticket had both channels byte-identical
    — the NORMAL state. It must not read as a conflict."""
    path = tmp_path / "cred.txt"
    path.write_text(CRED, encoding="utf-8")
    _set_env(monkeypatch, CRED)

    resolved = exit_guard.resolve_credential(str(path))
    assert resolved.diverged is False
    assert "DISAGREE" not in resolved.detail


def test_the_run_records_that_it_fell_back_to_the_environment(
    tmp_path, monkeypatch
):
    """Which source won is visible even when only one was usable — otherwise
    a reading taken through the fallback is indistinguishable from one taken
    through the file."""
    _set_env(monkeypatch, CRED)
    resolved = exit_guard.resolve_credential(str(tmp_path / "nope.txt"))
    assert resolved.source == exit_guard.SOURCE_ENVIRONMENT
    assert exit_guard.ENVIRONMENT_CREDENTIAL_VAR in resolved.detail


# --- the new source must not become the path that bypasses redact() ---------


def test_NO_channel_leaks_the_credential_into_the_provenance_it_records(
    tmp_path, monkeypatch
):
    """`detail` is written into the RECORD, which is committed. It names a
    path or a variable NAME — a coordinate, never a value."""
    path = tmp_path / "cred.txt"
    path.write_text(CRED, encoding="utf-8")
    _set_env(monkeypatch, OTHER_CRED)

    for resolved in (
        exit_guard.resolve_credential(str(path)),
        exit_guard.resolve_credential(str(tmp_path / "nope.txt")),
    ):
        assert "s3cr3t" not in resolved.detail
        assert "0th3r" not in resolved.detail
        assert "alice" not in resolved.detail
        assert "bob" not in resolved.detail


def test_a_refusal_message_never_carries_either_channels_credential(
    tmp_path, monkeypatch
):
    """The refusing paths are the easy ones to forget, and they are the ones
    that get printed and pasted into tickets."""
    path = tmp_path / "cred.txt"
    # Malformed on BOTH channels, so both dispositions reach the message.
    path.write_text("http://alice:s3cr3t@proxy.example:8080", encoding="utf-8")
    _set_env(monkeypatch, "http://bob:0th3r@relay.example.net:8080")

    with pytest.raises(ExitNotProven) as exc:
        load_credential(str(path))
    message = str(exc.value)
    assert "s3cr3t" not in message
    assert "0th3r" not in message


# --- PS-160: an unreadable file says WHY, and still redacts -----------------
#
# The arm reported only `OSError`/`IsADirectoryError` and threw the message
# away, so "permission denied" and "is a directory" arrived identical. The
# tests below are driven through the REAL filesystem rather than a raised
# stub: what reaches this arm is a property of `open()`, and a stub would let
# the arm be tested on an exception `open()` never actually produces.
#
# ⚠️ THREE shapes reach it, and this enumeration said TWO until PS-166 — the
# omission is very likely WHY the third went unhandled for a release. The two
# below are the `OSError` pair; the third is a file of UNDECODABLE BYTES,
# which raises `UnicodeDecodeError` from the `.read()`, NOT from `open()`.
# It is easy to miss precisely because it does not look like an I/O failure:
# `UnicodeDecodeError` inherits from `ValueError`, so for a release it was not
# caught by an `except OSError` at all and escaped as a raw traceback. It is
# covered in the PS-166 section further down; the arm is now
# `except (OSError, UnicodeDecodeError)`.
#
# What does NOT reach it: `_from_file` tests `os.path.exists` FIRST, and ELOOP
# (`Errno 40`) and name-too-long (`Errno 36`) both answer False there —
# measured — so they take the "no proxy credential at" arm. A test built on
# those would assert this arm's wording against text this arm did not write.
# ⚠️ Note the binary file is NOT excluded that way: it EXISTS, so it passes
# the `os.path.exists` gate and reaches the read. That is the asymmetry the
# original "only these two" reading missed.


def _unreadable_dir(tmp_path):
    """A path that EXISTS and cannot be read: a directory where a file goes."""
    path = tmp_path / "cred.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()
    return str(path)


def _unreadable_perms(tmp_path):
    """A path that exists and denies reading. Requires a non-root uid."""
    path = tmp_path / "cred.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CRED, encoding="utf-8")
    path.chmod(0)
    return str(path)


# ⚠️ WHY FOUR TESTS BELOW DECLINE TO RUN ON WINDOWS, AND WHAT IS *NOT* GIVEN
# UP BY THAT.
#
# Both markers guard the STAGING, never the contract. The every-platform pair
# at the end of this section re-asserts the same redaction guarantee through
# the seam directly, so nothing below is proven on POSIX only.
#
# The first form of this file shipped `os.geteuid() == 0` bare and took the
# ENTIRE 88-test file down on Windows CI with an AttributeError at COLLECTION
# time — not two skips, an interrupted run. `tests/test_browser_env_policy.py`
# had already written the fix down; this is it.
_unprivileged_posix = pytest.mark.skipif(
    # hasattr short-circuits: os.geteuid does NOT exist on Windows, and a
    # skipif condition is evaluated at COLLECTION time, so calling it
    # unguarded fails the whole job rather than one test.
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="POSIX-only STAGING: root reads a mode-000 file regardless, and "
           "Windows does not deny reads through POSIX mode bits — the "
           "unreadable file this needs cannot be built there",
)

# The redaction tests need a path that is genuinely NAMED like a proxy URL, so
# the secret reaches the message from `open()` itself rather than from a string
# the test wrote. `redact` matches `scheme://user:pass@host`, so such a name
# REQUIRES a `:` — which is not a legal NTFS filename character (it is the
# alternate-data-stream separator). The staging is unbuildable on Windows;
# `mkdir` raises OSError before the assertion is reached.
_credential_shaped_path = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only STAGING: a credential-NAMED path requires ':', which "
           "is illegal in an NTFS filename",
)

# The directory arm's CLASS NAME is the platform's, not this module's. Opening
# a directory is EISDIR on POSIX (`IsADirectoryError`, prose "Is a directory")
# and access-denied on Windows (`PermissionError`, WinError 5) — the same arm,
# the same refusal, a different name for the cause. Since the refusal text is
# `f"...({type(exc).__name__}: {exc})"`, any assertion that spells the
# NEIGHBOUR'S name is asserting on the platform.
#
# Guards the STAGING, never the contract, exactly like the two above: what this
# ticket actually claims — that the new class fires for the undecodable file
# and NOT for its neighbour — is asserted UNGUARDED in the companion test of
# each pair, and holds on Windows unchanged (a `PermissionError` is still not a
# `UnicodeDecodeError`, so the contrast still separates the two causes there).
_posix_eisdir = pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX-only STAGING: opening a directory raises EISDIR here and "
           "PermissionError (WinError 5) on Windows — the CLASS NAME is the "
           "platform's, not this arm's",
)


@_unprivileged_posix
def test_an_unreadable_credential_says_WHY_not_merely_THAT(tmp_path):
    """The reason reaches the caller, in the operator's own words.

    Asserted on the ERRNO PROSE (`permission denied`), which only the
    exception's message carries — the class name alone cannot produce it, so
    this cannot pass on the old code.
    """
    with pytest.raises(ExitNotProven) as exc:
        load_credential(_unreadable_perms(tmp_path))

    message = str(exc.value).lower()
    assert "could not be read" in message
    assert "permissionerror" in message
    assert "permission denied" in message


@_unprivileged_posix
def test_the_two_unreadable_CAUSES_are_distinguishable_on_sight(tmp_path):
    """THE ROW THAT CARRIES THE FIX.

    A message that fires identically on every failure distinguishes nothing —
    which is the original defect restated. The positive test above passes on
    wording that is merely longer; this one fails unless the text actually
    SEPARATES the two causes an operator has to act on differently (fix a
    mode, versus remove a directory that is sitting where a file belongs).
    """
    with pytest.raises(ExitNotProven) as denied:
        load_credential(_unreadable_perms(tmp_path / "a"))
    with pytest.raises(ExitNotProven) as is_dir:
        load_credential(_unreadable_dir(tmp_path / "b"))

    denied_text, is_dir_text = str(denied.value), str(is_dir.value)
    assert denied_text != is_dir_text

    assert "permission denied" in denied_text.lower()
    assert "permission denied" not in is_dir_text.lower()
    assert "is a directory" in is_dir_text.lower()
    assert "is a directory" not in denied_text.lower()


@_credential_shaped_path
def test_an_exception_message_cannot_smuggle_the_credential_past_redact(
    tmp_path,
):
    """THE ONE THAT MUST NOT REGRESS.

    This arm now interpolates text this module did NOT author, so it is the
    one `why` whose safety cannot be argued from "we only ever put a
    coordinate in it". `OSError` names the path it failed on, and a path is
    attacker-shaped in the only sense that matters here: an operator can point
    `--credential` at one that is credential-shaped, and the module would
    write it into a refusal that gets printed, logged and pasted into tickets.

    Driven through the real filesystem: the directory below is genuinely
    NAMED like a proxy URL, so the secret reaches the exception message from
    `open()` itself rather than from a string this test wrote.
    """
    # `socks5://alice:s3cr3t@gate...` as a real path. POSIX collapses the
    # doubled separator when resolving, but `open()` reports the name AS
    # PASSED — which is what lands in the message.
    nest = tmp_path / "socks5:" / "alice:s3cr3t@gate.example.com:10000"
    nest.mkdir(parents=True)
    passed = f"{tmp_path}/socks5://alice:s3cr3t@gate.example.com:10000"

    with pytest.raises(ExitNotProven) as exc:
        load_credential(passed)

    message = str(exc.value)
    # The failure still names itself...
    assert "is a directory" in message.lower()
    # ...and the credential did not survive into it.
    assert "s3cr3t" not in message
    assert "***:***@" in message


@_credential_shaped_path
def test_the_FALLBACK_report_redacts_the_unreadable_files_message_too(
    tmp_path, monkeypatch
):
    """The second reader of `why`, which is the easier one to miss.

    When the file is unusable and the ENVIRONMENT answers, the run does not
    refuse — it proceeds and records the file's disposition in `detail`, which
    is written into the COMMITTED record. That is a different code path from
    the refusal above (`detail` at the `else` arm, not the `not usable` arm),
    so redaction proven on one is not proven on the other.
    """
    nest = tmp_path / "socks5:" / "alice:s3cr3t@gate.example.com:10000"
    nest.mkdir(parents=True)
    passed = f"{tmp_path}/socks5://alice:s3cr3t@gate.example.com:10000"
    _set_env(monkeypatch, OTHER_CRED)

    resolved = exit_guard.resolve_credential(passed)

    # The run proceeded on the other channel...
    assert resolved.source == exit_guard.SOURCE_ENVIRONMENT
    # ...the record still says why the file was unusable...
    assert "is a directory" in resolved.detail.lower()
    # ...and neither channel's secret is in the text that gets committed.
    assert "s3cr3t" not in resolved.detail
    assert "0th3r" not in resolved.detail


# --- the same guarantee, on EVERY platform ----------------------------------
#
# THE WINDOWS-RUNNABLE HALF, and the reason the four tests above may decline to
# run without a guarantee being dropped. What is POSIX-only up there is the
# STAGING — a mode-000 file, and a path NAMED like a proxy URL — not the
# contract. Both are ways of getting a credential-shaped string into the
# exception message from OUTSIDE this module; neither is the claim being made.
#
# The claim is narrower and platform-independent: WHATEVER text arrives in that
# `OSError`, both readers of `why` put it through `redact` before it is shown.
# That rests on exactly one seam — `open()` raising `OSError` inside
# `_from_file` — so these stage that seam directly and assert the same thing
# everywhere, including on a filesystem that cannot express either POSIX
# staging.
#
# The trade is stated rather than hidden: the message here is one this TEST
# wrote, so unlike the POSIX pair these do not also prove the secret can arrive
# from `open()` unaided. That is why both halves exist and neither replaces the
# other.


def _open_raising(monkeypatch, exc):
    """Make `open()` raise `exc` for the credential read, and only for it."""
    real_open = builtins.open

    def _fake(file, *args, **kwargs):
        if str(file).endswith("cred.txt"):
            raise exc
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _fake)


def test_a_credential_in_the_exceptions_message_is_redacted_on_every_platform(
    tmp_path, monkeypatch
):
    """The refusal path's redaction, staged through the seam not the filesystem."""
    path = tmp_path / "cred.txt"
    path.write_text(CRED, encoding="utf-8")
    # An OSError whose message carries a credential-shaped URL — the shape the
    # POSIX tests obtain from a real path, obtained here from the seam.
    _open_raising(monkeypatch, OSError(f"I/O error reading {CRED}"))

    with pytest.raises(ExitNotProven) as exc:
        load_credential(str(path))

    message = str(exc.value)
    # The failure still says what went wrong...
    assert "i/o error" in message.lower()
    # ...and the credential did not survive into it.
    assert "s3cr3t" not in message
    assert "alice" not in message
    assert "***:***@" in message


def test_the_FALLBACK_reports_redaction_holds_on_every_platform_too(
    tmp_path, monkeypatch
):
    """The second reader (`detail`, which is COMMITTED), same staging."""
    path = tmp_path / "cred.txt"
    path.write_text(CRED, encoding="utf-8")
    _set_env(monkeypatch, OTHER_CRED)
    _open_raising(monkeypatch, OSError(f"I/O error reading {CRED}"))

    resolved = exit_guard.resolve_credential(str(path))

    assert resolved.source == exit_guard.SOURCE_ENVIRONMENT
    assert "i/o error" in resolved.detail.lower()
    assert "s3cr3t" not in resolved.detail
    assert "alice" not in resolved.detail
    assert "0th3r" not in resolved.detail
    assert "***:***@" in resolved.detail


# --- PS-166: a file of UNDECODABLE BYTES is "unusable", not "we crashed" ----
#
# THE THIRD SHAPE the PS-160 enumeration above missed. `open(..., "r",
# encoding="utf-8")` does not fail on binary content — the `.read()` does, with
# `UnicodeDecodeError`, which inherits from `ValueError` and is therefore NOT
# caught by `except OSError`. So it escaped the guard entirely and reached the
# operator as a raw traceback with **exit 1** — the code `checker_cli`'s own
# docstring reserves for "the run itself broke", explicitly "distinct from 2 so
# 'we declined to measure' is never confused with 'we crashed'". An unreadable
# credential is definitionally the latter.
#
# ⚠️ The staging is REAL BYTES ON THE REAL FILESYSTEM, never a raised stub —
# the house rule stated in the PS-160 header, and it bites harder here: the
# whole defect is that the exception's real ANCESTRY (`ValueError`, not
# `OSError`) is not what the arm assumed. A stub raising a hand-built error
# would let this be "tested" against an exception `open()`/`read()` never
# actually produces, which is precisely how the class was mis-filed to begin
# with.

# `user:` then bytes that are not valid UTF-8 in any position. 0xff is an
# invalid START byte, so the failure is unambiguous rather than a truncation
# artefact — and the prefix makes the file look credential-ish, so nothing here
# passes by virtue of the file being obviously junk.
_UNDECODABLE = b"user:\xff\xfe\x00pass\n"


def _binary_credential(tmp_path):
    """A path that EXISTS, is readable, and is not decodable as UTF-8."""
    path = tmp_path / "cred.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_UNDECODABLE)
    return str(path)


def test_a_binary_credential_file_REFUSES_rather_than_raising(tmp_path):
    """AC1, at the public seam: the guard refuses, it does not explode.

    `ExitNotProven` is this module's refusal; a `UnicodeDecodeError` escaping
    to the caller is the defect. `pytest.raises(ExitNotProven)` alone would NOT
    catch the regression — an unhandled `UnicodeDecodeError` fails the test as
    an error rather than passing, which is the point: the assertion is that the
    refusal is REACHED.
    """
    with pytest.raises(ExitNotProven) as exc:
        load_credential(_binary_credential(tmp_path))

    message = str(exc.value)
    assert "could not be read" in message.lower()
    # Names the CAUSE (AC3) — the class, so the operator can tell this from a
    # permissions problem without guessing.
    assert "UnicodeDecodeError" in message
    # ...and the offending PATH, so they know WHICH file to go and look at.
    assert "cred.txt" in message


def test_the_internal_candidate_carries_the_refusal_shape_not_an_exception(
    tmp_path,
):
    """AC1 at `_from_file` itself — the module's normal refusal shape.

    The seam test above proves the public behaviour; this one pins that the
    refusal is expressed the SAME way every other unreadable-file arm expresses
    it (a `_Candidate` with `proxy_url=None` and a populated `why`), rather
    than by some new bespoke path that happens to look right from outside.
    """
    candidate = exit_guard._from_file(_binary_credential(tmp_path))

    assert candidate.proxy_url is None
    assert candidate.source == exit_guard.SOURCE_FILE
    assert "UnicodeDecodeError" in candidate.why


def test_the_binary_and_OSError_causes_are_distinguishable_on_sight(tmp_path):
    """AC4 — THE CONTRAST ROW, mirroring the PS-160 test of the same name.

    A message that fires on everything distinguishes nothing. The positive
    tests above pass on wording that merely got longer; this one fails unless
    the text actually SEPARATES the new cause from its neighbour — and, in the
    other direction, unless widening the arm left the `OSError` wording
    untouched. The negative half is the half that catches an over-broad fix:
    an `except Exception` that reported one generic class for both would sail
    through every assertion except these.
    """
    with pytest.raises(ExitNotProven) as binary:
        load_credential(_binary_credential(tmp_path / "a"))
    with pytest.raises(ExitNotProven) as is_dir:
        load_credential(_unreadable_dir(tmp_path / "b"))

    binary_text, is_dir_text = str(binary.value), str(is_dir.value)
    assert binary_text != is_dir_text

    # The new class fires for the binary file...
    assert "UnicodeDecodeError" in binary_text
    # ...and NOT for the neighbouring OSError input, whose wording is unchanged.
    assert "UnicodeDecodeError" not in is_dir_text


@_posix_eisdir
def test_the_OSError_arms_own_PROSE_survives_the_widening(tmp_path):
    """The POSIX-named half of the contrast above, split out rather than lost.

    The companion test asserts the part that is about THIS ticket — the new
    class fires for the undecodable file and not for its neighbour — and it
    runs everywhere. This one adds the part that is about the PLATFORM: that
    the neighbour still reports itself in the operator's own errno prose,
    unchanged by the widening. `IsADirectoryError`/"is a directory" is EISDIR,
    which Windows does not raise here (it answers PermissionError), so the
    wording is the platform's and only this half is staged behind the marker.
    """
    with pytest.raises(ExitNotProven) as binary:
        load_credential(_binary_credential(tmp_path / "a"))
    with pytest.raises(ExitNotProven) as is_dir:
        load_credential(_unreadable_dir(tmp_path / "b"))

    binary_text, is_dir_text = str(binary.value), str(is_dir.value)

    # The directory arm's own prose is intact — the widening did not relabel it...
    assert "is a directory" in is_dir_text.lower()
    # ...and did not smear it across the new arm either.
    assert "is a directory" not in binary_text.lower()


def test_an_undecodable_file_falls_back_to_the_environment(
    tmp_path, monkeypatch
):
    """AC5 — the availability consequence, which is the real severity here.

    The file channel is consulted FIRST, so before the fix this did not merely
    print an ugly traceback: it KILLED a run that had a perfectly good
    credential in the environment. The missing-file and directory arms already
    degrade into the fallback; this one crashed instead. Measured on the real
    CLI at the time of the fix: directory → exit 0, binary → exit 1.

    So the assertion is that the run PROCEEDS, on the other channel, with the
    file's disposition recorded in the committed `detail`.
    """
    _set_env(monkeypatch, OTHER_CRED)

    resolved = exit_guard.resolve_credential(_binary_credential(tmp_path))

    # The run proceeded, on the environment's credential...
    assert resolved.source == exit_guard.SOURCE_ENVIRONMENT
    assert resolved.proxy_url == (
        "socks5h://bob:0th3r@relay.example.net:20000"
    )
    # ...and the record still says why the file was unusable.
    assert "UnicodeDecodeError" in resolved.detail


@_credential_shaped_path
def test_the_undecodable_arms_message_is_redacted_like_the_OSError_arms(
    tmp_path,
):
    """AC3's redaction half — the constraint that must not be reasoned away.

    `UnicodeDecodeError`'s own message is tame: it carries the offending byte
    in hex and its position, never the path and never file content. That is a
    reason to be UNWORRIED, not a reason to bypass `redact` — this module's
    doctrine is that the risky part is the one nobody thought of, and widening
    an `except` arm is exactly the change that could later admit an exception
    whose message is not so tame.

    But the refusal text is not only the exception: this module interpolates
    the PATH beside it, and an operator can point `--credential` at a
    credential-shaped path. So there IS a secret on this path, from the
    module's own f-string. Mirrors
    `test_an_exception_message_cannot_smuggle_the_credential_past_redact`.
    """
    # A real FILE (not a directory, as in the OSError mirror) named like a
    # proxy URL, whose CONTENT is undecodable — so the path reaches the message
    # while the failure is the new arm's, not the old one's.
    nest = tmp_path / "socks5:"
    nest.mkdir(parents=True)
    (nest / "alice:s3cr3t@gate.example.com:10000").write_bytes(_UNDECODABLE)
    passed = f"{tmp_path}/socks5://alice:s3cr3t@gate.example.com:10000"

    with pytest.raises(ExitNotProven) as exc:
        load_credential(passed)

    message = str(exc.value)
    # The failure still names itself...
    assert "UnicodeDecodeError" in message
    # ...and the credential did not survive into it.
    assert "s3cr3t" not in message
    assert "***:***@" in message


# --- PS-166: the CONTRACT, driven through the real operator entry point -----
#
# Everything above asserts on the library seam. The ticket's headline claim is
# about an EXIT CODE, which no in-process test can observe: `checker_cli`'s 1
# and 2 are produced by the process, and the traceback is produced by the
# interpreter unwinding. Asserting on the driven process is the only way to
# assert on what an operator actually sees.
#
# ⚠️ The subprocess inherits this shell's environment, and the CI/dev container
# EXPORTS a real credential in `PERSONA_TEST_PROXY` — the same trap the autouse
# fixture at the top of this file exists for, which does NOT reach a subprocess
# because it patches this process's `os.environ`. Removed explicitly below;
# without that, the fallback would answer and the run would exit 0 having
# measured nothing about refusal.

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _drive_checker_cli(credential_path):
    """Run the real CLI in a child process, with NO ambient credential."""
    env = dict(os.environ)
    env.pop(exit_guard.ENVIRONMENT_CREDENTIAL_VAR, None)
    return subprocess.run(
        [sys.executable, "-m", "src.services.verify.checker_cli",
         "read", "--credential", credential_path],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        # Refusal is decided before any network work, so this is generous.
        # It is a guard against a hang becoming a silent pass, not a budget.
        timeout=120, encoding="utf-8",
    )


def test_the_CLI_exits_2_and_shows_no_traceback_for_a_binary_credential(
    tmp_path,
):
    """AC2 — THE ROW THAT CARRIES THIS TICKET.

    Asserted on the driven process's exit code and output, not on the diff.
    Before the fix this was exit 1 with a raw `UnicodeDecodeError` traceback;
    the contract in `checker_cli`'s docstring reserves 2 for "REFUSED — ... a
    missing credential, AN UNUSABLE ONE, a refused connection ... all land
    here", and 1 for "the run itself broke".
    """
    result = _drive_checker_cli(_binary_credential(tmp_path))
    output = result.stdout + result.stderr

    assert result.returncode == 2, (
        f"expected REFUSED(2), got {result.returncode}. Output:\n{output}"
    )
    # Exit 1 came with an unhandled exception; a refusal must not.
    assert "Traceback" not in output
    assert "REFUSED" in output


def test_the_CLI_names_the_undecodable_cause_but_not_for_the_OSError_input(
    tmp_path,
):
    """AC2 + AC4 through the operator's eyes: both inputs refuse, and the
    refusals do not read identically.

    The directory arm is the control. It refused with 2 before this ticket and
    must still do so, with its wording untouched — if a fix made every
    unreadable file report the same new class, the operator would have lost the
    distinction this module's messages exist to draw.
    """
    binary = _drive_checker_cli(_binary_credential(tmp_path / "a"))
    is_dir = _drive_checker_cli(_unreadable_dir(tmp_path / "b"))

    # Both are refusals, neither is a crash.
    assert binary.returncode == 2
    assert is_dir.returncode == 2

    binary_out = binary.stdout + binary.stderr
    is_dir_out = is_dir.stdout + is_dir.stderr

    assert "UnicodeDecodeError" in binary_out
    assert "UnicodeDecodeError" not in is_dir_out


@_posix_eisdir
def test_the_CLI_still_names_the_OSError_inputs_own_class(tmp_path):
    """The POSIX-named half of the CLI contrast above, split out not dropped.

    The companion asserts what this ticket claims — both inputs refuse with 2,
    and the new class appears for one and not the other — on every platform.
    What is left here is the platform's own vocabulary: EISDIR is POSIX, and
    Windows reports the same arm as PermissionError (WinError 5). Asserting the
    class name that the OPERATING SYSTEM chose is staging, so it is marked;
    the distinction the ticket restores is not, so it is not.
    """
    is_dir = _drive_checker_cli(_unreadable_dir(tmp_path / "b"))
    is_dir_out = is_dir.stdout + is_dir.stderr

    assert is_dir.returncode == 2
    assert "IsADirectoryError" in is_dir_out


# --- socks5h is enforced, not preferred -------------------------------------


def test_plain_socks5_is_refused_by_the_fetcher():
    """The one that would leak DNS while measuring whether we leak DNS."""
    with pytest.raises(ProxyRefused) as exc:
        parse_socks5h("socks5://alice:s3cr3t@gate.example.com:10000")
    assert "socks5h" in str(exc.value)


def test_the_refusal_message_does_not_carry_the_credential():
    try:
        parse_socks5h(CRED)
    except ProxyRefused as exc:
        assert "s3cr3t" not in str(exc)
        assert "alice" not in str(exc)
    else:  # pragma: no cover
        pytest.fail("expected a refusal")


def test_a_plaintext_checker_url_is_refused():
    with pytest.raises(ProxyRefused):
        fetch_json("http://tls.peet.ws/api/all",
                   proxy_url="socks5h://127.0.0.1:1")


# --- the relay is stopped / nothing is listening ----------------------------


def test_a_dead_relay_fails_the_fetch_rather_than_going_direct():
    """Port 1 on loopback has nothing listening — the "relay stopped" case.

    The assertion that matters is not merely that it raised: it is that the
    checker was NOT fetched some other way. A fetcher that fell back to a
    direct connection would return a perfectly good 200 here.
    """
    with pytest.raises(FetchFailed) as exc:
        fetch_json(
            "https://ipinfo.io/json",
            proxy_url="socks5h://127.0.0.1:1",
            timeout=5,
        )
    # The failure names its cause rather than being a bare "error".
    assert ":" in str(exc.value)
    # And it failed AT THE RELAY, not somewhere past it. The loopback endpoint
    # that refused is named, which is what distinguishes "the relay is down"
    # from "the relay worked and the checker itself refused us" — two outcomes
    # that mean opposite things about whether the exit is usable.
    assert "127.0.0.1:1" in str(exc.value)


def test_a_dead_relay_refuses_the_whole_run():
    with pytest.raises(ExitNotProven) as exc:
        observe_exit("socks5h://127.0.0.1:1", timeout=5)
    message = str(exc.value).lower()
    assert "refusing to fall back to a direct connection" in message


def test_prove_exit_refuses_when_the_relay_is_dead(tmp_path):
    path = tmp_path / "cred.txt"
    path.write_text("socks5://alice:s3cr3t@127.0.0.1:1", encoding="utf-8")
    with pytest.raises(ExitNotProven):
        prove_exit(credential_path=str(path), timeout=5)


# --- the exit answers, but it is the wrong one ------------------------------


def _observe(payload, monkeypatch):
    monkeypatch.setattr(
        exit_guard, "fetch_json", lambda url, **kw: payload
    )
    return observe_exit("socks5h://127.0.0.1:9")


def test_a_non_polish_exit_refuses_the_run(monkeypatch):
    """Geography is not one tagged red — a checker folds it into its
    cross-checks, so the surrounding readings become meaningless."""
    with pytest.raises(ExitNotProven) as exc:
        _observe({"ip": "8.8.8.8", "country": "US"}, monkeypatch)
    assert "US" in str(exc.value)
    assert EXPECTED_COUNTRY in str(exc.value)


def test_a_datacenter_exit_with_no_country_refuses_the_run(monkeypatch):
    with pytest.raises(ExitNotProven):
        _observe({"ip": "8.8.8.8"}, monkeypatch)


def test_an_observation_with_no_address_refuses_the_run(monkeypatch):
    """Country right, address missing: the exit is still unproven."""
    with pytest.raises(ExitNotProven) as exc:
        _observe({"country": "PL"}, monkeypatch)
    assert "no address" in str(exc.value).lower()


def test_an_observation_that_is_not_an_object_refuses_the_run(monkeypatch):
    with pytest.raises(ExitNotProven):
        _observe(["not", "an", "object"], monkeypatch)


def test_a_polish_exit_is_accepted_and_recorded(monkeypatch):
    observed = _observe(
        {
            "ip": "83.175.184.100",
            "country": "PL",
            "region": "Mazovia",
            "city": "Warsaw",
            "org": "AS9141 P4 Sp. z o.o.",
            "timezone": "Europe/Warsaw",
        },
        monkeypatch,
    )
    assert observed.country == "PL"
    assert observed.as_record()["city"] == "Warsaw"


def test_rotation_within_poland_is_not_a_fault(monkeypatch):
    """A DIFFERENT Polish address on a later run is the design, not an error:
    a fixed exit would permanently hide a coupling between a fingerprint
    reading and the address."""
    first = _observe({"ip": "83.175.184.100", "country": "PL"}, monkeypatch)
    second = _observe({"ip": "188.146.33.135", "country": "PL"}, monkeypatch)
    assert first.ip != second.ip
    assert first.country == second.country == "PL"


# --- PS-128: one dead oracle must not refuse a provably good exit -----------
#
# The Python twin of the engine-side rows in `test_verify_checkers.py`. Both
# sites walk a provider list; both must advance on a NON-ANSWER and stop dead
# on a wrong-country ANSWER. They are tested separately because they share no
# code — a defect fixed in one is not fixed in the other, which is exactly how
# the no-country branch below survived the first round.


def _ipwho_payload(country_code="PL", ip="95.49.113.111"):
    """The SECOND provider's dialect, matching the real body captured through
    the mobile exit on 2026-08-23.

    It differs from ipinfo's shape in three ways that all matter to the
    normaliser: ``country`` is the full NAME with the code in ``country_code``,
    the ISP is nested under ``connection``, and ``timezone`` is an OBJECT
    rather than a string. A double that flattened these would prove the
    normaliser works against a shape the provider never sends.
    """
    return {
        "ip": ip,
        "success": True,
        "country": "Poland",
        "country_code": country_code,
        "region": "Masovian Voivodeship",
        "city": "Warsaw",
        "connection": {
            "asn": 5617,
            "org": "Orange Polska Spolka Akcyjna",
            "isp": "Orange Polska Spolka Akcyjna",
        },
        "timezone": {"id": "Europe/Warsaw", "abbr": "CEST", "utc": "+02:00"},
    }


def _observe_scripted(monkeypatch, script):
    """Answer each provider DIFFERENTLY, and record who was actually asked.

    The `_observe` helper above hands the same payload to every provider, so
    it cannot see the loop at all — it would pass identically against the
    single-oracle version this replaced. `script` maps a substring of the
    provider URL to either a payload (returned) or an exception (raised).

    Returns `(result_or_exception, asked)` so a test can assert on the ANSWER
    and on WHICH providers were consulted — the second is what tells a real
    fall-through from a lucky identical payload.
    """
    asked = []

    def fake_fetch_json(url, **kw):
        asked.append(url)
        for fragment, behaviour in script.items():
            if fragment in url:
                if isinstance(behaviour, Exception):
                    raise behaviour
                return behaviour
        raise AssertionError(f"test script has no entry for {url}")

    monkeypatch.setattr(exit_guard, "fetch_json", fake_fetch_json)
    try:
        return observe_exit("socks5h://127.0.0.1:9"), asked
    except ExitNotProven as exc:
        return exc, asked


def test_a_rate_limited_first_provider_falls_through_to_the_second(monkeypatch):
    """THE REGRESSION THIS EXISTS FOR.

    ipinfo.io answered `HTTP 429 Rate limit hit` through the mobile exit — the
    limit attaches to the exit's SHARED address, so it is not ours to clear
    and cannot be retried around. Every reading that run was supposed to take
    was refused over an exit that was provably Polish.

    The assertion is that the observation SUCCEEDS and returns PL, not merely
    that a second URL was visited — which would pass even if the country never
    reached the caller.
    """
    observed, asked = _observe_scripted(
        monkeypatch,
        {
            "ipinfo.io": FetchFailed("HTTP 429 Rate limit hit"),
            "ipwho.is": _ipwho_payload(),
        },
    )

    assert isinstance(observed, Exit), f"refused a healthy exit: {observed}"
    assert observed.country == "PL"
    assert observed.ip == "95.49.113.111"
    # It really did fall through rather than reading the 429 as an answer.
    assert len(asked) == 2


def test_a_provider_that_answers_without_a_country_is_not_an_answer(
    monkeypatch
):
    """A 200 carrying valid JSON and NO country is a NON-ANSWER, so the guard
    must ask the next provider — the same rule the engine-side twin applies.

    This is the sharper half of the 429 case and the one that is easy to miss.
    The normaliser coerces a missing country to "", so without this branch the
    body falls through to the country comparison and refuses a HEALTHY Polish
    exit while reporting it as "(unknown)". That message is actively FALSE
    rather than merely unhelpful, which makes it worse than the rate limit the
    fallback was added to survive.
    """
    observed, asked = _observe_scripted(
        monkeypatch,
        {
            # Shape of a rate-limit/error body: parses fine, says nothing.
            "ipinfo.io": {"ip": "95.49.113.111", "error": "rate limited"},
            "ipwho.is": _ipwho_payload(),
        },
    )

    assert isinstance(observed, Exit), f"refused a healthy exit: {observed}"
    assert observed.country == "PL"
    assert len(asked) == 2


def test_the_second_providers_dialect_is_normalised_not_read_as_Poland(
    monkeypatch
):
    """ipwho.is says `"country": "Poland"` with the CODE in `country_code`.

    Read with ipinfo's key layout that yields "POLAND", which does not equal
    "PL" — so the guard would refuse a healthy Polish exit and say it was in
    the wrong country. The nested fields are asserted too: they must be
    REACHED rather than stringified, or the record beside every reading
    silently carries `{'id': 'Europe/Warsaw', ...}` as its timezone.
    """
    observed, _ = _observe_scripted(
        monkeypatch, {"ipinfo.io": _ipwho_payload()}
    )

    assert isinstance(observed, Exit), f"refused a healthy exit: {observed}"
    assert observed.country == "PL"
    assert observed.timezone == "Europe/Warsaw"
    assert observed.org == "Orange Polska Spolka Akcyjna"
    assert observed.city == "Warsaw"


def test_a_wrong_country_is_NOT_retried_against_a_friendlier_provider(
    monkeypatch
):
    """The fallback is redundancy for REACHABILITY, never a second opinion on
    geography. A provider that answers is authoritative: if the first says US,
    the run ends there. Asking the next one until a Polish answer turns up is
    how a fallback becomes a way to launder a bad exit.
    """
    observed, asked = _observe_scripted(
        monkeypatch,
        {
            "ipinfo.io": {"ip": "8.8.8.8", "country": "US"},
            # Polish, and must never be consulted.
            "ipwho.is": _ipwho_payload(),
        },
    )

    assert isinstance(observed, ExitNotProven)
    assert "US" in str(observed)
    assert len(asked) == 1, "a wrong country was re-shopped to another provider"


def test_every_provider_answering_without_a_country_refuses_the_run(
    monkeypatch
):
    """The list is exhausted, so nothing may be recorded — and the message
    says WHICH of the two causes it was. "Nothing could be reached" and "it
    answered and did not say" point at opposite halves (the proxy/credential
    vs. a rate-limit body), and collapsing them makes an operator chase the
    wrong one.
    """
    observed, asked = _observe_scripted(
        monkeypatch,
        {
            "ipinfo.io": {"ip": "95.49.113.111"},
            "ipwho.is": {"ip": "95.49.113.111"},
        },
    )

    assert isinstance(observed, ExitNotProven)
    assert len(asked) == 2, "the guard stopped before exhausting the list"
    assert "carried no country" in str(observed)
    # NOT the unreachable message: both providers answered.
    assert "could not observe the exit" not in str(observed)


# --- PS-126: a dead sticky session token names ITSELF ------------------------
#
# The credential this project holds pins a sticky session token. When that
# token dies the relay AUTHENTICATES it and then has no exit to allocate:
# auth succeeds, allocation fails. That used to be reported as "the connection
# may be refused, timed out, or the credential unusable" — a message that
# blames the relay and the credential while both are working, and that reads
# identically to a proxy which is simply down. An operator seeing it chases
# the wrong half.
#
# ⚠️ THE PREMISE THIS BLOCK ONCE CARRIED WAS FALSE, AND IT IS WHY THE ROWS
# BELOW ARE NOT SUFFICIENT ON THEIR OWN.
#
# It used to read: "the stage is knowable without a network and without
# touching the fetcher — PySocks raises a different class at each stage and
# `socks_fetch.fetch` wraps it preserving the class name". The first half is
# true at the RAISE SITE. The second half is not true at the CALLER, and the
# difference shipped a dead feature three times.
#
# `socksocket.connect` wraps the whole negotiation in `except socket.error`
# (socks.py:810-814) and re-raises as `GeneralProxyError`. `ProxyError`
# subclasses `OSError`, so that arm SHADOWS the `except ProxyError` arm at
# :817. Driven through a real relay, all eight connect-stage reply codes AND
# an auth rejection arrived identically as `GeneralProxyError` — the class
# name was destroyed one frame before `socks_fetch` ever saw it.
#
# So `socks_fetch` now unwraps `ProxyError.socket_err` (socks.py:59-64) to
# recover the class that describes the failure. See `_reported_failure`.
#
# WHAT THAT MEANS FOR THE ROWS IN THIS SECTION. They drive a hand-authored
# `FetchFailed` text through the guard, so they pin the guard's WORDING GIVEN
# A TEXT — and they cannot establish that the text ever occurs. Every one of
# them passed while the feature was unreachable in production. They are kept
# because message-shape coverage is genuinely cheap here and the wording rules
# are fiddly, but the question "does the branch fire at all?" is answered ONLY
# by the real-relay section further down, which drives real PySocks through
# real `socks_fetch`. Do not let these stand in for it.

# What PySocks 1.7.1 raises at the connect stage, AFTER `socks_fetch` unwraps
# the `GeneralProxyError` PySocks re-wrapped it in — the message is formatted
# `"{:#04x}: {}"` at socks.py:533. Confirmed against a driven relay, not
# transcribed from the wheel source (which is what got this wrong three times).
_CONNECT_STAGE_TEXT = "SOCKS5Error: 0x01: General SOCKS server failure"
_AUTH_STAGE_TEXT = "SOCKS5AuthError: SOCKS5 authentication failed"


def _observe_raising(monkeypatch, exc):
    """Drive `observe_exit` with a fetcher that always raises — no network.

    The `_observe` helper above RETURNS a payload, so it can only exercise the
    answered paths. This one exercises the unreachable arm, which is where the
    stage distinction lives.
    """
    monkeypatch.setattr(
        exit_guard, "fetch_json", lambda url, **kw: (_ for _ in ()).throw(exc)
    )
    with pytest.raises(ExitNotProven) as caught:
        observe_exit("socks5h://127.0.0.1:9")
    return str(caught.value)


def test_a_dead_session_token_reports_the_stage_not_a_generic_failure(
    monkeypatch
):
    """THE REGRESSION THIS EXISTS FOR.

    A connect-stage refusal means the proxy took the credential and then could
    not give this run an exit. The message must say that happened — naming the
    stage and pointing at the stale sticky session token — rather than
    offering the operator a list of three things to check, two of which are
    provably fine.
    """
    message = _observe_raising(monkeypatch, FetchFailed(_CONNECT_STAGE_TEXT))

    lowered = message.lower()
    # It says auth PASSED and allocation failed — the two halves of the stage.
    assert "authenticated" in lowered
    assert "allocate an exit" in lowered
    # And it names the likely cause an operator can actually act on.
    assert "sticky session token" in lowered
    # It must NOT fall back to the wording that blames the relay/credential.
    assert "the credential unusable" not in lowered


def test_the_connect_stage_message_is_not_the_dead_relay_message(monkeypatch):
    """THE CONTRAST THAT MAKES IT A DISTINCTION.

    `ProxyConnectionError` is the relay-unreachable case: nothing was
    negotiated, so no stage was reached. If the new wording fired here too it
    would distinguish nothing — which is precisely the defect, restated.
    """
    relay = _observe_raising(
        monkeypatch,
        FetchFailed("ProxyConnectionError: Error connecting to SOCKS5 proxy"),
    )

    lowered = relay.lower()
    assert "sticky session token" not in lowered
    assert "authenticated" not in lowered
    # It keeps the existing generic wording, which is CORRECT here.
    assert "the credential unusable" in lowered


def test_the_connect_stage_message_is_not_the_timeout_message(monkeypatch):
    """The other half of the contrast. A timeout reached no stage either."""
    timed_out = _observe_raising(
        monkeypatch, FetchFailed("timeout: timed out")
    )

    assert "sticky session token" not in timed_out.lower()
    assert "the credential unusable" in timed_out.lower()


def test_an_auth_failure_is_not_reported_as_a_dead_session_token(monkeypatch):
    """The discriminator that a careless substring match would break.

    "SOCKS5Error" is not a substring of "SOCKS5AuthError" — the names diverge
    at the character after `SOCKS5` — so a REJECTED credential must keep
    reporting as a rejected credential. Reporting it as "auth succeeded"
    would be worse than the generic message it replaced: actively false about
    the one thing it claims to have observed.
    """
    auth = _observe_raising(monkeypatch, FetchFailed(_AUTH_STAGE_TEXT))

    lowered = auth.lower()
    assert "authenticated this run" not in lowered
    assert "sticky session token" not in lowered
    # An auth failure genuinely IS "the credential unusable".
    assert "the credential unusable" in lowered


# --- the two cases that passed while being wrong (PR #105 audit) ------------
#
# Both were invisible to the suite above because it pinned exactly one reply
# code (0x01) and never drove a non-verdict body through the guard. They are
# the reason the predicate reads a CLASS NAME AT A POSITION rather than a
# substring anywhere in the collected prose.

# A DESTINATION-side reply. PySocks raises this after the relay has already
# ALLOCATED an exit — the target was unreachable from it. Same class, same
# stage, opposite cause to 0x01.
_DESTINATION_SIDE_TEXT = "SOCKS5Error: 0x04: Host unreachable"


def test_a_destination_side_reply_does_not_blame_the_session_token(monkeypatch):
    """The message may not claim a cause the reply code contradicts.

    0x03/0x04/0x05/0x06 are raised AFTER an exit was allocated: the relay
    authenticated, gave this run an exit, and the destination was unreachable
    or refused from it. Reporting "refused to allocate an exit ... stale sticky
    session token" there is a CONFIDENT FALSEHOOD — it points the operator at a
    token that is working, and this module's standard is that an actively
    misleading message is worse than a merely unhelpful one.

    What is still true for every code in the class is that authentication
    PASSED, so that half is asserted rather than dropped.
    """
    message = _observe_raising(
        monkeypatch, FetchFailed(_DESTINATION_SIDE_TEXT)
    )
    lowered = message.lower()

    # The half that IS known from the class: auth passed, connect stage failed.
    assert "authenticated" in lowered
    assert "0x04" in lowered
    # The half that is NOT known: this code does not implicate the token.
    assert "sticky session token" not in lowered
    # And it must not silently fall back to blaming the credential either —
    # we know from the class that the credential was accepted.
    assert "the credential unusable" not in lowered


# --- the third group: neither relay-side nor destination-side --------------
#
# 0x07/0x08 are a PROTOCOL-level disagreement — the module's own comment calls
# them "neither of the above". Before PR #105's third round the code had two
# branches for three groups, so these fell into the `else` and inherited prose
# written for the destination-side group: "raised AFTER an exit was allocated
# ... the session token is not implicated and re-minting it would not help".
# For a protocol-level rejection no exit need have been allocated at all.
#
# These rows are the reason the group has a branch. Both PASSED while being
# wrong, because a group with no branch is invisible to a suite that pins one
# row per branch — the same way pinning only 0x01 hid round 1.
_BAD_COMMAND_TEXT = "SOCKS5Error: 0x07: Command not supported"
_BAD_ADDRESS_TYPE_TEXT = "SOCKS5Error: 0x08: Address type not supported"

# A connect-stage failure whose reply code cannot be read. `_connect_stage_code`
# returns None and promises in its docstring that this is reported "as an
# unattributed connect-stage failure rather than guessed at".
_UNPARSEABLE_CODE_TEXT = "SOCKS5Error: the relay said no"


@pytest.mark.parametrize(
    "failure_text, code",
    [(_BAD_COMMAND_TEXT, "0x07"), (_BAD_ADDRESS_TYPE_TEXT, "0x08")],
)
def test_a_protocol_level_reply_attributes_neither_side(
    monkeypatch, failure_text, code
):
    """The third group says LESS, and this asserts that it said less.

    A group whose correct behaviour is to withhold attribution needs a test
    that pins the withholding, or nothing stops it from inheriting a
    neighbour's wording. 0x07/0x08 mean the relay and the client disagreed
    about the request itself — that says nothing about whether an exit was
    allocated, so BOTH causes must go unstated: not the stale session token
    (relay-side prose) and not "raised after an exit was allocated"
    (destination-side prose).

    What the CLASS still establishes is unaffected: PySocks only reaches the
    reply-code read once auth has completed, so "auth passed" is asserted for
    these codes too.
    """
    message = _observe_raising(monkeypatch, FetchFailed(failure_text))
    lowered = message.lower()

    # Still fully earned from the class: auth passed, connect stage failed,
    # and the code the relay actually sent is carried verbatim.
    assert "authenticated" in lowered
    assert code in lowered
    # Neither neighbour's cause may be asserted.
    assert "sticky session token" not in lowered
    assert "after an exit was allocated" not in lowered
    assert "not implicated" not in lowered
    # It says so explicitly rather than trailing off.
    assert "does not say which side failed" in lowered
    # And it does not regress to blaming the credential the class cleared.
    assert "the credential unusable" not in lowered


def test_an_unreadable_reply_code_is_reported_unattributed(monkeypatch):
    """"Reply unreported" and a named cause cannot both be true.

    This is the one that matters most, because the claim it used to invent is
    the INVERSE of this ticket's purpose: it told the operator that re-minting
    the session token would not help, on a run where nothing whatsoever was
    known about the reply code. `_connect_stage_code`'s docstring already
    promises an unattributed report "rather than guessed at"; this pins the
    code to that promise.
    """
    message = _observe_raising(monkeypatch, FetchFailed(_UNPARSEABLE_CODE_TEXT))
    lowered = message.lower()

    # The stage is still known — it came from the class, not from the code.
    assert "authenticated" in lowered
    # It is honest that the code could not be read...
    assert "unreported" in lowered
    # ...and then does not attribute anyway, in either direction.
    assert "sticky session token" not in lowered
    assert "after an exit was allocated" not in lowered
    assert "not implicated" not in lowered
    assert "does not say which side failed" in lowered


def test_the_unattributed_arm_carries_no_credential(monkeypatch):
    """Redaction holds on the NEW arm too, asserted on output.

    Each arm builds its own message, so credential-safety proven on one is not
    proven on another. Pushed through as a credential-shaped string.
    """
    message = _observe_raising(
        monkeypatch,
        FetchFailed(f"SOCKS5Error: 0x07: Command not supported via {CRED}"),
    )

    # It really did take the unattributed arm...
    assert "does not say which side failed" in message.lower()
    # ...and the credential did not survive into it.
    assert "s3cr3t" not in message
    assert "alice" not in message
    assert "***:***@" in message


def test_a_checkers_own_body_cannot_trigger_the_connect_stage_message(
    monkeypatch
):
    """Remote text must not decide what this guard reports.

    `fetch_json` echoes the checker's response body into its failure message
    (`socks_fetch.py`, "first 120 chars: ..."), that `FetchFailed` is caught on
    the unreachable arm, and it lands in the same collection the stage is read
    from. So a page that merely MENTIONS `SOCKS5Error` — an error-index URL, a
    status page, a rate-limit notice — reaches the predicate.

    An unanchored substring match fires here and tells the operator
    authentication succeeded on a run where no SOCKS negotiation happened at
    all. The class name is read as the FIRST TOKEN of the wrapped text, which
    a body cannot occupy.
    """
    body = "Rate limit hit. see https://example.com/errors#SOCKS5Error-faq"
    message = _observe_raising(
        monkeypatch,
        FetchFailed(
            "HTTP 429: the checker answered, but not with a verdict "
            f"(first 120 chars: {body[:120]!r})"
        ),
    )
    lowered = message.lower()

    assert "authenticated" not in lowered
    assert "sticky session token" not in lowered
    # It is a genuine "nothing answered" round, so the generic wording is right.
    assert "the credential unusable" in lowered


def test_the_connect_stage_message_still_raises_ExitNotProven(monkeypatch):
    """Control flow does NOT change — see the `ExitNotProven` docstring.

    One exception class on purpose: the caller's correct response to every
    way the exit can fail is identical (stop, record the reason, do not fall
    back). This ticket changes the MESSAGE. A caller that started behaving
    differently would be the bug.
    """
    monkeypatch.setattr(
        exit_guard,
        "fetch_json",
        lambda url, **kw: (_ for _ in ()).throw(
            FetchFailed(_CONNECT_STAGE_TEXT)
        ),
    )
    # Not a new subclass, and nothing leaks a SOCKS exception to the caller.
    with pytest.raises(ExitNotProven) as caught:
        observe_exit("socks5h://127.0.0.1:9")
    assert type(caught.value) is ExitNotProven


def test_the_connect_stage_message_carries_no_credential(monkeypatch):
    """A new failure path must not become the one place a credential lands.

    Pushed through the branch as a credential-shaped string and asserted on
    what comes OUT — the module's own rule is that the risky part is the one
    nobody thought of, so this asserts on the message rather than on the code.
    """
    message = _observe_raising(
        monkeypatch,
        FetchFailed(f"SOCKS5Error: 0x01: General SOCKS server failure via {CRED}"),
    )

    # It really did take the new branch...
    assert "sticky session token" in message.lower()
    # ...and the credential did not survive into it.
    assert "s3cr3t" not in message
    assert "alice" not in message
    assert "***:***@" in message


# --- the credential never reaches a message ---------------------------------


def test_redact_strips_credentials_from_a_proxy_url():
    assert "s3cr3t" not in redact(f"could not connect to {CRED}")
    assert "***:***@" in redact(f"could not connect to {CRED}")


def test_a_failed_observation_message_is_redacted(monkeypatch):
    def boom(url, **kw):
        raise FetchFailed(f"ConnectionError: could not reach {CRED}")

    monkeypatch.setattr(exit_guard, "fetch_json", boom)
    with pytest.raises(ExitNotProven) as exc:
        observe_exit("socks5h://127.0.0.1:9")
    assert "s3cr3t" not in str(exc.value)


def test_the_recorded_exit_carries_no_credential():
    record = Exit(ip="1.2.3.4", country="PL").as_record()
    assert "password" not in json.dumps(record)
    assert "s3cr3t" not in json.dumps(record)


# --- there is no way to ask for a direct connection -------------------------


def test_no_fallback_to_direct_exists_in_the_guard():
    """A structural assertion, not a behavioural one: the module must not grow
    an escape hatch. Every fetch in it goes through a proxy argument."""
    source = open(exit_guard.__file__, encoding="utf-8").read()
    assert "proxy_url=proxy_url" in source
    for hatch in ("allow_direct", "no_proxy", "fallback_direct"):
        assert hatch not in source


def test_the_cli_offers_no_flag_to_read_over_a_direct_connection():
    from src.services.verify import checker_cli

    parser = checker_cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["read", "--allow-direct"])


def test_the_cli_refuses_with_exit_code_2_when_the_exit_is_unproven(
    tmp_path, capsys
):
    """2 is "we declined to measure", distinct from 1 ("we crashed") — and
    emphatically distinct from 0."""
    from src.services.verify import checker_cli

    out = tmp_path / "should-not-exist.json"
    code = checker_cli.main(
        ["read", "--credential", str(tmp_path / "absent.txt"), "-o", str(out)]
    )
    assert code == 2
    assert not out.exists(), "a refused run must not write a record"
    assert "REFUSED" in capsys.readouterr().err


# --- THE ROWS THAT DECIDE THIS TICKET: a REAL SOCKS5 relay ------------------
#
# Everything above this line drives a `FetchFailed` string the test author
# wrote. Everything below drives a real PySocks negotiation against a real
# loopback relay, through real `socks_fetch`, into real `exit_guard`. Nothing
# here is monkeypatched.
#
# WHY THE SPLIT IS LOAD-BEARING, and not merely thorough. PS-126 shipped three
# times and failed QA three times. Every round's tests passed. The feature was
# unreachable on every real run, because PySocks re-wraps a negotiation failure
# as `GeneralProxyError` (socks.py:810-814) and the hand-authored text
# "SOCKS5Error: 0x01: ..." that every test injected never occurred in
# production. A suite that only ever sees text it wrote itself cannot detect
# that — it is the exact failure mode the project's own PS-11 article names.
#
# So these rows assert on what HAPPENS. They would all fail if the unwrapping
# in `socks_fetch._reported_failure` were removed, while every row above would
# still pass — which is precisely the asymmetry that let the defect through.

from tests.socks5_relay import (  # noqa: E402
    MODE_AUTH_FAIL,
    MODE_HANG,
    Socks5Relay,
)

_GENERIC_WORDING = "may be refused, timed out, or the credential unusable"


def _observe_through_relay(relay, timeout=5):
    """Run the guard against a real relay and return the refusal message."""
    with pytest.raises(ExitNotProven) as caught:
        observe_exit(
            relay.proxy_url,
            timeout=timeout,
            urls=("https://ipinfo.io/json",),
        )
    return str(caught.value)


@pytest.mark.parametrize("reply_code", [0x01, 0x02])
def test_a_real_relay_refusing_to_allocate_names_the_session_token(reply_code):
    """THE ACCEPTANCE CRITERION. Measured, not injected.

    A relay that completes RFC1929 auth and then answers 0x01/0x02 IS the
    dead-sticky-token shape: authentication succeeded, allocation failed. QA
    drove exactly this and got the generic wording on 0 of 8 codes. This row
    is that reproduction, inverted into a check.
    """
    with Socks5Relay(reply_code=reply_code) as relay:
        message = _observe_through_relay(relay)

    lowered = message.lower()
    assert "authenticated" in lowered
    assert "allocate an exit" in lowered
    assert "sticky session token" in lowered
    assert f"{reply_code:#04x}" in lowered
    # The wording this ticket exists to remove, gone on the path it was
    # measured on.
    assert _GENERIC_WORDING not in lowered


@pytest.mark.parametrize("reply_code", [0x03, 0x04, 0x05, 0x06])
def test_a_real_destination_side_reply_does_not_blame_the_token(reply_code):
    """An exit WAS allocated, so the token is not implicated — on a real run.

    The distinction has to survive the trip, not just exist in the guard: if
    the class were still being destroyed, every one of these would take the
    generic arm instead and this row would fail on the message text.
    """
    with Socks5Relay(reply_code=reply_code) as relay:
        message = _observe_through_relay(relay)

    lowered = message.lower()
    assert "authenticated" in lowered
    assert f"{reply_code:#04x}" in lowered
    assert "after an exit was allocated" in lowered
    assert "sticky session token" not in lowered
    assert _GENERIC_WORDING not in lowered


@pytest.mark.parametrize("reply_code", [0x07, 0x08])
def test_a_real_protocol_level_reply_attributes_neither_side(reply_code):
    """The group whose correct behaviour is to say LESS, driven for real.

    A group that withholds attribution is the easiest one to get wrong by
    accident, because "said nothing" and "was never reached" look identical
    from a green suite. This asserts it was reached AND said less.
    """
    with Socks5Relay(reply_code=reply_code) as relay:
        message = _observe_through_relay(relay)

    lowered = message.lower()
    # It was genuinely reached: the stage and the code are reported.
    assert "authenticated" in lowered
    assert f"{reply_code:#04x}" in lowered
    assert "does not say which side failed" in lowered
    # And neither neighbour's cause was borrowed.
    assert "sticky session token" not in lowered
    assert "after an exit was allocated" not in lowered
    assert _GENERIC_WORDING not in lowered


def test_a_real_auth_rejection_is_not_reported_as_a_dead_session_token():
    """The stage distinction, measured at the OTHER stage.

    This is the row that would catch an over-eager fix. PySocks reports an auth
    rejection with the SAME outer class as a connect failure
    (`GeneralProxyError`), so anything keying on the outer class would report a
    REJECTED credential as "authentication succeeded" — actively false about
    the one thing the message claims to have observed.
    """
    with Socks5Relay(mode=MODE_AUTH_FAIL) as relay:
        message = _observe_through_relay(relay)

    lowered = message.lower()
    assert "authenticated this run" not in lowered
    assert "sticky session token" not in lowered
    # An auth rejection genuinely IS "the credential unusable".
    assert _GENERIC_WORDING in lowered


def test_a_real_negotiation_timeout_does_not_acquire_connect_stage_wording():
    """The contrast that a class-name shortcut would break.

    A timeout arrives as `GeneralProxyError` too — same outer class as all
    eight reply codes. Keying on that class would fire the stale-token wording
    on a timeout, re-creating the over-claiming defect that failed rounds 1
    and 2. The unwrapping only descends into a `ProxyError` inner, and a
    timeout's inner is a bare `TimeoutError`, so it stops.
    """
    with Socks5Relay(mode=MODE_HANG) as relay:
        message = _observe_through_relay(relay, timeout=1)

    lowered = message.lower()
    assert "authenticated" not in lowered
    assert "sticky session token" not in lowered
    assert _GENERIC_WORDING in lowered


def test_a_real_dead_relay_keeps_the_generic_wording():
    """The contrast case the ticket names, driven rather than injected.

    Nothing is listening on port 1, so no stage was reached. `socks_fetch`
    must not promote `ProxyConnectionError`'s inner `ConnectionRefusedError`
    into anything stage-shaped.
    """
    with pytest.raises(ExitNotProven) as caught:
        observe_exit(
            "socks5h://127.0.0.1:1",
            timeout=5,
            urls=("https://ipinfo.io/json",),
        )

    lowered = str(caught.value).lower()
    assert "authenticated" not in lowered
    assert "sticky session token" not in lowered
    assert _GENERIC_WORDING in lowered


def test_a_real_connect_stage_failure_still_raises_ExitNotProven():
    """Control flow is unchanged on the measured path too.

    The `ExitNotProven` docstring is explicit that there is ONE class on
    purpose and the MESSAGE distinguishes. A real relay must not be the thing
    that leaks a PySocks exception to the caller.
    """
    with Socks5Relay(reply_code=0x01) as relay:
        with pytest.raises(ExitNotProven) as caught:
            observe_exit(
                relay.proxy_url,
                timeout=5,
                urls=("https://ipinfo.io/json",),
            )
    assert type(caught.value) is ExitNotProven


def test_a_real_connect_stage_failure_carries_no_credential():
    """Credential-safety, asserted on the output of a REAL run.

    The relay is reached through a URL carrying a credential-shaped
    user/password, so this drives a real credential through the whole path —
    PySocks, `socks_fetch`, the new branch — and asserts on what comes out.

    WHAT THIS ROW DOES AND DOES NOT PROVE, stated because the difference is
    easy to assert past. It does NOT assert the `***:***@` marker, and the
    absence of that assertion is deliberate rather than an omission: PySocks
    formats a connect-stage failure as `"{:#04x}: {}"` (socks.py:533) and puts
    NO proxy URL in it, so on a real run there is nothing here for `redact` to
    rewrite and demanding the marker would be demanding evidence of a
    substitution that correctly never happened. What it proves is the property
    that actually matters — the credential does not reach the message — and it
    proves it against the text PySocks genuinely produces rather than against
    a hand-built string.

    That `redact` DOES rewrite a URL when one is present is a separate claim,
    pinned separately by `test_the_connect_stage_message_carries_no_credential`
    (which injects a URL-bearing text) and by `test_redact_strips_credentials_
    from_a_proxy_url`. The two rows are complementary: one covers the path a
    real failure takes, the other covers the transformation.
    """
    with Socks5Relay(reply_code=0x01) as relay:
        message = _observe_through_relay(relay)

    # It really did take the new branch...
    assert "sticky session token" in message.lower()
    # ...and neither half of the credential survived into it.
    assert Socks5Relay.PASSWORD not in message
    assert Socks5Relay.USERNAME not in message
