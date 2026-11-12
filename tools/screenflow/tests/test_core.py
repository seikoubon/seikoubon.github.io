import hashlib
import threading
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader

from core import (Cancelled, CaptureSettings, batch_crop, capture_pages, image_files,
                  make_pdf, oriented_image, save_png, selection_box, unique_folder)


def progress(*args):
    pass


def create(path, size=(30, 20), color='red', mode='RGB'):
    with Image.new(mode, size, color) as image:
        image.save(path)
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_natural_order_and_filter(tmp_path):
    for name in ['page_10.png', 'page_2.PNG', 'page_1.jpg', 'page_11.jpeg']:
        create(tmp_path / name)
    (tmp_path / 'ignore.txt').write_text('ignored')
    (tmp_path / 'fake.png').mkdir()
    assert [p.name for p in image_files(tmp_path)] == ['page_1.jpg', 'page_2.PNG', 'page_10.png', 'page_11.jpeg']


@pytest.mark.parametrize('missing', [False, True])
def test_empty_or_missing(tmp_path, missing):
    with pytest.raises(ValueError):
        image_files(tmp_path / 'missing' if missing else tmp_path)


@pytest.mark.parametrize('a,b,size,expected', [
    ((10.2, 4.5), (25.8, 15.1), (30, 20), (10, 4, 26, 16)),
    ((40, 24), (-4, -2), (30, 20), (0, 0, 30, 20)),
    ((20, 15), (2, 3), (30, 20), (2, 3, 20, 15)),
    ((10, 10), (10, 10), (30, 20), None),
])
def test_selection_bounds(a, b, size, expected):
    assert selection_box(a, b, size) == expected


