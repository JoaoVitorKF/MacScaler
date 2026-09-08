"""Trabalho pesado fora da thread da UI.

O upscale é um processo síncrono e longo. Rodar na thread da janela congela a
interface e o macOS mostra o beachball — daí toda etapa de rede ou de inferência
passar por aqui.
"""
from __future__ import annotations

import threading
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal

from ..errors import Cancelled, MacScalerError


class Worker(QThread):
    """Roda `fn(progress, cancel)` numa thread e devolve o resultado por signal."""

    progressed = Signal(str, float, str)      # etapa, 0..1, mensagem
    succeeded = Signal(object)
    failed = Signal(str, str, str)            # mensagem, detalhe, dica
    cancelled = Signal()

    def __init__(self, fn: Callable[..., Any], parent: QObject = None):
        super().__init__(parent)
        self._fn = fn
        self._stop = threading.Event()

    def cancel(self) -> None:
        self._stop.set()

    def is_cancelled(self) -> bool:
        return self._stop.is_set()

    def run(self) -> None:
        def progress(stage: str, frac: float, msg: str) -> None:
            self.progressed.emit(stage, float(frac), msg)

        try:
            result = self._fn(progress, self.is_cancelled)
        except Cancelled:
            self.cancelled.emit()
        except MacScalerError as e:
            self.failed.emit(e.message, e.detail, e.hint)
        except Exception as e:                      # nada de stack trace na janela
            self.failed.emit(
                "Algo inesperado quebrou.",
                "{}: {}\n{}".format(type(e).__name__, e, traceback.format_exc()[-1200:]),
                "Se acontecer de novo, o detalhe acima ajuda a diagnosticar.",
            )
        else:
            if self.is_cancelled():
                self.cancelled.emit()
            else:
                self.succeeded.emit(result)
