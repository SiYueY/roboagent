# Chat 示例

这是一个最小的流式、多轮浏览器聊天示例。它展示 `ChatModel`、`Agent`、
`AgentSession`、`AgentRun` 与 `MessageDeltaEvent` 如何组合；页面使用 Gradio
实现，但 Gradio 不是该示例的核心能力。示例以 Gradio 6 为基线，不兼容 Gradio 5。

示例不包含 Tool、Skill、MCP、模型切换、文件上传或认证。

## 页面交互

页面采用左侧会话栏、主消息区和底部输入区的布局。可在当前浏览器页面中新建和切换
会话；每个会话持有独立的 `AgentSession`，不会通过页面 history 重建 transcript。
首条消息会自动成为会话标题。会话仅保存在页面内存中，刷新浏览器后会清空。

页面代码与应用入口分离：`ui.py` 保存 Gradio 组件、浏览器会话状态和页面回调；
`app.py` 仅负责加载配置、创建 Agent 并启动服务。`frontend.js` 保存侧栏、语音面板与浏览器
Media API 行为；它通过 Gradio 的页面 `<head>` 在组件挂载后初始化浏览器 API。`style.css` 只负责
响应式布局和视觉层级，不包含 RoboAgent 运行时行为。

### 语音通话模式

输入区的“语音通话”会在同一会话中切换到底部语音控制面板，不会创建新页面或新会话。麦克风
按钮会请求浏览器音频权限，字幕只显示临时测试文本；扬声器使用浏览器生成的短提示音验证静音与
恢复。挂断会释放麦克风与音频资源并恢复文本输入区。

该模式仅验证浏览器端 UI 和 Media API：不会把语音或视频发送给 Agent。摄像头画面只在当前
浏览器中本地预览，不上传、不录制，也不包含 ASR、TTS、WebSocket 或 WebRTC。可从语音面板
开关摄像头，并在视频开启时使用右上角按钮切换前后摄像头。

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
uv sync --extra gradio
uv run python examples/chat/app.py
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
