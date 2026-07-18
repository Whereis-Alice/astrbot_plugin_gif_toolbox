"""CPU-bound image and animation operations for GIF Toolbox.

Copyright (C) 2026 Huli3 and AstrBot Plugin Authors.
This file is a modified work based on shskjw/astrbot_plugin_gifcaijian and
is licensed under the GNU Affero General Public License v3.0 or later.
See LICENSE for the complete license text.
"""

from __future__ import annotations

import io
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError
from PIL.Image import DecompressionBombError, DecompressionBombWarning

try:
    import imageio.v2 as imageio
except ImportError:  # pragma: no cover - exercised when optional video support is absent
    imageio = None


class MediaOperationError(ValueError):
    """A user-facing media processing error."""


@dataclass(frozen=True)
class MediaOptions:
    """Validated limits shared by media operations."""

    max_side: int = 1280
    max_frames: int = 160
    gif_max_colors: int = 256
    max_output_bytes: int = 10 * 1024 * 1024


def _open_image(data: bytes) -> Image.Image:
    """Open an image while turning Pillow's unsafe-image errors into a stable error."""

    if not data:
        raise MediaOperationError("图片内容为空")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", DecompressionBombWarning)
            image = Image.open(io.BytesIO(data))
            image.load()
        return image
    except (UnidentifiedImageError, DecompressionBombError, DecompressionBombWarning) as exc:
        raise MediaOperationError("文件不是可处理的图片，或图片像素过大") from exc
    except OSError as exc:
        raise MediaOperationError("图片无法读取，可能已损坏") from exc


def _resample() -> int:
    try:
        return Image.Resampling.LANCZOS
    except AttributeError:  # pragma: no cover - compatibility with older Pillow
        return Image.LANCZOS


def fit_within(image: Image.Image, max_side: int) -> Image.Image:
    """Return a copy that does not exceed max_side on either dimension."""

    if max_side < 16:
        raise MediaOperationError("最大边长不能小于 16 像素")
    image = image.convert("RGBA")
    width, height = image.size
    largest = max(width, height)
    if largest <= max_side:
        return image.copy()
    ratio = max_side / largest
    target = (max(1, round(width * ratio)), max(1, round(height * ratio)))
    return image.resize(target, _resample())


def _sample_indices(total: int, maximum: int) -> list[int]:
    if total <= 0:
        return []
    if total <= maximum:
        return list(range(total))
    if maximum <= 1:
        return [0]
    return sorted({round(index * (total - 1) / (maximum - 1)) for index in range(maximum)})


def _animation_frames(data: bytes, options: MediaOptions) -> tuple[list[Image.Image], list[int], bool]:
    """Load sampled composited frames, their durations, and the animation flag."""

    image = _open_image(data)
    try:
        total = max(1, int(getattr(image, "n_frames", 1)))
        animated = bool(getattr(image, "is_animated", False) and total > 1)
        indices = _sample_indices(total, options.max_frames)
        frames: list[Image.Image] = []
        durations: list[int] = []
        for index in indices:
            image.seek(index)
            duration = int(image.info.get("duration", 100) or 100)
            frames.append(fit_within(image.copy(), options.max_side))
            durations.append(max(20, duration))
        if not frames:
            raise MediaOperationError("图片中没有可用帧")
        return frames, durations, animated
    except EOFError as exc:
        raise MediaOperationError("动画帧数据不完整") from exc
    finally:
        image.close()


def _encode_animation_once(
    frames: list[Image.Image],
    durations: list[int],
    output_format: str,
    colors: int,
) -> bytes:
    if not frames:
        raise MediaOperationError("没有可写入的动画帧")

    frames = _prevent_frame_coalescing(frames)
    format_name = output_format.upper()
    if format_name not in {"GIF", "APNG", "WEBP"}:
        format_name = "GIF"
    duration_arg: int | list[int] = durations[0] if len(set(durations)) == 1 else durations
    output = io.BytesIO()

    try:
        if format_name == "GIF":
            palette_frames = [
                frame.convert("RGBA").convert(
                    "P",
                    palette=Image.Palette.ADAPTIVE,
                    colors=max(2, min(256, colors)),
                )
                for frame in frames
            ]
            palette_frames[0].save(
                output,
                format="GIF",
                save_all=True,
                append_images=palette_frames[1:],
                duration=duration_arg,
                loop=0,
                disposal=2,
                optimize=False,
            )
        elif format_name == "APNG":
            rgba_frames = [frame.convert("RGBA") for frame in frames]
            rgba_frames[0].save(
                output,
                format="PNG",
                save_all=True,
                append_images=rgba_frames[1:],
                duration=duration_arg,
                loop=0,
                disposal=2,
            )
        else:
            rgba_frames = [frame.convert("RGBA") for frame in frames]
            rgba_frames[0].save(
                output,
                format="WEBP",
                save_all=True,
                append_images=rgba_frames[1:],
                duration=duration_arg,
                loop=0,
                lossless=False,
                quality=80,
                method=4,
            )
    except OSError as exc:
        raise MediaOperationError("动画编码失败") from exc
    return output.getvalue()


