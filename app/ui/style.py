"""Aparência. Um lugar só, para não espalhar cor por todo lado.

Direção visual: grafite + latão. Fundo quase preto neutro, superfícies em
camadas separadas por fio de 1 px, e um único acento metálico quente para o
que é ação ou foco. Verde e terracota ficam reservados para estado (deu certo
/ atenção), nunca para decoração — é isso que impede a janela de virar um
painel colorido genérico.

Regra de bolso ao mexer aqui: cor nova só entra se disser algo que a
hierarquia de tipo e o fio de 1 px não conseguem dizer sozinhos.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont

# Setas e marca de seleção. O QSS do Qt não desenha forma nenhuma — ou vem de
# um arquivo, ou o Qt cai no widget nativo do macOS (que pinta azul de sistema
# e ignora a paleta daqui). Os `@2x` ao lado são achados sozinhos em tela retina.
_ICONS = (Path(__file__).resolve().parents[2] / "assets" / "ui").as_posix()

# --------------------------------------------------------------- superfícies
INK = "#0b0d10"           # a janela
INK_2 = "#0f1216"         # pé do degradê da janela
SURFACE = "#14171c"       # painel
SURFACE_HI = "#191d23"    # topo do degradê do painel
FIELD = "#1b1f25"         # campo, botão secundário
FIELD_HI = "#21262d"      # o mesmo, sob o cursor
HAIRLINE = "#23282f"      # o fio que separa
HAIRLINE_HI = "#31373f"   # o fio quando pede atenção

# ------------------------------------------------------------------- texto
TEXT = "#e7e9ec"
MUTED = "#98a0aa"
FAINT = "#6b727c"         # rótulo de seção, legenda de rodapé
DISABLED = "#4e545c"

# ------------------------------------------------------------------ acento
ACCENT = "#c9a45e"        # latão
ACCENT_HI = "#dcbc7e"
ACCENT_LO = "#a6843f"
ACCENT_SOFT = "rgba(201, 164, 94, 0.13)"   # preenchimento de segmento ativo

# ------------------------------------------------------------------ estado
OK = "#6fbf9b"
WARN = "#e08055"

FAMILY = "'.AppleSystemUIFont', 'SF Pro Text', 'Helvetica Neue', Arial, sans-serif"


def track(widget, spacing: float, *, size: int | None = None,
          weight: int | None = None):
    """Espaçamento entre letras — o QSS do Qt não tem `letter-spacing`.

    Tamanho e peso vão junto de propósito: quando a fonte é posta à mão o Qt
    passa a tratá-la como explícita, e deixar o tamanho só na folha de estilo
    faz o rótulo mudar de corpo dependendo da ordem em que a folha é aplicada.
    """
    f: QFont = widget.font()
    if size is not None:
        f.setPixelSize(size)
    if weight is not None:
        f.setWeight(QFont.Weight(weight))
    f.setLetterSpacing(QFont.AbsoluteSpacing, spacing)
    widget.setFont(f)
    return widget


QSS = """
QWidget {{
    background: {ink};
    color: {text};
    font-family: {family};
    font-size: 13px;
}}
QDialog {{ background: {ink}; }}
/* A janela ganha um degradê muito baixo para o topo não bater igual ao pé.
   Fica no #window para não pintar cada filho com um degradê próprio. */
QWidget#window {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {ink}, stop:1 {ink2});
}}
/* Sem isto todo QLabel pinta o fundo da janela por cima do painel, virando
   uma barra escura atrás do texto. */
QLabel, QCheckBox {{ background: transparent; }}

QLabel#brand {{ font-size: 18px; font-weight: 700; color: {text}; }}
QLabel#subtitle {{ color: {faint}; font-size: 12px; }}
QLabel#section {{ color: {faint}; font-size: 10px; font-weight: 700; }}
QLabel#muted {{ color: {muted}; }}
QLabel#stat {{ font-size: 27px; font-weight: 300; color: {text}; }}
QLabel#ok {{ color: {ok}; }}
QLabel#warn {{ color: {warn}; }}
QLabel#dot {{ font-size: 15px; color: {faint}; }}
QLabel#hint {{ color: {faint}; font-size: 13px; }}

