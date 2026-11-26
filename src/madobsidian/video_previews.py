"""Generate full-length, watermarked MP4 previews for imported videos."""

from __future__ import annotations

import json
import math
import mimetypes
import os
import re
import shutil
import statistics
import subprocess
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from madobsidian.config import (
    AppConfig,
    DEFAULT_CONFIG,
    VideoConfig,
    WatermarkConfig,
    resolve_explicit_executable,
)


VIDEO_PREVIEW_THRESHOLD_BYTES = DEFAULT_CONFIG.general.small_file_threshold_bytes
MAX_VIDEO_PREVIEW_DIMENSION = DEFAULT_CONFIG.videos.max_dimension
VIDEO_EXTENSIONS = frozenset(
    {
        ".3g2", ".3gp", ".asf", ".avi", ".f4v", ".flv", ".m2t", ".m2ts",
        ".m2v", ".m4v", ".mkv", ".mov", ".mp4", ".mpe", ".mpeg", ".mpg",
        ".mpv", ".mts", ".mxf", ".ogv", ".ts", ".vob", ".webm", ".wmv",
    }
)

class VideoPreviewError(OSError):
    """Raised when an eligible video cannot be inspected or previewed."""


@dataclass(frozen=True)
class VideoInfo:
    streams: tuple[dict[str, Any], ...]
    video: dict[str, Any]
    audio: dict[str, Any] | None
    video_time_base: Fraction
    presentation_duration: Fraction
    display_width: int
    display_height: int


@dataclass(frozen=True)
class FrameTimeline:
    timestamps: tuple[Fraction, ...]
    intervals: tuple[Fraction, ...]
    frame_count: int
    variable_frame_rate: bool


def is_video_candidate(filename: str, size_bytes: int, threshold_bytes: int = DEFAULT_CONFIG.general.small_file_threshold_bytes) -> bool:
    """Check the threshold and both MIME and extension-based video hints."""
    if size_bytes < threshold_bytes:
        return False
    mime_type, _ = mimetypes.guess_type(filename)
    return bool(mime_type and mime_type.startswith("video/")) or Path(filename).suffix.casefold() in VIDEO_EXTENSIONS


def _executable(env_name: str, default_name: str, configured: str = "auto") -> str:
    env_value = os.environ.get(env_name)
    if env_value is not None and env_value != "":
        try:
            return resolve_explicit_executable(env_value)
        except ValueError as error:
            raise VideoPreviewError(f"{env_name} does not name an executable file: {env_value} ({error})") from error
    cleaned = configured.strip()
    if cleaned and cleaned.casefold() != "auto":
        try:
            return resolve_explicit_executable(cleaned)
        except ValueError as error:
            raise VideoPreviewError(
                f"Configured [tools] {default_name} does not name an executable file: {configured} ({error})"
            ) from error
    located = shutil.which(default_name)
    if not located:
        raise VideoPreviewError(
            f"Cannot find {default_name}; configure [tools] {default_name}, set {env_name}, or add it to PATH"
        )
    return located


def _run_json_probe(ffprobe: str, arguments: list[str], path: Path) -> dict[str, Any]:
    command = [ffprobe, "-v", "error", *arguments, "-of", "json", str(path)]
    try:
        with tempfile.TemporaryFile() as output:
            completed = subprocess.run(command, stdout=output, stderr=subprocess.DEVNULL, check=False, timeout=120)
            output.seek(0)
            payload = output.read(1024 * 1024 + 1)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise VideoPreviewError(f"Could not start ffprobe: {error}") from error
    if completed.returncode:
        raise VideoPreviewError(f"ffprobe failed for {path.name}: {completed.returncode}")
    if len(payload) > 1024 * 1024:
        raise VideoPreviewError(f"ffprobe metadata exceeds scan limit for {path.name}")
    try:
        return json.loads(payload)
    except json.JSONDecodeError as error:
        raise VideoPreviewError(f"ffprobe returned invalid JSON for {path.name}") from error


def _as_fraction(value: Any) -> Fraction | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None


def _stream_duration(stream: dict[str, Any], time_base: Fraction) -> Fraction | None:
    duration_ts = stream.get("duration_ts")
    if duration_ts not in (None, "", "N/A"):
        try:
            ticks = int(duration_ts)
            if ticks > 0:
                return ticks * time_base
        except (TypeError, ValueError):
            pass
    duration = _as_fraction(stream.get("duration"))
    return duration if duration is not None and duration > 0 else None


