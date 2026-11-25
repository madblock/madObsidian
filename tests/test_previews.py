"""Image preview tests using generated disposable files and archives."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image, ImageDraw

from test_importer import import_fixture
from madobsidian.previews import PREVIEW_THRESHOLD_BYTES, PreviewError, generate_preview


class PreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.archive = self.root / "archive"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _pad_to_size(self, path: Path, size: int) -> None:
        current = path.stat().st_size
        self.assertLessEqual(current, size)
        with path.open("ab") as stream:
            stream.write(b"\0" * (size - current))

    def _make_image(
        self,
        name: str = "picture.png",
        size: tuple[int, int] = (80, 60),
        mode: str = "RGB",
        image_format: str = "PNG",
        target_bytes: int | None = None,
    ) -> Path:
        path = self.root / name
        image = Image.new(mode, size, (40, 100, 180, 0) if mode == "RGBA" else (40, 100, 180))
        image.save(path, format=image_format)
        if target_bytes is not None:
            self._pad_to_size(path, target_bytes)
        return path

    def test_threshold_below_has_no_preview_and_boundary_gets_webp_at_correct_path(self) -> None:
        small = self._make_image("small.png")
        self.assertLess(small.stat().st_size, PREVIEW_THRESHOLD_BYTES)
        small_result = import_fixture(small, self.archive)[0]
        small_object = self.archive / "objects" / small_result["id"]
        self.assertFalse((small_object / "previews").exists())

        boundary = self._make_image("boundary.png", target_bytes=PREVIEW_THRESHOLD_BYTES)
        self.assertEqual(boundary.stat().st_size, 256_000)
        source_bytes = boundary.read_bytes()
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        result = import_fixture(boundary, self.archive)[0]
        object_dir = self.archive / "objects" / result["id"]
        preview_path = object_dir / "previews" / "preview.webp"
        self.assertTrue(preview_path.is_file())
        with Image.open(preview_path) as preview:
            self.assertEqual(preview.format, "WEBP")
        self.assertEqual(boundary.read_bytes(), source_bytes)
        self.assertEqual(hashlib.sha256(boundary.read_bytes()).hexdigest(), source_hash)
        self.assertEqual(hashlib.sha256((object_dir / "originals" / boundary.name).read_bytes()).hexdigest(), source_hash)

    def test_large_preview_respects_dimension_aspect_ratio_and_does_not_enlarge(self) -> None:
        wide = self._make_image("wide.png", size=(1920, 960), target_bytes=256_000)
        wide_result = import_fixture(wide, self.archive)[0]
        wide_preview = self.archive / "objects" / wide_result["id"] / "previews" / "preview.webp"
        with Image.open(wide_preview) as preview:
            self.assertEqual(preview.size, (960, 480))
            self.assertAlmostEqual(preview.width / preview.height, 1920 / 960)
            self.assertLessEqual(max(preview.size), 960)

        small = self._make_image("small-large-file.png", size=(80, 60), target_bytes=256_000)
        result = import_fixture(small, self.archive)[0]
        preview_path = self.archive / "objects" / result["id"] / "previews" / "preview.webp"
        with Image.open(preview_path) as preview:
            self.assertEqual(preview.size, (80, 60))

    def test_transparent_input_keeps_transparency_in_webp(self) -> None:
        image = Image.new("RGBA", (80, 60), (0, 0, 0, 0))
        image.putpixel((1, 1), (255, 0, 0, 255))
        source = self.root / "transparent.png"
        image.save(source, format="PNG")
        self._pad_to_size(source, PREVIEW_THRESHOLD_BYTES)
        result = import_fixture(source, self.archive)[0]
        preview_path = self.archive / "objects" / result["id"] / "previews" / "preview.webp"
        with Image.open(preview_path) as preview:
            preview.load()
            self.assertIn("A", preview.getbands())
            self.assertEqual(preview.getpixel((20, 20))[3], 0)
            self.assertGreater(preview.getpixel((1, 1))[3], 0)

    def test_animated_gif_preview_uses_first_frame_and_preserves_original(self) -> None:
        source = self.root / "two-frame.gif"
        first = Image.new("RGB", (80, 60), (255, 0, 0))
        second = Image.new("RGB", (80, 60), (0, 0, 255))
        first.save(
            source,
            format="GIF",
            save_all=True,
            append_images=[second],
            duration=100,
            loop=0,
            optimize=False,
        )
        self._pad_to_size(source, PREVIEW_THRESHOLD_BYTES)
        original_bytes = source.read_bytes()
        original_hash = hashlib.sha256(original_bytes).hexdigest()
        with Image.open(source) as animation:
            self.assertEqual(animation.n_frames, 2)
            animation.seek(0)
            first_pixel = animation.convert("RGB").getpixel((10, 10))
            animation.seek(1)
            second_pixel = animation.convert("RGB").getpixel((10, 10))
            self.assertNotEqual(first_pixel, second_pixel)

        result = import_fixture(source, self.archive)[0]
        object_dir = self.archive / "objects" / result["id"]
        preview_path = object_dir / "previews" / "preview.webp"
        with Image.open(preview_path) as preview:
            self.assertEqual(preview.format, "WEBP")
            self.assertEqual(preview.n_frames, 1)
            preview.load()
            preview_pixel = preview.convert("RGB").getpixel((10, 10))
            self.assertGreater(preview_pixel[0], preview_pixel[2] * 2)
        self.assertEqual(source.read_bytes(), original_bytes)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original_hash)
        self.assertEqual((object_dir / "originals" / source.name).read_bytes(), original_bytes)

    def test_exif_orientation_and_watermark_include_cyrillic_metadata(self) -> None:
        source = self.root / "Фото-кот.jpg"
        image = Image.new("RGB", (300, 600), (30, 120, 200))
        exif = image.getexif()
        exif[274] = 6
        image.save(source, format="JPEG", exif=exif)
        self._pad_to_size(source, PREVIEW_THRESHOLD_BYTES)
        before = hashlib.sha256(source.read_bytes()).hexdigest()

        drawn_text: list[str] = []
        original_text = ImageDraw.ImageDraw.text

        def capture_text(draw, xy, text, *args, **kwargs):
            drawn_text.append(str(text))
            return original_text(draw, xy, text, *args, **kwargs)

        with mock.patch.object(ImageDraw.ImageDraw, "text", capture_text):
            result = import_fixture(source, self.archive)[0]
        object_dir = self.archive / "objects" / result["id"]
        preview_path = object_dir / "previews" / "preview.webp"
        with Image.open(preview_path) as preview:
            self.assertEqual(preview.size, (600, 300))
            preview.load()
        watermark = "".join(drawn_text)
        self.assertIn("Фото-кот.jpg", watermark)
        self.assertIn("0.3 MB", watermark)
        self.assertIn("600 × 300", watermark)
        self.assertNotIn("available", watermark.casefold())
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)
        self.assertEqual((object_dir / "originals" / source.name).read_bytes(), source.read_bytes())

    def test_non_image_file_at_threshold_has_no_preview(self) -> None:
        source = self.root / "large.bin"
        source.write_bytes(b"not an image" + b"\0" * (PREVIEW_THRESHOLD_BYTES - len(b"not an image")))
        batch = import_fixture(source, self.archive)
        self.assertEqual(len(batch), 0)
        self.assertEqual(batch.skipped[0]["reason"], "unsupported media extension")
        self.assertFalse((self.archive / "objects").exists())

    def test_corrupt_registered_image_is_not_treated_as_non_image(self) -> None:
        source = self.root / "corrupt.jpg"
        source.write_bytes(b"not a JPEG" + b"\0" * (PREVIEW_THRESHOLD_BYTES - len(b"not a JPEG")))
        with self.assertRaises(PreviewError):
            generate_preview(source, self.root / "previews", source.name, source.stat().st_size)

    def test_preview_failure_leaves_no_published_or_incomplete_object(self) -> None:
        source = self._make_image("failure.png", target_bytes=PREVIEW_THRESHOLD_BYTES)
        source_bytes = source.read_bytes()
        with mock.patch("PIL.Image.Image.save", side_effect=OSError("simulated WebP failure")):
            batch = import_fixture(source, self.archive)
            self.assertEqual(len(batch.failures), 1)
            self.assertFalse(batch.failures[0]["serious"])
        objects = self.archive / "objects"
        self.assertEqual(list(objects.iterdir()), [])
        self.assertEqual(source.read_bytes(), source_bytes)

    def test_bundled_font_and_license_are_present(self) -> None:
        assets = Path(__file__).parents[1] / "src" / "madobsidian" / "assets"
        self.assertTrue((assets / "DejaVuSans.ttf").is_file())
        license_text = (assets / "DEJAVU-LICENSE.txt").read_text(encoding="utf-8")
        self.assertIn("Bitstream Vera Fonts Copyright", license_text)
        self.assertIn("DejaVu changes are in public domain", license_text)


if __name__ == "__main__":
    unittest.main()
