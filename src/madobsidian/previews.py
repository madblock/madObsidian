"""Generate small, watermarked WebP previews for imported raster images."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from madobsidian.config import DEFAULT_CONFIG, ImageConfig, WatermarkConfig


PREVIEW_THRESHOLD_BYTES = DEFAULT_CONFIG.general.small_file_threshold_bytes
MAX_PREVIEW_DIMENSION = DEFAULT_CONFIG.images.max_dimension
REGISTERED_IMAGE_EXTENSIONS = frozenset(Image.registered_extensions())


def is_registered_image_filename(filename: str) -> bool:
    """Return whether Pillow recognizes this filename extension as an image format."""
    return Path(filename).suffix.casefold() in REGISTERED_IMAGE_EXTENSIONS


class PreviewError(OSError):
    """Raised when a recognized image cannot be previewed safely."""


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Wrap watermark text, splitting long filenames when necessary."""
    lines: list[str] = []
    line = ""
    for word in text.split(" "):
        candidate = f"{line} {word}" if line else word
        if font.getlength(candidate) <= max_width:
            line = candidate
            continue
        if line:
            lines.append(line)
            line = ""
        part = ""
        for character in word:
            if part and font.getlength(part + character) > max_width:
                lines.append(part)
                part = character
            else:
                part += character
        line = part
    if line:
        lines.append(line)
    return lines or [""]


def _watermark(image: Image.Image, text: str, settings: WatermarkConfig) -> Image.Image:
    width, height = image.size
    padding = settings.margin if settings.margin is not None else max(4, min(12, width // 80))
    font_size = settings.font_size if settings.font_size is not None else max(8, min(16, width // 55))
    font = ImageFont.truetype(str(settings.font_path), font_size)
    lines = _wrap_text(text, font, max(1, width - padding * 2))
    line_height = max(font_size + 3, font.getbbox("Ag")[3] - font.getbbox("Ag")[1] + 3)

    # Reduce the type only for unusually short images so all text stays inside.
    while len(lines) * line_height + padding * 2 > height and font_size > 6:
        font_size -= 1
        font = ImageFont.truetype(str(settings.font_path), font_size)
        lines = _wrap_text(text, font, max(1, width - padding * 2))
        line_height = max(font_size + 2, font.getbbox("Ag")[3] - font.getbbox("Ag")[1] + 2)

    band_height = min(height, len(lines) * line_height + padding * 2)
    top = 0 if settings.position.startswith("top-") else height - band_height
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    bottom = top + band_height
    opacity = settings.opacity if settings.opacity is not None else 150
    draw.rectangle((0, top, width, bottom), fill=(0, 0, 0, opacity))
    y = top + padding
    for line in lines:
        text_width = font.getlength(line)
        x = width - padding - text_width if settings.position.endswith("right") else padding
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_height
    return Image.alpha_composite(image.convert("RGBA"), overlay)


def generate_preview(
    original_copy: Path,
    preview_directory: Path,
    original_filename: str,
    original_size_bytes: int,
    *,
    image_config: ImageConfig | None = None,
    watermark_config: WatermarkConfig | None = None,
    small_file_threshold_bytes: int | None = None,
) -> Path | None:
    """Create a configured WebP preview for a sufficiently large image."""
    image_config = image_config or DEFAULT_CONFIG.images
    watermark_config = watermark_config or DEFAULT_CONFIG.watermark
    threshold = PREVIEW_THRESHOLD_BYTES if small_file_threshold_bytes is None else small_file_threshold_bytes
    if original_size_bytes < threshold:
        return None

    try:
        opened = Image.open(original_copy)
    except UnidentifiedImageError as error:
        if is_registered_image_filename(original_filename):
            raise PreviewError(f"Recognized image file cannot be decoded: {original_filename}") from error
        return None
    except OSError as error:
        raise PreviewError(f"Could not inspect eligible image {original_filename}: {error}") from error

    try:
        with opened:
            # Animated formats use their first frame as the archive preview.
            opened.seek(0)
            oriented = ImageOps.exif_transpose(opened)
            oriented.load()
            original_width, original_height = oriented.size
            has_transparency = (
                "A" in oriented.getbands()
                or "transparency" in oriented.info
            )
            preview = oriented.convert("RGBA" if has_transparency else "RGB")
            preview.thumbnail(
                (image_config.max_dimension, image_config.max_dimension),
                Image.Resampling.LANCZOS,
            )
            watermark_text = (
                f"{original_filename} | {original_size_bytes / 1_000_000:.1f} MB | "
                f"{original_width} × {original_height}"
            )
            watermarked = _watermark(preview, watermark_text, watermark_config) if watermark_config.enabled else preview
            preview_directory.mkdir(parents=True, exist_ok=True)
            preview_path = preview_directory / f"preview.{image_config.format}"
            if has_transparency:
                watermarked.save(preview_path, format=image_config.format.upper(), lossless=True, method=image_config.method)
            else:
                watermarked.convert("RGB").save(
                    preview_path, format=image_config.format.upper(), quality=image_config.quality, method=image_config.method
                )
            return preview_path
    except Exception as error:
        if isinstance(error, PreviewError):
            raise
        raise PreviewError(f"Could not generate preview for {original_filename}: {error}") from error
