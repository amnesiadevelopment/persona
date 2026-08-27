"""The panic wipe destroys the quarantined store copies the corrupt-file guard leaves.

When a store file fails to parse, `StoreGuardMixin._quarantine_store_file` renames
it to `<path>.corrupt-<ts>` so the next save writes a fresh file beside the original
instead of over it. That guard is CORRECT — leaving the unreadable file in place is
recoverable, overwriting it with an empty dict is not — and nothing here weakens it.
The three tests that assert a quarantine file IS created (test_proxy_store.py,
test_ssh_store.py, test_cert_store.py) stay green, unmodified.

The defect was that nothing ever removed what it leaves. The quarantined file is a
verbatim byte-copy of the store, and three of the guarded stores hold SOCKS5
credentials, SSH passwords and .p12 bundle passwords — which is why atomic.py writes
exactly those `private=True`. trash.json carries all three kinds verbatim. Because the
suffix embeds int(time.time()), every corruption event mints a UNIQUE file, so they
accumulate one per event, forever, and no permanent delete, trash purge or panic wipe
reached any of them.

That made a claim the tree makes in trash/store.py false — "The trash is the same
store's own guarded area, never a second, less-guarded copy of the operator's
identity", and "a wipe that claims everything is gone is telling the truth". A
`trash.json.corrupt-<ts>` holding those payloads that the wipe does not touch is
precisely the second copy that sentence says does not exist.

So these tests drive the SHIPPED path end to end — real stores, real credentials, a
real torn write (tail bytes lost, records intact), the real quarantine firing, and a
real `wipe_all_profiles()` — and assert on BYTES ON DISK IN THE HOME, never on a
helper having been called. A test bound to a call could not fail if the glob were
removed, which is exactly the falsification this slice has to stay open to.

Every assertion carries a CONTROL: the wipe must be shown genuinely destroying
something in the same run. Without it, "no quarantine file survives" could equally
mean the probe never created one, and that reads as a clean pass rather than a
broken instrument.

WHAT THE CONTROL DELIBERATELY DOES *NOT* CLAIM. The live credential stores
(proxies.json, ssh_hosts.json, certificates.json) are NOT destroyed by the panic
wipe — measured on this tree, they survive it with their credentials intact.
`wipe_all_profiles` destroys profiles, the trash and the logs; the credential
stores sit outside its stated scope, and this slice does not change that. A probe
that tears those files first APPEARS to show the wipe removing them, because the
quarantine rename has already moved each one aside — `exists()` then reads False
for a reason that has nothing to do with the wipe. The control below therefore
rests on the profile data dir and the live trash, both of which the wipe
demonstrably does destroy.

THE HOME PATH IS DATA, NOT A PATTERN. PERSONA_HOME is operator-overridable, and
glob interprets metacharacters across the WHOLE pattern, directory portion
included — so an unescaped home containing `[` yields a pattern matching
nothing, and the sweep becomes a SILENT no-op: no exception, no empty-result
branch, the wipe reports success while the credential-bearing copy survives.
Two tests below drive that case under a bracketed home, bound to bytes on disk.
"""
import os
import pathlib

import pytest

from src.services.cert.store import CertStore, Certificate
from src.services.profile.manager import ProfileManager
from src.services.proxy.store import ProxyStore
from src.services.ssh.store import SSHHost, SSHHostStore
from src.services.trash.store import TrashStore

PROXY_SECRET = "s0cks5-proxy-passw0rd"
SSH_SECRET = "ssh-host-passw0rd"
CERT_SECRET = "p12-bundle-passw0rd"

#: kind -> the credential that must not survive the wipe in any form.
SECRETS = {"proxy": PROXY_SECRET, "ssh": SSH_SECRET, "cert": CERT_SECRET}


def _point_the_layout_at(root, monkeypatch):
    """Point the shipped layout at `root`: every store lives in PERSONA_HOME,
    exactly as it does in production, so the quarantine files land in the
    directory the wipe is standing in.

    PERSONA_HOME is read from the config MODULE at call time by the wipe, so it
    is patched there; the stores get env overrides pointing INSIDE that same
    home (a relocation outside the home is a stated bound of this fix, not a
    case these tests claim to cover)."""
    import src.core.config as cfg
    import src.services.profile.manager as mod
    import src.services.ssh.store as ssh_mod

    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cfg, "PERSONA_HOME", str(root), raising=False)
    # ssh/store.py binds PERSONA_HOME at import; cert/trash resolve it per call.
    monkeypatch.setattr(ssh_mod, "PERSONA_HOME", str(root), raising=False)
    for m in (cfg, mod):
        monkeypatch.setattr(
            m, "PROFILES_FILE", str(root / "profiles.json"), raising=False
        )
        monkeypatch.setattr(m, "DATA_DIR", str(root / "data"), raising=False)
    monkeypatch.setenv("PERSONA_TRASH_FILE", str(root / "trash.json"))
    monkeypatch.setenv("PERSONA_SSH_HOSTS_FILE", str(root / "ssh_hosts.json"))
    monkeypatch.setenv("PERSONA_CERTS_FILE", str(root / "certificates.json"))
    return root


