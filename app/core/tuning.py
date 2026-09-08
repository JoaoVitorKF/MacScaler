"""Perfil da máquina: o que este Mac faz de melhor com esta imagem.

Existe porque as escolhas que mudam o resultado — qual modelo, até que tamanho,
com que tile — dependem do hardware e do que veio do Pinterest, e o usuário não
tem como saber a resposta. O botão da janela chama `recommend()` e despeja o
resultado nos controles.

MEDIÇÃO — 30 execuções do upscayl-bin neste Mac (M4, 10 núcleos de GPU, 24 GB),
modelo ultrasharp-4x, em 2026-09-02. Nada aqui é chute; o que é chute está dito.

    720x1280 (0,9 MP)          1800x3200 (5,8 MP)
    tile    0   21,4 s         tile    0   127,3 s
    tile  128   22,9 s         tile  256   131,5 s
    tile  256   21,3 s         tile  512   142,7 s / 136,8 s
    tile  512   20,7 s         tile  768   131,2 s
    tile 1024   21,4 s         tile 1024     3,9 s  <- PRETO (reproduzido 2x)
    -j 1:1:1 / 1:2:2 / 2:2:2 / 1:4:4:  19,9 a 20,9 s, indistinguíveis
    -x (TTA) em 720x1280:  153,7 s contra 20,8 s sem a flag

Conclusões que viraram código:

- **Tile e threads não são alavanca de velocidade neste chip.** A matriz inteira
  cabe entre 19,9 s e 24,7 s na mesma imagem, e na imagem grande o automático do
  ncnn ganhou de todos os valores fixos. Logo o perfil deixa os dois como estão:
  o que ele escolhe é o que dá para escolher com efeito.
- **Tile 1024 corrompe em silêncio.** Sai com código 0, imprime "Upscayled
  Successfully!" e grava um PNG do tamanho certo, todo preto — 1080x1920 em 1,8 s,
  1800x3200 em 3,9 s, reproduzido. Abaixo de ~1 MP de entrada o mesmo tile passa
  limpo, então não dá para confiar em "funcionou uma vez". Nenhum marcador de OOM
  aparece no stderr; quem pega isso é `upscaler._is_blank()`.
- **O tempo é linear nos megapixels de ENTRADA**, ~22,5 s/MP: 0,9 MP em 21,4 s e
  5,8 MP em 127,3 s caem os dois na mesma reta com 4% de erro. Por isso baixar o
  tamanho de saída não economiza um segundo de GPU — o 4x roda igual, e o Lanczos
  vem depois. O teto de tamanho aqui é sobre arquivo e utilidade, não sobre tempo.
- **Teto de tamanho.** Medido: o 4x de 1800x3200 dá um PNG de 7200x12800 com
  **221 MB**, para ser visto numa tela de 2880 px de largura. E, pelo argumento do
  `finalize`, 4x reduzido com Lanczos é melhor do que a saída nativa. O teto é
  conselho, não trava — o campo continua editável.

`-x` (TTA) fica de fora de propósito, e aqui também foi medido: 153,7 s contra
20,8 s na mesma imagem — **7,4x**, não os 4x que se costuma repetir. Sete vezes o
tempo de GPU seguida, numa máquina sem ventoinha, por um ganho que não aparece em
imagem de Pinterest.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional

from .classifier import KIND_ART, KIND_PHOTO
from . import models as M
from .finalize import FMT_PNG

# --- constantes medidas -----------------------------------------------------

# Segundos por megapixel de ENTRADA, com o 4x. Ver a tabela no cabeçalho.
SEC_PER_MP = 22.5

# Teto de tamanho sugerido para o lado maior. Ver o cabeçalho.
MAX_LONG_EDGE = 6000

# 0 = automático do ncnn, que foi o mais rápido em tudo que se mediu aqui.
TILE_AUTO = 0
# Abaixo desta memória o automático tem menos folga para errar para cima. O 256
# é palpite conservador — não há máquina de 8 GB nesta bancada para medir.
TILE_TIGHT_MEMORY = 256
TIGHT_MEMORY_GB = 16
TILE_MIN = 64

# Acima disto o perfil avisa que é bastante GPU seguida.
SLOW_SECONDS = 150


# --- máquina ----------------------------------------------------------------

@dataclass(frozen=True)
class Machine:
    model_name: str      # "MacBook Air"
    chip: str            # "Apple M4"
    memory_gb: int
    gpu_cores: int
    fanless: bool

    @property
    def summary(self) -> str:
        bits = [b for b in (self.model_name, self.chip) if b]
        tail = []
        if self.memory_gb:
            tail.append("{} GB".format(self.memory_gb))
        if self.gpu_cores:
            tail.append("GPU de {} núcleos".format(self.gpu_cores))
        if self.fanless:
            tail.append("sem ventoinha")
        return "  ·  ".join([" ".join(bits) or "Mac"] + tail)


def _sysctl(key: str) -> str:
    try:
        return subprocess.run(["sysctl", "-n", key], capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except Exception:
        return ""


def _hardware_profile() -> dict:
    """`system_profiler SPHardwareDataType` — 0,2 s, e é onde mora o nome comercial.

    `hw.model` só devolve o código de placa (Mac16,13); "MacBook Air" é o que
    distingue a máquina sem ventoinha, e não dá para deduzir do código sem manter
    uma tabela que envelhece a cada lançamento.
    """
    out = ""
    try:
        out = subprocess.run(["system_profiler", "SPHardwareDataType"],
                             capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return {}
    fields = {}
    for line in out.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fields[k.strip().lower()] = v.strip()
    return fields


def _gpu_cores() -> int:
    """`ioreg -l` inteiro custa 0,4 s e vem com bytes que não são UTF-8; o nó do
    acelerador sozinho custa 0,02 s. Ainda assim decodifica com `replace`."""
    try:
        out = subprocess.run(["ioreg", "-rd1", "-c", "AGXAccelerator"],
                             capture_output=True, text=True, errors="replace",
                             timeout=10).stdout
    except Exception:
        return 0
    m = re.search(r'"gpu-core-count"\s*=\s*(\d+)', out)
    return int(m.group(1)) if m else 0


_cached: Optional[Machine] = None


def detect(refresh: bool = False) -> Machine:
    """Lê o hardware uma vez por execução — nada aqui muda com o app aberto."""
    global _cached
    if _cached is not None and not refresh:
        return _cached

    hw = _hardware_profile()
    model_name = hw.get("model name", "") or _sysctl("hw.model")
    chip = hw.get("chip", "") or _sysctl("machdep.cpu.brand_string")

    memory_gb = 0
    try:
        memory_gb = int(_sysctl("hw.memsize")) // (1024 ** 3)
    except ValueError:
        m = re.match(r"(\d+)\s*GB", hw.get("memory", ""))
        if m:
            memory_gb = int(m.group(1))

    _cached = Machine(
        model_name=model_name,
        chip=chip,
        memory_gb=memory_gb,
        gpu_cores=_gpu_cores(),
        # Todo MacBook Air é passivo; o resto da linha tem ventoinha. É o único
        # bit do hardware que muda a recomendação de threads.
        fanless="air" in model_name.lower(),
    )
    return _cached


# --- recomendação -----------------------------------------------------------

@dataclass(frozen=True)
class Recommendation:
    tile: int
    jobs: str
    long_edge: int
    model_name: str
    fmt: str
    skip_upscale: bool
    estimate_seconds: float
    headline: str
    reasons: List[str] = field(default_factory=list)   # curtos, cabem na janela
    details: List[str] = field(default_factory=list)   # longos, vão para o tooltip

    @property
    def estimate_text(self) -> str:
        return format_seconds(self.estimate_seconds)

    def note_text(self) -> str:
        return "  ·  ".join(self.reasons)

    def detail_text(self) -> str:
        return "\n".join("• " + d for d in (self.details or self.reasons))


def format_seconds(s: float) -> str:
    if s <= 0:
        return "—"
    if s < 90:
        return "~{:.0f}s".format(s)
    return "~{:.0f} min".format(s / 60.0)


def tile_for(machine: Machine) -> int:
    """Tile em pixels de entrada. `TILE_AUTO` deixa o ncnn decidir.

    Não depende do tamanho da imagem: um tile maior que a imagem se comporta como
    um tile único. Depende da memória, e só para o caso em que o automático teria
    pouca folga.
    """
    if machine.memory_gb and machine.memory_gb < TIGHT_MEMORY_GB:
        return TILE_TIGHT_MEMORY
    return TILE_AUTO


def jobs_for(machine: Machine) -> str:
    """`-j load:proc:save`, ou "" para não passar a flag.

    Medido: 1:1:1, 1:2:2, 2:2:2 e 1:4:4 ficaram todos dentro de um segundo um do
    outro. Uma GPU só é o gargalo, e mexer nas threads de leitura e gravação não
    encosta nele. Passar a flag seria fingir uma escolha que não muda nada.
    """
    return ""


def estimate_seconds(width: int, height: int) -> float:
    """Tempo do modelo 4x, a partir dos megapixels de *entrada*."""
    return SEC_PER_MP * (width * height) / 1e6


def recommend(machine: Machine, width: int, height: int, kind: str,
              oversized: bool = False) -> Recommendation:
    model_name = M.DEFAULT_FOR.get(kind, M.DEFAULT_FOR[KIND_PHOTO])
    model = M.get(model_name)
    tile = tile_for(machine)
    jobs = jobs_for(machine)
    longest = max(width, height)
    native = longest * model.scale
    long_edge = min(native, MAX_LONG_EDGE)
    seconds = estimate_seconds(width, height)

    short_label = model.label.split("—")[0].strip()
    kind_word = "arte" if kind == KIND_ART else "foto"

    if oversized:
        # A imagem já veio grande: o certo é não chamar o modelo.
        reasons = ["sem IA — o original já está no tamanho"]
        details = ["O original tem {}×{} px. Passar isso pelo modelo custaria minutos "
                   "e não acrescentaria detalhe: o que falta em uma imagem dessas não é "
                   "resolução.".format(width, height)]
        headline = "Perfil aplicado — sem IA, este original já está no tamanho."
        return Recommendation(tile, jobs, longest, model_name, FMT_PNG, True,
                              0.0, headline, reasons, details)

    reasons = ["{} ({})".format(short_label, kind_word)]
    details = ["Modelo {}: o classificador disse {}, e é a melhor aposta que existe "
               "aqui.".format(model.label, kind_word)]

    if long_edge < native:
        reasons.append("{} px de teto".format(long_edge))
        details.append("Alvo de {} px no lado maior. O modelo entrega {} px; o Lanczos "
                       "desce até o alvo, que é onde a imagem fica melhor. Não custa "
                       "tempo de GPU: o 4x roda igual. Acima de {} px só o arquivo "
                       "cresce — um PNG de 7200x12800 medido aqui deu 221 MB, para uma "
                       "tela de 2880 px.".format(long_edge, native, MAX_LONG_EDGE))
    else:
        reasons.append("{} px (nativo do modelo)".format(long_edge))
        details.append("Alvo de {} px no lado maior: a saída nativa do 4×, sem "
                       "redução.".format(long_edge))

    if tile == TILE_AUTO and not jobs:
        reasons.append("tile e threads no automático")
        details.append("Tile e threads ficam como o motor decide: medindo aqui, a "
                       "matriz inteira coube entre 20 s e 25 s na mesma imagem, e na "
                       "imagem grande o automático ganhou de todos os valores fixos. "
                       "Fixar um número seria fingir uma escolha.")
    else:
        reasons.append("tile {}{}".format(tile, " · -j " + jobs if jobs else ""))
        details.append("Tile de {} px: com {} GB de memória unificada o automático do "
                       "motor tem pouca folga.".format(tile, machine.memory_gb))

    reasons.append("estimativa {}".format(format_seconds(seconds)))
    details.append("Estimativa de {} para os {:.1f} MP de entrada, medida com o "
                   "UltraSharp neste Mac.".format(format_seconds(seconds),
                                                  width * height / 1e6))
    if seconds >= SLOW_SECONDS and machine.fanless:
        details.append("É bastante tempo de GPU seguido em uma máquina sem ventoinha. "
                       "Vale deixar a janela quieta enquanto roda.")

    headline = "Perfil aplicado — {} · {} px · estimativa {}".format(
        short_label, long_edge, format_seconds(seconds))
    return Recommendation(tile, jobs, long_edge, model_name, FMT_PNG, False,
                          seconds, headline, reasons, details)
