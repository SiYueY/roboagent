# RoboAgent V1.1 设计规格

## 1. 版本目标、范围与核心不变量

### 1.1 版本定位

RoboAgent V1.1 是 V1 Runtime Kernel 的协议定型版本。

V1 已经建立了 Agent Runtime 的基础结构和核心语义，包括：

* `Agent / Session / Run` 的职责边界；
* `AgentLoop` 负责编排模型与工具执行；
* `ToolExecutor` 独立于 `AgentLoop`；
* `RunContext` 与 `ModelContext` 分离；
* Session transcript、RunState、Events 不互为事实来源；
* tool 并发完成顺序不得改变模型可见结果顺序；
* steer / follow-up 不得在模型实际消费前污染 transcript；
* Hooks、Policy、Events 分别承担生命周期扩展、执行决策和观测职责。

V1.1 不重新设计这些基础语义，而是正式稳定以下扩展边界：

```text
Model / ModelProvider
Context / Prompt
Tool / ToolExecutor
Skill
```

并补齐：

```text
Builtin Tools
Runtime Effects
Events / Hooks
Session ownership
Streaming protocol
Migration
Semantic test suite
```

V1.1 完成后的核心目标是：

> 后续增加 MCP、ROS2 Tool、Robot Tool、Web Tool、Memory、新 Provider 或更多 Skill 时，原则上不需要修改 AgentLoop 的核心控制流。

---

### 1.2 V1.1 范围

V1.1 包含：

```text
Model
ModelProvider
ModelSettings
ModelCapabilities
Canonical Model Streaming Protocol

RunContext
ContextSnapshot
ContextManager
PromptInput
PromptRenderer
ModelContext

Tool
ToolDefinition
ToolRegistry
ToolExecutor
ToolExecutionPolicy
ToolBatchResult
ToolResult canonicalization
ToolEffectRecord

Filesystem Builtin Tools
Shell Tool

Skill discovery
SkillCatalog
Skill metadata
read_skill Tool

Session single-active-Run ownership
Pending input lifecycle
RunResult
Events
Hooks

Compatibility / Migration
Semantic Test Suite
```

V1.1 暂不包含：

```text
token-aware context budget
automatic summarization
long-term memory
RAG
MCP
multi-agent
plugin framework
permission framework
sandbox framework
distributed runtime
browser automation
remote skill registry
skill dependency resolver
persistent run recovery
```

V1.1 暂不设计 token budget。

但未来任何 Context 裁剪、过滤或 compaction，都必须遵守本文定义的 message 和 ToolExchangeBlock 结构不变量。

---

### 1.3 Runtime 核心对象

核心对象定义如下：

```text
Agent
= 稳定配置与能力组合

Session
= 跨 Run 的 durable conversation facts

Run
= 一次明确的执行实例

RunContext
= Run 内 Runtime 可访问的稳定运行信息

RunState
= Run 当前瞬时执行状态

ContextSnapshot
= 一次 Context prepare 的不可变事实快照

ModelContext
= 某次模型调用真正可见的信息

Events
= Runtime observation

RunResult
= Run 最终状态、输出及真实执行 effects
```

必须满足：

```text
RunState != Session transcript

Events != Session transcript

Events != RunState

RunContext != ModelContext

RunResult.effects != Session transcript
```

其中：

```text
Session transcript
```

表示已经提交的对话事实。

```text
RunResult.effects
```

表示现实世界中已经发生或可能发生的 Tool side effects。

二者必须分离。

---

### 1.4 AgentLoop 唯一职责

AgentLoop 只负责编排：

```text
consume pending input at legal boundary
        ↓
capture immutable ContextSnapshot
        ↓
ContextManager.prepare()
        ↓
Model.stream()
        ↓
assemble canonical ModelResponse
        ↓
run after_model hooks
        ↓
tool calls?
   ├── no
   │    ↓
   │  commit assistant turn
   │    ↓
   │  finish / continue
   │
   └── yes
        ↓
     ToolExecutor.execute()
        ↓
     ToolBatchResult
        ↓
     atomic transcript commit
        ↓
     emit tool_batch.committed
        ↓
     next turn
```

AgentLoop 中禁止出现：

```python
if provider == "openai":
    ...

if tool.name == "shell":
    ...

if skill_requested:
    ...

if mcp_tool:
    ...
```

Provider、Tool、Builtin、Skill 等能力的特殊行为必须封装在各自边界内部。

---

### 1.5 Canonical JSON

跨模块 JSON-compatible 数据必须统一 canonicalize。

逻辑类型：

```python
JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | FrozenJsonArray | FrozenJsonObject
```

#### FrozenJsonArray

逻辑等价于：

```python
tuple[JsonValue, ...]
```

要求：

```text
immutable
ordered
structural equality
stable JSON serialization
```

#### FrozenJsonObject

`FrozenJsonObject` 是 immutable mapping。

必须满足：

```text
key:
str only

value:
JsonValue only

iteration order:
preserve canonical insertion order

equality:
mapping structural equality

hash:
not required

JSON serialization:
use canonical iteration order
```

构造流程：

```text
input
→ validate
→ reject non-finite float
→ deep copy
→ recursively canonicalize
→ freeze
```

必须拒绝：

```text
NaN
Infinity
-Infinity
non-string mapping keys
Path
bytes
Exception
provider SDK objects
custom mutable objects
arbitrary Python class instances
```

普通 `dict/list` 可以作为输入，但 canonicalization 后必须和原 mutable object 完全解耦。

---

### 1.6 Canonical Message 与多模态

V1.1 不重新定义项目现有的 canonical message hierarchy。

继续复用：

```text
AgentMessage
ModelMessage
UserMessage
AssistantMessage
ToolResultMessage

TextContent
ImageContent
AudioContent
FileContent
...
```

V1.1 重构不得退化已有：

```text
image
audio
file
```

输入/输出能力。

新的 Tool output 类型不得和现有 canonical `TextContent` 等类型重名。

---

### 1.7 CancellationToken

统一最小协议：

```python
class CancellationToken(Protocol):
    @property
    def cancelled(self) -> bool:
        ...

    def raise_if_cancelled(self) -> None:
        ...

    async def wait_cancelled(self) -> None:
        ...
```

Cancellation 属于 Runtime control-flow。

不得将 cancellation 转换成普通：

```text
ToolError
ContextError
ModelError
```

然后继续执行当前 Run。

---

### 1.8 RunContext

```python
@dataclass(frozen=True)
class RunContext:
    run_id: str
    session_id: str
    cancellation: CancellationToken
```

不得向：

```text
Model
Tool
Hook
ContextManager
```

直接暴露 mutable `Run` 或 `Session`。

---

### 1.9 Session 单 active Run

V1.1 明确：

> 同一个 Session 同一时刻最多允许一个 active Run。

允许：

```text
Session A → Run A
Session B → Run B
Session C → Run C
```

并发。

禁止：

```text
Session A
├── Run 1 active
└── Run 2 active
```

第二个 Run：

```text
→ SessionBusyError
```

V1.1 不引入：

```text
transcript revision
optimistic concurrency
Run writer arbitration
multi-Run commit ordering
```

等复杂机制。

---

### 1.10 Session ownership 原子性

Session 必须提供原子 ownership 获取。

逻辑接口：

```python
async def acquire_run(self, run_id: str) -> None:
    ...

async def release_run(self, run_id: str) -> None:
    ...
```

`acquire_run()` 必须在同一同步原语内执行：

```text
check active_run_id
+
assign active_run_id
```

即：

```python
async with lock:
    if active_run_id is not None:
        raise SessionBusyError
    active_run_id = run_id
```

`release_run()` 必须验证：

```text
active_run_id == run_id
```

其他 Run 不得错误释放 ownership。

Run 无论：

```text
completed
failed
cancelled
```

都必须通过 `finally` 释放 ownership。

---

### 1.11 Pending Input

Session 拥有 pending input queue。

active Run 是唯一 consumer。

V1.1 将：

```text
steer()
follow_up()
```

定义为 Session API。

建议：

```python
async def session.follow_up(
    self,
    message: UserMessage,
) -> InputReceipt:
    ...

async def session.steer(
    self,
    message: UserMessage,
) -> InputReceipt:
    ...
```

`UserMessage` 是唯一允许进入 pending input queue 的消息类型。不得通过
`steer()` / `follow_up()` 注入 assistant 或 tool message。

```python
@dataclass(frozen=True)
class InputReceipt:
    input_id: str
    sequence: int
    session_id: str
```

`sequence` 是 Session queue 内严格递增的入队顺序；它决定同一 legal turn
中多个 pending input 的消费顺序。

二者都只执行：

```text
enqueue
```

不执行：

```text
immediate transcript append
cancel active model
cancel active tool
```

---

### 1.12 无 active Run 时输入行为

Session 没有 active Run 时：

```text
steer / follow_up
```

仍允许 enqueue。

下一次 Run 成为 consumer。

如果 Session 已关闭：

```text
→ SessionClosedError
```

输入不得进入 queue。

---

### 1.13 Pending Input 消费

输入只能在合法 turn boundary：

```text
dequeue
→ canonicalize
→ append Session transcript
→ capture ContextSnapshot
```

后才成为模型可见事实。

不能在：

```text
model streaming 中
tool batch execution 中
transcript atomic commit 中
```

插入输入。

---

## 2. Model、Provider、Context 与 Prompt

