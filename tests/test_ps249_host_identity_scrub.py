"""PS-249: the public build record must not identify the owner's machine.

`amnesiadevelopment/persona` is PUBLIC, so Actions logs and workflow artifacts
are world-readable. `scripts/ps218_record_env.sh` used to publish the runner's
hostname (`uname -a`) and its exact CPU model (`lscpu | grep 'model name'`) —
an identifiable personal workstation. Persona exists to stop a browser
disclosing the machine behind it; publishing the maintainer's own machine is
that same leak one layer up.

⚠️ EVERY VALUE IN THIS FILE IS SYNTHETIC. The ticket forbids embedding the real
hostname or CPU string anywhere public — PR title, PR body, commit messages, or
a test fixture — because that is the obvious way to fix a disclosure and
republish it in the same commit. The names below are invented and deliberately
implausible; nothing here is a reading of any real machine.

WHY A PSEUDONYM AND NOT A REDACTION
───────────────────────────────────
`ps218_verify_control.sh` (PS-244) decides whether a borrowed control may be
reused by COMPARING the hostname and CPU model of two records. So the scrub is
constrained from both sides at once:

  * delete the fields  -> the comparator reads empty, `is_readable()` refuses,
                          and EVERY borrow fails
  * use a fixed label  -> the comparator compares a constant to itself, passes
                          for every host, and a control built on a DIFFERENT
                          machine is accepted

The second is the dangerous one: a guard that can never fire, which is the
failure class this project keeps getting bitten by. A salted digest is the only
option that is stable on one machine, discriminating between machines, and
discloses neither value — and those three properties are what this file pins.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RECORD_SH = REPO_ROOT / "scripts" / "ps218_record_env.sh"
VERIFY_SH = REPO_ROOT / "scripts" / "ps218_verify_control.sh"
HOST_ID_SH = REPO_ROOT / "scripts" / "ps218_host_id.sh"

# ─── Synthetic host identity. Invented; not a reading of any machine. ─────────
FAKE_HOSTNAME = "SYNTHETIC-TESTHOST-ZZZ9"
FAKE_CPU_MODEL = "Fictional Ultra 9 Model X-0000"
FAKE_CPU_FLAGS = "fpu vme de pse perfctr_core avx512f synthetic_flag"
FAKE_KERNEL = "9.9.9-synthetic-kernel"

# ⚠️ THE GUARD IS `sys.platform`, NOT `shutil.which("bash")`.
#
# `which("bash")` looks like the right check and is a trap on windows-latest: it
# resolves to `C:\Windows\System32\bash.exe`, the WSL *launcher*, so it returns a
# path rather than None. The skip would not fire, these tests would run, and they
# would die in the WSL stub — which is how PS-254's five failures happened.
#
# SKIPPING IS CORRECT HERE, and for a reason that did NOT apply to PS-254.
# There the script under test genuinely executes on Windows, so a skip hid a real
# gap. `ps218_record_env.sh` runs ONLY on `[self-hosted, persona-build]` (the
# workflow's sole `runs-on`, at :214 and :414) — a Linux host. It has no Windows
# code path to leave untested, and the stub shims below are extensionless shell
# scripts Windows cannot execute in any case. Running these there would test the
# runner, not the scrub.
pytestmark = [
    pytest.mark.skipif(
        sys.platform == "win32",
        reason="ps218_record_env.sh runs only on the self-hosted Linux build runner; "
               "on windows-latest `bash` is the WSL launcher, not a shell",
    ),
    pytest.mark.skipif(
        shutil.which("bash") is None, reason="the recording script is bash"
    ),
]


def _stub_dir(tmp_path: Path) -> Path:
    """A PATH front-end whose `uname`/`lscpu` emit the synthetic values above.

    Stubbing the *tools* rather than editing the script is what makes this a
    test of the shipped code path: the script under test is executed verbatim.
    """
    stubs = tmp_path / "stubs"
    stubs.mkdir(parents=True)

    (stubs / "uname").write_text(
        "#!/bin/bash\n"
        "case \"$1\" in\n"
        f'  -s) echo "Linux" ;;\n'
        f'  -n) echo "{FAKE_HOSTNAME}" ;;\n'
        f'  -r) echo "{FAKE_KERNEL}" ;;\n'
        '  -v) echo "#1 SMP SYNTHETIC" ;;\n'
        '  -m) echo "x86_64" ;;\n'
        '  -o) echo "GNU/Linux" ;;\n'
        f'  -a) echo "Linux {FAKE_HOSTNAME} {FAKE_KERNEL} #1 SMP SYNTHETIC x86_64 GNU/Linux" ;;\n'
        '  *)  echo "Linux" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    (stubs / "lscpu").write_text(
        "#!/bin/bash\n"
        "cat <<'EOF'\n"
        "Architecture:                            x86_64\n"
        "Byte Order:                              Little Endian\n"
        "CPU(s):                                  32\n"
        f"Model name:                              {FAKE_CPU_MODEL}\n"
        "Thread(s) per core:                      2\n"
        "Core(s) per socket:                      16\n"
        "Socket(s):                               1\n"
        "CPU max MHz:                             5700.0000\n"
        f"Flags:                                   {FAKE_CPU_FLAGS}\n"
        "EOF\n",
        encoding="utf-8",
    )
    for stub in stubs.iterdir():
        stub.chmod(0o755)
    return stubs


@pytest.fixture
def record(tmp_path) -> str:
    """Run the real recording script against the synthetic machine."""
    ucpl = tmp_path / "ucpl"
    ucpl.mkdir()
    workdir = tmp_path / "work"
    workdir.mkdir()

    stubs = _stub_dir(tmp_path)
    env = dict(os.environ)
    env["PATH"] = f"{stubs}{os.pathsep}{env.get('PATH', '')}"
    env["UCPL_DIR"] = str(ucpl)
    env["PS218_HOST_SALT_FILE"] = str(tmp_path / "salt")

    proc = subprocess.run(
        ["bash", str(RECORD_SH), "unmodified"],
        cwd=workdir, env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, f"recording script failed:\n{proc.stderr}"
    return (workdir / "record" / "environment-unmodified.txt").read_text(
        encoding="utf-8"
    )


# ─────────────────────────────────────────────────────────────────────────────
# THE LEAK — what must never reach a world-readable log or artifact
# ─────────────────────────────────────────────────────────────────────────────

def test_the_hostname_never_reaches_the_record(record):
    """`uname -a` field 2 is the nodename, which on this runner is a name that
    identifies a personal workstation rather than a cloud runner.

    ⚠️ The prefix that name carries is deliberately NOT written here. Naming it
    would put a host-identity fragment into the very file that exists to remove
    one — the same recursion this ticket warns about, one notch smaller. It is
    also a real regression against `main`, which carries that fragment in zero
    tracked files.
    """
    assert FAKE_HOSTNAME not in record, (
        "the hostname was published to a record that reaches a PUBLIC "
        "repository's Actions log and workflow artifacts"
    )


def test_the_cpu_model_never_reaches_the_record(record):
    """The exact retail CPU model links a person to a specific physical
    machine, which is the correlation the threat model treats as valuable."""
    assert FAKE_CPU_MODEL not in record


def test_the_cpu_flag_string_never_reaches_the_record(record):
    """The leak the ticket did NOT cite, and the reason the field list is now
    explicit rather than a regex.

    The old pattern was `'model name|^cpu\\(s\\)|thread|core|socket|mhz'`, and
    the bare `core` alternative also matches the `Flags:` line — every modern
    x86 flag list contains `perfctr_core`. So the whole flag string was
    published, which is a NARROWER fingerprint than a model name: it pins
    microarchitecture, stepping and errata state.
    """
    assert "Flags:" not in record, "the CPU flags line reached the record"
    assert "perfctr_core" not in record, (
        "a `core`-matching flag pulled the whole flag string in — this is the "
        "regex widening that an explicit allow-list exists to prevent"
    )
    assert FAKE_CPU_FLAGS not in record


# ⚠️ A SHAPE, NEVER A VALUE. Writing the real part number here as a literal
# would republish the very string this ticket exists to remove — the test file
# is as public as the script it guards, so `grep` would simply find the model in
# `tests/` instead of in `scripts/`. That is the recursion the ticket warns
# about ("the obvious way to fix a disclosure and republish it in the same
# commit"), and it is easy to walk into while writing the guard against it.
#
# Matching the FAMILY of retail part numbers instead is both safe and stronger:
# it discloses nothing, and it catches any processor name a future edit might
# introduce rather than only the one that happened to be there.
_RETAIL_CPU_PATTERNS = (
    r"\bi[3579][-\s]?\d{4,5}[A-Z]{0,3}\b",           # Intel Core i_-_____
    r"\b(?:Ryzen|Threadripper|EPYC|Xeon)\b",         # AMD / Intel server + HEDT
    r"\bCore\s+Ultra\s+\d\b",                        # Intel Core Ultra _
    r"\b\d{4,5}[KXGSTF]{1,3}\b",                     # bare part suffix
)

# The definition above necessarily CONTAINS those shapes, so the self-check
# below skips it. Bounded by explicit markers rather than by line number, which
# would rot on the next edit.
_PATTERN_BLOCK_START = "_RETAIL_CPU_PATTERNS = ("
_PATTERN_BLOCK_END = ")"


def _source_excluding_pattern_definitions(text: str) -> str:
    """This file's own source, minus the tuple of shapes defined above."""
    out, skipping = [], False
    for line in text.splitlines():
        if line.startswith(_PATTERN_BLOCK_START):
            skipping = True
            continue
        if skipping:
            if line.startswith(_PATTERN_BLOCK_END):
                skipping = False
            continue
        out.append(line)
    return "\n".join(out)


