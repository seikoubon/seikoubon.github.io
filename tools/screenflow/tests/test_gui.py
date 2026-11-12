import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import time
import threading
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app import CropView, MainWindow
from core import Cancelled


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def finish(app, window, timeout=10):
    end = time.monotonic() + timeout
    while window.job is not None and time.monotonic() < end:
        app.processEvents()
        QTest.qWait(5)
    assert window.job is None


def test_drag_mapping_resizing_and_clear(app):
    view = CropView()
    view.resize(800, 550)
    view.show()
    image = QImage(1600, 1200, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    view.set_image(image)
    app.processEvents()
    view.fit_image()
    a = view.mapFromScene(QPointF(200, 150))
    b = view.mapFromScene(QPointF(1000, 900))
    scene_a, scene_b = view.mapToScene(a), view.mapToScene(b)
    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=a)
    QTest.mouseMove(view.viewport(), b)
    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=b)
    from core import selection_box
    assert view.box == selection_box((scene_a.x(), scene_a.y()), (scene_b.x(), scene_b.y()), (1600, 1200))
    box = view.box
    view.resize(1000, 600)
    app.processEvents()
    view.zoom(1.25)
    assert view.box == box
    assert abs(box[0] - 200) <= 3
    assert abs(box[3] - 900) <= 3
    view.clear_selection()
    assert view.box is None
    view.close()


def test_gui_load_crop_pdf_flow(app, tmp_path):
    window = MainWindow()
    errors = []
    window.error = errors.append
    window.show()
    source = tmp_path / 'source'
    source.mkdir()
    for i in [10, 2, 1]:
        Image.new('RGB', (80, 120), (i, 30, 40)).save(source / f'page_{i}.png')
    window.load_images(source)
    finish(app, window)
    assert len(window.files) == 3
    assert window.files[1].name == 'page_2.png'
    window.view.set_box((10, 20, 60, 100))
    window.crop_parent.setText(str(tmp_path))
    window.start_crop()
    finish(app, window)
    assert window.cropped_folder.is_dir()
    window.crop_to_pdf()
    assert Path(window.pdf_input.text()) == window.cropped_folder
    window.pdf_output.setText(str(tmp_path / 'result.pdf'))
    window.start_pdf()
    finish(app, window)
    assert window.last_pdf.is_file()
    assert window.open_pdf.isEnabled()
    from pypdf import PdfReader
    pdf = PdfReader(window.last_pdf)
    assert len(pdf.pages) == 3
    assert pdf.pages[0].images[0].image.size == (50, 80)
    assert not errors
    window.close()


def test_worker_responsive_cancel_and_safe_close(app):
    window = MainWindow()
    window.show()
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start(5)
    def slow(cancel, progress):
        cancel.wait(2)
        if cancel.is_set():
            raise Cancelled('stopped')
    window.start_job(slow, lambda result: None)
    QTest.qWait(70)
    assert ticks
    assert not window.tabs.isEnabled()
    window.close()  # Closing requests cancellation; it must not destroy a running QThread.
    assert window.isVisible()
    finish(app, window)
    assert window.tabs.isEnabled()
    assert window.status.text() == 'stopped'
    timer.stop()
    window.close()


def test_gui_empty_folder_error(app, tmp_path):
    window = MainWindow()
    errors = []
    window.error = errors.append
    window.load_images(tmp_path)
    finish(app, window)
    assert errors and '画像がありません' in errors[0]
    window.close()


def test_empty_input_is_reported_without_crashing(app):
    window = MainWindow()
    errors = []
    window.error = errors.append
    window.crop_input.clear()
    window.load_from_edit()
    assert errors and window.job is None
    window.close()


def test_reference_page_selection_crop_and_enlargement(app, tmp_path, monkeypatch):
    window = MainWindow()
    errors = []
    window.error = errors.append
    window.show()
    source = tmp_path / 'source'
    source.mkdir()
    Image.new('RGB', (80, 120), 'red').save(source / 'page_1.png')
    Image.new('RGB', (80, 120), 'blue').save(source / 'page_2.png')
    window.load_images(source)
    finish(app, window)
    assert not window.previous_page.isEnabled()
    window.preview_page.setCurrentIndex(1)
    finish(app, window)
    assert window.preview_index == 1
    assert 'page_2.png' in window.file_info.text()
    assert not window.next_page.isEnabled()
    window.view.set_box((10, 20, 60, 100))
    window.previous_page.click()
    finish(app, window)
    assert window.preview_index == 0 and window.view.box == (10, 20, 60, 100)
    window.next_page.click()
    finish(app, window)
    assert window.preview_index == 1 and window.view.box == (10, 20, 60, 100)
    results = []
    with monkeypatch.context() as patch:
        patch.setattr(window, 'start_job', lambda work, done: results.append(work(threading.Event(), lambda *args: None)))
        window.confirm_selection()
    assert results[0].size().width() == 50
    assert results[0].pixelColor(0, 0).blue() == 255  # Enlarged view uses page 2, not cover.
    window.crop_parent.setText(str(tmp_path))
    window.start_crop()
    finish(app, window)
    from core import image_files
    files = image_files(window.cropped_folder)
    assert len(files) == 2  # Choosing a reference never drops or reorders the cover.
    for path, color in zip(files, [(255, 0, 0), (0, 0, 255)]):
        with Image.open(path) as image:
            assert image.size == (50, 80)
            assert image.getpixel((0, 0)) == color
    assert not errors
    window.close()