def _rotation_degrees(video: dict[str, Any]) -> int:
    for side_data in video.get("side_data_list", ()):
        if "rotation" in side_data:
            try:
                return round(float(side_data["rotation"])) % 360
            except (TypeError, ValueError):
                return 0
    tags = video.get("tags", {})
    try:
        return round(float(tags.get("rotate", 0))) % 360
    except (TypeError, ValueError):
        return 0


def _inspect_video(ffprobe: str, path: Path) -> VideoInfo | None:
    report = _run_json_probe(ffprobe, ["-show_streams", "-show_format"], path)
    streams = tuple(report.get("streams", ()))
    videos = [stream for stream in streams if stream.get("codec_type") == "video"]
    if not videos:
        return None
    video = videos[0]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    audio = audio_streams[0] if audio_streams else None
    time_base = _as_fraction(video.get("time_base"))
    if time_base is None or time_base <= 0:
        raise VideoPreviewError(f"Video stream in {path.name} has no valid time base")
    try:
        width, height = int(video["width"]), int(video["height"])
        if width <= 0 or height <= 0:
            raise VideoPreviewError(f"Video stream in {path.name} has invalid dimensions")
    except (KeyError, TypeError, ValueError) as error:
        raise VideoPreviewError(f"Video stream in {path.name} has invalid dimensions") from error
    rotation = _rotation_degrees(video)
    if rotation % 180:
        width, height = height, width
    duration = _stream_duration(video, time_base)
    if duration is None:
        timeline = _probe_timeline(ffprobe, path, "v:0", time_base)
        if not timeline.timestamps:
            raise VideoPreviewError(f"Video stream in {path.name} has no valid presentation timestamps")
        if not timeline.intervals:
            raise VideoPreviewError(
                f"Cannot determine presentation duration for {path.name}"
            )
        duration = timeline.timestamps[-1] - timeline.timestamps[0] + timeline.intervals[-1]
    return VideoInfo(streams, video, audio, time_base, duration, width, height)


def _probe_timeline(ffprobe: str, path: Path, selector: str, time_base: Fraction) -> FrameTimeline:
    command = [
        ffprobe, "-v", "error", "-select_streams", selector, "-show_frames",
        "-show_entries", "frame=best_effort_timestamp", "-of", "compact=p=1:nk=0", str(path),
    ]
    ticks: list[int] = []
    invalid = 0
    try:
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8")
            assert process.stdout is not None
            with process.stdout:
                for line in process.stdout:
                    # FFprobe 5.1 can emit an extra newline after nested frame side data.
                    section, _, fields = line.rstrip("\r\n").partition("|")
                    if section != "frame":
                        continue
                    if len(ticks) + invalid >= 1_000_000:
                        process.kill()
                        process.wait()
                        raise VideoPreviewError(f"Frame scan limit reached for {path.name}")
                    record = dict(field.split("=", 1) for field in fields.split("|") if "=" in field)
                    value = record.get("best_effort_timestamp")
                    try:
                        ticks.append(int(value))
                    except (TypeError, ValueError):
                        invalid += 1
            return_code = process.wait()
            errors.seek(max(0, os.fstat(errors.fileno()).st_size - 2000))
            error_text = errors.read(2000).decode("utf-8", "replace").strip()
    except OSError as error:
        raise VideoPreviewError(f"Could not run ffprobe frame scan: {error}") from error
    if return_code:
        raise VideoPreviewError(f"ffprobe frame scan failed for {path.name}: {error_text or return_code}")
    if invalid:
        raise VideoPreviewError(
            f"ffprobe reported {invalid} frame(s) without valid presentation timestamps in {path.name} ({selector})"
        )
    if not ticks:
        raise VideoPreviewError(f"ffprobe found no decoded frames in {path.name}")

    if any(right <= left for left, right in zip(ticks, ticks[1:])):
        raise VideoPreviewError(
            f"Video stream in {path.name} has non-increasing presentation timestamps"
        )
    timestamps = tuple(Fraction(tick) * time_base for tick in ticks)
    intervals = tuple(right - left for left, right in zip(timestamps, timestamps[1:]))
    median = statistics.median(intervals) if intervals else None
    variable = False
    if median is not None and median > 0:
        tolerance = max(Fraction(1, 1000), median / 100)
        variable = any(abs(interval - median) > tolerance for interval in intervals)
    return FrameTimeline(timestamps, intervals, len(ticks), variable)


