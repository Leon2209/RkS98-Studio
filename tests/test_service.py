from dataclasses import replace

import pytest

from rks98 import service
from rks98.protocol import EFFECTS, FIXED_COLOR_EFFECTS, Lighting, ProtocolError, packet
from rks98.service import Controller


class FakeTransport:
    def __init__(self):
        profile = bytearray(128)
        profile[10] = 1
        profile[58:60] = bytes((4, 0x20))
        self.profile = bytes(profile)
        self.colors = bytes(420)
        self.writes = []
        self.requests = []
        self.ignore_color_write = False
        self.ignore_profile_write = False
        self.bad_response = False
        self.reject_color_query = False
        self.rejection_padding = 0

    def send(self, data):
        self.writes.append(data)
        if data[1] == 0x04 and not self.ignore_profile_write:
            self.profile = data[8:136]
        if data[1] == 0x0A and not self.ignore_color_write:
            self.colors = data[8:428]

    def exchange(self, data):
        self.requests.append(data)
        if self.bad_response:
            return bytes(520)
        if data[1] == 0x8A and self.reject_color_query:
            return bytes((9, 0x0A, 6, data[3], 1, 0, 0, 0)) + bytes([0xA5]) * self.rejection_padding
        payload = self.profile if data[1] == 0x84 else self.colors
        return packet(data[1], len(payload), data[3], payload)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(service.time, "sleep", lambda _: None)


def test_read_requests_never_write_configuration():
    transport = FakeTransport()
    lighting = Controller(transport).read_lighting(2)
    assert lighting == Lighting(1, 4, 2, (0, 0, 0))
    assert not transport.writes
    assert [data[1] for data in transport.requests] == [0x84, 0x8A]
    assert all(data[3] == 2 for data in transport.requests)


def test_apply_reads_before_writing_and_verifies_both():
    transport = FakeTransport()
    desired = Lighting(2, 3, 1, (255, 0, 128), 7)
    assert Controller(transport).apply_lighting(1, desired) == desired
    assert [data[1] for data in transport.writes] == [0x0A, 0x04]
    assert [data[1] for data in transport.requests] == [0x84, 0x8A, 0x8A, 0x84, 0x8A]
    assert all(data[3] == 1 for data in transport.writes)


def test_bad_initial_read_prevents_all_configuration_writes():
    transport = FakeTransport()
    transport.bad_response = True
    with pytest.raises(ProtocolError):
        Controller(transport).apply_lighting(0, Lighting(1, 2, 2, (1, 2, 3)))
    assert not transport.writes


def test_failed_color_readback_stops_before_profile_write():
    transport = FakeTransport()
    transport.ignore_color_write = True
    with pytest.raises(ProtocolError, match="partially applied"):
        Controller(transport).apply_lighting(0, Lighting(2, 2, 2, (1, 2, 3)))
    assert [data[1] for data in transport.writes] == [0x0A]


def test_failed_profile_readback_is_reported():
    transport = FakeTransport()
    transport.ignore_profile_write = True
    with pytest.raises(ProtocolError, match="partially applied"):
        Controller(transport).apply_lighting(0, Lighting(2, 2, 2, (0, 0, 0)))


def test_unchanged_settings_do_not_write():
    transport = FakeTransport()
    controller = Controller(transport)
    desired = controller.read_lighting(0)
    assert controller.apply_lighting(0, desired) == desired
    assert not transport.writes


def test_clock_reports_transfer_not_verified_success():
    transport = FakeTransport()
    message = Controller(transport).sync_clock()
    assert "no verified readback" in message
    assert len(transport.writes) == 1
    assert transport.writes[0][1] == 11
    assert not transport.requests


def test_volume_sends_one_status_packet_without_configuration_writes():
    transport = FakeTransport()
    profile, colors = transport.profile, transport.colors
    message = Controller(transport).sync_volume(65)
    assert "no verified readback" in message
    assert len(transport.writes) == 1
    assert transport.writes[0][:11] == bytes.fromhex("09 0b 00 00 01 00 0e 00 41 00 00")
    assert (transport.profile, transport.colors) == (profile, colors)
    assert not transport.requests


def test_invalid_volume_never_sends():
    transport = FakeTransport()
    with pytest.raises(ProtocolError):
        Controller(transport).sync_volume(101)
    assert not transport.writes


@pytest.mark.parametrize("padding", [0, 93, 512])
def test_rejected_color_table_allows_profile_only_updates(caplog, padding):
    caplog.set_level("DEBUG", logger="rks98.service")
    transport = FakeTransport()
    transport.reject_color_query = True
    transport.rejection_padding = padding
    controller = Controller(transport)
    assert controller.read_lighting(0).color is None
    desired = Lighting(14, 16, 0, None, 7)
    assert controller.apply_lighting(0, desired) == desired
    assert [data[1] for data in transport.writes] == [0x04]
    assert "Custom RGB requires write-only mode" in caplog.text