### 2.1 ModelProvider

正式定义：

```python
class ModelProvider(Protocol):
    def get_model(
        self,
        name: str,
    ) -> Model:
        ...

    async def close(self) -> None:
        ...
```

Provider 负责：

```text
model resolution
provider-wide configuration
shared provider client
shared connection lifecycle
optional model caching
```

Provider 不负责：

```text
Session
Context preparation
Prompt rendering
AgentLoop
Tool execution
Run lifecycle
```

无法解析 Model：

```text
→ ModelProviderError
```

Runtime 不要求 Provider 每次执行前通过远程接口验证模型名称。

---

### 2.2 Provider ownership

两种方式：

#### 用户直接提供 Model

```python
agent = Agent(model=model)
```

Model / Provider 生命周期归用户。

#### Harness 创建 Provider

```text
Harness creates Provider
→ Harness owns Provider
→ close at Harness shutdown
```

如果用户注入已创建 Provider：

```text
caller owns Provider by default
```

除非显式：

```text
take_ownership=True
```

不得隐式 close 用户传入的 client。

---

### 2.3 Model

```python
class Model(Protocol):
    @property
    def capabilities(self) -> ModelCapabilities:
        ...

    def stream(
        self,
        context: ModelContext,
        settings: ModelSettings | None = None,
    ) -> AsyncIterator[ModelEvent]:
        ...
```

V1.1 只定义一套底层执行协议：

```text
streaming
```

非 streaming API 如需要：

```text
collect stream
→ ModelResponse
```

实现。

---

### 2.4 ModelCapabilities

```python
class Modality(Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    FILE = "file"
```

```python
@dataclass(frozen=True)
class ModelCapabilities:
    input_modalities: frozenset[Modality]
    output_modalities: frozenset[Modality]

    tool_calling: bool
    parallel_tool_calls: bool
```

Provider 请求前验证：

```text
ModelContext input modalities
⊆
input_modalities
```

否则：

```text
ModelCapabilityError
```

Provider 返回内容也必须：

```text
response modalities
⊆
output_modalities
```

---

### 2.5 tool_calling=False

ContextManager 保持 provider-independent。

因此它不根据 Model 能力偷偷删除 tools。

如果：

```text
model.capabilities.tool_calling == False
AND
ModelContext.tools non-empty
```

由 Model Adapter 在请求前返回：

```text
ModelCapabilityError
```

Harness/Agent 构造阶段可以提前检查并避免配置 tools，但 Model 边界仍必须防御。

---

### 2.6 parallel_tool_calls

`ModelCapabilities.parallel_tool_calls` 表示：

> Model / Provider 是否允许一个 assistant response 返回多个 ToolCall。

它只影响：

```text
provider request configuration
provider response capability validation
```

它不决定 ToolExecutor Runtime 是否并发执行。

ToolExecutor 并发只依据：

```text
ToolExecutionMode
ToolExecutorConfig
```

如果：

```text
parallel_tool_calls=False
```

Provider 却返回多个 ToolCall：

```text
→ ModelCapabilityError / ModelProtocolError
```

---

### 2.7 ModelSettings

```python
@dataclass(frozen=True)
class ModelSettings:
    temperature: float | None = None
    max_output_tokens: int | None = None
    top_p: float | None = None
    extra: FrozenJsonObject = EMPTY_JSON_OBJECT
```

基础验证：

```text
temperature:
finite if provided

top_p:
0 < value <= 1

max_output_tokens:
> 0
```

`extra` 必须：

```text
JSON-compatible
immutable
```

不得放入 provider SDK object。

复杂 Provider-specific 对象应通过 Provider / Model 构造参数提供。

---

### 2.8 Usage

```python
@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
```

所有提供的值：

```text
>= 0
```

如果 Provider 未返回 total，但：

```text
input_tokens
output_tokens
```

均可靠：

```text
Adapter may compute total_tokens
```

---

### 2.9 FinishReason

```python
class FinishReason(Enum):
    STOP = "stop"
    TOOL_CALL = "tool_call"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    OTHER = "other"
```

FinishReason 只是 termination metadata。

AgentLoop 是否执行 Tool 必须检查：

```python
response.message.tool_calls
```

而不是仅依赖：

```python
finish_reason
```

---

### 2.10 ModelResponse

```python
@dataclass(frozen=True)
class ModelResponse:
    message: AssistantMessage
    finish_reason: FinishReason
    usage: Usage | None = None
```

成功 stream：

```text
exactly one ResponseCompleted
→ iterator ends
```

失败：

```text
no ResponseCompleted
```

---

### 2.11 Model Error

最小层级：

```text
ModelError
├── ModelProviderError
├── ModelProtocolError
└── ModelCapabilityError
```

`ModelProviderError`：

```text
network failure
authentication
rate limit
provider unavailable
provider API error
model resolution failure
```

`ModelProtocolError`：

```text
provider stream/response 无法归一化为合法 RoboAgent canonical protocol
```

建议：

```python
class ModelProtocolError(ModelError):
    code: str
    message: str
    provider: str | None
```

code 至少包括：

```text
duplicate_response_started
duplicate_tool_call_started
invalid_tool_call_delta_state
duplicate_tool_call_completed
invalid_tool_arguments
incomplete_tool_call
duplicate_tool_call_id
tool_call_response_mismatch
invalid_stream_sequence
missing_terminal_response
invalid_provider_response
```

底层异常通过：

```python
__cause__
```

保留。

---

### 2.12 Canonical Streaming Events

最小事件：

```python
ResponseStarted(
    response_id: str,
)

TextDelta(
    sequence: int,
    text: str,
)

ToolCallStarted(
    sequence: int,
    call_index: int,
    call_id: str,
    name: str | None,
)

ToolCallArgumentsDelta(
    sequence: int,
    call_index: int,
    call_id: str,
    delta: str,
)

ToolCallCompleted(
    sequence: int,
    call_index: int,
    call: ToolCall,
)

UsageUpdated(
    sequence: int,
    usage: Usage,
)

ResponseCompleted(
    sequence: int,
    response: ModelResponse,
)
```

`sequence` 在一次 stream 内：

```text
strictly increasing
```

第一项 sequence 固定为 `0`；之后每个 canonical event 的 sequence 恰好比前一项大 `1`。

---

### 2.13 ResponseStarted

一次成功/有效开始的 stream：

```text
exactly one ResponseStarted
```

且必须是第一 canonical model event。

重复：

```text
→ ModelProtocolError("duplicate_response_started")
```

---

### 2.14 ToolCall

```python
@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: FrozenJsonObject
```

Provider Adapter 负责：

```text
provider fragments
→ identify ToolCall
→ accumulate args
→ parse JSON
→ canonicalize/freeze
→ ToolCallCompleted
```

AgentLoop / ToolExecutor 不接收 partial JSON。

---

### 2.15 ToolCall 状态机

每个 `call_index`：

```text
NOT_STARTED
→ STARTED
→ COMPLETED
```

不可逆。

同一个 `ModelResponse` 中，`call_id` 必须跨所有 `call_index` 全局唯一。不同
index 出现同一 ID 也产生 `ModelProtocolError("duplicate_tool_call_id")`。

#### ToolCallStarted

同 index：

```text
exactly once
```

`call_id` 必须存在。

Provider 没有 ID 时：

```text
Adapter generates stable runtime id
```

`name` 可暂时为 `None`。

---

### 2.16 ToolCallArgumentsDelta

只允许：

```text
state == STARTED
```

在：

```text
NOT_STARTED
COMPLETED
```

状态出现：

```text
→ ModelProtocolError
```

---

### 2.17 ToolCallCompleted

Completed 时必须：

```text
name non-empty
call_id valid
arguments valid JSON object
```

同 index：

```text
exactly once
```

Stream 结束仍为 STARTED：

```text
→ incomplete_tool_call
```

---

### 2.18 Final AssistantMessage 一致性

最终：

```python
ResponseCompleted.response.message.tool_calls
```

必须和所有 `ToolCallCompleted`：

```text
数量
顺序
id
name
arguments
```

完全一致。

否则：

```text
tool_call_response_mismatch
```

---

### 2.19 文本与 ToolCall 共存

同一 assistant turn 可同时有：

```text
text
tool calls
```

`AssistantMessage`：

```text
content
= ordered canonical content parts

tool_calls
= ordered ToolCall tuple
```

ToolCall 顺序按 `call_index`。

实时：

```text
text delta
tool-call delta
```

交错顺序通过：

```text
ModelEvent.sequence
```

保留。

Transcript 不需要把 content/tool calls 合成一个统一 parts array。

---

### 2.20 UsageUpdated

表示：

```text
latest cumulative usage snapshot
```

不是 delta。

允许：

```text
0..N
```

最终：

```python
ResponseCompleted.response.usage
```

应等于最后一份可用 snapshot。

---

### 2.21 Model stream terminal semantics

成功：

```text
ResponseStarted
...
ResponseCompleted
iterator ends
```

Provider / protocol failure：

```text
raise ModelProviderError / ModelProtocolError / ModelCapabilityError
```

Cancellation：

```text
propagate cancellation
```

不定义 `ResponseFailed` ModelEvent。

Runtime Events 可产生：

```text
model.failed
model.cancelled
```

---

### 2.22 Stream early close

AgentLoop 提前结束消费：

```text
async iterator close
```

Model Adapter 必须：

