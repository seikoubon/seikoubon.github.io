"""Qt-independent operations. Inputs are snapshots; originals are never modified."""
from __future__ import annotations

import io
import math
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

from PIL import Image, ImageOps

Progress = Callable[[int, int, str], None]

# Long-edge limits, never enlarge small sources. High preserves the original.
PDF_QUALITY = {"low": (1600, 65), "medium": (2560, 85), "high": (None, None)}


def suggested_pdf_path(parent: Path, now: datetime | None = None) -> Path:
    stem = f"capture_{now or datetime.now():%Y%m%d_%H%M}"
    candidate = parent / f"{stem}.pdf"
    counter = 2
    while candidate.exists():
        candidate = parent / f"{stem}_{counter}.pdf"
        counter += 1
    return candidate


class Cancelled(Exception):
    pass


def check_cancel(cancel: threading.Event):
    if cancel.is_set():
        raise Cancelled("停止しました。保存済みの画像は保持されています。")


def natural_key(path: Path):
    return tuple((1, int(s)) if s.isdecimal() else (0, s.casefold())
                 for s in re.split(r"(\d+)", path.name))


def image_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        raise ValueError(f"画像フォルダが見つかりません: {folder}")
    files = sorted((p for p in folder.iterdir()
                    if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg"}),
                   key=lambda p: (natural_key(p), p.name))
    if not files:
        raise ValueError(f"PNG / JPEG画像がありません: {folder}")
    return files


def unique_folder(parent: Path, prefix: str) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    folder = parent / f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}"
    folder.mkdir()  # Never reuse or overwrite a previous job.
    return folder


def oriented_image(path: Path) -> Image.Image:
    try:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source)
            image.load()
            return image
    except Exception as exc:
        raise ValueError(f"画像を読み込めません: {path.name}\n{exc}") from exc


