import argparse
import json
import logging

from .device import discover


def main() -> int:
    parser = argparse.ArgumentParser(description="RK-S98 Studio for Linux")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--list", action="store_true", help="List candidate interfaces without USB writes"
    )
    mode.add_argument(
        "--volume-service",
        action="store_true",
        help="Run headless volume/time sync (writes to the keyboard; intended for the user service)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if args.volume_service:
        from .background import run_volume_service

        try:
            return run_volume_service()
        except (OSError, RuntimeError) as error:
            logging.error("Cannot start volume service: %s", error)
            return 1
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

    from PySide6.QtWidgets import QApplication, QMessageBox

    from .background import ProcessLease
    from .gui import MainWindow, confirm_hardware_risk

    app = QApplication(["rks98"])
    app.setApplicationName("rks98")
    app.setDesktopFileName("rks98")
    if not confirm_hardware_risk():
        return 0
    try:
        with ProcessLease(timeout=5):
            window = MainWindow()
            app.setWindowIcon(window.windowIcon())
            window.show()
            return app.exec()
    except (OSError, RuntimeError) as error:
        logging.error("Cannot start RK-S98 Studio: %s", error)
        QMessageBox.critical(None, "Cannot start RK-S98 Studio", str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
