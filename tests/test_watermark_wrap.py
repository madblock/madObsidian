"""Focused tests for watermark text wrapping and Pillow text layout."""

from __future__ import annotations

import unittest
from pathlib import Path

from PIL import ImageFont, features

from madobsidian.video_previews import _wrap_text


class _LengthFont:
    def getlength(self, text: str) -> int:
        if "\n" in text or "\r" in text:
            raise AssertionError("newline passed to getlength")
        return len(text) * 10


class WatermarkWrapTests(unittest.TestCase):
    def test_multiline_cyrillic_preserves_explicit_line_breaks(self) -> None:
        self.assertEqual(_wrap_text("\u041f\u0440\u0438\u0432\u0435\u0442 \u043c\u0438\u0440\n\u0412\u0438\u0434\u0435\u043e \u0430\u0440\u0445\u0438\u0432", _LengthFont(), 200), ["\u041f\u0440\u0438\u0432\u0435\u0442 \u043c\u0438\u0440", "\u0412\u0438\u0434\u0435\u043e \u0430\u0440\u0445\u0438\u0432"])

    def test_long_words_wrap_by_character(self) -> None:
        self.assertEqual(_wrap_text("abcdefgh", _LengthFont(), 30), ["abc", "def", "gh"])

    def test_words_wrap_at_spaces_before_splitting_long_words(self) -> None:
        self.assertEqual(_wrap_text("\u041f\u0440\u0438\u0432\u0435\u0442 \u043c\u0438\u0440", _LengthFont(), 70), ["\u041f\u0440\u0438\u0432\u0435\u0442", "\u043c\u0438\u0440"])

    def test_empty_paragraphs_and_trailing_newline_are_preserved(self) -> None:
        self.assertEqual(_wrap_text("one\n\ntwo\n", _LengthFont(), 200), ["one", "", "two", ""])

    def test_newline_wrapping_with_raqm_or_basic_layout(self) -> None:
        font_path = Path(__file__).parents[1] / "src" / "madobsidian" / "assets" / "DejaVuSans.ttf"
        layout = ImageFont.Layout.RAQM if features.check_feature("raqm") else ImageFont.Layout.BASIC
        font = ImageFont.truetype(str(font_path), 18, layout_engine=layout)
        wrapped = _wrap_text("[Видео] Рецепт.mov\n400 px | 1920×1080 | 00:10", font, 1000)
        self.assertEqual(len(wrapped), 2)
        self.assertTrue(all("\n" not in line and "\r" not in line for line in wrapped))


if __name__ == "__main__":
    unittest.main()
