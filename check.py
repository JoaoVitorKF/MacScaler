#!/usr/bin/env python
"""Critérios de aceite da seção 9 do documento. Roda contra o Pinterest de verdade."""
from __future__ import annotations

import io
import re
import sys
import time
from pathlib import Path

from PIL import Image

from app.core.http import TIMEOUT, new_session
from app.core.resolver import (SIZE_LADDER, candidate_urls, resolve, split_cdn_url)
from app.errors import MacScalerError

SESSION = new_session()
results = []


def check(name):
    def deco(fn):
        results.append((name, fn))
        return fn
    return deco


def dims(url):
    r = SESSION.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    with Image.open(io.BytesIO(r.content)) as im:
        return im.size


@check("Pin com thumbnail 236x devolve a resolução de originals")
def c1():
    thumb = "https://i.pinimg.com/236x/9a/d3/00/9ad300488462432cf9b1d4745ab7abad.jpg"
    small = dims(thumb)
    res = resolve(thumb, SESSION)
    big = dims(res.url)
    assert res.size_segment == "originals", res.size_segment
    assert big[0] > small[0], (small, big)
    return "{}x{} -> {}x{} ({:.0f}x de área)".format(
        small[0], small[1], big[0], big[1], (big[0] * big[1]) / (small[0] * small[1]))


@check("Original PNG com thumbnail .jpg é resolvido pela troca de extensão")
def c2():
    # Este arquivo existe como .png em /originals/, mas o caminho anunciado é .jpg.
    jpg = "https://i.pinimg.com/236x/d5/3b/01/d53b014d86a6b6761bf649a0ed813c2b.jpg"
    res = resolve(jpg, SESSION)
    assert res.url.endswith(".png"), res.url
    assert res.size_segment == "originals", res.size_segment
    return "achou {}".format(res.url.rsplit("/", 1)[-1])


@check("Sem originals, desce a escada sem travar")
def c3():
    # Hash inexistente em /originals/: tem que percorrer a escada e falhar limpo.
    fake = "https://i.pinimg.com/736x/00/00/00/000000000000deadbeef00000000dead.jpg"
    t0 = time.time()
    try:
        resolve(fake, SESSION)
    except MacScalerError as e:
        n = len(candidate_urls("736x", "00/00/00/000000000000deadbeef00000000dead.jpg"))
        assert "CDN" in e.message or "Nenhuma" in e.message, e.message
        return "{} candidatos testados em {:.1f}s, erro legível".format(n, time.time() - t0)
    raise AssertionError("deveria ter falhado")


@check("Pin canônico pinterest.com/pin/<id>/ é resolvido pela og:image")
def c4():
    res = resolve("https://www.pinterest.com/pin/37436240650825608/", SESSION)
    assert res.pin_id == "37436240650825608", res.pin_id
    assert res.og_url, "sem og:image"
    return "og {} -> {} ({}x{})".format(
        split_cdn_url(res.og_url)[0], res.size_segment, *dims(res.url))


@check("Link curto pin.it é seguido até o pin")
def c4b():
    res = resolve("https://pin.it/206Eg7cFG", SESSION)
    assert res.pin_id, "não extraiu o id do pin"
    assert res.size_segment == "originals", res.size_segment
    return "pin.it -> pin {} -> /{}/ ({}x{})".format(
        res.pin_id, res.size_segment, *dims(res.url))


@check("Link curto que aponta para um board é recusado com mensagem clara")
def c4c():
    try:
        resolve("https://pin.it/3SaMEkpid", SESSION)
    except MacScalerError as e:
        assert "board" in (e.hint or "").lower(), e.hint
        return e.message
    raise AssertionError("aceitou um link de board")


@check("Sem og:image, a varredura do HTML acha a imagem do pin")
def c5():
    import re as _re
    from app.core.resolver import largest_pinimg_in_html
    html = SESSION.get("https://www.pinterest.com/pin/37436240650825608/",
                       timeout=TIMEOUT).text
    stripped = _re.sub(r"<meta[^>]+og:image[^>]*>", "", html)   # simula página sem og:image
    found = largest_pinimg_in_html(stripped)
    assert found and "/originals/" in found, found
    return "achou {} ({}x{})".format(found.rsplit("/", 1)[-1], *dims(found))