def test_no_committed_prose_names_the_processor():
    """The script's own comments are committed to a public repo, so they leak
    with no run at all — scrubbing only the runtime output would miss them.

    This is where the leak's third instance lived: a comment naming the exact
    retail part in prose, to explain the performance/efficiency core split. The
    asymmetry is worth explaining; the part number is not.
    """
    source = RECORD_SH.read_text(encoding="utf-8")
    for pattern in _RETAIL_CPU_PATTERNS:
        assert re.search(pattern, source) is None, (
            "committed source names a retail processor, which is host identity "
            "published to a public repository with no run at all. Describe the "
            "property that matters (a hybrid-core part) rather than the model. "
            f"Offending pattern: {pattern!r}"
        )


def test_this_test_file_does_not_republish_what_it_guards():
    """⚠️ THE GUARD MUST NOT BECOME THE LEAK.

    A test written as `assert "<real model>" not in source` embeds that model as
    a literal — and this file is exactly as public as the script it protects, so
    the string stays one `grep` away, just in `tests/` instead of `scripts/`.
    The leak would have moved rather than gone.

    I walked into precisely that while writing the test above; this is what
    stops it regressing.
    """
    body = _source_excluding_pattern_definitions(
        Path(__file__).read_text(encoding="utf-8")
    )
    for pattern in _RETAIL_CPU_PATTERNS:
        assert re.search(pattern, body) is None, (
            "this test file names a real processor, which republishes the value "
            "the scrub removed. Assert on a SHAPE, never on the literal. "
            f"Offending pattern: {pattern!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# WHAT MUST SURVIVE — the ticket forbids scrubbing the record into uselessness
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "field",
    ["nproc:", "nproc --all:", "CPU(s):", "Thread(s) per core:",
     "Core(s) per socket:", "Socket(s):", "Architecture:"],
)
def test_the_capacity_figures_are_kept(record, field):
    """Counts and clocks make the wall-clock and OOM readings interpretable and
    identify no machine. The build's own timing analysis depends on knowing it
    ran on 32 threads.

    These fields come from the `lscpu` allow-list and the `nproc` echoes, both
    of which the stubs supply, so they are asserted on every host.
    """
    assert field in record, (
        f"{field!r} was scrubbed away. It is a capacity figure, not host "
        "identity, and the record is useless without it."
    )


