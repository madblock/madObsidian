"""Real FFmpeg video-preview integration tests using temporary media only."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from unittest import mock

from PIL import ImageDraw

from madobsidian.importer import import_path
from madobsidian.video_previews import (
    VIDEO_PREVIEW_THRESHOLD_BYTES,
    VideoPreviewError,
    _inspect_video,
    _probe_timeline,
)


class VideoPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ffmpeg = os.environ.get("MADOBSIDIAN_FFMPEG") or shutil.which("ffmpeg")
        cls.ffprobe = os.environ.get("MADOBSIDIAN_FFPROBE") or shutil.which("ffprobe")
        if not cls.ffmpeg:
            raise unittest.SkipTest("FFmpeg is unavailable; set MADOBSIDIAN_FFMPEG or add ffmpeg to PATH")
        if not cls.ffprobe:
            raise unittest.SkipTest("ffprobe is unavailable; set MADOBSIDIAN_FFPROBE or add ffprobe to PATH")
        encoders = subprocess.run(
            [cls.ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True, check=False
        )
        if encoders.returncode or "libx264" not in encoders.stdout:
            raise unittest.SkipTest("Installed FFmpeg does not provide the libx264 encoder")

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.archive = self.root / "archive"
        self.tool_env = mock.patch.dict(
            os.environ,
            {"MADOBSIDIAN_FFMPEG": str(self.ffmpeg), "MADOBSIDIAN_FFPROBE": str(self.ffprobe)},
        )
        self.tool_env.start()
        self.addCleanup(self.tool_env.stop)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run_ffmpeg(self, arguments: list[str]) -> None:
        result = subprocess.run(
            [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2500:])


    def _make_cfr(self, name: str, *, audio: bool, delayed_audio: bool = False, duration: int = 2,) -> Path:
        source = self.root / name
        arguments = ["-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=10:duration={duration}"]

        if audio:
            if delayed_audio:
                arguments.extend(["-itsoffset", "0.5"])
            arguments.extend([
                "-f", "lavfi",
                "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}",
            ])

        arguments.extend(["-map", "0:v:0"])
        if audio:
            arguments.extend(["-map", "1:a:0"])

        arguments.extend([
            "-c:v", "libx264",
            "-preset", "medium",
            "-bf", "3",
            "-g", "50",
            "-crf", "32",
            "-pix_fmt", "yuv420p",
        ])

        if audio:
            arguments.extend(["-c:a", "aac", "-shortest"])

        arguments.append(str(source))
        self._run_ffmpeg(arguments)
        return source


    def _make_vfr(self, name: str = "irregular-vfr.mp4") -> Path:
        source = self.root / name
        arguments = [
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=10:duration=1.2",
            "-vf", "setpts='if(lt(N,6),N*0.1/TB,0.6/TB+(N-6)*0.2/TB)'",
            "-fps_mode:v", "passthrough", "-enc_time_base:v", "1:1000",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32", "-pix_fmt", "yuv420p",
            str(source),
        ]
        self._run_ffmpeg(arguments)
        return source

    def _pad_mp4_to(self, path: Path, target_size: int) -> None:
        current_size = path.stat().st_size
        free_box_size = target_size - current_size
        self.assertGreaterEqual(free_box_size, 8, "generated fixture is too large to pad to threshold")
        with path.open("ab") as stream:
            stream.write(struct.pack(">I", free_box_size))
            stream.write(b"free")
            stream.write(b"\0" * (free_box_size - 8))
        self.assertEqual(path.stat().st_size, target_size)

    def _probe(self, path: Path) -> dict:
        result = subprocess.run(
            [self.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2500:])
        return json.loads(result.stdout)

    def _object_preview(self, result, name: str = "preview.mp4") -> Path:
        return self.archive / "objects" / result.metadata.id / "previews" / name

    def test_cfr_video_with_audio_generates_full_h264_aac_watermarked_preview(self) -> None:
        source = self._make_cfr("Клип-cfr.mp4", audio=True)
        source_streams = self._probe(source)["streams"]
        source_video = next(s for s in source_streams if s["codec_type"] == "video")
        self.assertGreater(source_video["has_b_frames"], 0)
        self._pad_mp4_to(source, VIDEO_PREVIEW_THRESHOLD_BYTES)
        original_bytes = source.read_bytes()
        original_hash = hashlib.sha256(original_bytes).hexdigest()
        rendered_text: list[str] = []
        original_draw_text = ImageDraw.ImageDraw.text

        def capture_text(draw, xy, text, *args, **kwargs):
            rendered_text.append(str(text))
            return original_draw_text(draw, xy, text, *args, **kwargs)

        with mock.patch.object(ImageDraw.ImageDraw, "text", capture_text):
            result = import_path(source, self.archive)[0]
        object_dir = self.archive / "objects" / result.metadata.id
        preview = self._object_preview(result)
        self.assertTrue(preview.is_file())
        streams = self._probe(preview)["streams"]
        self.assertEqual(streams[0]["codec_name"], "h264")
        self.assertEqual(streams[0]["codec_type"], "video")
        self.assertLessEqual(max(streams[0]["width"], streams[0]["height"]), 400)
        self.assertEqual(next(s for s in streams if s["codec_type"] == "audio")["codec_name"], "aac")
        text = "".join(rendered_text)
        self.assertIn("Клип-cfr.mp4", text)
        self.assertIn("0.3 MB", text)
        self.assertIn("640×360", text)
        self.assertIn("00:00:02.00", text)
        self.assertEqual(source.read_bytes(), original_bytes)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original_hash)
        self.assertEqual((object_dir / source.name).read_bytes(), original_bytes)

    def test_silent_cfr_video_generates_preview_without_audio(self) -> None:
        source = self._make_cfr("silent.mp4", audio=False)
        self._pad_mp4_to(source, VIDEO_PREVIEW_THRESHOLD_BYTES)
        result = import_path(source, self.archive)[0]
        streams = self._probe(self._object_preview(result))["streams"]
        self.assertEqual([stream["codec_type"] for stream in streams], ["video"])

    def test_vfr_preview_preserves_actual_output_intervals_and_marks_average_fps(self) -> None:
        source = self._make_vfr()
        self._pad_mp4_to(source, VIDEO_PREVIEW_THRESHOLD_BYTES)
        original = _inspect_video(self.ffprobe, source)
        self.assertIsNotNone(original)
        source_timeline = _probe_timeline(self.ffprobe, source, "v:0", original.video_time_base)
        self.assertTrue(source_timeline.variable_frame_rate)
        self.assertGreater(len(set(source_timeline.intervals)), 1)

        rendered_text: list[str] = []
        original_draw_text = ImageDraw.ImageDraw.text

        def capture_text(draw, xy, text, *args, **kwargs):
            rendered_text.append(str(text))
            return original_draw_text(draw, xy, text, *args, **kwargs)

        with mock.patch.object(ImageDraw.ImageDraw, "text", capture_text):
            result = import_path(source, self.archive)[0]
        output = self._object_preview(result)
        output_info = _inspect_video(self.ffprobe, output)
        output_timeline = _probe_timeline(self.ffprobe, output, "v:0", _probe_fraction(output_info.video["time_base"]))
        self.assertEqual(source_timeline.frame_count, output_timeline.frame_count)
        tolerance = max(Fraction(1, 1000), original.video_time_base + _probe_fraction(output_info.video["time_base"]))
        for source_interval, output_interval in zip(source_timeline.intervals, output_timeline.intervals):
            self.assertLessEqual(abs(source_interval - output_interval), tolerance)
        self.assertIn("FPS avg", " ".join(rendered_text))



    def test_delayed_audio_start_offset_and_duration_are_preserved(self) -> None:
        source = self._make_cfr("delayed-audio.mp4", audio=True, delayed_audio=True)
        self._pad_mp4_to(source, VIDEO_PREVIEW_THRESHOLD_BYTES)
        source_probe = self._probe(source)
        source_video = next(s for s in source_probe["streams"] if s["codec_type"] == "video")
        source_audio = next(s for s in source_probe["streams"] if s["codec_type"] == "audio")
        source_video_timeline = _probe_timeline(
            self.ffprobe, source, "v:0", _probe_fraction(source_video["time_base"])
        )
        source_audio_timeline = _probe_timeline(
            self.ffprobe, source, "a:0", _probe_fraction(source_audio["time_base"])
        )
        source_offset = source_audio_timeline.timestamps[0] - source_video_timeline.timestamps[0]

        result = import_path(source, self.archive)[0]
        output = self._object_preview(result)
        output_probe = self._probe(output)
        output_video = next(s for s in output_probe["streams"] if s["codec_type"] == "video")
        output_audio = next(s for s in output_probe["streams"] if s["codec_type"] == "audio")
        output_video_timeline = _probe_timeline(
            self.ffprobe, output, "v:0", _probe_fraction(output_video["time_base"])
        )
        output_audio_timeline = _probe_timeline(
            self.ffprobe, output, "a:0", _probe_fraction(output_audio["time_base"])
        )
        output_offset = output_audio_timeline.timestamps[0] - output_video_timeline.timestamps[0]
        output_offset_tolerance = max(
            Fraction(1024, int(output_audio.get("sample_rate", 48000))),
            _probe_fraction(source_video["time_base"])
            + _probe_fraction(source_audio["time_base"])
            + _probe_fraction(output_video["time_base"])
            + _probe_fraction(output_audio["time_base"]),
        )
        self.assertGreater(source_offset, Fraction(2, 5))
        self.assertLessEqual(abs(source_offset - output_offset), output_offset_tolerance)
        source_video_duration = _stream_duration_for_test(source_video)
        output_video_duration = _stream_duration_for_test(output_video)
        self.assertLessEqual(abs(source_video_duration - output_video_duration), Fraction(1, 20))
        source_container_duration = _probe_fraction(source_probe["format"]["duration"])
        output_container_duration = _probe_fraction(output_probe["format"]["duration"])
        self.assertLessEqual(abs(source_container_duration - output_container_duration), Fraction(1, 20))

    def test_below_threshold_video_has_no_preview(self) -> None:
        source = self._make_cfr("small.mp4", audio=False)
        self.assertLess(source.stat().st_size, VIDEO_PREVIEW_THRESHOLD_BYTES)
        result = import_path(source, self.archive)[0]
        self.assertFalse((self.archive / "objects" / result.metadata.id / "previews").exists())

    def test_exact_threshold_video_gets_preview(self) -> None:
        source = self._make_cfr("threshold.mp4", audio=False)
        self._pad_mp4_to(source, VIDEO_PREVIEW_THRESHOLD_BYTES)
        self.assertEqual(source.stat().st_size, 256_000)
        result = import_path(source, self.archive)[0]
        self.assertTrue(self._object_preview(result).is_file())

    def test_large_nonvideo_input_has_no_video_preview(self) -> None:
        source = self.root / "not-video.bin"
        source.write_bytes(b"not a video" + b"\0" * (VIDEO_PREVIEW_THRESHOLD_BYTES - len(b"not a video")))
        result = import_path(source, self.archive)[0]
        self.assertFalse((self.archive / "objects" / result.metadata.id / "previews").exists())

    def test_ffprobe_failure_aborts_import_and_cleans_temporary_object(self) -> None:
        source = self._make_cfr("probe-failure.mp4", audio=False)
        self._pad_mp4_to(source, VIDEO_PREVIEW_THRESHOLD_BYTES)
        with mock.patch.dict(os.environ, {"MADOBSIDIAN_FFPROBE": str(self.root / "missing-ffprobe.exe")}):
            with self.assertRaises(VideoPreviewError):
                import_path(source, self.archive)
        self.assertEqual(list((self.archive / "objects").iterdir()), [])

    def test_ffmpeg_failure_aborts_import_and_cleans_temporary_object(self) -> None:
        source = self._make_cfr("encode-failure.mp4", audio=False)
        self._pad_mp4_to(source, VIDEO_PREVIEW_THRESHOLD_BYTES)
        actual_run = subprocess.run

        def fail_ffmpeg(command, *args, **kwargs):
            if Path(command[0]) == Path(self.ffmpeg):
                return subprocess.CompletedProcess(command, 1, "", "simulated FFmpeg failure")
            return actual_run(command, *args, **kwargs)

        with mock.patch("madobsidian.video_previews.subprocess.run", side_effect=fail_ffmpeg):
            with self.assertRaises(VideoPreviewError):
                import_path(source, self.archive)
        self.assertEqual(list((self.archive / "objects").iterdir()), [])

    def test_rotated_video_preview_uses_display_dimensions(self) -> None:
        source = self._make_cfr("rotated-video.mp4", audio=True, duration=10)

        rotated = self.root / "rotated-video-tagged.mp4"
        self._run_ffmpeg([
            "-display_rotation:v:0", "90",
            "-i", str(source),
            "-c", "copy",
            str(rotated),
        ])

        source_info = self._probe(rotated)
        video_stream = next(
            stream for stream in source_info["streams"]
            if stream["codec_type"] == "video"
        )


        self.assertGreaterEqual(
            rotated.stat().st_size,
            VIDEO_PREVIEW_THRESHOLD_BYTES,
        )
        original_bytes = rotated.read_bytes()
        source.unlink()

        captured_text: list[str] = []
        original_draw_text = ImageDraw.ImageDraw.text

        def capture_text(draw, xy, text, *args, **kwargs):
            captured_text.append(str(text))
            return original_draw_text(draw, xy, text, *args, **kwargs)

        with mock.patch.object(ImageDraw.ImageDraw, "text", capture_text):
            result = import_path(rotated, self.archive)[0]


        output = self._object_preview(result)
        output_info = _inspect_video(self.ffprobe, output)

        self.assertEqual(
            (output_info.display_width, output_info.display_height),
            (226, 400),
        )
        self.assertIn("360×640", " ".join(captured_text))
        self.assertEqual(rotated.read_bytes(), original_bytes)


def _probe_fraction(value: str) -> Fraction:
    return Fraction(value)


def _stream_duration_for_test(stream: dict) -> Fraction:
    time_base = _probe_fraction(stream["time_base"])
    if stream.get("duration_ts") not in (None, "N/A"):
        return Fraction(int(stream["duration_ts"])) * time_base
    return _probe_fraction(stream["duration"])



if __name__ == "__main__":
    unittest.main()