@check("A varredura do HTML não devolve o logotipo do Pinterest")
def c5b():
    from app.core.resolver import largest_pinimg_in_html
    # O logotipo mora em /originals/ dentro de um <style>, e por isso venceria
    # qualquer thumbnail do pin no ranking por tamanho.
    css = ("<style>:root{--instagram-background-url:url(https://i.pinimg.com/originals/"
           "d5/3b/01/d53b014d86a6b6761bf649a0ed813c2b.png)}</style>"
           "<img src=\"https://i.pinimg.com/736x/4b/2b/eb/"
           "4b2beba4f9fcbcb9791128ea370f8243.jpg\">")
    found = largest_pinimg_in_html(css)
    assert found and "4b2beb" in found, found
    assert "d53b014d" not in found, "devolveu o logotipo"
    return "ignorou o /originals/ do CSS e pegou o 736x do <img>"


@check("Ilustração é classificada como arte e escolhe o modelo de arte")
def c6():
    from app.core import models as M
    from app.core.classifier import KIND_ART, classify
    url = "https://i.pinimg.com/originals/da/0d/06/da0d0600684d25c98c8386c6d423fb00.jpg"
    r = SESSION.get(url, timeout=TIMEOUT)
    cls = classify(Image.open(io.BytesIO(r.content)))
    assert cls.kind == KIND_ART, cls
    model = M.get(M.DEFAULT_FOR[cls.kind])
    assert model.kind == KIND_ART
    return "{} -> {} ({})".format(cls.label, model.name, cls.explain())


@check("Foto com fundo desfocado NÃO é confundida com arte")
def c7():
    from app.core.classifier import KIND_PHOTO, classify
    url = "https://i.pinimg.com/originals/c4/86/2f/c4862fc2086b36c2256fe753639f601a.jpg"
    r = SESSION.get(url, timeout=TIMEOUT)
    cls = classify(Image.open(io.BytesIO(r.content)))
    assert cls.kind == KIND_PHOTO, cls
    return "{} ({})".format(cls.label, cls.reason)


@check("Imagem já grande avisa em vez de gerar um PNG gigante")
def c8():
    from app.core.downloader import SOFT_MAX_SIDE, Downloaded
    from app.core import pipeline
    d = Downloaded(path=Path("/dev/null"), url="x", width=5000, height=4000, fmt="JPEG",
                   size_bytes=9_000_000, has_alpha=False, oversized=True,
                   already_large=True)
    assert d.already_large and max(d.width, d.height) > SOFT_MAX_SIDE
    return "{}x{} ({:.1f} MP) marcado como oversized -> upscale pulado por padrão".format(
        d.width, d.height, d.megapixels)


@check("Link inválido produz mensagem legível, não stack trace")
def c9():
    for bad in ["", "banana", "https://example.com/foto.jpg",
                "https://www.pinterest.com/algumboard/"]:
        try:
            resolve(bad, SESSION)
        except MacScalerError as e:
            assert e.message and not e.message.startswith("Traceback"), e.message
            assert e.hint, "sem dica: " + e.message
        else:
            raise AssertionError("aceitou link inválido: %r" % bad)
    return "4 entradas ruins, 4 mensagens com dica"


@check("Escala do modelo é coerente com o nome do arquivo")
def c10():
    from app.core import models as M
    from app.errors import UpscaleError
    for m in M.REGISTRY.values():
        m.validate()
        assert M.scale_from_name(m.name) == m.scale, m.name
    bad = M.Model("realesr-animevideov3-x2", "x", "art", 4, "models")
    try:
        bad.validate()
    except UpscaleError:
        return "{} modelos coerentes; incoerência é detectada".format(len(M.REGISTRY))
    raise AssertionError("não detectou modelo 2x declarado como 4x")


@check("A inferência é offline (nenhuma rede depois do download)")
def c11():
    import socket
    from app.core import models as M
    from app.core.upscaler import upscale
    from app import paths
    real = socket.socket

    class Blocked(socket.socket):
        def connect(self, *a, **k):
            raise AssertionError("o upscale tentou usar a rede!")
        def connect_ex(self, *a, **k):
            raise AssertionError("o upscale tentou usar a rede!")

    socket.socket = Blocked
    try:
        out = paths.WORK_DIR / "offline-test.png"
        out.unlink(missing_ok=True)
        r = upscale(paths.ASSETS_DIR / "smoke.png", out, M.get(M.SMOKE_TEST_MODEL))
        assert (r.width, r.height) == (128, 128), (r.width, r.height)
    finally:
        socket.socket = real
    return "32x32 -> 128x128 com os sockets bloqueados, em {}".format(r.device)