/* --------------------------------------------------------------- moldura */
QFrame#panel {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {surface_hi}, stop:1 {surface});
    border: 1px solid {hairline};
    border-radius: 12px;
}}
QFrame#dropzone {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {surface_hi}, stop:1 {surface});
    border: 1px solid {hairline};
    border-radius: 12px;
}}
QFrame#dropzoneActive {{
    background: {field};
    border: 1px dashed {accent};
    border-radius: 12px;
}}
QFrame#rule {{ background: {hairline}; border: none; }}
QFrame#mark {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {accent_hi}, stop:1 {accent_lo});
    border: none;
    border-radius: 2px;
}}

/* --------------------------------------------------------------- entrada */
QLineEdit, QSpinBox, QComboBox {{
    background: {field};
    border: 1px solid {hairline};
    border-radius: 8px;
    padding: 8px 11px;
    min-height: 18px;
    selection-background-color: {accent};
    selection-color: {ink};
}}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: {hairline_hi}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {accent_lo}; }}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{
    color: {disabled}; background: {surface};
}}
QLineEdit#link {{ font-size: 13px; padding: 9px 12px; }}
QComboBox::drop-down {{
    border: none; width: 26px;
    subcontrol-origin: padding; subcontrol-position: center right;
}}
QComboBox::down-arrow {{ image: url("{icons}/chevron-down.png"); width: 11px; height: 7px; }}
QComboBox::down-arrow:disabled {{ image: none; }}
QComboBox QAbstractItemView {{
    background: {field};
    border: 1px solid {hairline_hi};
    border-radius: 8px;
    padding: 4px;
    selection-background-color: {accent_soft};
    selection-color: {accent_hi};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background: {hairline}; border: none; width: 20px;
    subcontrol-origin: border;
}}
QSpinBox::up-button {{
    subcontrol-position: top right; border-top-right-radius: 7px;
}}
QSpinBox::down-button {{
    subcontrol-position: bottom right; border-bottom-right-radius: 7px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {hairline_hi}; }}
QSpinBox::up-arrow {{ image: url("{icons}/chevron-up.png"); width: 9px; height: 6px; }}
QSpinBox::down-arrow {{ image: url("{icons}/chevron-down.png"); width: 9px; height: 6px; }}
QSpinBox::up-arrow:disabled, QSpinBox::down-arrow:disabled {{ image: none; }}

/* ---------------------------------------------------------------- botões */
QPushButton {{
    background: {field};
    border: 1px solid {hairline};
    border-radius: 8px;
    padding: 8px 14px;
    min-height: 16px;
    color: {text};
}}
QPushButton:hover {{ background: {field_hi}; border-color: {hairline_hi}; }}
QPushButton:pressed {{ background: {surface}; }}
QPushButton:disabled {{ color: {disabled}; background: {surface}; border-color: {hairline}; }}

QPushButton#ghost {{
    background: transparent;
    border: 1px solid {hairline};
    color: {muted};
    padding: 7px 13px;
}}
QPushButton#ghost:hover {{ background: {field}; color: {text}; border-color: {hairline_hi}; }}

QPushButton#primary {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {accent_hi}, stop:1 {accent_lo});
    border: 1px solid {accent};
    border-radius: 8px;
    color: #17130a;
    font-weight: 700;
    padding: 10px 20px;
}}
QPushButton#primary:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #ecd0a0, stop:1 {accent});
}}
QPushButton#primary:pressed {{ background: {accent_lo}; }}
QPushButton#primary:disabled {{
    background: {surface}; border: 1px solid {hairline}; color: {disabled};
}}

