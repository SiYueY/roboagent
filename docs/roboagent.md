# RoboAgent v1 Agent Runtime Kernel 设计

## 1. 定位与设计原则

RoboAgent v1 是一个：

> **模型无关、工具无关、可嵌入的异步 Agent Runtime Kernel。**

它定义：

> **Agent 如何运行。**

而不定义：

> **Agent 应该如何安全地操作机器人、浏览器、代码仓库、数据库或其他业务系统。**

因此，v1 负责的核心能力包括：

* Agent / Session / Run 生命周期；
* canonical transcript；
* ModelContext 与 RunContext；
* Model streaming；
* Tool Call 执行；
* sequential / parallel tool execution；
* cancel / steer / follow-up；
* timeout / max turns；
* transcript commit；
* runtime state；
* lifecycle events；
* `continue_run()`；
* execution policy。

以下能力不属于 v1 Kernel：

```text
机器人安全
硬件资源互斥
审批与权限
浏览器安全
代码沙箱

持久化 Memory
RAG
MCP

Handoff
Sub-Agent
Multi-Agent

Gateway
Cron
Telemetry Backend
```

这些能力未来通过：

```text
RunContext
ToolExecutionPolicy
Hooks
Events
Tool abstraction
```

在 Kernel 之外实现。

### 1.1 参考框架

RoboAgent 不直接复制某一个 Agent 框架，而是综合借鉴：

| 框架                   | 主要参考                                                              |
| -------------------- | ----------------------------------------------------------------- |
| Pi Agent Core        | Agent Loop、steering、follow-up、continue、tool batch、runtime control |
| OpenAI Agents Python | Agent / Run 分层、RunContext、RunConfig、policy boundary               |
| smolagents           | structured execution state / result                               |
| Hermes Agent         | interrupt、skills、memory、subagent 等长期扩展边界                          |

总体原则：

> **Pi 提供运行语义基线，OpenAI Agents 提供职责边界；RoboAgent 保持轻量、Pythonic、可嵌入。**

---

# 2. Runtime 对象模型

RoboAgent v1 固定以下对象职责：

```text
Agent        immutable reusable definition
Session      canonical transcript owner
Run          one execution lifecycle

ModelContext model-visible input
RunContext   local runtime context
RunState     transient execution state
RunControl   external control plane

AgentLoop    runtime orchestration
ToolExecutor tool batch execution
```

整体关系：

```text
                    Agent
                      │
                      ▼
                   Session
             canonical transcript
                      │
                      ▼
                     Run
                      │
       ┌──────────────┼──────────────┐
       ▼              ▼              ▼
  RunContext      RunState      RunControl
       │              │              │
       └──────────────┼──────────────┘
                      ▼
                 AgentLoop
              ┌───────┴───────┐
              ▼               ▼
       ContextManager     ToolExecutor
              │               │
              ▼               ▼
        ModelContext          Tool
              │
              ▼
             Model
```

## 2.1 Agent

`Agent` 是：

> **不可变、可复用的 Agent Definition。**

包含：

```text
model
tools
system_prompt
context_manager
hooks
default_run_config
```

不包含：

```text
current turn
current response
current RunState
run task
pending controls
tool execution task
```

因此，一个 Agent 可以同时服务多个 Session：

```text
Agent
├── Session A
│   ├── Run 1
│   └── Run 2
└── Session B
    └── Run 1
```

---

## 2.2 AgentSession

`AgentSession` 是：

> **Conversation lifecycle owner。**

主要负责：

```text
session_id
canonical transcript
active-run ownership

start()
run()
continue_run()
```

其中：

> **`Session.messages` 是唯一 canonical conversation transcript。**

对外只能暴露：

```text
immutable snapshot
或
read-only view
```

不能允许外部直接修改内部 transcript。

### Session 原子性

`start()` 必须遵循：

```text
acquire active-run ownership
        ↓
validate session state
        ↓
normalize / validate UserMessage
        ↓
commit initial UserMessage
        ↓
create AgentRun
        ↓
start execution task
```

如果：

```text
active Run 冲突
输入非法
Session 状态非法
```

则：

> **不得修改 transcript。**

v1 只保证同一 event loop 内的并发安全。

默认不保证：

```text
cross-thread
cross-event-loop
```

调用安全。

如果应用需要跨线程控制 AgentRun，应自行将操作 marshal 到 Run 所属 event loop。

---

## 2.3 AgentRun

`AgentRun` 表示：

> **一次 Agent execution。**

推荐异步启动：

```python
run = session.start(
    "Inspect the repository",
    config=config,
)
```

观察事件：

```python
async for event in run.events():
    ...
```

等待结果：

```python
result = await run.result()
```

运行中可以：

```python
run.steer("Do not modify files")
run.follow_up("Then summarize the result")
run.cancel()
```

Convenience API：

```python
result = await session.run(
    "Inspect the repository",
    config=config,
)
```

语义等价于：

```python
run = session.start(prompt, config=config)
return await run.result()
```

因此：

```text
start() → AgentRun
run()   → RunResult
```

### Eager Start

`session.start()`：

> **立即启动 execution task。**

不是 lazy start。

因此：

* active-run ownership 在 `start()` 成功时立即生效；
* execution event 从此刻开始产生；
* timeout 从 execution task 创建时开始计算；
* 即使没有 subscriber，Run 仍继续执行；
* 即使没有立即 `await result()`，Run 也不会暂停；
* Run terminal 后自动释放 Session active ownership。

---

## 2.4 ModelContext 与 RunContext

二者必须严格隔离。

### ModelContext

表示：

> **模型能够看到的输入。**

路径：

```text
Session.messages
      ↓
ContextManager
      ↓
ModelContext
      ↓
Model
```

其中可能包含：

```text
system prompt
conversation messages
tool schemas
显式 model-visible context
```

### RunContext

表示：

> **Runtime / Tool / Hook 可以访问，但模型默认不可见的本地运行时上下文。**

推荐：

```python
@dataclass(slots=True)
class RunContext:
    session_id: str
    run_id: str
    cancellation: CancellationToken
    turn: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)
```

必须满足：

```text
RunContext != ModelContext
```

并且：

> **Kernel 永远不会隐式将 `RunContext.metadata` 投影到 ModelContext。**

`metadata`：

