"""AstrBot GIF Toolbox plugin entry point.

Copyright (C) 2026 Whereis-Alice and AstrBot Plugin Authors.
Modified on 2026-07-18 from shskjw/astrbot_plugin_gifcaijian.
This independent AGPL-3.0-or-later fork fixes source-image resolution for
current AstrBot components and keeps the upstream GIF utility commands.
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
from urllib.parse import unquote, urlparse

import aiohttp

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
import astrbot.api.message_components as Comp
from astrbot.api.star import Context, Star, register

from .media_ops import (
    MediaOperationError,
    MediaOptions,
    age_image,
    change_gif_speed,
    crop_grid,
    decompose_animation,
    image_to_line_art,
    make_single_image_gif,
    multi_image_to_gif,
    sprite_sheet_to_animation,
    video_to_animation,
)


PLUGIN_ID = "astrbot_plugin_gif_toolbox"
PLUGIN_VERSION = "v2.0.0"
PLUGIN_DESC = "独立 Fork 的 GIF/APNG/WebP 图片工具箱：可靠下载、变速、裁剪、合成与单图转 GIF"
FORK_REPO = "https://github.com/Whereis-Alice/astrbot_plugin_gif_toolbox"
UPSTREAM_REPO = "https://github.com/shskjw/astrbot_plugin_gifcaijian"

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

    def media_options(self) -> MediaOptions:
        return MediaOptions(
            max_side=self.max_side,
            max_frames=self.max_frames,
            gif_max_colors=self.gif_max_colors,
            max_output_bytes=self.max_output_bytes,
        )


@register(PLUGIN_ID, "Whereis-Alice (fork of shskjw)", PLUGIN_DESC, PLUGIN_VERSION, FORK_REPO)
class GifToolboxPlugin(Star):
    """GIF utility commands with AstrBot 4.16+ image-source compatibility."""

    def __init__(self, context: Context, config: AstrBotConfig | dict[str, Any] | None = None) -> None:
        super().__init__(context, config)
        self.config = config or {}

    async def initialize(self) -> None:
        logger.info("[%s] initialized; upstream: %s", PLUGIN_ID, UPSTREAM_REPO)

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
            "User-Agent": "Mozilla/5.0 (compatible; AstrBot GIF Toolbox)",
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
        if not candidates:
            raise MediaOperationError("未检测到图片。请直接发送图片或回复一条含图片的消息")
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
            )
            suffix = "，已为控制体积自动压缩" if "自动压缩" in detail else ""
            message = f"✅ GIF 已{action}至 {display_factor:g}倍{suffix}"
            yield self._image_result(event, message, result)
        except MediaOperationError as exc:
            yield event.plain_result(f"❌ {exc}")
        except Exception:
            logger.exception("[%s] GIF speed processing failed", PLUGIN_ID)
            yield event.plain_result("❌ GIF 处理失败，请稍后重试")

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
                    name="GIF 工具箱",
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
                    name="GIF 工具箱",
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

    @filter.command("gif工具箱帮助")
    async def gif_toolbox_help(self, event: AstrMessageEvent) -> AsyncIterator[Any]:
        """显示 GIF 工具箱的主要命令和 AGPL 源码提示。"""

        yield event.plain_result(
            "GIF 工具箱命令：\n"
            "• 图片转gif [0.5s]（别名：单图转gif）\n"
            "• 加速 [倍数] / 减速 [倍数]（回复动图）\n"
            "• 合成1gif / 合成2gif [6x6] [0.1s] [边距 8]\n"
            "• 多图合成gif [0.5s]、裁剪 [2x3]、gif分解\n"
            "• 图片转线稿、表情包做旧 [次数]、视频转gif [1s-4s fps 10 0.5]\n\n"
            f"这是 {UPSTREAM_REPO} 的独立 AGPL-3.0-or-later Fork。"
            "当前版本源码请向机器人管理员索取，管理员发布时应提供其 Fork 仓库。"
        )