def _prevent_frame_coalescing(frames: list[Image.Image]) -> list[Image.Image]:
    """Keep requested repeated frames from being folded into one by Pillow."""

    if len(frames) < 2:
        return frames
    result: list[Image.Image] = []
    previous_bytes: bytes | None = None
    for index, source in enumerate(frames):
        frame = source.convert("RGBA")
        frame_bytes = frame.tobytes()
        if previous_bytes is not None and frame_bytes == previous_bytes:
            frame = frame.copy()
            marker_x = max(0, frame.width - 1)
            marker_y = max(0, frame.height - 1)
            red, green, blue, _ = frame.getpixel((marker_x, marker_y))
            marker = (
                (red + 97 * index) % 256,
                (green + 57 * index) % 256,
                (blue + 23 * index) % 256,
                255,
            )
            # A two-pixel corner marker is visually negligible but survives
            # palette quantisation more reliably than a one-channel change.
            frame.putpixel((marker_x, marker_y), marker)
            if frame.width > 1:
                frame.putpixel((marker_x - 1, marker_y), marker)
            frame_bytes = frame.tobytes()
        result.append(frame)
        previous_bytes = frame_bytes
    return result


def encode_animation(
    frames: list[Image.Image],
    durations: list[int],
    options: MediaOptions,
    output_format: str = "GIF",
) -> tuple[bytes, bool]:
    """Encode an animation and make bounded, deterministic reductions when needed."""

    if len(frames) != len(durations):
        raise MediaOperationError("动画帧和时长数量不一致")

    candidates = (
        (1.0, options.gif_max_colors),
        (0.85, min(options.gif_max_colors, 160)),
        (0.70, min(options.gif_max_colors, 96)),
        (0.55, min(options.gif_max_colors, 64)),
    )
    last_result = b""
    was_reduced = False
    for scale, colors in candidates:
        if scale == 1.0:
            candidate_frames = frames
        else:
            candidate_frames = [
                frame.resize(
                    (
                        max(1, round(frame.width * scale)),
                        max(1, round(frame.height * scale)),
                    ),
                    _resample(),
                )
                for frame in frames
            ]
        result = _encode_animation_once(candidate_frames, durations, output_format, colors)
        last_result = result
        if len(result) <= options.max_output_bytes:
            return result, was_reduced
        was_reduced = True

    return last_result, True


def change_gif_speed(data: bytes, factor: float, options: MediaOptions) -> tuple[bytes, str]:
    """Change GIF playback speed while retaining each source frame duration."""

    if not 0.1 <= factor <= 20:
        raise MediaOperationError("倍速必须在 0.1 到 20 之间")
    frames, durations, animated = _animation_frames(data, options)
    if not animated:
        raise MediaOperationError("这不是 GIF/APNG/WebP 动图")
    adjusted = [max(20, round(duration / factor)) for duration in durations]
    result, reduced = encode_animation(frames, adjusted, options, "GIF")
    suffix = "，已为控制体积自动压缩" if reduced else ""
    return result, f"✅ GIF 已调整为 {factor:g} 倍速度{suffix}"


def make_single_image_gif(
    data: bytes,
    duration_ms: int,
    frame_count: int,
    options: MediaOptions,
) -> tuple[bytes, str]:
    """Wrap the first frame of an image in an actual GIF animation."""

    if not 20 <= duration_ms <= 60_000:
        raise MediaOperationError("每帧时长必须在 20 到 60000 毫秒之间")
    if not 2 <= frame_count <= 12:
        raise MediaOperationError("GIF 帧数必须在 2 到 12 之间")
    frames, _, _ = _animation_frames(data, options)
    first = frames[0]
    repeated = [first.copy() for _ in range(frame_count)]
    result, reduced = encode_animation(
        repeated,
        [duration_ms] * frame_count,
        options,
        "GIF",
    )
    suffix = "，已为控制体积自动压缩" if reduced else ""
    return result, f"✅ 已转换为 GIF（{frame_count} 帧，每帧 {duration_ms}ms）{suffix}"


