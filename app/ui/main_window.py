"""A janela."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import (QAction, QGuiApplication, QImageReader, QKeySequence,
                           QPixmap, QTransform)
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QRadioButton, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from .. import paths
from ..core import models as M
from ..core import pipeline
from ..core import tuning
from ..core.classifier import KIND_ART, KIND_PHOTO
from ..core.finalize import (FMT_JPEG, FMT_PNG, rotated_size, target_from_long_edge)
from ..setup import bootstrap
from .style import FAINT, MUTED, OK, QSS, WARN, track
from .worker import Worker

# Fatia da barra de progresso por etapa, para os dois modos de operação.
SPAN_FULL = {"resolve": (0, 6), "download": (6, 22), "classify": (22, 28),
             "upscale": (28, 92), "finalize": (92, 100)}
SPAN_PREPARE = {"resolve": (0, 20), "download": (20, 85), "classify": (85, 100)}
SPAN_PROCESS = {"upscale": (0, 90), "finalize": (90, 100)}
SPAN_SETUP = {"setup": (0, 100)}


def _panel(obj_name: str = "panel") -> QFrame:
    f = QFrame()
    f.setObjectName(obj_name)
    return f


def _label(text: str, obj: str = "") -> QLabel:
    lb = QLabel(text)
    if obj:
        lb.setObjectName(obj)
    return lb


def _section(text: str) -> QLabel:
    """Rótulo de seção: caixa alta, corpo pequeno, letra espaçada.

    A caixa alta e o espaçamento saem daqui e não da folha de estilo porque o
    QSS do Qt não implementa `text-transform` nem `letter-spacing` — declarar
    lá não dá erro, simplesmente não faz nada.
    """
    return track(_label(text.upper(), "section"), 1.4, size=10, weight=700)


def _rule() -> QFrame:
    """Fio de 1 px. É o que separa as seções — nenhuma caixa dentro de caixa."""
    f = QFrame()
    f.setObjectName("rule")
    f.setFixedHeight(1)
    return f


def _mark(height: int) -> QFrame:
    """A barra de latão do cabeçalho."""
    f = QFrame()
    f.setObjectName("mark")
    f.setFixedSize(3, height)
    return f


# --------------------------------------------------------------------- setup

class SetupDialog(QDialog):
    """Primeira execução: baixa o motor, os modelos, e prova que a GPU responde."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preparando o MacScaler")
        self.setModal(True)
        self.setFixedWidth(460)
        self.ok = False

        v = QVBoxLayout(self)
        v.setContentsMargins(26, 24, 26, 22)
        v.setSpacing(13)

        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(_mark(30), 0, Qt.AlignVCenter)
        tb = QVBoxLayout()
        tb.setSpacing(3)
        tb.addWidget(track(_label("MACSCALER", "brand"), 3.4, size=16, weight=700))
        tb.addWidget(_label("Instalando o motor de upscale", "subtitle"))
        head.addLayout(tb)
        head.addStretch(1)
        v.addLayout(head)
        v.addWidget(_rule())

        v.addWidget(_label(
            "Baixo uma vez o binário do upscayl-ncnn e os modelos (~80 MB). "
            "Depois disso o app funciona offline.", "subtitle"))

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        v.addWidget(self.bar)
        self.status = _label("Começando...", "muted")
        self.status.setWordWrap(True)
        v.addWidget(self.status)

        row = QHBoxLayout()
        row.addStretch(1)
        self.close_btn = QPushButton("Cancelar")
        self.close_btn.setObjectName("ghost")
        self.close_btn.clicked.connect(self._on_cancel)
        row.addWidget(self.close_btn)
        v.addLayout(row)

        self.worker = Worker(lambda progress, cancel: bootstrap.run(
            on_progress=lambda f, m: progress("setup", f, m), cancel=cancel))
        self.worker.progressed.connect(self._on_progress)
        self.worker.succeeded.connect(self._on_done)
        self.worker.failed.connect(self._on_fail)
        self.worker.cancelled.connect(self.reject)
        self.worker.start()

    def _on_progress(self, _stage, frac, msg):
        self.bar.setValue(int(frac * 100))
        self.status.setText(msg)

    def _on_done(self, _r):
        self.ok = True
        self.accept()

    def _on_fail(self, message, detail, hint):
        self.status.setObjectName("warn")
        self.status.setStyleSheet("color: {};".format(WARN))
        self.status.setText(message + ("\n" + hint if hint else ""))
        self.close_btn.setText("Fechar")
        box = QMessageBox(self)
        box.setWindowTitle("O setup falhou")
        box.setText(message)
        box.setInformativeText(hint)
        if detail:
            box.setDetailedText(detail)
        box.exec()

    def _on_cancel(self):
        self.worker.cancel()
        self.reject()