def test_rejected_color_table_blocks_custom_color_without_writes():
    transport = FakeTransport()
    transport.reject_color_query = True
    with pytest.raises(ProtocolError, match="Custom color is unavailable"):
        Controller(transport).apply_lighting(0, Lighting(1, 19, 0, (255, 0, 0)))
    assert not transport.writes


@pytest.mark.parametrize("effect", EFFECTS)
def test_all_effects_from_factory_state_with_padded_color_rejection(effect):
    transport = FakeTransport()
    profile = bytearray(transport.profile)
    profile[58:60] = bytes((20, 0x17))
    transport.profile = bytes(profile)
    transport.reject_color_query = True
    transport.rejection_padding = 93
    desired = Lighting(effect, 20, 1, None, 7)
    result = Controller(transport).apply_lighting(0, desired)
    assert result.effect == effect
    if effect in FIXED_COLOR_EFFECTS:
        assert result == replace(desired, palette_index=0)
    elif effect != 0:
        assert result == desired
    assert all(data[1] == 0x04 for data in transport.writes)
    assert transport.profile[10] == effect


@pytest.mark.parametrize("palette_index", [0, 1, 2, 7])
def test_static_palette_changes_use_profile_not_rejected_rgb_table(palette_index):
    transport = FakeTransport()
    transport.reject_color_query = True
    transport.rejection_padding = 93
    profile = bytearray(transport.profile)
    profile[58:60] = bytes((20, 0x11))
    transport.profile = bytes(profile)
    controller = Controller(transport)
    original = controller.read_lighting(0)
    assert original.palette_index == 1
    desired = Lighting(1, 20, 1, None, palette_index)
    assert controller.apply_lighting(0, desired) == desired
    assert transport.profile[59] == 0x10 | palette_index
    assert all(data[1] == 0x04 for data in transport.writes)
    transport.writes.clear()
    assert controller.apply_lighting(0, desired) == desired
    assert not transport.writes


def test_write_only_custom_rgb_uses_saved_table_and_verifies_only_profile():
    transport = FakeTransport()
    transport.reject_color_query = True
    saved = bytes(i % 256 for i in range(420))
    desired = Lighting(1, 19, 2, (0, 132, 255), 7)
    result = Controller(transport).apply_custom_color(0, desired, saved)
    assert not result.color_verified
    assert result.lighting == Lighting(1, 19, 2, (0, 132, 255), 0)
    assert result.colors[21:24] == bytes((0, 132, 255))
    assert result.colors[:21] == saved[:21]
    assert result.colors[24:] == saved[24:]
    assert [data[1] for data in transport.writes] == [0x0A, 0x04]
    assert [data[1] for data in transport.requests] == [0x84, 0x8A, 0x84]
    # A cached table is never mistaken for proof of current device state.
    transport.writes.clear()
    Controller(transport).apply_custom_color(0, result.lighting, result.colors)
    assert [data[1] for data in transport.writes] == [0x0A, 0x04]


def test_first_custom_write_initializes_unknown_entries_and_preserves_known_ones_later():
    from rks98.protocol import default_color_table

    transport = FakeTransport()
    transport.reject_color_query = True
    controller = Controller(transport)
    first = controller.apply_custom_color(0, Lighting(1, 20, 1, (0, 132, 255)))
    assert first.colors[:21] == default_color_table()[:21]
    second = controller.apply_custom_color(
        0,
        Lighting(2, 20, 1, (255, 32, 96)),
        first.colors,
    )
    assert second.colors[21:24] == bytes((0, 132, 255))
    assert second.colors[42:45] == bytes((255, 32, 96))
    assert len(second.colors) == 420


def test_custom_rgb_uses_real_table_in_preference_to_local_history():
    transport = FakeTransport()
    transport.colors = bytes([123]) * 420
    result = Controller(transport).apply_custom_color(
        1,
        Lighting(1, 19, 2, (0, 132, 255)),
        bytes(420),
    )
    assert result.color_verified
    assert result.colors[:21] == bytes([123]) * 21


@pytest.mark.parametrize("failure", ["color", "profile", "saved"])
def test_custom_color_failures_are_explicit(failure):
    transport = FakeTransport()
    transport.ignore_color_write = failure == "color"
    transport.ignore_profile_write = failure == "profile"
    with pytest.raises(ProtocolError):
        Controller(transport).apply_custom_color(
            0,
            Lighting(1, 19, 2, (0, 132, 255)),
            b"invalid" if failure == "saved" else None,
        )
    if failure == "saved":
        assert not transport.writes


def test_firmware_version_is_read_without_writes():
    class VersionTransport(FakeTransport):
        def exchange(self, data):
            self.requests.append(data)
            reply = bytearray(520)
            reply[:18] = bytes((9, 0x82, 1, 0, 1, 0, 10, 0, 6, 0, 0, 0, 0, 0x23, 0, 0, 0x21, 0x17))
            return bytes(reply)

    transport = VersionTransport()
    assert Controller(transport).read_firmware_version() == "2117"
    assert [request[1] for request in transport.requests] == [0x82]
    assert not transport.writes