def sprite_sheet_to_animation(
    data: bytes,
    rows: int,
    columns: int,
    duration_ms: int,
    options: MediaOptions,
    output_format: str,
    margins: tuple[int, int, int, int] = (0, 0, 0, 0),
) -> tuple[bytes, str]:
    """Split a sprite sheet row-by-row and encode every tile as a frame."""

    if not 1 <= rows <= 30 or not 1 <= columns <= 30:
        raise MediaOperationError("网格行列数必须在 1 到 30 之间")
    if not 20 <= duration_ms <= 60_000:
        raise MediaOperationError("每帧时长必须在 20 到 60000 毫秒之间")
    image = fit_within(_open_image(data), options.max_side)
    left, top, right, bottom = margins
    if min(margins) < 0:
        raise MediaOperationError("边距不能为负数")
    usable_width = image.width - left - right
    usable_height = image.height - top - bottom
    if usable_width < columns or usable_height < rows:
        raise MediaOperationError("边距或网格参数超过图片尺寸")

    tile_width = usable_width // columns
    tile_height = usable_height // rows
    frames: list[Image.Image] = []
    for row in range(rows):
        for column in range(columns):
            x0 = left + column * tile_width
            y0 = top + row * tile_height
            frames.append(image.crop((x0, y0, x0 + tile_width, y0 + tile_height)))
    if len(frames) > options.max_frames:
        frames = [frames[index] for index in _sample_indices(len(frames), options.max_frames)]
    output, reduced = encode_animation(
        frames,
        [duration_ms] * len(frames),
        options,
        output_format,
    )
    suffix = "，已自动压缩" if reduced else ""
    return output, f"✅ 精灵图已合成，共 {len(frames)} 帧{suffix}"


def multi_image_to_gif(
    images: Iterable[bytes],
    duration_ms: int,
    options: MediaOptions,
) -> tuple[bytes, str]:
    """Place multiple source images on a common canvas and create a GIF."""

    if not 20 <= duration_ms <= 60_000:
        raise MediaOperationError("每帧时长必须在 20 到 60000 毫秒之间")
    source_frames: list[Image.Image] = []
    for data in images:
        if len(source_frames) >= options.max_frames:
            break
        frames, _, _ = _animation_frames(data, options)
        source_frames.append(frames[0])
    if not source_frames:
        raise MediaOperationError("没有可用图片")

    width = max(frame.width for frame in source_frames)
    height = max(frame.height for frame in source_frames)
    frames: list[Image.Image] = []
    for frame in source_frames:
        canvas = Image.new("RGBA", (width, height), (255, 255, 255, 0))
        x = (width - frame.width) // 2
        y = (height - frame.height) // 2
        canvas.alpha_composite(frame.convert("RGBA"), (x, y))
        frames.append(canvas)

    output, reduced = encode_animation(frames, [duration_ms] * len(frames), options, "GIF")
    suffix = "，已自动压缩" if reduced else ""
    return output, f"✅ 已将 {len(frames)} 张图片合成为 GIF{suffix}"


def crop_grid(
    data: bytes,
    rows: int,
    columns: int,
    margins: tuple[int, int, int, int],
    max_parts: int,
) -> list[bytes]:
    """Crop an image into a grid and return PNG parts."""

    if not 1 <= rows <= 20 or not 1 <= columns <= 20:
        raise MediaOperationError("网格行列数必须在 1 到 20 之间")
    if rows * columns > max_parts:
        raise MediaOperationError(f"分块数量不能超过 {max_parts}")
    image = _open_image(data).convert("RGBA")
    left, top, right, bottom = margins
    if min(margins) < 0:
        raise MediaOperationError("边距不能为负数")
    width = image.width - left - right
    height = image.height - top - bottom
    if width < columns or height < rows:
        raise MediaOperationError("边距或网格参数超过图片尺寸")
    cell_width = width // columns
    cell_height = height // rows
    result: list[bytes] = []
    for row in range(rows):
        for column in range(columns):
            x0 = left + column * cell_width
            y0 = top + row * cell_height
            output = io.BytesIO()
            image.crop((x0, y0, x0 + cell_width, y0 + cell_height)).save(output, format="PNG")
            result.append(output.getvalue())
    return result


def decompose_animation(data: bytes, options: MediaOptions) -> list[bytes]:
    """Turn an animated image into PNG frame bytes."""

    frames, _, animated = _animation_frames(data, options)
    if not animated:
        raise MediaOperationError("这不是 GIF/APNG/WebP 动图")
    result: list[bytes] = []
    for frame in frames:
        output = io.BytesIO()
        frame.save(output, format="PNG")
        result.append(output.getvalue())
    return result