* application-owned；
* Kernel 不解释；
* Tool / Hook 默认只读；
* 不自动进入 ModelContext；
* 不进入 Events；
* 不进入 RunResult；
* 不进入默认 logging / tracing。

如果应用希望某些 runtime data 对模型可见，必须显式构造 model-visible input。

---

## 2.5 ToolInvocation

推荐：

```python
@dataclass(...)
class ToolInvocation:
    call: ToolCall
    run_context: RunContext
    tool_context: ToolCallContext
    model_context: ModelContext | None = None
```

其中：

```text
model_context
```

默认必须为：

```text
None
```

只有显式配置允许 Tool 读取当前 Turn 的 ModelContext 时才提供。

如果提供：

> 必须是当前 Turn 的 immutable ModelContext snapshot。

因为 ModelContext 可能包含：

```text
system prompt
conversation history
tool definitions
model-only instructions
```

普通 Tool 不应该无意读取这些内容。

---

## 2.6 RunState

RunState 表示：

> **当前 Run 的瞬时 execution snapshot。**

内部可以维护：

```python
@dataclass(slots=True)
class RunState:
    status: RunStatus
    phase: RunPhase
    turn: int = 0

    streaming_message: AssistantMessage | None = None
    pending_tool_calls: tuple[ToolCall, ...] = ()

    error: RunError | None = None
```

但：

> 对外只能返回 immutable snapshot / copy。

不能把同一个 mutable RunState 暴露给调用方。

### RunStatus

```python
class RunStatus(Enum):
    CREATED = "created"
    RUNNING = "running"

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    MAX_TURNS = "max_turns"
```

### RunPhase

```python
class RunPhase(Enum):
    IDLE = "idle"
    PREPARING_CONTEXT = "preparing_context"
    MODEL = "model"
    TOOL = "tool"
    BETWEEN_TURNS = "between_turns"
    TERMINAL = "terminal"
```

合法状态转换：

```text
CREATED / IDLE
      │
      ▼
RUNNING / PREPARING_CONTEXT
      │
      ├── MODEL
      ├── TOOL
      └── BETWEEN_TURNS
      │
      ▼
terminal status / TERMINAL
```

即：

```text
CREATED → RUNNING → exactly one terminal status
```

terminal 后不能重新进入 RUNNING。

### Cancellation Request

Cancellation request 本身不是新的 `RunStatus`。

通过：

```text
CancellationToken
CancellationRequested event
```

表示。

完成 runtime cleanup 后才进入：

```text
CANCELLED
TIMED_OUT
```

等 terminal status。

### RunState.error

规则：

* 正常执行时为 `None`；
* terminal failure 确认后写入；
* 单个 ToolCall failure 不写入 RunState.error；
* terminal 后不再清除。

---

## 2.7 三类信息来源

必须保持：

```text
Session.messages ≠ RunState ≠ Events
```

分别表示：

```text
Session.messages
    canonical conversation truth

RunState
    transient execution snapshot

Events
    observable execution records
```

三者不能相互替代。

---

# 3. Canonical Transcript Protocol

Kernel 保存：

> **provider-neutral canonical transcript。**

基础消息：

```text
UserMessage
AssistantMessage
ToolResultMessage
```

具体 Provider 的 tool protocol 差异由 Model Adapter 处理。

## 3.1 Transcript Grammar

抽象语法：

```text
Transcript :=
    Message*

Message :=
    UserMessage
  | AssistantMessageWithoutTools
  | ToolExchange

ToolExchange :=
    AssistantMessageWithTools
    ToolResultMessage+
```

假设：

```text
AssistantMessage(
    tool_calls=[A, B, C]
)
```

那么必须紧跟：

```text
ToolResultMessage(A)
ToolResultMessage(B)
ToolResultMessage(C)
```

要求：

```text
数量一致
顺序一致
tool_call_id 一致
```

在这些 ToolResults 全部完成前：

> 不能插入新的 UserMessage 或 AssistantMessage。

---

## 3.2 合法 Tool Exchange

合法：

```text
Assistant(tool_calls=[A])
ToolResult(A)
```

合法：

```text
Assistant(
    text="I'll inspect the repository.",
    tool_calls=[A, B],
)

ToolResult(A)
ToolResult(B)
```

---

## 3.3 非法 Tool Exchange

非法：

```text
Assistant(tool_calls=[A, B])
ToolResult(B)
ToolResult(A)
```

非法：

```text
Assistant(tool_calls=[A])
UserMessage(...)
ToolResult(A)
```

非法：

```text
ToolResult(A)
```

非法：

```text
Assistant(no tool_calls)
ToolResult(A)
```

非法：

```text
Assistant(tool_calls=[A])
ToolResult(A)
ToolResult(A)
```

---

## 3.4 Consecutive UserMessage

以下合法：

```text
UserMessage(A)
UserMessage(B)
UserMessage(C)
```

这是 steering / follow-up 合并进入下一 Model Turn 的重要基础。

---

## 3.5 Assistant text + ToolCall

合法：

```text
AssistantMessage(
    content="I'll inspect the files.",
    tool_calls=[A],
)
```

但必须继续完成：

```text
ToolResult(A)
```

---

## 3.6 Empty Assistant

以下 provider-normalized message 合法：

```text
AssistantMessage(
    content="",
    tool_calls=[],
)
```

Kernel 不因为内容为空而自动破坏 transcript。

是否将其解释为：

```text
normal completion
model protocol error
特殊 finish state
```

由 Model Adapter / finish reason 处理。

---

## 3.7 ToolCall ID

同一个 AssistantMessage 中：

> **ToolCall.id 必须唯一。**

重复：

```text
ToolCall(id="1")
ToolCall(id="1")
```

必须产生：

```text
ProtocolError
```

不能进入 ToolExecutor。

---

## 3.8 History Import

导入已有 transcript 时必须使用与 runtime 相同的：

```text
TranscriptValidator
```

验证：

```text
message sequence
ToolCall IDs
ToolResult count
ToolResult order
ToolResult ownership
dangling ToolCall
```

非法 transcript：

> 直接拒绝导入或创建 Session。

不能等到下一 Model 调用才发现。

---

# 4. AgentLoop 与 Runtime Control

## 4.1 Turn 定义

一个 Turn 定义为：

