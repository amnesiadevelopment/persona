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
        cwd=workdir, env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"recording script failed:\n{proc.stderr}"
    return (workdir / "record" / "environment-unmodified.txt").read_text(
        encoding="utf-8"
    )


# ─────────────────────────────────────────────────────────────────────────────
# THE LEAK — what must never reach a world-readable log or artifact
# ─────────────────────────────────────────────────────────────────────────────

def test_the_hostname_never_reaches_the_record(record):
    """`uname -a` field 2 is the nodename. On this runner it is a `DESKTOP-*`
    name that identifies a personal workstation, not a cloud runner."""
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
     "Core(s) per socket:", "Socket(s):", "MemTotal", "Architecture:"],
)
def test_the_capacity_figures_are_kept(record, field):
    """Counts and clocks make the wall-clock and OOM readings interpretable and
    identify no machine. The build's own timing analysis depends on knowing it
    ran on 32 threads."""
    assert field in record, (
        f"{field!r} was scrubbed away. It is a capacity figure, not host "
        "identity, and the record is useless without it."
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
        cwd=workdir, env=env, capture_output=True, text=True, timeout=120, check=True,
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
        cwd=workdir, env=env, capture_output=True, text=True, timeout=120, check=True,
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
        cwd=workdir, env=env, capture_output=True, text=True, timeout=120, check=True,
    )
    resalted = (workdir / "record" / "environment-unmodified.txt").read_text(
        encoding="utf-8"
    )
    assert _pseudonyms(record) != _pseudonyms(resalted), (
        "the same host produced the same digest under a different salt, so the "
        "salt is not being applied and the digest is a plain hash of a "
        "low-entropy string"
    )