@pytest.mark.skipif(
    not Path("/proc/meminfo").exists(),
    reason="the memory figures are read from /proc/meminfo, which is Linux-only",
)
def test_the_memory_figures_are_kept(record):
    """The memory ceiling is the figure the whole record exists to establish —
    PS-218's own reason for being is that "WSL2's memory allocation is not the
    host's", and an OOM at link time is read against it.

    ⚠️ SEPARATED FROM THE PARAMETRISED SET, AND GUARDED, BECAUSE IT IS THE ONE
    CAPACITY FIELD THAT IS NOT PORTABLE. The script reads it with
    `grep ... /proc/meminfo || true`, which yields nothing on a host that has no
    procfs — so on macOS the field is legitimately absent and asserting it
    unconditionally fails for a reason that has nothing to do with the scrub.
    That is what happened on the first CI run of this file: a real defect in the
    test, not in the fix.

    The guard is `/proc/meminfo` itself rather than `sys.platform`, so it states
    the actual precondition instead of a proxy for it.
    """
    assert "MemTotal" in record, (
        "MemTotal was scrubbed away. It is a capacity figure, not host "
        "identity, and the OOM readings cannot be interpreted without it."
    )


def test_the_kernel_release_survives_in_its_parsed_position(record):
    """`ps218_verify_control.sh` reads the `# host:` line positionally —
    `awk '{print $i}'` with i=2 for the nodename and i=3 for the kernel.

    Rebuilding the line from the same components in the same order is a
    CONTRACT, not a formatting choice: dropping a field would shift the kernel
    into the hostname slot, and the borrow check would compare the wrong things
    while still reporting success.
    """
    host_line = next(l for l in record.splitlines() if l.startswith("# host: "))
    fields = host_line[len("# host: "):].split()

    assert fields[0] == "Linux", "field 1 must remain the kernel NAME"
    assert fields[1] != FAKE_HOSTNAME, "field 2 must be pseudonymised"
    assert fields[2] == FAKE_KERNEL, (
        "field 3 must remain the kernel RELEASE — the verify script reads this "
        "position, so a dropped field silently shifts what it compares"
    )


