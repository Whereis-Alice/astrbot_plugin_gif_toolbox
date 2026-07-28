from __future__ import annotations

import asyncio
import io
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import astrbot.api.message_components as Comp

from astrbot_plugin_gif_toolbox.main import GifToolboxPlugin
from astrbot_plugin_gif_toolbox.help_card import HELP_CARD_COMMANDS, HELP_CARD_SECTIONS, load_help_card
from astrbot_plugin_gif_toolbox.media_ops import (
    MediaOperationError,
    MediaOptions,
    change_gif_speed,
    crop_animation,
    decompose_animation,
    inspect_animation,
    make_single_image_gif,
    multi_image_to_gif,
    trim_animation,
    transform_image,
)


def image_bytes(color: tuple[int, int, int, int]) -> bytes:
    image = Image.new("RGBA", (32, 24), color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class MediaOperationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.options = MediaOptions(max_side=256, max_frames=20, max_output_bytes=1024 * 1024)

    def test_single_image_conversion_is_animated_gif(self) -> None:
        output, _ = make_single_image_gif(image_bytes((255, 0, 0, 255)), 250, 2, self.options)
        self.assertIn(output[:6], {b"GIF87a", b"GIF89a"})
        with Image.open(io.BytesIO(output)) as image:
            self.assertGreaterEqual(image.n_frames, 2)

    def test_speed_change_scales_each_frame_duration(self) -> None:
        frames = [
            Image.new("RGBA", (32, 24), (255, 0, 0, 255)),
            Image.new("RGBA", (32, 24), (0, 0, 255, 255)),
        ]
        source = io.BytesIO()
        frames[0].save(
            source,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=[100, 200],
            loop=0,
            disposal=2,
        )
        output, _ = change_gif_speed(source.getvalue(), 2, self.options)
        with Image.open(io.BytesIO(output)) as image:
            self.assertEqual(image.n_frames, 2)
            image.seek(0)
            self.assertEqual(image.info["duration"], 50)
            image.seek(1)
            self.assertEqual(image.info["duration"], 100)

    def test_duplicate_inputs_keep_multi_image_gif_animated(self) -> None:
        source = image_bytes((0, 255, 0, 255))
        output, _ = multi_image_to_gif([source, source], 100, self.options)
        with Image.open(io.BytesIO(output)) as image:
            self.assertGreaterEqual(image.n_frames, 2)

    def test_decompose_returns_one_png_per_source_frame(self) -> None:
        frames = [
            Image.new("RGBA", (16, 16), (255, 0, 0, 255)),
            Image.new("RGBA", (16, 16), (0, 0, 255, 255)),
        ]
        source = io.BytesIO()
        frames[0].save(
            source,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=[100, 100],
            loop=0,
            disposal=2,
        )
        self.assertEqual(len(decompose_animation(source.getvalue(), self.options)), 2)

    def test_animation_crop_uses_one_anchor_box_and_preserves_timing(self) -> None:
        frames: list[Image.Image] = []
        for blue in (40, 180):
            frame = Image.new("RGBA", (6, 4))
            for y in range(frame.height):
                for x in range(frame.width):
                    frame.putpixel((x, y), (x * 30, y * 40, blue, 255))
            frames.append(frame)
        source = io.BytesIO()
        frames[0].save(
            source,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=[80, 120],
            loop=0,
            disposal=2,
        )

        output, message = crop_animation(
            source.getvalue(),
            self.options,
            aspect_ratio=(1, 1),
            anchor="top_right",
        )
        self.assertIn("整体裁剪为 4×4", message)
        self.assertIn("比例 1:1，右上", message)
        with Image.open(io.BytesIO(output)) as image:
            self.assertEqual(image.size, (4, 4))
            self.assertEqual(image.n_frames, 2)
            image.seek(0)
            self.assertEqual(image.info["duration"], 80)
            self.assertEqual(image.convert("RGBA").getpixel((0, 0)), (60, 0, 40, 255))
            image.seek(1)
            self.assertEqual(image.info["duration"], 120)
            self.assertEqual(image.convert("RGBA").getpixel((0, 0)), (60, 0, 180, 255))

    def test_animation_crop_supports_size_and_margins_and_rejects_static_images(self) -> None:
        frames = [
            Image.new("RGBA", (6, 4), (255, 0, 0, 255)),
            Image.new("RGBA", (6, 4), (0, 0, 255, 255)),
        ]
        source = io.BytesIO()
        frames[0].save(
            source,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=[100, 100],
            loop=0,
            disposal=2,
        )

        sized, _ = crop_animation(
            source.getvalue(),
            self.options,
            target_size=(3, 2),
            anchor="bottom_left",
        )
        margined, _ = crop_animation(
            source.getvalue(),
            self.options,
            margins=(1, 1, 2, 1),
        )
        with Image.open(io.BytesIO(sized)) as image:
            self.assertEqual(image.size, (3, 2))
        with Image.open(io.BytesIO(margined)) as image:
            self.assertEqual(image.size, (3, 2))

        with self.assertRaisesRegex(MediaOperationError, "动图"):
            crop_animation(image_bytes((255, 0, 0, 255)), self.options, aspect_ratio=(1, 1))
        with self.assertRaisesRegex(MediaOperationError, "超出"):
            crop_animation(source.getvalue(), self.options, target_size=(7, 2))
        with self.assertRaisesRegex(MediaOperationError, "位置"):
            crop_animation(source.getvalue(), self.options, aspect_ratio=(1, 1), anchor="unknown")

    def test_animation_trim_removes_requested_frames_and_preserves_durations(self) -> None:
        frames = [
            Image.new("RGBA", (8, 8), (index * 40, 0, 0, 255))
            for index in range(5)
        ]
        source = io.BytesIO()
        frames[0].save(
            source,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=[60, 80, 100, 120, 140],
            loop=0,
            disposal=2,
        )

        output, message = trim_animation(
            source.getvalue(),
            self.options,
            drop_first=1,
            drop_last=1,
        )
        self.assertIn("移除前 1 帧、移除后 1 帧", message)
        self.assertIn("保留 3/5 帧", message)
        with Image.open(io.BytesIO(output)) as image:
            self.assertEqual(image.n_frames, 3)
            for index, expected in enumerate(((40, 80), (80, 100), (120, 120))):
                image.seek(index)
                red, duration = expected
                self.assertEqual(image.convert("RGBA").getpixel((0, 0)), (red, 0, 0, 255))
                self.assertEqual(image.info["duration"], duration)

        ranged, message = trim_animation(source.getvalue(), self.options, keep_range=(2, 4))
        self.assertIn("第 2 至 4 帧", message)
        with Image.open(io.BytesIO(ranged)) as image:
            self.assertEqual(image.n_frames, 3)
            image.seek(0)
            self.assertEqual(image.convert("RGBA").getpixel((0, 0)), (40, 0, 0, 255))
            image.seek(2)
            self.assertEqual(image.convert("RGBA").getpixel((0, 0)), (120, 0, 0, 255))

    def test_animation_trim_samples_only_after_the_kept_range(self) -> None:
        frames = [
            Image.new("RGBA", (8, 8), (index * 20, 0, 0, 255))
            for index in range(8)
        ]
        source = io.BytesIO()
        frames[0].save(
            source,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=[100] * len(frames),
            loop=0,
            disposal=2,
        )
        limited = MediaOptions(max_side=256, max_frames=3, max_output_bytes=1024 * 1024)

        output, message = trim_animation(source.getvalue(), limited, drop_first=2)
        self.assertIn("均匀采样为 3 帧", message)
        with Image.open(io.BytesIO(output)) as image:
            self.assertEqual(image.n_frames, 3)
            colors: list[int] = []
            for index in range(image.n_frames):
                image.seek(index)
                colors.append(image.convert("RGBA").getpixel((0, 0))[0])
            self.assertEqual(colors, [40, 80, 140])

        with self.assertRaisesRegex(MediaOperationError, "至少需要保留 2 帧"):
            trim_animation(source.getvalue(), self.options, drop_first=7)
        with self.assertRaisesRegex(MediaOperationError, "结束帧"):
            trim_animation(source.getvalue(), self.options, keep_range=(2, 9))

    def test_animation_info_reports_frame_metadata(self) -> None:
        frames = [
            Image.new("RGBA", (12, 8), (255, 0, 0, 255)),
            Image.new("RGBA", (12, 8), (0, 255, 0, 255)),
            Image.new("RGBA", (12, 8), (0, 0, 255, 255)),
        ]
        source = io.BytesIO()
        frames[0].save(
            source,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=[100, 200, 300],
            loop=0,
            disposal=2,
        )

        info = inspect_animation(source.getvalue())
        self.assertIn("格式：GIF", info)
        self.assertIn("尺寸：12×8 px", info)
        self.assertIn("帧数：3", info)
        self.assertIn("循环：无限循环", info)
        self.assertIn("总时长：600 ms", info)
        self.assertIn("平均帧率：5.00 FPS", info)
        static_info = inspect_animation(image_bytes((255, 0, 0, 255)))
        self.assertIn("动画：否", static_info)
        self.assertIn("帧数：1", static_info)

    def test_static_transforms_preserve_alpha_and_odd_sized_symmetry(self) -> None:
        source_image = Image.new("RGBA", (5, 1))
        for x, red in enumerate((10, 20, 30, 40, 50)):
            source_image.putpixel((x, 0), (red, 20, 30, 80))
        source = io.BytesIO()
        source_image.save(source, format="PNG")

        inverted, _ = transform_image(source.getvalue(), "invert", self.options)
        with Image.open(io.BytesIO(inverted)) as image:
            self.assertEqual(image.convert("RGBA").getpixel((0, 0)), (245, 235, 225, 80))

        flipped, _ = transform_image(source.getvalue(), "flip_horizontal", self.options)
        with Image.open(io.BytesIO(flipped)) as image:
            self.assertEqual(image.convert("RGBA").getpixel((0, 0)), (50, 20, 30, 80))

        mirrored_left, _ = transform_image(source.getvalue(), "mirror_left", self.options)
        with Image.open(io.BytesIO(mirrored_left)) as image:
            self.assertEqual(
                [image.convert("RGBA").getpixel((x, 0))[0] for x in range(5)],
                [10, 20, 30, 20, 10],
            )

        mirrored_right, _ = transform_image(source.getvalue(), "mirror_right", self.options)
        with Image.open(io.BytesIO(mirrored_right)) as image:
            self.assertEqual(
                [image.convert("RGBA").getpixel((x, 0))[0] for x in range(5)],
                [50, 40, 30, 40, 50],
            )

        vertical = Image.new("RGBA", (1, 5))
        for y, red in enumerate((10, 20, 30, 40, 50)):
            vertical.putpixel((0, y), (red, 20, 30, 80))
        vertical_source = io.BytesIO()
        vertical.save(vertical_source, format="PNG")
        mirrored_top, _ = transform_image(vertical_source.getvalue(), "mirror_top", self.options)
        with Image.open(io.BytesIO(mirrored_top)) as image:
            self.assertEqual(
                [image.convert("RGBA").getpixel((0, y))[0] for y in range(5)],
                [10, 20, 30, 20, 10],
            )
        mirrored_bottom, _ = transform_image(vertical_source.getvalue(), "mirror_bottom", self.options)
        with Image.open(io.BytesIO(mirrored_bottom)) as image:
            self.assertEqual(
                [image.convert("RGBA").getpixel((0, y))[0] for y in range(5)],
                [50, 40, 30, 40, 50],
            )

    def test_rotate_and_transform_animated_images(self) -> None:
        static = Image.new("RGBA", (3, 2), (12, 34, 56, 255))
        static_source = io.BytesIO()
        static.save(static_source, format="PNG")
        rotated, _ = transform_image(static_source.getvalue(), "rotate_clockwise", self.options)
        with Image.open(io.BytesIO(rotated)) as image:
            self.assertEqual(image.size, (2, 3))
        rotated_back, _ = transform_image(static_source.getvalue(), "rotate_counterclockwise", self.options)
        with Image.open(io.BytesIO(rotated_back)) as image:
            self.assertEqual(image.size, (2, 3))

        frames = [
            Image.new("RGBA", (3, 2), (255, 0, 0, 255)),
            Image.new("RGBA", (3, 2), (0, 0, 255, 255)),
        ]
        animated_source = io.BytesIO()
        frames[0].save(
            animated_source,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=[80, 120],
            loop=0,
            disposal=2,
        )
        transformed, _ = transform_image(animated_source.getvalue(), "flip_vertical", self.options)
        with Image.open(io.BytesIO(transformed)) as image:
            self.assertEqual(image.n_frames, 2)
            image.seek(0)
            self.assertEqual(image.info["duration"], 80)
            image.seek(1)
            self.assertEqual(image.info["duration"], 120)

    def test_frame_drop_mode_can_exceed_minimum_frame_interval_limit(self) -> None:
        frames = [
            Image.new("RGBA", (8, 8), (index * 20, 0, 255 - index * 20, 255))
            for index in range(8)
        ]
        source = io.BytesIO()
        frames[0].save(
            source,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=[100] * len(frames),
            loop=0,
            disposal=2,
        )

        output, message = change_gif_speed(source.getvalue(), 10, self.options, allow_frame_drop=True)
        self.assertIn("丢弃", message)
        with Image.open(io.BytesIO(output)) as image:
            self.assertEqual(image.n_frames, 4)
            self.assertEqual(image.info["duration"], 20)


class Message:
    def __init__(self, message: list[object]) -> None:
        self.message = message


class Event:
    def __init__(
        self,
        message: list[object],
        bot: object | None = None,
        message_str: str = "",
    ) -> None:
        self.message_obj = Message(message)
        self.bot = bot
        self.message_str = message_str
        self.stopped = False

    def get_messages(self) -> list[object]:
        return self.message_obj.message

    def stop_event(self) -> None:
        self.stopped = True

    @staticmethod
    def plain_result(text: str) -> str:
        return text

    @staticmethod
    def chain_result(chain: list[object]) -> list[object]:
        return chain


class SourceResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_help_card_covers_registered_commands_and_handler_returns_png(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        registered_commands = set(re.findall(r'@filter\.command\("([^"]+)"', source))
        self.assertEqual(registered_commands, HELP_CARD_COMMANDS)
        card_source = "\n".join(
            f"{entry.command}\n{entry.detail}"
            for section in HELP_CARD_SECTIONS
            for entry in section.entries
        )
        for command in registered_commands:
            self.assertIn(f"/{command}", card_source)

        card = load_help_card()
        self.assertTrue(card.startswith(b"\x89PNG\r\n\x1a\n"))
        with Image.open(io.BytesIO(card)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (1440, 1756))

        plugin = GifToolboxPlugin(None, {})
        event = Event([])
        handler = plugin.gif_toolbox_help(event)
        result = await anext(handler)
        self.assertEqual(result[0].text, "GIF 工具箱命令速查")
        self.assertTrue(result[1].file.startswith("base64://"))
        with self.assertRaises(StopAsyncIteration):
            await anext(handler)
        self.assertTrue(event.stopped)

    async def test_animation_trim_parser_supports_front_back_and_ranges(self) -> None:
        plugin = GifToolboxPlugin(None, {})

        removal = plugin._parse_animation_trim("gif截取 前10帧 后5帧")
        self.assertEqual(removal.drop_first, 10)
        self.assertEqual(removal.drop_last, 5)
        self.assertIsNone(removal.keep_range)

        frame_range = plugin._parse_animation_trim("gif截取 第12到第60帧")
        self.assertEqual(frame_range.keep_range, (12, 60))
        natural_range = plugin._parse_animation_trim("gif截取 第12帧到第60帧")
        self.assertEqual(natural_range.keep_range, (12, 60))

        with self.assertRaisesRegex(MediaOperationError, "请指定"):
            plugin._parse_animation_trim("gif截取")
        with self.assertRaisesRegex(MediaOperationError, "起始帧"):
            plugin._parse_animation_trim("gif截取 60-12帧")
        with self.assertRaisesRegex(MediaOperationError, "不能和前后"):
            plugin._parse_animation_trim("gif截取 前10帧 20-60帧")
        with self.assertRaisesRegex(MediaOperationError, "1 到 1000000"):
            plugin._parse_animation_trim("gif截取 前0帧")

    async def test_animation_crop_parser_supports_ratio_size_margins_and_anchor(self) -> None:
        plugin = GifToolboxPlugin(None, {})

        ratio = plugin._parse_animation_crop("gif裁剪 16:9 右上")
        self.assertEqual(ratio.aspect_ratio, (16, 9))
        self.assertEqual(ratio.anchor, "top_right")

        size = plugin._parse_animation_crop("gif裁剪 尺寸 512x288 左下")
        self.assertEqual(size.target_size, (512, 288))
        self.assertEqual(size.anchor, "bottom_left")

        margins = plugin._parse_animation_crop("gif裁剪 边距 8 上边距 4")
        self.assertEqual(margins.margins, (8, 4, 8, 8))
        self.assertEqual(margins.anchor, "center")

        with self.assertRaisesRegex(MediaOperationError, "指定一种"):
            plugin._parse_animation_crop("gif裁剪")
        with self.assertRaisesRegex(MediaOperationError, "边距裁剪不支持"):
            plugin._parse_animation_crop("gif裁剪 边距 8 右上")

    async def test_animation_crop_handler_stops_later_matching_handlers(self) -> None:
        plugin = GifToolboxPlugin(None, {})
        event = Event([], message_str="gif裁剪 1:1")
        handler = plugin.crop_animation_gif(event)

        self.assertEqual(await anext(handler), "⏳ 正在整体裁剪动图...")
        self.assertFalse(event.stopped)
        self.assertTrue((await anext(handler)).startswith("❌ 未检测到图片"))
        self.assertFalse(event.stopped)
        with self.assertRaises(StopAsyncIteration):
            await anext(handler)
        self.assertTrue(event.stopped)

    async def test_animation_trim_and_info_handlers_stop_later_matching_handlers(self) -> None:
        plugin = GifToolboxPlugin(None, {})
        trim_event = Event([], message_str="gif截取 前10帧")
        trim_handler = plugin.trim_animation_gif(trim_event)

        self.assertEqual(await anext(trim_handler), "⏳ 正在按帧截取动图...")
        self.assertTrue((await anext(trim_handler)).startswith("❌ 未检测到图片"))
        with self.assertRaises(StopAsyncIteration):
            await anext(trim_handler)
        self.assertTrue(trim_event.stopped)

        info_event = Event([], message_str="gif信息")
        info_handler = plugin.inspect_gif(info_event)
        self.assertEqual(await anext(info_handler), "⏳ 正在读取动图信息...")
        self.assertTrue((await anext(info_handler)).startswith("❌ 未检测到图片"))
        with self.assertRaises(StopAsyncIteration):
            await anext(info_handler)
        self.assertTrue(info_event.stopped)

    async def test_transform_handler_stops_later_matching_handlers(self) -> None:
        plugin = GifToolboxPlugin(None, {})
        event = Event([])
        handler = plugin._apply_image_transform(event, "invert", "反色")

        self.assertEqual(await anext(handler), "⏳ 正在处理反色...")
        self.assertFalse(event.stopped)
        self.assertTrue((await anext(handler)).startswith("❌ 未检测到图片"))
        self.assertFalse(event.stopped)
        with self.assertRaises(StopAsyncIteration):
            await anext(handler)
        self.assertTrue(event.stopped)

    async def test_speed_handler_stops_later_matching_handlers(self) -> None:
        plugin = GifToolboxPlugin(None, {})
        event = Event([])
        handler = plugin._change_speed(event, 2, 2, "调速")

        self.assertEqual(await anext(handler), "⏳ 正在处理 调速 2倍...")
        self.assertFalse(event.stopped)
        self.assertTrue((await anext(handler)).startswith("❌ 未检测到图片"))
        self.assertFalse(event.stopped)
        with self.assertRaises(StopAsyncIteration):
            await anext(handler)
        self.assertTrue(event.stopped)

    async def test_at_avatar_fallback_is_limited_to_onebot_events(self) -> None:
        plugin = GifToolboxPlugin(None, {})
        message = [Comp.At(qq=2127074778)]

        self.assertIsNone(plugin._at_avatar_candidate(Event(message)))
        candidate = plugin._at_avatar_candidate(Event(message, object()))
        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertEqual(
            candidate.reference,
            "https://q1.qlogo.cn/g?b=qq&nk=2127074778&s=640",
        )

    async def test_file_uri_base64_and_reply_chain_are_supported(self) -> None:
        expected = image_bytes((1, 2, 3, 255))
        descriptor, filename = tempfile.mkstemp(suffix=".png")
        os.close(descriptor)
        path = Path(filename)
        path.write_bytes(expected)
        try:
            plugin = GifToolboxPlugin(None, {})
            settings = plugin._settings()
            for component in (
                Comp.Image(str(path.as_uri())),
                Comp.Image.fromBytes(expected),
                Comp.Reply(id="1", chain=[Comp.Image(str(path))]),
            ):
                self.assertEqual(
                    await plugin._load_image(Event([component]), settings),
                    expected,
                )
        finally:
            path.unlink(missing_ok=True)

    async def test_onebot_file_id_fallback_is_supported(self) -> None:
        expected = image_bytes((4, 5, 6, 255))
        descriptor, filename = tempfile.mkstemp(suffix=".png")
        os.close(descriptor)
        path = Path(filename)
        path.write_bytes(expected)

        class Api:
            async def call_action(self, action: str, **kwargs: object) -> dict[str, str]:
                self.action = action
                self.kwargs = kwargs
                return {"file": str(path)}

        class Bot:
            api = Api()

        try:
            plugin = GifToolboxPlugin(None, {})
            settings = plugin._settings()
            event = Event([Comp.Image("onebot-file-id")], Bot())
            self.assertEqual(await plugin._load_image(event, settings), expected)
            self.assertEqual(event.bot.api.action, "get_file")
            self.assertEqual(event.bot.api.kwargs, {"file_id": "onebot-file-id"})
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
