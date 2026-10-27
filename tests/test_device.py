from pathlib import Path

import pytest

from rks98 import device
from rks98.device import DeviceError, HidTransport, Keyboard, feature_report_sizes
from rks98.protocol import packet

DESCRIPTOR = bytes.fromhex(
    "0600ff0901a1018505150026ff00190029ff75089505b102c0"
    "0600ff0901a1018506150026ff00190129027508960704b102c0"
    "0602ff0902a1018509150026ff007508960702b102c0"
)


def test_descriptor_matches_actual_keyboard_sizes():
    assert feature_report_sizes(DESCRIPTOR) == {5: 5, 6: 1031, 9: 519}


def test_descriptor_global_push_pop_and_multiple_features():
    descriptor = bytes.fromhex("850975089502b102a4850675109501b102b4b102")
    assert feature_report_sizes(descriptor) == {9: 4, 6: 2}


@pytest.mark.parametrize("descriptor", [b"\x96\x01", b"\xb4", b"\xa4", b"\xfe"])
def test_malformed_descriptors_rejected(descriptor):
    with pytest.raises(DeviceError):
        feature_report_sizes(descriptor)


def test_discovery_filters_and_deduplicates_without_opening(monkeypatch):
    infos = [
        {"path": b"/dev/hidraw3", "usage_page": 1},
        {"path": b"/dev/hidraw5", "usage_page": 0xFF00, "product_string": "Keyboard"},
        {"path": b"/dev/hidraw5", "usage_page": 0xFF02},
    ]
    monkeypatch.setattr(device.hid, "enumerate", lambda vid, pid: infos)
    monkeypatch.setattr(Path, "read_bytes", lambda self: DESCRIPTOR)
    devices = device.discover()
    assert len(devices) == 1
    assert devices[0].path == b"/dev/hidraw5"
    assert devices[0].supported


def test_wrong_descriptor_is_not_supported(monkeypatch):
    monkeypatch.setattr(
        device.hid,
        "enumerate",
        lambda *_: [
            {"path": b"/dev/hidraw5", "usage_page": 0xFF00},
        ],
    )
    monkeypatch.setattr(Path, "read_bytes", lambda self: bytes.fromhex("85067508960704b102"))
    assert not device.discover()[0].supported


class Handle:
    def __init__(self):
        self.closed = False
        self.sent = []
        self.short = False
        self.error = False

    def open_path(self, path):
        if self.error:
            raise OSError("permission denied")

    def close(self):
        self.closed = True

    def send_feature_report(self, data):
        self.sent.append(data)
        return len(data) - int(self.short)

    def get_feature_report(self, report, size):
        assert report == 9 and size == 520
        return list(self.sent[-1])


def test_transport_uses_feature_reports_and_closes(monkeypatch):
    handle = Handle()
    monkeypatch.setattr(device.hid, "device", lambda: handle)
    monkeypatch.setattr(device.time, "sleep", lambda _: None)
    keyboard = Keyboard(b"/dev/hidraw5", "test", True, "")
    with HidTransport(keyboard) as transport:
        data = packet(0x84, 128)
        assert transport.exchange(data) == data
        with pytest.raises(DeviceError):
            transport.send(bytes(520))
        handle.short = True
        with pytest.raises(DeviceError, match="Short USB write"):
            transport.send(data)
    assert handle.closed


def test_permission_error_is_actionable_and_closes_handle(monkeypatch):
    handle = Handle()
    handle.error = True
    monkeypatch.setattr(device.hid, "device", lambda: handle)
    with pytest.raises(DeviceError, match="udev"):
        with HidTransport(Keyboard(b"/dev/hidraw5", "test", True, "")):
            pytest.fail("Should not open")
    assert handle.closed


def test_unsupported_interface_never_opens():
    with pytest.raises(DeviceError):
        HidTransport(Keyboard(b"/dev/hidraw3", "test", False, ""))


@pytest.mark.parametrize(("requested", "actual"), [(0, 1), (250, 250)])
def test_input_reads_use_a_finite_timeout(monkeypatch, requested, actual):
    handle = Handle()

    def read(length, timeout):
        assert length == 64 and timeout == actual
        return [9, 10, 6, 1]

    handle.read = read
    monkeypatch.setattr(device.hid, "device", lambda: handle)
    with HidTransport(Keyboard(b"/dev/hidraw5", "test", True, "")) as transport:
        assert transport.read_input(requested) == bytes((9, 10, 6, 1))


def test_input_read_error_is_actionable(monkeypatch):
    handle = Handle()

    def read(*_):
        raise OSError("disconnected")

    handle.read = read
    monkeypatch.setattr(device.hid, "device", lambda: handle)
    with HidTransport(Keyboard(b"/dev/hidraw5", "test", True, "")) as transport:
        with pytest.raises(DeviceError, match="USB input read failed"):
            transport.read_input(10)
