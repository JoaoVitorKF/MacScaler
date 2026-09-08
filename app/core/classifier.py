"""[C] Foto ou arte digital?

Escolher o modelo errado custa mais que escolher a escala errada: modelo de foto
numa ilustração borra as linhas; modelo de anime numa foto deixa a pele plástica.

A regra de partida do documento (cores < 1500 E área_plana > 0.35, com área plana
definida como gradiente <= 2) foi calibrada contra 36 imagens reais do Pinterest e
produzia 5 falsos "arte" em 30 fotos. O motivo: **foto com fundo desfocado tem
área chapada igual à de uma ilustração**. Bokeh, fundo de estúdio e recorte sobre
branco enchem o quadro de gradiente baixo.

O que separa de verdade é a combinação de dois sinais opostos:

    arte  = grandes chapados de cor  E  bordas duras
    foto  = grandes chapados de cor  E  bordas suaves   (o chapado veio de desfoque)

Por isso "área plana" aqui é gradiente *estritamente zero* — um chapado de cor
tem gradiente 0, uma rampa de desfoque tem 1, 2, 3 — e entra um segundo termo de
borda dura. Nessa amostra: 35/36, nenhum falso "arte".

Os limiares ficam todos aqui em cima, para recalibrar sem caçar dentro do código.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from PIL import Image, ImageChops, ImageOps

THUMB = 256
QUANT_LEVELS = 16          # 16 níveis por canal -> 4096 bins possíveis
HARD_EDGE_MIN = 33         # gradiente >= isto é borda dura

# Limiares calibrados. Ver o cabeçalho para a amostra que os produziu.
FLAT_ART_MIN = 0.15        # chapado + borda dura -> arte
HARD_EDGE_ART_MIN = 0.13
FLAT_POSTER_MIN = 0.50     # quase tudo chapado: cartaz, texto, gráfico
PALETTE_ART_MAX = 120      # paleta minúscula: arte, sem discussão

KIND_PHOTO = "photo"
KIND_ART = "art"


@dataclass
class Classification:
    kind: str                 # KIND_PHOTO | KIND_ART
    unique_colors: int
    flat_ratio: float         # fração de pixels com gradiente exatamente zero
    hard_edge_ratio: float    # fração de pixels com gradiente >= HARD_EDGE_MIN
    mean_saturation: float
    confidence: float         # 0..1
    reason: str = ""

    @property
    def label(self) -> str:
        return "arte digital" if self.kind == KIND_ART else "foto"

    def explain(self) -> str:
        return "{} cores · {:.0f}% chapado · {:.0f}% de borda dura".format(
            self.unique_colors, self.flat_ratio * 100, self.hard_edge_ratio * 100)


def _gradient_hist(gray: Image.Image):
    """Histograma do gradiente local (máximo entre |dx| e |dy|), sem numpy."""
    w, h = gray.size
    if w < 3 or h < 3:
        return None, 0.0
    dx = ImageChops.difference(gray.crop((1, 0, w, h)), gray.crop((0, 0, w - 1, h)))
    dy = ImageChops.difference(gray.crop((0, 1, w, h)), gray.crop((0, 0, w, h - 1)))
    cw, ch = w - 1, h - 1
    grad = ImageChops.lighter(dx.crop((0, 0, cw, ch)), dy.crop((0, 0, cw, ch)))
    return grad.histogram(), float(cw * ch)


def _prepare(source: Union[str, Path, Image.Image]) -> Image.Image:
    if isinstance(source, Image.Image):
        im = source.copy()
    else:
        im = Image.open(source)
        im.load()
    im = ImageOps.exif_transpose(im)
    # Fundo branco sob o alpha: transparência não deve contar como chapado.
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    im.thumbnail((THUMB, THUMB), Image.LANCZOS)
    return im


def classify(source: Union[str, Path, Image.Image]) -> Classification:
    im = _prepare(source)

    step = 256 // QUANT_LEVELS
    counts = Counter((r // step, g // step, b // step) for r, g, b in im.getdata())
    colors = len(counts)

    hist, total = _gradient_hist(im.convert("L"))
    if hist is None:
        flat = edges = 0.0
    else:
        flat = hist[0] / total
        edges = sum(hist[HARD_EDGE_MIN:]) / total

    sat_hist = im.convert("HSV").split()[1].histogram()
    n = sum(sat_hist) or 1
    sat = sum(i * c for i, c in enumerate(sat_hist)) / n

    if colors <= PALETTE_ART_MAX:
        kind, reason = KIND_ART, "paleta de {} cores".format(colors)
        margin = (PALETTE_ART_MAX - colors) / float(PALETTE_ART_MAX)
    elif flat >= FLAT_POSTER_MIN:
        kind, reason = KIND_ART, "{:.0f}% do quadro é cor chapada".format(flat * 100)
        margin = min(1.0, (flat - FLAT_POSTER_MIN) / (1.0 - FLAT_POSTER_MIN))
    elif flat >= FLAT_ART_MIN and edges >= HARD_EDGE_ART_MIN:
        kind, reason = KIND_ART, "chapados de cor com bordas duras"
        margin = min(1.0, min((flat - FLAT_ART_MIN) / FLAT_ART_MIN,
                              (edges - HARD_EDGE_ART_MIN) / HARD_EDGE_ART_MIN))
    else:
        kind = KIND_PHOTO
        if flat >= FLAT_ART_MIN:
            reason = "área lisa, mas de desfoque e não de cor chapada"
        else:
            reason = "textura e ruído em todo o quadro"
        margin = min(1.0, max((FLAT_ART_MIN - flat) / FLAT_ART_MIN,
                              (HARD_EDGE_ART_MIN - edges) / HARD_EDGE_ART_MIN, 0.0))

    return Classification(
        kind=kind, unique_colors=colors, flat_ratio=flat, hard_edge_ratio=edges,
        mean_saturation=sat, confidence=max(0.0, min(1.0, margin)), reason=reason,
    )