```text
cancel provider request
close transport/stream
release resources
```

不得依赖 GC。

---

### 2.23 ContextSnapshot

```python
@dataclass(frozen=True)
class ContextSnapshot:
    transcript: tuple[AgentMessage, ...]
    prompt: PromptInput | None
    tool_definitions: tuple[ToolDefinition, ...]
    skill_metadata: tuple[SkillMetadata, ...]
```

不包含：

```text
pending_inputs
Session
Run
RunState
ToolRegistry
SkillManager
Events
Hooks
```

Capture 时必须 deep canonical freeze。

---

### 2.24 ContextManager

```python
class ContextManager(Protocol):
    async def prepare(
        self,
        snapshot: ContextSnapshot,
        cancellation: CancellationToken,
    ) -> ModelContext:
        ...
```

ContextManager 不得隐式读取：

```text
Session
current Run
ToolRegistry
mutable SkillManager
```

---

### 2.25 ModelContext

```python
@dataclass(frozen=True)
class ModelContext:
    system_prompt: str | None
    messages: tuple[ModelMessage, ...]
    tools: tuple[ToolDefinition, ...]
```

不包含：

```text
Run
Session
RunState
CancellationToken
ToolExecutor
Events
Hooks
SkillManager
```

---

### 2.26 ToolExchangeBlock

以下结构：

```text
AssistantMessage containing ToolCalls
+
all ToolResultMessages corresponding to that assistant turn
```

构成：

```text
ToolExchangeBlock
```

未来：

```text
filter
truncation
compaction
```

都必须以完整 block 为最小单位。

禁止：

```text
orphan ToolResult
ToolCall without result
partial exchange
```

---

### 2.27 PromptInput

```python
@dataclass(frozen=True)
class PromptInput:
    system: str | None = None
    variables: FrozenJsonObject = EMPTY_JSON_OBJECT
```

不得放 runtime mutable object。

---

### 2.28 PromptRenderer

```python
class PromptRenderer(Protocol):
    async def render(
        self,
        prompt: PromptInput | None,
        cancellation: CancellationToken,
    ) -> str | None:
        ...
```

特点：

```text
async allowed
cancellable
explicit inputs only
```

Run metadata 如需进入 Prompt：

```text
caller must explicitly serialize it into variables
```

异常：

```text
PromptRenderError
```

---

### 2.29 System Prompt 组合顺序

固定：

```text
base prompt
→ RoboAgent runtime instructions
→ Available Skills section
```

Skill metadata 不得放在基础 Prompt 之前。

---

### 2.30 Skill metadata rendering

固定 Markdown：

```markdown
## Available skills

- `ros2-debug` [project]: Diagnose ROS 2 communication problems.
- `python-debug` [user]: Diagnose Python runtime problems.
```

排序：

```text
name ascending
then source
```

不注入：

```text
path
body
```

---

### 2.31 Skill metadata description normalization

固定：

```text
CRLF / CR
→ LF

control chars except TAB/LF
→ remove

LF / TAB
→ single space

consecutive whitespace
→ single space

strip
```

之后按：

```text
SkillConfig.max_description_chars
```

截断。

截断：

```text
Unicode code-point boundary
+
…
```

`…` 计入限制。

---

## 3. Tool、Registry、Executor、Policy 与 Effect

### 3.1 ToolDefinition

```python
@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: FrozenJsonObject
```

name：

```text
^[A-Za-z][A-Za-z0-9_.-]{0,63}$
```

description：

```text
non-empty
bounded
normalized
```

schema：

```text
valid JSON Schema
top-level type=object
```

V1.1 固定单一 validator/dialect。

如果项目已有实现，沿用。

否则使用：

```text
JSON Schema Draft 2020-12
```

---

### 3.2 ToolExecutionMode

```python
class ToolExecutionMode(Enum):
    SERIAL = "serial"
    CONCURRENT = "concurrent"
```

默认：

```text
SERIAL
```

---

### 3.3 ToolEffectKind

```python
class ToolEffectKind(Enum):
    READ_ONLY = "read_only"
    SIDE_EFFECTING = "side_effecting"
```

不能根据 `execution_mode` 推断 effect kind。

---

### 3.4 Tool

```python
class Tool(Protocol):
    @property
    def definition(self) -> ToolDefinition:
        ...

    @property
    def execution_mode(self) -> ToolExecutionMode:
        ...

    @property
    def effect_kind(self) -> ToolEffectKind:
        ...

    @property
    def timeout(self) -> float | None:
        ...

    async def execute(
        self,
        arguments: FrozenJsonObject,
        context: ToolContext,
    ) -> ToolContent:
        ...
```

---

### 3.5 ToolContext

```python
@dataclass(frozen=True)
class ToolContext:
    run_id: str
    session_id: str
    cancellation: CancellationToken
```

---

### 3.6 ToolRegistry

```python
class ToolRegistry:
    def register(
        self,
        tool: Tool,
        *,
        replace: bool = False,
    ) -> None:
        ...

    def get(self, name: str) -> Tool | None:
        ...

    def definitions(self) -> tuple[ToolDefinition, ...]:
        ...
```

注册时验证：

```text
name
description
schema
```

duplicate：

```text
replace=False
→ ToolRegistrationError

replace=True
→ explicit replacement
```

definitions：

```text
stable registration order
```

---

### 3.7 ToolExecutionPolicy

```python
class ToolDecision(Enum):
    ALLOW = "allow"
    REJECT = "reject"
    FAIL_RUN = "fail_run"
```

```python
class ToolExecutionPolicy(Protocol):
    async def evaluate(
        self,
        call: ToolCall,
        tool: Tool | None,
        context: ToolContext,
    ) -> ToolDecision:
        ...
```

默认：

```text
AllowAllToolPolicy
```

语义：

```text
ALLOW
→ continue

REJECT
→ model-visible rejected ToolResult
→ Run continues

FAIL_RUN
→ stop batch
→ Run fails
```

---

### 3.8 ToolContent

```python
@dataclass(frozen=True)
class ToolTextContent:
    text: str
    truncated: bool = False
```

```python
@dataclass(frozen=True)
class ToolJsonContent:
    value: JsonValue
```

```python
ToolContent = ToolTextContent | ToolJsonContent
```

V1.1 不支持 generic media Tool result。

---

### 3.9 ToolErrorInfo

```python
@dataclass(frozen=True)
class ToolErrorInfo:
    code: str
    message: str
    retryable: bool = False
```

message：

```text
Unicode text
normalized
bounded
```

超限：

```text
truncate at Unicode code-point boundary
append …
```

---

### 3.10 ToolExecutionResult

```python
@dataclass(frozen=True)
class ToolExecutionResult:
    call_id: str
    name: str
    content: ToolContent | None
    error: ToolErrorInfo | None
```

严格 XOR。

成功：

```text
content != None
error == None
```

失败：

```text
content == None
error != None
```

---

### 3.11 ToolResultMessage 映射

V1.1 模型可见 ToolResult terminal status 只定义：

```python
class ToolResultStatus(Enum):
    SUCCESS = "success"
    ERROR = "error"
```

映射：

```text
successful ToolExecutionResult
→ SUCCESS
```

任何：

```text
timeout
rejected
unknown_tool
invalid_arguments
execution_error
```

都：

```text
→ ERROR
```

具体原因由：

```text
ToolErrorInfo.code
```

表达。

Provider Adapter 不理解 Runtime 内部 ToolEffectStatus。

---

### 3.12 Tool Text 输出限制

大小按：

```text
UTF-8 encoded bytes
```

计算。

截断 marker：

```text
\n...[truncated]
```

marker 计入上限。

截断不得切断 Unicode/UTF-8 code point。

---

### 3.13 JSON Tool 输出超限

JSON：

```text
canonical JSON serialize
```

未超限：

```text
ToolJsonContent
```

超限：

```text
serialized JSON text
→ ToolTextContent
→ UTF-8 safe truncation
→ truncated=True
```

禁止返回非法 truncated JSON。

---

### 3.14 ToolExecutorConfig

```python
@dataclass(frozen=True)
class ToolExecutorConfig:
    max_calls_per_turn: int = 32
    max_concurrency: int = 8

    default_timeout: float | None = 60.0
    cancellation_grace_period: float = 2.0

    max_output_bytes: int = ...
    max_error_chars: int = ...
```

验证：

```text
max_calls_per_turn >= 1
max_concurrency >= 1
cancellation_grace_period >= 0
```

---

### 3.15 ToolBatchResult

```python
@dataclass(frozen=True)
class ToolBatchResult:
    calls: tuple[ToolCall, ...]
    results: tuple[ToolExecutionResult, ...]
    effects: tuple[ToolEffectRecord, ...]
```

必须：

```text
len(calls) == len(results)
```

以及：

```text
results[i].call_id == calls[i].id
results[i].name == calls[i].name
```

`results` 永远按原始 ToolCall 顺序。

该不变量只适用于正常完成的 batch。以下情况不返回部分 `ToolBatchResult`：

```text
Policy FAIL_RUN
Runtime unrecoverable error
Run cancellation
```

前两种情况抛出 `ToolBatchAborted`（携带已记录 effects）；Run cancellation
传播 cancellation。调用方不得把缺少结果的 batch 当作可 commit 结果。

```python
class ToolBatchAborted(RuntimeError):
    reason: RunError
    effects: tuple[ToolEffectRecord, ...]
```

