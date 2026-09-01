# RoboAgent Context 模块设计

## 目标

Context 模块回答的是：

> 下一次模型调用应该看到哪些 Session 消息？

它位于 Session 与模型之间，不拥有 Session 历史，也不执行 Tool、Memory 或规划。

```text
Session messages
      ↓
ContextManager
      ↓
ModelContext
      ↓
ContextTransform
      ↓
Model
```

`AgentSession.messages` 仍是完整事实记录；`ModelContext.messages` 只是一次调用的工作视图。

## Phase 1

目录保持最小：

```text
roboagent/context/
├── __init__.py
└── manager.py
```

统一接口直接返回项目已有的 `ModelContext`：

```python
class ContextManager(Protocol):
    async def prepare(
        self,
        *,
        system_prompt: str | None,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
        cancellation: CancellationToken,
    ) -> ModelContext:
        ...
```

提供两个实现：

* `FullContextManager` 原样构造 `ModelContext`，是 `Agent` 的默认值，保持现有完整历史行为。
* `WindowContextManager(max_messages=64)` 在超出消息数预算时保留最新的完整消息组。

窗口的消息组规则：普通消息单独成组；带 tool calls 的 assistant 消息与其后连续的 tool result 消息为一个组。裁剪只能保留或删除整个组，不能使用 `messages[-N:]` 拆开工具交互。若最新组本身超过预算，仍整体保留。

`Agent` 只暴露 `context_manager`，不暴露窗口预算等零散参数：

```python
Agent(
    model=model,
    context_manager=WindowContextManager(max_messages=64),
)
```

`ContextTransform` 保持为 ContextManager 之后的应用层定制入口。ContextManager 不修改传入的 Session transcript。

## 延后实现

以下能力在确认长会话需要保留旧信息后再引入：

* Context state；
* LLM summary / compaction；
* Token budget；
* Memory、RAG、机器人运行时状态。

届时再评估是否需要在 `prepare()` 返回值中加入 state；Phase 1 不预建这些抽象。

## 测试重点

* 默认完整透传兼容性；
* 窗口裁剪；
* 单个和多个 tool call 的完整性；
* Context 不修改 Session transcript；
* ContextTransform 的调用顺序。
