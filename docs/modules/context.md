# RoboAgent Context 模块设计

## 1. 目标与边界

当前 RoboAgent 已经具备较完整的基础 Agent Runtime：

```text
Agent
  ↓
AgentSession
  ↓
AgentRun
  ↓
Agent Loop
  ↓
Model → Tool → Model → ...
```

现阶段的主要问题是：

```text
Session transcript
        =
Model context
```

即每次模型调用基本直接使用完整的 Session 消息历史。

短会话下这种方式足够简单，但随着机器人 Agent 逐步引入：

* 多轮语音交互；
* Tool 调用及执行结果；
* 导航和操作任务；
* Perception observation；
* 机器人运行状态；
* 错误与恢复信息；

Session 中的消息会持续增长，最终产生：

* Context Window 超限；
* Token 消耗持续增加；
* 推理延迟增加；
* 大量历史信息对当前任务无价值；
* Tool Call / Tool Result 裁剪容易破坏消息结构。

因此需要增加独立的：

```text
roboagent/context
```

用于解决：

> **下一次模型调用应该看到什么？**

而不是：

> 这个 Session 曾经发生过什么？

### 核心职责划分

最终应明确以下几个概念：

```text
Agent
    定义 Agent 能做什么

Session
    保存实际发生过什么

Context
    决定模型当前需要知道什么

Run
    管理一次正在执行的 Agent 运行

Model
    执行模型推理

Tool
    执行动作
```

最重要的边界是：

```text
Session != Context
AgentContext != ModelContext
```

其中：

* `AgentSession` 是完整历史的事实源；
* `AgentContext` 是面向当前推理的工作上下文；
* `ModelContext` 是模型 Provider 最终接收的调用参数。

Context 模块第一阶段不负责：

```text
long-term memory
vector database
embedding
RAG
knowledge graph
planning
sub-agent
robot state acquisition
```

这些能力以后可以接入 Context，但不属于 Context 核心本身。

---

## 2. 总体架构

推荐的数据流：

```text
                Agent
         model / tools / policy
                  │
                  ▼
             AgentSession
          full transcript
                  │
                  ▼
           ContextManager
        select / trim / compact
                  │
                  ▼
            AgentContext
                  │
                  ▼
            ModelContext
                  │
                  ▼
              ChatModel
```

Context 模块只位于：

```text
Session
   ↓
Model
```

之间，不侵入 Tool 和其他 Runtime 模块。

建议目录保持克制：

```text
roboagent/
├── agent/
│   ├── agent.py
│   ├── session.py
│   ├── run.py
│   ├── loop.py
│   └── types.py
│
├── context/
│   ├── __init__.py
│   ├── context.py
│   ├── manager.py
│   └── compaction.py
│
├── model/
├── runtime/
├── skill/
└── tool/
```

当前阶段不要提前增加：

```text
providers/
pipelines/
stores/
processors/
policies/
context_graph/
```

只有出现多个真实实现后再拆分。

---

## 3. 核心设计

### 3.1 AgentContext

`AgentContext` 表示当前模型需要使用的 Agent 工作上下文。

```python
@dataclass(frozen=True, slots=True)
class AgentContext:
    messages: tuple[Message, ...]
    summary: str | None = None
```

第一版保持两个字段即可。

其中：

* `messages`：当前需要保留的原始消息；
* `summary`：已经压缩的历史上下文。

不要把这些运行数据塞进去：

```text
session_id
run_id
model
tools
token_count
robot_state
metadata
```

它们不是 Agent Context 的核心组成。

---

### 3.2 SessionContextState

Session 本身继续保存完整 transcript，同时增加少量 Context 状态：

```python
@dataclass(frozen=True, slots=True)
class SessionContextState:
    summary: str | None = None
    compacted_until: int = 0
```

假设：

```text
messages[0:50]
```

已经被压缩，那么：

```text
summary
        ≈
messages[0:50]
```

模型下一次实际使用：

```text
summary
+
messages[50:]
```

但原始：

```text
messages[0:]
```

仍然完整保存在 Session 中。

因此：

```text
Session transcript
    append-only logical history

SessionContextState
    optimized context cursor
```

这样既能支持 Context 优化，又不会失去：

* Debug；
* Replay；
* Evaluation；
* Failure analysis；
* Agent trajectory；
* 后续训练数据。

---

### 3.3 ContextResult

ContextManager 一次处理同时需要返回：

* 当前模型上下文；
* 更新后的 Context State。

推荐：

```python
@dataclass(frozen=True, slots=True)
class ContextResult:
    context: AgentContext
    state: SessionContextState
```

这样避免不断增加裸 tuple。

---

### 3.4 ContextManager

核心接口：

```python
class ContextManager(Protocol):

    async def prepare(
        self,
        messages: Sequence[Message],
        state: SessionContextState,
        cancellation: CancellationToken,
    ) -> ContextResult:
        ...
```

默认实现：

```python
DefaultContextManager
```

负责：

```text
完整 transcript
      ↓
读取 context state
      ↓
判断是否超出预算
      ↓
安全裁剪 / 压缩
      ↓
AgentContext
```

