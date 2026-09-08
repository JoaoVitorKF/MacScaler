"""Orquestra [A]→[E]. Sem nada de Qt aqui — o core roda em teste e em CLI."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Tuple

from .. import paths
from ..errors import Cancelled
from . import models as M
from .classifier import Classification, classify
from .downloader import Downloaded, download
from .finalize import (FMT_PNG, Finalized, finalize, rotate, rotated_size,
                       target_from_long_edge)
from .http import new_session
from .resolver import Resolved, resolve
from .upscaler import UpscaleResult, upscale

# (etapa, fração 0..1 dentro da etapa, mensagem)
ProgressFn = Callable[[str, float, str], None]
CancelFn = Callable[[], bool]

STAGE_RESOLVE = "resolve"
STAGE_DOWNLOAD = "download"
STAGE_CLASSIFY = "classify"
STAGE_UPSCALE = "upscale"
STAGE_FINALIZE = "finalize"


@dataclass
class Prepared:
    """Resultado de [A]+[B]+[C]. Guardado para reprocessar sem rebater no Pinterest."""
    resolved: Resolved
    downloaded: Downloaded
    classification: Classification

    @property
    def suggested_model(self) -> str:
        return M.DEFAULT_FOR[self.classification.kind]


@dataclass
class Outcome:
    final: Finalized
    prepared: Prepared
    model_used: Optional[str]
    upscaled: Optional[UpscaleResult] = None
    skipped_upscale: bool = False
    messages: list = field(default_factory=list)


def prepare(link: str, on_progress: Optional[ProgressFn] = None,
            cancel: Optional[CancelFn] = None) -> Prepared:
    """[A] + [B] + [C]: tudo que dá para fazer antes de o usuário escolher."""
    say = on_progress or (lambda s, f, m: None)
    session = new_session()

    say(STAGE_RESOLVE, 0.0, "Resolvendo o link...")
    res = resolve(link, session, progress=lambda m: say(STAGE_RESOLVE, 0.5, m))
    say(STAGE_RESOLVE, 1.0, "Original encontrado ({})".format(res.size_segment))

    dl = download(res, session,
                  on_progress=lambda f, m: say(STAGE_DOWNLOAD, f, m),
                  cancel=cancel)
    say(STAGE_DOWNLOAD, 1.0, "{}x{} baixados".format(dl.width, dl.height))

    say(STAGE_CLASSIFY, 0.0, "Analisando a imagem...")
    cls = classify(dl.path)
    say(STAGE_CLASSIFY, 1.0, "Parece {} — {}".format(cls.label, cls.reason))

    return Prepared(res, dl, cls)


def _work_png(prepared: Prepared, rotation: int = 0) -> Path:
    """Sempre converter a entrada para PNG antes de passar ao binário: alimentá-lo
    com JPEG faz o modelo amplificar os artefatos de compressão junto com o conteúdo.

    A rotação é aplicada **aqui**, antes do modelo, e não no fim. Girar depois
    seria girar uma imagem 16x maior, e — mais importante — o modelo veria a cena
    deitada. Assim tudo daqui para frente, inclusive o tamanho alvo, já fala da
    imagem em pé.
    """
    from PIL import Image, ImageOps
    paths.ensure_dirs()
    dest = paths.WORK_DIR / "input.png"
    with Image.open(prepared.downloaded.path) as im:
        im = ImageOps.exif_transpose(im)
        im = rotate(im, rotation)
        if im.mode not in ("RGB", "RGBA", "L"):
            im = im.convert("RGBA" if "A" in im.mode else "RGB")
        im.save(dest, "PNG")
    return dest


def process(prepared: Prepared, out_dir: Optional[Path] = None,
            model_name: Optional[str] = None,
            long_edge: Optional[int] = None,
            fmt: str = FMT_PNG,
            skip_upscale: bool = False,
            rotation: int = 0,
            tile: Optional[int] = None,
            jobs: Optional[str] = None,
            on_progress: Optional[ProgressFn] = None,
            cancel: Optional[CancelFn] = None) -> Outcome:
    """[D] + [E]. Reprocessável: recebe o Prepared e não toca na rede."""
    say = on_progress or (lambda s, f, m: None)
    out_dir = out_dir or paths.DEFAULT_OUTPUT_DIR
    dl = prepared.downloaded
    messages = []
    if dl.note:
        messages.append(dl.note)

    if skip_upscale or (dl.oversized and long_edge is None):
        if dl.oversized and not skip_upscale:
            messages.append(
                "A imagem já tem {}x{} ({:.1f} MP) — o upscale foi pulado.".format(
                    dl.width, dl.height, dl.megapixels))
        say(STAGE_FINALIZE, 0.5, "Salvando sem upscale...")
        # O alvo tem de ser medido na imagem já girada, senão o lado maior é o
        # outro e a proporção sai trocada.
        rw, rh = rotated_size(dl.width, dl.height, rotation)
        target = target_from_long_edge(rw, rh, long_edge) if long_edge else None
        final = finalize(dl.path, out_dir, prepared.resolved.pin_id, target, fmt, rotation)
        say(STAGE_FINALIZE, 1.0, "Pronto")
        return Outcome(final, prepared, None, None, True, messages)

    model = M.get(model_name or prepared.suggested_model)
    say(STAGE_UPSCALE, 0.0, "Preparando a imagem...")
    src = _work_png(prepared, rotation)
    dst = paths.WORK_DIR / "upscaled.png"

    up = upscale(src, dst, model,
                 on_progress=lambda f, m: say(STAGE_UPSCALE, f, m),
                 cancel=cancel, tile=tile, jobs=jobs)

    say(STAGE_FINALIZE, 0.2, "Reduzindo com Lanczos...")
    if long_edge:
        target = target_from_long_edge(up.width, up.height, long_edge)
        if long_edge > max(up.width, up.height):
            messages.append(
                "O alvo de {}px passa do que o modelo entregou ({}px); "
                "salvei no tamanho do modelo.".format(long_edge, max(up.width, up.height)))
            target = None
    else:
        target = None

    final = finalize(dst, out_dir, prepared.resolved.pin_id, target, fmt)
    say(STAGE_FINALIZE, 1.0, "Pronto")

    return Outcome(final, prepared, model.name, up, False, messages)


def run(link: str, out_dir: Optional[Path] = None, model_name: Optional[str] = None,
        long_edge: Optional[int] = None, fmt: str = FMT_PNG, rotation: int = 0,
        tile: Optional[int] = None, jobs: Optional[str] = None,
        on_progress: Optional[ProgressFn] = None,
        cancel: Optional[CancelFn] = None) -> Outcome:
    prepared = prepare(link, on_progress, cancel)
    return process(prepared, out_dir, model_name, long_edge, fmt, rotation=rotation,
                   tile=tile, jobs=jobs, on_progress=on_progress, cancel=cancel)
