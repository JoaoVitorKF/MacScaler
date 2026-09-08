"""Erros com mensagem legível — nunca deixar stack trace chegar na janela."""
from __future__ import annotations


class MacScalerError(Exception):
    """Erro esperado, com texto pronto para ser mostrado ao usuário."""

    def __init__(self, message: str, detail: str = "", hint: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.hint = hint

    def __str__(self) -> str:
        parts = [self.message]
        if self.hint:
            parts.append(self.hint)
        return "\n".join(parts)


class InvalidLinkError(MacScalerError):
    pass


class NetworkError(MacScalerError):
    pass


class ResolveError(MacScalerError):
    pass


class SetupError(MacScalerError):
    pass


class UpscaleError(MacScalerError):
    pass


class Cancelled(Exception):
    """Usuário cancelou. Não é erro, não vira caixa de diálogo."""