ContextManager 不负责：

```text
model invocation
tool execution
session persistence
planning
memory retrieval
robot state acquisition
```

### Agent 中的配置

不要把大量 Context 参数直接塞进 Agent：

```python
Agent(
    max_context_messages=64,
    keep_context_messages=24,
    ...
)
```

而应：

```python
Agent(
    model=model,
    context_manager=DefaultContextManager(
        max_messages=64,
        keep_recent=24,
    ),
)
```

这样 Agent 公共 API 继续保持稳定。

---

### 3.5 Context Budget

第一阶段不建议自己实现 Provider tokenizer。

先支持简单的 Message Budget：

```python
DefaultContextManager(
    max_messages=64,
    keep_recent=24,
)
```

基本逻辑：

```text
active messages <= 64
        ↓
直接使用

active messages > 64
        ↓
保留最近约 24 条
```

后续再升级为：

```text
message budget
+
token budget
```

Token 计算最好最终由 Model 层提供能力，而不是 ContextManager 自己理解每种模型 tokenizer。

---

### 3.6 Tool Call 完整性

Context 裁剪不能简单按：

```python
messages[-24:]
```

因为 Tool Calling 消息存在结构关系：

```text
Assistant(tool call A)
ToolResult(A)
```

必须作为一个逻辑整体。

例如：

```text
User

Assistant(tool A)

Tool A

Assistant(tool B, C)

Tool B
Tool C

Assistant(final)
```

应划分为：

```text
[User]

[Assistant(tool A), Tool A]

[Assistant(tool B, C), Tool B, Tool C]

[Assistant(final)]
```

Context trimming 必须按 Message Group 处理。

必须保证：

```text
ToolResult
```

不会失去对应 Tool Call；

同样也不能保留 Tool Call，却删除其 Tool Result。

第一阶段可以把 grouping 做成 `manager.py` 内部实现，不需要新增公共 `MessageGroup` 类型。

---

### 3.7 Compaction

Context Window 只能解决“删掉什么”，Compaction 用于解决：

> 删掉的旧历史中，哪些重要信息仍然值得保留？

例如：

```text
m1
m2
...
m40
m41
...
m64
```

变成：

```text
summary(m1 ... m40)

m41
...
m64
```

因此：

```text
OLD HISTORY
      ↓
 compact
      ↓
 SUMMARY

RECENT HISTORY
      ↓
 unchanged
```

Summary 不应该伪装成：

```text
UserMessage
AssistantMessage
```

因为它并不是真实发生的 conversation event。

应继续作为：

```python
AgentContext.summary
```

存在，并在构造 ModelContext 时加入 system context，例如：

```text
System Prompt

Previous session context:
...

Conversation:
...
```

### Incremental Compaction

Compaction 必须增量进行：

```text
previous summary
+
newly compacted messages
          ↓
new summary
```

而不是每次：

```text
所有历史消息
     ↓
重新总结
```

这样才能适合长期运行 Agent。

可定义：

```python
class ContextCompactor(Protocol):

    async def compact(
        self,
        messages: Sequence[Message],
        previous_summary: str | None,
        cancellation: CancellationToken,
    ) -> str:
        ...
```

但建议它在第二阶段再正式引入。

---

## 4. Runtime 集成

### 4.1 AgentSession

当前 AgentSession 是完整 transcript 的 owner。

继续保持这个设计，只增加：

```python
context_state: SessionContextState
```

即：

```text
AgentSession
├── messages
├── context_state
└── active run
```

ContextManager 不直接修改 Session。

---

### 4.2 Agent Loop

当前逻辑大致是：

```text
messages
    ↓
ModelContext
    ↓
context transforms
    ↓
model
```

改为：

```text
messages
    ↓
ContextManager.prepare()
    ↓
AgentContext
    ↓
ModelContext
    ↓
ContextTransform
    ↓
ChatModel
```

现有 `ContextTransform` 建议继续保留。

两者职责不同：

```text
ContextManager
    framework-level lifecycle

ContextTransform
    application-level customization
```

因此 ContextManager 不应取代 ContextTransform。

---

### 4.3 ModelContext 构建

每轮：

```python
context_result = await context_manager.prepare(
    messages,
    context_state,
    cancellation,
)

context_state = context_result.state

agent_context = context_result.context
```

然后：

```python
model_context = ModelContext(
    system_prompt=build_system_prompt(
        system_prompt,
        agent_context.summary,
    ),
    messages=agent_context.messages,
    tools=definitions,
)
```

之后再运行现有：

```python
context_transforms
```

最后调用模型。

---

### 4.4 Run 与 Session Commit

Run 启动时使用 Session Context State 的 snapshot。

执行过程中：

```text
run-local context state
```

不断更新。

Run 完成后，与 transcript 一起提交：

```python
session._commit(
    messages,
    context_state,
)
```

建议同时把现有 Agent Loop 返回的裸 tuple 改成：

