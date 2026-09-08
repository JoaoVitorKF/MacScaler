"""[E] Finalizar: reduzir com Lanczos e salvar.

Os modelos têm escala fixa (4x aqui). O impulso natural é usar um modelo 2x
quando se quer 2x — o resultado melhor vem de rodar sempre o 4x e depois reduzir
com Lanczos até o tamanho alvo. O downscale de alta qualidade depois da
inferência recupera detalhe e disfarça as texturas sintéticas que o modelo
inventa: um 4x reduzido para 2x é visivelmente superior a um 2x direto.

Por isso a UI pergunta o tamanho de saída, não o multiplicador.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageOps

from .pil_limits import no_bomb_limit

FMT_PNG = "png"
FMT_JPEG = "jpeg"
JPEG_QUALITY = 95


@dataclass
class Finalized:
    path: Path
    width: int
    height: int
    fmt: str
    downscaled_from: Optional[Tuple[int, int]] = None


def rotate(im: Image.Image, rotation: int) -> Image.Image:
    """Gira em múltiplos de 90 graus. `rotation` é horário, como o usuário vê.

    O `expand=True` é obrigatório: sem ele o Pillow mantém a moldura original e
    corta os cantos, que num quadro 3:2 come um terço da imagem.
    """
    r = rotation % 360
    if r == 0:
        return im
    if r not in (90, 180, 270):
        raise ValueError("rotação tem de ser múltipla de 90, veio {}".format(rotation))
    return im.rotate(-r, expand=True)


def rotated_size(w: int, h: int, rotation: int) -> Tuple[int, int]:
    """Dimensões depois de girar — o que a janela precisa antes de girar de fato."""
    return (h, w) if rotation % 180 == 90 else (w, h)


def target_from_long_edge(src_w: int, src_h: int, long_edge: int) -> Tuple[int, int]:
    """Converte 'quero o lado maior com N px' em (w, h), preservando a proporção."""
    if src_w >= src_h:
        w = long_edge
        h = max(1, round(long_edge * src_h / src_w))
    else:
        h = long_edge
        w = max(1, round(long_edge * src_w / src_h))
    return w, h


def unique_path(directory: Path, stem: str, ext: str) -> Path:
    """Não sobrescrever. Se o nome existir, sufixar."""
    directory.mkdir(parents=True, exist_ok=True)
    candidate = directory / "{}.{}".format(stem, ext)
    if not candidate.exists():
        return candidate
    n = 2
    while True:
        candidate = directory / "{}-{}.{}".format(stem, n, ext)
        if not candidate.exists():
            return candidate
        n += 1


def build_stem(pin_id: Optional[str], width: int, height: int) -> str:
    """pin_{id}_{largura}x{altura} — previsível e informativo."""
    base = "pin_{}".format(pin_id) if pin_id else "pin"
    return "{}_{}x{}".format(base, width, height)


def finalize(upscaled: Path, out_dir: Path, pin_id: Optional[str],
             target: Optional[Tuple[int, int]] = None,
             fmt: str = FMT_PNG, rotation: int = 0) -> Finalized:
    # A saída do 4x passa do teto de "bomba" do Pillow a partir de um original
    # de ~3000x4000. Ver pil_limits.
    with no_bomb_limit(), Image.open(upscaled) as im:
        im = ImageOps.exif_transpose(im)   # orientação preservada; o resto dos metadados cai fora
        im = rotate(im, rotation)
        im.load()
        src_size = im.size
        downscaled_from = None

        if target and target != src_size:
            tw, th = target
            if tw < src_size[0] or th < src_size[1]:
                downscaled_from = src_size
            im = im.resize((max(1, tw), max(1, th)), Image.LANCZOS)

        w, h = im.size
        ext = "png" if fmt == FMT_PNG else "jpg"
        dest = unique_path(out_dir, build_stem(pin_id, w, h), ext)

        if fmt == FMT_JPEG:
            if im.mode in ("RGBA", "LA", "P"):
                bg = Image.new("RGB", im.size, (255, 255, 255))
                rgba = im.convert("RGBA")
                bg.paste(rgba, mask=rgba.split()[-1])
                im = bg
            else:
                im = im.convert("RGB")
            im.save(dest, "JPEG", quality=JPEG_QUALITY, subsampling=0,
                    optimize=True, progressive=True)
        else:
            im.save(dest, "PNG", optimize=True)

    return Finalized(dest, w, h, ext, downscaled_from)


def save_without_upscale(original: Path, out_dir: Path, pin_id: Optional[str],
                         target: Optional[Tuple[int, int]] = None,
                         fmt: str = FMT_PNG, rotation: int = 0) -> Finalized:
    """Caminho para a imagem que já está grande: pular o modelo é a resposta certa."""
    return finalize(original, out_dir, pin_id, target, fmt, rotation)