def _video_duration(info: VideoInfo) -> Fraction:
    if info.presentation_duration <= 0:
        raise VideoPreviewError("Video presentation duration is not positive")
    return info.presentation_duration


def _format_duration(seconds: Fraction) -> str:
    value = float(seconds)
    hours = int(value // 3600)
    minutes = int((value % 3600) // 60)
    remainder = value - hours * 3600 - minutes * 60
    return f"{hours:02d}:{minutes:02d}:{remainder:05.2f}"


def _fit_dimensions(width: int, height: int, max_dimension: int = MAX_VIDEO_PREVIEW_DIMENSION) -> tuple[int, int, int, int]:
    scale = min(1.0, max_dimension / max(width, height))
    content_width = max(1, round(width * scale))
    content_height = max(1, round(height * scale))
    canvas_width = content_width + content_width % 2
    canvas_height = content_height + content_height % 2
    return content_width, content_height, canvas_width, canvas_height


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    def wrap_paragraph(paragraph: str) -> list[str]:
        lines: list[str] = []
        current = ""
        for word in paragraph.split(" "):
            candidate = f"{current} {word}" if current else word
            if font.getlength(candidate) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
                current = ""
            piece = ""
            for character in word:
                if piece and font.getlength(piece + character) > width:
                    lines.append(piece)
                    piece = character
                else:
                    piece += character
            current = piece
        if current:
            lines.append(current)
        return lines or [""]

    lines: list[str] = []
    for paragraph in re.split(r"\r\n|\r|\n", text):
        lines.extend(wrap_paragraph(paragraph))
    return lines


def _write_watermark(
    path: Path,
    canvas_size: tuple[int, int],
    filename: str,
    size_bytes: int,
    resolution: tuple[int, int],
    duration: Fraction,
    timeline: FrameTimeline,
    settings: WatermarkConfig | None = None,
) -> str:
    settings = settings or DEFAULT_CONFIG.watermark
    width, height = canvas_size
    fps = Fraction(timeline.frame_count, 1) / duration
    detail = (
        f"{size_bytes / 1_000_000:.1f} MB | {resolution[0]}×{resolution[1]} | "
        f"{_format_duration(duration)}"
    )
    if timeline.variable_frame_rate:
        detail += f" | {float(fps):.2f} FPS avg"
    text = f"{filename}\n{detail}"
    font_size = settings.font_size if settings.font_size is not None else max(9, min(14, width // 32))
    font = ImageFont.truetype(str(settings.font_path), font_size)
    padding = settings.margin if settings.margin is not None else 6
    lines = _wrap_text(text, font, max(1, width - padding * 2))
    line_height = max(font_size + 2, font.getbbox("Ag")[3] - font.getbbox("Ag")[1] + 2)
    band_height = min(height, len(lines) * line_height + padding * 2)
    overlay = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    top = 0 if settings.position.startswith("top-") else height - band_height
    opacity = settings.opacity if settings.opacity is not None else 155
    draw.rectangle((0, top, width, top + band_height), fill=(0, 0, 0, opacity))
    y = top + padding
    for line in lines:
        text_width = font.getlength(line)
        x = width - padding - text_width if settings.position.endswith("right") else padding
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_height
    overlay.save(path, format="PNG")
    return text


def _interval_tolerance(source_tb: Fraction, output_tb: Fraction) -> Fraction:
    return max(Fraction(1, 1000), source_tb + output_tb)


def _stream_start(timeline: FrameTimeline) -> Fraction:
    return timeline.timestamps[0]


def _check_output(
    ffprobe: str,
    output: Path,
    source_info: VideoInfo,
    source_video_timeline: FrameTimeline,
    source_audio_timeline: FrameTimeline | None,
    dimensions: tuple[int, int],
    video_config: VideoConfig | None = None,
) -> None:
    video_config = video_config or DEFAULT_CONFIG.videos
    result = _run_json_probe(ffprobe, ["-show_streams", "-show_format"], output)
    streams = tuple(result.get("streams", ()))
    videos = [stream for stream in streams if stream.get("codec_type") == "video"]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    codec_name = {"libx264": "h264"}.get(video_config.video_codec, video_config.video_codec)
    if not videos or videos[0].get("codec_name") != codec_name:
        raise VideoPreviewError(f"Generated preview does not contain {codec_name} video")
    output_video = videos[0]
    if (int(output_video.get("width", 0)), int(output_video.get("height", 0))) != dimensions:
        raise VideoPreviewError("Generated preview dimensions do not match the requested scale")
    if max(dimensions) > video_config.max_dimension:
        raise VideoPreviewError("Generated preview exceeds the maximum dimension")
    if source_info.audio is None:
        if audios:
            raise VideoPreviewError("Generated silent-video preview unexpectedly contains audio")
    elif not audios or audios[0].get("codec_name") != video_config.audio_codec:
        raise VideoPreviewError(f"Generated preview is missing the first {video_config.audio_codec} audio stream")

    output_tb = _as_fraction(output_video.get("time_base"))
    if output_tb is None:
        raise VideoPreviewError("Generated preview has no valid video time base")
    output_video_timeline = _probe_timeline(ffprobe, output, "v:0", output_tb)
    if output_video_timeline.frame_count != source_video_timeline.frame_count:
        raise VideoPreviewError(
            "Generated preview frame count changed "
            f"({source_video_timeline.frame_count} source, {output_video_timeline.frame_count} output)"
        )
    if len(output_video_timeline.intervals) != len(source_video_timeline.intervals):
        raise VideoPreviewError("Generated preview frame interval count changed")
    tolerance = _interval_tolerance(source_info.video_time_base, output_tb)
    for index, (source_interval, output_interval) in enumerate(
        zip(source_video_timeline.intervals, output_video_timeline.intervals)
    ):
        if abs(source_interval - output_interval) > tolerance:
            raise VideoPreviewError(f"Generated preview changed frame interval {index}")

    output_duration = _stream_duration(output_video, output_tb)
    if output_duration is None:
        if not output_video_timeline.intervals:
            raise VideoPreviewError(
                "Cannot determine generated preview duration"
            )
        output_duration = (
            output_video_timeline.timestamps[-1]
            - output_video_timeline.timestamps[0]
            + output_video_timeline.intervals[-1]
        )
    duration_tolerance = max(Fraction(1, 20), source_info.video_time_base + output_tb)
    if abs(source_info.presentation_duration - output_duration) > duration_tolerance:
        raise VideoPreviewError(
            "Generated preview video duration differs from the source "
            f"({float(source_info.presentation_duration):.6f}s source, "
            f"{float(output_duration):.6f}s output, "
            f"time bases {source_info.video_time_base}/{output_tb})"
        )

    if source_info.audio is not None and source_audio_timeline is not None:
        output_audio_tb = _as_fraction(audios[0].get("time_base"))
        if output_audio_tb is None:
            raise VideoPreviewError("Generated AAC stream has no valid time base")
        output_audio_timeline = _probe_timeline(ffprobe, output, "a:0", output_audio_tb)
        source_offset = _stream_start(source_audio_timeline) - _stream_start(source_video_timeline)
        output_offset = _stream_start(output_audio_timeline) - _stream_start(output_video_timeline)
        sample_rate = int(audios[0].get("sample_rate", 48000))
        aac_frame_duration = Fraction(1024, max(sample_rate, 1))
        av_tolerance = max(
            aac_frame_duration,
            source_info.video_time_base
            + (_as_fraction(source_info.audio.get("time_base")) or Fraction(0))
            + output_tb
            + output_audio_tb,
        )
        if abs(source_offset - output_offset) > av_tolerance:
            raise VideoPreviewError("Generated preview changed the relative audio/video start offset")

        source_audio_duration = _stream_duration(
            source_info.audio,
            _as_fraction(source_info.audio.get("time_base")) or Fraction(1, sample_rate),
        )
        output_audio_duration = _stream_duration(audios[0], output_audio_tb)
        if source_audio_duration is not None and output_audio_duration is not None:
            if abs(source_audio_duration - output_audio_duration) > max(duration_tolerance, aac_frame_duration):
                raise VideoPreviewError("Generated AAC duration differs from the source audio duration")

    if not output.is_file() or output.stat().st_size == 0:
        raise VideoPreviewError("Generated MP4 preview is empty")


def generate_video_preview(
    original_copy: Path,
    preview_directory: Path,
    original_filename: str,
    original_size_bytes: int,
    *,
    encoder_time_base: str | None = None,
    config: AppConfig | None = None,
) -> Path | None:
    """Create and verify a full-length MP4 preview for an eligible video."""
    config = config or DEFAULT_CONFIG
    video_settings = config.videos
    watermark_settings = config.watermark
    if not is_video_candidate(original_filename, original_size_bytes, config.general.small_file_threshold_bytes):
        return None
    ffmpeg = _executable("MADOBSIDIAN_FFMPEG", "ffmpeg", config.tools.ffmpeg)
    ffprobe = _executable("MADOBSIDIAN_FFPROBE", "ffprobe", config.tools.ffprobe)
    info = _inspect_video(ffprobe, original_copy)
    if info is None:
        raise VideoPreviewError(f"Eligible video candidate has no video stream: {original_filename}")

    source_video_timeline = _probe_timeline(ffprobe, original_copy, "v:0", info.video_time_base)
    duration = _video_duration(info)
    source_audio_timeline = None
    if info.audio is not None:
        audio_tb = _as_fraction(info.audio.get("time_base"))
        if audio_tb is None:
            raise VideoPreviewError("First audio stream has no valid time base")
        source_audio_timeline = _probe_timeline(ffprobe, original_copy, "a:0", audio_tb)

    content_width, content_height, canvas_width, canvas_height = _fit_dimensions(
        info.display_width, info.display_height, video_settings.max_dimension
    )
    video_filter_base = (
        f"[0:v:0]scale={content_width}:{content_height}:flags=lanczos,"
        f"pad={canvas_width}:{canvas_height}:(ow-iw)/2:(oh-ih)/2:color=black[main]"
    )
    preview_directory.mkdir(parents=True, exist_ok=True)
    final_path = preview_directory / f"preview.{video_settings.format}"
    time_base = encoder_time_base or f"{info.video_time_base.numerator}:{info.video_time_base.denominator}"
    try:
        with tempfile.TemporaryDirectory(prefix=".video-work-", dir=preview_directory.parent) as work:
            work_dir = Path(work)
            if watermark_settings.enabled:
                watermark_path = work_dir / "watermark.png"
                _write_watermark(
                    watermark_path,
                    (canvas_width, canvas_height),
                    original_filename,
                    original_size_bytes,
                    (info.display_width, info.display_height),
                    duration,
                    source_video_timeline,
                    watermark_settings,
                )
                video_filter = (
                    video_filter_base + ";"
                    # The watermark input is looped forever. Stop framesync at end of main video.
                    + "[main][1:v:0]overlay=x=0:y=0:eof_action=repeat:shortest=1:repeatlast=1,"
                    "format=yuv420p[vout]"
                )
            else:
                video_filter = video_filter_base + ";[main]format=yuv420p[vout]"
            output_path = work_dir / f"preview.{video_settings.format}"
            command = [
                ffmpeg,
                "-hide_banner", "-nostdin", "-y",
                "-copyts", "-start_at_zero",
                "-i", str(original_copy),
            ]
            if watermark_settings.enabled:
                command.extend(["-loop", "1", "-framerate", "1", "-i", str(watermark_path)])
            if info.audio and info.audio.get("sample_rate"):
                # MP4 edit-list offsets use movie ticks. Millisecond ticks can round
                # the AAC priming delay beyond one audio frame on delayed tracks.
                command.extend(["-movie_timescale", str(info.audio["sample_rate"])])
            command.extend([
                "-filter_complex", video_filter, "-map", "[vout]", "-map", "0:a:0?",
                "-fps_mode:v", "passthrough",
                "-enc_time_base:v", time_base,
                "-c:v", video_settings.video_codec, "-preset", video_settings.preset, "-bf", "0", "-crf", str(video_settings.video_crf),
                "-c:a", video_settings.audio_codec, "-b:a", "128k",
                "-movflags", "+faststart",
                "-f", video_settings.format,
                str(output_path),
            ])
            try:
                completed = subprocess.run(command, capture_output=True, text=True, check=False)
            except OSError as error:
                raise VideoPreviewError(f"Could not start FFmpeg: {error}") from error
            if completed.returncode:
                details = completed.stderr.strip()[-3000:]
                raise VideoPreviewError(f"FFmpeg failed for {original_filename}: {details or completed.returncode}")
            _check_output(
                ffprobe,
                output_path,
                info,
                source_video_timeline,
                source_audio_timeline,
                (canvas_width, canvas_height),
                video_settings,
            )
            os.replace(output_path, final_path)
    except VideoPreviewError:
        raise
    except Exception as error:
        raise VideoPreviewError(f"Could not generate video preview for {original_filename}: {error}") from error
    return final_path
