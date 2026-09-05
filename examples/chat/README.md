# Chat 示例

这是一个最小的流式、多轮浏览器聊天示例。它展示 `Model`、`Agent`、
`Session`、`Run` 与 canonical Run event 如何组合；页面使用 Gradio
实现，但 Gradio 不是该示例的核心能力。示例以 Gradio 6 为基线，不兼容 Gradio 5。

示例不包含 Tool、Skill、MCP、模型切换、文件上传或认证。

## 页面交互

页面采用左侧会话栏、主消息区和底部输入区的布局。可在当前浏览器页面中新建和切换
会话；每个会话持有独立的 `Session`，不会通过页面 history 重建 transcript。
首条消息会自动成为会话标题。会话仅保存在页面内存中，刷新浏览器后会清空。

页面代码与应用入口分离：`ui.py` 保存 Gradio 组件、浏览器会话状态和页面回调；
`app.py` 仅负责加载配置、创建 Agent 并启动服务。`frontend.js` 保存侧栏、语音面板与浏览器
Media API 行为；它通过 Gradio 的页面 `<head>` 在组件挂载后初始化浏览器 API。`style.css` 只负责
响应式布局和视觉层级，不包含 RoboAgent 运行时行为。

### 语音通话模式

输入区的“语音通话”会在同一会话中切换到底部语音控制面板，不会创建新页面或新会话。麦克风
按钮会请求浏览器音频权限；浏览器先进行低通重采样，再用二进制帧发送 PCM。服务端将同一份
降噪音频供 VAD 和 ASR 使用。挂断会释放麦克风与音频资源并恢复文本输入区。

该模式使用同源 WebSocket 发送 16 kHz PCM16 麦克风音频，经通义实时 ASR、当前对话的
`Session` 与通义实时 TTS 后回放 24 kHz PCM16 音频。语音转写和回答会写入当前文字会话；
用户在播放期间再次说话会取消未完成的回答与播放。摄像头画面仍只在浏览器本地预览，不上传或录制。

## 配置

从仓库根目录复制示例配置：

```bash
cp config.example.yaml config.yaml
cp .env.example .env
```

在 `.env` 中填写 Qwen3.7-Flash 的 API Key：

```dotenv
DASHSCOPE_API_KEY=your-dashscope-api-key
```

`config.yaml` 使用 `qwen3.7-flash` 和 `${DASHSCOPE_API_KEY}`。RoboAgent 配置
加载器会统一展开该变量；终端中已设置的同名环境变量优先于 `.env`。不要提交
`.env`。

## 启动

```bash
uv sync --extra gradio --extra speech
bash scripts/deploy_silero_vad.sh
uv run python examples/chat/app.py
```

`speech` extra 同时安装通义 SDK、RNNoise、SOXR、ONNX Runtime 和 Uvicorn 的 WebSocket 支持；若服务日志出现
`No supported WebSocket library detected`，请重新执行上面的 `uv sync` 命令后再启动。

默认 `audio.processor: rnnoise` 会在服务端执行降噪；`vad.provider: silero` 使用本地 ONNX
模型。将 Silero 模型放在 `roboagent/speech/audio/data/silero_vad.onnx`，或在配置中设置
`speech.vad.model_path`（也可使用 `ROBOAGENT_SILERO_VAD_MODEL`）。模型不可用时会记录明确警告，并
优先使用 RNNoise 产生的人声概率、再退化到能量门限；在生产环境可设置 `speech.vad.required: true`
禁止该退化。

`scripts/deploy_silero_vad.sh` 默认从 Silero 官方 `master` 下载 16 kHz ONNX 模型，并以原子方式
写入上述默认路径；已有有效模型不会被覆盖，使用 `--force` 才会刷新。脚本会保存 SHA-256 到本地
清单，便于审计，但该值不是固定版本信任锚，因为模型来源按 `master` 更新。可用
`bash scripts/deploy_silero_vad.sh --verify-only` 仅校验现有模型。

语音回答默认在累计 16 个字符后即提交首个 TTS 片段，后续以 48 个字符或句末优先断句；同一通话
会复用通义实时 TTS 连接，以缩短首音延迟。可在 `speech.tts.first_chunk_chars` 与
`speech.tts.chunk_chars` 调整这两个值（前者不得大于后者）。
`speech.tts.volume` 默认是 `100`；若要降低播放音量，请优先调低该值而不是放大浏览器 PCM。

播放期间先需要连续 300 ms 的有效语音（置信度至少 0.55、输入音量至少 0.003）形成候选；只有
ASR 返回可识别的 partial/final 文本后才真正停止声音，以兼顾正常说话打断和扬声器回声抑制。
可通过 `speech.turn.interruption` 三项按设备调整。

资源受限环境可设置 `audio.processor: passthrough` 和 `vad.provider: energy`。不要重新打开
`autoGainControl`：它常会在静音间隙放大风扇和机械噪声。Krisp 属于独立厂商 SDK/模型接入，当前
配置位保留但不会随 `speech` extra 安装。

如果 `7860` 已被其他本地服务占用，可改用其他端口：

```bash
ROBOAGENT_CHAT_PORT=7861 uv run python examples/chat/app.py
```

如需使用其他配置文件，设置 `ROBOAGENT_CONFIG_PATH`；RoboAgent 会读取该
配置文件同目录的 `.env`。

## 局域网访问

服务绑定 `0.0.0.0:7860`。本机访问 `http://127.0.0.1:7860`。其他电脑或
手机先在服务端执行：

```bash
hostname -I
```

再访问 `http://<局域网-IP>:7860`，例如 `http://192.168.1.100:7860`。仅应在
可信局域网开放该服务；必要时在防火墙中放行 7860 端口。

## 局域网 HTTPS（自签名证书）

麦克风和摄像头均需要浏览器安全上下文。可使用示例自带脚本为当前局域网 IP 生成本地自签名
证书；将 IP 替换为 `hostname -I` 显示的实际无线网卡地址：

```bash
chmod +x examples/chat/generate_cert.sh
examples/chat/generate_cert.sh 192.168.10.166
uv run python examples/chat/app.py
```

脚本生成 `examples/chat/certs/roboagent-cert.pem` 与私钥；应用检测到这两个文件后会自动
使用 HTTPS，访问地址为 `https://<局域网-IP>:7860`。Gradio 会仅对本机启动健康检查跳过
该自签名证书的校验，使服务能正常启动；证书目录已被 Git 忽略，脚本不会覆盖已有证书或私钥。

自签名证书不会被手机浏览器自动信任：若浏览器仍显示连接不安全，它不会被视为可调用
麦克风的安全上下文。要在手机上使用真实麦克风，仍需信任该证书，或改用具有受信任
证书的公网 HTTPS 地址。
