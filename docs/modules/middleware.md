# Middleware Module Design

## 1. 模块定位

`middleware` 模块负责 RoboAgent 运行时的横切逻辑。

它不负责创建模型、注册工具、发现 Skill 或持久化数据，而是在
LangChain Agent 执行过程中拦截模型调用、工具调用和 agent 生命周期
事件。

当前已包含：

- `SkillContextMiddleware`
- `ToolErrorHandlingMiddleware`
- `RunJournalMiddleware`
- `build_runtime_middlewares(...)`

## 2. 设计职责

`middleware` 模块应承担：

- 将 active skills 转换为模型可见上下文
- 将 tool 执行异常归一化为稳定 `ToolMessage`
- 在运行时记录 agent/model/tool 边界事件
- 根据 `RuntimeFeatures` 组装默认 middleware 链

`middleware` 模块不应承担：

- 配置文件解析
- 模型实例创建
- Skill discovery 或 registry 管理
- Tool registry 管理
- 事件持久化后端选择

## 3. 当前结构

- `skill_context.py`
  `SkillContextMiddleware` 通过 `wrap_model_call` / `awrap_model_call`
  修改 `ModelRequest.system_message`，注入 active skill context。
- `tool_error.py`
  `ToolErrorHandlingMiddleware` 通过 `wrap_tool_call` / `awrap_tool_call`
  捕获异常并返回 `ToolMessage(status="error")`。
- `run_journal.py`
  `RunJournalMiddleware` 记录 agent、model、tool 的粗粒度运行事件。
- `builder.py`
  `build_runtime_middlewares(...)` 根据 `RuntimeFeatures` 组装默认链。

## 4. 依赖方向

允许依赖：

- `middleware -> skill`
- `middleware -> runtime`
- `middleware -> langchain`

禁止依赖：

- `middleware -> agent`
- `middleware -> config`
- `middleware -> model factory`

原因：

- middleware 应是运行时拦截层，不应反向依赖 agent 装配入口
- config-driven 选择应发生在 `agent.factory`
- 事件写入接口由 `runtime` 提供，middleware 只消费接口

## 5. 后续演进建议

- 增加 `GuardrailMiddleware`
- 增加 retry / timeout / approval middleware
- 将 `SkillContextMiddleware` 从 system message 注入升级为更细粒度的
  state/context 注入
- 将 `RunJournalMiddleware` 与持久化 store、trace id、token usage 连接