@pytest.fixture
def home(tmp_path, monkeypatch):
    """The ordinary case: a home whose path holds no glob metacharacters."""
    return _point_the_layout_at(tmp_path, monkeypatch)


@pytest.fixture
def bracketed_home(tmp_path, monkeypatch):
    """The SAME layout under a home whose directory name contains `[`.

    PERSONA_HOME is operator-overridable (config.py, "e.g. for a portable
    layout"), and glob interprets metacharacters across the WHOLE pattern —
    directory portion included. `[` and `]` are legal on both POSIX and Windows
    (unlike `*` and `?`, which Windows forbids), and a user-named portable/USB
    directory is exactly where an overridden home comes from."""
    return _point_the_layout_at(tmp_path / "Persona[1] portable", monkeypatch)


def _manager(home) -> ProfileManager:
    pm = ProfileManager()
    pm.set_trash(TrashStore())
    return pm


def _tear(path) -> None:
    """Damage the file the way a torn write actually looks: the tail is lost,
    the credential records themselves are intact. A file overwritten with junk
    would prove nothing — the point is that a REAL corruption leaves the secret
    readable in the quarantined copy."""
    raw = pathlib.Path(path).read_bytes()
    pathlib.Path(path).write_bytes(raw[: max(1, len(raw) - 3)])


def _write_credentials(home) -> None:
    """Save a real credential of each kind through the REAL store APIs, so the
    bytes on disk got there the way the shipped code puts them there."""
    ProxyStore(path=str(home / "proxies.json")).add(
        "p1", f"socks5://user:{PROXY_SECRET}@1.2.3.4:1080"
    )
    SSHHostStore().add(
        SSHHost(name="h1", host="10.0.0.1", username="root", password=SSH_SECRET)
    )
    CertStore().add(
        Certificate(name="c1", p12_path=str(home / "c1.p12"), password=CERT_SECRET)
    )


def _quarantine_the_stores(home) -> None:
    """Drive the SHIPPED quarantine: tear each store file, then LOAD it. The
    rename is performed by store_guard, not by the test."""
    _write_credentials(home)
    for name in ("proxies.json", "ssh_hosts.json", "certificates.json"):
        _tear(home / name)
    # Loading an unparseable file is what fires _quarantine_store_file.
    ProxyStore(path=str(home / "proxies.json"))
    SSHHostStore()
    CertStore()


def _quarantine_files(home) -> list[pathlib.Path]:
    return sorted(pathlib.Path(home).glob("*.corrupt-*"))


def _surviving_secrets(home) -> dict[str, list[str]]:
    """Which credentials are still readable in a quarantine file on disk.

    Reads BYTES IN THE HOME DIRECTORY. Deliberately not "was the helper
    called" — that assertion would survive the glob being deleted."""
    out = {}
    for p in _quarantine_files(home):
        blob = p.read_text(encoding="utf-8", errors="replace")
        holds = [kind for kind, secret in SECRETS.items() if secret in blob]
        if holds:
            out[p.name] = holds
    return out


# --- the premise these tests rest on ---


def test_the_quarantined_copy_really_holds_the_credential(home):
    """Guards the premise. If the quarantine stopped firing, or stopped copying
    the secret verbatim, every assertion below would pass vacuously."""
    _quarantine_the_stores(home)

    assert _quarantine_files(home), "quarantine never fired — nothing to clear"
    holds = _surviving_secrets(home)
    assert sorted(k for v in holds.values() for k in v) == ["cert", "proxy", "ssh"], (
        f"expected all three credential kinds in quarantined copies, got {holds}"
    )


# --- the defect itself ---


