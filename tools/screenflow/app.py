from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

from PIL.ImageQt import ImageQt
from PySide6.QtCore import Qt, QRectF, Signal, QThread, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen, QPixmap, QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QTabWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QDoubleSpinBox,
    QFileDialog, QMessageBox, QProgressBar, QGraphicsView, QGraphicsScene,
    QDialog, QCheckBox, QComboBox,
)

from core import (Cancelled, CaptureSettings, image_files, oriented_image, selection_box,
                  batch_crop, make_pdf, capture_pages, unique_folder, check_cancel,
                  suggested_pdf_path)


class Job(QThread):
    progress = Signal(int, int, str)
    succeeded = Signal(object)
    failed = Signal(str)
    stopped = Signal(str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function
        self.cancel = threading.Event()

    def run(self):
        try:
            result = self.function(self.cancel, self.progress.emit)
        except Cancelled as exc:
            self.stopped.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.succeeded.emit(result)


class CropView(QGraphicsView):
    selectionChanged = Signal(object)

    def __init__(self, selectable=True):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor("#dedede"))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setMinimumSize(320, 180)
        self.size_px = (0, 0)
        self.box = None
        self.overlay = None
        self.anchor = None
        self.selectable = selectable
        self.auto_fit = True

    def set_image(self, image):
        self.scene().clear()
        self.overlay = None
        self.box = None
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(1.0)  # Scene units are source pixels, even on HiDPI.
        self.size_px = (pixmap.width(), pixmap.height())
        self.scene().addPixmap(pixmap)
        self.setSceneRect(QRectF(0, 0, *self.size_px))
        self.fit_image()
        self.selectionChanged.emit(None)

    def fit_image(self):
        self.auto_fit = True
        if self.size_px[0]:
            self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def zoom(self, factor):
        self.auto_fit = False
        self.scale(factor, factor)

    def actual_size(self):
        self.auto_fit = False
        self.resetTransform()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.auto_fit:
            self.fit_image()

    def clear_selection(self):
        self.box = None
        if self.overlay is not None:
            self.scene().removeItem(self.overlay)
            self.overlay = None
        self.selectionChanged.emit(None)

    def set_box(self, box):
        self.box = box
        if self.overlay is not None:
            self.scene().removeItem(self.overlay)
            self.overlay = None
        if box:
            l, t, r, b = box
            pen = QPen(QColor("#0868ca"), 2)
            pen.setCosmetic(True)
            self.overlay = self.scene().addRect(QRectF(l, t, r-l, b-t), pen,
                                                QColor(30, 120, 230, 45))
        self.selectionChanged.emit(box)

    def point(self, event):
        return self.mapToScene(event.position().toPoint())

    def mousePressEvent(self, event):
        if self.selectable and self.size_px[0] and event.button() == Qt.MouseButton.LeftButton:
            p = self.point(event)
            if not self.sceneRect().contains(p):
                return
            self.anchor = p
            self.clear_selection()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.anchor is not None:
            p = self.point(event)
            self.set_box(selection_box((self.anchor.x(), self.anchor.y()),
                                       (p.x(), p.y()), self.size_px))
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.anchor is not None and event.button() == Qt.MouseButton.LeftButton:
            self.mouseMoveEvent(event)
            self.anchor = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)


def preview_data(files, index=0):
    with oriented_image(files[index]) as image:
        return ImageQt(image.convert("RGBA")).copy(), image.size


def directory_row(edit, choose):
    widget = QWidget()
    row = QHBoxLayout(widget)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(edit, 1)
    button = QPushButton("参照…")
    button.clicked.connect(choose)
    row.addWidget(button)
    return widget