# ─────────────────────────────────────────────────────────────────────────────
# THE PSEUDONYM'S THREE PROPERTIES — a constant label would satisfy none of them
# ─────────────────────────────────────────────────────────────────────────────

def _pseudonyms(record: str) -> tuple[str, str]:
    host_line = next(l for l in record.splitlines() if l.startswith("# host: "))
    node = host_line[len("# host: "):].split()[1]
    model = next(
        l.split(":", 1)[1].strip()
        for l in record.splitlines()
        if l.lower().startswith("model name")
    )
    return node, model


def test_the_fields_are_present_and_readable_not_deleted(record):
    """Deleting them would make `is_readable()` refuse and every borrow fail.

    The comparator treats ""/unknown/(not recorded)/n/a as unreadable, so an
    absent field is not a neutral outcome — it breaks the feature.
    """
    node, model = _pseudonyms(record)
    for value in (node, model):
        assert value not in ("", "unknown", "(not recorded)", "n/a"), (
            "the field is unreadable, so ps218_verify_control.sh would refuse "
            "every borrowed control rather than compare them"
        )


def test_the_pseudonym_is_stable_across_dispatches(tmp_path, record):
    """Records from the same runner must stay comparable, or a legitimate
    borrow is refused on the second dispatch."""
    ucpl = tmp_path / "ucpl2"; ucpl.mkdir()
    workdir = tmp_path / "work2"; workdir.mkdir()
    env = dict(os.environ)
    env["PATH"] = f"{_stub_dir(tmp_path / 'second')}{os.pathsep}{env.get('PATH', '')}"
    env["UCPL_DIR"] = str(ucpl)
    env["PS218_HOST_SALT_FILE"] = str(tmp_path / "salt")   # SAME salt = same host

    subprocess.run(
        ["bash", str(RECORD_SH), "unmodified"],
        cwd=workdir, env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=120, check=True,
    )
    second = (workdir / "record" / "environment-unmodified.txt").read_text(
        encoding="utf-8"
    )
    assert _pseudonyms(record) == _pseudonyms(second), (
        "the pseudonym changed between two runs on the same machine, so two "
        "records from the same runner would no longer compare equal"
    )


