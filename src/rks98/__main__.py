import argparse
import json
import logging

from .device import discover


def main() -> int:
    parser = argparse.ArgumentParser(description="RK-S98 Studio for Linux")
    parser.add_argument(
        "--list", action="store_true", help="List candidate interfaces without USB writes"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if args.list:
        try:
            devices = discover()
        except OSError as error:
            logging.error("Device enumeration failed: %s", error)
            return 1
        print(
            json.dumps(
                [
                    {
                        "path": device.path_text,
                        "name": device.name,
                        "supported": device.supported,
                        "detail": device.detail,
                    }
                    for device in devices
                ],
                indent=2,
            )
        )
        return 0

    from PySide6.QtWidgets import QApplication

    from .gui import MainWindow, confirm_hardware_risk

    app = QApplication(["rks98"])
    app.setApplicationName("rks98")
    app.setDesktopFileName("rks98")
    if not confirm_hardware_risk():
        return 0
    window = MainWindow()
    app.setWindowIcon(window.windowIcon())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
