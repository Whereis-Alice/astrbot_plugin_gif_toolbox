"""Build the high-resolution static PNG command-reference asset for releases."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from help_card import (
    HELP_CARD_COLUMNS,
    HELP_CARD_COMMANDS,
    HELP_CARD_FOOTER,
    HELP_CARD_PATH,
    HELP_CARD_SECTIONS,
    HelpCardEntry,
    HelpCardSection,
)


CANVAS_WIDTH = 2048
CANVAS_HEIGHT = 6000
PAGE_MARGIN = 74
COLUMN_GAP = 42
CARD_RADIUS = 24
BACKGROUND = "#F1F4F6"
INK = "#16242D"
MUTED = "#53636E"
HEADER = "#133544"
HEADER_MUTED = "#C8D7DC"
HEADER_ACCENT = "#38B2AC"
CARD = "#FFFFFF"
CARD_BORDER = "#D7E1E6"
FOOTER = "#EAF7F5"
FOOTER_BORDER = "#9ED9D2"


def _font(path: Path, size: int, variation: str) -> ImageFont.FreeTypeFont:
    """Load a legible static font instance, including variable-font weights."""

    font = ImageFont.truetype(str(path), size)
    names = {
        name.decode("ascii", errors="ignore")
        for name in getattr(font, "get_variation_names", lambda: [])()
    }
    if variation in names:
        font.set_variation_by_name(variation)
    return font


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """Wrap mixed CJK/Latin text by measured pixels while preserving newlines."""

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
    height = 128
    content_width = width - 72
    for entry in section.entries:
        command_lines, detail_lines = _entry_lines(draw, entry, command_font, detail_font, content_width)
        height += len(command_lines) * 52 + 14 + len(detail_lines) * 42 + 34
    return height - 14


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
    draw.rounded_rectangle(
        (x, y, x + width, y + height),
        radius=CARD_RADIUS,
        fill=CARD,
        outline=CARD_BORDER,
        width=2,
    )
    draw.rounded_rectangle(
        (x + 30, y + 30, x + 40, y + 82),
        radius=5,
        fill=section.color,
    )
    draw.text((x + 60, y + 35), section.title, font=title_font, fill=INK)

    cursor = y + 116
    content_x = x + 36
    content_width = width - 72
    for index, entry in enumerate(section.entries):
        command_lines, detail_lines = _entry_lines(draw, entry, command_font, detail_font, content_width)
        for line in command_lines:
            draw.text((content_x, cursor), line, font=command_font, fill=section.color)
            cursor += 52
        cursor += 14
        for line in detail_lines:
            draw.text((content_x, cursor), line, font=detail_font, fill=MUTED)
            cursor += 42
        if index != len(section.entries) - 1:
            cursor += 20
            draw.line((content_x, cursor, x + width - 36, cursor), fill="#E7EDF0", width=2)
            cursor += 14
    return y + height


def _draw_footer(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    title_font: ImageFont.FreeTypeFont,
    detail_font: ImageFont.FreeTypeFont,
) -> int:
    content_width = width - 72
    lines = [line for item in HELP_CARD_FOOTER for line in _wrap_text(draw, item, detail_font, content_width)]
    height = 104 + len(lines) * 42
    draw.rounded_rectangle(
        (x, y, x + width, y + height),
        radius=CARD_RADIUS,
        fill=FOOTER,
        outline=FOOTER_BORDER,
        width=2,
    )
    draw.text((x + 36, y + 30), "使用说明", font=title_font, fill=INK)
    cursor = y + 80
    for line in lines:
        draw.text((x + 36, cursor), line, font=detail_font, fill=MUTED)
        cursor += 42
    return y + height


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--font", type=Path, required=True, help="Noto Sans SC TTF/OTF font path")
    parser.add_argument("--output", type=Path, default=HELP_CARD_PATH, help="PNG asset output path")
    args = parser.parse_args()
    if not args.font.is_file():
        parser.error(f"font file does not exist: {args.font}")

    title_font = _font(args.font, 70, "Bold")
    eyebrow_font = _font(args.font, 24, "Medium")
    subtitle_font = _font(args.font, 34, "Regular")
    section_font = _font(args.font, 40, "Bold")
    command_font = _font(args.font, 36, "Bold")
    detail_font = _font(args.font, 30, "Regular")

    image = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, CANVAS_WIDTH, 300), fill=HEADER)
    draw.rectangle((PAGE_MARGIN, 54, PAGE_MARGIN + 142, 62), fill=HEADER_ACCENT)
    draw.text((PAGE_MARGIN, 88), "GIF 工具箱", font=title_font, fill="#FFFFFF")
    draw.text(
        (PAGE_MARGIN, 184),
        f"命令速查 · 全部 {len(HELP_CARD_COMMANDS)} 个指令、别名与关键参数",
        font=subtitle_font,
        fill=HEADER_MUTED,
    )
    chip_text = "回复素材后发送命令"
    chip_width = int(draw.textlength(chip_text, font=eyebrow_font)) + 56
    chip_x = CANVAS_WIDTH - PAGE_MARGIN - chip_width
    draw.rounded_rectangle((chip_x, 86, CANVAS_WIDTH - PAGE_MARGIN, 142), radius=18, fill="#E1F2F0")
    draw.text((chip_x + 28, 102), chip_text, font=eyebrow_font, fill="#135761")

    column_width = (CANVAS_WIDTH - PAGE_MARGIN * 2 - COLUMN_GAP) // 2
    column_x = (PAGE_MARGIN, PAGE_MARGIN + column_width + COLUMN_GAP)
    column_y = [348, 348]
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
            ) + 34

    footer_bottom = _draw_footer(
        draw,
        PAGE_MARGIN,
        max(column_y) + 12,
        CANVAS_WIDTH - PAGE_MARGIN * 2,
        section_font,
        detail_font,
    )
    footnote = "astrbot_plugin_gif_toolbox · 独立 Fork · 详细参数以 README 为准"
    draw.text((PAGE_MARGIN, footer_bottom + 38), footnote, font=eyebrow_font, fill="#65757F")
    final_height = footer_bottom + 92

    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.crop((0, 0, CANVAS_WIDTH, final_height)).save(args.output, format="PNG", optimize=True)
    print(f"wrote {args.output} ({CANVAS_WIDTH}x{final_height})")


if __name__ == "__main__":
    main()