def test_a_different_machine_still_produces_a_different_pseudonym(tmp_path, record):
    """⚠️ THE PROPERTY A CONSTANT LABEL WOULD DESTROY.

    If the hostname were replaced by a fixed string, the borrow check would
    compare that constant to itself, pass for every host, and accept a control
    built on different hardware. That is a guard that can never fire.
    """
    other = tmp_path / "other"
    other.mkdir()
    stubs = other / "stubs"; stubs.mkdir()
    (stubs / "uname").write_text(
        "#!/bin/bash\n"
        "case \"$1\" in\n"
        '  -s) echo "Linux" ;;\n'
        '  -n) echo "SYNTHETIC-OTHERHOST-QQ1" ;;\n'
        f'  -r) echo "{FAKE_KERNEL}" ;;\n'
        '  -v) echo "#1 SMP SYNTHETIC" ;;\n'
        '  -m) echo "x86_64" ;;\n'
        '  -o) echo "GNU/Linux" ;;\n'
        '  *)  echo "Linux" ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    (stubs / "lscpu").write_text(
        "#!/bin/bash\n"
        "cat <<'EOF'\n"
        "CPU(s):                                  32\n"
        "Model name:                              Fictional Core 5 Model Y-1111\n"
        "EOF\n",
        encoding="utf-8",
    )
    for stub in stubs.iterdir():
        stub.chmod(0o755)

    ucpl = other / "ucpl"; ucpl.mkdir()
    workdir = other / "work"; workdir.mkdir()
    env = dict(os.environ)
    env["PATH"] = f"{stubs}{os.pathsep}{env.get('PATH', '')}"
    env["UCPL_DIR"] = str(ucpl)
    env["PS218_HOST_SALT_FILE"] = str(tmp_path / "salt")   # same salt, other host

    subprocess.run(
        ["bash", str(RECORD_SH), "unmodified"],
        cwd=workdir, env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=120, check=True,
    )
    other_record = (workdir / "record" / "environment-unmodified.txt").read_text(
        encoding="utf-8"
    )

    assert _pseudonyms(record) != _pseudonyms(other_record), (
        "two DIFFERENT machines produced the same pseudonym, so the borrow "
        "check would accept a control built somewhere else — the guard would "
        "be vacuous rather than merely weak"
    )


def test_the_digest_is_salted_so_a_low_entropy_name_cannot_be_reversed(tmp_path, record):
    """Both inputs are low-entropy — a WSL hostname is a fixed prefix plus a
    handful of characters, and there are only a few hundred retail CPU models.
    An UNSALTED digest of either is trivially brute-forced from a public log,
    which would leave the value disclosed in all but appearance.
    """
    ucpl = tmp_path / "ucpl3"; ucpl.mkdir()
    workdir = tmp_path / "work3"; workdir.mkdir()
    env = dict(os.environ)
    env["PATH"] = f"{_stub_dir(tmp_path / 'third')}{os.pathsep}{env.get('PATH', '')}"
    env["UCPL_DIR"] = str(ucpl)
    env["PS218_HOST_SALT_FILE"] = str(tmp_path / "different-salt")   # DIFFERENT salt

    subprocess.run(
        ["bash", str(RECORD_SH), "unmodified"],
        cwd=workdir, env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=120, check=True,
    )
    resalted = (workdir / "record" / "environment-unmodified.txt").read_text(
        encoding="utf-8"
    )
    assert _pseudonyms(record) != _pseudonyms(resalted), (
        "the same host produced the same digest under a different salt, so the "
        "salt is not being applied and the digest is a plain hash of a "
        "low-entropy string"
    )


# ─────────────────────────────────────────────────────────────────────────────
# THE TWO AUDIT DEFECTS (persona-reviewer-2, 06:51:58Z)
#
# Both were REPRODUCED by the auditor, and neither was catchable by the merged
# ps244 suite — those tests write BOTH sides of the comparison from one
# template, so a one-sided format change looks identical on both sides and
# cannot be seen. These build the two sides SEPARATELY, which is the whole
# point.
# ─────────────────────────────────────────────────────────────────────────────

