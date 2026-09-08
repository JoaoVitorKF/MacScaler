"""[D] Upscale via upscayl-ncnn.

É um processo por imagem, síncrono e potencialmente longo — tem que rodar em
subprocess fora da thread da UI, senão a janela congela e aparece o beachball.

O progresso sai no stderr. Observado no M4, o binário imprime as porcentagens
usando o separador decimal do locale (`0,00%` em pt-BR) e a última linha vem sem
o `%`. O parser aceita as duas formas.
"""
from __future__ import annotations

import re
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from .. import paths
from ..errors import Cancelled, UpscaleError
from .models import Model
from .pil_limits import no_bomb_limit

ProgressFn = Callable[[float, str], None]
CancelFn = Callable[[], bool]

# Uma linha que é só um número, com vírgula ou ponto, com ou sem %.
_PCT_RE = re.compile(r"^(\d{1,3})[.,](\d+)\s*%?$")
_GPU_RE = re.compile(r"^\[\d+\s+([^\]]+)\]")

# Se a memória da GPU estourar, descer o tile antes de desistir.
TILE_LADDER: List[int] = [0, 256, 128, 64]

# Degraus de recuo quando o chamador pediu um tile explícito (ver tuning.py).
_FALLBACK_TILES: List[int] = [256, 128, 64]


def ladder_for(preferred: Optional[int]) -> List[int]:
    """A escada de tiles a tentar. Sem preferência, a de sempre (auto primeiro).

    Com um tile pedido, ele vem na frente e o recuo é só para baixo — voltar ao
    auto depois de um tile explícito falhar por memória não ajuda: o auto do ncnn
    costuma pedir mais, não menos.
    """
    if not preferred:
        return list(TILE_LADDER)
    return [preferred] + [t for t in _FALLBACK_TILES if t < preferred]


_OOM_MARKERS = ("vkallocatememory failed", "out of memory", "failed to allocate",
                "vkcreateimage failed", "out of device memory")
_NOGPU_MARKERS = ("no vulkan device", "failed to create instance", "vkcreateinstance failed",
                  "no gpu device")

# Amostra usada para detectar a saída preta. Pequena de propósito: é só para
# saber se sobrou alguma coisa na imagem, não para medir nada.
_BLANK_SAMPLE = 160


def _is_blank(path: Path) -> bool:
    """Imagem inteiramente preta.

    Com tile grande demais o binário não reclama: sai com código 0, no tempo de
    uma cópia, e grava um PNG do tamanho certo e todo preto. Medido neste Mac —
    1800x3200 com `-t 1024` produziu 7200x12800 pretos em 4,3 s. Nenhum dos
    marcadores de erro aparece no stderr, então a única forma de pegar é olhar
    para o que saiu.
    """
    from PIL import Image
    try:
        with no_bomb_limit():
            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((_BLANK_SAMPLE, _BLANK_SAMPLE))
                return im.getextrema() == ((0, 0), (0, 0), (0, 0))
    except Exception:
        return False   # se não dá para olhar, não é aqui que se decide


@dataclass
class UpscaleResult:
    path: Path
    width: int
    height: int
    device: str
    tile: int
    used_cpu: bool
    jobs: str = ""


def _run_once(binary: Path, src: Path, dst: Path, model: Model, tile: int,
              gpu: Optional[int], on_progress: Optional[ProgressFn],
              cancel: Optional[CancelFn], jobs: Optional[str] = None):
    cmd = [
        str(binary),
        "-i", str(src),
        "-o", str(dst),
        "-n", model.name,
        "-m", str(paths.MODELS_DIR),
        "-z", str(model.scale),   # escala nativa do modelo
        "-s", str(model.scale),   # sem redimensionar aqui: o Lanczos da etapa [E] faz melhor
        "-f", "png",              # PNG preserva alpha e não reintroduz artefato de JPEG
        "-t", str(tile),
    ]
    if gpu is not None:
        cmd += ["-g", str(gpu)]
    if jobs:
        cmd += ["-j", jobs]      # load:proc:save; ver tuning.py

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except OSError as e:
        raise UpscaleError("Não consegui executar o motor de upscale.", detail=str(e),
                           hint="Rode o setup de novo pelo menu do app.")

    device = ""
    tail: List[str] = []
    buf = b""
    try:
        while True:
            if cancel and cancel():
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise Cancelled()
            chunk = proc.stderr.read(1)
            if not chunk:
                break
            if chunk in (b"\r", b"\n"):
                line = buf.decode("utf-8", "replace").strip()
                buf = b""
                if not line:
                    continue
                tail.append(line)
                del tail[:-40]
                m = _PCT_RE.match(line)
                if m and on_progress:
                    pct = float("{}.{}".format(m.group(1), m.group(2)))
                    on_progress(min(pct / 100.0, 1.0), "Ampliando com IA... {:.0f}%".format(pct))
                    continue
                g = _GPU_RE.match(line)
                if g and not device:
                    device = g.group(1).strip()
            else:
                buf += chunk
                if len(buf) > 4096:
                    buf = buf[-1024:]
    finally:
        try:
            proc.stderr.close()
        except Exception:
            pass
    code = proc.wait()
    return code, device, "\n".join(tail)


def upscale(src: Path, dst: Path, model: Model,
            on_progress: Optional[ProgressFn] = None,
            cancel: Optional[CancelFn] = None,
            binary: Optional[Path] = None,
            tile: Optional[int] = None,
            jobs: Optional[str] = None) -> UpscaleResult:
    binary = binary or paths.BINARY
    if not binary.exists():
        raise UpscaleError("O motor de upscale não está instalado.",
                           hint="Rode o setup pelo menu do app.")
    model.validate()

    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    attempts = [(t, None) for t in ladder_for(tile)] + [(0, -1)]  # -g -1 = CPU, último recurso
    last = ""
    for attempt_tile, gpu in attempts:
        dst.unlink(missing_ok=True)
        code, device, err = _run_once(binary, src, dst, model, attempt_tile, gpu,
                                      on_progress, cancel, jobs)
        low = err.lower()

        if code == 0 and dst.exists() and dst.stat().st_size > 0:
            if _is_blank(dst) and not _is_blank(src):
                # Sucesso de mentira. Trata como estouro de memória: desce o tile.
                last = ("O motor saiu com código 0 mas gravou uma imagem preta "
                        "(tile {}).".format(attempt_tile))
                continue
            from PIL import Image
            with no_bomb_limit():
                with Image.open(dst) as im:
                    w, h = im.size
            return UpscaleResult(dst, w, h, device or "GPU", attempt_tile, gpu == -1,
                                 jobs or "")

        last = err

        # Morto por sinal, sem nada no stderr: assinatura clássica do Gatekeeper.
        if code is not None and code < 0 and not err.strip():
            raise UpscaleError(
                "O macOS bloqueou o motor de upscale.",
                detail="Encerrado pelo sinal {}, sem saída.".format(-code),
                hint="O binário está em quarentena. Rode o setup de novo — ele remove o "
                     "atributo com xattr.",
            )
        if any(k in low for k in _NOGPU_MARKERS):
            if gpu == -1:
                break
            continue      # segue a escada; a última tentativa é na CPU
        if any(k in low for k in _OOM_MARKERS):
            continue      # tile menor na próxima volta
        # Erro que não é de memória nem de GPU: insistir não ajuda.
        break

    raise UpscaleError(
        "O upscale falhou.",
        detail=last[-800:] if last else "O motor não deixou mensagem.",
        hint="Tente uma imagem menor, ou rode o setup de novo pelo menu.",
    )