def test_no_quarantined_store_file_survives_the_wipe(home):
    """AC1/AC3/AC4. Bound to bytes on disk: delete the glob from
    _clear_quarantine_files_for_wipe and this goes RED."""
    _quarantine_the_stores(home)
    assert _quarantine_files(home)  # premise

    _manager(home).wipe_all_profiles()

    survivors = _quarantine_files(home)
    assert survivors == [], (
        "quarantined credential copies survived the panic wipe: "
        f"{[p.name for p in survivors]}"
    )
    assert _surviving_secrets(home) == {}


def test_no_credential_of_any_kind_is_readable_in_the_home_after_the_wipe(home):
    """The operator-facing claim, asserted on content rather than on filenames:
    after the strongest destructive gesture the product has, no file left in the
    home holds any of the three credentials."""
    _quarantine_the_stores(home)

    _manager(home).wipe_all_profiles()

    for path in pathlib.Path(home).rglob("*"):
        if not path.is_file():
            continue
        blob = path.read_text(encoding="utf-8", errors="replace")
        leaked = [kind for kind, secret in SECRETS.items() if secret in blob]
        assert not leaked, f"{path.name} still holds {leaked} after the wipe"


def test_the_wipe_really_destroys_what_it_claims_to(home):
    """THE CONTROL ARM. Every test above could pass because the probe failed to
    create anything rather than because the sweep worked, and that reads as a
    clean pass instead of a broken instrument. This pins the wipe to the things
    it genuinely destroys, so "the quarantine file is gone" is a real comparison.

    NOTE ON WHAT IS *NOT* ASSERTED HERE, because it is easy to get wrong and this
    test file is where the correction belongs. The live credential stores
    (proxies.json, ssh_hosts.json, certificates.json) are NOT destroyed by the
    panic wipe — measured on this tree, they survive it with their credentials
    intact. `wipe_all_profiles` destroys profiles, the trash and the logs; the
    credential stores are outside its stated scope, and that is existing shipped
    behaviour this slice does not change.

    A probe that tears those files first APPEARS to show the wipe removing them,
    because the QUARANTINE RENAME has already moved each one aside — `exists()`
    then reads False for a reason that has nothing to do with the wipe. That
    confound is why this control asserts the profile dir and the trash instead,
    both of which the wipe demonstrably does destroy."""
    pm = _manager(home)

    # 1. A profile's data dir — rmtree'd by the wipe.
    pm.add_profile("acme-viktor", "", "windows")
    data_dir = pathlib.Path(pm._data_path("acme-viktor"))
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "Cookies").write_text("logged-in", encoding="utf-8")

    # 2. The live trash, which holds credentials verbatim — purged by the wipe.
    pm._trash().add(
        kind="proxy",
        name="p1",
        payload={"name": "p1", "url": f"socks5://user:{PROXY_SECRET}@1.2.3.4:1080"},
    )
    trash_path = home / "trash.json"
    assert PROXY_SECRET in trash_path.read_text(
        encoding="utf-8"
    ), "probe broken: trash never held it"

    assert pm.wipe_all_profiles() == 1

    assert not data_dir.exists(), "the wipe did not rmtree the profile data dir"
    live_trash = (
        trash_path.read_text(encoding="utf-8", errors="replace")
        if trash_path.exists()
        else ""
    )
    assert PROXY_SECRET not in live_trash, "the wipe did not purge the live trash"


def test_the_trash_quarantine_does_not_survive_the_wipe(home):
    """The sharper instance: trash.json carries proxy creds, SSH passwords and
    .p12 passwords VERBATIM ("The record's own to_dict(), verbatim"), so a
    single trash.json.corrupt-<ts> is a second copy of all three at once —
    precisely the copy trash/store.py's docstring says does not exist."""
    trash = TrashStore()
    trash.add(
        kind="proxy",
        name="p1",
        payload={"name": "p1", "url": f"socks5://user:{PROXY_SECRET}@1.2.3.4:1080"},
    )
    trash_path = home / "trash.json"
    assert PROXY_SECRET in trash_path.read_text(
        encoding="utf-8"
    ), "trash never held the credential"

    _tear(trash_path)
    TrashStore()  # loading the torn file quarantines it

    quarantined = list(pathlib.Path(home).glob("trash.json.corrupt-*"))
    assert quarantined, "trash quarantine never fired"
    assert PROXY_SECRET in quarantined[0].read_text(encoding="utf-8", errors="replace")

    _manager(home).wipe_all_profiles()

    assert list(pathlib.Path(home).glob("trash.json.corrupt-*")) == []
    # CONTROL: the live trash is purged by the same wipe.
    live = (
        trash_path.read_text(encoding="utf-8", errors="replace")
        if trash_path.exists()
        else ""
    )
    assert PROXY_SECRET not in live


