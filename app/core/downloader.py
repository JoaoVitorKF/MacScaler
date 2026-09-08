"""[B] Download, com teto e com verificação de que o "maior" é mesmo maior."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

from .. import paths
from ..errors import Cancelled, NetworkError, ResolveError
from .http import TIMEOUT, new_session
from .resolver import Resolved

# Acima disto o upscale deixa de fazer sentido: a imagem já está em alta resolução.
SOFT_MAX_SIDE = 4000
# Acima disto o upscale 4x geraria um PNG absurdo; o padrão passa a ser pular.
MAX_BYTES = 60 * 1024 * 1024
MAX_SIDE = 8000
# Teto duro só para não baixar algo sem fim se o Content-Length mentir.
HARD_MAX_BYTES = 200 * 1024 * 1024

ProgressFn = Callable[[float, str], None]
CancelFn = Callable[[], bool]


@dataclass
class Downloaded:
    path: Path
    url: str
    width: int
    height: int
    fmt: str
    size_bytes: int
    has_alpha: bool
    oversized: bool          # passou de MAX_BYTES/MAX_SIDE -> padrão é pular o upscale
    already_large: bool      # passou de SOFT_MAX_SIDE -> avisar
    note: str = ""

    @property
    def megapixels(self) -> float:
        return self.width * self.height / 1_000_000.0


def _cache_path(url: str) -> Path:
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    m = re.search(r"(\.[A-Za-z0-9]+)$", url)
    ext = (m.group(1) if m else ".img").lower()
    return paths.ORIGINALS_DIR / "{}{}".format(h, ext)


def _stream_to_file(session, url: str, dest: Path,
                    on_progress: Optional[ProgressFn], cancel: Optional[CancelFn]) -> int:
    try:
        r = session.get(url, timeout=TIMEOUT, stream=True, allow_redirects=True)
    except requests.RequestException as e:
        raise NetworkError("Falha de rede ao baixar a imagem.", detail=str(e))
    if r.status_code == 403:
        raise NetworkError(
            "O CDN do Pinterest recusou o download (403).",
            hint="Costuma ser temporário; tente de novo em alguns segundos.",
        )
    if r.status_code != 200:
        raise NetworkError("O CDN respondeu {} ao baixar.".format(r.status_code))

    total = r.headers.get("Content-Length")
    total = int(total) if total and total.isdigit() else 0
    got = 0
    tmp = dest.with_suffix(dest.suffix + ".part")
    with open(tmp, "wb") as fh:
        for chunk in r.iter_content(chunk_size=64 * 1024):
            if cancel and cancel():
                fh.close()
                tmp.unlink(missing_ok=True)
                raise Cancelled()
            if not chunk:
                continue
            fh.write(chunk)
            got += len(chunk)
            if got > HARD_MAX_BYTES:
                fh.close()
                tmp.unlink(missing_ok=True)
                raise NetworkError(
                    "A imagem passou de {} MB e o download foi interrompido.".format(
                        HARD_MAX_BYTES // (1024 * 1024)))
            if on_progress and total:
                on_progress(min(1.0, got / total),
                            "Baixando... {:.1f} MB".format(got / 1048576))
    tmp.replace(dest)
    return got


def _inspect(path: Path):
    try:
        with Image.open(path) as im:
            fmt = im.format or "?"            # exif_transpose devolve imagem nova, sem .format
            alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
            im = ImageOps.exif_transpose(im)  # dimensões já com a orientação EXIF aplicada
            return im.size, fmt, alpha
    except UnidentifiedImageError:
        raise ResolveError(
            "O que veio do Pinterest não é uma imagem que eu consiga abrir.",
            hint="O pin pode ser um vídeo ou um GIF animado.",
        )


def download(resolved: Resolved, session: Optional[requests.Session] = None,
             on_progress: Optional[ProgressFn] = None,
             cancel: Optional[CancelFn] = None) -> Downloaded:
    session = session or new_session()
    paths.ensure_dirs()

    dest = _cache_path(resolved.url)
    if dest.exists() and dest.stat().st_size > 0:
        size_bytes = dest.stat().st_size
        if on_progress:
            on_progress(1.0, "Original já em cache")
    else:
        size_bytes = _stream_to_file(session, resolved.url, dest, on_progress, cancel)

    (w, h), fmt, alpha = _inspect(dest)
    note = ""

    # "Validar que o maior é realmente maior": o CDN às vezes devolve 200 em
    # /originals/ servindo exatamente a mesma imagem reduzida.
    ow, oh = resolved.og_width, resolved.og_height
    if ow and oh and resolved.og_url and resolved.og_url != resolved.url:
        if ow * oh > w * h:
            alt = _cache_path(resolved.og_url)
            try:
                if not (alt.exists() and alt.stat().st_size > 0):
                    _stream_to_file(session, resolved.og_url, alt, None, cancel)
                (aw, ah), afmt, aalpha = _inspect(alt)
                if aw * ah > w * h:
                    dest, w, h, fmt, alpha = alt, aw, ah, afmt, aalpha
                    size_bytes = dest.stat().st_size
                    note = "O /originals/ veio menor que a og:image; usei a og:image."
            except (NetworkError, ResolveError, Cancelled):
                pass  # o que já temos serve

    if resolved.size_segment == "originals" and ow and oh and w * h < ow * oh:
        note = note or "O /originals/ tem a mesma resolução do thumbnail — não há versão maior."

    oversized = size_bytes > MAX_BYTES or max(w, h) > MAX_SIDE
    already_large = max(w, h) > SOFT_MAX_SIDE

    return Downloaded(
        path=dest, url=resolved.url, width=w, height=h, fmt=fmt,
        size_bytes=size_bytes, has_alpha=alpha,
        oversized=oversized, already_large=already_large, note=note,
    )
