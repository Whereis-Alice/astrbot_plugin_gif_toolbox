"""Static command-reference card metadata and packaged asset loader."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final


PNG_SIGNATURE: Final = b"\x89PNG\r\n\x1a\n"
HELP_CARD_PATH: Final = Path(__file__).with_name("assets") / "gif_toolbox_help.png"


@dataclass(frozen=True)
class HelpCardEntry:
    """One concise command entry displayed on the packaged reference card."""

    command: str
    detail: str


@dataclass(frozen=True)
class HelpCardSection:
    """A colored command group displayed in the packaged reference card."""

    title: str
    color: str
    entries: tuple[HelpCardEntry, ...]


HELP_CARD_SECTIONS: Final = (
    HelpCardSection(
        "动图处理",
        "#0F766E",
        (
            HelpCardEntry(
                "/加速 [倍率]  /减速 [倍率]  /调速 [倍率]",
                "回复 GIF/APNG/WebP 动图。倍率 0.1-20，省略时为 2；"
                "/调速 0.5 表示半速。兼容写法：变快、变慢。",
            ),
            HelpCardEntry(
                "/gif裁剪",
                "别名：/动图裁剪。比例 宽:高 [位置]、尺寸 宽×高 [位置]、"
                "边距 N [单边边距]；比例和尺寸默认居中。",
            ),
            HelpCardEntry(
                "/gif截取",
                "别名：/动图截取。前N帧 [后N帧] 会删除两端；"
                "起始-结束帧仅保留该范围，帧号从 1 开始。",
            ),
            HelpCardEntry(
                "/gif信息",
                "别名：/动图信息、/gif详情。查看帧数、尺寸、时长、循环和元数据；"
                "静态图也可用。",
            ),
            HelpCardEntry("/gif分解", "回复动图，拆为 PNG 帧并通过合并转发送出。"),
        ),
    ),
    HelpCardSection(
        "图片变换",
        "#2563EB",
        (
            HelpCardEntry(
                "/反色  /顺时针  /逆时针\n/左右翻转  /上下翻转",
                "无参数。静态图和 GIF/APNG/WebP 动图均可处理。",
            ),
            HelpCardEntry(
                "/左对称  /右对称  /上对称  /下对称",
                "无参数。保留对应半边，再镜像补全另一半。",
            ),
        ),
    ),
    HelpCardSection(
        "图片生成",
        "#4D7C0F",
        (
            HelpCardEntry(
                "/图片转gif [时长]",
                "别名：/单图转gif。回复图片，输出实际 GIF 动画。"
                "时长可写 0.5s、0.5秒、2fps 或 0.5。",
            ),
            HelpCardEntry(
                "/图片转线稿",
                "回复图片，本地生成线稿；动图只取第一帧。",
            ),
            HelpCardEntry(
                "/表情包做旧 [次数]",
                "默认 5，范围 1-50；也可写 /做旧。静态图输出 JPEG，动图逐帧处理。",
            ),
        ),
    ),
    HelpCardSection(
        "合成与切图",
        "#B45309",
        (
            HelpCardEntry(
                "/合成1gif [行×列] [时长] [边距]",
                "回复精灵图，按从左到右、从上到下切格合成动画。默认 6×6、100ms。",
            ),
            HelpCardEntry(
                "/合成2gif [行×列] [时长] [边距]",
                "保留的兼容入口，参数和行为与 /合成1gif 相同。",
            ),
            HelpCardEntry(
                "/多图合成gif [时长]",
                "读取当前消息、回复消息或合并转发中的多张图片，每张图片成为一帧。",
            ),
            HelpCardEntry(
                "/裁剪 [行×列] [边距]",
                "网格切图命令，会发送多个小图；与 /gif裁剪 的整体裁剪不同。",
            ),
        ),
    ),
    HelpCardSection(
        "视频与速查",
        "#BE123C",
        (
            HelpCardEntry(
                "/视频转gif [开始-结束] [fps N] [缩放]",
                "回复或附带视频。示例：/视频转gif 1s-4s fps 10 0.5。",
            ),
            HelpCardEntry(
                "/gif工具箱帮助",
                "别名：/gif速查、/动图速查。直接发送本张完整指令速查图。",
            ),
        ),
    ),
)

HELP_CARD_COLUMNS: Final = ((0, 1, 4), (2, 3))
HELP_CARD_FOOTER: Final = (
    "使用方式：回复目标素材消息，或与命令同一条附带图片、动图或视频。",
    (
        "边距可写 边距 8、左边距 8、上边距 8 等；整体裁剪位置可写"
        " 左上、上、右上、左、居中、右、左下、下、右下。"
    ),
    "完整参数边界、配置项和上游声明见仓库 README。",
)

# Keep the exact registered command set beside the card source so updates can
# be checked without OCRing the PNG asset.
HELP_CARD_COMMANDS: Final = frozenset(
    {
        "加速",
        "减速",
        "调速",
        "反色",
        "顺时针",
        "逆时针",
        "左右翻转",
        "上下翻转",
        "左对称",
        "右对称",
        "上对称",
        "下对称",
        "图片转gif",
        "图片转线稿",
        "合成1gif",
        "合成2gif",
        "多图合成gif",
        "gif裁剪",
        "gif截取",
        "gif信息",
        "裁剪",
        "gif分解",
        "表情包做旧",
        "视频转gif",
        "gif工具箱帮助",
    }
)


class HelpCardAssetError(RuntimeError):
    """Raised when a release package is missing its static help-card asset."""


def load_help_card() -> bytes:
    """Return the packaged PNG help card without requiring host CJK fonts."""

    try:
        data = HELP_CARD_PATH.read_bytes()
    except OSError as exc:
        raise HelpCardAssetError("命令速查图片资源不可读取") from exc
    if not data.startswith(PNG_SIGNATURE):
        raise HelpCardAssetError("命令速查图片资源已损坏")
    return data