QPushButton#tune {{
    background: {accent_soft};
    border: 1px solid {accent_lo};
    color: {accent_hi};
    font-weight: 600;
    padding: 9px 14px;
}}
QPushButton#tune:hover {{ background: rgba(201, 164, 94, 0.22); border-color: {accent}; }}
QPushButton#tune:disabled {{
    background: {surface}; border-color: {hairline}; color: {disabled};
}}

QPushButton#rot {{ font-size: 17px; padding: 4px 0 7px 0; color: {muted}; }}
QPushButton#rot:hover {{ color: {accent_hi}; }}

/* Presets de tamanho como controle segmentado: um bloco só, dividido por fio.
   As bordas do meio encavalam 1 px para não virar fio duplo. */
QPushButton#segL, QPushButton#segM, QPushButton#segR {{
    padding: 7px 0; font-weight: 600; color: {muted}; border-radius: 0;
}}
QPushButton#segL {{ border-top-left-radius: 8px; border-bottom-left-radius: 8px; }}
QPushButton#segM {{ margin-left: -1px; }}
QPushButton#segR {{
    margin-left: -1px;
    border-top-right-radius: 8px; border-bottom-right-radius: 8px;
}}
QPushButton#segL:hover, QPushButton#segM:hover, QPushButton#segR:hover {{
    color: {accent_hi};
}}

/* ------------------------------------------------------- escolha de tipo */
/* Sem indicador: os rádios viram segmentos. O QButtonGroup continua o mesmo,
   só o desenho muda. */
/* `width/height: 0` sozinho não basta: sem `image` e sem borda declaradas
   o Qt cai no indicador nativo do macOS e desenha o ponto azul. */
QRadioButton::indicator {{
    width: 0px; height: 0px;
    border: none; background: transparent; image: none;
}}
QRadioButton {{
    background: {field};
    border: 1px solid {hairline};
    border-radius: 8px;
    padding: 6px 13px;
    color: {muted};
}}
QRadioButton:hover {{ color: {text}; border-color: {hairline_hi}; }}
QRadioButton:checked {{
    background: {accent_soft};
    border-color: {accent_lo};
    color: {accent_hi};
    font-weight: 600;
}}
QRadioButton:disabled {{
    background: {surface}; border-color: {hairline}; color: {disabled};
}}

QCheckBox {{ color: {muted}; spacing: 8px; }}
QCheckBox:hover {{ color: {text}; }}
QCheckBox:disabled {{ color: {disabled}; }}
QCheckBox::indicator {{
    width: 14px; height: 14px;
    border: 1px solid {hairline_hi};
    border-radius: 4px;
    background: {field};
}}
QCheckBox::indicator:checked {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 {accent_hi}, stop:1 {accent_lo});
    border-color: {accent};
    image: url("{icons}/check.png");
}}
QCheckBox::indicator:disabled {{ border-color: {hairline}; background: {surface}; }}

/* -------------------------------------------------------------- progresso */
QProgressBar {{
    background: {hairline};
    border: none;
    border-radius: 2px;
    height: 4px;
    text-align: center;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 {accent_lo}, stop:1 {accent_hi});
    border-radius: 2px;
}}

/* ---------------------------------------------------------------- resto */
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px 0; }}
QScrollBar::handle:vertical {{
    background: {hairline_hi}; border-radius: 4px; min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: #414954; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QToolTip {{
    background: {field};
    color: {text};
    border: 1px solid {hairline_hi};
    border-radius: 6px;
    padding: 6px 8px;
}}
""".format(ink=INK, ink2=INK_2, surface=SURFACE, surface_hi=SURFACE_HI,
           field=FIELD, field_hi=FIELD_HI, hairline=HAIRLINE,
           hairline_hi=HAIRLINE_HI, text=TEXT, muted=MUTED, faint=FAINT,
           disabled=DISABLED, accent=ACCENT, accent_hi=ACCENT_HI,
           accent_lo=ACCENT_LO, accent_soft=ACCENT_SOFT, ok=OK, warn=WARN,
           family=FAMILY, icons=_ICONS)
