"""Tests for centralized INI configuration and CLI compatibility."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from madobsidian.archive import rebuild_index, scan_archive
from madobsidian.config import (
    DEFAULT_CONFIG,
    ConfigError,
    ToolsConfig,
    load_config,
)
from madobsidian.cli import main
from madobsidian.importer import ImportBatchResult
from madobsidian.video_previews import VideoInfo, VideoPreviewError, _executable, generate_video_preview
from fractions import Fraction


EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.ini"


def _write_executable(path: Path) -> None:
    path.write_bytes(b"#!/bin/sh\nexit 0\n")
    if os.name == "posix":
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


class ConfigTests(unittest.TestCase):
    def test_example_configuration_loads(self) -> None:
        config = load_config(EXAMPLE)
        self.assertEqual(config.general.small_file_threshold_bytes, 256_000)
        self.assertFalse(config.general.allow_unrelated_syncthing_conflicts)
        self.assertEqual(config.images.max_dimension, 960)
        self.assertEqual(config.videos.video_codec, "libx264")
        self.assertTrue(config.watermark.enabled)
        self.assertEqual(config.watermark.font, "bundled")
        self.assertEqual(config.watermark.position, "bottom-left")
        self.assertEqual(config.tools.ffmpeg, "auto")
        self.assertEqual(config.tools.ffprobe, "auto")

    def test_missing_config_uses_backward_compatible_defaults(self) -> None:
        self.assertIs(load_config(), DEFAULT_CONFIG)
        self.assertEqual(load_config().general.incoming_directory, "Incoming")
        self.assertFalse(load_config().general.allow_unrelated_syncthing_conflicts)
        self.assertEqual(load_config().watermark.font_path.name, "DejaVuSans.ttf")
        self.assertEqual(load_config().tools, ToolsConfig(ffmpeg="auto", ffprobe="auto"))

    def _load_text(self, contents: str) -> tuple[object | None, Exception | None]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "config.ini"
        path.write_text(contents, encoding="utf-8")
        try:
            return load_config(path), None
        except Exception as error:  # exercise errors as public configuration behavior
            return None, error

    def test_unrelated_syncthing_conflicts_uses_general_boolean_validation(self) -> None:
        for value, expected in (("on", True), ("no", False)):
            with self.subTest(value=value):
                config, error = self._load_text(f"[general]\nallow_unrelated_syncthing_conflicts = {value}\n")
                self.assertIsNone(error)
                self.assertIs(config.general.allow_unrelated_syncthing_conflicts, expected)
        _, error = self._load_text("[general]\nallow_unrelated_syncthing_conflicts = sometimes\n")
        self.assertIsInstance(error, ConfigError)
        self.assertIn("[general] allow_unrelated_syncthing_conflicts", str(error))

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

    def test_import_cli_accepts_configured_objects_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Incoming").mkdir()
            (root / "note.md").write_text("[[item.png]]", encoding="utf-8")
            source = root / "Incoming" / "item.png"
            source.write_text("sample", encoding="utf-8")
            config_path = root / "custom.ini"
            config_path.write_text("[general]\nobjects_directory = archive-objects\n", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = main(["import", str(root), "--config", str(config_path)])
            self.assertEqual(status, 0)
            self.assertTrue((root / "archive-objects").is_dir())
            self.assertIn("Imported 1 file(s)", output.getvalue())

    def test_vault_cli_config_option_is_forwarded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Incoming").mkdir()
            config_path = root / "config.ini"
            config_path.write_text("[general]\nincoming_directory = Incoming\n", encoding="utf-8")
            with mock.patch("madobsidian.cli.import_vault", return_value=ImportBatchResult()) as importer:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["import", str(root), "--dry-run", "--config", str(config_path)]), 0)
            self.assertEqual(importer.call_args.kwargs["config"].config_path, config_path.resolve())

    def test_watermark_disabled_allows_nonexistent_custom_font(self) -> None:
        with mock.patch("madobsidian.config.ImageFont.truetype", side_effect=AssertionError("should not load font")):
            config, error = self._load_text("[watermark]\nenabled = false\nfont = missing-font.ttf\n")
        self.assertIsNone(error)
        assert config is not None
        self.assertFalse(config.watermark.enabled)
        self.assertEqual(config.watermark.font, "missing-font.ttf")

    def test_watermark_disabled_allows_invalid_custom_font_without_pillow_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.ini"
            (Path(directory) / "corrupt.ttf").write_text("not a valid font", encoding="utf-8")
            config_path.write_text("[watermark]\nenabled = false\nfont = corrupt.ttf\n", encoding="utf-8")
            with mock.patch("madobsidian.config.ImageFont.truetype", side_effect=AssertionError("should not load font")):
                config = load_config(config_path)
        self.assertFalse(config.watermark.enabled)
        self.assertEqual(config.watermark.font_path, (Path(directory) / "corrupt.ttf").resolve())

    def test_watermark_enabled_still_rejects_missing_and_invalid_custom_font(self) -> None:
        _, missing_error = self._load_text("[watermark]\nenabled = true\nfont = missing.ttf\n")
        self.assertIsInstance(missing_error, ConfigError)
        self.assertIn("font file does not exist", str(missing_error))
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.ini"
            (Path(directory) / "bad.ttf").write_text("not a font", encoding="utf-8")
            config_path.write_text("[watermark]\nenabled = true\nfont = bad.ttf\n", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "cannot be loaded"):
                load_config(config_path)

    def test_missing_tools_section_retains_backward_compatibility(self) -> None:
        with mock.patch("madobsidian.config.shutil.which", return_value=None):
            config, error = self._load_text("[general]\nincoming_directory = Incoming\n")
        self.assertIsNone(error)
        assert config is not None
        self.assertEqual(config.tools, ToolsConfig(ffmpeg="auto", ffprobe="auto"))

    def test_tools_auto_is_lazy_during_load_config_and_fails_when_needed(self) -> None:
        with mock.patch("madobsidian.config.shutil.which", return_value=None):
            config, error = self._load_text("[tools]\nffmpeg = auto\nffprobe = auto\n")
        self.assertIsNone(error)
        assert config is not None
        self.assertEqual(config.tools, ToolsConfig(ffmpeg="auto", ffprobe="auto"))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MADOBSIDIAN_FFMPEG", None)
            os.environ.pop("MADOBSIDIAN_FFPROBE", None)
            with mock.patch("madobsidian.video_previews.shutil.which", return_value=None):
                with self.assertRaisesRegex(VideoPreviewError, r"Cannot find ffmpeg.*\[tools\] ffmpeg"):
                    _executable("MADOBSIDIAN_FFMPEG", "ffmpeg", config.tools.ffmpeg)
                with self.assertRaisesRegex(VideoPreviewError, r"Cannot find ffprobe.*\[tools\] ffprobe"):
                    _executable("MADOBSIDIAN_FFPROBE", "ffprobe", config.tools.ffprobe)

    def test_tools_absolute_and_relative_paths_resolve_from_config_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            cfg_dir = base / "cfg"
            bin_dir = cfg_dir / "bin"
            other_dir = base / "abs"
            bin_dir.mkdir(parents=True)
            other_dir.mkdir(parents=True)
            rel_ffmpeg = bin_dir / "ffmpeg.exe"
            abs_ffprobe = other_dir / "ffprobe.exe"
            _write_executable(rel_ffmpeg)
            _write_executable(abs_ffprobe)
            config_path = cfg_dir / "config.ini"
            config_path.write_text(
                f"[tools]\nffmpeg = bin/ffmpeg.exe\nffprobe = {abs_ffprobe}\n",
                encoding="utf-8",
            )
            config = load_config(config_path)
        self.assertEqual(config.tools.ffmpeg, str(rel_ffmpeg.resolve()))
        self.assertEqual(config.tools.ffprobe, str(abs_ffprobe.resolve()))

    def test_tools_bare_executable_name_uses_which_not_config_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            # Decoy file inside config directory must NOT be treated as relative path for a bare name.
            _write_executable(base / "my-ffmpeg")
            path_tool = base / "on-path" / "my-ffmpeg.exe"
            path_tool.parent.mkdir()
            _write_executable(path_tool)
            config_path = base / "config.ini"
            config_path.write_text("[tools]\nffmpeg = my-ffmpeg\n", encoding="utf-8")
            with mock.patch("madobsidian.config.shutil.which", return_value=str(path_tool)) as which_mock:
                config = load_config(config_path)
            which_mock.assert_called_once_with("my-ffmpeg")
            self.assertEqual(config.tools.ffmpeg, str(path_tool))

            with mock.patch("madobsidian.config.shutil.which", return_value=None):
                with self.assertRaisesRegex(ConfigError, r"\[tools\] ffmpeg.*not found on PATH"):
                    load_config(config_path)

    def test_tools_invalid_explicit_paths_fail_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config_path = base / "config.ini"
            config_path.write_text("[tools]\nffmpeg = bin/missing-ffmpeg.exe\n", encoding="utf-8")
            with mock.patch("madobsidian.config.shutil.which", return_value=None):
                with self.assertRaisesRegex(ConfigError, r"\[tools\] ffmpeg.*does not exist"):
                    load_config(config_path)

            # Explicit invalid bare name must not fall back to default "ffmpeg" on PATH.
            config_path.write_text("[tools]\nffmpeg = nonexistent-custom-ffmpeg\n", encoding="utf-8")
            with mock.patch(
                "madobsidian.config.shutil.which",
                side_effect=lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None,
            ):
                with self.assertRaisesRegex(ConfigError, r"\[tools\] ffmpeg.*not found on PATH"):
                    load_config(config_path)

    def test_tools_posix_non_executable_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            tool = base / "bin" / "ffmpeg"
            tool.parent.mkdir()
            tool.write_bytes(b"#!/bin/sh\n")
            config_path = base / "config.ini"
            config_path.write_text("[tools]\nffmpeg = bin/ffmpeg\n", encoding="utf-8")
            with contextlib.ExitStack() as stack:
                if os.name != "posix":
                    stack.enter_context(mock.patch("madobsidian.config._is_posix", return_value=True))
                    stack.enter_context(mock.patch("madobsidian.config.os.access", return_value=False))
                with self.assertRaisesRegex(ConfigError, r"\[tools\] ffmpeg.*not executable"):
                    load_config(config_path)

    def test_tools_precedence_env_over_config_over_path_and_independent_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            env_ffmpeg = base / "env" / "ffmpeg.exe"
            cfg_ffmpeg = base / "cfg" / "ffmpeg.exe"
            cfg_ffprobe = base / "cfg" / "ffprobe.exe"
            path_ffprobe = base / "path" / "ffprobe.exe"
            for p in (env_ffmpeg, cfg_ffmpeg, cfg_ffprobe, path_ffprobe):
                p.parent.mkdir(parents=True, exist_ok=True)
                _write_executable(p)

            tools = ToolsConfig(ffmpeg=str(cfg_ffmpeg), ffprobe=str(cfg_ffprobe))
            # 1. Env var overrides [tools] for ffmpeg while ffprobe uses [tools].
            with mock.patch.dict(os.environ, {"MADOBSIDIAN_FFMPEG": str(env_ffmpeg)}, clear=False):
                os.environ.pop("MADOBSIDIAN_FFPROBE", None)
                self.assertEqual(_executable("MADOBSIDIAN_FFMPEG", "ffmpeg", tools.ffmpeg), str(env_ffmpeg.resolve()))
                self.assertEqual(_executable("MADOBSIDIAN_FFPROBE", "ffprobe", tools.ffprobe), str(cfg_ffprobe.resolve()))

            # 2. [tools] overrides PATH discovery; auto falls through to PATH independently.
            mixed = ToolsConfig(ffmpeg=str(cfg_ffmpeg), ffprobe="auto")
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("MADOBSIDIAN_FFMPEG", None)
                os.environ.pop("MADOBSIDIAN_FFPROBE", None)
                with mock.patch(
                    "madobsidian.video_previews.shutil.which",
                    side_effect=lambda name: str(path_ffprobe) if name == "ffprobe" else str(env_ffmpeg),
                ):
                    self.assertEqual(_executable("MADOBSIDIAN_FFMPEG", "ffmpeg", mixed.ffmpeg), str(cfg_ffmpeg.resolve()))
                    self.assertEqual(_executable("MADOBSIDIAN_FFPROBE", "ffprobe", mixed.ffprobe), str(path_ffprobe))

            # 3. Invalid env var fails explicitly without falling back to valid [tools] or PATH.
            with mock.patch.dict(os.environ, {"MADOBSIDIAN_FFMPEG": str(base / "no-such-ffmpeg.exe")}, clear=False):
                with mock.patch("madobsidian.config.shutil.which", return_value=None):
                    with self.assertRaisesRegex(VideoPreviewError, "MADOBSIDIAN_FFMPEG does not name an executable"):
                        _executable("MADOBSIDIAN_FFMPEG", "ffmpeg", tools.ffmpeg)

            # 4. Bare env var name uses shutil.which() and fails without falling back to [tools].
            with mock.patch.dict(os.environ, {"MADOBSIDIAN_FFMPEG": "custom-env-ffmpeg"}, clear=False):
                with mock.patch("madobsidian.config.shutil.which", return_value=str(env_ffmpeg)) as which_mock:
                    self.assertEqual(_executable("MADOBSIDIAN_FFMPEG", "ffmpeg", tools.ffmpeg), str(env_ffmpeg))
                which_mock.assert_called_once_with("custom-env-ffmpeg")
                with mock.patch("madobsidian.config.shutil.which", return_value=None):
                    with self.assertRaisesRegex(VideoPreviewError, "MADOBSIDIAN_FFMPEG does not name an executable"):
                        _executable("MADOBSIDIAN_FFMPEG", "ffmpeg", tools.ffmpeg)

            # 5. Empty explicit [tools] value in config.ini is rejected.
            _, empty_error = self._load_text("[tools]\nffmpeg =\n")
            self.assertIsInstance(empty_error, ConfigError)
            self.assertIn("ffmpeg", str(empty_error))

    def test_tools_configuration_used_consistently_across_import_verify_and_rebuild_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            bin_dir = base / "tools"
            bin_dir.mkdir()
            custom_ffmpeg = bin_dir / "custom-ffmpeg.exe"
            custom_ffprobe = bin_dir / "custom-ffprobe.exe"
            _write_executable(custom_ffmpeg)
            _write_executable(custom_ffprobe)
            config_path = base / "config.ini"
            config_path.write_text(
                "[tools]\nffmpeg = tools/custom-ffmpeg.exe\nffprobe = tools/custom-ffprobe.exe\n",
                encoding="utf-8",
            )
            config = load_config(config_path)

            # Prepare an archive with one video object and preview.mp4.
            archive = base / "archive"
            identifier = str(uuid.uuid4())
            obj_dir = archive / "objects" / identifier
            (obj_dir / "originals").mkdir(parents=True)
            (obj_dir / "previews").mkdir(parents=True)
            original = obj_dir / "originals" / "clip.mp4"
            content = b"\0" * 256_000
            original.write_bytes(content)
            (obj_dir / "previews" / "preview.mp4").write_bytes(b"preview-bytes")
            metadata = {
                "id": identifier,
                "original_filename": "clip.mp4",
                "original_path": "originals/clip.mp4",
                "extension": ".mp4",
                "mime_type": "video/mp4",
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "imported_at": datetime.now(timezone.utc).isoformat(),
            }
            (obj_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

            fake_info = VideoInfo(
                streams=({"codec_type": "video", "codec_name": "h264"},),
                video={"codec_type": "video", "codec_name": "h264"},
                audio=None,
                video_time_base=Fraction(1, 30),
                presentation_duration=Fraction(1, 1),
                display_width=320,
                display_height=240,
            )

            seen_import_ffprobe: list[str] = []
            seen_verify_ffprobe: list[str] = []
            seen_verify_ffmpeg: list[str] = []

            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("MADOBSIDIAN_FFMPEG", None)
                os.environ.pop("MADOBSIDIAN_FFPROBE", None)

                # 1. Import / video preview generation path
                def capture_import_probe(ffprobe_bin, _path):
                    seen_import_ffprobe.append(ffprobe_bin)
                    return None  # triggers early VideoPreviewError after executable resolution

                with mock.patch("madobsidian.video_previews._inspect_video", side_effect=capture_import_probe):
                    with self.assertRaises(VideoPreviewError):
                        generate_video_preview(original, obj_dir / "previews", "clip.mp4", len(content), config=config)
                self.assertEqual(seen_import_ffprobe, [str(custom_ffprobe.resolve())])

                # 2. Verify and rebuild-index CLI paths
                def capture_archive_probe(ffprobe_bin, _path):
                    seen_verify_ffprobe.append(ffprobe_bin)
                    return fake_info

                def capture_archive_decode(command, *args, **kwargs):
                    seen_verify_ffmpeg.append(command[0])
                    return subprocess.CompletedProcess(command, 0, "", "")

                with mock.patch("madobsidian.archive._inspect_video", side_effect=capture_archive_probe), mock.patch(
                    "madobsidian.archive.subprocess.run", side_effect=capture_archive_decode
                ):
                    with contextlib.redirect_stdout(io.StringIO()):
                        verify_rc = main(["verify", str(archive), "--config", str(config_path)])
                        rebuild_rc = main(["rebuild-index", str(archive), "--config", str(config_path)])
                self.assertEqual(verify_rc, 0)
                self.assertEqual(rebuild_rc, 0)
                self.assertEqual(seen_verify_ffprobe, [str(custom_ffprobe.resolve()), str(custom_ffprobe.resolve())])
                self.assertEqual(seen_verify_ffmpeg, [str(custom_ffmpeg.resolve()), str(custom_ffmpeg.resolve())])
                self.assertTrue((archive / "index.json").is_file())


if __name__ == "__main__":
    unittest.main()