`effects` 是中止前已经记录的 immutable effect view；它不是 partial results 的替代品。

---

### 3.16 ToolExecutor 正式接口

```python
class ToolExecutor:
    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: ToolExecutionPolicy,
        hooks: Sequence[RunHook],
        events: RunEventEmitter,
        config: ToolExecutorConfig,
    ) -> None:
        ...

    async def execute(
        self,
        calls: tuple[ToolCall, ...],
        context: ToolContext,
    ) -> ToolBatchResult:
        ...
```

`execute()` 只在整批完成且每个 call 都有 canonical terminal result 时返回。
它可以抛出 `ToolBatchAborted` 或传播 cancellation；两者均不产生部分
`ToolBatchResult`。

ToolExecutor 负责：

```text
lookup
policy
validation
hooks
events
scheduling
timeout
execution
cancellation
normalization
effect recording
```

不负责：

```text
Session transcript commit
next model turn
Run final status
```

---

### 3.17 Tool 执行顺序

固定：

```text
1. registry lookup
2. policy evaluation
3. argument validation
4. before_tool hooks
5. tool.started event
6. execute
7. normalize ToolExecutionResult
8. record ToolEffectRecord
9. tool.completed/tool.failed event
10. after_tool hooks
```

不执行底层 Tool 的结果统一使用下列 lifecycle 规则：

| 情况 | before_tool | tool.started | terminal event | after_tool | effect record |
| --- | --- | --- | --- | --- | --- |
| `Policy.REJECT` | 否 | 否 | `tool.failed(rejected)` | 是，接收 rejected result | 否 |
| unknown + ALLOW | 否 | 否 | `tool.failed(unknown_tool)` | 是，接收 error result | 否 |
| invalid arguments + ALLOW | 否 | 否 | `tool.failed(invalid_arguments)` | 是，接收 error result | 否 |
| `Policy.FAIL_RUN` | 否 | 否 | `tool.failed(policy_fail_run)` | 否 | 否 |

这些 terminal event 仅表示该 ToolCall 已得到 Runtime terminal outcome；不代表
底层 Tool 被启动。

Run cancellation 优先于正常 completion：若在 `before_tool` 期间触发，直接传播
cancellation，不发 `tool.started`/terminal tool event，不创建 effect，也不调用
`after_tool`；若在 `tool.started` 后触发，取消 Tool task，记录可确定的
`CANCELLED` 或 `UNKNOWN` effect，发 `tool.cancelled`，随后传播 cancellation，仍不调用
`after_tool`。因此 cancellation 绝不伪装成可供 batch commit 的 ToolResult。

---

### 3.18 Unknown Tool

lookup：

```text
tool=None
```

仍进入 Policy。

因此：

```text
unknown + FAIL_RUN
→ Run failure

unknown + REJECT
→ rejected

unknown + ALLOW
→ unknown_tool
```

---

### 3.19 Known Tool invalid arguments

Policy 在 schema validation 前。

因此：

```text
invalid arguments + REJECT
→ rejected
```

不会暴露：

```text
invalid_arguments
```

只有：

```text
ALLOW
```

后才校验 schema。

---

### 3.20 Batch 并发策略

唯一规则：

```text
所有 calls 都是 CONCURRENT
→ bounded parallel execution
```

否则：

```text
整个 batch 按原调用顺序串行
```

不采用分段并发。

Tool 的 lookup、Policy、schema validation 与 `before_tool` 是每 call 的准备阶段。
对于全 `CONCURRENT` batch，不同 call 的准备阶段和 `after_tool` hook 可以并发；
同一 call 内始终遵守 3.17 的顺序。任一 call 返回 `FAIL_RUN` 时，Runtime 停止
调度未开始的调用、取消可取消的活动调用，并以 `ToolBatchAborted` 结束 batch。

---

### 3.21 max_calls_per_turn

若：

```text
len(calls) > max_calls_per_turn
```

则：

```text
entire batch does not start
```

不得执行部分 calls。

---

### 3.22 Timeout precedence

统一：

```text
validated per-call override
>
Tool.timeout
>
ToolExecutorConfig.default_timeout
```

如果 Tool API 不支持 per-call override，则第一层不存在。

Timeout：

```text
cancel tool task
→ wait cancellation_grace_period
→ timeout error
```

普通 timeout 不自动 fail whole Run。

---

### 3.23 Ordinary Tool Failure

普通执行异常：

```text
normalize to ToolExecutionResult.error
```

其余 calls 继续。

只在：

```text
Run cancellation
Policy FAIL_RUN
Runtime unrecoverable error
```

时中止剩余 batch。

---

### 3.24 ToolEffectStatus

```python
class ToolEffectStatus(Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"
```

---

### 3.25 ToolEffectRecord

```python
@dataclass(frozen=True)
class ToolEffectRecord:
    call_id: str
    tool_name: str
    effect_kind: ToolEffectKind
    status: ToolEffectStatus

    content: ToolContent | None
    error: ToolErrorInfo | None

    transcript_committed: bool
```

---

### 3.26 Effect 状态不变量

只有底层 Tool 已实际启动的 call 才能创建 `ToolEffectRecord`。其 `call_id` 和
`tool_name` 必须与原始 `ToolCall` 完全一致，`effect_kind` 必须等于已注册 Tool 的
声明；`content/error` 始终满足与 `ToolExecutionResult` 相同的 XOR 约束。

| ToolEffectStatus | 对应模型可见 ToolResultStatus（若 batch 正常完成） | 含义 |
| --- | --- | --- |
| `SUCCEEDED` | `SUCCESS` | Tool 成功完成，`content` 存在且 `error` 为空。 |
| `FAILED` | `ERROR` | Tool 明确失败，`error` 存在。 |
| `TIMED_OUT` | `ERROR` | 超时，`error.code == "timeout"`。 |
| `CANCELLED` | 不适用：Run cancellation 中止 batch | 已启动操作确认未成功完成。 |
| `UNKNOWN` | 不适用：不可恢复中止 batch；正常 error result 时为 `ERROR` | 已启动操作的真实副作用无法确认。 |

#### SUCCEEDED

```text
content != None
error == None
```

#### FAILED

```text
content == None
error != None
```

#### TIMED_OUT

```text
content == None
error.code == "timeout"
```

#### CANCELLED

Runtime 可确认操作没有成功完成时：

```text
content == None
error.code == "cancelled"
```

#### UNKNOWN

Tool 已开始，但 Runtime 无法判断副作用是否已经发生：

```text
content == None
error != None
```

典型情况：

```text
remote request sent but connection lost
hardware command acknowledgement unavailable
detached process cleanup uncertain
```

---

### 3.27 transcript_committed

Effect 初始：

```text
False
```

只有完整 ToolExchangeBlock atomic commit 成功后：

```text
effect records for that batch
→ transcript_committed=True
```

由于对象 frozen，实现应产生新的 final view，而不是原地修改。

---

### 3.28 Tool Batch Atomic Commit

Model 返回 ToolCall 后：

```text
AssistantMessage
```

先保存在 Run-local pending exchange。

ToolExecutor 完整执行。

只有：

```text
all ToolExecutionResults terminal
after_tool hooks complete
Run not cancelled
```

才进行：

```text
assistant ToolCall message
+
all ToolResultMessages
```

一次逻辑原子提交。

失败/取消：

```text
entire pending ToolExchangeBlock not committed
```

因此 Session transcript 永不出现 dangling ToolCall。

---

### 3.29 Model-visible result order

调用：

```text
A B C
```

完成：

```text
B C A
```

commit：

```text
A B C
```

模型可见顺序与原始 ToolCall 一致。

---

### 3.30 retry_safe

完整公式：

```text
retry_safe =
    NOT EXISTS effect WHERE
        effect.effect_kind == SIDE_EFFECTING
        AND effect.transcript_committed == False
        AND effect.status IN {SUCCEEDED, UNKNOWN}
```

因此：

```text
pure Model failure
pure Context failure
read-only failure
unknown tool
invalid schema arguments
Policy REJECT
```

若没有未提交成功/未知 side effect：

```text
retry_safe=True
```

---

## 4. Builtin Tools 与 Skill

### 4.1 Builtin 默认原则

V1.1：

> `Agent()` 默认注册 0 个 builtin tools。

不会自动拥有：

```text
filesystem read
filesystem write
shell
read_skill
network
```

Builtin 是 available capability，不是 default permission。

---

### 4.2 Builtin 集合

```text
Filesystem:
read_file
write_file
edit_file
list_files
find_files
search_files

Execution:
shell

Skill:
read_skill
```

---

### 4.3 Workspace

```python
@dataclass(frozen=True)
class Workspace:
    root: Path
```

构造：

```text
absolute
resolve
must exist
must be directory
```

Filesystem 与 Shell 默认共享 Workspace。

---

### 4.4 FilesystemConfig

```python
@dataclass(frozen=True)
class FilesystemConfig:
    workspace: Workspace

    max_file_bytes: int
    max_read_bytes: int
    max_write_bytes: int

    max_list_results: int
    max_list_output_bytes: int

    max_search_results: int
    max_search_bytes: int

    include_hidden: bool = False
    max_depth: int = 32
```

所有 `*_bytes`：

```text
UTF-8/raw byte count as appropriate
```

---

### 4.5 Filesystem path 规则

所有 filesystem path：

```text
relative only
```

一律拒绝：

