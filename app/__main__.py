"""Ponto de entrada da janela."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from . import paths
from .errors import SetupError
from .setup import bootstrap
from .ui.main_window import MainWindow, SetupDialog
from .ui.style import QSS


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("MacScaler")
    app.setApplicationDisplayName("MacScaler")
    app.setStyleSheet(QSS)

    try:
        bootstrap.check_arch()
    except SetupError as e:
        QMessageBox.critical(None, "Não dá para rodar aqui", str(e))
        return 1

    paths.ensure_dirs()
    if not bootstrap.status().complete:
        dlg = SetupDialog()
        dlg.exec()
        if not dlg.ok:
            return 1

    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
