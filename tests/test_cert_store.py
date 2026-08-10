import json
import os
import sys

import pytest

from src.services.cert.store import Certificate, CertStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_CERTS_FILE", str(tmp_path / "certs.json"))
    return CertStore()


def test_add_get_persist(store):
    c = Certificate(
        name="admin", p12_path="/vault/admin.p12", password="s3cret",
        url="https://admin.example.com/login",
    )
    assert store.add(c)
    assert store.get("admin").p12_path == "/vault/admin.p12"
    assert store.get("admin").url == "https://admin.example.com/login"
    raw = json.load(open(os.environ["PERSONA_CERTS_FILE"]))
    assert raw["admin"]["password"] == "s3cret"
    assert raw["admin"]["url"] == "https://admin.example.com/login"


def test_old_cert_without_url_loads_clean(store, tmp_path):
    # a certificates.json written before the url field must still load
    import json as _json
    with open(os.environ["PERSONA_CERTS_FILE"], "w", encoding="utf-8") as f:
        _json.dump(
            {"legacy": {"name": "legacy", "p12_path": "/a.p12", "password": "p"}},
            f,
        )
    reloaded = CertStore()
    assert reloaded.get("legacy").url == ""


def test_add_duplicate_rejected(store):
    store.add(Certificate(name="admin", p12_path="/a.p12"))
    assert store.add(Certificate(name="admin", p12_path="/b.p12")) is False


def test_update_and_rename(store):
    store.add(Certificate(name="admin", p12_path="/a.p12"))
    assert store.update("admin", Certificate(name="admin2", p12_path="/b.p12"))
    assert store.get("admin") is None
    assert store.get("admin2").p12_path == "/b.p12"


def test_remove(store):
    store.add(Certificate(name="admin", p12_path="/a.p12"))
    assert store.remove("admin")
    assert store.get("admin") is None
    assert store.remove("admin") is False


def test_survives_reload(store):
    store.add(Certificate(name="admin", p12_path="/a.p12", password="pw"))
    reloaded = CertStore()
    assert reloaded.get("admin").password == "pw"


def test_list_and_names(store):
    store.add(Certificate(name="a", p12_path="/a.p12"))
    store.add(Certificate(name="b", p12_path="/b.p12"))
    assert {c.name for c in store.list()} == {"a", "b"}
    assert set(store.names()) == {"a", "b"}


def test_get_missing_returns_none(store):
    assert store.get("nope") is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_certificates_file_is_private(store):
    store.add(Certificate(name="admin", p12_path="/a.p12", password="pw"))
    mode = os.stat(os.environ["PERSONA_CERTS_FILE"]).st_mode & 0o777
    assert mode == 0o600


def test_corrupt_file_is_quarantined_not_overwritten(store, tmp_path):
    # audit7 #7: certificates.json holds every .p12 bundle password. If the file
    # is unreadable JSON, the next _save() would clobber it with the empty
    # in-memory dict — silently destroying every stored password. It must be moved
    # aside (.corrupt-<ts>) so a save can't overwrite it.
    path = os.environ["PERSONA_CERTS_FILE"]
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ this is not valid json")
    reloaded = CertStore()
    assert reloaded.certs == {}
    # original preserved under a .corrupt-* sibling
    d = os.path.dirname(path)
    corrupt = [n for n in os.listdir(d) if ".corrupt-" in n]
    assert corrupt, "corrupt certificates.json must be quarantined"
    # a subsequent save writes a fresh file, leaving the quarantined copy intact
    reloaded.add(Certificate(name="admin", p12_path="/a.p12", password="pw"))
    assert json.load(open(path))["admin"]["password"] == "pw"


def test_save_blocked_when_quarantine_fails(store, tmp_path, monkeypatch):
    # if the corrupt file can't be moved aside, saving must be disabled rather
    # than overwrite the unreadable-but-secret-bearing file with an empty dict.
    path = os.environ["PERSONA_CERTS_FILE"]
    with open(path, "w", encoding="utf-8") as f:
        f.write("}{ broken")

    import pathlib as _pl
    monkeypatch.setattr(
        _pl.Path, "rename",
        lambda self, target: (_ for _ in ()).throw(OSError("cannot move")),
    )
    reloaded = CertStore()
    assert reloaded._save_blocked is True
    # a save is a no-op; the broken file is left exactly as-is
    reloaded.add(Certificate(name="x", p12_path="/x.p12"))
    assert open(path).read() == "}{ broken"


def test_malformed_record_skipped_not_aborting_load(store):
    # one bad record must not abort the whole load and drop every later cert's
    # password on the next save.
    path = os.environ["PERSONA_CERTS_FILE"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "good": {"name": "good", "p12_path": "/g.p12", "password": "pw"},
                "bad": ["not", "a", "dict"],
            },
            f,
        )
    reloaded = CertStore()
    assert reloaded.get("good").password == "pw"
    assert reloaded.get("bad") is None
    # not quarantined — the file parsed as JSON, only one record was malformed
    d = os.path.dirname(path)
    assert not [n for n in os.listdir(d) if ".corrupt-" in n]


def test_import_p12_copies_into_store_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_CERTS_FILE", str(tmp_path / "certs.json"))
    certs_dir = tmp_path / "vault"
    monkeypatch.setenv("PERSONA_CERTS_DIR", str(certs_dir))

    src = tmp_path / "downloads" / "admin.p12"
    src.parent.mkdir()
    src.write_bytes(b"PKCS12-BYTES")

    st = CertStore()
    dest = st.import_p12("admin", str(src))
    # copied under the store dir, original untouched
    assert os.path.dirname(dest) == str(certs_dir)
    assert os.path.isfile(dest)
    assert open(dest, "rb").read() == b"PKCS12-BYTES"
    assert src.exists()
    # a different name yields a distinct file so certs never collide
    dest2 = st.import_p12("staging", str(src))
    assert dest2 != dest
