"""Conservative Linux HID discovery and bounded feature-report transport."""

import time
from dataclasses import dataclass
from pathlib import Path

import hidraw as hid

from .protocol import REPORT_ID, REPORT_SIZE

VENDOR_ID = 0x258A
PRODUCT_ID = 0x01AF


class DeviceError(RuntimeError):
    """A device operation failed."""


def feature_report_sizes(descriptor: bytes) -> dict[int, int]:
    """Return feature payload sizes, excluding each report's ID byte."""
    sizes: dict[int, int] = {}
    state = {7: 0, 8: 0, 9: 0}
    stack: list[dict[int, int]] = []
    index = 0
    while index < len(descriptor):
        prefix = descriptor[index]
        index += 1
        if prefix == 0xFE:
            raise DeviceError("Long HID descriptor items are not supported.")
        length = (0, 1, 2, 4)[prefix & 3]
        if index + length > len(descriptor):
            raise DeviceError("Truncated HID report descriptor.")
        value = int.from_bytes(descriptor[index : index + length], "little")
        index += length
        kind, tag = (prefix >> 2) & 3, prefix >> 4
        if kind == 1:
            if tag in state:
                state[tag] = value
            elif tag == 10:
                stack.append(state.copy())
            elif tag == 11:
                if not stack:
                    raise DeviceError("Unbalanced HID descriptor state.")
                state = stack.pop()
        elif kind == 0 and tag == 11:
            report_id = state[8]
            sizes[report_id] = sizes.get(report_id, 0) + state[7] * state[9]
    if stack:
        raise DeviceError("Unbalanced HID descriptor state.")
    return {report_id: (bits + 7) // 8 for report_id, bits in sizes.items()}


@dataclass(frozen=True)
class Keyboard:
    path: bytes
    name: str
    supported: bool
    detail: str

    @property
    def path_text(self) -> str:
        return self.path.decode(errors="replace")


def discover() -> list[Keyboard]:
    devices: list[Keyboard] = []
    seen: set[bytes] = set()
    for info in hid.enumerate(VENDOR_ID, PRODUCT_ID):
        path = info["path"]
        if path in seen or info.get("usage_page", 0) < 0xFF00:
            continue
        seen.add(path)
        name = info.get("product_string") or "RK-S98 candidate"
        node = Path(path.decode()).name
        if not node.startswith("hidraw"):
            devices.append(Keyboard(path, name, False, "Only Linux hidraw is supported."))
            continue
        descriptor = Path("/sys/class/hidraw") / node / "device/report_descriptor"
        try:
            sizes = feature_report_sizes(descriptor.read_bytes())
        except (OSError, DeviceError) as error:
            devices.append(Keyboard(path, name, False, f"Cannot verify descriptor: {error}"))
            continue
        supported = sizes.get(REPORT_ID) == REPORT_SIZE - 1
        detail = (
            "Wired configuration interface verified (report 9, 519-byte payload)."
            if supported
            else f"Unsupported feature-report sizes: {sizes}."
        )
        devices.append(Keyboard(path, name, supported, detail))
    return devices


class HidTransport:
    def __init__(self, keyboard: Keyboard):
        if not keyboard.supported:
            raise DeviceError("Refusing to open an unverified configuration interface.")
        self.keyboard = keyboard
        self.handle = None

    def __enter__(self) -> "HidTransport":
        handle = hid.device()
        try:
            handle.open_path(self.keyboard.path)
        except OSError as error:
            handle.close()
            raise DeviceError(
                f"Cannot open {self.keyboard.path_text}: {error}. "
                "Install the supplied udev rule, reconnect the keyboard, then refresh. "
                "Do not run the desktop app as root."
            ) from error
        self.handle = handle
        return self

    def __exit__(self, *_args: object) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None

    def send(self, data: bytes) -> None:
        if self.handle is None:
            raise DeviceError("Device is not open.")
        if len(data) != REPORT_SIZE or data[0] != REPORT_ID:
            raise DeviceError("Refusing a feature report with an unexpected ID or size.")
        try:
            sent = self.handle.send_feature_report(data)
        except OSError as error:
            raise DeviceError(f"USB write failed: {error}") from error
        if sent != len(data):
            raise DeviceError(f"Short USB write: {sent} of {len(data)} bytes sent.")

    def exchange(self, data: bytes) -> bytes:
        self.send(data)
        time.sleep(0.05)
        if self.handle is None:
            raise DeviceError("Device is not open.")
        try:
            return bytes(self.handle.get_feature_report(REPORT_ID, REPORT_SIZE))
        except OSError as error:
            raise DeviceError(f"USB read failed: {error}") from error

    def read_input(self, timeout_ms: int) -> bytes:
        """Return one input report (report ID first), or b"" on timeout."""
        if self.handle is None:
            raise DeviceError("Device is not open.")
        try:
            # hidraw treats 0 as "wait forever", so poll for at least 1 ms instead.
            return bytes(self.handle.read(64, max(1, timeout_ms)))
        except OSError as error:
            raise DeviceError(f"USB input read failed: {error}") from error