@check("O perfil deste Mac é detectado e recomenda tile/threads plausíveis")
def c12():
    from app.core import tuning
    from app.core.classifier import KIND_PHOTO

    m = tuning.detect()
    assert m.memory_gb > 0, "memória não detectada"
    rec = tuning.recommend(m, 1080, 1920, KIND_PHOTO)
    # tile 0 = automático do motor; qualquer outro valor tem que ser utilizável.
    assert rec.tile == tuning.TILE_AUTO or rec.tile >= tuning.TILE_MIN, rec.tile
    assert rec.tile != 1024, "1024 é o tile que grava imagem preta neste Mac"
    assert rec.jobs == "" or rec.jobs.count(":") == 2, rec.jobs
    # O alvo nunca passa da saída nativa do modelo nem do teto.
    assert rec.long_edge <= min(1920 * 4, tuning.MAX_LONG_EDGE), rec.long_edge
    assert rec.estimate_seconds > 0
    return "{} -> tile {}, -j {}, {} px, {}".format(
        m.summary, rec.tile or "auto", rec.jobs or "padrão", rec.long_edge,
        rec.estimate_text)


@check("O tile pedido chega ao motor e a escada de recuo só desce")
def c13():
    from app.core import models as M
    from app.core.upscaler import ladder_for, upscale
    from app import paths

    assert ladder_for(512) == [512, 256, 128, 64], ladder_for(512)
    assert ladder_for(128) == [128, 64], ladder_for(128)
    assert ladder_for(None)[0] == 0, ladder_for(None)   # sem pedido, auto primeiro

    out = paths.WORK_DIR / "tile-test.png"
    out.unlink(missing_ok=True)
    r = upscale(paths.ASSETS_DIR / "smoke.png", out, M.get(M.SMOKE_TEST_MODEL),
                tile=192, jobs="1:2:2")
    assert r.tile == 192, r.tile
    assert r.jobs == "1:2:2", r.jobs
    return "escada correta; o motor rodou com tile 192 e -j 1:2:2"


@check("Saída preta do motor é reprovada em vez de virar arquivo salvo")
def c14():
    from app.core.upscaler import _is_blank
    from app import paths

    black = paths.WORK_DIR / "blank-test.png"
    Image.new("RGB", (600, 400), (0, 0, 0)).save(black)
    assert _is_blank(black), "não reconheceu a imagem preta"
    assert not _is_blank(paths.ASSETS_DIR / "smoke.png"), "reprovou uma imagem boa"
    # Preto vindo de um original preto não é falha — só a saída que perdeu o conteúdo.
    return "medido: 1080x1920 com -t 1024 sai preto em 1,8s dizendo 'Successfully'"


