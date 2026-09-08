"""[A] Resolver o link até o arquivo original.

A etapa que mais vale: o ganho aqui é gratuito e sem perda. Fazer upscale de IA
sobre um thumbnail de 236px quando existe um original de 2400px é exatamente o
erro que este software existe para evitar.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from ..errors import InvalidLinkError, NetworkError, ResolveError
from .http import TIMEOUT, new_session

# Escada de tamanhos do CDN, do maior para o menor.
SIZE_LADDER: List[str] = ["originals", "1200x", "736x", "564x", "474x", "236x", "192x", "70x"]

# O thumbnail é servido como .jpg mesmo quando o original é PNG. Causa nº 1 de
# falso-negativo "o original não existe".
EXT_LADDER: List[str] = [".jpg", ".png", ".webp", ".jpeg", ".gif"]

# A extensão é obrigatória e não-gulosa: sem isso o regex engole CSS vizinho
# quando a URL aparece dentro de um url(...) numa folha de estilo.
_CDN_RE = re.compile(
    r"https?://i\.pinimg\.com/(?P<size>originals|\d+x\d*)/"
    r"(?P<rest>[^\s\"'?<>()\[\]{}\\]+?\.(?:jpg|jpeg|png|webp|gif))",
    re.IGNORECASE,
)
_PIN_ID_RE = re.compile(r"/pin/(?P<id>\d+)")
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s\"'<>]+")
_OG_IMAGE_RE = re.compile(
    r"<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"'](?P<url>[^\"']+)[\"']", re.I
)
_OG_IMAGE_ALT_RE = re.compile(
    r"<meta[^>]+content=[\"'](?P<url>[^\"']+)[\"'][^>]+property=[\"']og:image[\"']", re.I
)
_OG_DIM_RE = r"<meta[^>]+property=[\"']og:image:{d}[\"'][^>]+content=[\"'](?P<v>\d+)[\"']"
_ANY_PINIMG_RE = re.compile(
    r"i\.pinimg\.com/(?:originals|\d+x\d*)/"
    r"[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]+\.(?:jpg|jpeg|png|webp|gif)",
    re.I,
)

ProgressFn = Callable[[str], None]


@dataclass
class Resolved:
    """O que a etapa [A] entrega para a [B]."""
    url: str                       # melhor URL encontrada e validada
    size_segment: str              # "originals", "1200x", ...
    pin_id: Optional[str] = None
    source_url: str = ""           # o link que o usuário colou
    og_url: Optional[str] = None   # o que a página prometia
    og_width: Optional[int] = None
    og_height: Optional[int] = None
    content_length: Optional[int] = None
    tried: List[str] = field(default_factory=list)

    @property
    def is_original(self) -> bool:
        return self.size_segment == "originals"


# ---------------------------------------------------------------- normalização

def extract_url(text: str) -> str:
    """Aceita texto colado com sujeira em volta; devolve a primeira URL."""
    text = (text or "").strip()
    if not text:
        raise InvalidLinkError("Nenhum link informado.", hint="Cole o link de um pin do Pinterest.")
    m = _URL_IN_TEXT_RE.search(text)
    if not m:
        # tolerar "pinterest.com/pin/123" sem esquema
        if "pinterest.com" in text or "pin.it" in text or "pinimg.com" in text:
            return "https://" + text.lstrip("/")
        raise InvalidLinkError(
            "Isso não parece um link.",
            detail=text[:120],
            hint="Esperado algo como https://pin.it/xxxx ou https://www.pinterest.com/pin/123456/",
        )
    return m.group(0).rstrip(".,)]}”’")


def is_pinterest_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return (
        host == "pin.it"
        or host == "pinimg.com"
        or host.endswith(".pinimg.com")
        or host == "pinterest.com"
        or host.endswith(".pinterest.com")
    )


def split_cdn_url(url: str) -> Optional[Tuple[str, str]]:
    """Devolve (size_segment, resto-do-caminho-sem-extensão + ext) ou None."""
    m = _CDN_RE.search(url)
    if not m:
        return None
    return m.group("size"), m.group("rest")


def build_cdn_url(size: str, rest: str, ext: Optional[str] = None) -> str:
    if ext is not None:
        rest = re.sub(r"\.[A-Za-z0-9]+$", "", rest) + ext
    return "https://i.pinimg.com/{}/{}".format(size, rest)


# ------------------------------------------------------------------- validação

def _probe(session: requests.Session, url: str) -> Tuple[bool, Optional[int]]:
    """HEAD; se o CDN não colaborar com HEAD, cai para um GET de 1 byte."""
    try:
        r = session.head(url, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code == 200:
            cl = r.headers.get("Content-Length")
            return True, int(cl) if cl and cl.isdigit() else None
        if r.status_code in (403, 405, 501):
            r = session.get(
                url, timeout=TIMEOUT, allow_redirects=True, stream=True,
                headers={"Range": "bytes=0-0"},
            )
            ok = r.status_code in (200, 206)
            cr = r.headers.get("Content-Range", "")
            total = None
            if "/" in cr:
                tail = cr.rsplit("/", 1)[-1]
                if tail.isdigit():
                    total = int(tail)
            r.close()
            return ok, total
        return False, None
    except requests.RequestException:
        return False, None


# -------------------------------------------------------------- páginas do pin

def follow_short_link(session: requests.Session, url: str) -> str:
    """pin.it exige seguir redirects até o pin canônico."""
    try:
        r = session.get(url, timeout=TIMEOUT, allow_redirects=True, stream=True)
        final = r.url
        r.close()
        return final
    except requests.RequestException as e:
        raise NetworkError(
            "Não consegui abrir o link curto.",
            detail=str(e),
            hint="Verifique a conexão e se o link ainda é válido.",
        )


def fetch_pin_page(session: requests.Session, url: str) -> str:
    try:
        r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException as e:
        raise NetworkError("Falha de rede ao abrir o pin.", detail=str(e))
    if r.status_code == 404:
        raise ResolveError("Esse pin não existe mais.", hint="O link pode ter sido removido.")
    if r.status_code == 403:
        raise ResolveError(
            "O Pinterest recusou a requisição (403).",
            hint="Tente novamente em alguns instantes ou cole a URL direta da imagem (i.pinimg.com).",
        )
    if r.status_code != 200:
        raise ResolveError("O Pinterest respondeu {} ao abrir o pin.".format(r.status_code))
    return r.text


def parse_og_image(html: str) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    """OpenGraph é o caminho estável — não depende do JSON interno do app."""
    m = _OG_IMAGE_RE.search(html) or _OG_IMAGE_ALT_RE.search(html)
    url = m.group("url").replace("&amp;", "&") if m else None

    def _dim(which: str) -> Optional[int]:
        mm = re.search(_OG_DIM_RE.format(d=which), html, re.I)
        return int(mm.group("v")) if mm else None

    return url, _dim("width"), _dim("height")


def largest_pinimg_in_html(html: str) -> Optional[str]:
    """Fallback quando não há og:image: maior ocorrência de i.pinimg.com no HTML.

    Antes de varrer, tira os blocos <style> e os `url(...)` de CSS. Sem isso o
    primeiro resultado é o logotipo do próprio Pinterest — que mora em
    /originals/ e por isso ganharia de qualquer thumbnail do pin. Devolver a
    logomarca em vez da imagem do pin é pior que não achar nada.
    """
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"url\(\s*['\"]?[^)]*\)", " ", html, flags=re.I)

    best, best_rank = None, -1
    for raw in set(_ANY_PINIMG_RE.findall(html)):
        candidate = "https://" + raw
        parts = split_cdn_url(candidate)
        if not parts:
            continue
        size = parts[0]
        rank = len(SIZE_LADDER) - SIZE_LADDER.index(size) if size in SIZE_LADDER else 0
        if rank > best_rank:
            best, best_rank = candidate, rank
    return best


# ------------------------------------------------------------------ orquestração

def candidate_urls(size: str, rest: str) -> List[str]:
    """Escada completa: para cada tamanho (do maior ao menor), cada extensão plausível.

    A extensão original vem primeiro, porque na maioria dos casos ela acerta de
    primeira e economiza requisições.
    """
    m = re.search(r"(\.[A-Za-z0-9]+)$", rest)
    own_ext = m.group(1).lower() if m else ".jpg"
    exts = [own_ext] + [e for e in EXT_LADDER if e != own_ext]

    start = SIZE_LADDER.index(size) if size in SIZE_LADDER else 0
    sizes = SIZE_LADDER[:start + 1] if size in SIZE_LADDER else SIZE_LADDER

    out: List[str] = []
    for s in sizes:
        for e in exts:
            # Só vale caçar extensão alternativa nos tamanhos grandes; nos menores
            # o thumbnail sempre existe com a extensão anunciada.
            if e != own_ext and s not in ("originals", "1200x"):
                continue
            out.append(build_cdn_url(s, rest, e))
    return out


def resolve(link: str, session: Optional[requests.Session] = None,
            progress: Optional[ProgressFn] = None) -> Resolved:
    """link (pin.it | /pin/id | i.pinimg.com) -> maior URL do CDN que responde 200."""
    session = session or new_session()
    say = progress or (lambda _m: None)

    url = extract_url(link)
    if not is_pinterest_host(url):
        raise InvalidLinkError(
            "Esse link não é do Pinterest.",
            detail=url,
            hint="Aceito pin.it, pinterest.com/pin/... ou uma URL direta de i.pinimg.com.",
        )

    pin_id: Optional[str] = None
    og_url = og_w = og_h = None

    if "pin.it" in url:
        say("Seguindo o link curto...")
        url = follow_short_link(session, url)

    m = _PIN_ID_RE.search(url)
    if m:
        pin_id = m.group("id")

    parts = split_cdn_url(url)
    if parts is None:
        if "/videos/" in url:
            raise ResolveError("Isso é um vídeo.", hint="A v1 trabalha só com imagens estáticas.")
        if not pin_id:
            raise InvalidLinkError(
                "Não achei o id do pin nesse link.",
                detail=url,
                hint="Use o link de um pin individual, não o de um board.",
            )
        say("Lendo a página do pin...")
        html = fetch_pin_page(session, "https://www.pinterest.com/pin/{}/".format(pin_id))
        og_url, og_w, og_h = parse_og_image(html)
        chosen = og_url or largest_pinimg_in_html(html)
        if not chosen:
            raise ResolveError(
                "Não encontrei a imagem na página do pin.",
                hint="O Pinterest pode ter mudado o HTML, ou o pin exige login.",
            )
        parts = split_cdn_url(chosen)
        if parts is None:
            raise ResolveError("A imagem do pin está num formato de URL que não reconheço.",
                               detail=chosen)
        url = chosen

    size, rest = parts
    say("Procurando o arquivo original...")

    tried: List[str] = []
    for candidate in candidate_urls(size, rest):
        tried.append(candidate)
        ok, length = _probe(session, candidate)
        if ok:
            csize = split_cdn_url(candidate)[0]
            return Resolved(
                url=candidate, size_segment=csize, pin_id=pin_id, source_url=link,
                og_url=og_url, og_width=og_w, og_height=og_h,
                content_length=length, tried=tried,
            )

    raise ResolveError(
        "Nenhuma versão dessa imagem respondeu no CDN do Pinterest.",
        detail="Tentei {} URLs, da maior para a menor.".format(len(tried)),
        hint="O pin pode ter sido removido, ou sua conexão está bloqueando o i.pinimg.com.",
    )
