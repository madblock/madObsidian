"""Tests for centralized INI configuration and CLI compatibility."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from madobsidian.config import (
    DEFAULT_CONFIG,
    ConfigError,
    load_config,
)
from madobsidian.cli import main


EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.ini"


class ConfigTests(unittest.TestCase):
    def test_example_configuration_loads(self) -> None:
        config = load_config(EXAMPLE)
        self.assertEqual(config.general.small_file_threshold_bytes, 256_000)
        self.assertEqual(config.images.max_dimension, 960)
        self.assertEqual(config.videos.video_codec, "libx264")
        self.assertTrue(config.watermark.enabled)
        self.assertEqual(config.watermark.font, "bundled")
        self.assertEqual(config.watermark.position, "bottom-left")

    def test_missing_config_uses_backward_compatible_defaults(self) -> None:
        self.assertIs(load_config(), DEFAULT_CONFIG)
        self.assertEqual(load_config().general.incoming_directory, "Incoming")
        self.assertEqual(load_config().watermark.font_path.name, "DejaVuSans.ttf")

    def _load_text(self, contents: str) -> tuple[object | None, Exception | None]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "config.ini"
        path.write_text(contents, encoding="utf-8")
        try:
            return load_config(path), None
        except Exception as error:  # exercise errors as public configuration behavior
            return None, error

    def test_valid_custom_values_and_relative_font_path(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / "settings.ini"
            font = Path(directory) / "fonts" / "DejaVuSans.ttf"
            font.parent.mkdir()
            font.write_bytes((root / "src/madobsidian/assets/DejaVuSans.ttf").read_bytes())
            config_file.write_text(
                "[general]\nincoming_directory = Media/In\nobjects_directory = Archive\n"
                "small_file_threshold_bytes = 1234\n[images]\nmax_dimension = 720\n"
                "quality = 77\nmethod = 4\n[videos]\nmax_dimension = 300\nvideo_crf = 31\n"
                "preset = fast\n[watermark]\nenabled = false\nfont = fonts/DejaVuSans.ttf\n"
                "font_size = 18\nopacity = 120\nposition = top-right\nmargin = 9\n",
                encoding="utf-8",
            )
            config = load_config(config_file)
        self.assertEqual(config.general.incoming_directory, "Media/In")
        self.assertEqual(config.general.small_file_threshold_bytes, 1234)
        self.assertEqual(config.images.quality, 77)
        self.assertEqual(config.videos.preset, "fast")
        self.assertFalse(config.watermark.enabled)
        self.assertEqual(config.watermark.font_path, font.resolve())

    def test_invalid_numeric_ranges_are_rejected_with_file_and_setting(self) -> None:
        for contents, setting in (
            ("[images]\nquality = 101\n", "quality"),
            ("[videos]\nmax_dimension = 301\n", "max_dimension"),
            ("[watermark]\nopacity = 256\n", "opacity"),
        ):
            _, error = self._load_text(contents)
            self.assertIsInstance(error, ConfigError)
            self.assertIn(setting, str(error))
            self.assertIn("config.ini", str(error))

    def test_invalid_boolean_is_rejected(self) -> None:
        _, error = self._load_text("[watermark]\nenabled = perhaps\n")
        self.assertIsInstance(error, ConfigError)
        self.assertIn("Boolean", str(error))

    def test_unsupported_formats_and_codecs_are_rejected(self) -> None:
        for contents, setting in (
            ("[images]\nformat = png\n", "format"),
            ("[videos]\nformat = avi\n", "format"),
            ("[videos]\nvideo_codec = hevc\n", "video_codec"),
            ("[videos]\naudio_codec = mp3\n", "audio_codec"),
        ):
            _, error = self._load_text(contents)
            self.assertIsInstance(error, ConfigError)
            self.assertIn(setting, str(error))

    def test_missing_and_invalid_font_paths_are_rejected(self) -> None:
        _, missing_error = self._load_text("[watermark]\nfont = missing.ttf\n")
        self.assertIsInstance(missing_error, ConfigError)
        self.assertIn("font", str(missing_error))
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.ini"
            (Path(directory) / "invalid.ttf").write_text("not a font", encoding="utf-8")
            config_path.write_text("[watermark]\nfont = invalid.ttf\n", encoding="utf-8")
            try:
                load_config(config_path)
            except ConfigError as error:
                invalid_error = error
            else:
                self.fail("invalid font data should be rejected")
            self.assertIn("cannot be loaded", str(invalid_error))

    def test_unknown_keys_are_not_ignored(self) -> None:
        _, error = self._load_text("[images]\nqualtiy = 80\n")
        self.assertIsInstance(error, ConfigError)
        self.assertIn("unknown key", str(error))

    def test_import_cli_accepts_config_and_legacy_syntax_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "item.txt"
            source.write_text("sample", encoding="utf-8")
            config_path = root / "custom.ini"
            config_path.write_text("[general]\nobjects_directory = archive-objects\n", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main(["import", str(source), str(root), "--config", str(config_path)])
            self.assertEqual(status, 0)
            self.assertTrue((root / "archive-objects").is_dir())
            self.assertIn("Imported 1 file(s)", output.getvalue())

    def test_vault_cli_config_option_is_forwarded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Incoming").mkdir()
            config_path = root / "config.ini"
            config_path.write_text("[general]\nincoming_directory = Incoming\n", encoding="utf-8")
            with mock.patch("madobsidian.cli.import_vault", return_value=[]) as importer:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["import", str(root), "--dry-run", "--config", str(config_path)]), 0)
            self.assertEqual(importer.call_args.kwargs["config"].config_path, config_path.resolve())


if __name__ == "__main__":
    unittest.main()