> **一次 Model Invocation，以及由它产生的一次完整 Tool Batch。**

例如：

```text
Model
  ↓
Tool A
Tool B
  ↓
next Model
```

第一段属于一个 Turn。

所以：

```text
steering → new Model Invocation → +1 turn

follow-up → new Model Invocation → +1 turn
```

`max_turns` 计算：

> Model Invocation 数量。

---

## 4.2 Frozen Visible Tool Set

每个 Turn 首先解析本轮可见工具：

```text
ToolResolver
      ↓
FrozenToolSet
      ↓
┌───────────────┐
│               │
▼               ▼
ModelContext  ToolExecutor
```

推荐接口：

```python
class ToolResolver(Protocol):
    async def resolve(
        self,
        run_context: RunContext,
        tools: Sequence[Tool],
    ) -> FrozenToolSet:
        ...
```

流程：

```text
resolve visible tools
        ↓
freeze tool set
        ↓
build ModelContext
using exactly those tool schemas
        ↓
Model generates ToolCalls
        ↓
Executor resolves calls
against the same FrozenToolSet
```

必须保证：

> **模型看到的工具集合和本 Turn Executor 能执行的工具集合完全一致。**

Resolver 可以根据：

```text
RunContext
```

进行本地工具过滤。

但不能把：

```text
RunContext.metadata
```

自动注入模型。

---

## 4.3 ContextManager

每个 Turn 都重新执行：

```python
ContextManager.prepare(...)
```

概念输入：

```text
canonical transcript
system prompt
FrozenToolSet definitions
explicit model-visible application values
```

所谓：

```text
model-visible runtime values
```

必须由应用显式提供。

Kernel 不允许：

```text
RunContext.metadata
      ↓ automatic extraction
ModelContext
```

Context preparation failure：

```text
RunStatus.FAILED
RunTerminationReason.CONTEXT_ERROR
```

现有 context transform 能力应逐渐归入：

```text
ContextManager pipeline
```

而不是 Hooks。

---

## 4.4 AgentLoop

AgentLoop 负责：

```text
Run lifecycle
Turn lifecycle

resolve FrozenToolSet
Context preparation

Model invocation
Model streaming

Assistant commit

ToolExecutor orchestration
ToolResult commit

RunControl observation
RunControl consumption

termination
```

而：

> **不再负责底层 Tool execution。**

整体：

```text
Run Started
     │
     ▼
resolve FrozenToolSet
     │
     ▼
ContextManager.prepare()
     │
     ▼
invoke Model
     │
     ▼
stream model events
     │
     ▼
commit AssistantMessage
     │
     ├── tool calls ───────────────┐
     │                             │
     ▼                             │
ToolExecutor                       │
     │                             │
     ▼                             │
ToolExecutionBatchResult           │
     │                             │
     ▼                             │
commit ToolResultMessages          │
     │                             │
     └──────────────┬──────────────┘
                    ▼
              safe boundary
                    │
           commit controls
                    │
             next model turn?
              │            │
             yes           no
              │            │
              ▼            ▼
          next turn      terminal
```

---

## 4.5 RunControl

RunControl 提供：

```text
cancel
steer
follow_up
```

其中：

```text
steer
follow_up
```

调用时：

> **只进入 pending-control queue。**

不能立即写入 transcript。

推荐：

```python
@dataclass(frozen=True)
class PendingControl:
    sequence: int
    kind: ControlKind
    message: UserMessage
```

它表示：

> 已经收到，但尚未达到合法 transcript commit boundary 的用户输入。

因此并不是 canonical transcript 的第二份副本。

---

## 4.6 Control API 输入

以下 API：

```text
session.start()
run.steer()
run.follow_up()
```

使用同一个 UserMessage normalization。

公共输入：

```python
str | UserMessage
```

不接受任意 `Message`。

字符串统一转换：

```python
UserMessage(content=value)
```

校验：

```text
空字符串 → reject
whitespace-only → reject
非法 UserMessage → reject
```

Message size limit 如果未来存在，也必须由同一套 UserMessage validation 负责。

---

## 4.7 Terminal Run 上的 Control

Run 已 terminal 后：

```python
run.cancel()
```

是：

> **幂等 no-op。**

而：

```python
run.steer(...)
run.follow_up(...)
```

必须：

```text
raise RunFinishedError
```

不能假装接受了用户消息。

---

## 4.8 Observe 与 Consume

Steering 可以在 Tool boundary 被观察：

```text
observe pending steer
```

此时 policy 可以决定：

```text
停止启动新 ToolCall
取消某些已启动 ToolCall
继续某些已启动 ToolCall
```

但是：

> **此时不能把 steer 写入 transcript。**

只有安全边界才能：

```text
consume
     ↓
commit UserMessage
```

因此：

```text
observe != consume
```

---

## 4.9 Safe Transcript Boundary

pending control 可以进入 transcript 的条件：

1. 当前不存在未完成 Model stream；
2. 如果本 Turn 已有 AssistantMessage，则它已经完整 commit；
3. 如果 AssistantMessage 含 ToolCalls，则所有 ToolCalls 已有 terminal outcome；
4. ToolResults 已全部按 call order commit；
5. 下一次 ContextManager.prepare 尚未开始。

例如：

```text
Assistant(tool_calls=[A, B])
ToolResult(A)
ToolResult(B)
UserMessage(steer)
```

合法。

而：

```text
Assistant(tool_calls=[A, B])
UserMessage(steer)
ToolResult(A)
ToolResult(B)
```

非法。

---

## 4.10 无 AssistantMessage 时的 Safe Boundary

如果本 Turn：

```text
Context preparation 失败
```

或者：

```text
Model 在产生 AssistantMessage 前失败
```

那么：

> 当前不存在未完成的 Assistant / Tool Exchange。

此时 ToolCall/ToolResult 条件视为满足。

所以可以直接：

```text
current transcript tail
      ↓
commit pending controls
      ↓
terminate Run
```

---

## 4.11 Control 顺序

所有 control entry 都有：

```text
monotonic sequence
```

commit 时严格按接收顺序。

例如：

```text
1. follow_up("总结")
2. steer("先检查测试")
```

当 steer 触发下一 Turn 时：

```text
UserMessage("总结")
UserMessage("先检查测试")
```

一起进入 transcript。

因此：