def button(text, callback):
    widget = QPushButton(text)
    widget.clicked.connect(callback)
    return widget


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ScreenFlow — 撮影・トリミング・PDF")
        available = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1060, available.width()-40), min(820, available.height()-40))
        self.setMinimumSize(760, 560)
        self.job = None
        self.files = []
        self.preview_index = 0
        self.source_folder = None
        self.source_size = None
        self.cropped_folder = None
        self.last_pdf = None
        self.last_capture = None
        self.outcome = None
        self.capture_active = False
        self.tabs = QTabWidget()
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(self.tabs, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.status = QLabel("撮影するか、トリミングタブで既存の画像フォルダを開いてください。")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.stop = button("処理を停止", self.cancel_job)
        self.stop.setEnabled(False)
        layout.addWidget(self.stop)
        self.setCentralWidget(central)
        self.build_capture()
        self.build_crop()
        self.build_pdf()
        self.tabs.currentChanged.connect(lambda index: self.refresh_pdf_name() if index == 2 else None)

    def folder(self, edit):
        folder = QFileDialog.getExistingDirectory(self, "フォルダを選択", edit.text() or str(Path.home()))
        if folder:
            edit.setText(folder)

    @staticmethod
    def path(edit):
        text = edit.text().strip()
        if not text:
            raise ValueError("フォルダまたはファイルを指定してください。")
        return Path(text).expanduser().resolve()

    def error(self, message):
        QMessageBox.warning(self, "確認してください", str(message))

    def build_capture(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        info = QLabel("メインディスプレイ全体を撮影します。対象もメイン画面に置いてください。\n"
                      "開始後はアプリが最小化されます。準備時間内に対象を最前面にしてください。\n"
                      "撮影中は F8、またはマウスをメイン画面の隅へ移動すると停止します。")
        info.setWordWrap(True)
        layout.addWidget(info)
        form = QFormLayout()
        self.pages = QSpinBox()
        self.pages.setRange(1, 10000)
        self.pages.setValue(10)
        self.pages.setMaximumWidth(220)
        self.direction = QComboBox()
        self.direction.addItem("右矢印（→）", "right")
        self.direction.addItem("左矢印（←）", "left")
        self.direction.setMaximumWidth(220)
        self.delay = QDoubleSpinBox()
        self.delay.setRange(0.1, 120)
        self.delay.setSingleStep(0.1)
        self.delay.setValue(1.5)
        self.delay.setSuffix(" 秒")
        self.delay.setMaximumWidth(220)
        self.start_delay = QSpinBox()
        self.start_delay.setRange(2, 300)
        self.start_delay.setValue(10)
        self.start_delay.setSuffix(" 秒")
        self.start_delay.setMaximumWidth(220)
        self.capture_parent = QLineEdit(str(Path.home() / "Pictures" / "ScreenFlow"))
        form.addRow("撮影枚数", self.pages)
        form.addRow("ページ送りの方向", self.direction)
        form.addRow("ページ送り後の待機", self.delay)
        form.addRow("開始までの準備時間", self.start_delay)
        form.addRow("保存先（毎回サブフォルダを作成）", directory_row(
            self.capture_parent, lambda: self.folder(self.capture_parent)))
        layout.addLayout(form)
        layout.addWidget(button("撮影開始", self.start_capture))
        self.capture_result = QLabel("撮影画像は元のまま保持されます。最後の撮影後はページを送りません。")
        self.capture_result.setWordWrap(True)
        layout.addWidget(self.capture_result)
        self.to_crop = button("撮影画像をトリミングへ →", self.capture_to_crop)
        self.to_crop.setEnabled(False)
        layout.addWidget(self.to_crop)
        layout.addStretch()
        self.tabs.addTab(page, "1  撮影")

    def build_crop(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        top = QHBoxLayout()
        self.crop_input = QLineEdit()
        top.addWidget(self.crop_input, 1)
        top.addWidget(button("画像フォルダを開く…", self.choose_images))
        top.addWidget(button("読み込み", self.load_from_edit))
        layout.addLayout(top)
        preview_row = QHBoxLayout()
        preview_row.addWidget(QLabel("範囲指定に使う画像"))
        self.preview_page = QComboBox()
        self.preview_page.setMinimumContentsLength(18)
        self.preview_page.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.preview_page.setMaxVisibleItems(12)
        self.preview_page.currentIndexChanged.connect(self.change_preview)
        preview_row.addWidget(self.preview_page, 1)
        self.previous_page = button("← 前の画像", lambda: self.preview_page.setCurrentIndex(self.preview_index - 1))
        self.next_page = button("次の画像 →", lambda: self.preview_page.setCurrentIndex(self.preview_index + 1))
        self.previous_page.setEnabled(False)
        self.next_page.setEnabled(False)
        preview_row.addWidget(self.previous_page)
        preview_row.addWidget(self.next_page)
        layout.addLayout(preview_row)
        self.file_info = QLabel("画像を選び、残す範囲をドラッグしてください。範囲は全画像に適用します。")
        layout.addWidget(self.file_info)
        tools = QHBoxLayout()
        self.view = CropView()
        tools.addWidget(button("全体表示", self.view.fit_image))
        tools.addWidget(button("100%", self.view.actual_size))
        tools.addWidget(button("＋", lambda: self.view.zoom(1.25)))
        tools.addWidget(button("−", lambda: self.view.zoom(0.8)))
        tools.addWidget(button("選択解除", self.view.clear_selection))
        tools.addWidget(button("選択範囲を拡大確認", self.confirm_selection))
        layout.addLayout(tools)
        layout.addWidget(self.view, 1)
        self.selection_info = QLabel("範囲未選択")
        self.view.selectionChanged.connect(self.selection_changed)
        layout.addWidget(self.selection_info)
        self.crop_parent = QLineEdit(str(Path.home() / "Pictures" / "ScreenFlow"))
        form = QFormLayout()
        form.addRow("出力先（毎回サブフォルダを作成）", directory_row(
            self.crop_parent, lambda: self.folder(self.crop_parent)))
        layout.addLayout(form)
        bottom = QHBoxLayout()
        bottom.addWidget(button("全画像を一括トリミング", self.start_crop))
        self.to_pdf = button("トリミング画像をPDFへ →", self.crop_to_pdf)
        self.to_pdf.setEnabled(False)
        bottom.addWidget(self.to_pdf)
        layout.addLayout(bottom)
        self.tabs.addTab(page, "2  トリミング")

    def build_pdf(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        label = QLabel("PNG / JPEGを自然順に並べ、1画像を1ページにします。元の画像ファイルは変更しません。")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.use_cropped = QCheckBox("直前にトリミングした画像を使用する")
        self.use_cropped.toggled.connect(self.select_pdf_source)
        layout.addWidget(self.use_cropped)
        self.pdf_input = QLineEdit()
        self._suggested_pdf_output = str(suggested_pdf_path(Path.home() / "Documents"))
        self.pdf_output = QLineEdit(self._suggested_pdf_output)
        self.pdf_quality = QComboBox()
        self.pdf_quality.addItem("低画質 — 容量優先（長辺1600px・JPEG品質65）", "low")
        self.pdf_quality.addItem("中画質 — 標準（長辺2560px・JPEG品質85）", "medium")
        self.pdf_quality.addItem("高画質 — 元画質を保持（ファイルサイズ大）", "high")
        self.pdf_quality.setCurrentIndex(1)
        form = QFormLayout()
        form.addRow("入力画像フォルダ", directory_row(self.pdf_input, self.choose_pdf_source))
        form.addRow("PDF保存先・ファイル名", directory_row(self.pdf_output, self.choose_pdf_output))
        form.addRow("PDFの画質", self.pdf_quality)
        layout.addLayout(form)
        layout.addWidget(button("PDFを作成", self.start_pdf))
        row = QHBoxLayout()
        self.open_pdf = button("完成したPDFを開く", lambda: self.open_path(self.last_pdf))
        self.open_folder = button("出力先を開く", lambda: self.open_path(self.last_pdf.parent if self.last_pdf else None))
        self.open_pdf.setEnabled(False)
        self.open_folder.setEnabled(False)
        row.addWidget(self.open_pdf)
        row.addWidget(self.open_folder)
        layout.addLayout(row)
        self.pdf_result = QLabel("")
        self.pdf_result.setWordWrap(True)
        self.pdf_result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.pdf_result)
        layout.addStretch()
        self.tabs.addTab(page, "3  PDF作成")

    def start_job(self, function, callback, capture=False):
        if self.job is not None:
            return
        self.outcome = None
        self.done_callback = callback
        self.capture_active = capture
        self.job = Job(function, self)
        self.job.progress.connect(self.update_progress)
        self.job.succeeded.connect(lambda value: self.set_outcome("ok", value))
        self.job.failed.connect(lambda value: self.set_outcome("error", value))
        self.job.stopped.connect(lambda value: self.set_outcome("stop", value))
        self.job.finished.connect(self.job_finished)
        self.tabs.setEnabled(False)
        self.stop.setEnabled(True)
        self.progress.setValue(0)
        self.status.setText("処理を開始しています…")
        if capture:
            self.showMinimized()
        self.job.start()

    def set_outcome(self, state, value):
        self.outcome = (state, value)

    def update_progress(self, current, total, message):
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(current)
        self.status.setText(message)
        if self.capture_active:
            self.setWindowTitle(f"ScreenFlow — {message.split('  保存先:')[0]}")

    def job_finished(self):
        old = self.job
        self.job = None
        self.tabs.setEnabled(True)
        self.stop.setEnabled(False)
        if self.capture_active:
            self.showNormal()
            self.raise_()
        self.capture_active = False
        self.setWindowTitle("ScreenFlow — 撮影・トリミング・PDF")
        old.deleteLater()
        state, result = self.outcome or ("error", "処理結果を受信できませんでした。")
        if state == "ok":
            self.status.setText("完了しました。")
            self.done_callback(result)
        else:
            self.status.setText(result)
            if state == "error":
                self.error(result)

    def cancel_job(self):
        if self.job:
            self.job.cancel.set()
            self.status.setText("停止を要求しました。現在の画像処理が終わるまでお待ちください。")

    def start_capture(self):
        try:
            if os.name != "nt":
                raise ValueError("画面撮影はWindows 11で実行してください。")
            parent = self.path(self.capture_parent)
            folder = unique_folder(parent, "capture")
            settings = CaptureSettings(self.pages.value(), self.delay.value(), self.start_delay.value(),
                                       self.direction.currentData())
            self.last_capture = folder
            self.capture_result.setText(f"元画像の保存先: {folder}")
            self.to_crop.setEnabled(True)
            def work(cancel, progress):
                from capture_windows import WindowsCapture
                backend = WindowsCapture(cancel)
                try:
                    return capture_pages(settings, folder, cancel, progress, backend)
                finally:
                    backend.close()
            self.start_job(work, lambda result: self.capture_result.setText(f"撮影完了: {result}"), capture=True)
        except Exception as exc:
            self.error(exc)

    def capture_to_crop(self):
        if self.last_capture:
            self.tabs.setCurrentIndex(1)
            self.load_images(self.last_capture)

    def choose_images(self):
        folder = QFileDialog.getExistingDirectory(self, "画像フォルダを開く", self.crop_input.text())
        if folder:
            self.load_images(Path(folder))

    def load_from_edit(self):
        try:
            self.load_images(self.path(self.crop_input))
        except Exception as exc:
            self.error(exc)

    def load_images(self, folder):
        try:
            folder = Path(folder)
            def work(cancel, progress):
                check_cancel(cancel)
                files = image_files(folder)
                image, size = preview_data(files)
                check_cancel(cancel)
                return folder, files, image, size
            def done(result):
                folder, self.files, image, self.source_size = result
                self.preview_index = 0
                self.preview_page.blockSignals(True)
                self.preview_page.clear()
                for i, path in enumerate(self.files):
                    self.preview_page.addItem(f"{i+1} / {len(self.files)}  {path.name}")
                self.preview_page.blockSignals(False)
                self.source_folder = folder
                self.cropped_folder = None
                self.use_cropped.setChecked(False)
                self.to_pdf.setEnabled(False)
                self.crop_input.setText(str(folder))
                self.crop_parent.setText(str(folder.parent))
                self.pdf_input.setText(str(folder))
                self.view.set_image(image)
                self.update_preview_info()
            self.start_job(work, done)
        except Exception as exc:
            self.error(exc)

    def update_preview_info(self):
        self.file_info.setText(f"{len(self.files)} 枚  |  範囲指定: {self.preview_index+1}枚目  "
                               f"{self.files[self.preview_index].name}  |  "
                               f"{self.source_size[0]} × {self.source_size[1]} px")
        self.previous_page.setEnabled(self.preview_index > 0)
        self.next_page.setEnabled(self.preview_index + 1 < len(self.files))

    def change_preview(self, index):
        # Commit the selector only after decoding succeeds. A failed preview leaves
        # the previous image, crop coordinates and baseline dimensions together.
        self.preview_page.blockSignals(True)
        self.preview_page.setCurrentIndex(self.preview_index if self.files else -1)
        self.preview_page.blockSignals(False)
        if self.job or not 0 <= index < len(self.files) or index == self.preview_index:
            return
        files = list(self.files)
        old_size, old_box = self.source_size, self.view.box
        def work(cancel, progress):
            check_cancel(cancel)
            result = preview_data(files, index)
            check_cancel(cancel)
            return result
        def done(result):
            image, size = result
            self.preview_index, self.source_size = index, size
            self.preview_page.blockSignals(True)
            self.preview_page.setCurrentIndex(index)
            self.preview_page.blockSignals(False)
            self.view.set_image(image)
            if size == old_size and old_box:
                self.view.set_box(old_box)
            self.update_preview_info()
            if size != old_size:
                self.status.setText("画像サイズが変わったため、選択範囲を解除しました。範囲を指定し直してください。")
        self.start_job(work, done)

    def selection_changed(self, box):
        if box:
            l, t, r, b = box
            self.selection_info.setText(f"元画像座標: 左 {l} / 上 {t} / 右 {r} / 下 {b}  |  "
                                        f"サイズ {r-l} × {b-t} px（右・下は含まない）")
        else:
            self.selection_info.setText("範囲未選択 — 画像上をドラッグしてください。")

    def confirm_selection(self):
        if not self.view.box or not self.files:
            self.error("先に範囲を選択してください。")
            return
        box = self.view.box
        reference = self.files[self.preview_index]
        def work(cancel, progress):
            check_cancel(cancel)
            with oriented_image(reference) as image:
                with image.crop(box) as cropped:
                    return ImageQt(cropped.convert("RGBA")).copy()
        def done(image):
            dialog = QDialog(self)
            dialog.setWindowTitle("選択範囲の確認（元画像の100%表示）")
            dialog.resize(850, 650)
            layout = QVBoxLayout(dialog)
            view = CropView(selectable=False)
            layout.addWidget(view)
            view.set_image(image)
            view.actual_size()
            row = QHBoxLayout()
            row.addWidget(button("全体表示", view.fit_image))
            row.addWidget(button("100%", view.actual_size))
            row.addWidget(button("閉じる", dialog.accept))
            layout.addLayout(row)
            dialog.exec()
        self.start_job(work, done)

    def start_crop(self):
        try:
            if not self.files or not self.view.box:
                raise ValueError("画像を読み込み、範囲を選択してください。")
            parent = self.path(self.crop_parent)
            files, box, size = list(self.files), self.view.box, self.source_size
            def done(folder):
                self.cropped_folder = folder
                self.to_pdf.setEnabled(True)
                self.status.setText(f"トリミング完了: {folder}")
            self.start_job(lambda c, p: batch_crop(files, box, size, parent, c, p), done)
        except Exception as exc:
            self.error(exc)

    def crop_to_pdf(self):
        self.tabs.setCurrentIndex(2)
        self.use_cropped.setChecked(True)
        self.select_pdf_source(True)

    def select_pdf_source(self, checked):
        self.pdf_input.setReadOnly(checked)
        if checked:
            if self.cropped_folder:
                self.pdf_input.setText(str(self.cropped_folder))
            else:
                self.use_cropped.blockSignals(True)
                self.use_cropped.setChecked(False)
                self.use_cropped.blockSignals(False)
                self.pdf_input.setReadOnly(False)
                self.error("まだトリミング済み画像がありません。")
        elif self.source_folder:
            self.pdf_input.setText(str(self.source_folder))

    def choose_pdf_source(self):
        folder = QFileDialog.getExistingDirectory(self, "PDFにする画像フォルダ", self.pdf_input.text())
        if folder:
            self.use_cropped.blockSignals(True)
            self.use_cropped.setChecked(False)
            self.use_cropped.blockSignals(False)
            self.pdf_input.setReadOnly(False)
            self.pdf_input.setText(folder)

    def choose_pdf_output(self):
        self.refresh_pdf_name()
        path, _ = QFileDialog.getSaveFileName(self, "PDF保存先", self.pdf_output.text(), "PDF (*.pdf)")
        if path:
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            keep_auto_name = (self._suggested_pdf_output is not None and
                              Path(path).name == Path(self._suggested_pdf_output).name)
            self._suggested_pdf_output = path if keep_auto_name else None
            self.pdf_output.setText(path)

    def refresh_pdf_name(self):
        if self._suggested_pdf_output is not None and self.pdf_output.text() == self._suggested_pdf_output:
            path = suggested_pdf_path(Path(self.pdf_output.text()).parent)
            self._suggested_pdf_output = str(path)
            self.pdf_output.setText(str(path))

    def start_pdf(self):
        try:
            self.refresh_pdf_name()
            source, output = self.path(self.pdf_input), self.path(self.pdf_output)
            quality = self.pdf_quality.currentData()
            if output.suffix.lower() != ".pdf":
                raise ValueError("ファイル名の末尾を .pdf にしてください。")
            overwrite = output.exists()
            if overwrite and QMessageBox.question(self, "PDFの上書き", f"既存のPDFを置き換えますか？\n{output}") != QMessageBox.StandardButton.Yes:
                return
            def work(cancel, progress):
                return make_pdf(image_files(source), output, cancel, progress, overwrite, quality)
            def done(result):
                self.last_pdf = result
                self.open_pdf.setEnabled(True)
                self.open_folder.setEnabled(True)
                size_mb = result.stat().st_size / (1024 * 1024)
                self.pdf_result.setText(f"PDF作成完了: {result}\n容量: {size_mb:.2f} MB")
                self.refresh_pdf_name()
            self.start_job(work, done)
        except Exception as exc:
            self.error(exc)

    def open_path(self, path):
        if path and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.error(f"開けませんでした: {path}")

    def closeEvent(self, event):
        if self.job:
            self.cancel_job()
            event.ignore()
            self.status.setText("停止中です。処理が止まってから、もう一度閉じてください。")
        else:
            event.accept()


def main():
    # Qt 6 handles DPI scaling; no conflicting legacy SetProcessDPIAware call.
    app = QApplication(sys.argv)
    app.setApplicationName("ScreenFlow")
    app.setStyle("windowsvista" if os.name == "nt" else "Fusion")
    app.setFont(QFont("Yu Gothic UI" if os.name == "nt" else "Noto Sans CJK JP", 10))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
