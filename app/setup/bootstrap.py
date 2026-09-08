"""Setup. Os passos 'baixar', 'tirar a quarentena' e 'testar de verdade' são os
três que costumam ser esquecidos e os três que produzem falha silenciosa.
"""
from __future__ import annotations

import io
import os
import platform
import shutil
import stat
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

import requests

from .. import paths
from ..core import models as M
from ..core.http import TIMEOUT, new_session
from ..errors import Cancelled, SetupError

RELEASES_API = "https://api.github.com/repos/upscayl/upscayl-ncnn/releases/latest"
# Se a API do GitHub estiver fora do ar ou com rate limit, cai para esta release.
PINNED_TAG = "20251207-174704"
PINNED_URL = (
    "https://github.com/upscayl/upscayl-ncnn/releases/download/"
    "{t}/upscayl-bin-{t}-macos.zip".format(t=PINNED_TAG)
)

ProgressFn = Callable[[float, str], None]
CancelFn = Callable[[], bool]


@dataclass
class SetupStatus:
    binary_ok: bool
    models_missing: List[str]
    smoke_ok: bool

    @property
    def complete(self) -> bool:
        return self.binary_ok and not self.models_missing and self.smoke_ok


def check_arch() -> None:
    if platform.machine() != "arm64":
        raise SetupError(
            "Este app é só para Mac com Apple Silicon.",
            detail="Arquitetura detectada: {}".format(platform.machine()),
            hint="O binário de inferência roda nativo em arm64; em Intel não há suporte na v1.",
        )


def status() -> SetupStatus:
    missing = []
    for m in M.ESSENTIAL:
        for f in m.files:
            if not (paths.MODELS_DIR / f).exists():
                missing.append(f)
    return SetupStatus(
        binary_ok=paths.BINARY.exists() and os.access(paths.BINARY, os.X_OK),
        models_missing=missing,
        smoke_ok=(paths.SUPPORT_DIR / ".smoke-ok").exists(),
    )


def _latest_macos_zip(session) -> str:
    try:
        r = session.get(RELEASES_API, timeout=TIMEOUT)
        if r.status_code == 200:
            for a in r.json().get("assets", []):
                if a["name"].endswith("-macos.zip"):
                    return a["browser_download_url"]
    except (requests.RequestException, ValueError):
        pass
    return PINNED_URL


def _download(session, url: str, on_progress: Optional[ProgressFn],
              label: str, base: float, span: float, cancel: Optional[CancelFn]) -> bytes:
    try:
        r = session.get(url, timeout=TIMEOUT, stream=True)
        r.raise_for_status()
    except requests.RequestException as e:
        raise SetupError("Não consegui baixar {}.".format(label), detail=str(e),
                         hint="Verifique a conexão com a internet.")
    total = r.headers.get("Content-Length")
    total = int(total) if total and total.isdigit() else 0
    buf = io.BytesIO()
    got = 0
    for chunk in r.iter_content(64 * 1024):
        if cancel and cancel():
            raise Cancelled()
        buf.write(chunk)
        got += len(chunk)
        if on_progress:
            # O raw.githubusercontent serve com gzip: o Content-Length é o tamanho
            # comprimido, mas iter_content devolve bytes descomprimidos. Sem o
            # clamp a barra passa de 100%.
            frac = min(1.0, got / total) if total else 0.0
            on_progress(base + span * frac, "{} — {:.1f} MB".format(label, got / 1048576))
    return buf.getvalue()


def clear_quarantine(target: Path) -> None:
    """O binário não é assinado nem notarizado. Baixado programaticamente ele
    recebe o atributo de quarentena e o macOS o mata na primeira execução com uma
    caixa de diálogo — não com um erro no stderr. Fazer isto aqui, no setup."""
    subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(target)],
                   check=False, capture_output=True)