* follow-up 本身不主动打断 Run；
* steer 可以触发下一 Turn；
* steer 触发时，位于它之前的 pending controls 一并 commit；
* Kernel 不重新排序；
* Kernel 不自动删除旧 follow-up。

---

## 4.12 Control Priority

固定优先级：

```text
cancel
  >
steer-triggered continuation
  >
natural follow-up continuation
```

cancel 被观察后：

```text
停止启动新 Model

停止启动 pending ToolCall

请求取消已经运行 ToolCall

不再因为 steer/follow-up 创建新 Turn
```

然后：

```text
完成 Tool terminal normalization
      ↓
commit required ToolResults
      ↓
reach safe boundary
      ↓
commit pending controls
      ↓
terminal Run
```

---

## 4.13 Pending Control 在终止时的归宿

以下正常 terminal path：

```text
cancel
timeout
max turns
model error
context error
policy FAIL_RUN
```

必须：

> **在最终 safe boundary 将所有已经成功接收的 pending controls 按顺序 commit 到 Session。**

但：

> 不再由这些 controls 启动新的 Model Invocation。

因此正常情况：

```python
RunResult.uncommitted_controls == ()
```

只有出现：

```text
Kernel invariant failure
transcript corruption
unrecoverable internal failure
```

无法建立 safe boundary 时，才允许：

```python
uncommitted_controls != ()
```

此时必须：

```text
status == FAILED
```

并且：

```text
termination_reason
in {
    RUNTIME_ERROR,
    INVALID_STATE,
}
```

---

## 4.14 continue_run()

```python
run = session.continue_run(
    config=config,
)
```

表示：

> **不新增 UserMessage，从当前 canonical transcript 创建一个新的 AgentRun。**

例如：

```text
Run #1
 FAILED
   │
   ▼
continue_run()
   │
   ▼
Run #2
```

不是恢复 Run #1 的 coroutine。

创建前必须验证：

```text
Session non-empty
no active Run
canonical transcript valid
no dangling ToolCall
```

非法：

```text
raise InvalidContinuationError
```

不创建一个立即失败的 AgentRun。

---

# 5. Cancellation Model

## 5.1 CancellationToken

v1 最小接口：

```python
class CancellationToken(Protocol):

    @property
    def cancelled(self) -> bool:
        ...

    @property
    def reason(self) -> CancellationReason | None:
        ...

    async def wait_cancelled(self) -> None:
        ...

    def throw_if_cancelled(self) -> None:
        ...

    def child(self) -> CancellationToken:
        ...
```

---

## 5.2 CancellationReason

推荐：

```python
class CancellationReason(Enum):
    USER = "user"
    TIMEOUT = "timeout"
    RUN_TERMINATED = "run_terminated"
    TOOL_POLICY = "tool_policy"
```

规则：

> **first cancellation reason wins。**

例如：

```text
USER cancel
↓
timeout later arrives
↓
reason remains USER
```

反之：

```text
TIMEOUT
↓
later USER cancel
↓
reason remains TIMEOUT
```

Child Tool token：

```text
Run cancellation
    → inherit Run reason

Tool policy cancellation
    → TOOL_POLICY
```

已确定的 child reason 不被后续 reason 覆盖。

---

## 5.3 Timeout

`RunConfig.timeout` 定义为：

> **触发 cooperative cancellation 的 wall-clock soft deadline。**

起点：

```text
AgentRun execution task creation
```

即：

```text
session.start()
     ↓
create task
     ↓
start timeout clock
```

deadline 到达：

```text
CancellationRequested(
    reason=TIMEOUT
)
```

随后：

```text
cooperative cleanup
tool normalization
transcript completion
```

真正 Run terminal 后才发送：

```text
RunTimedOut
```

所以：

```text
deadline reached
      ↓
CancellationRequested(TIMEOUT)
      ↓
cleanup
      ↓
RunTimedOut
```

v1 不提供 hard kill guarantee。

如果外部 Model / Tool 不响应 cancellation：

> Run 可能在 deadline 后继续等待其结束。

---

# 6. Tool Runtime

## 6.1 ToolCallContext

每一个 ToolCall 都拥有 child cancellation token：

```python
@dataclass(slots=True)
class ToolCallContext:
    call_id: str
    cancellation: CancellationToken
```

结构：

```text
Run CancellationToken
        │
        ├── Tool A Token
        ├── Tool B Token
        └── Tool C Token
```

Run cancel：

```text
cancel all children
```

ToolExecutionPolicy：

```text
can cancel selected child
```

因此可以表达：

```text
A running → continue
B running → cancel
C pending → skip
```

---

## 6.2 Tool Result 三层模型

必须严格区分：

```text
ToolOutput

ToolCallOutcome

ToolExecutionBatchResult
```

### ToolOutput

表示：

> **Tool handler 原始业务结果。**

推荐兼容现有字段：

```python
@dataclass(...)
class ToolOutput:
    content: Any

    is_error: bool = False

    error_code: str | None = None
    details: Any | None = None
```

现有：

```text
stop_run
```

不再属于 ToolOutput。

因为 Tool handler 不应该直接控制 Agent Run lifecycle。

需要终止 Run 的语义由：

```text
ToolExecutionPolicy
```

决定。

---

## 6.3 ToolCallOutcome

表示：

> **Executor 对一个 ToolCall 的最终 execution state。**

```python
class ToolCallStatus(Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
```

```python
@dataclass(frozen=True)
class ToolCallOutcome:
    call_id: str
    tool_name: str

    status: ToolCallStatus

    output: ToolOutput | None = None
    error: ToolExecutionError | None = None
```

---

## 6.4 ToolExecutionBatchResult

表示：

> **一次 ToolCall batch 的有序最终结果。**

```python
@dataclass(frozen=True)
class ToolExecutionBatchResult:
    outcomes: tuple[ToolCallOutcome, ...]
```

必须满足：

```python
len(outcomes) == len(tool_calls)

outcomes[i].call_id == tool_calls[i].id
```

---

## 6.5 ToolOutput → Outcome

固定映射：

