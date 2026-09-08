"""Registro de modelos.

A escala vem do nome do arquivo — é a convenção do Upscayl e o binário aceita
`-z` (escala nativa do modelo) e `-s` (escala de saída) separadamente. Passar um
`-z` que não bate com o modelo produz lixo silenciosamente, então a coerência é
validada aqui, antes de qualquer chamada.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from ..errors import UpscaleError
from .classifier import KIND_ART, KIND_PHOTO

UPSCAYL_RAW = "https://raw.githubusercontent.com/upscayl/upscayl/main"

# Detecta x2/2x/x4/4x no nome, a convenção do Upscayl.
_SCALE_RE = re.compile(r"(?:^|[^a-z0-9])(?:x([234])|([234])x)(?:$|[^a-z0-9])", re.I)


def scale_from_name(name: str) -> Optional[int]:
    m = _SCALE_RE.search(name)
    if not m:
        return None
    return int(m.group(1) or m.group(2))


@dataclass(frozen=True)
class Model:
    name: str            # nome do arquivo, sem extensão — é o que vai no -n
    label: str
    kind: str            # KIND_PHOTO | KIND_ART
    scale: int
    source: str          # subpasta no repo do Upscayl
    note: str = ""
    essential: bool = True   # baixado no setup; os demais são opcionais

    @property
    def files(self) -> List[str]:
        return ["{}.param".format(self.name), "{}.bin".format(self.name)]

    def url_for(self, filename: str) -> str:
        return "{}/{}/{}".format(UPSCAYL_RAW, self.source, filename)

    def validate(self) -> None:
        """A escala declarada tem de bater com a que o nome anuncia."""
        from_name = scale_from_name(self.name)
        if from_name is not None and from_name != self.scale:
            raise UpscaleError(
                "Modelo {} está declarado como {}x mas o nome diz {}x.".format(
                    self.name, self.scale, from_name),
                hint="Corrija o registro em app/core/models.py antes de usar.",
            )


REGISTRY: Dict[str, Model] = {}


def _add(m: Model) -> Model:
    m.validate()
    REGISTRY[m.name] = m
    return m


# --- foto -------------------------------------------------------------------
ULTRASHARP = _add(Model(
    "ultrasharp-4x", "UltraSharp — foto (padrão)", KIND_PHOTO, 4,
    "resources/models", "Nítido e neutro. A escolha segura para fotografia."))

REMACRI = _add(Model(
    "remacri-4x", "Remacri — foto suave, pele", KIND_PHOTO, 4,
    "resources/models", "Menos agressivo em pele e gradientes."))

# --- arte -------------------------------------------------------------------
DIGITAL_ART = _add(Model(
    "digital-art-4x", "Digital Art — ilustração", KIND_ART, 4,
    "resources/models", "Preserva linha e chapado de cor."))

ANIMEVIDEO = _add(Model(
    "realesr-animevideov3-x4", "Anime — linha limpa", KIND_ART, 4,
    "models", "Real-ESRGAN anime. Leve e muito rápido.", essential=False))

# --- utilitário -------------------------------------------------------------
LITE = _add(Model(
    "upscayl-lite-4x", "Upscayl Lite — rápido", KIND_PHOTO, 4,
    "resources/models", "Modelo pequeno, usado também no teste de setup."))

# Padrão por classe.
DEFAULT_FOR = {KIND_PHOTO: ULTRASHARP.name, KIND_ART: DIGITAL_ART.name}

# O menor de todos — é com ele que o setup prova que a GPU respondeu.
SMOKE_TEST_MODEL = LITE.name

ESSENTIAL = [m for m in REGISTRY.values() if m.essential]


def get(name: str) -> Model:
    try:
        return REGISTRY[name]
    except KeyError:
        raise UpscaleError("Modelo desconhecido: {}".format(name))


def for_kind(kind: str) -> List[Model]:
    return [m for m in REGISTRY.values() if m.kind == kind]