def install_binary(session, on_progress: Optional[ProgressFn] = None,
                   cancel: Optional[CancelFn] = None) -> Path:
    paths.ensure_dirs()
    url = _latest_macos_zip(session)
    data = _download(session, url, on_progress, "Motor de upscale", 0.0, 0.45, cancel)

    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SetupError("O arquivo do motor veio corrompido.", hint="Tente o setup de novo.")

    member = next((n for n in zf.namelist() if n.rsplit("/", 1)[-1] == "upscayl-bin"), None)
    if member is None:
        raise SetupError("Não achei o executável dentro do zip.",
                         detail=", ".join(zf.namelist()[:10]))

    with zf.open(member) as src, open(paths.BINARY, "wb") as dst:
        shutil.copyfileobj(src, dst)

    clear_quarantine(paths.BIN_DIR)
    paths.BINARY.chmod(paths.BINARY.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return paths.BINARY


def install_models(session, wanted: Optional[List[M.Model]] = None,
                   on_progress: Optional[ProgressFn] = None,
                   cancel: Optional[CancelFn] = None) -> None:
    paths.ensure_dirs()
    wanted = wanted or M.ESSENTIAL
    jobs = [(m, f) for m in wanted for f in m.files
            if not (paths.MODELS_DIR / f).exists()]
    if not jobs:
        return
    span = 0.45 / len(jobs)
    for i, (model, fname) in enumerate(jobs):
        base = 0.45 + i * span
        data = _download(session, model.url_for(fname), on_progress,
                         "Modelo {}".format(model.label.split("—")[0].strip()),
                         base, span, cancel)
        (paths.MODELS_DIR / fname).write_bytes(data)


def smoke_test(on_progress: Optional[ProgressFn] = None) -> None:
    """Um upscale de verdade numa imagem minúscula. É a única forma de saber que
    o Vulkan inicializou e que a GPU respondeu. Falhar aqui, com mensagem útil, é
    infinitamente melhor do que falhar na primeira imagem do usuário."""
    from ..core.upscaler import upscale
    from ..errors import UpscaleError

    if on_progress:
        on_progress(0.92, "Testando a GPU...")

    src = paths.ASSETS_DIR / "smoke.png"
    if not src.exists():
        raise SetupError("Falta a imagem de teste do app.", detail=str(src))
    out = paths.WORK_DIR / "smoke-out.png"
    out.unlink(missing_ok=True)

    try:
        upscale(src, out, M.get(M.SMOKE_TEST_MODEL))
    except UpscaleError as e:
        raise SetupError(
            "O motor de upscale não conseguiu usar a GPU.",
            detail=e.detail or e.message,
            hint="Reinicie o Mac e rode o setup de novo. Se persistir, sua GPU pode não "
                 "expor o Vulkan via MoltenVK.",
        )

    from PIL import Image
    try:
        with Image.open(out) as im:
            w, h = im.size
    except Exception as e:
        raise SetupError("O teste rodou mas não gerou uma imagem válida.", detail=str(e))
    if (w, h) != (128, 128):
        raise SetupError(
            "O teste devolveu {}x{} em vez de 128x128.".format(w, h),
            hint="A escala do modelo e a do binário não estão coerentes.",
        )

    (paths.SUPPORT_DIR / ".smoke-ok").write_text("ok")
    if on_progress:
        on_progress(1.0, "GPU respondeu — tudo pronto.")


def run(on_progress: Optional[ProgressFn] = None,
        cancel: Optional[CancelFn] = None) -> None:
    """Setup completo e idempotente."""
    check_arch()
    paths.ensure_dirs()
    session = new_session()
    st = status()

    if not st.binary_ok:
        install_binary(session, on_progress, cancel)
    elif on_progress:
        on_progress(0.45, "Motor já instalado")

    install_models(session, None, on_progress, cancel)

    # A quarentena pode ter voltado se o usuário mexeu na pasta.
    clear_quarantine(paths.BIN_DIR)
    smoke_test(on_progress)
