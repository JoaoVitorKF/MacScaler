"""Onde tudo mora. Um único lugar para os caminhos do app."""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "MacScaler"
# Nomes anteriores, do mais antigo para o mais novo. O app trocou de nome três
# vezes; quem estava em qualquer um dos anteriores tem de chegar aqui sem
# rebaixar os 100 MB de motor e modelos nem perder as imagens já salvas.
LEGACY_APP_NAMES = ("PinUpscaler", "BatUpscaler", "BatScaler")

SUPPORT_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
BIN_DIR = SUPPORT_DIR / "bin"
MODELS_DIR = SUPPORT_DIR / "models"
ORIGINALS_DIR = SUPPORT_DIR / "originals"   # matéria-prima p/ reprocessar sem rebater no Pinterest
WORK_DIR = SUPPORT_DIR / "work"             # PNGs intermediários
STATE_FILE = SUPPORT_DIR / "state.json"

BINARY = BIN_DIR / "upscayl-bin"

DEFAULT_OUTPUT_DIR = Path.home() / "Pictures" / APP_NAME

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"


def migrate_legacy() -> None:
    """Renomeia as pastas do nome antigo, se ainda estiverem lá.

    O motor e os modelos pesam ~100 MB e o cache de originais evita rebater no
    Pinterest: trocar o nome do app não é motivo para baixar tudo de novo, nem
    para o usuário perder as imagens que já salvou. Roda antes de criar qualquer
    pasta nova — senão o destino já existe e a migração não acontece mais.
    """
    support_root = Path.home() / "Library" / "Application Support"
    pictures_root = Path.home() / "Pictures"
    for legacy in LEGACY_APP_NAMES:
        for old, new in ((support_root / legacy, SUPPORT_DIR),
                         (pictures_root / legacy, DEFAULT_OUTPUT_DIR)):
            if old.is_dir() and not new.exists():
                try:
                    old.rename(new)
                except OSError:
                    pass   # não é fatal: o app segue e refaz o que faltar


def ensure_dirs() -> None:
    migrate_legacy()
    for d in (SUPPORT_DIR, BIN_DIR, MODELS_DIR, ORIGINALS_DIR, WORK_DIR, DEFAULT_OUTPUT_DIR):
        d.mkdir(parents=True, exist_ok=True)


def reveal_in_finder(path: os.PathLike) -> None:
    import subprocess
    subprocess.run(["open", "-R", str(path)], check=False)