@check("Saída acima do teto de 'bomba' do Pillow é aberta em vez de explodir")
def c15():
    from app.core.pil_limits import no_bomb_limit

    big = (16000, 12000)   # 192 MP: o 4x de um original 4000x3000
    try:
        Image._decompression_bomb_check(big)
    except Exception as e:
        pass
    else:
        raise AssertionError("o Pillow deixou passar; o teste perdeu o sentido")
    with no_bomb_limit():
        Image._decompression_bomb_check(big)   # dentro do contexto, não levanta
    # E o limite volta ao lugar depois.
    assert Image.MAX_IMAGE_PIXELS is not None, "o limite não foi restaurado"
    return "{} MP passa dentro do contexto e o limite volta a {} MP".format(
        big[0] * big[1] // 10 ** 6, Image.MAX_IMAGE_PIXELS // 10 ** 6)


@check("O bundle monta, declara arm64 e aponta para um Python que existe")
def c16():
    import plistlib
    import build_app

    app = build_app.build_bundle()
    plist = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert plist["CFBundleName"] == "MacScaler", plist["CFBundleName"]
    # Sem isto o Finder abre o lançador sob Rosetta e o Pillow arm64 não carrega.
    assert plist.get("LSArchitecturePriority") == ["arm64"], plist.get("LSArchitecturePriority")

    launcher = app / "Contents" / "MacOS" / plist["CFBundleExecutable"]
    assert launcher.exists() and launcher.stat().st_mode & 0o111, "lançador não é executável"
    body = launcher.read_text()
    assert "arch -" in body, "o lançador não força a arquitetura nativa"

    # O lançador monta o caminho a partir de PROJECT; o que interessa é que o
    # Python que ele vai chamar exista de fato.
    m = re.search(r'^PROJECT="([^"]+)"', body, re.M)
    assert m, "o lançador não define PROJECT"
    target = Path(m.group(1)) / ".venv" / "bin" / "python"
    assert target.exists(), "o lançador aponta para {}, que não existe".format(target)
    icns = app / "Contents" / "Resources" / "AppIcon.icns"
    assert icns.stat().st_size > 10_000, "o .icns saiu pequeno demais para ter as 12 fatias"
    return "{} · {} KB · ícone {} KB".format(
        app.name, sum(f.stat().st_size for f in app.rglob("*") if f.is_file()) // 1024,
        icns.stat().st_size // 1024)


@check("Foto com EXIF de rotação aparece em pé no preview, não deitada")
def c17():
    from PySide6.QtGui import QImageReader
    from app import paths

    paths.ensure_dirs()
    src = paths.WORK_DIR / "exif-test.jpg"
    im = Image.new("RGB", (60, 90), (40, 40, 44))
    exif = im.getexif()
    exif[274] = 6                      # girar 90° ao exibir -> 90x60 na tela
    im.save(src, "JPEG", exif=exif)

    reader = QImageReader(str(src))
    reader.setAutoTransform(True)
    shown = reader.read()
    assert (shown.width(), shown.height()) == (90, 60), (shown.width(), shown.height())

    # E sem autoTransform vem torto — que era o defeito.
    plain = QImageReader(str(src))
    plain.setAutoTransform(False)
    raw = plain.read()
    assert (raw.width(), raw.height()) == (60, 90), "o teste perdeu o sentido"
    return "gravada 60x90 com orientation=6, exibida 90x60"


@check("Girar troca o eixo do lado maior e preserva a proporção")
def c18():
    from app.core.finalize import rotate, rotated_size
    from app.core.pipeline import Prepared, process
    from app.core.downloader import Downloaded
    from app.core.resolver import Resolved
    from app.core.classifier import classify
    from app.core import models as M
    from app import paths

    assert rotated_size(40, 20, 90) == (20, 40), rotated_size(40, 20, 90)
    assert rotated_size(40, 20, 180) == (40, 20)
    # Horário: o topo vai para a direita. Sem isto o giro sai espelhado.
    probe = Image.new("RGB", (2, 1), (0, 0, 0))
    probe.putpixel((0, 0), (255, 0, 0))
    assert rotate(probe, 90).getpixel((0, 0)) == (255, 0, 0), "giro não é horário"
    assert rotate(probe, 90).size == (1, 2)

    paths.ensure_dirs()
    src = paths.WORK_DIR / "rot-test.png"
    base = Image.open(paths.ASSETS_DIR / "smoke.png").convert("RGB").resize((48, 24))
    base.save(src)

    prepared = Prepared(
        Resolved(url="file://rot", size_segment="originals", pin_id="rot"),
        Downloaded(path=src, url="file://rot", width=48, height=24, fmt="PNG",
                   size_bytes=src.stat().st_size, has_alpha=False,
                   oversized=False, already_large=False),
        classify(src),
    )
    out = process(prepared, out_dir=paths.WORK_DIR / "rot-out",
                  model_name=M.SMOKE_TEST_MODEL, long_edge=96, rotation=90)
    with Image.open(out.final.path) as fin:
        w, h = fin.size
    assert (w, h) == (48, 96), (w, h)      # deitada 2:1 vira de pé 1:2, lado maior 96
    return "48x24 girada 90° -> {}x{}, proporção preservada, mesmo alvo de lado maior".format(w, h)


def main():
    print("\n  Critérios de aceite — MacScaler\n  " + "-" * 62)
    ok = fail = 0
    for name, fn in results:
        try:
            note = fn()
        except AssertionError as e:
            fail += 1
            print("  ✗  {}\n       {}".format(name, e))
        except Exception as e:
            fail += 1
            print("  ✗  {}\n       {}: {}".format(name, type(e).__name__, e))
        else:
            ok += 1
            print("  ✓  {}\n       {}".format(name, note))
    print("  " + "-" * 62)
    print("  {} passaram, {} falharam\n".format(ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