```text
absolute path
NUL
任何 ".." path component
```

所以：

```text
a/../b
../foo
foo/../../bar
```

全部非法。

`.` 可以 normalize。

最终 resolved target 必须位于 Workspace root。

---

### 4.6 Symlink

目录 symlink：

```text
list/find/search 不递归 follow
```

file symlink：

```text
read allowed only if resolved target in root
```

write/edit：

```text
目标为 file symlink
→ reject

父目录链 resolve 后必须仍在 root
```

write/edit 不跟随最终 file symlink。这样 atomic replace 的语义始终是替换
regular file，而不是在“替换链接本身”和“写入链接目标”之间依赖平台实现。

非 regular file：

```text
FIFO
socket
device
directory-as-file
```

拒绝。

V1.1 主支持平台：

```text
Linux / POSIX
```

---

### 4.7 read_file

输入：

```python
{
    "path": str,
    "offset": int | None,
    "limit": int | None,
}
```

`offset/limit`：

```text
Unicode code-point units
```

`max_read_bytes`：

```text
model-visible UTF-8 byte limit
```

`max_file_bytes`：

```text
source file maximum size
```

若：

```text
stat(file).size > max_file_bytes
```

则：

```text
file_too_large
```

V1.1 不要求支持任意大文件 streaming。

UTF-8 only。

invalid UTF-8：

```text
unsupported_file_encoding
```

成功返回：

```python
ToolTextContent(text=<selected UTF-8 text>, truncated=<byte limit reached>)
```

---

### 4.8 write_file

输入：

```python
{
    "path": str,
    "content": str,
    "create_parents": bool = False,
}
```

`max_write_bytes`：

```text
len(content.encode("utf-8"))
```

目标存在：

```text
must be regular file
```

目标不存在时，`create_parents=False` 要求父目录已存在；`create_parents=True`
只允许在 Workspace root 内创建缺失父目录。

成功返回：

```python
ToolJsonContent({"path": <relative path>, "bytes_written": <int>, "created": <bool>})
```

`path` 是已规范化的相对路径，`bytes_written` 是写入 UTF-8 字节数，`created` 仅表示
目标 file 是否在本次调用前不存在（不表示是否新建父目录）。

保留：

```text
POSIX mode
```

不承诺保留：

```text
owner
ACL
xattr
```

写：

```text
temp in same directory
→ flush
→ fsync where practical
→ atomic replace
```

---

### 4.9 edit_file

```python
{
    "path": str,
    "old_text": str,
    "new_text": str,
}
```

`old_text` 必须非空。目标文件在读取前受 `max_file_bytes` 限制；替换后的
UTF-8 内容受 `max_write_bytes` 限制，任一超限均不写回。

要求：

```text
old_text exactly one match
```

0：

```text
edit_not_found
```

> 1：

```text
edit_ambiguous
```

写回使用 write_file 相同 atomic replace 语义。

成功返回：

```python
ToolJsonContent({"path": <relative path>, "bytes_written": <int>})
```

其中 `path` 是已规范化的相对路径，`bytes_written` 是替换后文件的 UTF-8 字节数。

---

### 4.10 list_files

输入：

```python
{
    "path": str = ".",
}
```

只列直接子项。

结果：

```python
ToolJsonContent(
    {
        "items": (
            {
                "name": str,
                "type": "file" | "directory" | "symlink",
            },
            ...
        ),
        "truncated": bool,
    }
)
```

排序：

```text
name ascending
```

默认不含 hidden。

限制：

```text
max_list_results
max_list_output_bytes
```

处理：

```text
stable sort
→ item limit
→ serialized-byte limit
→ drop trailing items deterministically
```

---

### 4.11 Relative Glob Validator

统一：

```text
validate_relative_glob()
```

供：

```text
find_files.pattern
search_files.glob
```

共同使用。

拒绝：

```text
absolute glob
NUL
任何 ".." component
```

允许：

```text
*
?
[]
**
/
```

---

### 4.12 find_files

输入：

```python
{
    "pattern": str,
    "path": str = ".",
}
```

结果：

```python
ToolJsonContent(
    {
        "items": (
            {"path": str},
            ...
        ),
        "truncated": bool,
    }
)
```

排序：

```text
relative path lexical ascending
```

限制：

```text
max_depth
max_search_results
max_search_bytes
```

`max_search_bytes`：

```text
final model-visible serialized output bytes
```

不是 source scan bytes。

---

### 4.13 search_files

输入：

```python
{
    "query": str,
    "path": str = ".",
    "glob": str | None = None,
    "case_sensitive": bool = True,
}
```

`glob` 必须使用同一个 relative glob validator。

V1.1：

```text
literal search only
```

不定义 regex。

只搜索：

```text
UTF-8 regular files
```

invalid UTF-8/binary：

```text
skip
```

结果：

```python
ToolJsonContent(
    {
        "items": (
            {
                "path": str,
                "line": int,
                "column": int,
                "text": str,
            },
            ...
        ),
        "truncated": bool,
    }
)
```

排序：

```text
path
→ line
→ column
```

限制：

```text
max_search_results
max_search_bytes
```

达到限制：

```text
stop collecting
truncated=True
```

不能返回半个 item。

---

### 4.14 Shell Tool 定位

V1.1 Shell：

```text
explicit capability
not default
not sandbox
non-interactive
Linux/POSIX only
```

执行：

```text
/bin/sh -lc <command>
```

因此支持 shell syntax：

```text
pipe
redirection
command substitution
&
```

---

### 4.15 ShellConfig

```python
@dataclass(frozen=True)
class ShellConfig:
    workspace: Workspace

    max_command_bytes: int
    max_stdout_bytes: int
    max_stderr_bytes: int

    default_timeout: float | None = None
    max_timeout: float | None = None
    cancellation_grace_period: float = 2.0

    env: FrozenJsonObject | None = None
```

`env` 为 `None` 时不加 overrides；非 `None` 时必须是 `str -> str` 的平面 mapping。
拒绝数字、布尔、null、数组、嵌套 object 或非字符串 key/value。

---

### 4.16 Shell 输入

```python
{
    "command": str,
    "cwd": str | None = None,
    "timeout": float | None = None,
}
```

cwd：

```text
relative to Workspace.root
```

absolute / `..`：

```text
reject
```

---

### 4.17 Shell stdin

固定：

```text
stdin = /dev/null
```

不继承 Runtime stdin。

这样避免 interactive command 卡住。

---

### 4.18 Shell Environment

Tool 创建时：

```python
base_env = dict(os.environ)
```

以后每次调用：

```text
copy base_env
→ explicit overrides
→ subprocess
```

不得修改全局 `os.environ`。

V1.1 不做 secret filtering。

---

### 4.19 Shell stdout/stderr

先按 raw bytes 限制：

```text
max_stdout_bytes
max_stderr_bytes
```

再：

```python
decode("utf-8", errors="replace")
```

非法 UTF-8：

```text
U+FFFD replacement
```

结果：

```python
ToolJsonContent(
    {
        "exit_code": int,
        "stdout": str,
        "stderr": str,
        "stdout_truncated": bool,
        "stderr_truncated": bool,
    }
)
```

Shell 先独立截断 stdout/stderr，再构造该 JSON result。若整个 JSON 仍超过
`ToolExecutorConfig.max_output_bytes`，按 3.13 的通用规则转换为截断
`ToolTextContent`；这时原 JSON 字段不再保证模型可见。

---

### 4.20 Shell exit_code

正常：

```text
>= 0
```

signal termination：

```text
-N
```

例如：

```text
SIGTERM → -15
SIGKILL → -9
```

Timeout 不作为成功 JSON output。

而是：

```text
ToolExecutionResult.error.code="timeout"
```

---

### 4.21 Shell timeout

Shell argument：

```text
timeout
```

就是 ToolExecutor per-call override。

必须：

```text
> 0
```

若配置：

```text
ShellConfig.max_timeout
```

则：

```text
effective requested timeout
= min(requested, max_timeout)
```

最终 precedence：

```text
validated shell argument timeout
>
Tool.timeout
>
ToolExecutor default_timeout
```

---

### 4.22 Shell process lifecycle

启动：

```text
new POSIX session/process group
```

timeout/cancel：

```text
SIGTERM process group
→ wait grace period
→ SIGKILL
```

对于：

```text
double-fork
setsid
daemonize
```

逃离原 process group 的子进程：

```text
cleanup best-effort only
```

V1.1 不提供 persistent/background process management。

---

### 4.23 Skill 定位

Skill：

```text
task guidance
workflow
knowledge
tool usage instructions
```

Skill 不是：

```text
Tool
Permission
Executor
Runtime
```

Skill 不能：

```text
grant tools
change Policy
replace base system prompt
```

---

### 4.24 SkillConfig

```python
@dataclass(frozen=True)
class SkillConfig:
    max_description_chars: int = 512
    max_body_bytes: int = 64 * 1024
```

必须：

```text
>= 1
```

---

### 4.25 Skill 文件

固定：

```text
<skill-dir>/SKILL.md
```

frontmatter：

```yaml
---
name: ros2-debug
description: Diagnose ROS 2 communication and runtime problems.
---
```

frontmatter 必须有成对且位于文件开头的 `---` 分隔符。`name` 与 `description`
必须各出现至多一次且值为字符串；拒绝 YAML tag、alias、anchor、非标量值和超出
`SkillConfig.max_body_bytes` 的 frontmatter/body 文件。任何违反规则的文件均按
4.30 的 non-strict invalid-skill 路径处理。

