"""CPU-bound image and animation operations for GIF Toolbox.

Copyright (C) 2026 Whereis-Alice and AstrBot Plugin Authors.
This file is a modified work based on shskjw/astrbot_plugin_gifcaijian and
includes non-meme transform functionality informed by
lirundong093-glitch/astrbot_plugin_pic_toolbox. It is licensed under the
GNU Affero General Public License v3.0 or later.
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


def _animation_frames(
    data: bytes,
    options: MediaOptions,
    indices: Iterable[int] | None = None,
) -> tuple[list[Image.Image], list[int], bool]:
    """Load composited frames, sampling all frames unless explicit indices are supplied."""

    image = _open_image(data)
    try:
        total = max(1, int(getattr(image, "n_frames", 1)))
        animated = bool(getattr(image, "is_animated", False) and total > 1)
        selected_indices = _sample_indices(total, options.max_frames) if indices is None else list(indices)
        if not selected_indices:
            raise MediaOperationError("图片中没有可用帧")
        if any(index < 0 or index >= total for index in selected_indices):
            raise MediaOperationError("动画帧索引超出范围")
        frames: list[Image.Image] = []
        durations: list[int] = []
        for index in selected_indices:
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


def _animation_info(data: bytes) -> tuple[int, bool]:
    """Read animation metadata without decoding every source frame."""

    image = _open_image(data)
    try:
        total = max(1, int(getattr(image, "n_frames", 1)))
        return total, bool(getattr(image, "is_animated", False) and total > 1)
    finally:
        image.close()


def _gif_palette_frames(
    frames: list[Image.Image],
    colors: int,
) -> tuple[list[Image.Image], int | None]:
    """Quantize RGBA frames while reserving one GIF palette index for alpha."""

    rgba_frames = [frame.convert("RGBA") for frame in frames]
    has_transparency = any(frame.getchannel("A").getextrema()[0] < 255 for frame in rgba_frames)
    if not has_transparency:
        return (
            [
                frame.convert(
                    "P",
                    palette=Image.Palette.ADAPTIVE,
                    colors=max(2, min(256, colors)),
                )
                for frame in rgba_frames
            ],
            None,
        )

    # GIF has only one binary transparency index. Reserve 255 so visible
    # colors cannot be mistaken for transparent pixels after quantization.
    palette_frames: list[Image.Image] = []
    for frame in rgba_frames:
        alpha = frame.getchannel("A")
        rgb = Image.new("RGB", frame.size, (0, 0, 0))
        rgb.paste(frame, mask=alpha)
        palette_frame = rgb.convert(
            "P",
            palette=Image.Palette.ADAPTIVE,
            colors=max(1, min(255, colors - 1)),
        )
        transparency_mask = alpha.point(lambda value: 255 if value < 128 else 0)
        palette_frame.paste(255, mask=transparency_mask)
        palette_frames.append(palette_frame)
    return palette_frames, 255


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
            palette_frames, transparency = _gif_palette_frames(frames, colors)
            save_kwargs: dict[str, int | bool | list[Image.Image] | list[int]] = {
                "save_all": True,
                "append_images": palette_frames[1:],
                "duration": duration_arg,
                "loop": 0,
                "disposal": 2,
                "optimize": False,
            }
            if transparency is not None:
                save_kwargs["transparency"] = transparency
            palette_frames[0].save(output, format="GIF", **save_kwargs)
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


def change_gif_speed(
    data: bytes,
    factor: float,
    options: MediaOptions,
    allow_frame_drop: bool = False,
) -> tuple[bytes, str]:
    """Change GIF speed, optionally dropping frames once 20ms frame timing is reached."""

    # ``/减速 20`` is represented internally as a 0.05 playback factor.
    # Keep the public 0.1-20 command multiplier range usable in both directions.
    if not 0.05 <= factor <= 20:
        raise MediaOperationError("倍速必须在 0.05 到 20 之间")
    frames, durations, animated = _animation_frames(data, options)
    if not animated:
        raise MediaOperationError("这不是 GIF/APNG/WebP 动图")

    adjusted = [max(20, round(duration / factor)) for duration in durations]
    source_duration = sum(durations)
    target_duration = source_duration / factor
    selected_indices = list(range(len(frames)))

    # A GIF frame cannot reliably use an interval below 20ms on common
    # platforms. When enabled, retain evenly spaced frames until the encoded
    # duration can approach the requested playback time without breaking that
    # minimum interval.
    if allow_frame_drop and sum(adjusted) > target_duration:
        average_adjusted = sum(adjusted) / len(adjusted)
        keep_count = max(1, min(len(frames), int(target_duration // average_adjusted)))
        selected_indices = _sample_indices(len(frames), keep_count)
        while len(selected_indices) > 1:
            selected_duration = sum(adjusted[index] for index in selected_indices)
            if selected_duration <= target_duration:
                break
            selected_indices = _sample_indices(len(frames), len(selected_indices) - 1)

    selected_frames = [frames[index] for index in selected_indices]
    selected_durations = [adjusted[index] for index in selected_indices]
    result, reduced = encode_animation(selected_frames, selected_durations, options, "GIF")
    output_duration = sum(selected_durations)
    actual_factor = source_duration / output_duration if output_duration else factor
    suffix = "，已为控制体积自动压缩" if reduced else ""
    if len(selected_indices) < len(frames):
        return (
            result,
            (
                "✅ GIF 已调速至 "
                f"约 {actual_factor:g} 倍（为突破 20ms 帧间隔，已均匀丢弃 "
                f"{len(frames) - len(selected_indices)} 帧）{suffix}"
            ),
        )
    if actual_factor + 0.05 < factor:
        return (
            result,
            f"✅ GIF 已调速至约 {actual_factor:g} 倍（受 20ms 最小帧间隔限制）{suffix}",
        )
    return result, f"✅ GIF 已调整为 {factor:g} 倍速度{suffix}"


def reverse_animation(data: bytes, options: MediaOptions) -> tuple[bytes, str]:
    """Reverse an animation while keeping every frame paired with its duration."""

    total, animated = _animation_info(data)
    frames, durations, _ = _animation_frames(data, options)
    if not animated:
        raise MediaOperationError("这不是 GIF/APNG/WebP 动图")

    result, reduced = encode_animation(frames[::-1], durations[::-1], options, "GIF")
    sampling = f"（原始 {total} 帧，均匀采样为 {len(frames)} 帧）" if len(frames) < total else f"（{len(frames)} 帧）"
    suffix = "，已为控制体积自动压缩" if reduced else ""
    return result, f"✅ 动图已倒放{sampling}{suffix}"


_CROP_ANCHORS: dict[str, tuple[str, str]] = {
    "top_left": ("left", "top"),
    "top": ("center", "top"),
    "top_right": ("right", "top"),
    "left": ("left", "center"),
    "center": ("center", "center"),
    "right": ("right", "center"),
    "bottom_left": ("left", "bottom"),
    "bottom": ("center", "bottom"),
    "bottom_right": ("right", "bottom"),
}

_CROP_ANCHOR_LABELS = {
    "top_left": "左上",
    "top": "上方",
    "top_right": "右上",
    "left": "左侧",
    "center": "居中",
    "right": "右侧",
    "bottom_left": "左下",
    "bottom": "下方",
    "bottom_right": "右下",
}


def _crop_offset(available: int, alignment: str) -> int:
    if alignment == "left" or alignment == "top":
        return 0
    if alignment == "right" or alignment == "bottom":
        return available
    return available // 2


def _anchored_crop_box(
    width: int,
    height: int,
    crop_width: int,
    crop_height: int,
    anchor: str,
) -> tuple[int, int, int, int]:
    try:
        horizontal, vertical = _CROP_ANCHORS[anchor]
    except KeyError as exc:
        raise MediaOperationError("不支持的裁剪位置") from exc
    if not 1 <= crop_width <= width or not 1 <= crop_height <= height:
        raise MediaOperationError("裁剪尺寸超出动图画布")
    x0 = _crop_offset(width - crop_width, horizontal)
    y0 = _crop_offset(height - crop_height, vertical)
    return x0, y0, x0 + crop_width, y0 + crop_height


def crop_animation(
    data: bytes,
    options: MediaOptions,
    *,
    aspect_ratio: tuple[int, int] | None = None,
    target_size: tuple[int, int] | None = None,
    margins: tuple[int, int, int, int] | None = None,
    anchor: str = "center",
) -> tuple[bytes, str]:
    """Crop every frame of an animation with one shared crop box.

    Exactly one of ``aspect_ratio``, ``target_size`` or ``margins`` must be
    supplied. The input is composited before cropping, so GIF disposal and
    transparency are preserved consistently across frames.
    """

    modes = sum(value is not None for value in (aspect_ratio, target_size, margins))
    if modes != 1:
        raise MediaOperationError("请指定一种动图裁剪方式")
    if anchor not in _CROP_ANCHORS:
        raise MediaOperationError("不支持的裁剪位置")
    frames, durations, animated = _animation_frames(data, options)
    if not animated:
        raise MediaOperationError("这不是 GIF/APNG/WebP 动图")

    width, height = frames[0].size
    if aspect_ratio is not None:
        ratio_width, ratio_height = aspect_ratio
        if ratio_width < 1 or ratio_height < 1:
            raise MediaOperationError("裁剪比例必须为正数")
        if width * ratio_height >= height * ratio_width:
            crop_width = max(1, min(width, height * ratio_width // ratio_height))
            crop_height = height
        else:
            crop_width = width
            crop_height = max(1, min(height, width * ratio_height // ratio_width))
        crop_box = _anchored_crop_box(width, height, crop_width, crop_height, anchor)
        detail = f"比例 {ratio_width}:{ratio_height}，{_CROP_ANCHOR_LABELS[anchor]}"
    elif target_size is not None:
        crop_width, crop_height = target_size
        if crop_width < 1 or crop_height < 1:
            raise MediaOperationError("裁剪尺寸必须为正数")
        crop_box = _anchored_crop_box(width, height, crop_width, crop_height, anchor)
        detail = f"尺寸 {crop_width}×{crop_height}，{_CROP_ANCHOR_LABELS[anchor]}"
    else:
        assert margins is not None
        left, top, right, bottom = margins
        if min(margins) < 0:
            raise MediaOperationError("边距不能为负数")
        crop_width = width - left - right
        crop_height = height - top - bottom
        if crop_width < 1 or crop_height < 1:
            raise MediaOperationError("边距超过动图画布")
        crop_box = (left, top, left + crop_width, top + crop_height)
        detail = f"边距 左{left} 上{top} 右{right} 下{bottom}"

    cropped = [frame.crop(crop_box) for frame in frames]
    output, reduced = encode_animation(cropped, durations, options, "GIF")
    suffix = "，已为控制体积自动压缩" if reduced else ""
    crop_width = crop_box[2] - crop_box[0]
    crop_height = crop_box[3] - crop_box[1]
    return (
        output,
        f"✅ 动图已整体裁剪为 {crop_width}×{crop_height}（{detail}，{len(cropped)} 帧）{suffix}",
    )


def trim_animation(
    data: bytes,
    options: MediaOptions,
    *,
    drop_first: int = 0,
    drop_last: int = 0,
    keep_range: tuple[int, int] | None = None,
) -> tuple[bytes, str]:
    """Remove leading/trailing frames or keep an inclusive, one-based frame range."""

    if drop_first < 0 or drop_last < 0:
        raise MediaOperationError("要移除的帧数不能为负数")
    if keep_range is not None and (drop_first or drop_last):
        raise MediaOperationError("保留范围不能和前后删帧同时使用")

    total, animated = _animation_info(data)
    if not animated:
        raise MediaOperationError("这不是 GIF/APNG/WebP 动图")

    if keep_range is not None:
        start_frame, end_frame = keep_range
        if start_frame < 1 or end_frame < 1 or start_frame > end_frame:
            raise MediaOperationError("保留帧范围无效")
        if end_frame > total:
            raise MediaOperationError(f"结束帧不能超过动图总帧数 {total}")
        keep_start = start_frame - 1
        keep_end = end_frame
        detail = f"已截取第 {start_frame} 至 {end_frame} 帧"
    else:
        keep_start = drop_first
        keep_end = total - drop_last
        actions: list[str] = []
        if drop_first:
            actions.append(f"移除前 {drop_first} 帧")
        if drop_last:
            actions.append(f"移除后 {drop_last} 帧")
        if not actions:
            raise MediaOperationError("请指定要移除的前帧、后帧或保留范围")
        detail = "、".join(actions)

    kept_count = keep_end - keep_start
    if kept_count < 2:
        raise MediaOperationError("截取后至少需要保留 2 帧动图")

    output_count = min(kept_count, options.max_frames)
    selected_indices = [
        keep_start + index
        for index in _sample_indices(kept_count, output_count)
    ]
    frames, durations, _ = _animation_frames(data, options, selected_indices)
    output, reduced = encode_animation(frames, durations, options, "GIF")
    sampling = f"，已从保留范围均匀采样为 {len(frames)} 帧" if len(frames) < kept_count else ""
    suffix = "，已为控制体积自动压缩" if reduced else ""
    return output, f"✅ {detail}（保留 {kept_count}/{total} 帧）{sampling}{suffix}"


def _format_file_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KiB"
    return f"{size / (1024 * 1024):.2f} MiB"


def _format_duration(duration_ms: int) -> str:
    if duration_ms < 1000:
        return f"{duration_ms} ms"
    if duration_ms < 60_000:
        return f"{duration_ms / 1000:.2f} 秒"
    minutes, milliseconds = divmod(duration_ms, 60_000)
    return f"{minutes} 分 {milliseconds / 1000:.2f} 秒"


def inspect_animation(data: bytes, duration_sample_limit: int = 5_000) -> str:
    """Return safe, human-readable container and frame metadata for an image."""

    if duration_sample_limit < 1:
        raise MediaOperationError("信息读取的抽样帧数必须至少为 1")
    image = _open_image(data)
    try:
        metadata = dict(image.info)
        format_name = image.format or "未知"
        width, height = image.size
        total = max(1, int(getattr(image, "n_frames", 1)))
        animated = bool(getattr(image, "is_animated", False) and total > 1)
        sampled_indices = _sample_indices(total, min(total, duration_sample_limit))
        durations: list[int] = []
        for index in sampled_indices:
            image.seek(index)
            duration = int(image.info.get("duration", metadata.get("duration", 0)) or 0)
            durations.append(max(0, duration))

        loop = metadata.get("loop")
        if loop is None:
            loop_text = "未声明"
        elif loop == 0:
            loop_text = "无限循环"
        else:
            loop_text = f"{loop} 次"
        has_transparency = "A" in image.getbands() or "transparency" in metadata
        lines = [
            "✅ 动图信息",
            f"格式：{format_name}",
            f"尺寸：{width}×{height} px",
            f"文件大小：{_format_file_size(len(data))}",
            f"颜色模式：{image.mode}",
            f"透明度：{'有' if has_transparency else '无'}",
            f"动画：{'是' if animated else '否'}",
            f"帧数：{total}",
            f"循环：{loop_text}",
        ]
        if "version" in metadata:
            lines.append(f"容器版本：{metadata['version']}")
        if durations:
            average_duration = sum(durations) / len(durations)
            duration_prefix = "帧时长" if len(durations) == total else f"帧时长（抽样 {len(durations)}/{total}）"
            lines.append(
                f"{duration_prefix}：{min(durations)} 到 {max(durations)} ms，平均 {average_duration:.1f} ms"
            )
            if len(durations) == total:
                total_duration = sum(durations)
                lines.append(f"总时长：{_format_duration(total_duration)}")
                if total_duration > 0:
                    lines.append(f"平均帧率：{total * 1000 / total_duration:.2f} FPS")
        if "background" in metadata:
            lines.append(f"背景索引：{metadata['background']}")
        if "transparency" in metadata:
            lines.append(f"透明色索引：{metadata['transparency']}")
        if "dpi" in metadata:
            dpi = metadata["dpi"]
            if isinstance(dpi, tuple) and len(dpi) >= 2:
                lines.append(f"DPI：{dpi[0]:g}×{dpi[1]:g}")
        comment = metadata.get("comment")
        if isinstance(comment, bytes):
            lines.append(f"注释：{len(comment)} 字节")
        elif isinstance(comment, str) and comment:
            lines.append(f"注释：{len(comment.encode('utf-8'))} 字节")
        if metadata:
            lines.append("元数据键：" + "、".join(sorted(str(key) for key in metadata)))
        return "\n".join(lines)
    except EOFError as exc:
        raise MediaOperationError("动画帧数据不完整") from exc
    finally:
        image.close()


def _invert_rgba(image: Image.Image) -> Image.Image:
    red, green, blue, alpha = image.convert("RGBA").split()
    inverted = ImageOps.invert(Image.merge("RGB", (red, green, blue)))
    return Image.merge("RGBA", (*inverted.split(), alpha))


def _mirror_half(image: Image.Image, keep: str) -> Image.Image:
    """Mirror one half of an RGBA image while preserving odd-size centre pixels."""

    image = image.convert("RGBA")
    width, height = image.size
    result = Image.new("RGBA", image.size)
    if keep == "left":
        half_width = (width + 1) // 2
        half = image.crop((0, 0, half_width, height))
        result.alpha_composite(half, (0, 0))
        if width > 1:
            mirrored = half.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            result.alpha_composite(mirrored.crop((width % 2, 0, half_width, height)), (half_width, 0))
    elif keep == "right":
        half_width = (width + 1) // 2
        x0 = width - half_width
        half = image.crop((x0, 0, width, height))
        if width > 1:
            mirrored = half.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            result.alpha_composite(mirrored.crop((0, 0, half_width - (width % 2), height)), (0, 0))
        result.alpha_composite(half, (x0, 0))
    elif keep == "top":
        half_height = (height + 1) // 2
        half = image.crop((0, 0, width, half_height))
        result.alpha_composite(half, (0, 0))
        if height > 1:
            mirrored = half.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            result.alpha_composite(mirrored.crop((0, height % 2, width, half_height)), (0, half_height))
    elif keep == "bottom":
        half_height = (height + 1) // 2
        y0 = height - half_height
        half = image.crop((0, y0, width, height))
        if height > 1:
            mirrored = half.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            result.alpha_composite(mirrored.crop((0, 0, width, half_height - (height % 2))), (0, 0))
        result.alpha_composite(half, (0, y0))
    else:
        raise MediaOperationError("不支持的对称方向")
    return result


def transform_image(data: bytes, operation: str, options: MediaOptions) -> tuple[bytes, str]:
    """Apply a non-meme image transform to static or animated media."""

    transforms = {
        "invert": ("反色", _invert_rgba),
        "flip_horizontal": (
            "左右翻转",
            lambda image: image.convert("RGBA").transpose(Image.Transpose.FLIP_LEFT_RIGHT),
        ),
        "flip_vertical": (
            "上下翻转",
            lambda image: image.convert("RGBA").transpose(Image.Transpose.FLIP_TOP_BOTTOM),
        ),
        "rotate_clockwise": (
            "顺时针旋转",
            lambda image: image.convert("RGBA").transpose(Image.Transpose.ROTATE_270),
        ),
        "rotate_counterclockwise": (
            "逆时针旋转",
            lambda image: image.convert("RGBA").transpose(Image.Transpose.ROTATE_90),
        ),
        "mirror_left": ("左对称", lambda image: _mirror_half(image, "left")),
        "mirror_right": ("右对称", lambda image: _mirror_half(image, "right")),
        "mirror_top": ("上对称", lambda image: _mirror_half(image, "top")),
        "mirror_bottom": ("下对称", lambda image: _mirror_half(image, "bottom")),
    }
    try:
        label, transform = transforms[operation]
    except KeyError as exc:
        raise MediaOperationError("不支持的图片变换") from exc

    frames, durations, animated = _animation_frames(data, options)
    transformed = [transform(frame) for frame in frames]
    if animated:
        result, reduced = encode_animation(transformed, durations, options, "GIF")
        suffix = "，已为控制体积自动压缩" if reduced else ""
        return result, f"✅ {label}完成（{len(transformed)} 帧）{suffix}"

    output = io.BytesIO()
    transformed[0].save(output, format="PNG")
    return output.getvalue(), f"✅ {label}完成"


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
