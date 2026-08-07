"""AstrBot Alice GIF Toolbox plugin entry point.

Copyright (C) 2026 Whereis-Alice and AstrBot Plugin Authors.
Modified on 2026-07-18 from shskjw/astrbot_plugin_gifcaijian.
This independent AGPL-3.0-or-later fork fixes source-image resolution for
current AstrBot components and keeps the upstream GIF utility commands.
It also incorporates non-meme transform ideas from
lirundong093-glitch/astrbot_plugin_pic_toolbox.
See LICENSE for the complete license text.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Iterable
from urllib.parse import quote, unquote, urlparse

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
import astrbot.api.message_components as Comp
from astrbot.api.star import Context, Star, register

from .help_card import HelpCardAssetError, PLUGIN_DISPLAY_NAME, load_help_card
from .media_ops import (
    MediaOperationError,
    MediaOptions,
    age_image,
    change_gif_speed,
    crop_animation,
    crop_grid,
    decompose_animation,
    image_to_line_art,
    inspect_animation,
    make_single_image_gif,
    multi_image_to_gif,
    reverse_animation,
    sprite_sheet_to_animation,
    trim_animation,
    transform_image,
    video_to_animation,
)


PLUGIN_ID = "astrbot_plugin_gif_toolbox"
PLUGIN_VERSION = "v2.5.2"
PLUGIN_DESC = f"{PLUGIN_DISPLAY_NAME}（独立 Fork）：支持 GIF/APNG/WebP 变速、倒放、裁剪、信息查看、速查、合成与图像变换"
FORK_REPO = "https://github.com/Whereis-Alice/astrbot_plugin_gif_toolbox"
UPSTREAM_REPO = "https://github.com/shskjw/astrbot_plugin_gifcaijian"
PIC_TOOLBOX_REPO = "https://github.com/lirundong093-glitch/astrbot_plugin_pic_toolbox"

DEFAULT_MAX_INPUT_MB = 30.0
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_MAX_OUTPUT_MB = 10.0
DEFAULT_MAX_FORWARD_PARTS = 40
DEFAULT_MAX_MULTI_IMAGES = 20


@dataclass(frozen=True)
class SourceCandidate:
    """An image/video reference extracted from an AstrBot message component."""

    reference: str
    component: Any | None
    origin: str


@dataclass(frozen=True)
class AnimationCropRequest:
    """Validated whole-animation crop settings parsed from a command."""

    aspect_ratio: tuple[int, int] | None = None
    target_size: tuple[int, int] | None = None
    margins: tuple[int, int, int, int] | None = None
    anchor: str = "center"


@dataclass(frozen=True)
class AnimationTrimRequest:
    """Validated frame-range operation parsed from a trim command."""

    drop_first: int = 0
    drop_last: int = 0
    keep_range: tuple[int, int] | None = None


@dataclass(frozen=True)
class RuntimeSettings:
    """Validated configuration values used by handlers."""

    max_input_bytes: int
    timeout_seconds: int
    max_output_bytes: int
    max_side: int
    max_frames: int
    gif_max_colors: int
    default_output_format: str
    max_video_duration: float
    default_video_scale: float
    default_video_fps: int
    default_single_frame_duration_ms: int
    single_image_frame_count: int
    max_forward_parts: int
    max_multi_images: int
    enable_at_avatar: bool
    gif_speed_allow_frame_drop: bool

    def media_options(self) -> MediaOptions:
        return MediaOptions(
            max_side=self.max_side,
            max_frames=self.max_frames,
            gif_max_colors=self.gif_max_colors,
            max_output_bytes=self.max_output_bytes,
        )


@register(PLUGIN_ID, "Whereis-Alice (fork of shskjw)", PLUGIN_DESC, PLUGIN_VERSION, FORK_REPO)
class GifToolboxPlugin(Star):
    """Alice's GIF Toolbox commands with AstrBot 4.16+ source compatibility."""

    def __init__(self, context: Context, config: AstrBotConfig | dict[str, Any] | None = None) -> None:
        super().__init__(context, config)
        self.config = config or {}

    async def initialize(self) -> None:
        logger.info(
            "[%s] initialized; primary upstream: %s; transform reference: %s",
            PLUGIN_ID,
            UPSTREAM_REPO,
            PIC_TOOLBOX_REPO,
        )

    async def terminate(self) -> None:
        logger.info("[%s] terminated", PLUGIN_ID)

    def _config_value(self, key: str, default: Any) -> Any:
        getter = getattr(self.config, "get", None)
        if callable(getter):
            return getter(key, default)
        return default

    @staticmethod
    def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            result = int(value)
        except (TypeError, ValueError):
            result = default
        return max(minimum, min(maximum, result))

    @staticmethod
    def _as_float(value: Any, default: float, minimum: float, maximum: float) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError):
            result = default
        return max(minimum, min(maximum, result))

    @staticmethod
    def _as_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False
        return default

    def _settings(self) -> RuntimeSettings:
        format_name = str(self._config_value("output_format", "GIF")).upper()
        if format_name not in {"GIF", "APNG", "WEBP"}:
            format_name = "GIF"
        max_input_mb = self._as_float(
            self._config_value("max_input_size_mb", DEFAULT_MAX_INPUT_MB),
            DEFAULT_MAX_INPUT_MB,
            1.0,
            200.0,
        )
        max_output_mb = self._as_float(
            self._config_value("max_output_size_mb", DEFAULT_MAX_OUTPUT_MB),
            DEFAULT_MAX_OUTPUT_MB,
            1.0,
            100.0,
        )
        return RuntimeSettings(
            max_input_bytes=round(max_input_mb * 1024 * 1024),
            timeout_seconds=self._as_int(
                self._config_value("download_timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
                DEFAULT_TIMEOUT_SECONDS,
                5,
                300,
            ),
            max_output_bytes=round(max_output_mb * 1024 * 1024),
            max_side=self._as_int(self._config_value("max_image_side", 1280), 1280, 64, 4096),
            max_frames=self._as_int(self._config_value("max_frames", 160), 160, 2, 500),
            gif_max_colors=self._as_int(
                self._config_value("gif_max_colors", 256),
                256,
                2,
                256,
            ),
            default_output_format=format_name,
            max_video_duration=self._as_float(
                self._config_value("max_gif_duration", 10.0),
                10.0,
                0.5,
                120.0,
            ),
            default_video_scale=self._as_float(
                self._config_value("default_scale", 0.3),
                0.3,
                0.1,
                1.0,
            ),
            default_video_fps=self._as_int(
                self._config_value("default_fps", 10),
                10,
                1,
                60,
            ),
            default_single_frame_duration_ms=self._as_int(
                self._config_value("single_image_gif_duration_ms", 500),
                500,
                20,
                60_000,
            ),
            single_image_frame_count=self._as_int(
                self._config_value("single_image_gif_frame_count", 2),
                2,
                2,
                12,
            ),
            max_forward_parts=self._as_int(
                self._config_value("max_forward_parts", DEFAULT_MAX_FORWARD_PARTS),
                DEFAULT_MAX_FORWARD_PARTS,
                1,
                100,
            ),
            max_multi_images=self._as_int(
                self._config_value("max_multi_images", DEFAULT_MAX_MULTI_IMAGES),
                DEFAULT_MAX_MULTI_IMAGES,
                1,
                60,
            ),
            enable_at_avatar=self._as_bool(
                self._config_value("enable_at_avatar", True),
                True,
            ),
            gif_speed_allow_frame_drop=self._as_bool(
                self._config_value("gif_speed_allow_frame_drop", False),
                False,
            ),
        )

    @staticmethod
    def _message_chain(event: AstrMessageEvent) -> list[Any]:
        getter = getattr(event, "get_messages", None)
        if callable(getter):
            messages = getter()
            if isinstance(messages, list):
                return messages
        message_obj = getattr(event, "message_obj", None)
        messages = getattr(message_obj, "message", [])
        return messages if isinstance(messages, list) else []

    def _walk_components(self, items: Iterable[Any]) -> Iterable[Any]:
        """Yield nested message components, prioritising reply-chain attachments."""

        for item in items:
            if isinstance(item, Comp.Reply):
                chain = getattr(item, "chain", None)
                if isinstance(chain, list):
                    yield from self._walk_components(chain)
                continue
            if isinstance(item, Comp.Nodes):
                for node in getattr(item, "nodes", []) or []:
                    content = getattr(node, "content", None)
                    if isinstance(content, list):
                        yield from self._walk_components(content)
                continue
            if isinstance(item, Comp.Node):
                content = getattr(item, "content", None)
                if isinstance(content, list):
                    yield from self._walk_components(content)
                continue
            if isinstance(item, dict):
                kind = str(item.get("type", "")).lower()
                data = item.get("data")
                if kind == "reply":
                    chain = (data or {}).get("chain") if isinstance(data, dict) else item.get("chain")
                    if isinstance(chain, list):
                        yield from self._walk_components(chain)
                    continue
                if kind in {"node", "nodes"}:
                    nested: list[Any] = []
                    if isinstance(data, dict):
                        nested.extend(value for key in ("content", "messages") if isinstance((value := data.get(key)), list))
                    nested.extend(
                        value for key in ("content", "messages", "chain") if isinstance((value := item.get(key)), list)
                    )
                    for children in nested:
                        yield from self._walk_components(children)
                    continue
            yield item

    @staticmethod
    def _refs_from_component(item: Any, expected_type: str) -> list[str]:
        """Return all plausible source fields from a component or adapter dictionary."""

        refs: list[str] = []
        if isinstance(item, dict):
            kind = str(item.get("type", "")).lower()
            if kind and kind != expected_type:
                return refs
            data = item.get("data")
            data = data if isinstance(data, dict) else {}
            containers = (data, item)
            for container in containers:
                for key in ("path", "file", "url"):
                    value = container.get(key)
                    if isinstance(value, str) and value.strip():
                        refs.append(value.strip())
        else:
            component_type = getattr(getattr(item, "type", None), "value", getattr(item, "type", ""))
            if component_type and str(component_type).lower() != expected_type:
                return refs
            for key in ("path", "file", "url"):
                value = getattr(item, key, None)
                if isinstance(value, str) and value.strip():
                    refs.append(value.strip())

        unique: list[str] = []
        seen: set[str] = set()
        for reference in refs:
            if reference not in seen:
                unique.append(reference)
                seen.add(reference)
        return unique

    def _collect_sources(self, event: AstrMessageEvent, expected_type: str) -> list[SourceCandidate]:
        candidates: list[SourceCandidate] = []
        seen: set[str] = set()
        component_class = Comp.Image if expected_type == "image" else Comp.Video
        for item in self._walk_components(self._message_chain(event)):
            if not isinstance(item, (component_class, dict)):
                continue
            for reference in self._refs_from_component(item, expected_type):
                if reference in seen:
                    continue
                seen.add(reference)
                candidates.append(
                    SourceCandidate(
                        reference=reference,
                        component=item if isinstance(item, component_class) else None,
                        origin=expected_type,
                    )
                )
        return candidates

    def _at_target_id(self, event: AstrMessageEvent) -> str | None:
        """Return a numeric @ target suitable for the QQ avatar endpoint."""

        for item in self._walk_components(self._message_chain(event)):
            if isinstance(item, Comp.At):
                containers = (item,)
            elif isinstance(item, dict) and str(item.get("type", "")).lower() in {"at", "mention"}:
                data = item.get("data")
                containers = (data, item) if isinstance(data, dict) else (item,)
            else:
                continue
            for container in containers:
                for key in ("qq", "target", "user_id", "id"):
                    value = container.get(key) if isinstance(container, dict) else getattr(container, key, None)
                    target = str(value).strip() if value is not None else ""
                    if target.isdecimal():
                        return target
        return None

    def _at_avatar_candidate(self, event: AstrMessageEvent) -> SourceCandidate | None:
        # The qlogo endpoint identifies users by QQ number. AstrBot's OneBot
        # event exposes its adapter bot; do not accidentally treat numeric IDs
        # from another platform as QQ accounts.
        if getattr(event, "bot", None) is None:
            return None
        target = self._at_target_id(event)
        if target is None:
            return None
        return SourceCandidate(
            reference=f"https://q1.qlogo.cn/g?b=qq&nk={quote(target, safe='')}&s=640",
            component=None,
            origin="at-avatar",
        )

    @staticmethod
    def _local_path_from_reference(reference: str) -> Path | None:
        """Resolve an existing ordinary or file:// path without treating it as a URL."""

        try:
            plain_path = Path(reference)
            if plain_path.is_file():
                return plain_path
        except OSError:
            # Very long Base64 strings and malformed file identifiers are not
            # filesystem paths; let their dedicated resolvers handle them.
            pass
        parsed = urlparse(reference)
        if parsed.scheme.lower() != "file":
            return None
        raw_path = unquote(parsed.path)
        if parsed.netloc and parsed.netloc not in {"", "localhost"}:
            raw_path = f"//{parsed.netloc}{raw_path}"
        if re.match(r"^/[A-Za-z]:[\\/]", raw_path):
            raw_path = raw_path[1:]
        path = Path(raw_path)
        return path if path.is_file() else None

    @staticmethod
    def _decode_inline_data(reference: str, size_limit: int) -> bytes | None:
        payload = ""
        if reference.startswith("base64://"):
            payload = reference.removeprefix("base64://")
        elif reference.startswith("data:") and ";base64," in reference:
            payload = reference.split(";base64,", 1)[1]
        if not payload:
            return None
        try:
            decoded = base64.b64decode(payload, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise MediaOperationError("图片 Base64 数据无效") from exc
        if len(decoded) > size_limit:
            raise MediaOperationError("图片超过插件配置的输入体积限制")
        return decoded

    async def _download_http(self, reference: str, settings: RuntimeSettings) -> bytes:
        timeout = aiohttp.ClientTimeout(total=settings.timeout_seconds)
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; AstrBot Alice GIF Toolbox)",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }
        try:
            async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
                async with session.get(reference, headers=headers, allow_redirects=True) as response:
                    if response.status < 200 or response.status >= 300:
                        raise MediaOperationError(f"图片下载失败（HTTP {response.status}）")
                    content_length = response.content_length
                    if content_length is not None and content_length > settings.max_input_bytes:
                        raise MediaOperationError("图片超过插件配置的输入体积限制")
                    content = bytearray()
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        content.extend(chunk)
                        if len(content) > settings.max_input_bytes:
                            raise MediaOperationError("图片超过插件配置的输入体积限制")
                    if not content:
                        raise MediaOperationError("图片下载结果为空")
                    return bytes(content)
        except MediaOperationError:
            raise
        except asyncio.TimeoutError as exc:
            raise MediaOperationError("图片下载超时") from exc
        except aiohttp.ClientError as exc:
            raise MediaOperationError("图片下载连接失败") from exc

    async def _resolve_onebot_file(
        self,
        event: AstrMessageEvent,
        file_id: str,
        settings: RuntimeSettings,
    ) -> bytes | None:
        """Ask a OneBot-compatible adapter to expand a file ID when available."""

        api = getattr(getattr(event, "bot", None), "api", None)
        call_action = getattr(api, "call_action", None)
        if not callable(call_action):
            return None
        try:
            result = await call_action("get_file", file_id=file_id)
        except Exception as exc:
            logger.debug("[%s] adapter could not resolve file id %r: %s", PLUGIN_ID, file_id, exc)
            return None
        if not isinstance(result, dict):
            return None
        for key in ("path", "file", "url"):
            expanded = result.get(key)
            if not isinstance(expanded, str) or not expanded or expanded == file_id:
                continue
            try:
                return await self._read_reference(event, expanded, settings, allow_file_id=False)
            except MediaOperationError:
                continue
        return None

    async def _read_reference(
        self,
        event: AstrMessageEvent,
        reference: str,
        settings: RuntimeSettings,
        *,
        allow_file_id: bool = True,
    ) -> bytes:
        inline = self._decode_inline_data(reference, settings.max_input_bytes)
        if inline is not None:
            return inline

        path = self._local_path_from_reference(reference)
        if path is not None:
            try:
                size = path.stat().st_size
                if size > settings.max_input_bytes:
                    raise MediaOperationError("图片超过插件配置的输入体积限制")
                return await asyncio.to_thread(path.read_bytes)
            except OSError as exc:
                raise MediaOperationError("读取本地图片失败") from exc

        parsed = urlparse(reference)
        if parsed.scheme.lower() in {"http", "https"}:
            return await self._download_http(reference, settings)

        if allow_file_id:
            resolved = await self._resolve_onebot_file(event, reference, settings)
            if resolved is not None:
                return resolved
        raise MediaOperationError("图片来源不是可访问的 URL、文件路径或 Base64 数据")

    async def _load_image(self, event: AstrMessageEvent, settings: RuntimeSettings) -> bytes:
        candidates = self._collect_sources(event, "image")
        if not candidates and settings.enable_at_avatar:
            avatar = self._at_avatar_candidate(event)
            if avatar is not None:
                candidates = [avatar]
        if not candidates:
            raise MediaOperationError("未检测到图片。请直接发送图片、回复含图片的消息，或 @ 一位 QQ 用户")
        errors: list[str] = []
        for candidate in candidates:
            try:
                return await self._read_reference(event, candidate.reference, settings)
            except MediaOperationError as exc:
                errors.append(f"{candidate.reference[:80]!r}: {exc}")
        logger.warning("[%s] no usable image source: %s", PLUGIN_ID, "; ".join(errors))
        raise MediaOperationError("无法取得图片。请确认图片未过期，或直接重新发送原图")

    async def _load_all_images(self, event: AstrMessageEvent, settings: RuntimeSettings) -> list[bytes]:
        candidates = self._collect_sources(event, "image")[: settings.max_multi_images]
        if not candidates:
            raise MediaOperationError("未检测到图片。请直接发送图片或回复含图片的消息")
        results = await asyncio.gather(
            *(self._read_reference(event, candidate.reference, settings) for candidate in candidates),
            return_exceptions=True,
        )
        images: list[bytes] = []
        errors: list[str] = []
        for candidate, result in zip(candidates, results, strict=True):
            if isinstance(result, bytes):
                images.append(result)
            else:
                errors.append(f"{candidate.reference[:80]!r}: {result}")
        if not images:
            logger.warning("[%s] all image downloads failed: %s", PLUGIN_ID, "; ".join(errors))
            raise MediaOperationError("无法取得任何图片。请确认图片未过期，或直接重新发送原图")
        if errors:
            logger.info("[%s] skipped %d unavailable image source(s)", PLUGIN_ID, len(errors))
        return images

    async def _load_video_path(self, event: AstrMessageEvent, settings: RuntimeSettings) -> tuple[Path, bool]:
        candidates = self._collect_sources(event, "video")
        if not candidates:
            raise MediaOperationError("未检测到视频。请回复视频消息或与指令一起发送视频")
        errors: list[str] = []
        for candidate in candidates:
            local_path = self._local_path_from_reference(candidate.reference)
            if local_path is not None:
                if local_path.stat().st_size > settings.max_input_bytes:
                    raise MediaOperationError("视频超过插件配置的输入体积限制")
                return local_path, False
            try:
                content = await self._read_reference(event, candidate.reference, settings)
            except MediaOperationError as exc:
                errors.append(f"{candidate.reference[:80]!r}: {exc}")
                continue
            suffix = Path(urlparse(candidate.reference).path).suffix or ".mp4"
            handle = tempfile.NamedTemporaryFile(prefix=f"{PLUGIN_ID}_", suffix=suffix, delete=False)
            path = Path(handle.name)
            try:
                handle.write(content)
                handle.close()
                return path, True
            except OSError:
                handle.close()
                path.unlink(missing_ok=True)
                raise MediaOperationError("保存临时视频文件失败")
        logger.warning("[%s] no usable video source: %s", PLUGIN_ID, "; ".join(errors))
        raise MediaOperationError("无法取得视频。请确认视频未过期后重试")

    @staticmethod
    def _parse_factor(text: str, default: float = 2.0) -> float:
        match = re.search(r"(\d+(?:\.\d+)?)", text)
        if not match:
            return default
        return max(0.1, min(20.0, float(match.group(1))))

    @staticmethod
    def _parse_grid(text: str, default: tuple[int, int] = (6, 6)) -> tuple[int, int]:
        match = re.search(r"(\d+)\s*[*x×]\s*(\d+)", text, re.IGNORECASE)
        if not match:
            return default
        return int(match.group(1)), int(match.group(2))

    @staticmethod
    def _parse_margins(text: str) -> tuple[int, int, int, int]:
        values = {"top": 0, "bottom": 0, "left": 0, "right": 0}
        aliases = {"上": "top", "下": "bottom", "左": "left", "右": "right"}
        for direction, number in re.findall(r"([上下左右])?\s*边距\s*(\d+)", text):
            amount = min(10_000, int(number))
            if direction:
                values[aliases[direction]] = amount
            else:
                values = {key: amount for key in values}
        return values["left"], values["top"], values["right"], values["bottom"]

    @staticmethod
    def _parse_crop_anchor(text: str) -> str:
        normalized = text.replace("中央", "居中").replace("中心", "居中").replace("中间", "居中")
        for label, anchor in (
            ("左上", "top_left"),
            ("右上", "top_right"),
            ("左下", "bottom_left"),
            ("右下", "bottom_right"),
        ):
            if label in normalized:
                return anchor
        if "居中" in normalized:
            return "center"
        for label, anchor in (("左", "left"), ("右", "right"), ("上", "top"), ("下", "bottom")):
            if re.search(rf"(?<![上下左右]){label}(?![上下左右边])", normalized):
                return anchor
        return "center"

    @classmethod
    def _parse_animation_crop(cls, text: str) -> AnimationCropRequest:
        """Parse mutually exclusive ratio, size, or margin crop syntax."""

        ratio_match = re.search(r"(?:比例\s*)?(\d+)\s*[:：]\s*(\d+)", text)
        if ratio_match is None:
            ratio_match = re.search(r"比例\s*(\d+)\s*[*x×]\s*(\d+)", text, re.IGNORECASE)
        size_match = re.search(r"(?:尺寸|大小)\s*(\d+)\s*[*x×]\s*(\d+)", text, re.IGNORECASE)
        margins_requested = bool(re.search(r"([上下左右])?\s*边距\s*\d+", text))

        if sum((ratio_match is not None, size_match is not None, margins_requested)) != 1:
            raise MediaOperationError("请指定一种裁剪方式：比例 1:1、尺寸 512x512 或边距 20")

        anchor = cls._parse_crop_anchor(text)
        if ratio_match is not None:
            width, height = (int(value) for value in ratio_match.groups())
            if not 1 <= width <= 10_000 or not 1 <= height <= 10_000:
                raise MediaOperationError("裁剪比例的两个数必须在 1 到 10000 之间")
            return AnimationCropRequest(aspect_ratio=(width, height), anchor=anchor)
        if size_match is not None:
            width, height = (int(value) for value in size_match.groups())
            if not 1 <= width <= 4096 or not 1 <= height <= 4096:
                raise MediaOperationError("裁剪尺寸的宽高必须在 1 到 4096 像素之间")
            return AnimationCropRequest(target_size=(width, height), anchor=anchor)

        if anchor != "center":
            raise MediaOperationError("边距裁剪不支持位置参数")
        return AnimationCropRequest(margins=cls._parse_margins(text))

    @staticmethod
    def _parse_animation_trim(text: str) -> AnimationTrimRequest:
        """Parse leading/trailing frame removal or an inclusive frame range."""

        range_match = re.search(
            r"(?:第\s*)?(\d+)\s*(?:帧\s*)?(?:-|~|到|至)\s*(?:第\s*)?(\d+)\s*(?:帧)?",
            text,
        )
        first_match = re.search(r"前(?:面)?\s*(\d+)\s*(?:帧)?", text)
        last_match = re.search(r"后(?:面)?\s*(\d+)\s*(?:帧)?", text)
        if range_match is not None:
            if first_match is not None or last_match is not None:
                raise MediaOperationError("保留帧范围不能和前后删帧同时使用")
            start_frame, end_frame = (int(value) for value in range_match.groups())
            if not 1 <= start_frame <= 1_000_000 or not 1 <= end_frame <= 1_000_000:
                raise MediaOperationError("帧号必须在 1 到 1000000 之间")
            if start_frame > end_frame:
                raise MediaOperationError("起始帧不能大于结束帧")
            return AnimationTrimRequest(keep_range=(start_frame, end_frame))

        drop_first = int(first_match.group(1)) if first_match is not None else 0
        drop_last = int(last_match.group(1)) if last_match is not None else 0
        if (first_match is not None and not 1 <= drop_first <= 1_000_000) or (
            last_match is not None and not 1 <= drop_last <= 1_000_000
        ):
            raise MediaOperationError("要移除的帧数必须在 1 到 1000000 之间")
        if not drop_first and not drop_last:
            raise MediaOperationError("请指定前后删帧，例如 前10帧、后10帧 或 10-60帧")
        return AnimationTrimRequest(drop_first=drop_first, drop_last=drop_last)

    @staticmethod
    def _parse_duration_ms(text: str, default_ms: int) -> int:
        fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps\b", text, re.IGNORECASE)
        if fps_match:
            fps = float(fps_match.group(1))
            return round(1000 / max(0.1, min(60.0, fps)))
        seconds_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:秒|s)\b", text, re.IGNORECASE)
        if seconds_match:
            return max(20, min(60_000, round(float(seconds_match.group(1)) * 1000)))
        decimal_match = re.search(r"\b(0?\.\d+|\d+\.\d+)\b", text)
        if decimal_match:
            return max(20, min(60_000, round(float(decimal_match.group(1)) * 1000)))
        return default_ms

    @staticmethod
    def _video_options(text: str, settings: RuntimeSettings) -> tuple[float, float | None, int, float]:
        time_match = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:s|秒)?\s*[-~]\s*(\d+(?:\.\d+)?)\s*(?:s|秒)?",
            text,
            re.IGNORECASE,
        )
        start = float(time_match.group(1)) if time_match else 0.0
        end = float(time_match.group(2)) if time_match else None
        fps_match = re.search(r"(\d+)\s*fps\b", text, re.IGNORECASE)
        fps = int(fps_match.group(1)) if fps_match else settings.default_video_fps
        scale_match = re.search(r"\b(0\.\d+|1(?:\.0+)?)\b", text)
        scale = float(scale_match.group(1)) if scale_match else settings.default_video_scale
        return start, end, max(1, min(60, fps)), max(0.1, min(1.0, scale))

    @staticmethod
    def _image_result(event: AstrMessageEvent, text: str, data: bytes) -> Any:
        return event.chain_result([Comp.Plain(text), Comp.Image.fromBytes(data)])

    async def _change_speed(
        self,
        event: AstrMessageEvent,
        processing_factor: float,
        display_factor: float,
        action: str,
    ) -> AsyncIterator[Any]:
        settings = self._settings()
        yield event.plain_result(f"⏳ 正在处理 {action} {display_factor:g}倍...")
        try:
            source = await self._load_image(event, settings)
            result, detail = await asyncio.to_thread(
                change_gif_speed,
                source,
                processing_factor,
                settings.media_options(),
                settings.gif_speed_allow_frame_drop,
            )
            yield self._image_result(event, detail, result)
        except MediaOperationError as exc:
            yield event.plain_result(f"❌ {exc}")
        except Exception:
            logger.exception("[%s] GIF speed processing failed", PLUGIN_ID)
            yield event.plain_result("❌ GIF 处理失败，请稍后重试")
        finally:
            # Stop later matching handlers only after the final result has
            # passed through AstrBot's response stages.
            event.stop_event()

    @filter.command("加速")
    @filter.regex(r"(?:gif)?(?:加速|变快)\s*[*x×]?\s*(\d+(?:\.\d+)?)?")
    async def accelerate_gif(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """加速 GIF：回复动图后发送 加速 2。"""

        factor = self._parse_factor(event.message_str)
        async for result in self._change_speed(event, factor, factor, "加速"):
            yield result

    @filter.command("减速")
    @filter.regex(r"(?:gif)?(?:减速|变慢)\s*[*x×]?\s*(\d+(?:\.\d+)?)?")
    async def decelerate_gif(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """减速 GIF：回复动图后发送 减速 2。"""

        factor = self._parse_factor(event.message_str)
        async for result in self._change_speed(event, 1 / factor, factor, "减速"):
            yield result

    @filter.command("调速", priority=10)
    async def adjust_gif_speed(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """按目标倍率调速 GIF：调速 2 表示两倍速。"""

        factor = self._parse_factor(event.message_str)
        async for result in self._change_speed(event, factor, factor, "调速"):
            yield result

    @filter.command("倒放", alias={"gif倒放", "动图倒放"}, priority=10)
    async def reverse_gif(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """倒序播放 GIF/APNG/WebP 动图，保留每一帧原有时长。"""

        settings = self._settings()
        yield event.plain_result("⏳ 正在倒放动图...")
        try:
            result, message = await asyncio.to_thread(
                reverse_animation,
                await self._load_image(event, settings),
                settings.media_options(),
            )
            yield self._image_result(event, message, result)
        except MediaOperationError as exc:
            yield event.plain_result(f"❌ {exc}")
        except Exception:
            logger.exception("[%s] animation reverse failed", PLUGIN_ID)
            yield event.plain_result("❌ 动图倒放失败，请稍后重试")
        finally:
            event.stop_event()

    async def _apply_image_transform(
        self,
        event: AstrMessageEvent,
        operation: str,
        label: str,
    ) -> AsyncIterator[Any]:
        """Load one image and apply an in-memory static/animated transform."""

        settings = self._settings()
        yield event.plain_result(f"⏳ 正在处理{label}...")
        try:
            source = await self._load_image(event, settings)
            result, message = await asyncio.to_thread(
                transform_image,
                source,
                operation,
                settings.media_options(),
            )
            yield self._image_result(event, message, result)
        except MediaOperationError as exc:
            yield event.plain_result(f"❌ {exc}")
        except Exception:
            logger.exception("[%s] image transform failed: %s", PLUGIN_ID, operation)
            yield event.plain_result(f"❌ {label}失败，请稍后重试")
        finally:
            # AstrBot otherwise runs every matching command handler. Delay the
            # stop until the yielded final result has been sent.
            event.stop_event()

    @filter.command("反色", priority=10)
    async def invert_image(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """反转静态图或 GIF/APNG/WebP 的 RGB 颜色。"""

        async for result in self._apply_image_transform(event, "invert", "反色"):
            yield result

    @filter.command("顺时针", priority=10)
    async def rotate_clockwise(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """将静态图或动图顺时针旋转 90 度。"""

        async for result in self._apply_image_transform(event, "rotate_clockwise", "顺时针旋转"):
            yield result

    @filter.command("逆时针", priority=10)
    async def rotate_counterclockwise(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """将静态图或动图逆时针旋转 90 度。"""

        async for result in self._apply_image_transform(event, "rotate_counterclockwise", "逆时针旋转"):
            yield result

    @filter.command("左右翻转", priority=10)
    async def flip_horizontal(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """水平翻转静态图或 GIF/APNG/WebP。"""

        async for result in self._apply_image_transform(event, "flip_horizontal", "左右翻转"):
            yield result

    @filter.command("上下翻转", priority=10)
    async def flip_vertical(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """垂直翻转静态图或 GIF/APNG/WebP。"""

        async for result in self._apply_image_transform(event, "flip_vertical", "上下翻转"):
            yield result

    @filter.command("左对称", priority=10)
    async def mirror_left(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """保留左半边并镜像到右半边。"""

        async for result in self._apply_image_transform(event, "mirror_left", "左对称"):
            yield result

    @filter.command("右对称", priority=10)
    async def mirror_right(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """保留右半边并镜像到左半边。"""

        async for result in self._apply_image_transform(event, "mirror_right", "右对称"):
            yield result

    @filter.command("上对称", priority=10)
    async def mirror_top(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """保留上半边并镜像到下半边。"""

        async for result in self._apply_image_transform(event, "mirror_top", "上对称"):
            yield result

    @filter.command("下对称", priority=10)
    async def mirror_bottom(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """保留下半边并镜像到上半边。"""

        async for result in self._apply_image_transform(event, "mirror_bottom", "下对称"):
            yield result

    @filter.command("图片转gif", alias={"单图转gif"})
    async def single_image_to_gif(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """将单张图片包装为 GIF：图片转gif 0.5s。"""

        settings = self._settings()
        duration = self._parse_duration_ms(event.message_str, settings.default_single_frame_duration_ms)
        yield event.plain_result("⏳ 正在转换单张图片为 GIF...")
        try:
            source = await self._load_image(event, settings)
            result, message = await asyncio.to_thread(
                make_single_image_gif,
                source,
                duration,
                settings.single_image_frame_count,
                settings.media_options(),
            )
            yield self._image_result(event, message, result)
        except MediaOperationError as exc:
            yield event.plain_result(f"❌ {exc}")
        except Exception:
            logger.exception("[%s] single-image GIF conversion failed", PLUGIN_ID)
            yield event.plain_result("❌ 图片转 GIF 失败，请稍后重试")

    @filter.command("图片转线稿")
    async def image_to_line_art(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """将图片转换为本地线稿。"""

        settings = self._settings()
        yield event.plain_result("⏳ 正在生成线稿...")
        try:
            result = await asyncio.to_thread(
                image_to_line_art,
                await self._load_image(event, settings),
                settings.media_options(),
            )
            yield self._image_result(event, "✅ 线稿生成完成", result)
        except MediaOperationError as exc:
            yield event.plain_result(f"❌ {exc}")
        except Exception:
            logger.exception("[%s] line-art processing failed", PLUGIN_ID)
            yield event.plain_result("❌ 线稿处理失败，请稍后重试")

    async def _sprite_sheet(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        settings = self._settings()
        rows, columns = self._parse_grid(event.message_str)
        duration = self._parse_duration_ms(event.message_str, 100)
        margins = self._parse_margins(event.message_str)
        yield event.plain_result(
            f"⏳ 正在按 {rows} 行 {columns} 列合成 GIF（每帧 {duration}ms）..."
        )
        try:
            result, message = await asyncio.to_thread(
                sprite_sheet_to_animation,
                await self._load_image(event, settings),
                rows,
                columns,
                duration,
                settings.media_options(),
                settings.default_output_format,
                margins,
            )
            yield self._image_result(
                event,
                f"{message}（输出：{settings.default_output_format}）",
                result,
            )
        except MediaOperationError as exc:
            yield event.plain_result(f"❌ {exc}")
        except Exception:
            logger.exception("[%s] sprite-sheet processing failed", PLUGIN_ID)
            yield event.plain_result("❌ 精灵图合成失败，请稍后重试")

    @filter.command("合成1gif")
    async def make_gif_v1(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """按行优先把精灵图合成为动画。"""

        async for result in self._sprite_sheet(event):
            yield result

    @filter.command("合成2gif")
    async def make_gif_v2(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """兼容旧指令的精灵图合成入口。"""

        async for result in self._sprite_sheet(event):
            yield result

    @filter.command("多图合成gif")
    async def multi_image_gif(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """将消息或回复中的多张图片合成为 GIF。"""

        settings = self._settings()
        duration = self._parse_duration_ms(event.message_str, 500)
        yield event.plain_result("⏳ 正在下载图片并合成 GIF...")
        try:
            images = await self._load_all_images(event, settings)
            result, message = await asyncio.to_thread(
                multi_image_to_gif,
                images,
                duration,
                settings.media_options(),
            )
            yield self._image_result(event, message, result)
        except MediaOperationError as exc:
            yield event.plain_result(f"❌ {exc}")
        except Exception:
            logger.exception("[%s] multi-image GIF processing failed", PLUGIN_ID)
            yield event.plain_result("❌ 多图合成失败，请稍后重试")

    @filter.command("gif裁剪", alias={"动图裁剪"}, priority=10)
    async def crop_animation_gif(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """整体裁剪 GIF/APNG/WebP：gif裁剪 1:1 或 gif裁剪 尺寸 512x512。"""

        settings = self._settings()
        try:
            request = self._parse_animation_crop(event.message_str)
            yield event.plain_result("⏳ 正在整体裁剪动图...")
            result, message = await asyncio.to_thread(
                crop_animation,
                await self._load_image(event, settings),
                settings.media_options(),
                aspect_ratio=request.aspect_ratio,
                target_size=request.target_size,
                margins=request.margins,
                anchor=request.anchor,
            )
            yield self._image_result(event, message, result)
        except MediaOperationError as exc:
            yield event.plain_result(f"❌ {exc}")
        except Exception:
            logger.exception("[%s] animation crop processing failed", PLUGIN_ID)
            yield event.plain_result("❌ 动图裁剪失败，请稍后重试")
        finally:
            # Keep this distinct from the grid-crop command if another plugin
            # also has a broad image-cropping handler.
            event.stop_event()

    @filter.command("gif截取", alias={"动图截取"}, priority=10)
    async def trim_animation_gif(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """按帧截取动图：gif截取 前10帧、后10帧或 10-60帧。"""

        settings = self._settings()
        try:
            request = self._parse_animation_trim(event.message_str)
            yield event.plain_result("⏳ 正在按帧截取动图...")
            result, message = await asyncio.to_thread(
                trim_animation,
                await self._load_image(event, settings),
                settings.media_options(),
                drop_first=request.drop_first,
                drop_last=request.drop_last,
                keep_range=request.keep_range,
            )
            yield self._image_result(event, message, result)
        except MediaOperationError as exc:
            yield event.plain_result(f"❌ {exc}")
        except Exception:
            logger.exception("[%s] animation trim processing failed", PLUGIN_ID)
            yield event.plain_result("❌ 动图截取失败，请稍后重试")
        finally:
            event.stop_event()

    @filter.command("gif信息", alias={"动图信息", "gif详情"}, priority=10)
    async def inspect_gif(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """查看 GIF/APNG/WebP 的帧数、尺寸、时长、循环和文件元数据。"""

        settings = self._settings()
        try:
            yield event.plain_result("⏳ 正在读取动图信息...")
            message = await asyncio.to_thread(inspect_animation, await self._load_image(event, settings))
            yield event.plain_result(message)
        except MediaOperationError as exc:
            yield event.plain_result(f"❌ {exc}")
        except Exception:
            logger.exception("[%s] animation info inspection failed", PLUGIN_ID)
            yield event.plain_result("❌ 动图信息读取失败，请稍后重试")
        finally:
            event.stop_event()

    @filter.command("裁剪")
    async def crop_and_forward(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """按网格裁剪图片：裁剪 2x3 边距 8。"""

        settings = self._settings()
        rows, columns = self._parse_grid(event.message_str, (1, 1))
        if rows * columns > settings.max_forward_parts:
            yield event.plain_result(f"❌ 分块数量不能超过 {settings.max_forward_parts}")
            return
        yield event.plain_result(f"⏳ 正在裁剪为 {rows}×{columns}...")
        try:
            parts = await asyncio.to_thread(
                crop_grid,
                await self._load_image(event, settings),
                rows,
                columns,
                self._parse_margins(event.message_str),
                settings.max_forward_parts,
            )
            nodes = [
                Comp.Node(
                    name=PLUGIN_DISPLAY_NAME,
                    content=[Comp.Plain(f"裁剪结果 {index + 1}/{len(parts)}"), Comp.Image.fromBytes(part)],
                )
                for index, part in enumerate(parts)
            ]
            yield event.chain_result([Comp.Nodes(nodes=nodes)])
        except MediaOperationError as exc:
            yield event.plain_result(f"❌ {exc}")
        except Exception:
            logger.exception("[%s] crop processing failed", PLUGIN_ID)
            yield event.plain_result("❌ 图片裁剪失败，请稍后重试")

    @filter.command("gif分解")
    async def decompose_gif(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """将 GIF/APNG/WebP 动图分解为 PNG 帧。"""

        settings = self._settings()
        yield event.plain_result("⏳ 正在分解动画帧...")
        try:
            frames = await asyncio.to_thread(
                decompose_animation,
                await self._load_image(event, settings),
                settings.media_options(),
            )
            nodes = [
                Comp.Node(
                    name=PLUGIN_DISPLAY_NAME,
                    content=[Comp.Plain(f"第 {index + 1} 帧"), Comp.Image.fromBytes(frame)],
                )
                for index, frame in enumerate(frames)
            ]
            yield event.chain_result([Comp.Nodes(nodes=nodes)])
        except MediaOperationError as exc:
            yield event.plain_result(f"❌ {exc}")
        except Exception:
            logger.exception("[%s] decomposition failed", PLUGIN_ID)
            yield event.plain_result("❌ 动图分解失败，请稍后重试")

    @filter.command("表情包做旧")
    @filter.regex(r"(?:表情包?)?做旧\s*(\d+)?")
    async def age_meme(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """模拟重复转发造成的做旧效果：表情包做旧 10。"""

        settings = self._settings()
        match = re.search(r"做旧\s*(\d+)", event.message_str)
        times = int(match.group(1)) if match else 5
        times = max(1, min(50, times))
        yield event.plain_result(f"⏳ 正在做旧（{times} 次）...")
        try:
            result, message = await asyncio.to_thread(
                age_image,
                await self._load_image(event, settings),
                times,
                settings.media_options(),
            )
            yield self._image_result(event, message, result)
        except MediaOperationError as exc:
            yield event.plain_result(f"❌ {exc}")
        except Exception:
            logger.exception("[%s] image-aging processing failed", PLUGIN_ID)
            yield event.plain_result("❌ 表情包做旧失败，请稍后重试")

    @filter.command("视频转gif")
    async def video_to_gif(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """转换视频片段：视频转gif 1s-4s fps 10 0.5。"""

        settings = self._settings()
        start, end, fps, scale = self._video_options(event.message_str, settings)
        yield event.plain_result("⏳ 正在下载并转换视频...")
        temporary = False
        path: Path | None = None
        try:
            path, temporary = await self._load_video_path(event, settings)
            result, message = await asyncio.to_thread(
                video_to_animation,
                path,
                start,
                end,
                fps,
                scale,
                settings.media_options(),
                settings.default_output_format,
                settings.max_video_duration,
            )
            yield self._image_result(event, message, result)
        except MediaOperationError as exc:
            yield event.plain_result(f"❌ {exc}")
        except Exception:
            logger.exception("[%s] video conversion failed", PLUGIN_ID)
            yield event.plain_result("❌ 视频转 GIF 失败，请稍后重试")
        finally:
            if temporary and path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("[%s] could not remove temporary video %s", PLUGIN_ID, path)

    @filter.command(
        "爱丽丝的GIF工具箱帮助",
        alias={"gif工具箱帮助", "gif速查", "动图速查"},
        priority=10,
    )
    async def gif_toolbox_help(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """发送包含全部命令、别名与关键参数的 PNG 速查图。"""

        try:
            card = await asyncio.to_thread(load_help_card)
            yield self._image_result(event, f"{PLUGIN_DISPLAY_NAME}命令速查", card)
        except HelpCardAssetError:
            logger.exception("[%s] packaged help card is unavailable", PLUGIN_ID)
            yield event.plain_result("❌ 命令速查图片不可用，请查看插件 README")
        finally:
            event.stop_event()