# A control recorded BEFORE this ticket: raw values, exactly as it exists today
# on run 33151144134 — the only successful control that exists.
LEGACY_HOSTNAME = "SYNTHETIC-LEGACY-HOST-PP7"
LEGACY_CPU = "Fictional Legacy Model Z-9999"

_LEGACY_ENV = f"""\
# PS-218 build environment — tree: unmodified
# pass: pre-prepare
# host: Linux {LEGACY_HOSTNAME} 9.9.9-synthetic-kernel #1 SMP SYNTHETIC x86_64 GNU/Linux

== CPU as the RUNNER sees it ==
nproc:            32
nproc --all:      32
Model name:                              {LEGACY_CPU}

== SOURCE PROVENANCE ==
portablelinux tag:  144.0.7559.132-1
"""

_LEGACY_MANIFEST = """\
| ungoogled-chromium-portablelinux tag | `144.0.7559.132-1` |
| **1. Patches APPLIED** | YES |
| **2. Tree COMPILED** | YES |
| chrome binary on disk | PRESENT (436M) |
"""

_CONTROL_RUN = "33151144134"


def _borrow_fixture(tmp_path, salt, *, same_machine: bool):
    """A LEGACY (raw-value) control beside a NEW-format record for this run.

    The two sides are built by different means on purpose: the control is
    written raw, and this run's record is pseudonymised through the shipped
    helper. That asymmetry is exactly the situation the merged suite cannot
    represent, and it is where both defects lived.
    """
    control = tmp_path / "control"
    record = tmp_path / "record"
    control.mkdir()
    record.mkdir()

    (control / "environment-unmodified.txt").write_text(_LEGACY_ENV, encoding="utf-8")
    (control / "MANIFEST-unmodified.md").write_text(_LEGACY_MANIFEST, encoding="utf-8")
    (control / "compile-unmodified.provenance").write_text(
        "phase=compile\ntree=unmodified\nungoogled_tag=144.0.7559.132-1\n"
        f"github_run_id={_CONTROL_RUN}\n",
        encoding="utf-8",
    )
    (control / "compile-unmodified.log").write_text(
        "[1/1] LINK ./chrome\nninja: build completed successfully.\n", encoding="utf-8"
    )

    host = LEGACY_HOSTNAME if same_machine else "SYNTHETIC-OTHER-HOST-RR3"
    cpu = LEGACY_CPU if same_machine else "Fictional Other Model Y-1111"
    anon_host = _shell_pseudonymise(host, salt)
    anon_cpu = _shell_pseudonymise(cpu, salt)

    (record / "environment-patched.txt").write_text(
        _LEGACY_ENV.replace("tree: unmodified", "tree: patched")
        .replace(f"Linux {LEGACY_HOSTNAME}", f"Linux {anon_host}")
        .replace(LEGACY_CPU, anon_cpu),
        encoding="utf-8",
    )
    return control, record


def _shell_pseudonymise(value: str, salt: Path) -> str:
    """Call the SHIPPED helper, so the test cannot drift from the implementation."""
    proc = subprocess.run(
        ["bash", "-c", f'. "{HOST_ID_SH}"; pseudonymise "$1"', "_", value],
        env={**os.environ, "PS218_HOST_SALT_FILE": str(salt)},
        capture_output=True, text=True, encoding="utf-8", timeout=60, check=True,
    )
    return proc.stdout.strip()


def _run_verify(tmp_path, salt):
    return subprocess.run(
        ["bash", str(VERIFY_SH)],
        cwd=tmp_path,
        env={
            **os.environ,
            "PS218_HOST_SALT_FILE": str(salt),
            "CONTROL_DIR": "control",
            "CURRENT_ENV": "record/environment-patched.txt",
            "UNGOOGLED_TAG": "144.0.7559.132-1",
            "CONTROL_RUN_ID": _CONTROL_RUN,
            "GITHUB_RUN_ID": "33999999999",
        },
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )


