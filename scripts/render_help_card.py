"""Build the static PNG command-reference asset for release packaging."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from help_card import (
    HELP_CARD_COLUMNS,
    HELP_CARD_FOOTER,
    HELP_CARD_SECTIONS,
    HelpCardEntry,
    HelpCardSection,
)


CANVAS_WIDTH = 1440
CANVAS_HEIGHT = 5000
PAGE_MARGIN = 48
COLUMN_GAP = 30
CARD_RADIUS = 8
BACKGROUND = "#F4F6F8"
INK = "#17212B"
MUTED = "#4E5D68"
HEADER = "#183B4E"
CARD = "#FFFFFF"
CARD_BORDER = "#D9E1E7"
FOOTER = "#FFF8DB"
FOOTER_BORDER = "#E8C76A"


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """Wrap mixed CJK/Latin text by measured pixels, preserving newlines."""

    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for character in paragraph:
            candidate = current + character
            if current and draw.textlength(candidate, font=font) > width:
                lines.append(current.rstrip())
                current = character.lstrip()
            else:
                current = candidate
        if current:
            lines.append(current.rstrip())
    return lines


def _entry_lines(
    draw: ImageDraw.ImageDraw,
    entry: HelpCardEntry,
    command_font: ImageFont.FreeTypeFont,
    detail_font: ImageFont.FreeTypeFont,
    width: int,
) -> tuple[list[str], list[str]]:
    return (
        _wrap_text(draw, entry.command, command_font, width),
        _wrap_text(draw, entry.detail, detail_font, width),
    )


def _section_height(
    draw: ImageDraw.ImageDraw,
    section: HelpCardSection,
    command_font: ImageFont.FreeTypeFont,
    detail_font: ImageFont.FreeTypeFont,
    width: int,
) -> int:
    height = 88
    content_width = width - 52
    for entry in section.entries:
        command_lines, detail_lines = _entry_lines(draw, entry, command_font, detail_font, content_width)
        height += len(command_lines) * 32 + 8 + len(detail_lines) * 27 + 24
    return height - 8


def _draw_section(
    draw: ImageDraw.ImageDraw,
    section: HelpCardSection,
    x: int,
    y: int,
    width: int,
    title_font: ImageFont.FreeTypeFont,
    command_font: ImageFont.FreeTypeFont,
    detail_font: ImageFont.FreeTypeFont,
) -> int:
    height = _section_height(draw, section, command_font, detail_font, width)
    draw.rounded_rectangle((x, y, x + width, y + height), radius=CARD_RADIUS, fill=CARD, outline=CARD_BORDER, width=2)
    label_width = int(draw.textlength(section.title, font=title_font)) + 34
    draw.rounded_rectangle((x + 24, y + 20, x + 24 + label_width, y + 60), radius=6, fill=section.color)
    draw.text((x + 41, y + 27), section.title, font=title_font, fill="#FFFFFF")

    cursor = y + 84
    content_x = x + 26
    content_width = width - 52
    for index, entry in enumerate(section.entries):
        command_lines, detail_lines = _entry_lines(draw, entry, command_font, detail_font, content_width)
        for line in command_lines:
            draw.text((content_x, cursor), line, font=command_font, fill=section.color, stroke_width=1, stroke_fill=section.color)
            cursor += 32
        cursor += 8
        for line in detail_lines:
            draw.text((content_x, cursor), line, font=detail_font, fill=MUTED)
            cursor += 27
        if index != len(section.entries) - 1:
            cursor += 15
            draw.line((content_x, cursor, x + width - 26, cursor), fill="#E8EDF1", width=1)
            cursor += 9
    return y + height


def _draw_footer(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    title_font: ImageFont.FreeTypeFont,
    detail_font: ImageFont.FreeTypeFont,
) -> int:
    content_width = width - 52
    lines = [line for item in HELP_CARD_FOOTER for line in _wrap_text(draw, item, detail_font, content_width)]
    height = 76 + len(lines) * 27
    draw.rounded_rectangle((x, y, x + width, y + height), radius=CARD_RADIUS, fill=FOOTER, outline=FOOTER_BORDER, width=2)
    draw.text((x + 26, y + 22), "使用说明", font=title_font, fill=INK)
    cursor = y + 62
    for line in lines:
        draw.text((x + 26, cursor), line, font=detail_font, fill=MUTED)
        cursor += 27
    return y + height


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path, required=True, help="Noto Sans SC TTF/OTF font path")
    parser.add_argument(
        "--output",
        type=Path,
        default=PLUGIN_ROOT / "assets" / "gif_toolbox_help.png",
        help="PNG asset output path",
    )
    args = parser.parse_args()
    if not args.font.is_file():
        parser.error(f"font file does not exist: {args.font}")

    title_font = ImageFont.truetype(str(args.font), 46)
    subtitle_font = ImageFont.truetype(str(args.font), 24)
    section_font = ImageFont.truetype(str(args.font), 26)
    command_font = ImageFont.truetype(str(args.font), 22)
    detail_font = ImageFont.truetype(str(args.font), 19)

    image = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, CANVAS_WIDTH, 210), fill=HEADER)
    draw.text((PAGE_MARGIN, 46), "GIF 工具箱", font=title_font, fill="#FFFFFF", stroke_width=1, stroke_fill="#FFFFFF")
    draw.text((PAGE_MARGIN, 111), "命令速查 · 全部 25 个指令、别名与关键参数", font=subtitle_font, fill="#D8E9EF")
    chip_text = "回复素材后发送命令"
    chip_width = int(draw.textlength(chip_text, font=subtitle_font)) + 42
    draw.rounded_rectangle((CANVAS_WIDTH - PAGE_MARGIN - chip_width, 54, CANVAS_WIDTH - PAGE_MARGIN, 98), radius=8, fill="#E0F2F1")
    draw.text((CANVAS_WIDTH - PAGE_MARGIN - chip_width + 21, 65), chip_text, font=subtitle_font, fill="#0F4C5C")

    column_width = (CANVAS_WIDTH - PAGE_MARGIN * 2 - COLUMN_GAP) // 2
    column_x = (PAGE_MARGIN, PAGE_MARGIN + column_width + COLUMN_GAP)
    column_y = [244, 244]
    for column_index, section_indexes in enumerate(HELP_CARD_COLUMNS):
        for section_index in section_indexes:
            column_y[column_index] = _draw_section(
                draw,
                HELP_CARD_SECTIONS[section_index],
                column_x[column_index],
                column_y[column_index],
                column_width,
                section_font,
                command_font,
                detail_font,
            ) + 24

    footer_bottom = _draw_footer(
        draw,
        PAGE_MARGIN,
        max(column_y) + 8,
        CANVAS_WIDTH - PAGE_MARGIN * 2,
        section_font,
        detail_font,
    )
    footer_text = "astrbot_plugin_gif_toolbox · 独立 Fork · 详细参数以 README 为准"
    draw.text((PAGE_MARGIN, footer_bottom + 28), footer_text, font=detail_font, fill="#6B7882")
    final_height = footer_bottom + 76

    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.crop((0, 0, CANVAS_WIDTH, final_height)).save(args.output, format="PNG", optimize=True)
    print(f"wrote {args.output} ({CANVAS_WIDTH}x{final_height})")


if __name__ == "__main__":
    main()