def image_to_line_art(data: bytes, options: MediaOptions) -> bytes:
    """Apply a local, deterministic edge-detection line-art effect."""

    image = fit_within(_open_image(data), options.max_side).convert("RGB")
    edges = image.convert("L").filter(ImageFilter.FIND_EDGES)
    result = ImageEnhance.Contrast(ImageOps.invert(edges)).enhance(3.0)
    output = io.BytesIO()
    result.save(output, format="JPEG", quality=90)
    return output.getvalue()


def _age_one_frame(image: Image.Image, times: int) -> Image.Image:
    result = image.convert("RGB")
    for index in range(times):
        if index % 3 == 0:
            red, green, blue = result.split()
            green = green.point(lambda value: min(255, value + 2))
            red = red.point(lambda value: max(0, value - 1))
            blue = blue.point(lambda value: max(0, value - 1))
            result = Image.merge("RGB", (red, green, blue))
        buffer = io.BytesIO()
        result.save(buffer, format="JPEG", quality=max(25, 70 - index * 3))
        buffer.seek(0)
        with Image.open(buffer) as compressed:
            result = compressed.convert("RGB")
        if index % 3 == 0:
            result = result.filter(ImageFilter.GaussianBlur(radius=0.2 + index / 30))
        if index % 2 == 0:
            result = ImageEnhance.Color(result).enhance(0.985)
        else:
            result = ImageEnhance.Contrast(result).enhance(0.99)
    return result


def age_image(data: bytes, times: int, options: MediaOptions) -> tuple[bytes, str]:
    """Apply the upstream's repeated-forwarding meme effect to static or animated media."""

    times = max(1, min(50, times))
    frames, durations, animated = _animation_frames(data, options)
    aged = [_age_one_frame(frame, times) for frame in frames]
    if animated:
        output, reduced = encode_animation(aged, durations, options, "GIF")
        suffix = "，已自动压缩" if reduced else ""
        return output, f"✅ 动图做旧完成（{len(aged)} 帧，{times} 次）{suffix}"
    output = io.BytesIO()
    aged[0].save(output, format="JPEG", quality=max(30, 70 - times * 3))
    return output.getvalue(), f"✅ 图片做旧完成（{times} 次）"


def video_to_animation(
    source: Path,
    start_seconds: float,
    end_seconds: float | None,
    fps: int,
    scale: float,
    options: MediaOptions,
    output_format: str,
    max_duration_seconds: float,
) -> tuple[bytes, str]:
    """Extract a bounded clip from a local video with imageio/FFmpeg."""

    if imageio is None:
        raise MediaOperationError("缺少 imageio[ffmpeg]，无法处理视频")
    if not source.is_file():
        raise MediaOperationError("视频文件不存在")
    if not 0.1 <= scale <= 1.0:
        raise MediaOperationError("缩放比例必须在 0.1 到 1.0 之间")
    if fps < 1 or fps > 60:
        raise MediaOperationError("帧率必须在 1 到 60 之间")

    reader = None
    try:
        reader = imageio.get_reader(str(source), format="FFMPEG")
        metadata = reader.get_meta_data()
        source_fps = float(metadata.get("fps") or 30)
        duration = float(metadata.get("duration") or 0)
        if duration <= 0:
            raise MediaOperationError("无法读取视频时长")
        start = max(0.0, start_seconds)
        if start >= duration:
            raise MediaOperationError("开始时间超出视频时长")
        end = min(duration, end_seconds if end_seconds is not None else duration)
        end = min(end, start + max_duration_seconds)
        if end <= start:
            raise MediaOperationError("视频时间范围无效")

        target_fps = min(source_fps, float(fps))
        step = max(1, round(source_fps / target_fps))
        frames: list[Image.Image] = []
        for index, frame in enumerate(reader):
            current = index / source_fps
            if current < start:
                continue
            if current > end:
                break
            if index % step:
                continue
            frames.append(fit_within(Image.fromarray(frame), options.max_side))
            if len(frames) >= options.max_frames:
                break
        if not frames:
            raise MediaOperationError("指定时间范围内没有可用视频帧")
        output, reduced = encode_animation(
            frames,
            [max(20, round(1000 / target_fps))] * len(frames),
            options,
            output_format,
        )
        suffix = "，已自动压缩" if reduced else ""
        return output, f"✅ 视频转换完成（{len(frames)} 帧，{target_fps:.1f} FPS）{suffix}"
    except MediaOperationError:
        raise
    except Exception as exc:
        raise MediaOperationError(f"视频处理失败：{exc}") from exc
    finally:
        if reader is not None:
            reader.close()
