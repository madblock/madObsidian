"""Centralized loading and validation for madObsidian processing settings."""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path

from PIL import ImageFont


_BUNDLED_FONT = Path(__file__).parent / "assets" / "DejaVuSans.ttf"
_SECTIONS = {"general", "images", "videos", "watermark"}
_OPTIONS = {
    "general": {"incoming_directory", "objects_directory", "small_file_threshold_bytes"},
    "images": {"max_dimension", "format", "quality", "method"},
    "videos": {"max_dimension", "format", "video_codec", "video_crf", "preset", "audio_codec"},
    "watermark": {"enabled", "font", "font_size", "opacity", "position", "margin"},
}


class ConfigError(ValueError):
    """Raised when a configuration file is missing, malformed, or unsupported."""


@dataclass(frozen=True)
class GeneralConfig:
    incoming_directory: str = "Incoming"
    objects_directory: str = "objects"
    small_file_threshold_bytes: int = 256_000


@dataclass(frozen=True)
class ImageConfig:
    max_dimension: int = 960
    format: str = "webp"
    quality: int = 85
    method: int = 6


@dataclass(frozen=True)
class VideoConfig:
    max_dimension: int = 400
    format: str = "mp4"
    video_codec: str = "libx264"
    video_crf: int = 25
    preset: str = "medium"
    audio_codec: str = "aac"


@dataclass(frozen=True)
class WatermarkConfig:
    enabled: bool = True
    font: str = "bundled"
    font_path: Path = _BUNDLED_FONT
    font_size: int | None = None
    opacity: int | None = None
    position: str = "bottom-left"
    margin: int | None = None


@dataclass(frozen=True)
class AppConfig:
    general: GeneralConfig = GeneralConfig()
    images: ImageConfig = ImageConfig()
    videos: VideoConfig = VideoConfig()
    watermark: WatermarkConfig = WatermarkConfig()
    config_path: Path | None = None


DEFAULT_CONFIG = AppConfig()


def _error(path: Path, section: str, key: str, message: str) -> ConfigError:
    return ConfigError(f"Configuration {path}: [{section}] {key}: {message}")


def _integer(parser: configparser.ConfigParser, path: Path, section: str, key: str,
             default: int | None, minimum: int, maximum: int, *, allow_auto: bool = False) -> int | None:
    if not parser.has_option(section, key):
        return default
    raw = parser.get(section, key).strip()
    if allow_auto and raw.casefold() == "auto":
        return None
    try:
        value = int(raw, 10)
    except ValueError as error:
        raise _error(path, section, key, "must be an integer") from error
    if not minimum <= value <= maximum:
        raise _error(path, section, key, f"must be between {minimum} and {maximum}")
    return value


def _choice(parser: configparser.ConfigParser, path: Path, section: str, key: str,
            default: str, allowed: set[str]) -> str:
    value = parser.get(section, key, fallback=default).strip().casefold()
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise _error(path, section, key, f"unsupported value {value!r}; supported values: {choices}")
    return value


def _boolean(parser: configparser.ConfigParser, path: Path, section: str, key: str, default: bool) -> bool:
    if not parser.has_option(section, key):
        return default
    raw = parser.get(section, key).strip().casefold()
    values = {"true": True, "yes": True, "on": True, "1": True,
              "false": False, "no": False, "off": False, "0": False}
    if raw not in values:
        raise _error(path, section, key, "must be a Boolean (true/false, yes/no, on/off, or 1/0)")
    return values[raw]