识别：

```text
name
description
```

未知 key：

```text
ignored
```

name：

```text
^[a-z][a-z0-9-]{0,63}$
```

---

### 4.26 SkillSource / Metadata

```python
class SkillSource(Enum):
    PROJECT = "project"
    USER = "user"
```

```python
@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    path: Path
    source: SkillSource
```

path 只用于 Runtime 内部，不进入模型。

---

### 4.27 Skill discovery roots

```text
<project>/.roboagent/skills/
~/.roboagent/skills/
```

只扫描：

```text
direct child directories
```

不递归。

不 follow Skill directory symlink。

---

### 4.28 Discovery order

每个 root：

```text
sort directory name by Unicode code-point order
→ parse SKILL.md
```

保证 deterministic。

---

### 4.29 Duplicate Skill

同 source 同 name：

```text
all conflicting entries ignored
```

Diagnostic：

```python
SkillDiagnostic(
    code="duplicate_skill_name",
    name=...,
    source=...,
    paths=(sorted canonical paths...),
)
```

跨 source：

```text
PROJECT overrides USER
```

同时记录：

```python
SkillDiagnostic(
    code="skill_overridden",
    name=...,
    selected_path=...,
    ignored_path=...,
)
```

---

### 4.30 Skill invalid behavior

非法 metadata：

```text
skip
diagnostic
Agent continues
```

默认 non-strict。

Skill body symlink/resolve escape：

```text
reject
```

---

### 4.31 SkillCatalog

Discovery 在：

```text
Agent/Harness initialization
```

执行。

形成 immutable：

```text
SkillCatalog
```

运行中修改文件：

```text
no automatic effect
```

显式：

```python
SkillManager.reload()
```

才刷新。

Reload 只影响未来 ContextSnapshot。

每次 Run 在首次 capture `ContextSnapshot` 时绑定当时的 immutable `SkillCatalog`
revision。该 revision 同时用于该 Run 的 skill metadata 投影和 `read_skill` 名称解析；
运行中发生 `SkillManager.reload()` 不得改变 active Run 所看到的 Skill body。

reload 创建新 revision，只影响随后创建的 Run。`read_skill` Tool 必须从 `ToolContext`
或等价的 Run-scoped immutable catalog 获取内容，不能在执行时直接读取 manager 的最新 catalog。

---

### 4.32 Skill body

必须：

```text
UTF-8
```

文件大小：

```text
<= SkillConfig.max_body_bytes
```

超限：

```text
skill_too_large
```

V1.1 不返回 partial/truncated Skill body。

非法 UTF-8：

```text
skill_read_error
```

---

### 4.33 read_skill Tool

定义：

```text
name:
read_skill

description:
Read the instructions for one available RoboAgent skill.
```

schema：

```json
{
  "type": "object",
  "properties": {
    "name": {
      "type": "string"
    }
  },
  "required": ["name"],
  "additionalProperties": false
}
```

execution mode：

```text
CONCURRENT
```

effect kind：

```text
READ_ONLY
```

错误：

```text
unknown_skill
skill_unavailable
skill_too_large
skill_read_error
```

输出：

```text
ToolTextContent
```

---

### 4.34 read_skill Registration

Runtime `Agent()` 不自动注册 `read_skill`。

显式：

```python
registry.register(
    create_read_skill_tool(skill_manager)
)
```

SkillCatalog 和 read_skill capability 相互独立。

---

## 5. RunResult、Events、Hooks 与生命周期

### 5.1 RunStatus

```python
class RunStatus(Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

如：

```text
max_turns
```

使用：

```text
FAILED + RunError(code="max_turns")
```

---

### 5.2 RunResult

```python
@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: RunStatus

    output: AssistantMessage | None
    usage: Usage | None

    error: RunError | None
    cleanup_errors: tuple[RunError, ...]

    effects: tuple[ToolEffectRecord, ...]
    retry_safe: bool
```

---

### 5.3 after_model 成功/失败

正常：

```text
ModelResponse obtained
→ model.completed
→ after_model hooks
```

只有 after_model 成功后，AgentLoop 才接受该 Model turn。

#### after_model 失败，无 ToolCall

```text
assistant message not committed
Run FAILED
RunResult.output=None
```

Canonical ModelResponse 可以进入 diagnostics，但不是 successful output。

#### after_model 失败，有 ToolCall

```text
ToolExecutor not invoked
no Tool effect
assistant message not committed
Run FAILED
```

普通 after_model failure：

```text
primary Run error
```

不是 cleanup error。

---

### 5.4 Tool / Hook / Effect 顺序

单 Tool：

```text
before_tool
→ tool.started
→ execute
→ ToolExecutionResult
→ ToolEffectRecord
→ tool.completed/tool.failed
→ after_tool
```

after_tool failure：

```text
effect remains
batch not committed
Run FAILED
```

---

### 5.5 tool_batch.committed

V1.1 必须提供 Runtime Event：

```text
tool_batch.committed
```

成功：

```text
all results terminal
after_tool hooks succeed
atomic transcript commit succeeds
→ emit tool_batch.committed
```

未 commit：

```text
never emit
```

payload 至少：

```text
run_id
turn/assistant message identity
tool_call_ids
```

---

### 5.6 Event 类型

V1.1 至少：

```text
run.started
run.completed
run.failed
run.cancelled

model.started
model.completed
model.failed
model.cancelled

tool.started
tool.completed
tool.failed
tool.cancelled

tool_batch.committed
```

可选：

```text
model.delta
diagnostic
```

---

### 5.7 Event scope

V1.1 EventSubscription：

> per-Run only。

例如：

```python
run.subscribe(...)
```

Run A subscription 不接收 Run B event。

Agent-wide / Session-wide event bus 属于更上层聚合能力。

---

### 5.8 Event sequence

每 Run：

```text
monotonic sequence
```

`run.started`：

```text
first event
```

Run terminal：

```text
exactly one
```

并且是 lifecycle 最后 event。

---

### 5.9 EventSubscriptionConfig

```python
@dataclass(frozen=True)
class EventSubscriptionConfig:
    max_queue_size: int = 256
    replay_limit: int = 256
```

必须：

```text
>= 1
```

`replay_limit` 也必须 `>= 1`。每个 Run 保留独立且最多 `replay_limit` 条的 event
history；满时按最旧优先丢弃 non-terminal event。terminal event 到来时会置换最旧的
non-terminal event，故 history 始终至少保留 final terminal event。

否则：

```text
EventConfigurationError
```

---

### 5.10 Event delivery

每 subscription：

```text
independent bounded queue
```

Runtime：

```text
enqueue
do not await subscriber processing
```

慢 consumer 不阻塞 Run。

#### Late subscription / replay

`run.subscribe()` 在同一同步原语内完成：

```text
snapshot retained history
→ register live subscription
→ enqueue snapshot
→ receive later events
```

因此不存在 replay/live gap。Run 已 terminal 时仍允许订阅：它回放 retention 内
history，且至少包含 final terminal event；之后 subscription 自然结束。

---

### 5.11 Queue overflow

普通 event：

```text
drop oldest non-terminal
→ enqueue newest
```

Run terminal event：

1. queue 未满 → enqueue；
2. 满 → evict oldest non-terminal；
3. 如果全是 terminal → evict oldest terminal；
4. enqueue latest terminal。

因为 subscription per-Run，因此不会覆盖其他 Run terminal。

---

### 5.12 Subscriber failure

consumer exception：

```text
close subscription
record diagnostic
Run unaffected
other subscriptions unaffected
```

subscriber cancellation：

```text
close queue
release pending events
```

---

### 5.13 Event diagnostics

Queue overflow / subscriber failure：

```text
internal diagnostics / counters
```

不得通过同一 EventEmitter 递归生成自身错误 event。

---

### 5.14 Hook 定位

V1.1 Hook：

```text
deterministic lifecycle callback
```

能：

```text
observe
fail run
```

不能：

```text
replace ModelRequest
replace ModelResponse
replace ToolResult
mutate Session directly
```

---

### 5.15 HookDecision

```python
class HookDecision(Enum):
    CONTINUE = "continue"
    FAIL_RUN = "fail_run"
```

---

### 5.16 Hook Context

```python
@dataclass(frozen=True)
class RunHookContext:
    run_context: RunContext
```

```python
@dataclass(frozen=True)
class ModelHookContext:
    run_context: RunContext
    model_context: ModelContext
```

```python
@dataclass(frozen=True)
class ToolHookContext:
    run_context: RunContext
```

```python
@dataclass(frozen=True)
class RunEndHookContext:
    run_context: RunContext
    provisional_status: RunStatus
    primary_error: RunError | None
    effects: tuple[ToolEffectRecord, ...]
```

---

### 5.17 RunHook

```python
class RunHook(Protocol):
    async def on_run_start(
        self,
        context: RunHookContext,
    ) -> None:
        ...

    async def before_model(
        self,
        context: ModelHookContext,
    ) -> HookDecision:
        ...

    async def after_model(
        self,
        context: ModelHookContext,
        response: ModelResponse,
    ) -> None:
        ...

    async def before_tool(
        self,
        context: ToolHookContext,
        call: ToolCall,
    ) -> HookDecision:
        ...

    async def after_tool(
        self,
        context: ToolHookContext,
        result: ToolExecutionResult,
    ) -> None:
        ...

    async def on_run_end(
        self,
        context: RunEndHookContext,
    ) -> None:
        ...