| Handler / Runtime 状态         | ToolCallOutcome |
| ---------------------------- | --------------- |
| `ToolOutput(is_error=False)` | `COMPLETED`     |
| `ToolOutput(is_error=True)`  | `FAILED`        |
| handler exception            | `FAILED`        |
| validation failure           | `FAILED`        |
| unknown tool                 | `FAILED`        |
| policy reject                | `FAILED`        |
| 已启动并响应 cancellation          | `CANCELLED`     |
| 尚未启动且 batch 被停止              | `SKIPPED`       |

禁止：

```text
ToolOutput(is_error=True)
+
ToolCallStatus.COMPLETED
```

---

## 6.6 Canonical ToolResultMessage

ToolCallOutcome 必须转换为 provider-neutral transcript message：

```python
@dataclass(frozen=True)
class ToolResultMessage:
    tool_call_id: str
    tool_name: str

    status: ToolCallStatus

    content: Any = None

    error: ToolExecutionError | None = None
    details: Any | None = None
```

### COMPLETED

```text
status  = COMPLETED
content = ToolOutput.content
details = ToolOutput.details
error   = None
```

### FAILED

```text
status = FAILED

content =
    sanitized model-visible error message

error =
    structured safe ToolExecutionError
```

### CANCELLED

```text
status = CANCELLED

content =
    generic cancellation result
```

### SKIPPED

```text
status = SKIPPED

content =
    generic skipped result
```

Provider Adapter 负责：

> 将这些 provider-neutral 状态映射成具体模型 API 所接受的 ToolResult 格式。

---

## 6.7 ToolExecutor

ToolExecutor pipeline：

```text
ToolCall Batch
      │
      ▼
resolve against FrozenToolSet
      │
      ▼
validate
      │
      ▼
before policy
      │
      ▼
schedule
      │
      ▼
execute
      │
      ▼
normalize ToolOutput / exception
      │
      ▼
after hook
      │
      ▼
ToolCallOutcome
      │
      ▼
stable ordering
      │
      ▼
ToolExecutionBatchResult
```

ToolExecutor：

> 不持有 Session，不负责 transcript commit。

AgentLoop 才负责：

```text
ToolExecutionBatchResult
        ↓
ToolResultMessage[]
        ↓
Session commit
```

---

## 6.8 Execution Mode

```python
class ToolExecutionMode(Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
```

默认：

```text
SEQUENTIAL
```

原因不是领域安全，而是：

> Sequential 是最确定、最简单的通用默认执行语义。

需要 parallel 时显式配置。

---

## 6.9 max_concurrency

```python
@dataclass(frozen=True)
class ToolExecutionConfig:
    mode: ToolExecutionMode = ToolExecutionMode.SEQUENTIAL
    max_concurrency: int | None = None
```

定义：

> **单个 Tool batch 内的本地并发上限。**

规则：

```text
None
    → no additional concurrency limit

positive integer
    → maximum parallel ToolCalls

0 / negative
    → invalid configuration
```

Sequential 模式下：

```text
max_concurrency ignored
```

---

## 6.10 Stable Ordering

模型：

```text
ToolCall A
ToolCall B
ToolCall C
```

实际并行完成：

```text
B
C
A
```

Events 可以：

```text
ToolCompleted(B)
ToolCompleted(C)
ToolCompleted(A)
```

但 BatchResult 必须：

```text
AOutcome
BOutcome
COutcome
```

Transcript 必须：

```text
ToolResult(A)
ToolResult(B)
ToolResult(C)
```

因此：

```text
execution completion order
        !=
transcript order
```

---

# 7. ToolExecutionPolicy

## 7.1 RunConfig 与 Policy Factory

RunConfig：

```python
ToolPolicyFactory = Callable[
    [RunContext],
    ToolExecutionPolicy,
]
```

```python
@dataclass(frozen=True)
class RunConfig:
    max_turns: int = 32
    timeout: float | None = None

    tool_execution: ToolExecutionConfig = field(
        default_factory=ToolExecutionConfig
    )

    tool_policy_factory: ToolPolicyFactory | None = None
```

每个 Run：

```text
factory(run_context)
      ↓
private ToolExecutionPolicy instance
```

如果：

```python
tool_policy_factory is None
```

Kernel 必须创建：

```python
DefaultToolExecutionPolicy()
```

因此无配置情况下行为唯一确定。

---

## 7.2 DefaultToolExecutionPolicy

默认行为固定为：

```text
before_call
    → ALLOW

ToolCall FAILED
    → STOP_BATCH

steering + PENDING ToolCall
    → SKIP

steering + RUNNING ToolCall
    → CONTINUE
```

设计目标：

> **确定、保守、不主动中断已经开始的 Tool。**

Default policy 不实现：

```text
retry
approval
permission
resource lock
rate limit
domain safety
```

---

## 7.3 BeforeToolAction

```python
class BeforeToolAction(Enum):
    ALLOW = "allow"
    REJECT = "reject"
    SKIP = "skip"
    FAIL_RUN = "fail_run"
```

固定行为：

| Action     | 当前 ToolCall             | 后续未启动 ToolCall | Run                   |
| ---------- | ----------------------- | -------------- | --------------------- |
| `ALLOW`    | 正常执行                    | 正常执行           | 继续                    |
| `REJECT`   | `FAILED(policy_denied)` | 正常执行           | 继续                    |
| `SKIP`     | `SKIPPED`               | 正常执行           | 继续                    |
| `FAIL_RUN` | `FAILED(policy_denied)` | `SKIPPED`      | `FAILED / TOOL_ERROR` |

### ALLOW

```text
validate
  ↓
ALLOW
  ↓
execute handler
```

最终 outcome 由实际执行决定。

### REJECT

不执行 handler：

```text
FAILED(policy_denied)
```

但：

> batch 继续。

REJECT 不等于 STOP_BATCH。

### SKIP

不执行 handler：

```text
SKIPPED
```

且：

> 不触发 `on_error()`。

### FAIL_RUN

当前调用：

```text
FAILED(policy_denied)
```

尚未启动 sibling：

```text
SKIPPED
```

已经运行 sibling：

```text
request child cancellation
```

完成 terminal normalization 后：

```text
RunStatus.FAILED
RunTerminationReason.TOOL_ERROR
```

---

## 7.4 ToolErrorAction

```python
class ToolErrorAction(Enum):
    CONTINUE = "continue"
    STOP_BATCH = "stop_batch"
    FAIL_RUN = "fail_run"
```

### CONTINUE