def _directory(parser: configparser.ConfigParser, path: Path, key: str, default: str) -> str:
    value = parser.get("general", key, fallback=default).strip()
    if not value or "\0" in value:
        raise _error(path, "general", key, "must be a non-empty path")
    return value


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load a complete configuration, or return documented built-in defaults."""
    if path is None:
        return DEFAULT_CONFIG
    config_path = Path(path).expanduser().resolve()
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    try:
        with config_path.open("r", encoding="utf-8-sig") as stream:
            parser.read_file(stream)
    except OSError as error:
        raise ConfigError(f"Cannot read configuration file {config_path}: {error}") from error
    except configparser.Error as error:
        raise ConfigError(f"Invalid configuration file {config_path}: {error}") from error

    if parser.defaults():
        unknown = ", ".join(sorted(parser.defaults()))
        raise ConfigError(f"Configuration {config_path}: unknown DEFAULT keys: {unknown}")
    for section in parser.sections():
        if section not in _SECTIONS:
            raise ConfigError(f"Configuration {config_path}: unknown section [{section}]")
        unknown = set(parser.options(section)) - _OPTIONS[section]
        if unknown:
            raise ConfigError(f"Configuration {config_path}: unknown key(s) in [{section}]: {', '.join(sorted(unknown))}")

    general = GeneralConfig(
        incoming_directory=_directory(parser, config_path, "incoming_directory", "Incoming"),
        objects_directory=_directory(parser, config_path, "objects_directory", "objects"),
        small_file_threshold_bytes=_integer(
            parser, config_path, "general", "small_file_threshold_bytes", 256_000, 0, 2**63 - 1
        ),
    )
    images = ImageConfig(
        max_dimension=_integer(parser, config_path, "images", "max_dimension", 960, 1, 16_384),
        format=_choice(parser, config_path, "images", "format", "webp", {"webp"}),
        quality=_integer(parser, config_path, "images", "quality", 85, 1, 100),
        method=_integer(parser, config_path, "images", "method", 6, 0, 6),
    )
    videos = VideoConfig(
        max_dimension=_integer(parser, config_path, "videos", "max_dimension", 400, 2, 16_384),
        format=_choice(parser, config_path, "videos", "format", "mp4", {"mp4"}),
        video_codec=_choice(parser, config_path, "videos", "video_codec", "libx264", {"libx264"}),
        video_crf=_integer(parser, config_path, "videos", "video_crf", 25, 0, 51),
        preset=_choice(parser, config_path, "videos", "preset", "medium", {
            "ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow",
        }),
        audio_codec=_choice(parser, config_path, "videos", "audio_codec", "aac", {"aac"}),
    )
    font_value = parser.get("watermark", "font", fallback="bundled").strip()
    if not font_value:
        raise _error(config_path, "watermark", "font", "must be 'bundled' or a valid font path")
    if font_value.casefold() == "bundled":
        font_path = _BUNDLED_FONT.resolve()
        font_value = "bundled"
    else:
        configured_font = Path(font_value).expanduser()
        font_path = (configured_font if configured_font.is_absolute() else config_path.parent / configured_font).resolve()
        if not font_path.is_file():
            raise _error(config_path, "watermark", "font", f"font file does not exist: {font_path}")
        try:
            ImageFont.truetype(str(font_path), 12)
        except OSError as error:
            raise _error(config_path, "watermark", "font", f"font file cannot be loaded: {font_path}") from error
    watermark = WatermarkConfig(
        enabled=_boolean(parser, config_path, "watermark", "enabled", True),
        font=font_value,
        font_path=font_path,
        font_size=_integer(parser, config_path, "watermark", "font_size", None, 6, 256, allow_auto=True),
        opacity=_integer(parser, config_path, "watermark", "opacity", None, 0, 255, allow_auto=True),
        position=_choice(parser, config_path, "watermark", "position", "bottom-left", {
            "bottom-left", "bottom-right", "top-left", "top-right",
        }),
        margin=_integer(parser, config_path, "watermark", "margin", None, 0, 1024, allow_auto=True),
    )
    if videos.max_dimension % 2:
        raise _error(config_path, "videos", "max_dimension", "must be even for MP4-compatible output dimensions")
    return AppConfig(general, images, videos, watermark, config_path)


def resolve_vault_directory(vault: str | Path, configured: str) -> Path:
    """Resolve a configured vault directory and prevent it escaping the vault."""
    root = Path(vault).resolve()
    value = Path(configured).expanduser()
    candidate = Path(os.path.abspath(value if value.is_absolute() else root / value))
    try:
        lexical_relative = candidate.relative_to(root)
    except ValueError as error:
        raise ConfigError(f"Configured directory {configured!r} must be inside vault {root}") from error
    current = root
    for component in lexical_relative.parts:
        current = current / component
        if current.is_symlink():
            raise ConfigError(f"Configured directory path contains a symbolic link: {current}")
    target = candidate.resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise ConfigError(f"Configured directory {configured!r} resolves outside vault {root}") from error
    return target
