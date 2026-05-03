# Agent Module Design

## 1. 模块定位

`agent` 模块负责定义 Agent 运行时入口与组装逻辑。

它的核心职责不是承载业务能力本身，而是把以下对象组装为一个可运行 Agent：

- `model`
- `tools`
- `middlewares`
- `skills`
- system prompt
- runtime graph 或 execution pipeline

当前模块中的 `create_roboagent_runtime()` 是配置驱动入口，`AgentBuilder`
是纯参数组装入口，这说明该模块目前定位为组装层而非执行层。

## 2. 设计职责

`agent` 模块应承担：

- Agent 构建入口
- Agent 默认配置组装
- Agent 依赖注入边界
- `Skill` 与 `tool` 的接入桥接
- runtime graph 创建

`agent` 模块不应承担：

- `Skill` discovery 与 registry 管理
- 配置文件解析
- 具体领域能力实现
- 长期状态存储实现

## 3. 建议结构

推荐子结构：

- `builder.py`
  对外暴露纯参数 Agent 构建入口，负责构建逻辑与参数归一化
- `factory.py`
  负责从 `AppConfig` 和 `RuntimeContext` 装配默认依赖
- `features.py`
  负责声明 runtime feature flags
- `runtime.py`
  负责运行时 graph 或 execution flow 封装

## 4. 依赖方向

推荐依赖方向：

- `agent -> config`
- `agent -> skill`
- `agent -> external runtime framework`

禁止依赖方向：

- `skill -> agent`
- `config -> agent`

原因：

- `agent` 是上层组装模块
- `skill` 是能力子系统，不应反向依赖具体 Agent 实现
- `config` 是基础模块，应保持独立

## 5. 当前实现观察

当前 `AgentBuilder.build()` 调用外部 `create_agent()`，并在调用前完成 skill context 与 tool resolution。

这意味着后续需要逐步补齐：

- `Skill` 到 runtime prompt 或 tool schema 的转换逻辑
- runtime validation
- runtime factory 到 middleware/runtime store 的接入

## 6. 后续演进建议

- 显式区分 `tool`、`skill`、`middleware` 的注入逻辑
- 继续将 `skills` 的模型上下文转换逻辑放在 middleware 层
- 通过 `RuntimeFeatures.run_journal` 接入 run/event 观测能力
- 把 provider 相关逻辑与 Agent 核心构造解耦
