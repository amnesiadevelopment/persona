"""The self-update probe launches the new build with PERSONA_SELFTEST=1 and
waits for SELFTEST_OK. In the flet-bundled build, returning from main() leaves
the Flutter host process alive, so the probe used to hang for its full 30s
timeout on every update and fall into the fragile alive-after-settle fallback.
main() must hard-exit right after printing the token."""

import pytest

import src.main as main_mod


class _GuiPathEntered(Exception):
    pass


def _forbid_gui(monkeypatch):
    monkeypatch.setattr(
        main_mod, "Container",
        lambda: (_ for _ in ()).throw(_GuiPathEntered()),
    )


def test_selftest_prints_token_then_hard_exits(monkeypatch, capsys):
    monkeypatch.setenv("PERSONA_SELFTEST", "1")
    _forbid_gui(monkeypatch)
    exited = []

    def fake_exit(code):
        exited.append(code)
        raise SystemExit(code)

    monkeypatch.setattr(main_mod.os, "_exit", fake_exit)
    with pytest.raises(SystemExit):
        main_mod.main()
    assert exited == [0]
    assert "SELFTEST_OK" in capsys.readouterr().out


def test_normal_launch_skips_selftest_exit(monkeypatch, capsys):
    monkeypatch.delenv("PERSONA_SELFTEST", raising=False)
    _forbid_gui(monkeypatch)
    monkeypatch.setattr(
        main_mod.os, "_exit",
        lambda code: (_ for _ in ()).throw(AssertionError("hard-exited normally")),
    )
    with pytest.raises(_GuiPathEntered):
        main_mod.main()
    assert "SELFTEST_OK" not in capsys.readouterr().out