| 调用状态        | 行为   |
| ----------- | ---- |
| 未启动 sibling | 正常执行 |
| 已启动 sibling | 正常完成 |

### STOP_BATCH

| 调用状态        | 行为        |
| ----------- | --------- |
| 未启动 sibling | `SKIPPED` |
| 已启动 sibling | 继续运行并等待完成 |

所以：

> STOP_BATCH 只阻止新的调用启动，不撤销已经发生的工作。

### FAIL_RUN

| 调用状态        | 行为                         |
| ----------- | -------------------------- |
| 未启动 sibling | `SKIPPED`                  |
| 已启动 sibling | request child cancellation |

然后等待所有 ToolCall 达到 terminal outcome。

若 Tool 忽略 cancellation：

```text
允许最终 COMPLETED
```

但整个 Run 仍：

```text
FAILED / TOOL_ERROR
```

---

## 7.5 SteeringAction

```python
class SteeringAction(Enum):
    CONTINUE = "continue"
    CANCEL = "cancel"
    SKIP = "skip"
```

ToolCallState：

```python
class ToolCallState(Enum):
    PENDING = "pending"
    RUNNING = "running"
```

### PENDING

```text
CONTINUE → 正常启动

SKIP → SKIPPED

CANCEL → 等价于 SKIP
```

### RUNNING

```text
CONTINUE → 等待完成

CANCEL → request child cancellation

SKIP → invalid action
```

已经运行的 Tool 不能重新解释为“从未发生”。

---

## 7.6 Policy Scope

Policy 可以决定：

```text
allow
reject
skip
stop batch
fail run
steering response
```

Policy 不负责：

```text
task graph
priority scheduling
resource allocation
generic retry engine
```

Sequential / Parallel / semaphore scheduling 固定由 Executor 管理。

v1 不内置 retry。

---

# 8. Hooks 与 Events

## 8.1 Hooks

Hooks 是：

> **observer / integration callback。**

建议：

```text
on_run_start
on_run_end

on_turn_start
on_turn_end

on_model_start
on_model_end

on_tool_start
on_tool_end
```

其中：

```text
on_tool_end
```

接收：

```text
ToolCallOutcome
```

而不是裸 `ToolOutput`。

这样能够观察：

```text
COMPLETED
FAILED
CANCELLED
SKIPPED
```

### Hook Exception

Hook 异常：

> 只记录 log，不产生正式生命周期 Event，也不使 Run 失败。

因此 v1 不增加：

```text
HookError
```

公共事件。

Hooks 默认：

* 可异步；
* 按注册顺序执行；
* 不修改 ToolCallOutcome；
* 不修改 RunControl；
* 不负责 allow / deny；
* 不参与 scheduling。

Policy 和 Hook 必须保持职责分离。

---

## 8.2 Events

正式事件：

```text
RunStarted

TurnStarted
TurnCompleted

ModelStarted
ModelDelta
ModelCompleted
ModelFailed

ToolStarted
ToolCompleted
ToolFailed
ToolCancelled
ToolSkipped

SteeringReceived
FollowUpReceived
CancellationRequested

RunCompleted
RunFailed
RunCancelled
RunTimedOut
RunMaxTurns
```

每个 Run：

> **恰好产生一个 terminal Run event。**

Terminal events 互斥：

```text
RunCompleted
RunFailed
RunCancelled
RunTimedOut
RunMaxTurns
```

---

## 8.3 Event Sequence

单个 Run 内：

```python
event.sequence
```

严格单调递增。

Parallel Tool events：

> 按真实 execution order 产生。

例如：

```text
ToolStarted(A)
ToolStarted(B)
ToolCompleted(B)
ToolCompleted(A)
```

这与 transcript：

```text
ToolResult(A)
ToolResult(B)
```

不存在冲突。

---

## 8.4 Event History

RoboAgent v1 固定采用：

> **每个 AgentRun 保留完整 Event History。**

不实现：

```text
bounded event history
EventHistoryTruncated
```

Run 生命周期：

```text
AgentRun alive
    ↓
complete event history retained

AgentRun released
    ↓
event history released
```

未来如果长期 Run 产生巨大 ModelDelta 流，再考虑：

```text
bounded history
delta compaction
external trace sink
persistent tracing
```

不进入 v1。

---

## 8.5 events() Subscription

```python
async for event in run.events():
    ...
```

无论订阅发生于：

```text
刚启动
运行中
已经 terminal
```

都必须：

```text
replay complete current history
        ↓
continue live events
```

terminal 后订阅：

```text
replay complete history
        ↓
iterator ends
```

### Replay 与 Live 原子性

必须在同一 runtime synchronization boundary 内完成：

```text
capture history snapshot
+
register live subscriber
```

逻辑：

```text
critical section
   │
   ├── snapshot history
   └── register subscriber from next sequence
```

随后：

```text
replay snapshot
     ↓
consume live queue
```

保证：

> **既不重复，也不遗漏事件。**

---

## 8.6 Multiple Subscribers

多个 subscriber：

* 相互独立；
* 各自 replay 完整 history；
* 各自拥有 live queue；
* 一个慢 subscriber 不影响其他 subscriber；
* subscriber 不允许阻塞 Agent execution。

Slow subscriber：

```text
queue full
    ↓
disconnect subscriber
```

但 Event History 仍保留在 AgentRun。

调用方以后可以重新订阅并重新 replay。

---

## 8.7 result()

```python
result = await run.result()
```

必须：

* 支持多个 coroutine 同时 await；
* terminal 后缓存 immutable RunResult；
* 多次调用获得同一逻辑结果。

如果某个调用方取消：

```python
await run.result()
```

自己的 waiter：

> **不能取消底层 AgentRun。**

只有：

```python
run.cancel()
```

可以请求 Runtime cancellation。

---

# 9. RunResult 与错误模型

## 9.1 RunResult

```python
@dataclass(frozen=True)
class RunResult:
    status: RunStatus

    final_message: AssistantMessage | None

    turns: int

    termination_reason: RunTerminationReason

    error: RunError | None = None

    uncommitted_controls: tuple[PendingControl, ...] = ()
```

### final_message

表示：

> **本 Run 已经 commit 到 Session 的最后一个 AssistantMessage。**

它不保证：

```text
是自然语言最终答案
没有 ToolCall
是整个 Session 最后 Assistant
```