def selection_box(a, b, size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """Scene coordinates are original pixels; use floor/ceil, right/bottom exclusive."""
    w, h = size
    left = max(0, min(w, math.floor(min(a[0], b[0]))))
    top = max(0, min(h, math.floor(min(a[1], b[1]))))
    right = max(0, min(w, math.ceil(max(a[0], b[0]))))
    bottom = max(0, min(h, math.ceil(max(a[1], b[1]))))
    return (left, top, right, bottom) if right > left and bottom > top else None


def inspect_images(files, cancel, progress, expected_size=None):
    """Decode one file at a time before producing a crop or PDF."""
    if not files:
        raise ValueError("画像がありません。")
    for i, path in enumerate(files):
        check_cancel(cancel)
        try:
            with oriented_image(path) as image:
                if expected_size and image.size != expected_size:
                    raise ValueError(
                        f"画像サイズが異なります: {path.name} = {image.width}×{image.height}、"
                        f"基準 = {expected_size[0]}×{expected_size[1]}。一括処理を中止しました。")
        except Exception as exc:
            raise ValueError(f"{path.name}: {exc}") from exc
        progress(i + 1, len(files), f"画像を確認中: {path.name}")


def save_png(image, destination: Path):
    """Write privately then publish without replacing an existing image."""
    fd, name = tempfile.mkstemp(prefix=".writing_", suffix=".tmp", dir=destination.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            image.save(stream, format="PNG")
        if os.name == "nt":
            os.rename(tmp, destination)
        else:
            os.link(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)


def batch_crop(files, box, expected_size, output_parent, cancel, progress) -> Path:
    if not box or not (0 <= box[0] < box[2] <= expected_size[0]
                       and 0 <= box[1] < box[3] <= expected_size[1]):
        raise ValueError("有効なトリミング範囲を選択してください。")
    inspect_images(files, cancel, progress, expected_size)
    check_cancel(cancel)
    folder = unique_folder(output_parent, "cropped")
    progress(0, len(files), f"出力先: {folder}")
    for i, path in enumerate(files):
        check_cancel(cancel)
        try:
            with oriented_image(path) as image:
                # Validate again in case a file changed after the preflight.
                if image.size != expected_size:
                    raise ValueError("検査後に画像サイズが変わりました。")
                with image.crop(box) as cropped:
                    save_png(cropped, folder / f"page_{i+1:06d}.png")
        except Exception as exc:
            raise ValueError(f"{path.name} の保存に失敗: {exc}\n保存済み画像: {folder}") from exc
        progress(i + 1, len(files), f"トリミング: {path.name}")
    return folder


class PdfInput:
    """Read/compress one source at a time, keeping source files untouched."""
    def __init__(self, path, index, total, cancel, progress, quality="high"):
        self.path, self.index, self.total = path, index, total
        self.cancel, self.progress = cancel, progress
        self.quality = quality

    def read_bytes(self):
        check_cancel(self.cancel)
        self.progress(self.index, self.total, f"PDF変換: {self.path.name}")
        check_cancel(self.cancel)
        try:
            if self.quality != "high":
                limit, quality = PDF_QUALITY[self.quality]
                with oriented_image(self.path) as image:
                    image.thumbnail((limit, limit), Image.Resampling.LANCZOS)
                    check_cancel(self.cancel)
                    # JPEG has no alpha channel: composite transparency onto white.
                    rgba = image.convert("RGBA")
                    try:
                        with Image.new("RGB", rgba.size, "white") as rgb:
                            rgb.paste(rgba, mask=rgba.getchannel("A"))
                            with io.BytesIO() as buffer:
                                rgb.save(buffer, format="JPEG", quality=quality,
                                         subsampling=0, optimize=True)
                                check_cancel(self.cancel)
                                return buffer.getvalue()
                    finally:
                        rgba.close()
            with Image.open(self.path) as image:
                orientation = image.getexif().get(274, 1)
            if orientation not in (None, 1):
                with oriented_image(self.path) as image, io.BytesIO() as buffer:
                    if image.mode not in ("RGB", "RGBA", "L", "LA", "P", "1"):
                        image = image.convert("RGB")
                    image.save(buffer, format="PNG")
                    return buffer.getvalue()
            return self.path.read_bytes()
        except Cancelled:
            raise
        except Exception as exc:
            raise ValueError(f"PDF変換に失敗: {self.path.name}\n{exc}") from exc


class CancellableWriter:
    def __init__(self, stream, cancel):
        self.stream, self.cancel = stream, cancel

    def write(self, data):
        check_cancel(self.cancel)
        return self.stream.write(data)

    def tell(self):
        return self.stream.tell()


def make_pdf(files, output: Path, cancel, progress, overwrite=False, quality="high") -> Path:
    import img2pdf
    if quality not in PDF_QUALITY:
        raise ValueError("PDF画質は低・中・高から選んでください。")
    if output.suffix.lower() != ".pdf":
        raise ValueError("PDFファイル名の拡張子は .pdf にしてください。")
    if output.exists() and not overwrite:
        raise FileExistsError(f"同名PDFが存在します: {output}")
    inspect_images(files, cancel, progress)
    check_cancel(cancel)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".pdf_", suffix=".tmp", dir=output.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            inputs = [PdfInput(p, i, len(files), cancel, progress, quality) for i, p in enumerate(files)]
            # DPI sets physical page size. Resizing is limited to low/medium modes.
            img2pdf.convert(inputs, outputstream=CancellableWriter(stream, cancel),
                            engine=img2pdf.Engine.internal,
                            layout_fun=img2pdf.get_fixed_dpi_layout_fun((96, 96)),
                            rotation=img2pdf.Rotation.none)
        check_cancel(cancel)
        if overwrite:
            os.replace(tmp, output)
        elif os.name == "nt":
            os.rename(tmp, output)  # Windows rename refuses to replace existing files.
        else:
            os.link(tmp, output)  # Exclusive publication for Linux automated tests.
        progress(len(files), len(files), f"PDF作成完了: {output}")
        return output
    finally:
        tmp.unlink(missing_ok=True)


@dataclass(frozen=True)
class CaptureSettings:
    pages: int = 10
    delay: float = 1.5
    start_delay: float = 10
    direction: str = "right"


def capture_pages(settings, folder, cancel, progress, backend):
    if settings.pages < 1 or settings.delay < 0 or settings.start_delay < 0:
        raise ValueError("撮影枚数と待機時間が不正です。")
    if settings.direction not in {"right", "left"}:
        raise ValueError("ページ送りは右または左を選んでください。")
    # backend.wait checks stop and failsafe while waiting; easy to simulate in tests.
    for remaining in range(math.ceil(settings.start_delay), 0, -1):
        progress(0, settings.pages, f"開始まで {remaining} 秒。対象を最前面にしてください。")
        backend.wait(min(1, settings.start_delay - (math.ceil(settings.start_delay) - remaining)), cancel)
    check_cancel(cancel)
    target = backend.target()
    for i in range(settings.pages):
        check_cancel(cancel)
        backend.check_target(target)
        try:
            with backend.screenshot() as image:
                backend.check_target(target)
                check_cancel(cancel)
                save_png(image, folder / f"page_{i+1:06d}.png")
        except Cancelled:
            raise
        except Exception as exc:
            raise ValueError(f"{i+1}枚目の撮影・保存に失敗: {exc}") from exc
        progress(i + 1, settings.pages, f"撮影: {i+1} / {settings.pages}  保存先: {folder}")
        if i + 1 < settings.pages:
            check_cancel(cancel)
            backend.check_target(target)
            backend.turn_page(settings.direction)
            backend.wait(settings.delay, cancel)
    return folder
