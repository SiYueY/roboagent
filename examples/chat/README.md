# Chat 示例

这是一个最小的流式、多轮浏览器聊天示例。它展示 `ChatModel`、`Agent`、
`AgentSession`、`AgentRun` 与 `MessageDeltaEvent` 如何组合；页面使用 Gradio
实现，但 Gradio 不是该示例的核心能力。

示例不包含 Tool、Skill、MCP、模型切换、文件上传或认证。

## 页面交互

页面采用左侧会话栏、主消息区和底部输入区的布局。可在当前浏览器页面中新建和切换
会话；每个会话持有独立的 `AgentSession`，不会通过页面 history 重建 transcript。
首条消息会自动成为会话标题。会话仅保存在页面内存中，刷新浏览器后会清空。

页面代码与应用入口分离：`ui.py` 保存 Gradio 组件、浏览器会话状态和页面回调；
`app.py` 仅负责加载配置、创建 Agent 并启动服务。`style.css` 只负责响应式布局和视觉层级，
不包含 RoboAgent 运行时行为。

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