应用层需要自行解释业务意义。

---

## 9.2 RunTerminationReason

```python
class RunTerminationReason(Enum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    MAX_TURNS = "max_turns"

    MODEL_ERROR = "model_error"
    TOOL_ERROR = "tool_error"
    CONTEXT_ERROR = "context_error"

    INVALID_STATE = "invalid_state"
    RUNTIME_ERROR = "runtime_error"
```

合法映射：

| RunStatus   | termination_reason                                                         |
| ----------- | -------------------------------------------------------------------------- |
| `COMPLETED` | `COMPLETED`                                                                |
| `CANCELLED` | `CANCELLED`                                                                |
| `TIMED_OUT` | `TIMED_OUT`                                                                |
| `MAX_TURNS` | `MAX_TURNS`                                                                |
| `FAILED`    | `MODEL_ERROR / TOOL_ERROR / CONTEXT_ERROR / INVALID_STATE / RUNTIME_ERROR` |

---

## 9.3 RunError

推荐：

```python
@dataclass(frozen=True)
class RunError:
    code: str
    message: str

    retryable: bool = False

    cause_type: str | None = None
```

详细 Python exception：

```text
traceback
repr
internal paths
secret values
```

不得自动暴露给 Model。

Tool error 同样必须经过：

```text
sanitization
```

后才能进入 `ToolResultMessage`。

---

# 10. RunConfig

推荐：

```python
@dataclass(frozen=True)
class RunConfig:
    max_turns: int = 32
    timeout: float | None = None

    tool_execution: ToolExecutionConfig = field(
        default_factory=ToolExecutionConfig
    )

    tool_policy_factory: ToolPolicyFactory | None = None
```

Agent 持有：

```text
default_run_config
```

调用：

```python
session.start(
    prompt,
    config=None,
)

session.run(
    prompt,
    config=None,
)

session.continue_run(
    config=None,
)
```

规则：

```text
config is None
    ↓
use Agent.default_run_config
```

```text
config is not None
    ↓
use provided RunConfig completely
```

即：

> **per-run config 整体替换 Agent default。**

不执行隐式字段级 merge。

---

# 11. Runtime Invariants

RoboAgent v1 固定以下不变量。

1. `Agent` 是 immutable reusable definition。
2. `Session.messages` 是唯一 canonical transcript。
3. `start()` 必须先获取 active-run ownership，再提交 initial UserMessage。
4. `start()` 立即启动 execution。
5. 失败的 `start()` 不得污染 transcript。
6. 外部不能直接修改 Session transcript。
7. `Run` 只代表一次 execution。
8. `RunContext` 与 `ModelContext` 严格隔离。
9. metadata 不隐式进入 ModelContext、Events、RunResult 或默认日志。
10. RunState 对外只能提供 snapshot。
11. canonical transcript 必须满足正式 grammar。
12. Tool Exchange 必须连续、完整、顺序一致。
13. 同一 AssistantMessage 内 ToolCall.id 唯一。
14. ToolResult 不能独立出现。
15. 连续 UserMessage 合法。
16. Assistant text + ToolCall 合法。
17. partial Model stream 不进入 transcript。
18. steer/follow-up 调用时只进入 pending controls。
19. observe control 与 consume control 是不同阶段。
20. pending control 只能在 safe boundary commit。
21. 无 AssistantMessage 的失败 Turn 也可以形成 safe boundary。
22. pending controls 按 receive sequence commit。
23. cancel 优先于 steer / follow-up。
24. 正常 terminal path 必须清空 pending controls。
25. `uncommitted_controls != ()` 只能表示 internal Runtime failure。
26. committed ToolCall 和 terminal ToolResult 之间不能插入 UserMessage。
27. 每个 committed ToolCall 必须产生一个 terminal ToolResult。
28. Tool handler result 与 Tool execution outcome 是不同类型。
29. `ToolOutput.is_error=True` 必须映射 FAILED。
30. ToolCallOutcome 必须映射 canonical ToolResultMessage。
31. ToolCallOutcome 顺序必须与模型 ToolCall 顺序一致。
32. Provider Adapter 负责 Provider protocol 差异。
33. Kernel transcript 始终 provider-neutral。
34. Model 与 Executor 使用同一个 FrozenToolSet。
35. ToolCall 可以拥有 child cancellation token。
36. first cancellation reason wins。
37. STOP_BATCH 不取消已经运行的 sibling。
38. FAIL_RUN 请求取消已经运行的 sibling。
39. 不响应 cancellation 的 Tool 允许最终 COMPLETED。
40. 单 ToolCall FAILED 不自动等于 Run FAILED。
41. `tool_policy_factory=None` 使用 `DefaultToolExecutionPolicy`。
42. 每个 Run 获得独立 policy instance。
43. Default Tool failure action 是 STOP_BATCH。
44. Default steering：PENDING → SKIP，RUNNING → CONTINUE。
45. Hooks 默认只观察，不决定 execution。
46. Hook failure 不使 Run 失败。
47. 每个 Run 恰好产生一个 terminal event。
48. Events 按真实 runtime 顺序产生。
49. Transcript ToolResults 始终按 ToolCall 顺序 commit。
50. Event history 在 AgentRun 生命周期内完整保留。
51. replay snapshot 与 live subscriber 注册必须原子。
52. 慢 subscriber 不允许阻塞 Run。
53. 一个 result waiter 被取消不会取消 AgentRun。
54. `continue_run()` 创建新 Run。
55. `continue_run()` 前必须验证 transcript。
56. terminal `cancel()` 是 idempotent no-op。
57. terminal `steer/follow_up()` 必须抛 `RunFinishedError`。
58. Run timeout 是 soft cooperative deadline。
59. `RunTimedOut` 只在 cleanup 完成后产生。
60. per-run RunConfig 整体替换 Agent default。
61. Kernel 不解释任何领域 policy。

---

# 12. 推荐实现顺序

## Phase 1：Canonical Runtime Types

首先实现和冻结：

```text
UserMessage
AssistantMessage
ToolCall
ToolResultMessage

TranscriptValidator

RunContext
RunState
RunStatus
RunPhase

RunConfig
RunResult
RunError

CancellationToken
CancellationReason

ToolOutput
ToolCallContext
ToolCallOutcome
ToolExecutionBatchResult
```

