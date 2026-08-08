"""install.sh is the initial-install trust root: it must verify the downloaded
AppImage against the published sha256 BEFORE making it executable and moving it
onto PATH. A MITM'd/corrupted download must never be installed."""
import pathlib
import re


def _script():
    return (pathlib.Path(__file__).parent.parent / "install.sh").read_text()


def test_fetches_published_checksum():
    s = _script()
    assert ".sha256" in s
    assert "sha256sum" in s or "shasum" in s


def test_verify_happens_before_chmod_and_mv():
    s = _script()
    # the checksum comparison must come before the file is made executable and
    # moved onto PATH — verifying after would defeat the point.
    mismatch = s.index('"$actual" != "$expected"')
    chmod = s.index('chmod +x "$tmp"')
    mv = s.index('mv "$tmp" "$DEST/persona"')
    assert mismatch < chmod < mv


def test_aborts_on_mismatch_and_removes_tmp():
    s = _script()
    # the mismatch branch must exit non-zero and delete the bad partial file
    m = re.search(r'if \[ "\$actual" != "\$expected" \]; then(.*?)fi', s, re.S)
    assert m, "mismatch guard missing"
    body = m.group(1)
    assert "exit 1" in body
    assert 'rm -f "$tmp"' in body


def test_aborts_when_checksum_unavailable():
    s = _script()
    # if the published checksum can't be fetched, refuse to install rather than
    # falling back to installing an unverified binary.
    i = s.index('if [ -z "$expected" ]; then')
    window = s[i:i + 400]
    assert "exit 1" in window
    assert 'rm -f "$tmp"' in window
