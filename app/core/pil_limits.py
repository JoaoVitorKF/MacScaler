"""O Pillow trata imagem gigante como ataque; aqui ela é o produto.

`Image.MAX_IMAGE_PIXELS` avisa acima de ~89 MP e **levanta**
`DecompressionBombError` acima do dobro disso. A saída do 4x passa dos 179 MP a
partir de um original de ~3000x4000 — que o Pinterest serve — e a exceção vem
depois de a GPU já ter gasto os minutos todos. O limite é suspenso só em volta
de quem lê a saída do modelo, nunca para a imagem que veio da rede.
"""
from __future__ import annotations

from contextlib import contextmanager

from PIL import Image


@contextmanager
def no_bomb_limit():
    old = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = None
    try:
        yield
    finally:
        Image.MAX_IMAGE_PIXELS = old
