import sys
from types import SimpleNamespace

import pytest

from rks98 import __main__, background


def test_list_does_not_run_volume_service(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["rks98", "--list"])
    monkeypatch.setattr(__main__, "discover", lambda: [])
    assert __main__.main() == 0
    assert capsys.readouterr().out.strip() == "[]"


def test_volume_service_uses_headless_entrypoint(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["rks98", "--volume-service"])
    monkeypatch.setattr(background, "run_volume_service", lambda: 7)
    assert __main__.main() == 7


def test_volume_service_start_failure_is_reported(monkeypatch, caplog):
    monkeypatch.setattr(sys, "argv", ["rks98", "--volume-service"])

    def fail():
        raise RuntimeError("Cannot acquire service lease")

    monkeypatch.setattr(background, "run_volume_service", fail)
    assert __main__.main() == 1
    assert "Cannot acquire service lease" in caplog.text


def test_list_and_service_modes_are_mutually_exclusive(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["rks98", "--list", "--volume-service"])
    with pytest.raises(SystemExit) as error:
        __main__.main()
    assert error.value.code == 2


def test_gui_holds_foreground_lease_until_exit(tmp_path, monkeypatch):
    from PySide6 import QtWidgets

    from rks98 import gui

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["rks98"])
    monkeypatch.setattr(gui, "confirm_hardware_risk", lambda: True)

    def check_lease():
        with pytest.raises(background.LeaseBusy):
            with background.ProcessLease():
                pytest.fail("GUI did not hold foreground lease")

    def window():
        check_lease()
        return SimpleNamespace(windowIcon=lambda: None, show=lambda: None)

    def event_loop():
        check_lease()
        return 0

    app = SimpleNamespace(
        setApplicationName=lambda _value: None,
        setDesktopFileName=lambda _value: None,
        setWindowIcon=lambda _value: None,
        exec=event_loop,
    )
    monkeypatch.setattr(QtWidgets, "QApplication", lambda _args: app)
    monkeypatch.setattr(gui, "MainWindow", window)
    assert __main__.main() == 0
    with background.ProcessLease():
        pass