这一阶段的目标是：

> **先用类型和 validator 锁住 Runtime Protocol。**

---

## Phase 2：Session 与 AgentRun

实现：

```text
Session atomic start()

eager AgentRun execution

active-run ownership

read-only transcript

result() multi-await

full Event History
```

---

## Phase 3：RunControl

实现：

```text
cancel

steer

follow_up

pending-control queue

receive sequence

observation

safe commit

terminal flush
```

---

## Phase 4：ToolExecutor

先实现：

```text
sequential execution
```

并尽量保持当前行为等价。

随后实现：

```text
parallel execution

max_concurrency

ToolCall child cancellation

DefaultToolExecutionPolicy

STOP_BATCH

FAIL_RUN

stable outcome ordering
```

---

## Phase 5：AgentLoop

重构当前 Loop：

```text
resolve FrozenToolSet

ContextManager.prepare()

Model streaming

Assistant safe commit

ToolExecutor

ToolResult safe commit

RunControl observation

RunControl consumption

termination
```

---

## Phase 6：Adapters、Events 与 Semantic Tests

最后稳定：

```text
Provider Adapter protocol

Event replay

Hooks

continue_run()

semantic tests
```

目录调整应放在语义实现之后。

优先级：

> **公共语义与测试 > 文件组织。**

---

# 13. v1 Semantic Tests

v1 发布前必须重点测试运行语义。

## Transcript

```text
valid Tool Exchange

reversed ToolResult order rejected

missing ToolResult rejected

orphan ToolResult rejected

duplicate ToolCall ID rejected

consecutive UserMessage accepted

Assistant text + ToolCalls accepted

empty Assistant semantics

history import validation
```

## Session

```text
concurrent start() only one succeeds

failed start() does not mutate transcript

start() immediately executes

terminal Run releases active ownership

external transcript mutation impossible
```

## Control

```text
steer immediately after start()

steer during Model

steer during sequential Tool batch

steer during parallel Tool batch

follow-up during execution

follow-up + steer preserves receive order

cancel dominates pending controls

terminal steer rejected

terminal follow-up rejected

terminal cancel is no-op

normal terminal leaves no uncommitted controls
```

## Cancellation

```text
user cancellation

timeout cancellation

first cancellation reason wins

Run cancellation propagates to Tool children

Tool policy can cancel one child

non-cooperative Tool may complete after cancel request
```

## Tool

```text
ToolOutput → ToolCallOutcome

ToolCallOutcome → ToolResultMessage

BeforeToolAction.ALLOW

BeforeToolAction.REJECT

BeforeToolAction.SKIP

BeforeToolAction.FAIL_RUN

STOP_BATCH sequential

STOP_BATCH parallel

FAIL_RUN sequential

FAIL_RUN parallel

stable result ordering

ToolCall child cancellation

ToolOutput.is_error mapping

unknown Tool

validation failure
```

## Default Policy

```text
factory=None creates DefaultToolExecutionPolicy

before_call → ALLOW

Tool failure → STOP_BATCH

steering PENDING → SKIP

steering RUNNING → CONTINUE
```

## Resolver / Context

```text
Model and Executor use same FrozenToolSet

ToolResolver can filter with RunContext

metadata does not leak to ModelContext

explicit model-visible input works

ContextManager prepares each Turn
```

## Events

```text
late subscriber receives replay

terminal subscriber receives full replay

multiple subscribers

snapshot + live registration has no gap

no duplicated replay/live event

slow subscriber does not block Run

parallel events preserve actual completion order

exactly one terminal event
```

## result()

```text
multiple concurrent awaiters

repeat result()

waiter cancellation does not cancel Run

terminal result cached
```

## RunState

```text
CREATED → RUNNING → terminal

valid phase transitions

terminal phase

error only for Run terminal failure

no transition out of terminal
```

## continue_run()

```text
empty Session rejected

active Run rejected

dangling ToolCall rejected

valid User-ending transcript accepted

valid Assistant-ending transcript accepted

valid ToolResult-ending transcript accepted

imported history uses same validator
```

## Isolation

```text
multiple Sessions sharing same Agent remain isolated

each Run has independent policy

metadata never enters Events

metadata never enters ModelContext
```

---

# 14. v1 完成标准

RoboAgent v1 的完成标准不是：

```text
目录数量
功能数量
测试覆盖率百分比
```

而是：

> **Runtime ownership、transcript grammar、control、tool execution、cancellation、state transition、events 和 termination semantics 都已经拥有明确且唯一的解释，并通过 semantic tests 锁定。**

最终核心结构保持：

```text
                    Agent
                      │
                      ▼
                   Session
                      │
                      ▼
                     Run
                      │
       ┌──────────────┼──────────────┐
       ▼              ▼              ▼
  RunContext      RunState      RunControl
       │              │              │
       └──────────────┼──────────────┘
                      ▼
                 AgentLoop
              ┌───────┴───────┐
              ▼               ▼
       ContextManager     ToolExecutor
              │               │
              ▼               ▼
        ModelContext          Tool
              │
              ▼
             Model
```

并稳定以下协议：

```text
Session.messages
    =
canonical conversation truth
```

```text
RunContext
    !=
ModelContext
```

```text
Session.messages
    !=
RunState
    !=
Events
```

```text
Pending Control
      ↓
observe
      ↓
safe boundary
      ↓
consume / commit
```

```text
Assistant(tool_calls)
        ↓
ordered terminal ToolResults
        ↓
next User / Assistant
```

```text
Tool
  ↓
ToolOutput
  ↓
ToolExecutor
  ↓
ToolCallOutcome
  ↓
ToolResultMessage
```

```text
ToolResolver
      ↓
FrozenToolSet
   ↙          ↘
Model       Executor
```

以及：

```text
Runtime Events
    follow actual execution order

Canonical Transcript
    follows protocol commit order
```

当这些语义全部实现并通过测试后，RoboAgent v1 即可视为：

> **一个成熟的、模型无关、工具无关、可嵌入的通用异步 Agent Runtime Kernel。**

后续的：

```text
Memory
MCP
Handoff
Sub-Agent
Multi-Agent
Approval
Safety
Gateway
```

都应建立在这一 Kernel 之上，而不再改变 Kernel 的核心运行协议。
