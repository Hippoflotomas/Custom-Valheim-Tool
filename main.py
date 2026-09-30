"""Valheim Pack Builder - builds BannerShare and ShieldShare pack zips.

Run:  python main.py
      python main.py --self-test [result.txt]   (headless check, used by the release build)
"""
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from packbuilder import __version__
from packbuilder.ui.main_window import APP_NAME, MainWindow


def log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "ValheimPackBuilder" / "error.log"


def install_error_handler() -> None:
    """Show unexpected errors instead of losing them. A windowed exe has no console,
    so without this an error just vanishes (or the app quietly closes)."""
    def hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        where = log_path()
        try:
            where.parent.mkdir(parents=True, exist_ok=True)
            with open(where, "a", encoding="utf-8") as f:
                f.write(f"--- {datetime.now():%Y-%m-%d %H:%M:%S}  {APP_NAME} {__version__}\n{text}\n")
        except OSError:
            where = None
        if sys.stderr is not None:        # None in the windowed exe
            sys.stderr.write(text)
        if QApplication.instance() is not None:
            msg = f"Something went wrong:\n\n{exc_type.__name__}: {exc}"
            if where:
                msg += f"\n\nThe details were saved to:\n{where}\nPlease include that file if you report this."
            QMessageBox.critical(None, APP_NAME, msg)
    sys.excepthook = hook


def main() -> int:
    if "--self-test" in sys.argv:
        from packbuilder import selftest
        i = sys.argv.index("--self-test")
        result = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
        if sys.platform != "win32":
            # CI machines have no display. On Windows keep the real platform plugin, so the
            # test proves the one users will load is bundled and works (no window is shown).
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = QApplication([sys.argv[0]])
        return selftest.main(result)

    install_error_handler()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
