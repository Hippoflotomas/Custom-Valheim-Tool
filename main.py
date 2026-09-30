"""Valheim Pack Builder - builds BannerShare and ShieldShare pack zips.

Run:  python main.py
"""
import sys

from PySide6.QtWidgets import QApplication

from packbuilder.ui.main_window import APP_NAME, MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