@pytest.mark.parametrize("effect", sorted(FIXED_COLOR_EFFECTS))
def test_fixed_color_effects_keep_stored_color_byte(effect):
    transport = FakeTransport()
    profile = bytearray(transport.profile)
    profile[57 + effect * 2] = 0x25
    transport.profile = bytes(profile)
    result = Controller(transport).apply_lighting(0, Lighting(effect, 10, 1, None, 7))
    assert transport.profile[10] == effect
    assert transport.profile[56 + effect * 2 : 58 + effect * 2] == bytes((10, 0x15))
    assert result.palette_index == 5


@pytest.mark.parametrize("effect", sorted(FIXED_COLOR_EFFECTS))
def test_fixed_color_effects_refuse_custom_rgb_without_usb_traffic(effect):
    transport = FakeTransport()
    with pytest.raises(ProtocolError, match="own colors"):
        Controller(transport).apply_custom_color(0, Lighting(effect, 10, 1, (1, 2, 3)))
    assert not transport.requests
    assert not transport.writes


class UploadTransport:
    def __init__(self, replies=None):
        self.sent = []
        self.inputs = [bytes((1, 0, 0))]  # stale keyboard report to drain
        self.replies = replies or {}

    def send(self, data):
        self.sent.append(data)
        reply = self.replies.get(len(self.sent), 1)
        if reply is not None:
            self.inputs.append(bytes((9, 0x0A, 6, reply, len(self.sent) & 0xFF)))

    def exchange(self, data):
        raise AssertionError("upload must not use feature reads")

    def read_input(self, timeout_ms):
        return self.inputs.pop(0) if self.inputs else b""


def frame(value=0):
    return bytes([value]) * service.DISPLAY_FRAME_SIZE


def test_display_upload_is_ack_paced_and_committed():
    transport = UploadTransport()
    progress = []
    assert (
        Controller(transport).upload_display(
            [frame(1), frame(2)],
            100,
            progress=lambda done, total: progress.append((done, total)),
        )
        == 2
    )
    start, *chunks, finish = transport.sent
    assert start[:13] == bytes((9, 0x0D, 0, 0, 1, 0, 5, 0, 0x40, 2, 0, 75, 0))
    assert finish[8:13] == bytes((0, 2, 0, 75, 0))
    assert len(chunks) == 430 and progress[-1] == (430, 430)
    assert chunks[0][:8] == bytes((9, 0x0C, 0, 0, 0, 0, 0, 2))
    assert chunks[214][:8] == bytes((9, 0x0C, 0, 0, 214, 0, 0, 2))
    assert chunks[215][3:6] == bytes((1, 0, 0))
    assert all(len(packet) == 520 for packet in transport.sent)


def test_display_upload_resends_rejected_chunk():
    transport = UploadTransport({2: 0})
    Controller(transport).upload_display([frame()], 0)
    assert transport.sent[1] == transport.sent[2]
    assert len(transport.sent) == 218


@pytest.mark.parametrize("frames", [[], [frame()] * 101, [bytes(10)]])
def test_invalid_display_upload_sends_nothing(frames):
    transport = UploadTransport()
    with pytest.raises(ProtocolError):
        Controller(transport).upload_display(frames, 0)
    assert transport.sent == []


def test_display_upload_timeout_aborts(monkeypatch):
    monkeypatch.setattr(service, "ACK_TIMEOUT_S", 0.01)
    transport = UploadTransport({3: None})
    with pytest.raises(ProtocolError, match="stopped responding"):
        Controller(transport).upload_display([frame()], 0)
    assert transport.sent[-1][1] == 0x0D and transport.sent[-1][8] == 0x80


def test_cancelled_display_upload_aborts():
    transport = UploadTransport()
    with pytest.raises(service.UploadCancelled):
        Controller(transport).upload_display(
            [frame()],
            0,
            cancelled=lambda: len(transport.sent) > 5,
        )
    assert transport.sent[-1][8] == 0x80 and len(transport.sent) == 7


def test_upload_cancelled_before_start_sends_nothing():
    transport = UploadTransport()
    with pytest.raises(service.UploadCancelled):
        Controller(transport).upload_display([frame()], 0, cancelled=lambda: True)
    assert transport.sent == []


def test_upload_cancelled_after_last_chunk_does_not_commit():
    transport = UploadTransport()
    stop = False

    def progress(done, total):
        nonlocal stop
        stop = done == total

    with pytest.raises(service.UploadCancelled):
        Controller(transport).upload_display(
            [frame()],
            0,
            progress=progress,
            cancelled=lambda: stop,
        )
    assert transport.sent[-1][8] == 0x80
    assert not any(packet[1] == 0x0D and packet[8] == 0 for packet in transport.sent)
