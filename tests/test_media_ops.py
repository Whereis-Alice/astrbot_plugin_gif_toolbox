from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import astrbot.api.message_components as Comp

from astrbot_plugin_gif_toolbox.main import GifToolboxPlugin
from astrbot_plugin_gif_toolbox.media_ops import (
    MediaOperationError,
    MediaOptions,
    change_gif_speed,
    crop_animation,
    decompose_animation,
    make_single_image_gif,
    multi_image_to_gif,
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


class SourceResolutionTests(unittest.IsolatedAsyncioTestCase):
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
