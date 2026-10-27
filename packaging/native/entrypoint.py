import sys

from rks98.__main__ import main

if sys.argv[1:] == ["--packaging-smoke-test"]:
    from PySide6.QtWidgets import QApplication

    from rks98.gui import MainWindow

    app = QApplication([])
    window = MainWindow()
    assert not window.preview.image.isNull()
    assert not window.windowIcon().pixmap(64, 64).isNull()
    print("Bundled Qt widgets, application icon and keyboard image: OK")
    # Do not run the event loop: queued discovery must not touch real hardware.
    raise SystemExit(0)

raise SystemExit(main())