```

---

### 5.18 Hook ordering

多个 Hook：

```text
registration order
sequential await
```

不并发。

---

### 5.19 Hook timeout

Runtime 配置：

```text
hook_timeout
cleanup_hook_timeout
```

普通 Hook timeout：

```text
Run FAILED
```

---

### 5.20 Hook cancellation

Run cancellation 优先于普通 Hook timeout。

普通 Hook：

```text
on_run_start
before_model
after_model
before_tool
after_tool
```

运行中如果 Run cancelled：

```text
cancel Hook
Run CANCELLED
```

如果 cancellation 与 timeout 竞争，只要 cancellation 已被 Runtime 接受：

```text
CANCELLED wins
```

---

### 5.21 after_model cancellation

只有：

```text
ModelResponse obtained
AND
Run not cancelled
```

才启动 after_model。

若 completion 后但 before hook start 已 cancelled：

```text
after_model not started
response not committed
Run CANCELLED
```

若 after_model 运行中取消：

```text
cancel hook
response not committed
Run CANCELLED
```

---

### 5.22 after_tool cancellation

Tool result 已产生后：

```text
if not cancelled
→ after_tool
```

若 cancellation 已观察：

```text
do not start new after_tool
```

Effect 已存在则保留。

after_tool 中途取消：

```text
cancel hook
effect remains
batch not committed
Run CANCELLED
```

---

### 5.23 on_run_end

唯一 cancellation 后仍执行的 Hook。

使用独立 cleanup timeout/scope。

不直接受已经 cancelled 的 Run token 立即终止。

---

### 5.24 run.started 与 on_run_start

顺序固定：

```text
Session ownership acquired
→ Run accepted
→ emit run.started
→ on_run_start hooks
```

因此 run.started 永远第一。

on_run_start failure：

```text
run.started
→ failure
→ provisional FAILED
→ on_run_end
→ final run.failed
```

---

### 5.25 Model hook/event ordering

```text
before_model
→ model.started
→ Model.stream
→ model.completed
→ after_model
```

before_model FAIL_RUN：

```text
model.failed(reason=hook_error)
→ no Model invocation
```

Model invocation failure：

```text
model.started
→ model.failed
```

---

### 5.26 Tool hook/event ordering

```text
before_tool
→ tool.started
→ execute
→ canonical result
→ effect
→ terminal tool event
→ after_tool
```

before_tool FAIL_RUN：

```text
tool not executed
tool.failed(reason=hook_error)
```

普通 ToolExecutionResult error：

```text
tool.failed
→ after_tool still receives failed canonical result
```

---

### 5.27 Provisional 与 Final status

Runtime 先确定：

```text
provisional_status
```

再调用 `on_run_end`。

Hook 看到：

```text
provisional status
```

而不是 final。

---

### 5.28 on_run_end failure

若 provisional：

```text
COMPLETED
```

cleanup failure：

```text
final FAILED
primary error = cleanup failure
```

如果 assistant output 已在 provisional completion 前作为 durable transcript fact 提交，
`RunResult.output` 必须保留该 committed `AssistantMessage`。`status=FAILED` 与
`error=cleanup failure` 仅表达 cleanup 失败，不撤销已提交 conversation fact。

若 provisional：

```text
FAILED
```

cleanup failure：

```text
final FAILED
primary error unchanged
cleanup_errors += error
```

若 provisional：

```text
CANCELLED
```

cleanup failure：

```text
final CANCELLED
cleanup_errors += error
```

---

### 5.29 Final Run Event

顺序：

```text
determine provisional
→ on_run_end
→ determine final
→ build RunResult
→ emit final terminal event
```

Terminal event 表示：

> Runtime cleanup 已结束，RunResult 已最终确定。

---

### 5.30 Pending input terminal race

Run 在消费前：

```text
completed
failed
cancelled
max-turn failure
```

pending input：

```text
remains in Session queue
```

下一 Run 消费。

Session close：

```text
reject remaining queued inputs
```

不得静默丢失。

---

## 6. Public API、迁移与验收

本章及全文定义的是 **V1.1 目标接口与目标语义**，不是对当前 Runtime
实现状态的声明。当前 Runtime 仍可能使用旧的 Run-level `steer()` /
`follow_up()` 和旧 `ToolExecutor`；这些实现必须在后续实现阶段迁移，才可宣称
符合本规范。

### 6.1 Package path

V1.1 保持当前实际路径：

```text
roboagent.tool
roboagent.skill
```

不为了 aesthetics 改成：

```text
roboagent.tools
roboagent.skills
```

优先级：

```text
semantic stability
>
API stability
>
directory naming
```

---

### 6.2 Public API

普通：

```python
from roboagent import Agent, Session
```

Model：

```python
from roboagent.model import (
    Model,
    ModelProvider,
    ModelSettings,
)
```

Context：

```python
from roboagent.context import ContextManager
```

Tool：

```python
from roboagent.tool import (
    Tool,
    ToolRegistry,
    ToolExecutor,
)
```

Skill：

```python
from roboagent.skill import SkillManager
```

---

### 6.3 Default Agent capability

```python
Agent(...)
```

默认：

```text
0 builtin Tool
```

用户/Harness 必须显式组合能力。

---

### 6.4 Convenience profiles

可以提供：

```text
filesystem_readonly
filesystem_writable
shell
skills
```

但 factory 仅返回 ordinary Tool instances。

不产生第二套执行路径。

---

### 6.5 ContextManager migration

旧：

```python
prepare(run_context)
```

或隐式 Session Context：

```text
deprecated
```

V1.1：

```python
prepare(snapshot, cancellation)
```

Compatibility adapter 可以存在。

V1.2 计划删除旧 signature。

---

### 6.6 Model migration

旧 Model：

```text
provider raw response
non-canonical stream
generate-only
```

通过：

```text
LegacyModelAdapter
```

迁移。

Kernel 内禁止 provider raw objects。

---

### 6.7 Tool return migration

旧 Tool：

```python
return "abc"
```

兼容：

```text
str → ToolTextContent
```

JSON-compatible：

```text
→ ToolJsonContent
```

其他对象：

```text
unsupported_output
```

V1.2 要求直接 canonical ToolContent。

---

### 6.8 Skill migration

| Current              | V1.1                     |
| -------------------- | ------------------------ |
| discovery            | keep                     |
| metadata             | normalize                |
| reload               | keep/add                 |
| enable/disable       | optional catalog filter  |
| select/router        | remove from core         |
| SkillExecutor        | deprecated               |
| direct Skill execute | remove                   |
| body loading         | SkillManager.load        |
| model access         | explicit read_skill Tool |

---

### 6.9 Pending input migration

旧：

```text
steer/follow-up immediately append transcript
```

不保留兼容模式。

V1.1：

```text
Session.steer(UserMessage) / Session.follow_up(UserMessage)
→ queue only
```

当前 Run-level `steer()` / `follow_up()` 属于旧接口；迁移 adapter 至多将用户
文本规范化为 `UserMessage` 后转交 Session queue，绝不能接受或合成 assistant/tool
message，也不得恢复立即写 transcript 的旧行为。

---

### 6.10 Same Session concurrent Run migration

旧实现如果允许：

```text
same Session concurrent Runs
```

V1.1：

```text
second Run → SessionBusyError
```

不提供 compatibility mode。

---

### 6.11 实施顺序

推荐：

```text
Phase 1
V1 semantic baseline
+ Session ownership

Phase 2
Canonical JSON/core types

Phase 3
ModelProvider
+ canonical streaming

Phase 4
ContextSnapshot
+ Prompt
+ transcript projection

Phase 5
ToolRegistry
+ ToolExecutor
+ Policy
+ Effect

Phase 6
Filesystem
+ Shell

Phase 7
SkillCatalog
+ read_skill

Phase 8
Events
+ Hooks
+ RunResult

Phase 9
Compatibility cleanup
+ examples
+ final semantic suite
```

---

### 6.12 Core / Session tests

必须：

```text
one active Run per Session
atomic acquire ownership
concurrent acquire exactly one succeeds
wrong Run cannot release
terminal always releases

pending input one consumer
input with no active Run queues
closed Session rejects
next Run consumes queued input
only UserMessage can enqueue
InputReceipt contains input_id, session_id, and strictly increasing sequence
```

---

### 6.13 Frozen JSON tests

```text
deep freeze
stable iteration
stable serialization
structural equality

NaN rejected
Infinity rejected
non-string key rejected

original mutable input mutation
does not affect canonical object
```

---

### 6.14 Provider tests

```text
known model resolve
unknown model

shared provider
two Models

external Provider not closed
owned Provider closes once
provider close failure deterministic
```

---

### 6.15 Streaming tests

```text
exactly one ResponseStarted

text-only
tool-only
text + tool
multiple tool calls

interleaved deltas

duplicate ResponseStarted
duplicate ToolCallStarted
delta before Started
delta after Completed
duplicate Completed

fragmented JSON
invalid JSON
generated call ID
duplicate call ID
sequence begins at 0 and increments by exactly 1
call_id unique across the complete ModelResponse, not merely one call_index

incomplete call
final ToolCall mismatch

Usage cumulative

network failure
provider failure
protocol failure
capability failure

