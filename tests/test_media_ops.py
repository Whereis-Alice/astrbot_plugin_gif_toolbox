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
    MediaOptions,
    change_gif_speed,
    decompose_animation,
    make_single_image_gif,
    multi_image_to_gif,
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


class Message:
    def __init__(self, message: list[object]) -> None:
        self.message = message


class Event:
    def __init__(self, message: list[object], bot: object | None = None) -> None:
        self.message_obj = Message(message)
        self.bot = bot

    def get_messages(self) -> list[object]:
        return self.message_obj.message


class SourceResolutionTests(unittest.IsolatedAsyncioTestCase):
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