# ---------------------------------------------------------------- janela

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MacScaler")
        self.setObjectName("window")
        self.setMinimumSize(QSize(960, 620))
        self.resize(QSize(1080, 780))
        self.setAcceptDrops(True)
        self.setStyleSheet(QSS)

        self.prepared: Optional[pipeline.Prepared] = None
        self.worker: Optional[Worker] = None
        self.machine = tuning.detect()
        # Ligado pelo botão do perfil: fica ligado entre imagens, porque tile e
        # threads são propriedade da máquina, não da foto.
        self.tuned = False
        self.out_dir = paths.DEFAULT_OUTPUT_DIR
        self.result_path: Optional[Path] = None
        self._showing_result = False
        # Giro pedido pelo usuário, em graus horários. Vale para a imagem que
        # está carregada e zera a cada análise nova.
        self.rotation = 0

        self._build()
        self._set_idle()

        paste = QAction(self)
        paste.setShortcut(QKeySequence.Paste)
        paste.triggered.connect(self._paste_from_clipboard)
        self.addAction(paste)

        for seq, delta in (("Meta+[", -90), ("Meta+]", 90)):
            act = QAction(self)
            act.setShortcut(QKeySequence(seq))
            act.triggered.connect(lambda _c=False, d=delta: self._rotate(d))
            self.addAction(act)

    # ------------------------------------------------------------ construção
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 20)
        root.setSpacing(16)

        head = QHBoxLayout()
        head.setSpacing(13)
        head.addWidget(_mark(34), 0, Qt.AlignVCenter)
        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        title_box.addWidget(track(_label("MACSCALER", "brand"), 3.6, size=18,
                                  weight=700))
        title_box.addWidget(_label(
            "Resolve o original do Pinterest antes de chamar a IA — o primeiro "
            "ganho é de graça.", "subtitle"))
        head.addLayout(title_box)
        head.addStretch(1)
        self.folder_btn = QPushButton("Pasta de saída…")
        self.folder_btn.setObjectName("ghost")
        self.folder_btn.clicked.connect(self._choose_folder)
        head.addWidget(self.folder_btn, 0, Qt.AlignVCenter)
        root.addLayout(head)
        root.addWidget(_rule())

        # --- entrada
        bar = _panel()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(16, 12, 12, 12)
        bl.setSpacing(12)
        bl.addWidget(_section("Pin"), 0, Qt.AlignVCenter)
        self.link = QLineEdit()
        self.link.setObjectName("link")
        self.link.setPlaceholderText(
            "Cole o link do pin  —  pin.it/xxxx, pinterest.com/pin/… ou i.pinimg.com/…")
        self.link.returnPressed.connect(self._analyze)
        bl.addWidget(self.link, 1)
        self.analyze_btn = QPushButton("Analisar")
        self.analyze_btn.clicked.connect(self._analyze)
        bl.addWidget(self.analyze_btn)
        root.addWidget(bar)

        # --- corpo
        body = QHBoxLayout()
        body.setSpacing(16)

        self.preview_panel = _panel("dropzone")
        pv = QVBoxLayout(self.preview_panel)
        pv.setContentsMargins(16, 16, 16, 12)
        self.preview = QLabel("Arraste um link para cá\nou cole com ⌘V")
        self.preview.setObjectName("hint")
        self.preview.setAlignment(Qt.AlignCenter)
        track(self.preview, 0.6, size=13)
        self.preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        pv.addWidget(self.preview, 1)
        cap_row = QHBoxLayout()
        cap_row.setSpacing(8)
        # ↺ e ↻ (U+21BA/21BB) e não ⟲/⟳: na fonte do sistema estes desenham
        # traço cheio no tamanho de botão, os outros saem finos ou viram tofu.
        self.rot_left_btn = QPushButton("↺")
        self.rot_left_btn.setObjectName("rot")
        self.rot_left_btn.setToolTip("Girar 90° à esquerda  (⌘[)")
        self.rot_left_btn.setFixedWidth(42)
        self.rot_left_btn.clicked.connect(lambda: self._rotate(-90))
        self.rot_right_btn = QPushButton("↻")
        self.rot_right_btn.setObjectName("rot")
        self.rot_right_btn.setToolTip("Girar 90° à direita  (⌘])")
        self.rot_right_btn.setFixedWidth(42)
        self.rot_right_btn.clicked.connect(lambda: self._rotate(90))
        cap_row.addWidget(self.rot_left_btn)
        cap_row.addWidget(self.rot_right_btn)
        self.preview_caption = _label("", "muted")
        self.preview_caption.setAlignment(Qt.AlignCenter)
        track(self.preview_caption, 0.9, size=11, weight=600)
        cap_row.addWidget(self.preview_caption, 1)
        # Espaçador do mesmo tamanho dos botões, para a legenda ficar centrada.
        cap_row.addSpacing(92)
        pv.addLayout(cap_row)
        body.addWidget(self.preview_panel, 3)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(self._controls())
        scroll.setMinimumWidth(344)
        body.addWidget(scroll, 2)
        root.addLayout(body, 1)

        # --- rodapé
        root.addWidget(_rule())
        self.bar_progress = QProgressBar()
        self.bar_progress.setRange(0, 100)
        self.bar_progress.setTextVisible(False)
        self.bar_progress.hide()
        root.addWidget(self.bar_progress)

        foot = QHBoxLayout()
        foot.setSpacing(10)
        # Ponto de estado: a cor do texto do rodapé já diz o que houve, mas de
        # relance o olho acha o ponto antes de ler a frase.
        self.status_dot = _label("\u25cf", "dot")
        foot.addWidget(self.status_dot, 0, Qt.AlignVCenter)
        self.status = _label("Pronto.", "muted")
        self.status.setWordWrap(True)
        foot.addWidget(self.status, 1)
        self.reveal_btn = QPushButton("Mostrar no Finder")
        self.reveal_btn.setObjectName("ghost")
        self.reveal_btn.clicked.connect(self._reveal)
        self.reveal_btn.hide()
        foot.addWidget(self.reveal_btn)
        self.cancel_btn = QPushButton("Cancelar")
        self.cancel_btn.setObjectName("ghost")
        self.cancel_btn.clicked.connect(self._cancel)
        self.cancel_btn.hide()
        foot.addWidget(self.cancel_btn)
        self.run_btn = QPushButton("Ampliar e salvar")
        self.run_btn.setObjectName("primary")
        self.run_btn.clicked.connect(self._process)
        foot.addWidget(self.run_btn)
        root.addLayout(foot)

    def _controls(self) -> QWidget:
        panel = _panel()
        v = QVBoxLayout(panel)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(10)

        def divide():
            """Fio entre seções, no lugar de um respiro anônimo."""
            v.addSpacing(11)
            v.addWidget(_rule())
            v.addSpacing(11)

        v.addWidget(_section("Este Mac"))
        self.tune_btn = QPushButton("Ajustar para o meu Mac")
        self.tune_btn.setObjectName("tune")
        self.tune_btn.clicked.connect(self._apply_machine_profile)
        v.addWidget(self.tune_btn)
        self.machine_label = _label(self.machine.summary, "muted")
        self.machine_label.setWordWrap(True)
        v.addWidget(self.machine_label)
        track(self.machine_label, 0.1, size=11)
        self.tune_note = _label("", "muted")
        self.tune_note.setWordWrap(True)
        self.tune_note.hide()
        v.addWidget(self.tune_note)

        divide()
        v.addWidget(_section("Original"))
        self.info_size = _label("—", "stat")
        track(self.info_size, -0.4, size=27, weight=300)
        v.addWidget(self.info_size)
        self.info_gain = _label("Cole um link para começar.", "muted")
        self.info_gain.setWordWrap(True)
        v.addWidget(self.info_gain)
        self.info_warn = _label("", "warn")
        self.info_warn.setWordWrap(True)
        self.info_warn.setStyleSheet("color: {};".format(WARN))
        self.info_warn.hide()
        v.addWidget(self.info_warn)

        divide()
        v.addWidget(_section("Tipo de imagem"))
        self.kind_group = QButtonGroup(self)
        kr = QHBoxLayout()
        kr.setSpacing(6)
        for i, (key, text) in enumerate([("auto", "Automático"), (KIND_PHOTO, "Foto"),
                                         (KIND_ART, "Arte")]):
            rb = QRadioButton(text)
            rb.setProperty("kind", key)
            if i == 0:
                rb.setChecked(True)
            self.kind_group.addButton(rb, i)
            kr.addWidget(rb)
        kr.addStretch(1)
        v.addLayout(kr)
        self.kind_group.buttonClicked.connect(self._on_kind_changed)
        self.guess_label = _label("", "muted")
        self.guess_label.setWordWrap(True)
        v.addWidget(self.guess_label)

        self.model_combo = QComboBox()
        self.model_combo.currentIndexChanged.connect(self._update_target_label)
        v.addWidget(self.model_combo)

        divide()
        v.addWidget(_section("Tamanho de saída"))
        pres = QHBoxLayout()
        # Espaçamento zero: os três viram um controle segmentado só, dividido
        # por fio. Quem dá a forma de bloco é o objectName (segL/segM/segR).
        pres.setSpacing(0)
        self.preset_btns = []
        for name, mult in (("segL", 2), ("segM", 3), ("segR", 4)):
            b = QPushButton("{}×".format(mult))
            b.setObjectName(name)
            b.setFixedWidth(54)
            b.clicked.connect(lambda _c=False, m=mult: self._apply_preset(m))
            pres.addWidget(b)
            self.preset_btns.append(b)
        pres.addStretch(1)
        v.addLayout(pres)

        size_row = QHBoxLayout()
        size_row.setSpacing(8)
        self.long_edge = QSpinBox()
        self.long_edge.setRange(64, 16000)
        self.long_edge.setSingleStep(64)
        self.long_edge.setSuffix(" px")
        self.long_edge.valueChanged.connect(self._update_target_label)
        size_row.addWidget(self.long_edge)
        size_row.addWidget(_label("no lado maior", "muted"), 1)
        v.addLayout(size_row)
        self.target_label = _label("—", "muted")
        self.target_label.setWordWrap(True)
        v.addWidget(self.target_label)

        divide()
        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(10)
        fmt_row.addWidget(_section("Formato"), 0, Qt.AlignVCenter)
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItem("PNG (sem perda)", FMT_PNG)
        self.fmt_combo.addItem("JPEG (arquivo menor)", FMT_JPEG)
        fmt_row.addWidget(self.fmt_combo, 1)
        v.addLayout(fmt_row)

        self.skip_check = QCheckBox("Pular o upscale (só redimensionar e salvar)")
        self.skip_check.toggled.connect(self._update_target_label)
        v.addWidget(self.skip_check)

        v.addStretch(1)
        v.addSpacing(11)
        v.addWidget(_rule())
        v.addSpacing(11)
        self.folder_label = _label("", "muted")
        self.folder_label.setWordWrap(True)
        track(self.folder_label, 0.2, size=11)
        v.addWidget(self.folder_label)
        self._update_folder_label()
        return panel

    # ------------------------------------------------------------ estados
    def _set_busy(self, busy: bool):
        for w in (self.analyze_btn, self.link, self.run_btn, self.model_combo,
                  self.long_edge, self.fmt_combo, self.skip_check, self.folder_btn):
            w.setEnabled(not busy)
        self.tune_btn.setEnabled(not busy and self.prepared is not None)
        for b in (self.rot_left_btn, self.rot_right_btn):
            b.setEnabled(not busy and self.prepared is not None and not self._showing_result)
        for b in self.preset_btns:
            b.setEnabled(not busy and self.prepared is not None)
        for b in self.kind_group.buttons():
            b.setEnabled(not busy and self.prepared is not None)
        self.cancel_btn.setVisible(busy)
        self.bar_progress.setVisible(busy)
        if busy:
            self.reveal_btn.hide()
        else:
            self.bar_progress.setValue(0)

    def _set_idle(self):
        self.run_btn.setEnabled(False)
        self.tune_btn.setEnabled(False)
        for b in (self.rot_left_btn, self.rot_right_btn):
            b.setEnabled(False)
        for b in self.preset_btns:
            b.setEnabled(False)
        for b in self.kind_group.buttons():
            b.setEnabled(False)
        self.model_combo.setEnabled(False)
        self.long_edge.setEnabled(False)

    def _say(self, text: str, color: str = MUTED):
        self.status.setText(text)
        self.status.setStyleSheet("color: {};".format(color))
        self.status_dot.setStyleSheet("color: {};".format(
            FAINT if color == MUTED else color))

    # ------------------------------------------------------------ ações
    def _paste_from_clipboard(self):
        text = QGuiApplication.clipboard().text().strip()
        if text:
            self.link.setText(text)
            self.link.setFocus()

    def _choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Onde salvar", str(self.out_dir))
        if d:
            self.out_dir = Path(d)
            self._update_folder_label()

    def _update_folder_label(self):
        self.folder_label.setText("Salvando em  {}".format(
            str(self.out_dir).replace(str(Path.home()), "~")))

    def _reveal(self):
        if self.result_path:
            paths.reveal_in_finder(self.result_path)

    def _cancel(self):
        if self.worker:
            self.worker.cancel()
            self._say("Cancelando...")

    def _on_progress(self, spans, stage, frac, msg):
        lo, hi = spans.get(stage, (0, 100))
        self.bar_progress.setValue(int(lo + (hi - lo) * max(0.0, min(1.0, frac))))
        self._say(msg)

    def _on_failed(self, message, detail, hint):
        self._set_busy(False)
        self.bar_progress.setValue(0)
        self._say(message, WARN)
        box = QMessageBox(self)
        box.setWindowTitle("Não deu")
        box.setIcon(QMessageBox.Warning)
        box.setText(message)
        if hint:
            box.setInformativeText(hint)
        if detail:
            box.setDetailedText(detail)
        box.exec()

    def _on_cancelled(self):
        self._set_busy(False)
        self.bar_progress.setValue(0)
        self._say("Cancelado.")

    # --------------------------------------------------- [A][B][C] analisar
    def _analyze(self):
        link = self.link.text().strip()
        if not link:
            self._say("Cole um link do Pinterest primeiro.", WARN)
            return
        self._showing_result = False
        self.result_path = None
        self.reveal_btn.hide()
        self._set_busy(True)
        self.bar_progress.setValue(0)

        self.worker = Worker(lambda progress, cancel: pipeline.prepare(
            link, on_progress=progress, cancel=cancel))
        self.worker.progressed.connect(
            lambda s, f, m: self._on_progress(SPAN_PREPARE, s, f, m))
        self.worker.succeeded.connect(self._on_prepared)
        self.worker.failed.connect(self._on_failed)
        self.worker.cancelled.connect(self._on_cancelled)
        self.worker.start()

    def _on_prepared(self, prepared: pipeline.Prepared):
        self.prepared = prepared
        self.rotation = 0          # imagem nova, giro zerado
        self._set_busy(False)
        dl, res, cls = prepared.downloaded, prepared.resolved, prepared.classification

        self.info_size.setText("{} × {} px".format(dl.width, dl.height))
        bits = ["{} · {:.1f} MP".format(dl.fmt, dl.megapixels),
                "resolvido em /{}/".format(res.size_segment)]
        if res.og_width and res.og_height and res.og_url != res.url:
            bits.append("a página oferecia {}×{}".format(res.og_width, res.og_height))
        self.info_gain.setText("  ·  ".join(bits))

        warn = []
        if dl.note:
            warn.append(dl.note)
        if dl.oversized:
            warn.append("Já está em alta resolução ({:.1f} MP). O upscale foi desmarcado — "
                        "ampliar não acrescentaria detalhe.".format(dl.megapixels))
            self.skip_check.setChecked(True)
        elif dl.already_large:
            warn.append("Imagem grande ({} px no lado maior); o upscale vai demorar e "
                        "gerar um arquivo pesado.".format(max(dl.width, dl.height)))
        if warn:
            self.info_warn.setText("  ".join(warn))
            self.info_warn.show()
        else:
            self.info_warn.hide()

        self.guess_label.setText("Palpite: {} — {}.".format(cls.label, cls.reason))
        self.guess_label.setToolTip("{}\nconfiança {:.0%}".format(cls.explain(), cls.confidence))
        self._on_kind_changed()

        self.model_combo.setEnabled(True)
        self.long_edge.setEnabled(True)
        self.run_btn.setEnabled(True)
        for b in self.preset_btns:
            b.setEnabled(True)
        for b in self.kind_group.buttons():
            b.setEnabled(True)

        self.tune_btn.setEnabled(True)
        for b in (self.rot_left_btn, self.rot_right_btn):
            b.setEnabled(True)
        self._apply_preset(4)
        self._show_image(dl.path, "Original — {} × {}".format(dl.width, dl.height),
                         rotatable=True)
        if self.tuned:
            # O perfil continua valendo para a próxima imagem, com os números
            # recalculados para o tamanho dela.
            self._apply_machine_profile()
        else:
            self._say("Original pronto. Escolha o tamanho e mande ampliar.", OK)

    # ------------------------------------------------------------- rotação
    def _source_size(self):
        """Tamanho do original **como ele vai ser salvo**, com o giro já contado.

        Existe para que um único lugar decida isso: o palpite de tamanho, o alvo,
        a estimativa de tempo e o perfil do Mac têm de concordar entre si, e todos
        falam da imagem em pé.
        """
        dl = self.prepared.downloaded
        return rotated_size(dl.width, dl.height, self.rotation)

    def _rotate(self, delta: int):
        if not self.prepared or self._showing_result:
            return
        before = self._source_size()
        self.rotation = (self.rotation + delta) % 360
        after = self._source_size()
        self._rescale_preview()
        self._update_size_labels()
        if before != after:
            # O lado maior trocou de eixo: o alvo em pixels que estava lá valia
            # para a outra orientação.
            self._apply_preset(4) if not self.tuned else self._apply_machine_profile()
        self._update_target_label()
        w, h = after
        self._say("Girado {}°  ·  fica {} × {} px".format(self.rotation, w, h)
                  if self.rotation else "Sem giro  ·  {} × {} px".format(w, h))

    def _update_size_labels(self):
        if not self.prepared:
            return
        w, h = self._source_size()
        dl = self.prepared.downloaded
        self.info_size.setText("{} × {} px".format(w, h))
        self.preview_caption.setText("Original — {} × {}".format(w, h))
        del dl

    # ---------------------------------------------------------- perfil do Mac
    def _recommendation(self) -> tuning.Recommendation:
        w, h = self._source_size()
        return tuning.recommend(self.machine, w, h,
                                self.prepared.classification.kind,
                                self.prepared.downloaded.oversized)

    def _apply_machine_profile(self):
        """Põe nos controles o que este Mac faz de melhor com esta imagem."""
        if not self.prepared:
            return
        rec = self._recommendation()
        self.tuned = True

        # Tipo volta para automático: o classificador é a melhor aposta que existe aqui.
        for b in self.kind_group.buttons():
            if b.property("kind") == "auto":
                b.setChecked(True)
        self._on_kind_changed()

        idx = self.model_combo.findData(rec.model_name)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        idx = self.fmt_combo.findData(rec.fmt)
        if idx >= 0:
            self.fmt_combo.setCurrentIndex(idx)
        self.skip_check.setChecked(rec.skip_upscale)
        self.long_edge.setValue(rec.long_edge)
        self._update_target_label()

        self.tune_note.setText(rec.note_text())
        self.tune_note.setToolTip(rec.detail_text())
        self.tune_note.show()
        self.tune_btn.setText("Ajustado para este Mac  ✓")
        self._say(rec.headline, OK)

    # ------------------------------------------------------- modelo/tamanho
    def _effective_kind(self) -> str:
        btn = self.kind_group.checkedButton()
        key = btn.property("kind") if btn else "auto"
        if key == "auto":
            return self.prepared.classification.kind if self.prepared else KIND_PHOTO
        return key

    def _on_kind_changed(self, *_):
        if not self.prepared:
            return
        kind = self._effective_kind()
        self.model_combo.clear()
        for m in M.for_kind(kind):
            self.model_combo.addItem(m.label, m.name)
        default = M.DEFAULT_FOR[kind]
        idx = self.model_combo.findData(default)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)

    def _model_scale(self) -> int:
        name = self.model_combo.currentData()
        return M.get(name).scale if name else 4

    def _apply_preset(self, mult: int):
        if not self.prepared:
            return
        w, h = self._source_size()
        self.long_edge.setValue(max(w, h) * mult)

    def _update_target_label(self, *_):
        if not self.prepared:
            return
        sw, sh = self._source_size()
        w, h = target_from_long_edge(sw, sh, self.long_edge.value())
        native = max(sw, sh) * self._model_scale()
        if self.skip_check.isChecked():
            how = "redimensionamento direto, sem IA"
        elif self.long_edge.value() < native:
            how = "modelo {}× até {}px, depois Lanczos".format(self._model_scale(), native)
        else:
            how = "saída nativa do modelo"
        self.target_label.setText("→ {} × {} px  ·  {}".format(w, h, how))

    # ------------------------------------------------------- [D][E] processar
    def _process(self):
        if not self.prepared:
            return
        model_name = self.model_combo.currentData()
        long_edge = self.long_edge.value()
        fmt = self.fmt_combo.currentData()
        skip = self.skip_check.isChecked()
        out_dir = self.out_dir
        # Tile e threads são da máquina, não dos controles: recalculados agora,
        # para a imagem que está carregada.
        rotation = self.rotation
        tile = jobs = None
        if self.tuned:
            rec = self._recommendation()
            tile, jobs = rec.tile, rec.jobs

        self._set_busy(True)
        self.bar_progress.setValue(0)
        prepared = self.prepared
        self.worker = Worker(lambda progress, cancel: pipeline.process(
            prepared, out_dir=out_dir, model_name=model_name, long_edge=long_edge,
            fmt=fmt, skip_upscale=skip, rotation=rotation, tile=tile, jobs=jobs,
            on_progress=progress, cancel=cancel))
        self.worker.progressed.connect(
            lambda s, f, m: self._on_progress(SPAN_PROCESS, s, f, m))
        self.worker.succeeded.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.cancelled.connect(self._on_cancelled)
        self.worker.start()

    def _on_done(self, outcome: pipeline.Outcome):
        self._set_busy(False)
        self.result_path = outcome.final.path
        self.reveal_btn.show()

        how = "sem upscale" if outcome.skipped_upscale else "modelo {}".format(outcome.model_used)
        extra = ""
        if outcome.upscaled:
            extra = "  ·  {} → Lanczos".format(
                "{}×{}".format(outcome.upscaled.width, outcome.upscaled.height))
            if outcome.upscaled.used_cpu:
                extra += "  ·  rodou na CPU"
        msgs = ("  " + "  ".join(outcome.messages)) if outcome.messages else ""
        self._say("Salvo: {}  ({} × {}, {}){}{}".format(
            outcome.final.path.name, outcome.final.width, outcome.final.height,
            how, extra, msgs), OK)
        self._show_image(outcome.final.path, "Resultado — {} × {}".format(
            outcome.final.width, outcome.final.height))
        self._showing_result = True

    # ------------------------------------------------------------ preview
    def _show_image(self, path: Path, caption: str, rotatable: bool = False):
        """`rotatable` distingue o original do resultado.

        O resultado já sai do pipeline girado; aplicar o giro de novo aqui
        mostraria a imagem duas vezes torta.
        """
        # QPixmap(path) ignora a orientação EXIF — uma foto marcada para girar
        # aparecia deitada no preview enquanto o resto da janela já falava das
        # dimensões corrigidas. O QImageReader com autoTransform aplica.
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            return
        self._pixmap = QPixmap.fromImage(image)
        self._pixmap_rotatable = rotatable
        self.preview_caption.setText(caption)
        self._rescale_preview()

    def _rescale_preview(self):
        pix = getattr(self, "_pixmap", None)
        if pix is None:
            return
        if getattr(self, "_pixmap_rotatable", False) and self.rotation:
            pix = pix.transformed(QTransform().rotate(self.rotation),
                                  Qt.SmoothTransformation)
        area = self.preview.size()
        if area.width() < 20 or area.height() < 20:
            return
        self.preview.setPixmap(pix.scaled(area, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._rescale_preview()

    # ------------------------------------------------------------ drag/drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasText() or e.mimeData().hasUrls():
            self.preview_panel.setObjectName("dropzoneActive")
            self.preview_panel.setStyleSheet(QSS)
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self.preview_panel.setObjectName("dropzone")
        self.preview_panel.setStyleSheet(QSS)

    def dropEvent(self, e):
        self.preview_panel.setObjectName("dropzone")
        self.preview_panel.setStyleSheet(QSS)
        md = e.mimeData()
        text = ""
        if md.hasUrls() and md.urls():
            text = md.urls()[0].toString()
        elif md.hasText():
            text = md.text()
        text = text.strip()
        if text:
            self.link.setText(text)
            e.acceptProposedAction()
            self._analyze()