success exactly one ResponseCompleted
failure no ResponseCompleted
early close releases resources
```

---

### 6.16 Model capability tests

```text
existing multimodal preserved

unsupported input modality
unsupported output modality

tool_calling=False + tools
→ ModelCapabilityError

parallel_tool_calls=False
+ multiple provider calls
→ capability/protocol error

parallel_tool_calls does not affect ToolExecutor scheduling
```

---

### 6.17 Context tests

```text
snapshot deep immutable
Session mutation after snapshot irrelevant

prepare cancellation
Prompt cancellation
Prompt failure

ContextManager no hidden Session access

ToolExchangeBlock atomic projection

no orphan result
no partial exchange

Skill metadata deterministic
```

---

### 6.18 ToolRegistry / Policy tests

```text
valid registration
duplicate rejection
explicit replace
invalid name
invalid schema

lookup → policy → validation ordering

unknown + ALLOW
→ unknown_tool

unknown + REJECT
→ rejected

unknown + FAIL_RUN
→ Run failure

invalid args + REJECT
→ rejected

invalid args + ALLOW
→ invalid_arguments
```

---

### 6.19 ToolExecutor tests

```text
execute returns ToolBatchResult

result stable order

all CONCURRENT
→ bounded parallel

one SERIAL
→ full batch serial

max_calls_per_turn
max_concurrency

timeout precedence
timeout cleanup

ordinary failure lets rest continue

Run cancellation aborts batch
Policy.FAIL_RUN and unrecoverable executor error raise ToolBatchAborted
aborted batch never returns a partial ToolBatchResult
concurrent FAIL_RUN stops unscheduled calls and cancels cancellable active calls
REJECT / unknown+ALLOW / invalid-arguments+ALLOW hook-event-effect mapping
```

---

### 6.20 Tool result/effect tests

```text
content/error XOR

UTF-8 truncation
marker included in limit

large JSON → valid truncated text

ToolErrorInfo bound

SUCCEEDED invariant
FAILED invariant
TIMED_OUT invariant
CANCELLED invariant
UNKNOWN invariant

transcript_committed false before commit
true after commit
committed batch effects retained in a new transcript_committed=True final view

retry_safe formula
```

---

### 6.21 Atomic transcript tests

```text
assistant ToolCall not committed early

successful full batch atomic commit

result order original

cancellation → no dangling ToolCall

after_tool failure
→ effect retained
→ transcript uncommitted

tool_batch.committed
only after successful commit
```

---

### 6.22 Filesystem tests

```text
absolute path rejected
any .. rejected
NUL rejected

file symlink inside root
symlink escape rejected
directory symlink not traversed

non-regular rejected

read source size limit
read UTF-8
invalid UTF-8
offset/limit by Unicode chars

write UTF-8 byte limit
mode preservation
atomic write
create_parents
write/edit final file symlink rejected
write/edit success canonical JSON includes path and bytes_written

edit zero match
multiple match
atomic write
empty old_text rejected
max_file_bytes checked before edit; replacement checked against max_write_bytes

list items/truncated
stable ordering
hidden policy
result count/byte limit

find shared glob validator
stable ordering
max depth
result limit

search shared glob validator
literal semantics
binary skip
items/truncated
byte limit
```

---

### 6.23 Shell tests

```text
Linux/POSIX guard

/bin/sh -lc

stdin=/dev/null

pipeline
redirection
background syntax

cwd relative
absolute cwd reject
.. reject
workspace escape reject

env snapshot
os.environ unchanged
env accepts only str -> str

raw stdout/stderr byte limits
invalid UTF-8 replacement
shell stream truncation precedes global Tool output fallback

normal exit code
signal negative exit code

per-call timeout
max_timeout cap

SIGTERM
→ grace
→ SIGKILL

detached child cleanup best-effort
```

---

### 6.24 Skill tests

```text
frontmatter
unpaired delimiters rejected
name/description must be scalar strings
YAML tags, aliases, and anchors rejected

name validation
description required
unknown key ignored

project/user discovery

deterministic directory sort

same-source duplicates ignored
diagnostic includes sorted paths

project override diagnostic

directory symlink ignored
SKILL.md escape rejected

SkillCatalog immutable
reload affects future snapshots
active Run keeps one catalog revision for metadata and read_skill body

description normalization
description char limit

body UTF-8
body byte limit
oversized body rejected
no partial body

read_skill explicit registration
unknown skill
read error

Skill cannot grant absent Tool
Skill cannot change Policy
```

---

### 6.25 Event tests

```text
per-Run scope
late subscription atomically replays bounded retained history then receives live events
no replay/live gap

run.started first

exactly one Run terminal

sequence monotonic

slow subscriber does not block

queue size >=1

overflow deterministic

latest terminal preserved
late terminal subscription receives the retained terminal event

subscriber error isolated

subscriber cancel isolated

diagnostic no recursive emitter loop
```

---

### 6.26 Hook tests

```text
registration order

run.started before on_run_start

on_run_start fail
→ run.failed

before_model fail
after_model fail

after_model text response:
no commit
output=None

after_model tool response:
ToolExecutor not invoked

before_tool fail
after_tool fail
REJECT / unknown / invalid-arguments ordering

effect retained after after_tool fail

Run cancellation during hook
→ Hook cancelled
→ CANCELLED wins over timeout

on_run_end receives provisional status

completed + cleanup failure
→ final FAILED
→ committed output retained

failed + cleanup failure
→ primary preserved

cancelled + cleanup failure
→ final CANCELLED

terminal Event after cleanup
```

---

### 6.27 Steer / Follow-up tests

```text
enqueue does not mutate transcript

active Run not automatically cancelled

Tool batch not automatically cancelled

consume only at legal boundary

consume exactly once

no active Run
→ accepted into queue

completed before consume
→ remains queue

failed before consume
→ remains queue

cancelled before consume
→ remains queue

max-turn failure
→ remains queue

next Run consumes

Session closed rejects
```

---

### 6.28 Migration tests

```text
Legacy ContextManager adapter

Legacy Model adapter

Legacy Tool return normalizer

SkillExecutor deprecation

DeprecationWarning behavior

old immediate transcript mutation unsupported
legacy Run-level steer/follow_up migrated to Session UserMessage queue only

same Session concurrent Run rejected
```

---

### 6.29 V1.1 Definition of Done

V1.1 只有在以下条件全部满足时完成。

#### Runtime

```text
Agent / Session / Run boundaries stable

Session single-writer semantics deterministic

Pending input lifecycle deterministic

Run cancellation deterministic

Transcript never contains incomplete ToolExchangeBlock
```

#### Model

```text
ModelProvider explicit

Provider ownership explicit

Provider-specific logic never enters AgentLoop

One canonical streaming protocol

ToolCall state machine deterministic

Existing multimodal capability preserved
```

#### Context

```text
ContextSnapshot deeply immutable

ContextManager explicit-input only

Context prepare cancellable

RunContext != ModelContext

Prompt explicit and cancellable

ToolExchangeBlock projection structurally safe
```

#### Tool

```text
ToolRegistry deterministic

ToolExecutor formal API

Lookup/policy/validation order fixed

Tool outputs canonical

Batch scheduling deterministic

Limits explicit

Policy semantics explicit

Transcript facts separated from side effects
```

#### Builtin

```text
No builtin enabled automatically

Filesystem root/path rules explicit

Filesystem APIs fully specified

Shell is explicit non-interactive POSIX capability

Shell timeout/process lifecycle explicit
```

#### Skill

```text
Format deterministic

Discovery deterministic

Catalog immutable

Metadata rendering deterministic

Body bounded and lazy-loaded

Skill is guidance, never authority

read_skill explicit ordinary Tool
```

#### Events / Hooks

```text
Event subscriptions per-Run

bounded non-blocking queue

tool_batch.committed mandatory

Events are observation only

Hook ordering/failure/cancellation deterministic

Terminal Event reflects final post-cleanup status
```

#### Extensibility

未来增加：

```text
MCP
ROS2
Robot Tools
Web
Memory
new Provider
new Skill
```

时，不需要在 AgentLoop 中增加 capability-specific branching。

---

## 最终设计原则

RoboAgent V1.1 的核心边界最终定义为：

```text
ModelProvider
= model resolution + provider lifecycle

Model
= one model invocation streaming protocol

ContextSnapshot
= immutable facts for one context build

ContextManager
= snapshot → ModelContext

Prompt
= base Agent instructions

ToolRegistry
= executable capability catalog

ToolExecutor
= lookup + policy + validation + scheduling + execution

ToolBatchResult
= canonical tool batch outcome before transcript commit

ToolEffectRecord
= real-world execution fact

ToolResultMessage
= model-visible tool execution result

Skill
= bounded lazy-loaded task guidance

Session
= single-writer durable conversation fact log

RunResult.effects
= execution effects not necessarily committed to conversation

Events
= per-Run bounded observations

Hooks
= deterministic lifecycle callbacks

tool_batch.committed
= durable ToolExchangeBlock commit observation
```

RoboAgent V1.1 的最终验收原则是：

> 同一份规格交给两个独立实现者，在 Session ownership、Model streaming、Context 构建、Tool execution、Tool side effects、Builtin 边界、Skill discovery、Event delivery、Hook lifecycle 和 steer/follow-up 等关键路径上，应能够得到行为一致的实现，并通过同一套 semantic test suite。
