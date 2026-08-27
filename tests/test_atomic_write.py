"""Stores must write JSON atomically so a crash mid-save can't corrupt the file,
and credential files must not be world-readable.
"""
import json
import os
import sys

import pytest

from src.utils.atomic import atomic_write_json


def test_writes_json_content(tmp_path):
    p = tmp_path / "d.json"
    atomic_write_json(str(p), {"a": 1, "b": [2, 3]})
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1, "b": [2, 3]}


def test_no_temp_file_left_behind(tmp_path):
    p = tmp_path / "d.json"
    atomic_write_json(str(p), {"x": 1})
    leftovers = [f for f in os.listdir(tmp_path) if f != "d.json"]
    assert leftovers == []


def test_existing_file_preserved_on_write_failure(tmp_path):
    p = tmp_path / "d.json"
    atomic_write_json(str(p), {"good": 1})
    # a value json can't serialize must NOT clobber the existing good file
    with pytest.raises(TypeError):
        atomic_write_json(str(p), {"bad": object()})
    assert json.loads(p.read_text(encoding="utf-8")) == {"good": 1}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_private_mode_sets_0600(tmp_path):
    p = tmp_path / "secret.json"
    atomic_write_json(str(p), {"password": "s3cret"}, private=True)
    assert (os.stat(p).st_mode & 0o777) == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_default_mode_not_private(tmp_path):
    p = tmp_path / "plain.json"
    atomic_write_json(str(p), {"a": 1})
    # a normal (non-credential) file keeps normal perms
    assert (os.stat(p).st_mode & 0o600) == 0o600  # at least owner rw
