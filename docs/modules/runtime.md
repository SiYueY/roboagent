# Runtime Module Design

## 1. 模块定位

`runtime` 模块负责 RoboAgent 的运行记录与事件流。

它是执行观测层，不是 Agent 组装层，也不是业务能力层。当前实现为
本地内存版本，用于测试、调试和后续持久化接口稳定。

## 2. 设计职责

`runtime` 模块应承担：

- run record 的创建、查询和状态更新
- run event 的写入和查询
- 为 middleware 提供稳定的事件写入接口
- 为后续 JSONL / SQLite / remote store 提供抽象边界

`runtime` 模块不应承担：

- Agent graph 创建
- Tool 或 Skill 执行
- 模型调用
- middleware 链组装

## 3. 当前结构

- `runtime.runs`
  - `RunStatus`
  - `RunRecord`
  - `RunManager`
- `runtime.events`
  - `RunEvent`
  - `RunEventStore`
  - `MemoryRunEventStore`

`RunManager` 当前是线程安全内存 registry。`MemoryRunEventStore` 当前使用
每个 `thread_id` 独立递增的 `seq`，并支持按 run 查询事件和按 thread
查询 message 类事件。

## 4. 运行时事件

当前 `RunJournalMiddleware` 记录以下事件：

- `agent_start`
- `agent_end`
- `model_start`
- `model_end`
- `model_error`
- `tool_start`
- `tool_end`

事件统一包含：

- `seq`
- `thread_id`
- `run_id`
- `event_type`
- `category`
- `content`
- `metadata`
- `created_at`

## 5. 依赖方向

推荐依赖方向：

- `agent.factory -> runtime`
- `middleware -> runtime`

禁止依赖方向：

- `runtime -> agent`
- `runtime -> middleware`
- `runtime -> skill`
- `runtime -> tool`

原因：

- runtime 是底层观测接口，应保持独立
- middleware 负责写事件，runtime 不应知道写入者来自哪一层

## 6. 后续演进建议

- 增加 JSONL event store
- 增加 SQLite event store
- 增加 run cancellation / interruption 语义
- 增加 token usage、latency、tool result metadata
- 增加 run cleanup、pagination 和 persistent recovery
