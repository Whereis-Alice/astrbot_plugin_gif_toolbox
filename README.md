# GIF 工具箱（独立 Fork）

这是一个 AstrBot 图片与动图处理插件，基于上游
[shskjw/astrbot_plugin_gifcaijian](https://github.com/shskjw/astrbot_plugin_gifcaijian)
的功能方向重构而来。

v2.1.0 的通用图片变换与可选 GIF 丢帧调速参考了
[lirundong093-glitch/astrbot_plugin_pic_toolbox](https://github.com/lirundong093-glitch/astrbot_plugin_pic_toolbox)。
本插件只吸收其非表情包、非特效的图片处理能力；摸头、发射、杀、操你、抽你及其素材、OpenCV/numpy 依赖均未引入。

本 Fork 的唯一插件标识为 astrbot_plugin_gif_toolbox，展示名为“GIF 工具箱（独立 Fork）”。
它可以与上游 astrbot_plugin_gifcaijian 同时安装，不会覆盖上游目录、配置或插件更新记录。

## 解决的问题

上游在多数命令中把图片来源直接当作 HTTP URL 下载。当前 AstrBot 图片组件常把实际来源放在
file 或 path 字段，也可能是 file:/// 本地缓存路径、base64:// 数据或 OneBot 文件 ID。
这些情况会使 加速 2.0 等命令在显示“正在处理”后只返回“下载失败”。

本插件使用统一的来源解析顺序，支持：

- 直接图片、回复图片、合并转发中的图片
- 本地路径和 file:/// 缓存路径
- base64://... 和 data:image/...;base64,... 
- HTTP(S) 链接（含重定向、超时和流式体积限制）
- OneBot 适配器可解析的文件 ID
- 变换类命令在没有图片时可选用 `@QQ用户` 的头像作为回退来源

失败会写入带原因的插件日志，用户侧会得到可操作的提示，而不是静默吞掉异常。

## 功能与指令

| 指令 | 用法 | 说明 |
| --- | --- | --- |
| 图片转 GIF | 图片转gif 0.5s 或 单图转gif 2fps | 将单张静态图片转换为真实 GIF 容器。 |
| GIF 变速 | 回复动图后发送 加速 2、减速 2 或 调速 2（倍率范围 0.1~20） | 默认保留全部帧并重新编码为 GIF；可开启配置通过均匀丢帧突破 20ms 最小帧间隔。 |
| 基础变换 | 反色、顺时针、逆时针、左右翻转、上下翻转 | 静态图输出 PNG，GIF/APNG/WebP 输出 GIF；支持直接图片、回复图片或可选 `@QQ用户` 头像。 |
| 对称变换 | 左对称、右对称、上对称、下对称 | 保留指定半边并镜像到另一侧，支持静态图与动图。 |
| 精灵图合成 | 合成1gif 6x6 0.1s 边距 8 | 依次切分网格并合成为动画。合成2gif 作为旧指令兼容入口保留。 |
| 多图合成 | 多图合成gif 0.5s | 将当前消息、回复或转发中的多张图片按顺序合成 GIF。 |
| 网格裁剪 | 裁剪 2x3 边距 8 | 将图片切为合并转发的 PNG 小图。 |
| 动图分解 | gif分解 | 将 GIF/APNG/WebP 动图分解为 PNG 帧。 |
| 图片线稿 | 图片转线稿 | 本地边缘检测，不依赖外部 API。 |
| 表情包做旧 | 表情包做旧 10 | 对静态图或动图模拟重复压缩转发效果。 |
| 视频转动画 | 视频转gif 1s-4s fps 10 0.5 | 回复视频或随指令发送视频；依赖 imageio 的 FFmpeg 支持。 |
| 帮助 | gif工具箱帮助 | 显示主要指令。 |

所有需要输入文件的命令都支持“直接发送图片/视频”或“回复包含文件的消息后发送指令”。

## 安装

1. 将本目录放进 AstrBot 的 data/plugins/astrbot_plugin_gif_toolbox。
2. 在 AstrBot 的 Python 环境安装依赖：

   ~~~powershell
   pip install -r data/plugins/astrbot_plugin_gif_toolbox/requirements.txt
   ~~~

3. 重载插件或重启 AstrBot。
4. 在插件配置面板调整输入/输出体积、最大帧数、视频默认参数等设置。

> 首次使用 视频转gif 前，请确认当前 Python 环境能使用 imageio[ffmpeg]。图片和 GIF 功能不依赖外部图床或 API。
>
> 依赖声明兼容 AstrBot 4.26.6 的 Pillow 12.2.0；请让 AstrBot 管理其核心 Pillow 版本，不要手动降级它。

## 配置重点

- max_input_size_mb：下载、本地文件和 Base64 的单文件最大体积。
- max_output_size_mb：输出过大时会自动依次缩放图片、减少 GIF 调色板颜色。
- max_image_side 与 max_frames：防止高分辨率或超长动图耗尽内存。
- single_image_gif_duration_ms 与 single_image_gif_frame_count：控制单图转 GIF 的默认节奏。
- output_format：精灵图合成和视频转动画可选 GIF、APNG、WEBP；其他兼容性优先的命令固定输出 GIF。
- enable_at_avatar：没有直接图片或回复图片时，变换类命令能否下载被 @ 的 QQ 用户头像；非 QQ 平台可关闭。
- gif_speed_allow_frame_drop：默认关闭。开启后，调速在无法把每帧压到 20ms 以下时均匀丢帧，以接近目标倍速；会牺牲中间画面流畅度。

## Fork、标识与许可证

- 上游项目：[shskjw/astrbot_plugin_gifcaijian](https://github.com/shskjw/astrbot_plugin_gifcaijian)
- 上游提交基线：beffa3ebc4c6d2c36b8c5825643dc3d0d1057ced（2026-01-26）
- 图片变换参考：[lirundong093-glitch/astrbot_plugin_pic_toolbox](https://github.com/lirundong093-glitch/astrbot_plugin_pic_toolbox)（仅反色、旋转、翻转、对称和 GIF 调速思路）
- 本插件标识：astrbot_plugin_gif_toolbox
- Fork 仓库：[Whereis-Alice/astrbot_plugin_gif_toolbox](https://github.com/Whereis-Alice/astrbot_plugin_gif_toolbox)

上游使用 GNU Affero General Public License v3.0。这个修改版同样以
AGPL-3.0-or-later 发布，并在 LICENSE 中附带完整许可证。若将本插件提供给其他用户或公开部署，
请依照 AGPL 要求提供对应修改版源码，并保留上游归属说明。

上述图片工具箱的表情包/特效类功能和资源不在本插件范围内，未被合入。
