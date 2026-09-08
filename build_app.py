#!/usr/bin/env python
"""Monta o MacScaler.app.

É um **bundle fino**: o executável dentro do .app é um script que chama o venv
deste projeto. Não é redistribuível — leva junto o caminho de `~/pin-upscaler` —
e essa é a escolha, não um descuido. Um bundle autocontido com PySide6 passa de
400 MB, leva minutos para montar e obriga a reconstruir a cada linha mudada;
este aqui monta em dois segundos e reflete o código na hora.

O que ele entrega, que era o ponto: ícone no Dock, duplo clique no Finder, nome
certo na barra de menus, Cmd-Tab, e nenhum terminal aberto atrás da janela.

    .venv/bin/python build_app.py            -> dist/MacScaler.app
    .venv/bin/python build_app.py --install  -> e copia para /Applications

O ícone é desenhado aqui, sem arquivo de arte externo, para o build não depender
de um .png que alguém precisa lembrar de versionar.
"""
from __future__ import annotations

import argparse
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

PROJECT = Path(__file__).resolve().parent
DIST = PROJECT / "dist"

APP_DISPLAY = "MacScaler"
APP_EXEC = "MacScaler"
BUNDLE_ID = "com.joaovitorfarias.macscaler"
VERSION = "1.3.0"
MIN_MACOS = "13.0"

ICON_SIZES = [16, 32, 64, 128, 256, 512]    # cada um também em @2x
ICON_SOURCE = PROJECT / "assets" / "appicon-source.jpeg"


# --------------------------------------------------------------------- ícone

def _squircle(n: int) -> Image.Image:
    m = Image.new("L", (n, n), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, n - 1, n - 1], radius=int(n * 0.225), fill=255)
    return m


def render_icon(size: int = 1024) -> Image.Image:
    """A foto, recortada no quadrado e com o canto arredondado do macOS.

    A arte vive em `assets/appicon-source.jpeg`, copiada para dentro do projeto de
    propósito: se o build dependesse de `~/Downloads`, o ícone sumiria na primeira
    limpeza da pasta.
    """
    src = Image.open(ICON_SOURCE).convert("RGB")
    w, h = src.size
    side = min(w, h)
    src = src.crop(((w - side) // 2, (h - side) // 2,
                    (w + side) // 2, (h + side) // 2))
    src = src.resize((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(src, (0, 0), _squircle(size))
    return out


def build_icns(dest: Path) -> Path:
    """iconutil exige a pasta .iconset com os nomes exatos que a Apple espera."""
    master = render_icon(1024)
    iconset = dest.parent / "AppIcon.iconset"
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir(parents=True)
    for s in ICON_SIZES:
        master.resize((s, s), Image.LANCZOS).save(iconset / "icon_{0}x{0}.png".format(s))
        master.resize((s * 2, s * 2), Image.LANCZOS).save(
            iconset / "icon_{0}x{0}@2x.png".format(s))
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(dest)], check=True)
    shutil.rmtree(iconset, ignore_errors=True)
    return dest


# -------------------------------------------------------------------- bundle

LAUNCHER = """#!/bin/bash
# Executável do {display}.app. Não é o app: é quem chama o app.
#
# `exec` mantém o mesmo PID, que é o que faz o Dock continuar mostrando o ícone
# deste bundle em vez de um Python genérico.
#
# `arch -arm64` não é supersticão. O Python do Xcode é universal, e quando o
# bundle é aberto pelo Finder o LaunchServices escolheu x86_64 para este script:
# o Python subia sob Rosetta e o Pillow morria em
# "incompatible architecture (have 'arm64', need 'x86_64')". Pelo terminal isso
# nunca aparece, porque lá o shell já é nativo. O LSArchitecturePriority no
# Info.plist diz a mesma coisa; os dois ficam, porque o custo é zero e o sintoma
# é o app piscar no Dock e sumir sem deixar rastro na tela.
PROJECT="{project}"
VENV="$PROJECT/.venv/bin/python"
LOG="$HOME/Library/Logs/{exe}.log"
ARCH="$(uname -m)"

mkdir -p "$(dirname "$LOG")"

if [ ! -x "$VENV" ]; then
  # Aberto pelo Finder não existe terminal para reclamar: usa uma caixa do sistema.
  osascript -e 'display alert "{display} não encontrou o projeto" message "Esperava o ambiente em '"$VENV"'.\\n\\nO app é um atalho para a pasta do projeto — se ela mudou de lugar, rode build_app.py de novo lá." as critical' >/dev/null 2>&1
  exit 1
fi

cd "$PROJECT" || exit 1
exec arch -"$ARCH" "$VENV" -W ignore -m app >>"$LOG" 2>&1
"""


def build_bundle(install: bool = False) -> Path:
    app = DIST / "{}.app".format(APP_DISPLAY)
    contents = app / "Contents"
    macos, resources = contents / "MacOS", contents / "Resources"

    shutil.rmtree(app, ignore_errors=True)
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)

    info = {
        "CFBundleName": APP_DISPLAY,
        "CFBundleDisplayName": APP_DISPLAY,
        "CFBundleExecutable": APP_EXEC,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleIconFile": "AppIcon",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "CFBundleInfoDictionaryVersion": "6.0",
        "LSMinimumSystemVersion": MIN_MACOS,
        "LSApplicationCategoryType": "public.app-category.graphics-design",
        "NSHighResolutionCapable": True,
        # Sem isto o LaunchServices escolheu x86_64 para o script do lançador e o
        # Python universal subiu sob Rosetta, matando o Pillow arm64. Ver LAUNCHER.
        "LSArchitecturePriority": ["arm64"],
        # Sem isto o macOS pode forçar o app para a aparência clara, e a janela
        # é desenhada em escuro fixo — ficaria um retângulo escuro fora de lugar.
        "NSRequiresAquaSystemAppearance": False,
        "NSHumanReadableCopyright": "Uso pessoal.",
    }
    with open(contents / "Info.plist", "wb") as fh:
        plistlib.dump(info, fh)

    (contents / "PkgInfo").write_text("APPL????")

    launcher = macos / APP_EXEC
    launcher.write_text(LAUNCHER.format(display=APP_DISPLAY, project=PROJECT, exe=APP_EXEC))
    launcher.chmod(0o755)

    build_icns(resources / "AppIcon.icns")

    # Sem assinatura o Gatekeeper trata o bundle como vindo de lugar nenhum.
    # A ad-hoc basta para abrir na própria máquina e evita o "app danificado".
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)],
                   check=False, capture_output=True)
    # O Finder cacheia ícone por data de modificação do bundle.
    subprocess.run(["touch", str(app)], check=False)

    if install:
        target = Path("/Applications") / app.name
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(app, target, symlinks=True)
        subprocess.run(["touch", str(target)], check=False)
        return target
    return app


def main() -> int:
    ap = argparse.ArgumentParser(description="Monta o {}.app".format(APP_DISPLAY))
    ap.add_argument("--install", action="store_true",
                    help="copia o bundle para /Applications depois de montar")
    ap.add_argument("--icon-preview", metavar="PNG",
                    help="só grava o ícone em 1024 px e sai, para conferir a arte")
    args = ap.parse_args()

    if args.icon_preview:
        render_icon(1024).save(args.icon_preview)
        print("ícone em {}".format(args.icon_preview))
        return 0

    app = build_bundle(install=args.install)
    size = sum(f.stat().st_size for f in app.rglob("*") if f.is_file())
    print("{} pronto  ({:.0f} KB)".format(app, size / 1024))
    print("abrir com:  open '{}'".format(app))
    return 0


if __name__ == "__main__":
    sys.exit(main())