def test_a_control_recorded_before_this_change_can_still_be_borrowed(tmp_path):
    """⚠️ DEFECT 1: the format change must not be one-sided.

    Every control that exists today was recorded BEFORE this ticket, so it holds
    a raw hostname and CPU model. If only the writer is changed, this run emits
    pseudonyms, the comparator sees two different strings, and it refuses EVERY
    borrow — including run 33151144134, the only successful control there is.
    The feature would be dead on arrival for exactly the data it exists to use.

    The shared helper hashes the legacy value with the same salt, so the same
    machine still matches.
    """
    salt = tmp_path / "salt"
    _borrow_fixture(tmp_path, salt, same_machine=True)

    proc = _run_verify(tmp_path, salt)

    assert proc.returncode == 0, (
        "a legacy control from the SAME machine was refused, so no existing "
        f"control can be borrowed at all:\n{proc.stdout}\n{proc.stderr}"
    )
    assert "VERIFIED" in proc.stdout


def test_a_legacy_control_from_another_machine_is_still_refused(tmp_path):
    """The security property that the compatibility shim must not cost.

    Accepting a legacy record must not mean accepting ANY legacy record: a
    control built elsewhere still hashes differently and must still be refused.
    Without this, the fix for defect 1 would be a guard that never fires.
    """
    salt = tmp_path / "salt"
    _borrow_fixture(tmp_path, salt, same_machine=False)

    proc = _run_verify(tmp_path, salt)

    assert proc.returncode != 0, "a control from a DIFFERENT machine was accepted"
    assert "REFUSED [host]" in proc.stdout
    assert not (tmp_path / "control" / "BORROWED-CONTROL.verified").exists(), (
        "a borrow certificate was written for a refused control"
    )


@pytest.mark.parametrize("same_machine", [True, False], ids=["pass-path", "refusal-path"])
def test_the_borrow_never_republishes_the_values_this_ticket_removes(
    tmp_path, same_machine
):
    """⚠️ DEFECT 2: the verify script re-emitted the borrowed control's raw
    hostname and CPU model into `record/control-borrow-verification.txt` and
    stdout — both world-readable (the artifact is uploaded under `if: always()`,
    and the Actions log is public). So the borrow path republished exactly what
    the scrub removes.

    BOTH PATHS ARE CHECKED, and the refusal path is the one that mattered most:
    `compare_or_fail` prints both sides in its DIFFERS branch, and defect 1
    guaranteed every borrow took that branch — so defect 1 was the delivery
    mechanism for defect 2.
    """
    salt = tmp_path / "salt"
    _borrow_fixture(tmp_path, salt, same_machine=same_machine)

    proc = _run_verify(tmp_path, salt)
    report = tmp_path / "record" / "control-borrow-verification.txt"
    emitted = proc.stdout + proc.stderr + (
        report.read_text(encoding="utf-8") if report.exists() else ""
    )

    for secret in (LEGACY_HOSTNAME, LEGACY_CPU):
        assert secret not in emitted, (
            f"the borrow path published {secret!r} to the report and/or the "
            "Actions log, both of which are world-readable — republishing the "
            "value this ticket exists to remove"
        )


# ─────────────────────────────────────────────────────────────────────────────
# THE ROUND-3 BLOCKER: the digest tool must be RESOLVED, not assumed.
#
# `sha256sum` ships with GNU coreutils, so it is present on Linux and ABSENT on
# macOS, which carries `shasum`. Hardcoding it made every macOS caller fall
# through to the `unknown` path, and because `is_readable()` correctly refuses
# `unknown`, that REFUSED EVERY BORROW — defect 1's exact symptom reached by a
# different route, in the very file written to cure it.
#
# It shipped to the approval gate because a Linux-only run structurally cannot
# see it, and nothing pinned the behaviour. These two tests are that pin: they
# force each branch by CONSTRUCTING A PATH, so a Linux runner exercises the
# macOS and openssl-only code paths too.
# ─────────────────────────────────────────────────────────────────────────────

# The minimum a POSIX shell script needs, so a restricted PATH can hide the
# digest tools without breaking the script itself.
_DIGEST_TEST_ESSENTIALS = (
    "bash", "sh", "cat", "head", "grep", "sed", "awk", "cut", "tr",
    "mkdir", "rm", "chmod", "od", "env", "printf", "dd",
)
_DIGEST_TOOLS = ("sha256sum", "shasum", "openssl")


