"""Sessão HTTP única. O CDN do Pinterest recusa User-Agent de biblioteca."""
from __future__ import annotations

import warnings

import requests

warnings.filterwarnings("ignore", message=".*OpenSSL.*")

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# O CDN devolve 403 sem UA de navegador; em alguns casos exige o Referer também.
DEFAULT_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Referer": "https://www.pinterest.com/",
}

TIMEOUT = (10, 30)  # (connect, read)


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    return s