def test_reference_size_change_clears_selection(app, tmp_path):
    window = MainWindow()
    errors = []
    window.error = errors.append
    Image.new('RGB', (40, 120), 'red').save(tmp_path / 'cover.png')
    Image.new('RGB', (80, 120), 'blue').save(tmp_path / 'page.png')
    window.load_images(tmp_path)
    finish(app, window)
    window.view.set_box((0, 0, 20, 100))
    window.preview_page.setCurrentIndex(1)
    finish(app, window)
    assert window.source_size == (80, 120)
    assert window.view.box is None
    window.view.set_box((10, 10, 60, 100))
    window.start_crop()
    finish(app, window)
    assert errors and '画像サイズが異なります' in errors[0]
    window.close()


def test_failed_reference_load_restores_previous_page(app, tmp_path):
    window = MainWindow()
    errors = []
    window.error = errors.append
    Image.new('RGB', (80, 120), 'red').save(tmp_path / 'page_1.png')
    (tmp_path / 'page_2.png').write_bytes(b'broken')
    window.load_images(tmp_path)
    finish(app, window)
    window.view.set_box((10, 20, 60, 100))
    window.preview_page.setCurrentIndex(1)
    finish(app, window)
    assert window.preview_index == window.preview_page.currentIndex() == 0
    assert window.view.box == (10, 20, 60, 100)
    assert errors and 'page_2.png' in errors[0]
    window.close()


def test_direction_selector_values(app):
    window = MainWindow()
    assert window.direction.currentData() == 'right'
    window.direction.setCurrentIndex(1)
    assert window.direction.currentData() == 'left'
    window.close()


def test_gui_quality_setting_reaches_pdf_export(app, tmp_path):
    window = MainWindow()
    errors = []
    window.error = errors.append
    source = tmp_path / 'source'
    source.mkdir()
    Image.new('RGB', (3200, 1800), (30, 90, 170)).save(source / 'one.png')
    window.pdf_input.setText(str(source))
    assert window.pdf_quality.currentData() == 'medium'
    window.pdf_quality.setCurrentIndex(0)
    window.pdf_output.setText(str(tmp_path / 'low.pdf'))
    window.start_pdf()
    finish(app, window)
    from pypdf import PdfReader
    assert PdfReader(window.last_pdf).pages[0].images[0].image.size == (1600, 900)
    assert '容量:' in window.pdf_result.text()
    assert not errors
    window.close()


def test_gui_auto_names_unique_for_repeated_exports(app, tmp_path, monkeypatch):
    import app as app_module
    from datetime import datetime
    from core import suggested_pdf_path
    fixed_now = datetime(2026, 11, 12, 13, 30)
    monkeypatch.setattr(app_module, 'suggested_pdf_path', lambda parent: suggested_pdf_path(parent, fixed_now))
    window = MainWindow()
    errors = []
    window.error = errors.append
    source = tmp_path / 'source'
    source.mkdir()
    Image.new('RGB', (30, 20), 'red').save(source / 'one.png')
    window.pdf_input.setText(str(source))
    first = tmp_path / 'capture_20261112_1330.pdf'
    window._suggested_pdf_output = str(first)
    window.pdf_output.setText(str(first))
    window.start_pdf()
    finish(app, window)
    assert window.last_pdf == first
    assert Path(window.pdf_output.text()).name == 'capture_20261112_1330_2.pdf'
    original = first.read_bytes()
    window.start_pdf()
    finish(app, window)
    assert window.last_pdf.name == 'capture_20261112_1330_2.pdf'
    assert Path(window.pdf_output.text()).name == 'capture_20261112_1330_3.pdf'
    assert first.read_bytes() == original
    # User-specified names must not be replaced by automatic names.
    window.pdf_output.setText(str(tmp_path / 'my_book.pdf'))
    window.refresh_pdf_name()
    assert Path(window.pdf_output.text()).name == 'my_book.pdf'
    assert not errors
    window.close()
