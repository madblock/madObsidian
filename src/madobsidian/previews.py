"""Generate small, watermarked WebP previews for imported raster images."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError


PREVIEW_THRESHOLD_BYTES = 256_000
MAX_PREVIEW_DIMENSION = 960
_FONT_PATH = Path(__file__).parent / "assets" / "DejaVuSans.ttf"


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


def _watermark(image: Image.Image, text: str) -> Image.Image:
    width, height = image.size
    padding = max(4, min(12, width // 80))
    font_size = max(8, min(16, width // 55))
    font = ImageFont.truetype(str(_FONT_PATH), font_size)
    lines = _wrap_text(text, font, max(1, width - padding * 2))
    line_height = max(font_size + 3, font.getbbox("Ag")[3] - font.getbbox("Ag")[1] + 3)

    # Reduce the type only for unusually short images so all text stays inside.
    while len(lines) * line_height + padding * 2 > height and font_size > 6:
        font_size -= 1
        font = ImageFont.truetype(str(_FONT_PATH), font_size)
        lines = _wrap_text(text, font, max(1, width - padding * 2))
        line_height = max(font_size + 2, font.getbbox("Ag")[3] - font.getbbox("Ag")[1] + 2)

    band_height = min(height, len(lines) * line_height + padding * 2)
    top = height - band_height
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((0, top, width, height), fill=(0, 0, 0, 150))
    y = top + padding
    for line in lines:
        draw.text((padding, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_height
    return Image.alpha_composite(image.convert("RGBA"), overlay)


def generate_preview(
    original_copy: Path,
    preview_directory: Path,
    original_filename: str,
    original_size_bytes: int,
) -> Path | None:
    """Create a WebP preview for a sufficiently large image, if it is an image."""
    if original_size_bytes < PREVIEW_THRESHOLD_BYTES:
        return None

    try:
        opened = Image.open(original_copy)
    except UnidentifiedImageError:
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
                (MAX_PREVIEW_DIMENSION, MAX_PREVIEW_DIMENSION),
                Image.Resampling.LANCZOS,
            )
            watermark_text = (
                f"{original_filename} | {original_size_bytes / 1_000_000:.1f} MB | "
                f"{original_width} × {original_height}"
            )
            watermarked = _watermark(preview, watermark_text)
            preview_directory.mkdir(parents=True, exist_ok=True)
            preview_path = preview_directory / "preview.webp"
            if has_transparency:
                watermarked.save(preview_path, format="WEBP", lossless=True, method=6)
            else:
                watermarked.convert("RGB").save(
                    preview_path, format="WEBP", quality=85, method=6
                )
            return preview_path
    except Exception as error:
        if isinstance(error, PreviewError):
            raise
        raise PreviewError(f"Could not generate preview for {original_filename}: {error}") from error