def _restricted_path(tmp_path: Path, name: str, available: tuple[str, ...]) -> Path:
    """A PATH directory holding the shell essentials plus only `available`.

    Absence is what matters, so the tools are genuinely NOT on the PATH rather
    than shadowed by a failing stub: `command -v` must not find them, which is
    the condition the resolver actually branches on.
    """
    binroot = tmp_path / f"bin-{name}"
    binroot.mkdir(parents=True, exist_ok=True)
    for tool in _DIGEST_TEST_ESSENTIALS + available:
        found = shutil.which(tool)
        if found:
            link = binroot / tool
            if not link.exists():
                link.symlink_to(found)
    return binroot


def _pseudonymise_on_path(binroot: Path, salt: Path, value: str) -> str:
    proc = subprocess.run(
        [str(binroot / "bash"), "-c",
         f'. "{HOST_ID_SH}"; pseudonymise "$1"', "_", value],
        env={"PATH": str(binroot), "HOME": str(salt.parent),
             "PS218_HOST_SALT_FILE": str(salt)},
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    return proc.stdout.strip()


@pytest.mark.skipif(
    shutil.which("sha256sum") is None or shutil.which("openssl") is None,
    reason="needs sha256sum and openssl present to build the alternate PATHs",
)
def test_every_digest_tool_yields_the_same_label_so_a_record_crosses_platforms(tmp_path):
    """A record written where only one tool exists must compare EQUAL to one
    read where only another does.

    This is the invariant the hardcoded `sha256sum` broke. All three commands
    compute the same SHA-256 over the same bytes — the digest is a property of
    the input, not of the tool — but their OUTPUT FORMATS differ, and `openssl`
    prefixes its line (`SHA2-256(stdin)= <hex>`). A positional `cut` is correct
    for the first two and silently wrong for the third: it would emit a fragment
    of the prefix and hand THAT out as a host label, so a Linux-written record
    would stop matching itself. The hex run must be extracted by pattern.
    """
    salt = tmp_path / "salt"
    salt.write_text("0123456789abcdef" * 4, encoding="utf-8")

    labels = {}
    for tool in _DIGEST_TOOLS:
        if shutil.which(tool) is None:
            continue
        binroot = _restricted_path(tmp_path, tool, (tool,))
        labels[tool] = _pseudonymise_on_path(binroot, salt, FAKE_HOSTNAME)

    assert len(labels) >= 2, (
        "fewer than two digest tools were exercisable, so this test proved "
        f"nothing about portability: {labels}"
    )
    for tool, label in labels.items():
        assert label.startswith("anon-"), (
            f"{tool} did not produce a usable label ({label!r}) — a host where "
            "only this tool exists would refuse every borrow"
        )
    assert len(set(labels.values())) == 1, (
        "the digest tools disagree, so a record written on one platform would "
        f"no longer match itself when read on another: {labels}"
    )


@pytest.mark.skipif(
    shutil.which("sha256sum") is None,
    reason="needs a real digest tool present to build the restricted PATH",
)
def test_with_no_digest_tool_at_all_it_fails_closed_and_never_emits_the_value(tmp_path):
    """Fail-closed survives the portability fix.

    With no digest tool the label must be `unknown` — which `is_readable()`
    refuses, so a borrow is REFUSED LOUDLY rather than silently comparing two
    constants. It must NEVER fall back to the real value to keep working.
    """
    salt = tmp_path / "salt"
    salt.write_text("0123456789abcdef" * 4, encoding="utf-8")

    binroot = _restricted_path(tmp_path, "none", ())
    label = _pseudonymise_on_path(binroot, salt, FAKE_HOSTNAME)

    assert label == "unknown", (
        f"with no digest tool the label was {label!r}, not the fail-closed "
        "`unknown` that is_readable() refuses"
    )
    assert FAKE_HOSTNAME not in label, (
        "the value was emitted as its own label when no digest tool existed — "
        "a fallback to the real value is the one outcome the scrub forbids"
    )