```python
@dataclass(frozen=True, slots=True)
class AgentLoopResult:
    final_message: AssistantMessage | None
    status: AgentRunStatus
    error: str | None
    context_state: SessionContextState
```

这样以后 Agent Loop 再扩展不会不断增加 tuple 元素。

---

### 4.5 Cancellation

Context prepare 和 compaction 都必须支持：

```python
CancellationToken
```

尤其未来 Compaction 可能需要额外一次模型调用：

```text
Compaction Model Request
        ↓
用户取消
        ↓
立即结束
```

不能让隐藏的 summary 请求绕开 AgentRun 的 cancellation 生命周期。

---

## 5. 分阶段实现

Context 模块不建议一次全部完成。

### Phase 1：建立 Context 边界

第一阶段只解决：

```text
Session != Context
```

实现：

```text
context/
├── context.py
└── manager.py
```

核心能力：

* `AgentContext`；
* `SessionContextState`；
* `ContextResult`；
* `ContextManager`；
* `DefaultContextManager`；
* Message Group；
* safe sliding window；
* Agent / Session / Loop 集成。

第一阶段不要调用额外 LLM。

超出 Context Window 后直接安全裁剪旧 Message Group。

这样可以首先验证整个架构边界。

---

### Phase 2：Compaction

增加：

```text
context/compaction.py
```

实现：

* `ContextCompactor`；
* incremental summary；
* previous summary + newly compacted messages；
* optional automatic compaction；
* `context_compacted` Event。

Compaction 应作为可选能力：

```python
DefaultContextManager(
    max_messages=64,
    keep_recent=24,
    compactor=compactor,
)
```

没有 compactor：

```text
safe sliding window
```

有 compactor：

```text
summary + recent messages
```

---

### Phase 3：Token Budget

在 Message Budget 稳定以后再增加：

```text
max_input_tokens
compact_at_tokens
```

建议 Model 提供：

```text
context window
token estimation
```

ContextManager 只消费这些能力。

避免 Context 模块自己维护不同 Provider 的 tokenizer。

---

### Phase 4：机器人 Runtime Context

等真实需求出现后，再考虑：

```text
robot pose
battery
navigation state
task state
perception summary
```

这些内容通常不应该永久追加进 Session transcript。

未来可以扩展：

```python
AgentContext(
    messages=...,
    summary=...,
    runtime_context=...,
)
```

或者在多个真实来源出现后再抽象：

```text
ContextSource
```

不要提前设计 Context Provider Framework。

---

### Future：Long-term Memory

Memory 最终的数据流可以是：

```text
Session History ───────────┐
                           │
Session Summary ───────────┤
                           ├→ ContextManager
Long-term Memory ──────────┤
                           │
Robot Runtime State ───────┘
```

即：

```text
Memory
   ↓
Context
   ↓
Model
```

Context 是 Memory 的消费者，而不是 Memory 本身。

---

## 6. 约束、测试与最终形态

### 核心不变量

Context 实现必须保证：

```text
1. Session transcript is authoritative.

2. ContextManager never mutates the Session transcript.

3. AgentContext is only a model-facing working view.

4. Tool Call and Tool Result relationships remain valid.

5. Recent working context cannot be accidentally compacted away.

6. ContextManager does not execute normal Agent reasoning.

7. Long-term Memory does not belong to Context.
```

### 测试重点

第一阶段至少覆盖：

```text
empty context
single message
below max_messages
exactly max_messages
above max_messages

single tool call
multiple tool calls
tool error
tool-call grouping integrity

Session transcript immutability
Context state update
Run commit

Cancellation
```

第二阶段增加：

```text
initial compaction
incremental compaction
previous summary merge
compaction cancellation
compaction failure
```

### 推荐测试目录

```text
tests/
├── context/
│   ├── test_manager.py
│   ├── test_grouping.py
│   └── test_compaction.py
│
└── agent/
    ├── test_loop.py
    ├── test_run.py
    └── test_session.py
```

---

## 最终结构

Context 模块成熟以后仍然应该保持很小：

```text
context/
├── __init__.py
├── context.py
├── manager.py
└── compaction.py
```

未来只有出现真实需求以后，最多再增加：

```text
source.py
```

而不应该演变成一个独立 Context Framework。

最终 RoboAgent 的核心关系应保持：

```text
Agent
    capability definition

Session
    complete history

Context
    current working knowledge

Run
    execution lifecycle

Model
    reasoning backend

Tool
    external action
```

其中 Context 模块当前最重要的目标只有两个：

```text
1. Session 与 Model Context 解耦

2. 为未来 Compaction / Memory / Robot State
   建立稳定的接入边界
```

因此当前开发顺序建议严格保持：

```text
Phase 1
Context boundary
+ safe sliding window

        ↓

Phase 2
incremental compaction

        ↓

Phase 3
token budget

        ↓

Phase 4
robot runtime context

        ↓

Future
long-term memory
```

不要在第一版 Context 中同时引入 Memory、RAG、Planning 或复杂 Provider 体系。Context 本身应该继续符合 RoboAgent 的核心设计方向：**简单、明确、可扩展，但不过度抽象。**
