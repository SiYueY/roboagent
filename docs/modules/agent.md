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

当前模块中的 [agent.py](/home/siyuey/workspace/openclaw/roboagent/roboagent/agent/agent.py) 已提供 `create_roboagent()` 入口，这说明该模块目前定位为组装层而非执行层。

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

- `agent.py`
  对外暴露 Agent 构建入口
- `builder.py`
  负责复杂构建逻辑与参数归一化
- `factory.py`
  负责默认依赖装配
- `types.py`
  负责 Agent 相关类型定义
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

当前 `create_roboagent()` 直接调用外部 `create_agent()`，说明系统仍处于轻量封装阶段。

这意味着后续需要逐步补齐：

- `Skill` 到 runtime prompt 或 tool schema 的转换逻辑
- 默认 middleware 组装
- runtime validation
- 更清晰的 Agent factory 分层

## 6. 后续演进建议

- 将简单构造函数升级为 `AgentBuilder`
- 显式区分 `tool`、`skill`、`middleware` 的注入逻辑
- 为 `skills` 增加转换层，而不是直接透传 runtime object
- 把 provider 相关逻辑与 Agent 核心构造解耦
