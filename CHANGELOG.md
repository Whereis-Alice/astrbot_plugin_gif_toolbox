# 更新日志

## v2.1.0 - 2026-07-27

- 参考 lirundong093-glitch/astrbot_plugin_pic_toolbox，新增长期通用图片变换：反色、顺时针/逆时针旋转、左右/上下翻转，以及左/右/上/下对称；静态图和 GIF/APNG/WebP 均可处理。
- 新增 调速 [倍率] 指令，并加入 gif_speed_allow_frame_drop 配置。默认保持全部帧；开启后会在 20ms 最小帧间隔下均匀丢帧，以获得更高的实际加速倍率。
- 变换类命令可选用 @ QQ 用户头像作为没有图片输入时的回退来源，可通过 enable_at_avatar 关闭。
- 未引入上游的摸头、发射、操你、抽你、杀等表情包/特效功能、素材或 OpenCV/numpy 依赖。

## v2.0.1 - 2026-07-18

- 移除 Pillow 的小于 12 上限，使插件与 AstrBot 4.26.6 核心固定的 Pillow 12.2.0 兼容；安装时不会再触发核心依赖降级保护。

## v2.0.0 - 2026-07-18

- 从 shskjw/astrbot_plugin_gifcaijian 建立独立 Fork，插件唯一名改为 astrbot_plugin_gif_toolbox，不会覆盖或跟随上游同名插件更新。
- 重写图片来源解析：支持 AstrBot 当前的 path、file、url、file:///、base64://、Data URI、HTTP(S) 和可用的 OneBot 文件 ID。
- 修复“加速 2.0 倍”显示“下载失败”的根因：不再仅对 url 字段发起 HTTP 请求，也不再吞掉所有下载异常。
- 下载加入超时、状态码、流式大小上限和清晰日志；输出动画加入帧数、像素和体积控制。
- 新增 图片转gif（别名 单图转gif），可将静态图片生成实际 GIF 文件。
- 保留上游的 GIF 变速、精灵图合成、裁剪、分解、多图合成、线稿、做旧和视频转动画功能，并拆分为更易维护的处理模块。
