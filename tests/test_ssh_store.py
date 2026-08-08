import json
import os
import pathlib
import sys

import pytest

from src.services.ssh.store import SSHHost, SSHHostStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_SSH_HOSTS_FILE", str(tmp_path / "ssh.json"))
    return SSHHostStore()


def test_add_get_persist(store):
    h = SSHHost(name="box", host="1.2.3.4", username="root", profile="IE Main")
    assert store.add(h)
    assert store.get("box").host == "1.2.3.4"
    # persisted to disk
    raw = json.load(open(os.environ["PERSONA_SSH_HOSTS_FILE"]))
    assert raw["box"]["profile"] == "IE Main"


def test_add_duplicate_rejected(store):
    store.add(SSHHost(name="box", host="h"))
    assert store.add(SSHHost(name="box", host="other")) is False


def test_update_and_rename(store):
    store.add(SSHHost(name="box", host="h"))
    assert store.update("box", SSHHost(name="box2", host="h2"))
    assert store.get("box") is None
    assert store.get("box2").host == "h2"


def test_remove(store):
    store.add(SSHHost(name="box", host="h"))
    assert store.remove("box")
    assert store.get("box") is None
    assert store.remove("box") is False


def test_survives_reload(store, tmp_path):
    store.add(SSHHost(name="box", host="h", key_path="/k"))
    store2 = SSHHostStore()
    assert store2.get("box").key_path == "/k"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_ssh_hosts_file_is_private(store):
    store.add(SSHHost(name="box", host="h", password="secret"))
    mode = os.stat(os.environ["PERSONA_SSH_HOSTS_FILE"]).st_mode & 0o777
    assert mode == 0o600


def test_one_malformed_host_is_skipped_not_fatal(store, tmp_path):
    path = tmp_path / "ssh.json"
    path.write_text(
        json.dumps(
            {
                "good": {"name": "good", "host": "1.2.3.4", "port": 22},
                "bad": {"name": "bad", "port": "not-an-int"},  # int(port) raises
            }
        ),
        encoding="utf-8",
    )
    s = SSHHostStore()
    assert s.get("good").host == "1.2.3.4"
    assert s.get("bad") is None


def test_corrupt_file_quarantined_not_overwritten(store, tmp_path):
    path = tmp_path / "ssh.json"
    path.write_text("{ not valid json", encoding="utf-8")
    s = SSHHostStore()
    # An unreadable file holds passwords + passphrases; the next save must not
    # overwrite it with the empty in-memory dict — it's quarantined instead.
    s.add(SSHHost(name="box", host="h", password="secret"))
    backups = list(tmp_path.glob("ssh.json.corrupt-*"))
    assert backups, "corrupt ssh_hosts.json must be quarantined"
    assert backups[0].read_text(encoding="utf-8") == "{ not valid json"


def test_save_blocked_when_quarantine_fails(store, tmp_path, monkeypatch):
    path = tmp_path / "ssh.json"
    path.write_text("{ broken", encoding="utf-8")

    def boom(*a, **k):
        raise OSError("cannot rename")

    monkeypatch.setattr(pathlib.Path, "rename", boom)
    s = SSHHostStore()
    s.add(SSHHost(name="box", host="h", password="secret"))
    assert path.read_text(encoding="utf-8") == "{ broken"


def test_resolver_uses_profile_proxy(monkeypatch):
    from src.services.ssh.resolver import target_for
    from src.services.ssh.store import SSHHost

    class FakeProxyStore:
        def resolve(self, ref):
            return "socks5://u:p@1.1.1.1:1080" if ref == "myproxy" else None

    class FakeProfile:
        proxy = "myproxy"

    class FakePM:
        profiles = {"P1": FakeProfile()}

    host = SSHHost(name="b", host="srv", username="root", profile="P1")
    target = target_for(host, FakePM(), FakeProxyStore())
    assert target.proxy_url == "socks5://u:p@1.1.1.1:1080"
    assert target.host == "srv"


def test_resolver_direct_when_no_profile():
    from src.services.ssh.resolver import target_for
    from src.services.ssh.store import SSHHost

    class FakeProxyStore:
        def resolve(self, ref):
            return None

    host = SSHHost(name="b", host="srv", profile="")
    target = target_for(host, None, FakeProxyStore())
    assert target.proxy_url == ""