def test_crop_exact_pixels_and_preserves_originals(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    with Image.new('RGB', (30, 20)) as image:
        for y in range(20):
            for x in range(30):
                image.putpixel((x, y), (x*8, y*10, 123))
        image.save(source / 'page_2.png')
        image.save(source / 'page_10.png')
    files = image_files(source)
    before = [digest(p) for p in files]
    out = batch_crop(files, (2, 3, 25, 15), (30, 20), tmp_path, threading.Event(), progress)
    assert out != source
    for p in image_files(out):
        with Image.open(p) as cropped:
            assert cropped.size == (23, 12)
            assert cropped.getpixel((0, 0)) == (16, 30, 123)
            assert cropped.getpixel((22, 11)) == (192, 140, 123)
    assert [digest(p) for p in files] == before


def test_size_mismatch_fails_before_output(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    create(source / '1.png')
    create(source / '2.png', (31, 20))
    parent = tmp_path / 'outputs'
    with pytest.raises(ValueError, match='画像サイズが異なります'):
        batch_crop(image_files(source), (0, 0, 10, 10), (30, 20), parent, threading.Event(), progress)
    assert not parent.exists()


def test_corrupt_image_names_file(tmp_path):
    p = tmp_path / 'broken.png'
    p.write_bytes(b'invalid')
    with pytest.raises(ValueError, match='broken.png'):
        batch_crop([p], (0, 0, 1, 1), (30, 20), tmp_path / 'out', threading.Event(), progress)


def test_invalid_crop(tmp_path):
    p = create(tmp_path / '1.png')
    with pytest.raises(ValueError):
        batch_crop([p], (0, 0, 99, 1), (30, 20), tmp_path, threading.Event(), progress)


def test_exif_orientation(tmp_path):
    p = tmp_path / 'rotated.jpg'
    exif = Image.Exif()
    exif[274] = 6
    with Image.new('RGB', (40, 20), 'blue') as image:
        image.save(p, exif=exif)
    with oriented_image(p) as image:
        assert image.size == (20, 40)
    out = batch_crop([p], (0, 0, 10, 30), (20, 40), tmp_path, threading.Event(), progress)
    with Image.open(image_files(out)[0]) as image:
        assert image.size == (10, 30)
    pdf = make_pdf([p], tmp_path / 'rotated.pdf', threading.Event(), progress)
    assert tuple(PdfReader(pdf).pages[0].mediabox)[2:] == (15, 30)


def test_pdf_order_pixels_alpha_and_page_count(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    create(source / 'page_10.png', (40, 60), 'blue')
    create(source / 'page_2.png', (20, 30), 'red')
    create(source / 'page_11.png', (40, 20), (0, 255, 0, 120), 'RGBA')
    files = image_files(source)
    before = [digest(p) for p in files]
    result = make_pdf(files, tmp_path / 'out.pdf', threading.Event(), progress)
    reader = PdfReader(result)
    assert len(reader.pages) == 3
    assert [tuple(p.mediabox)[2:] for p in reader.pages] == [(15, 22.5), (30, 45), (30, 15)]
    for page, expected, pixel in zip(reader.pages, [(20, 30), (40, 60), (40, 20)], [(255, 0, 0), (0, 0, 255), (0, 255, 0)]):
        image = page.images[0].image
        assert image.size == expected
        assert image.convert('RGB').getpixel((0, 0)) == pixel
    assert [digest(p) for p in files] == before
    assert not list(tmp_path.glob('.pdf_*'))


def test_pdf_jpeg_preserves_encoded_image(tmp_path):
    p = create(tmp_path / 'one.jpg')
    pdf = make_pdf([p], tmp_path / 'out.pdf', threading.Event(), progress)
    obj = PdfReader(pdf).pages[0]['/Resources']['/XObject'].get_object()['/Im0'].get_object()
    assert obj['/Filter'] == '/DCTDecode'
    assert obj.get_data() == p.read_bytes()


def test_overwrite_requires_explicit_flag(tmp_path):
    p = create(tmp_path / '1.png')
    out = tmp_path / 'existing.pdf'
    out.write_bytes(b'existing')
    with pytest.raises(FileExistsError):
        make_pdf([p], out, threading.Event(), progress)
    assert out.read_bytes() == b'existing'
    make_pdf([p], out, threading.Event(), progress, overwrite=True)
    assert len(PdfReader(out).pages) == 1


def test_cancelled_pdf_preserves_existing_and_cleans_temp(tmp_path):
    p = create(tmp_path / '1.png')
    out = tmp_path / 'existing.pdf'
    out.write_bytes(b'existing')
    cancel = threading.Event()
    def stop(i, n, message):
        if message.startswith('PDF変換:'):
            cancel.set()
    with pytest.raises(Cancelled):
        make_pdf([p], out, cancel, stop, overwrite=True)
    assert out.read_bytes() == b'existing'
    assert not list(tmp_path.glob('.pdf_*'))


def test_cancel_crop_keeps_completed_images(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    files = [create(source / f'{i}.png') for i in range(3)]
    cancel = threading.Event()
    def stop(i, n, message):
        if message.startswith('トリミング:'):
            cancel.set()
    with pytest.raises(Cancelled):
        batch_crop(files, (0, 0, 10, 10), (30, 20), tmp_path, cancel, stop)
    folders = list(tmp_path.glob('cropped_*'))
    assert len(image_files(folders[0])) == 1
    assert len(image_files(source)) == 3


def test_unique_jobs_and_no_png_overwrite(tmp_path):
    assert unique_folder(tmp_path, 'job') != unique_folder(tmp_path, 'job')
    p = create(tmp_path / 'existing.png')
    original = digest(p)
    with Image.new('RGB', (30, 20), 'blue') as image:
        with pytest.raises(FileExistsError):
            save_png(image, p)
    assert digest(p) == original
    assert not list(tmp_path.glob('.writing_*'))


def test_save_failure_is_reported(tmp_path):
    p = create(tmp_path / '1.png')
    bad_parent = tmp_path / 'not_a_folder'
    bad_parent.write_text('blocker')
    with pytest.raises(OSError):
        batch_crop([p], (0, 0, 10, 10), (30, 20), bad_parent, threading.Event(), progress)


class FakeBackend:
    def __init__(self, cancel, focus_lost_after=None):
        self.cancel = cancel
        self.shots = 0
        self.keys = []
        self.waits = []
        self.focus_lost_after = focus_lost_after
    def target(self):
        return 123
    def check_target(self, target):
        assert target == 123
        if self.focus_lost_after is not None and self.shots >= self.focus_lost_after:
            raise Cancelled('focus changed')
    def screenshot(self):
        self.shots += 1
        return Image.new('RGB', (30, 20), (self.shots, 0, 0))
    def turn_page(self, direction):
        self.keys.append(direction)
    def wait(self, seconds, cancel):
        self.waits.append(seconds)
        if cancel.is_set():
            raise Cancelled()


def test_capture_exact_shots_no_last_key_and_delays(tmp_path):
    cancel = threading.Event()
    backend = FakeBackend(cancel)
    capture_pages(CaptureSettings(5, 1.5, 2.5), tmp_path, cancel, progress, backend)
    assert backend.shots == 5
    assert backend.keys == ['right'] * 4
    assert backend.waits == [1, 1, .5, 1.5, 1.5, 1.5, 1.5]
    assert len(image_files(tmp_path)) == 5


def test_capture_cancel_no_next_key(tmp_path):
    cancel = threading.Event()
    backend = FakeBackend(cancel)
    def stop(i, n, message):
        if i == 1:
            cancel.set()
    with pytest.raises(Cancelled):
        capture_pages(CaptureSettings(5, 1, 0), tmp_path, cancel, stop, backend)
    assert backend.shots == 1
    assert backend.keys == []
    assert len(image_files(tmp_path)) == 1


def test_capture_focus_change_no_key(tmp_path):
    cancel = threading.Event()
    backend = FakeBackend(cancel, focus_lost_after=1)
    with pytest.raises(Cancelled):
        capture_pages(CaptureSettings(5, 1, 0), tmp_path, cancel, progress, backend)
    assert backend.shots == 1
    assert backend.keys == []


def test_pdf_300_pages(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    for i in range(300):
        create(source / f'page_{i+1}.png', (60, 90), (i % 256, 3, 4))
    out = make_pdf(image_files(source), tmp_path / '300.pdf', threading.Event(), progress)
    reader = PdfReader(out)
    assert len(reader.pages) == 300
    assert reader.pages[-1].images[0].image.getpixel((0, 0))[:3] == (299 % 256, 3, 4)


@pytest.mark.parametrize('direction', ['right', 'left'])
@pytest.mark.parametrize('pages', [1, 3])
def test_selected_capture_direction_and_no_final_key(tmp_path, direction, pages):
    cancel = threading.Event()
    backend = FakeBackend(cancel)
    capture_pages(CaptureSettings(pages, 0, 0, direction), tmp_path, cancel, progress, backend)
    assert backend.keys == [direction] * (pages - 1)
    assert len(image_files(tmp_path)) == pages


def test_invalid_direction_rejected_before_capture(tmp_path):
    cancel = threading.Event()
    backend = FakeBackend(cancel)
    with pytest.raises(ValueError, match='右または左'):
        capture_pages(CaptureSettings(3, 0, 0, 'up'), tmp_path, cancel, progress, backend)
    assert backend.shots == 0 and backend.keys == []


@pytest.fixture(scope='module')
def four_k_image(tmp_path_factory):
    folder = tmp_path_factory.mktemp('four_k')
    path = folder / 'color_page.png'
    # Synthetic 4K high-detail image; no actual user's screen or book content.
    with Image.effect_noise((3840, 2160), 50) as gray:
        with gray.convert('RGB') as image:
            from PIL import ImageDraw
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, 0, 3839, 250), fill='white')
            draw.text((100, 60), '4K color PDF test: text and image detail', fill='black', font_size=65)
            draw.rectangle((100, 350, 900, 700), fill=(40, 100, 210))
            image.save(path)
    return path


def test_quality_presets_dimensions_size_and_original_unchanged(tmp_path, four_k_image):
    before = digest(four_k_image)
    sizes = {}
    for quality, expected in [('low', (1600, 900)), ('medium', (2560, 1440)), ('high', (3840, 2160))]:
        result = make_pdf([four_k_image], tmp_path / f'{quality}.pdf', threading.Event(), progress, quality=quality)
        reader = PdfReader(result)
        assert len(reader.pages) == 1
        assert reader.pages[0].images[0].image.size == expected
        sizes[quality] = result.stat().st_size
    assert sizes['low'] < sizes['medium'] < sizes['high']
    assert digest(four_k_image) == before


@pytest.mark.parametrize('quality', ['low', 'medium'])
def test_compressed_pdf_no_upscale_and_transparency_on_white(tmp_path, quality):
    path = create(tmp_path / 'alpha.png', (40, 20), (255, 0, 0, 0), 'RGBA')
    result = make_pdf([path], tmp_path / 'out.pdf', threading.Event(), progress, quality=quality)
    image = PdfReader(result).pages[0].images[0].image
    assert image.size == (40, 20)
    assert all(value >= 250 for value in image.convert('RGB').getpixel((0, 0)))


@pytest.mark.parametrize('quality', ['low', 'medium'])
def test_compressed_pdf_exif_orientation(tmp_path, quality):
    path = tmp_path / 'rotated.jpg'
    exif = Image.Exif()
    exif[274] = 6
    with Image.new('RGB', (40, 20), 'blue') as image:
        image.save(path, exif=exif)
    result = make_pdf([path], tmp_path / 'out.pdf', threading.Event(), progress, quality=quality)
    assert PdfReader(result).pages[0].images[0].image.size == (20, 40)


def test_invalid_quality_rejected(tmp_path):
    path = create(tmp_path / 'one.png')
    with pytest.raises(ValueError, match='低・中・高'):
        make_pdf([path], tmp_path / 'bad.pdf', threading.Event(), progress, quality='invalid')
    assert not (tmp_path / 'bad.pdf').exists()


def test_compressed_pdf_cancel_preserves_existing(tmp_path):
    path = create(tmp_path / 'one.png')
    output = tmp_path / 'old.pdf'
    output.write_bytes(b'existing')
    cancel = threading.Event()
    def stop(i, n, message):
        if message.startswith('PDF変換:'):
            cancel.set()
    with pytest.raises(Cancelled):
        make_pdf([path], output, cancel, stop, overwrite=True, quality='medium')
    assert output.read_bytes() == b'existing'
    assert not list(tmp_path.glob('.pdf_*'))


def test_default_pdf_name_datetime_and_collision(tmp_path):
    from datetime import datetime
    from core import suggested_pdf_path
    now = datetime(2026, 11, 12, 13, 30)
    first = suggested_pdf_path(tmp_path, now)
    assert first.name == 'capture_20261112_1330.pdf'
    first.write_bytes(b'old')
    second = suggested_pdf_path(tmp_path, now)
    assert second.name == 'capture_20261112_1330_2.pdf'
    second.write_bytes(b'old2')
    assert suggested_pdf_path(tmp_path, now).name == 'capture_20261112_1330_3.pdf'
    assert first.read_bytes() == b'old'