# --- the home path is data, not a pattern ---


def test_a_home_whose_name_contains_a_bracket_is_still_swept(bracketed_home):
    """The sweep must treat PERSONA_HOME as a PATH, not as a glob pattern.

    glob interprets metacharacters across the whole pattern, so joining an
    unescaped home produces `/tmp/.../Persona[1] portable/*.corrupt-*`, in which
    `[1]` is a character class matching the single character `1` — a directory
    that does not exist. glob.glob then returns [], the loop body never runs, and
    the wipe completes and reports success: no exception, no empty-result branch,
    no signal anywhere that the sweep did nothing. That silent, safe-looking
    failure is worse than a crash, and it re-breaks the exact claim this slice
    exists to restore.

    Bound to BYTES ON DISK, so it falsifies cleanly: drop the glob.escape and
    this goes RED with the credential still readable in the home."""
    _quarantine_the_stores(bracketed_home)
    assert _quarantine_files(bracketed_home), "premise: quarantine never fired"

    _manager(bracketed_home).wipe_all_profiles()

    survivors = _quarantine_files(bracketed_home)
    assert survivors == [], (
        "the sweep no-opped on a home containing a glob metacharacter; "
        f"quarantined credential copies survived: {[p.name for p in survivors]}"
    )
    assert _surviving_secrets(bracketed_home) == {}


def test_the_trash_quarantine_does_not_survive_a_wipe_from_a_bracketed_home(
    bracketed_home,
):
    """The same failure driven through the sharper instance: a single
    trash.json.corrupt-<ts> is a second copy of all three credential kinds at
    once. This is the case that was measured still shipping under a bracketed
    home, so it is pinned here rather than left to the aggregate test above."""
    trash = TrashStore()
    trash.add(
        kind="proxy",
        name="p1",
        payload={"name": "p1", "url": f"socks5://user:{PROXY_SECRET}@1.2.3.4:1080"},
    )
    trash_path = bracketed_home / "trash.json"
    assert PROXY_SECRET in trash_path.read_text(
        encoding="utf-8"
    ), "trash never held the credential"

    _tear(trash_path)
    TrashStore()  # loading the torn file quarantines it

    quarantined = list(pathlib.Path(bracketed_home).glob("trash.json.corrupt-*"))
    assert quarantined, "trash quarantine never fired"
    assert PROXY_SECRET in quarantined[0].read_text(encoding="utf-8", errors="replace")

    _manager(bracketed_home).wipe_all_profiles()

    assert list(pathlib.Path(bracketed_home).glob("trash.json.corrupt-*")) == []
    # CONTROL: the live trash is purged by the same wipe, in the same home.
    live = (
        trash_path.read_text(encoding="utf-8", errors="replace")
        if trash_path.exists()
        else ""
    )
    assert PROXY_SECRET not in live


# --- the shape of the sweep ---


def test_a_wipe_on_a_home_with_no_quarantine_files_is_unchanged(home):
    """AC6. The sweep must be a no-op when there is nothing to sweep."""
    pm = _manager(home)
    pm.add_profile("keeper", "", "windows")
    before = sorted(p.name for p in pathlib.Path(home).iterdir())

    assert pm.wipe_all_profiles() == 1

    after = sorted(p.name for p in pathlib.Path(home).iterdir())
    # profiles.json survives (emptied, not unlinked), and nothing extra is gone.
    assert "profiles.json" in after
    assert set(before) - set(after) == set()


def test_one_unremovable_file_neither_raises_nor_stops_the_rest(home, monkeypatch):
    """AC5. Mirrors _clear_logs_for_wipe: a wipe that raised because one file was
    locked (Windows) would be worse than the residue it failed to clear, and one
    stuck file must not shield the others."""
    _quarantine_the_stores(home)
    stuck = sorted(pathlib.Path(home).glob("proxies.json.corrupt-*"))[0]

    real_remove = os.remove

    def flaky_remove(path, *a, **kw):
        if str(path) == str(stuck):
            raise OSError("locked by another process")
        return real_remove(path, *a, **kw)

    monkeypatch.setattr(os, "remove", flaky_remove)

    # Must NOT raise.
    _manager(home).wipe_all_profiles()

    survivors = [p.name for p in _quarantine_files(home)]
    assert survivors == [stuck.name], (
        f"one locked file stopped the rest of the sweep: {survivors}"
    )
