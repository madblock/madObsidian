"""Focused checks that preview generation rejects eligible undecodable media."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from madobsidian.video_previews import VideoPreviewError, generate_video_preview


class MediaEligibilityTests(unittest.TestCase):
    def test_eligible_video_candidate_without_video_stream_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            original = Path(directory) / "audio-only.mp4"
            original.write_bytes(b"synthetic input")
            with mock.patch("madobsidian.video_previews._executable", side_effect=("ffmpeg", "ffprobe")), mock.patch(
                "madobsidian.video_previews._inspect_video", return_value=None
            ):
                with self.assertRaisesRegex(VideoPreviewError, "has no video stream"):
                    generate_video_preview(original, Path(directory) / "previews", original.name, 256_000)


if __name__ == "__main__":
    unittest.main()
