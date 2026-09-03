# RoboAgent v1 Agent Runtime Kernel 完整规格

## 1. 定位、范围与总体架构

### 1.1 项目定位

RoboAgent v1 是一个：

> **模型无关、工具无关、模态无关、可嵌入的异步 Agent Runtime Kernel。**

它定义：

> **Agent 如何运行，以及 Runtime 如何以稳定、统一、可验证的协议管理模型调用、工具调用、控制输入和多模态消息。**

它不定义：

> **Agent 应如何安全地操作机器人、浏览器、代码仓库、数据库或其他业务系统，也不负责具体媒体处理、长期存储、业务编排或领域安全策略。**

v1 负责：

* Agent / Session / Run 生命周期；
* provider-neutral canonical transcript；
* modality-neutral canonical message；
* Text / Image / Audio / File 内容；
* MediaSource；
* MediaLimits；
* MediaResolver；
* ResolvedMedia；
* ModelContext；
* RunContext；
* ModelCapabilities；
* ContextManager；
* ToolResolver / FrozenToolSet；
* Model streaming；
* Tool Call / Tool Result；
* sequential / parallel Tool execution；
* ToolExecutionPolicy；
* cancel / steer / follow-up；
* timeout / max turns；
* transcript safe commit；
* internal runtime state；
* public RunState snapshot；
* lifecycle events；
* event replay；
* `continue_run()`；
* canonical / semantic / capability validation；
* compatibility migration。

v1 明确不实现：

```text
STT
TTS
VAD
AEC

audio resampling
image resize
OCR
image captioning
video frame extraction
media transcoding

automatic modality conversion
automatic multimodal model routing

media blob store
Session persistence
raw multimodal durable Event persistence

Persistent Memory
RAG
MCP

Handoff
Sub-Agent
Multi-Agent

Approval
Permission
Robot Safety
Resource Lock

Gateway
Cron
Telemetry Backend
```

这些能力未来都应建立在 Kernel 的稳定协议和扩展接口之上。

---

### 1.2 v1 多模态支持范围

必须明确区分：

```text
canonical protocol support
```

和：

```text
end-to-end provider support
```

v1 定义：

| Modality | Canonical Protocol | Tool I/O | E2E Adapter required |
| -------- | -----------------: | -------: | -------------------: |
| Text     |                  ✅ |        ✅ |                    ✅ |
| Image    |                  ✅ |        ✅ |                    ✅ |
| Audio    |                  ✅ |        ✅ |             Optional |
| File     |                  ✅ |        ✅ |             Optional |

因此：

> **RoboAgent v1 的 canonical protocol 正式支持 Text、Image、Audio、File。**

但 v1 发布阻塞的 Provider 验收只要求：

```text
Text E2E
Text + Image E2E
Image ToolResult E2E
```

Audio / File 必须具备完整的：

```text
canonical representation
validation
Tool I/O
capability negotiation
error semantics
```

但具体 Provider Adapter 的真实端到端支持不是 v1 发布阻塞项。

Realtime Audio 不属于 v1 canonical transcript。

---

### 1.3 设计参考

RoboAgent 综合参考：

| 框架                   | 主要参考                                                              |
| -------------------- | ----------------------------------------------------------------- |
| Pi Agent Core        | Agent Loop、steering、follow-up、continue、tool batch、runtime control |
| OpenAI Agents Python | Agent / Run 边界、RunContext、Provider / Realtime 分层                  |
| smolagents           | modality-agnostic Agent / Tool abstraction                        |
| Hermes Agent         | vision、voice、interrupt、skills 等平台层扩展边界                            |

总体原则：

> **Pi 提供运行语义基线，OpenAI Agents 提供职责边界与 Realtime 分层参考，smolagents 提供 modality-agnostic 抽象参考；RoboAgent 保持轻量、Pythonic 和可嵌入。**

---

### 1.4 核心对象模型

RoboAgent v1 固定：

```text
Agent        immutable reusable definition
Session      canonical transcript owner
Run          one execution lifecycle

ModelContext model-visible input
RunContext   local runtime context

_RunState    internal mutable execution state
RunState     public immutable media-safe snapshot

RunControl   external control plane

AgentLoop    runtime orchestration
ToolExecutor tool batch execution
```

整体结构：

```text
                    Agent
                      │
          ┌───────────┴───────────┐
          ▼                       ▼
   MediaLimits              MediaResolver
          │                       │
          └───────────┬───────────┘
                      ▼
                   Session
             canonical transcript
                      │
                      ▼
                     Run
                      │
       ┌──────────────┼──────────────┐
       ▼              ▼              ▼
  RunContext      _RunState      RunControl
       │              │              │
       └──────────────┼──────────────┘
                      ▼
                 AgentLoop
              ┌───────┴────────┐
              ▼                ▼
       ContextManager      ToolExecutor
              │                │
              ▼                ▼
        ModelContext        ToolOutput
              │                │
              ▼                ▼
       MessageContent     MessageContent
              │                │
              └───────┬────────┘
                      ▼
                 Model Adapter
                      │
                      ▼
                    Model
```

最重要的架构原则：

> **Modality 是 Runtime 数据模型的一部分，不是 Runtime 控制流的一部分。**

因此增加：

```text
ImageContent
AudioContent
FileContent
```

后：

```text
Agent
Session
Run
RunControl
AgentLoop
ToolExecutor
```

的核心状态机不应发生结构变化。

---

## 2. Agent、Session、Run 与 Runtime 状态

### 2.1 Agent

`Agent` 是：

> **immutable reusable Agent Definition。**

概念上包含：

```text
model
tools
system_prompt
context_manager
hooks
default_run_config

media_limits
media_resolver
```

不包含：

```text
current turn
current response
current _RunState
run task
pending controls
tool execution tasks
```

一个 Agent 可以服务多个 Session：

```text
Agent
├── Session A
│   ├── Run 1
│   └── Run 2
└── Session B
    └── Run 1
```

Agent 是配置与 execution dependencies 定义，不是运行中的 Agent 实例。

---

### 2.2 MediaLimits 属于 Agent Definition

v1 定义：

```python
@dataclass(frozen=True, slots=True)
class MediaLimits:
    max_inline_bytes: int = 8 * 1024 * 1024
    max_contents_per_message: int = 16
```

MediaLimits 控制：

```text
BytesSource 单对象最大 inline bytes
单 Message 最大 MessageContent 数量
```

它控制的是：

> **canonical protocol acceptance。**

因此它：

```text
不属于 RunConfig
```

而属于：

```text
Agent definition
```

Session 创建时固定捕获：

```text
Agent.media_limits
      ↓
AgentSession._media_limits
```

该 Session 生命周期内：

```text
start()
history import
TranscriptValidator
continue_run()
ToolOutput validation
```

全部使用同一套 immutable MediaLimits。

这样同一份 canonical transcript：

> 不会因为不同 RunConfig 而在不同 Run 中忽然变成合法或非法。

---

### 2.3 MediaResolver 属于 Agent Execution Dependency

推荐：

```python
@dataclass(frozen=True)
class Agent:
    ...
    media_resolver: MediaResolver | None = None
```

MediaResolver 是：

> **Application-owned execution dependency。**

Agent 保存 Resolver reference，但 Kernel 不实现具体 filesystem / HTTP access policy。

同一个 Agent 的多个并发 Session / Run 默认共享同一个 MediaResolver：

```text
Agent
  │
  └── MediaResolver
        ├── Run A
        ├── Run B
        └── Run C
```

因此共享 Resolver 实现必须：

> **concurrency-safe。**

v1 不同时提供：

```text
resolver instance
+
resolver factory
```

两套机制。

保持一个明确入口。

---

### 2.4 AgentSession

`AgentSession` 是：

> **Conversation lifecycle owner。**

负责：

```text
session_id
canonical transcript
active-run ownership
fixed media_limits

start()
run()
continue_run()
```

其中：

> **`Session.messages` 是唯一 canonical conversation transcript。**

对外只能提供：

```text
immutable snapshot
或
read-only view
```

不能允许外部直接修改 transcript。

---

### 2.5 AgentRun

`AgentRun` 表示：

> **一次完整 Agent execution lifecycle。**

推荐：

```python
run = session.start(
    "Inspect the repository",
    config=config,
)
```

订阅：

```python
async for event in run.events():
    ...
```

等待：

```python
result = await run.result()
```

控制：

```python
run.cancel()
run.steer(...)
run.follow_up(...)
```

Convenience API：

```python
result = await session.run(
    prompt,
    config=config,
)
```

语义等价：

```python
run = session.start(prompt, config=config)
return await run.result()
```

因此：

```text
start() → AgentRun
run()   → RunResult
```

---

### 2.6 `start()` 原子性

固定顺序：

```text
acquire active-run ownership
        ↓
normalize public input
        ↓
canonical structure validation
        ↓
message semantic validation
        ↓
session state validation
        ↓
commit initial UserMessage
        ↓
create AgentRun
        ↓
immediately create execution task
```

如果：

```text
active Run exists
input invalid
Session invalid
```

则：

> **不得修改 transcript。**

v1 只保证：

> **同一 event loop 内并发安全。**

不默认承诺：

```text
cross-thread
cross-event-loop
```

安全。

---

### 2.7 Eager Start

`session.start()`：

> **立即启动 execution task。**

不是 lazy execution。

因此：

* active-run ownership 立即生效；
* Runtime events 可以立即产生；
* timeout 从 execution task 创建时开始；
* 无 subscriber 也继续执行；
* 无 `result()` waiter 也继续执行；
* terminal 后自动释放 Session active ownership。

---

### 2.8 RunStatus

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

---

### 2.9 RunPhase

```python
class RunPhase(Enum):
    IDLE = "idle"
    PREPARING_CONTEXT = "preparing_context"
    MODEL = "model"
    TOOL = "tool"
    BETWEEN_TURNS = "between_turns"
    TERMINAL = "terminal"
```

状态机：

```text
CREATED / IDLE
      │
      ▼
RUNNING
      │
      ├── PREPARING_CONTEXT
      ├── MODEL
      ├── TOOL
      └── BETWEEN_TURNS
      │
      ▼
terminal status / TERMINAL
```

即：

```text
CREATED
    ↓
RUNNING
    ↓
exactly one terminal
```

terminal 后不能回到 RUNNING。

---

### 2.10 Internal `_RunState`

Runtime 内部可以使用：

```python
@dataclass(slots=True)
class _RunState:
    status: RunStatus
    phase: RunPhase
    turn: int = 0

    streaming_message: AssistantMessage | None = None
    pending_tool_calls: tuple[ToolCall, ...] = ()

    error: RunError | None = None
```

它是：

> **AgentLoop 内部 mutable execution state。**

内部 `_RunState` 可以暂时持有：

```text
raw provisional MessageContent
BytesSource
provider-normalized provisional data
```

但不得直接作为 public API 暴露。

---

### 2.11 Public RunState Snapshot

公共 API 暴露的：

```text
run.state
run.state()
run.snapshot()
```

无论最终具体命名如何，都必须返回：

> **immutable、media-safe、read-only snapshot。**

推荐：

```python
@dataclass(frozen=True, slots=True)
class RunState:
    status: RunStatus
    phase: RunPhase
    turn: int

    streaming_content: tuple[ContentSummary, ...] = ()
    pending_tool_calls: tuple[ToolCallSummary, ...] = ()

    error: RunError | None = None
```

Public RunState：

```text
不得包含 raw BytesSource.data
不得暴露 full local file path
不得暴露 credential-bearing URL
不得暴露 raw provisional image/audio payload
```

因此：

```text
internal _RunState
        ↓
media-safe snapshot
        ↓
public RunState
```

---

### 2.12 RunState.error

规则：

```text
normal execution
    → None

single ToolCall FAILED
    → None

Run terminal FAILED
    → RunError

terminal
    → never clear
```

---

### 2.13 三类事实来源

始终：

```text
Session.messages != RunState != Events
```

分别表示：

```text
Session.messages
    canonical conversation truth

RunState
    public execution snapshot

Events
    observable runtime records
```

内部 `_RunState` 只是 Runtime implementation state，不是新的 canonical truth。

---

### 2.14 Turn

一个 Turn：

> **一次 Model Invocation，以及该 invocation 产生的完整 Tool Batch。**

因此：

```text
steer triggers Model
    → +1 turn

follow-up triggers Model
    → +1 turn
```

`max_turns`：

> 计算 Model Invocation 次数。

---

## 3. Canonical Message 与多模态内容协议

### 3.1 MessageContent

RoboAgent 使用：

```python
MessageContent = (
    TextContent
    | ImageContent
    | AudioContent
    | FileContent
)
```

这是：

> **v1 canonical protocol closed union。**

未知类型：

```text
UnsupportedContentTypeError
```

不能：

```text
ignore
best effort
Adapter decides
```

未来增加：

```text
VideoContent
```

时必须更新：

```text
MessageContent union
validator
ModelCapabilities
Adapter
serialization
semantic tests
```

因此：

> 新增 Video 是 canonical protocol 的版本化扩展。

但不应改变 AgentLoop 状态机。

v1 不定义 `Modality.VIDEO`。

---

### 3.2 TextContent

```python
@dataclass(frozen=True, slots=True)
class TextContent:
    text: str
```

必须 runtime validate：

```text
text is str
```

`TextContent("")`：

> canonical structure 合法。

但 UserMessage 另有 semantic validation。

---

### 3.3 ImageContent

```python
@dataclass(frozen=True, slots=True)
class ImageContent:
    source: MediaSource
    media_type: str | None = None
    detail: str | None = None
```

必须：

```text
source is MediaSource
detail is str | None
media_type is None or image/*
```

`detail`：

> **optional Adapter hint。**

Provider 不支持时：

```text
Adapter may ignore
```

默认不产生 capability failure。

如果应用要求某个 Provider-specific detail 必须生效，应通过 Adapter options 表达。

---

### 3.4 AudioContent

```python
@dataclass(frozen=True, slots=True)
class AudioContent:
    source: MediaSource
    media_type: str | None = None
    transcript: str | None = None
```

必须：

```text
source is MediaSource
transcript is str | None
media_type is None or audio/*
```

`transcript`：

> **non-model-visible auxiliary metadata。**

不能自动：

```text
transcript → TextContent
```

如果应用希望模型看到 transcript：

```python
UserMessage(
    content=(
        AudioContent(...),
        TextContent("known transcript"),
    )
)
```

必须显式提供。

---

### 3.5 FileContent

```python
@dataclass(frozen=True, slots=True)
class FileContent:
    source: MediaSource
    media_type: str | None = None
    filename: str | None = None
```

要求：

```text
filename is str | None
media_type is None or valid MIME
```

`Modality.FILE` 只表示：

> **存在某种 native file/document input 能力。**

不意味着：

```text
PDF
DOCX
ZIP
source code
binary
```

全部支持。

具体 MIME subtype 由 Adapter 精确验证。

---

### 3.6 MediaSource

```python
MediaSource = (
    BytesSource
    | FileSource
    | UrlSource
)
```

同样是 closed union。

未知 source：

```text
UnsupportedMediaSourceError
```

---

### 3.7 BytesSource

```python
@dataclass(frozen=True, slots=True)
class BytesSource:
    data: bytes
```

要求：

```text
type(data) is bytes
len(data) > 0
len(data) <= MediaLimits.max_inline_bytes
```

BytesSource：

> **inline immutable / snapshot-like media payload。**

---

### 3.8 FileSource

```python
@dataclass(frozen=True, slots=True)
class FileSource:
    path: str
```

要求：

```text
path is str
non-empty
non-whitespace
absolute path
```

`absolute path` 使用创建或验证 `FileSource` 的当前宿主操作系统的
`pathlib.Path(path).is_absolute()` 语义：POSIX host 使用 POSIX absolute
path；Windows host 接受 drive-rooted path 或 UNC path。当前宿主不能识别为
absolute 的路径必须拒绝；跨主机路径映射属于 Application / MediaResolver
policy，不属于 canonical protocol。

Kernel 不检查：

```text
file exists
readable
media valid
```

FileSource 是：

> **external live reference。**

因此：

```text
same canonical transcript
```

在未来 Turn / `continue_run()` 时可能读取到不同文件内容。

v1 接受：

```text
canonical structural identity
    !=
external media byte identity
```

---

### 3.9 UrlSource

```python
@dataclass(frozen=True, slots=True)
class UrlSource:
    url: str
```

Canonical validation 只验证：

```text
url is str
non-empty
absolute
scheme ∈ {http, https}
host syntactically valid
```

不判断：

```text
localhost
private IP
redirect
DNS rebinding
credentials
availability
resource size
```

这些属于 MediaResolver。

UrlSource 同样是：

> **external live reference。**

---

### 3.10 MediaSource Provenance 不等于授权

严格不变量：

> **MediaSource 的来源不影响访问权限。**

无论来自：

```text
User
Tool
Model Provider
history import
```

只要下一次 Provider invocation 需要实际访问：

```text
FileSource
UrlSource
```

都必须重新经过：

```text
MediaResolver
```

因此：

```text
provider-created URL
    !=
trusted URL
```

以及：

```text
source provenance
    !=
access authorization
```

---

### 3.11 MIME

Canonical MIME 使用：

```text
type/subtype
```

规则：

```text
ASCII
normalized lowercase
no MIME parameters
```

例如：

```text
image/jpeg
audio/wav
application/pdf
```

合法。

以下：

```text
image/jpeg; charset=utf-8
```

v1 canonical layer 拒绝。

类型必须匹配：

```text
ImageContent → image/*
AudioContent → audio/*
FileContent  → any valid canonical MIME
```

---

### 3.12 Public Construction 与 Tuple Normalization

Canonical 内部：

```python
tuple[MessageContent, ...]
```

公共 API 可接受：

```python
Sequence[MessageContent]
```

但必须排除：

```text
str
bytes
bytearray
```

处理：

```text
public value
    ↓
normalize
    ↓
runtime type validation
    ↓
canonical structure validation
    ↓
message semantic validation
    ↓
tuple[MessageContent, ...]
```

Canonical transcript 不保存 mutable list。

---

### 3.13 兼容字符串构造

v1 继续允许：

```python
UserMessage("hello")
AssistantMessage("hello")
ToolOutput("hello")
```

作为 ergonomic shorthand。

统一：

```text
str
 ↓
TextContent
 ↓
tuple
```

它可以长期作为公共便利 API。

真正需要 deprecated 的是：

```text
text-only internal assumptions
legacy result types
legacy serialization
```

而不是自然的字符串输入。

---

### 3.14 UserMessage

Canonical 语义：

```python
@dataclass(frozen=True, slots=True)
class UserMessage:
    content: tuple[MessageContent, ...]
```

要求：

```text
content non-empty
```

如果全部是 TextContent：

```text
至少一个具有非-whitespace text
```

合法：

```text
Image only
Audio only
File only
Text("") + Image
```

非法：

```text
content=()
Text("") only
Text("   ") only
```

---

### 3.15 AssistantMessage

```python
@dataclass(frozen=True, slots=True)
class AssistantMessage:
    content: tuple[MessageContent, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
```

允许：

```text
text only
image only
audio only
file only
mixed content
tool calls only
content + tool calls
```

因此：

```text
Assistant output != str
```

成为正式 v1 语义。

结构上：

```python
AssistantMessage(
    content=(),
    tool_calls=(),
)
```

可以 canonical-valid。

最终是否视为正常 finish，由 Adapter finish semantics 判断。

---

### 3.16 ToolResultMessage

```python
@dataclass(frozen=True, slots=True)
class ToolResultMessage:
    tool_call_id: str
    tool_name: str
    status: ToolCallStatus
    content: tuple[MessageContent, ...] = ()
    error: ToolExecutionError | None = None
```

不包含：

```text
details: Any
```

因为：

* `Any` 可能 mutable；
* 会破坏 canonical transcript 深度稳定性；
* 容易泄露 application internal state。

`details` 只存在：

```text
ToolOutput
ToolCallOutcome
internal observer data
```

---

### 3.17 Canonical Transcript Grammar

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

若：

```text
Assistant(tool_calls=[A, B, C])
```

必须：

```text
ToolResult(A)
ToolResult(B)
ToolResult(C)
```

严格满足：

```text
count
order
tool_call_id
```

Tool Exchange 完成前：

```text
禁止新 UserMessage
禁止新 AssistantMessage
```

---

### 3.18 合法 Transcript

合法：

```text
User(Text + Image)
Assistant(Text)
```

合法：

```text
Assistant(
    Text,
    tool_calls=[A, B]
)
ToolResult(A, Image)
ToolResult(B, Text)
```

合法：

```text
User(Text)
User(Image)
User(Audio)
Assistant(...)
```

---

### 3.19 非法 Transcript

非法：

```text
Assistant(A, B)
ToolResult(B)
ToolResult(A)
```

非法：

```text
Assistant(A)
User(...)
ToolResult(A)
```

非法：

```text
ToolResult(A)
```

非法：

```text
Assistant(no tools)
ToolResult(A)
```

非法 duplicate ToolResult。

---

### 3.20 ToolCall ID

同一个 AssistantMessage 内：

```text
ToolCall.id MUST be unique
```

重复：

```text
ProtocolError
```

不得执行。

---

### 3.21 TranscriptValidator

以下统一：

```text
runtime transcript
history import
continue_run()
```

使用同一个：

```text
TranscriptValidator
```

验证：

```text
runtime field types

MessageContent
MediaSource
MIME
Message semantics

ToolCall IDs
ToolResult count
ToolResult order
ToolResult ownership
dangling ToolCall
```

---

### 3.22 Validation 分层

#### Layer 1 — Canonical Structure Validation

判断对象本身是否合法：

```text
unknown Content
unknown Source

wrong runtime type

empty BytesSource
oversized BytesSource
relative FileSource
invalid URL
invalid MIME

Image + audio/*
Audio + image/*
```

#### Layer 2 — Message Semantic Validation

判断合法 Contents 能否组成合法 Message：

```text
empty UserMessage
whitespace-only text UserMessage
```

#### Layer 3 — Model Capability Validation

判断合法 ModelContext 是否能被当前 Model / Adapter 接受：

```text
ImageContent
+
TEXT-only model
```

产生：

```text
ModelCapabilityError
```

统一：

```text
FAILED / MODEL_ERROR
```

---

### 3.23 System / Tool Schema / Tool Arguments

v1 多模态只属于：

```text
MessageContent
```

以下仍然：

```text
system_prompt → str

Tool schema
    → JSON-compatible schema

Tool arguments
    → JSON-compatible values
```

不允许：

```text
ImageContent inside Tool arguments
AudioContent inside system prompt
```

---

## 4. MediaResolver、ResolvedMedia 与媒体访问边界

### 4.1 MediaResolver

正式接口：

```python
class MediaResolver(Protocol):
    async def resolve(
        self,
        source: MediaSource,
        *,
        expected_media_type: str | None,
        run_context: RunContext,
        cancellation: CancellationToken,
    ) -> ResolvedMedia:
        ...
```

它是：

> **Application-owned media access boundary。**

Kernel 不内置：

```text
filesystem downloader
HTTP downloader
SSRF policy
credential manager
```

`expected_media_type` 必须由 Adapter 从正在编码的 canonical content 的
`media_type` 原样传入。这样 Resolver 能把检测到的 MIME 与 canonical
声明比较；`None` 表示 canonical content 未声明 MIME。

---

### 4.2 MediaResolver 与 RunContext

Resolver 可以读取当前：

```text
session_id
run_id
metadata
```

用于：

```text
tenant-aware media policy
user-specific credentials
session-specific file roots
audit scope
per-run accounting
```

但：

> **Resolver 不得修改 RunContext。**

并且：

> **Resolver 读取 RunContext 不意味着 RunContext.metadata 可以隐式进入 ModelContext。**

仍然保持：

```text
RunContext != ModelContext
```

---

### 4.3 默认媒体访问行为

默认：

```text
BytesSource
    → directly resolvable

FileSource
UrlSource
    → denied if no MediaResolver
```

Adapter 不允许绕过 Resolver 自行：

```text
open(path)
HTTP GET(url)
```

---

### 4.4 ResolvedMediaPayload

v1：

```python
ResolvedMediaPayload = bytes | Path
```

不支持 streaming payload。

---

### 4.5 MediaOwnership

为避免 Path ownership 歧义，正式定义：

```python
class MediaOwnership(Enum):
    BORROWED = "borrowed"
    OWNED = "owned"
```

---

### 4.6 ResolvedMedia

```python
@dataclass(slots=True)
class ResolvedMedia:
    payload: bytes | Path
    media_type: str | None
    size: int
    source: MediaSource
    ownership: MediaOwnership

    async def close(self) -> None:
        ...
```

`ResolvedMedia` 表示：

> **已经通过 application access boundary，并可在当前 Provider consumption scope 中安全使用的媒体资源。**

---

### 4.7 ResolvedMedia.payload

允许：

```text
bytes
Path
```

`payload` 在：

```text
close()
```

前必须保持可用。

---

### 4.8 BORROWED Ownership

例如：

```text
FileSource("/data/a.jpg")
    ↓
ResolvedMedia(
    payload=Path("/data/a.jpg"),
    ownership=BORROWED
)
```

表示：

> payload 生命周期由外部 Application 所有。

`close()`：

```text
不得删除 payload
```

可以释放 Resolver 自己附带创建的 handle / internal bookkeeping。

---

### 4.9 OWNED Ownership

例如：

```text
UrlSource(...)
    ↓
download temp file
    ↓
ResolvedMedia(
    payload=Path("/tmp/..."),
    ownership=OWNED
)
```

表示：

> payload 生命周期由 Resolver 所有。

`close()`：

```text
应 best-effort 删除 / 释放 payload
```

---

### 4.10 Bytes Ownership

如果 Resolver 直接返回 canonical BytesSource.data：

```text
ownership=BORROWED
```

如果 Resolver 创建了新的 bytes payload：

```text
ownership=OWNED
```

---

### 4.11 为什么 v1 不支持 Streaming ResolvedMedia

v1 不定义：

```text
AsyncIterator[bytes]
read()
seek()
stream()
```

避免提前引入：

```text
rewind
partial retry
stream ownership
stream backpressure
provider streaming upload
```

大媒体可以 materialize 为 Path。

未来再扩展。

---

### 4.12 ResolvedMedia.media_type

如果 canonical Content 已显式提供：

```text
media_type
```

则：

> **canonical media_type 优先。**

这里的 canonical 值就是 `resolve(..., expected_media_type=...)` 的
`expected_media_type`；Resolver 不能只从 `MediaSource` 推断它。

Resolver 检测结果与其冲突：

```text
MediaResolutionError(
    MEDIA_TYPE_MISMATCH
)
```

不能静默替换 canonical MIME。

如果 canonical：

```text
media_type=None
```

Resolver 可以检测：

```text
ResolvedMedia.media_type
```

供 Adapter 使用。

但：

> 不反向修改 canonical MessageContent。

---

### 4.13 ResolvedMedia.size

`size`：

```text
必须 >= 0
```

表示实际 resolved payload byte size。

可以用于：

```text
provider-specific limit validation
download quota
upload validation
diagnostics
```

---

### 4.14 ResolvedMedia.close()

必须：

```text
async
idempotent
best-effort
```

负责：

```text
release Resolver-owned memory
delete OWNED temporary Path
close Resolver-created handle
release temporary upload preparation resources
```

不得：

```text
delete BORROWED FileSource path
mutate canonical MediaSource
```

---

### 4.15 ResolvedMedia 生命周期

每次：

```text
resolve()
```

产生：

> **单次 Provider consumption scope resource。**

推荐：

```python
resolved = await resolver.resolve(
    source,
    expected_media_type=content.media_type,
    run_context=run_context,
    cancellation=token,
)

try:
    request = adapter.encode_media(resolved)
    response = await adapter.invoke(request)
finally:
    await resolved.close()
```

必须覆盖：

```text
normal Provider success
Adapter encode failure
Provider request failure
Provider response failure
Run cancellation
timeout
```

不能等到整个 Run terminal 才统一 cleanup。

---

### 4.16 Cleanup Failure

`close()` cleanup failure：

```text
log / diagnostic only
```

默认不能：

```text
overwrite primary error
turn successful model call into Run failure
```

---

### 4.17 MediaResolutionError

建议稳定错误码：

```python
class MediaResolutionErrorCode(Enum):
    ACCESS_DENIED = "access_denied"
    NOT_FOUND = "not_found"
    TOO_LARGE = "too_large"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    FETCH_FAILED = "fetch_failed"
    MEDIA_TYPE_MISMATCH = "media_type_mismatch"
```

```python
class MediaResolutionError(Exception):
    code: MediaResolutionErrorCode
```

映射：

```text
ACCESS_DENIED
NOT_FOUND
TOO_LARGE
FETCH_FAILED
MEDIA_TYPE_MISMATCH
    → FAILED / MODEL_ERROR
```

如果：

```text
TIMEOUT
```

由 Run deadline 导致：

```text
→ TIMED_OUT
```

如果：

```text
CANCELLED
```

由用户 Run cancellation 导致：

```text
→ CANCELLED
```

cancel / timeout 不能被普通 MODEL_ERROR 覆盖。

---

### 4.18 Resolver Access Policy

v1 不额外增加：

```text
MediaAccessPolicy
MediaManager
MediaStore
```

应用将策略封装进 MediaResolver。

Resolver 可以控制：

```text
filesystem roots
allowed hosts
private networks
ports
redirects
credentials
download size
timeout
temporary files
tenant identity
```

---

### 4.19 Resolver Concurrency

共享 MediaResolver 必须：

> **concurrency-safe。**

多个 Run 的：

```text
resolve()
close()
```

不得互相污染。

---

### 4.20 同一 Provider Invocation 的多媒体 Resolution

同一条 ModelContext message 中的多个 external media 可以由 Adapter
并行 resolve，以减少准备时间；是否并行由 Adapter 决定。

无论 resolve completion order 如何：

```text
canonical MessageContent order
        =
encoded provider content order
```

若其中一个 ordinary resolution 失败：

1. 记录最先观察到的 ordinary failure 作为 primary failure；
2. 对尚未完成的 sibling resolve 请求 cancellation；
3. 等待所有已启动 resolve 到达可安全 cleanup 的状态；
4. 对每个已成功 resolve 的资源调用 `close()`；
5. 以 primary failure 结束本次 Model preparation。

Run cancellation 或 timeout 优先于 ordinary failure，并分别保持
`CANCELLED` / `TIMED_OUT` 语义。

---

### 4.21 Resolver Cancellation

Resolver 必须响应：

```text
Run cancellation
timeout
```

尽力：

```text
cancel network request
close stream
release temporary resource
```

---

### 4.22 Provider 输出媒体仍重新授权

如果 Provider 输出：

```python
ImageContent(
    source=UrlSource(...)
)
```

进入 canonical transcript：

> 它只是一个 canonical reference。

下一 Turn 需要实际访问时：

```text
必须重新经过 MediaResolver
```

Tool 输出 / history import 同理。

---

### 4.23 跨 Session / Run 媒体预算

v1 Kernel 只保证：

```text
max_inline_bytes
max_contents_per_message
```

以下属于 Application responsibility：

```text
Session cumulative media memory
Run cumulative media bytes
media cache
storage quota
upload quota
history retention
```

长期 Session 中大量：

```text
BytesSource
```

可能保留大量内存。

需要 bounded retention 的应用应：

```text
prefer external references
implement Session lifecycle
implement compaction
future blob storage
```

---

## 5. ModelContext、Capabilities、Streaming 与 Provider Adapter

### 5.1 ModelContext

```python
@dataclass(frozen=True)
class ModelContext:
    messages: tuple[Message, ...]
    ...
```

Messages 可以多模态。

路径：

```text
Session.messages
      ↓
ContextManager
      ↓
ModelContext
      ↓
Model Adapter
      ↓
MediaResolver as needed
      ↓
Provider
```

---

### 5.2 RunContext

```python
@dataclass(slots=True)
class RunContext:
    session_id: str
    run_id: str
    cancellation: CancellationToken
    turn: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)
```

始终：

```text
RunContext != ModelContext
```

---

### 5.3 metadata

metadata：

* application-owned；
* Kernel 不解释；
* Tool / Hook / Resolver 可以读取；
* 不隐式进入 ModelContext；
* 不进入 RunResult；
* 不进入默认 Event serialization；
* 不进入默认 logging。

---

### 5.4 Modality

```python
class Modality(Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    FILE = "file"
```

没有 VIDEO。

---

### 5.5 ModelCapabilities

```python
@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    input_modalities: frozenset[Modality]
    output_modalities: frozenset[Modality]
    tool_result_modalities: frozenset[Modality]
    supports_tools: bool = False
```

这是：

> **coarse-grained declaration。**

不是完整 Provider feature matrix。

---

### 5.6 Exact Adapter Validation

例如：

```text
IMAGE in input_modalities
```

只表示：

> Adapter 至少支持某种 Image input。

不保证：

```text
all roles
all MIME
all MediaSource
all provider modes
```

全部支持。

最终精确校验属于：

```text
Model Adapter
```

---

### 5.7 Role-specific Limitations

v1 不建立：

```text
role × modality × MIME × source
```

复杂 capability matrix。

例如：

```text
user image supported
assistant history image unsupported
```

由 Adapter request encoding 阶段精确校验。

失败：

```text
ModelCapabilityError
    ↓
FAILED / MODEL_ERROR
```

---

### 5.8 FILE Capability

```text
FILE in input_modalities
```

不意味着任意文件类型都支持。

Adapter 可以：

```text
accept application/pdf
reject application/zip
```

---

### 5.9 Tool Result Modalities

`tool_result_modalities` 单独定义：

```text
哪些 modality 可以出现在 canonical ToolResult 并进入下一 Model invocation
```

例如：

```text
normal input supports AUDIO
ToolResult does not support AUDIO
```

是合法能力组合。

---

### 5.10 ToolResolver 与 FrozenToolSet

每 Turn：

```text
ToolResolver.resolve()
        ↓
FrozenToolSet
        ↓
┌──────────────┐
│              │
▼              ▼
Model        Executor
```

推荐：

```python
class ToolResolver(Protocol):
    async def resolve(
        self,
        run_context: RunContext,
        tools: Sequence[Tool],
    ) -> FrozenToolSet:
        ...
```

必须保证：

> **模型看到的 Tool schemas 与 Executor 可执行的 Tool 集合一致。**

---

### 5.11 ContextManager

每 Turn：

```text
ToolResolver
      ↓
FrozenToolSet
      ↓
ContextManager.prepare()
```

ContextManager 输入：

```text
canonical transcript
system prompt
FrozenToolSet
explicit model-visible application values
```

不自动读取 RunContext.metadata 并投影给模型。

---

### 5.12 Multimodal Context

ContextManager 必须以：

```text
whole Message
```

为基本语义单元。

不能默认：

```text
User(Text + Image)
    ↓
Text only
```

Tool Exchange 同样必须整体保留。

---

### 5.13 Context Budget

v1 不定义：

```text
image token
audio token
file token
```

统一估算。

行为：

```text
ContextManager preserves semantic integrity
        ↓
Adapter / Model may estimate provider limits
        ↓
provider-specific rejection if needed
```

---

### 5.14 Provider Adapter

负责：

```text
coarse capability validation
exact provider validation

encode canonical Messages
encode MessageContent

request MediaResolver for external sources

stream provider output
normalize ToolCall
normalize finish reason
normalize multimodal output
```

Kernel 不知道具体：

```text
input_text
image_url
base64 image
input_audio
document block
tool result role
```

---

### 5.15 Internal ModelStreamItem

内部 Provider stream 使用：

```python
ModelStreamItem = (
    TextDelta
    | ToolCallDelta
    | ContentCompleted
)
```

它不是 public Event 类型。

---

### 5.16 TextDelta

```python
@dataclass(frozen=True)
class TextDelta:
    text: str
```

---

### 5.17 ToolCallDelta

v1 不要求 Provider 一开始就提供正式 ToolCall ID。

定义：

```python
@dataclass(frozen=True)
class ToolCallDelta:
    index: int
    call_id: str | None = None
    name: str | None = None
    arguments_delta: str = ""
```

其中：

```text
index
    = 本 Model invocation 内 provisional ToolCall identity
```

而：

```text
call_id
    = canonical identity candidate
```

---

### 5.18 ToolCall Streaming Normalization

Provider 可能按任意顺序提供：

```text
arguments
name
id
```

例如：

```text
index=0, arguments="{"
index=0, name="read_file"
index=0, arguments="...}"
index=0, call_id="call_123"
```

Adapter / stream builder 必须以：

```text
index
```

聚合同一 provisional ToolCall。

---

### 5.19 Canonical ToolCall Formation

Model stream 结束时，每一个 provisional ToolCall 必须最终拥有：

```text
non-empty canonical id
non-empty name
complete valid arguments
```

才能形成：

```python
ToolCall(...)
```

如果缺失：

```text
id
name
valid arguments
```

则：

```text
Model protocol normalization failure
    ↓
FAILED / MODEL_ERROR
```

不得进入 ToolExecutor。

---

### 5.20 Provider 不提供 ToolCall ID

如果 Provider 本身没有 call ID：

> **Adapter 可以生成稳定 provider-neutral canonical ID。**

但：

```text
AgentLoop 不生成 ToolCall ID
Kernel generic runtime 不猜 Provider identity
```

ID normalization 属于 Adapter contract。

---

### 5.21 ContentCompleted

```python
@dataclass(frozen=True)
class ContentCompleted:
    content: ImageContent | AudioContent | FileContent
```

它是：

> **内部 provisional stream item。**

允许持有：

```text
raw MessageContent
including BytesSource
```

但不能直接存入 public Event history。

v1 中 `ContentCompleted` 只表示非文本完整 block。Provider 若以完整 block
返回文本，Adapter 必须将它归一化为 `TextDelta(text=...)`，不能产生
`ContentCompleted(TextContent(...))`。这样 text buffer 的合并规则只有一个
来源。

---

### 5.22 Mixed Multimodal Streaming Order

必须定义有序 content builder。

规则：

#### TextDelta

```text
append to current text buffer
```

#### ContentCompleted(non-text)

```text
flush current text buffer
    ↓
append TextContent(buffer) if non-empty
    ↓
append non-text MessageContent
    ↓
clear text buffer
```

下一条 TextDelta：

```text
start / continue new text buffer
```

---

### 5.23 Mixed Stream 示例

输入：

```text
TextDelta("A")
ContentCompleted(Image)
TextDelta("B")
```

最终必须：

```python
(
    TextContent("A"),
    ImageContent(...),
    TextContent("B"),
)
```

不能：

```python
(
    TextContent("AB"),
    ImageContent(...),
)
```

---

### 5.24 相邻 TextDelta 合并规则

例如：

```text
TextDelta("A")
TextDelta("B")
ContentCompleted(Image)
TextDelta("C")
TextDelta("D")
```

得到：

```python
(
    TextContent("AB"),
    ImageContent(...),
    TextContent("CD"),
)
```

Invariant：

> **只有中间不存在非文本 `ContentCompleted` 的相邻 TextDelta 才能合并。**

---

### 5.25 Provider Block Index

如果 Provider 自身提供：

```text
content block index
```

Adapter 应先按照 Provider protocol 正确归一化事件顺序，然后再输出：

```text
ModelStreamItem
```

AgentLoop 不直接解释 Provider-specific block index。

---

### 5.26 v1 Streaming Boundary

支持：

```text
Text
    incremental

Tool arguments
    incremental

Image
Audio
File
    complete block only
```

不支持：

```text
image byte delta
audio PCM delta
file byte delta
```

---

### 5.27 Provisional vs Committed

所有：

```text
TextDelta
ToolCallDelta
ContentCompleted
```

都是 provisional。

只能进入：

```text
internal stream builder
internal _RunState
```

不能进入 canonical transcript。

---

### 5.28 Stream Completion

流程：

```text
Provider stream completes
        ↓
finish text buffer
        ↓
finalize provisional ToolCalls
        ↓
build final AssistantMessage
        ↓
canonical structure validation
        ↓
message semantic validation
        ↓
output capability validation
        ↓
commit AssistantMessage to Session
        ↓
update _RunState
        ↓
emit ModelCompleted
```

---

### 5.29 ModelCompleted 时序

正式规定：

> **收到 `ModelCompleted` 时，对应 AssistantMessage 已经存在于 `Session.messages`。**

即：

```text
ModelCompleted
    implies
canonical AssistantMessage already committed
```

Provider socket/stream 停止只是内部事实。

不等于 public `ModelCompleted`。

---

### 5.30 output_modalities

最终 AssistantMessage 中所有 Content：

```text
modality
    ∈
output_modalities
```

否则：

```text
no Assistant commit
ModelFailed
FAILED / MODEL_ERROR
```

---

### 5.31 Invalid Provider Output

以下属于：

```text
MODEL_ERROR
```

而不是 Kernel invariant failure：

```text
unknown provider content
invalid normalized MessageContent
unsupported output modality
duplicate ToolCall ID
incomplete ToolCall
provider protocol violation
```

---

### 5.32 Unsupported Input

如果 ModelContext 有 IMAGE，而 Model 不支持：

```text
ModelCapabilityError
    ↓
FAILED / MODEL_ERROR
```

---

### 5.33 Unsupported ToolResult

如果 ToolResult 有 AUDIO，而：

```text
AUDIO not in tool_result_modalities
```

同样：

```text
FAILED / MODEL_ERROR
```

---

### 5.34 Realtime Media

必须保持：

```text
Multimodal Message
    !=
Realtime Media Transport
```

完整 wav：

```text
AudioContent
```

可以进入 transcript。

持续 PCM frame：

```text
不进入 Session.messages
```

未来：

```text
RealtimeAudioStream
       ↓
Realtime / Speech Adapter
       ↓
Agent Turn
```

属于另一层。

---

## 6. RunControl、Continuation 与 Cancellation

### 6.1 PendingControl

```python
@dataclass(frozen=True)
class PendingControl:
    sequence: int
    kind: ControlKind
    message: UserMessage
```

steer / follow_up 不立即 commit。

---

### 6.2 Control Input

以下统一：

```text
session.start()
run.steer()
run.follow_up()
```

接受：

```python
str | UserMessage
```

字符串 normalize 成：

```text
TextContent
```

---

### 6.3 Terminal Control

terminal 后：

```text
cancel()
    → idempotent no-op

steer()
follow_up()
    → RunFinishedError
```

---

### 6.4 Observe 与 Consume

Runtime 可以：

```text
observe pending steer
```

用于：

```text
Tool scheduling / steering policy
```

但：

```text
observe != transcript commit
```

只有 safe boundary 才 consume。

---

### 6.5 Safe Transcript Boundary

pending control 可 commit，当且仅当：

1. 没有未完成 Model stream；
2. AssistantMessage 若存在，已完整 canonical commit；
3. ToolCalls 若存在，全部 terminal；
4. ToolResults 已按 call order commit；
5. 下一 `ContextManager.prepare()` 尚未开始。

---

### 6.6 无 Assistant 的 Failure

以下 failure：

```text
ContextManager failure
MediaResolver failure
capability failure
Model failure before Assistant
```

若当前没有未完成 committed Tool Exchange：

```text
可以直接形成 safe boundary
```

然后 commit pending controls。

---

### 6.7 Control Order

所有 pending controls：

```text
strict receive sequence
```

Kernel 不：

```text
reorder
drop earlier follow-up
automatically override
```

例如：

```text
1 follow_up("总结")
2 steer("先检查测试")
```

下一 Model Turn 前应按顺序：

```text
User("总结")
User("先检查测试")
```

---

### 6.8 Priority

固定：

```text
cancel
  >
steer-triggered continuation
  >
natural follow-up
```

---

### 6.9 Cancel 行为

Cancel 被观察后：

```text
stop new Model invocation

stop pending ToolCall start

request running Tool cancellation

request MediaResolver cancellation

do not create new turn from pending controls

normalize current Tool outcomes

commit required ToolResults

reach safe boundary

commit pending controls

terminate
```

---

### 6.10 Terminal Pending Controls

正常：

```text
cancel
timeout
max turns
context error
media error
model error
tool policy fail
```

终态前必须：

```text
reach final safe boundary
commit all received pending controls
```

正常：

```python
uncommitted_controls == ()
```

仅：

```text
Kernel invariant failure
transcript corruption
unrecoverable Runtime failure
```

允许：

```text
uncommitted_controls != ()
```

此时：

```text
status == FAILED
reason in {RUNTIME_ERROR, INVALID_STATE}
```

---

### 6.11 `continue_run()`

```python
run = session.continue_run(
    config=config,
)
```

表示：

> **从现有 canonical transcript 创建新的 AgentRun，不新增 UserMessage。**

不是恢复旧 coroutine。

创建前：

```text
Session non-empty
no active Run
canonical transcript valid
no dangling ToolCall
all MessageContent structurally valid
```

非法：

```text
InvalidContinuationError
```

---

### 6.12 External Media 与 `continue_run()`

Continuation validation：

```text
不读取 FileSource
不下载 UrlSource
```

只验证 canonical structure。

真正 Provider invocation 时：

```text
MediaResolver
```

重新执行访问授权和 resolution。

---

### 6.13 CancellationToken

```python
class CancellationToken(Protocol):

    @property
    def cancelled(self) -> bool: ...

    @property
    def reason(self) -> CancellationReason | None: ...

    async def wait_cancelled(self) -> None: ...

    def throw_if_cancelled(self) -> None: ...

    def child(self) -> CancellationToken: ...
```

---

### 6.14 CancellationReason

```python
class CancellationReason(Enum):
    USER = "user"
    TIMEOUT = "timeout"
    RUN_TERMINATED = "run_terminated"
    TOOL_POLICY = "tool_policy"
```

规则：

> **first cancellation reason wins。**

---

### 6.15 Child Cancellation

Run token：

```text
USER / TIMEOUT / RUN_TERMINATED
```

传播给 Tool child。

Tool policy 可以只取消某个 child：

```text
TOOL_POLICY
```

已确定 reason 不覆盖。

---

### 6.16 Timeout

Run timeout：

> **soft cooperative wall-clock deadline。**

起点：

```text
execution task creation
```

deadline：

```text
CancellationRequested(TIMEOUT)
```

然后：

```text
MediaResolver cancellation
Model cancellation
Tool cancellation
cleanup
protocol normalization
safe commit
```

最后：

```text
RunTimedOut
```

不提供 hard kill guarantee。

---

## 7. Tool Runtime 与 ToolExecutionPolicy

### 7.1 ToolInvocation

```python
@dataclass(...)
class ToolInvocation:
    call: ToolCall
    run_context: RunContext
    tool_context: ToolCallContext
    model_context: ModelContext | None = None
```

`model_context` 默认：

```text
None
```

显式 opt-in 才提供。

如果提供：

> 必须是当前 Turn immutable ModelContext snapshot。

---

### 7.2 ToolCallContext

```python
@dataclass(slots=True)
class ToolCallContext:
    call_id: str
    cancellation: CancellationToken
```

每 ToolCall 有 child token。

---

### 7.3 Tool Result 三层模型

严格区分：

```text
ToolOutput
ToolCallOutcome
ToolExecutionBatchResult
```

---

### 7.4 ToolOutput

```python
@dataclass(...)
class ToolOutput:
    content: tuple[MessageContent, ...] = ()
    is_error: bool = False
    error_code: str | None = None
    details: Any | None = None
```

其中：

```text
content
    model-visible candidate

details
    runtime/application data
```

details 不进入 canonical transcript。

---

### 7.5 ToolOutput Compatibility

现有 public constructor surface 在 v1 compatibility cycle 中保持。

原则：

> **只迁移 content representation，不借机改变其他已有 public fields 的语义。**

现有例如：

```text
timestamp
finish_reason
usage
model
tool_calls
details
error_code
```

等字段，如果属于 public API，应继续保持。

只将：

```text
content: str
```

兼容转换成：

```text
tuple[MessageContent, ...]
```

---

### 7.6 位置参数兼容

如果旧 API 允许：

```python
AssistantMessage("hello", tool_calls)
```

v1 compatibility cycle 中继续支持。

但：

> 新代码与新文档统一推荐 keyword arguments。

---

### 7.7 Deprecation 策略

建议：

```text
v1
    legacy text constructor compatibility

v1.x
    deprecate obsolete text-only internals

v2
    eligible to remove obsolete legacy-only APIs
```

但：

```python
UserMessage("hello")
```

这种 ergonomic shorthand 可以长期保留。

---

### 7.8 ToolCallStatus

```python
class ToolCallStatus(Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
```

---

### 7.9 ToolCallOutcome

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

### 7.10 ToolExecutionBatchResult

```python
@dataclass(frozen=True)
class ToolExecutionBatchResult:
    outcomes: tuple[ToolCallOutcome, ...]
```

必须：

```text
len(outcomes) == len(tool_calls)

outcomes[i].call_id == tool_calls[i].id
```

---

### 7.11 Invalid ToolOutput

如果 ToolOutput：

```text
invalid MessageContent
invalid MediaSource
wrong runtime type
invalid MIME
oversized BytesSource
```

则：

```text
ToolCallOutcome.FAILED
error = INVALID_TOOL_OUTPUT
```

这是：

> **单 ToolCall failure。**

不是 Runtime invariant failure。

---

### 7.12 ToolOutput → Outcome

| 情况                 | Outcome   |
| ------------------ | --------- |
| success            | COMPLETED |
| `is_error=True`    | FAILED    |
| exception          | FAILED    |
| invalid args       | FAILED    |
| unknown tool       | FAILED    |
| policy reject      | FAILED    |
| invalid ToolOutput | FAILED    |
| cooperative cancel | CANCELLED |
| never started      | SKIPPED   |

禁止：

```text
is_error=True
+
COMPLETED
```

---

### 7.13 Outcome → ToolResultMessage

#### COMPLETED

```text
status = COMPLETED
content = output.content
```

#### FAILED

```text
status = FAILED
content = sanitized TextContent error
error = safe ToolExecutionError
```

默认不携带 handler 的 media failure payload。

#### CANCELLED

```text
generic TextContent cancellation result
```

#### SKIPPED

```text
generic TextContent skipped result
```

---

### 7.14 Tool 成功但 Model 不能消费结果

场景：

```text
Tool success
    ↓
ImageContent
    ↓
Outcome COMPLETED
    ↓
ToolResult committed
    ↓
next Model cannot consume IMAGE ToolResult
```

规则：

```text
Tool remains COMPLETED
```

然后：

```text
ModelCapabilityError
    ↓
Run FAILED / MODEL_ERROR
```

Canonical ToolResult 保留。

不能倒改 Tool execution truth。

---

### 7.15 ToolExecutor

负责：

```text
resolve Tool
validate call
before policy
schedule
execute
normalize ToolOutput
validate ToolOutput
produce terminal Outcome
stable ordering
```

不负责：

```text
Session ownership
transcript commit
media semantic interpretation
```

---

### 7.16 ToolExecutionMode

```python
class ToolExecutionMode(Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
```

默认：

```text
SEQUENTIAL
```

因为它是最确定的通用默认，不是领域安全规则。

---

### 7.17 ToolExecutionConfig

```python
@dataclass(frozen=True)
class ToolExecutionConfig:
    mode: ToolExecutionMode = ToolExecutionMode.SEQUENTIAL
    max_concurrency: int | None = None
```

规则：

```text
None → no additional limit
positive → valid
0 / negative → invalid
```

只表示单 batch 本地并发上限。

Sequential 模式忽略。

---

### 7.18 Stable Ordering

```text
calls:
A B C

completion:
B C A

events:
B C A

BatchResult:
A B C

Transcript:
A B C
```

因此：

```text
runtime completion order
    !=
canonical transcript order
```

是正式行为。

---

### 7.19 ToolPolicyFactory

```python
ToolPolicyFactory = Callable[
    [RunContext],
    ToolExecutionPolicy,
]
```

每 Run 独立 policy instance。

---

### 7.20 DefaultToolExecutionPolicy

factory=None 时：

```text
before_call
    → ALLOW

Tool FAILED
    → STOP_BATCH

steering + PENDING
    → SKIP

steering + RUNNING
    → CONTINUE
```

---

### 7.21 BeforeToolAction

```python
class BeforeToolAction(Enum):
    ALLOW = "allow"
    REJECT = "reject"
    SKIP = "skip"
    FAIL_RUN = "fail_run"
```

| Action   | 当前调用    | 后续未启动   | Run                 |
| -------- | ------- | ------- | ------------------- |
| ALLOW    | execute | normal  | continue            |
| REJECT   | FAILED  | normal  | continue            |
| SKIP     | SKIPPED | normal  | continue            |
| FAIL_RUN | FAILED  | SKIPPED | FAILED / TOOL_ERROR |

FAIL_RUN 时 running sibling：

```text
request child cancellation
```

---

### 7.22 ToolErrorAction

```python
class ToolErrorAction(Enum):
    CONTINUE = "continue"
    STOP_BATCH = "stop_batch"
    FAIL_RUN = "fail_run"
```

STOP_BATCH：

```text
pending → SKIPPED
running → continue to terminal
```

FAIL_RUN：

```text
pending → SKIPPED
running → request cancellation
```

---

### 7.23 SteeringAction

```python
class SteeringAction(Enum):
    CONTINUE = "continue"
    CANCEL = "cancel"
    SKIP = "skip"
```

```python
class ToolCallState(Enum):
    PENDING = "pending"
    RUNNING = "running"
```

PENDING：

```text
CONTINUE → start
SKIP → SKIPPED
CANCEL → SKIPPED
```

RUNNING：

```text
CONTINUE → continue
CANCEL → child cancellation
SKIP → invalid
```

---

### 7.24 Policy Scope

Policy 可以决定：

```text
allow
reject
skip
stop batch
fail run
steering response
```

不负责：

```text
task graph
priority scheduler
resource allocation
generic retry engine
```

v1 不内置 generic retry。

---

## 8. Hooks、Events、History、Result 与安全边界

### 8.1 Hooks

Hooks：

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

`on_tool_end` 接收：

```text
ToolCallOutcome
```

Hook exception：

```text
log only
does not fail Run
```

Hooks 不：

```text
modify Outcome
modify RunControl
participate in allow/deny
perform scheduling
```

---

### 8.2 Public Event Types

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

每 Run：

> **恰好一个 terminal Event。**

---

### 8.3 Internal ModelStreamItem 与 Public Event 分层

严格区分：

```text
ModelStreamItem
```

和：

```text
AgentEvent
```

内部：

```text
TextDelta
ToolCallDelta
ContentCompleted(raw MessageContent)
```

可以持有 raw Runtime object。

Public Event history：

> **不能持有 raw media payload。**

---

### 8.4 ContentSummary

推荐统一：

```python
@dataclass(frozen=True, slots=True)
class ContentSummary:
    modality: Modality
    media_type: str | None = None
    source_kind: str | None = None
    size: int | None = None
```

对于 Text 可以附加受控 preview，也可以只记录长度，具体 public event schema 可进一步细化。

---

### 8.5 ContentCompletedSummary

当内部：

```python
ContentCompleted(
    content=MessageContent
)
```

到达时：

```text
raw content
    ↓
summarize/redact
    ↓
ContentSummary
    ↓
ModelDelta Event
```

而不是把原始 `ContentCompleted` 放进 Event history。

---

### 8.6 Public RunState 与 Event 使用同一媒体摘要规则

Public RunState 中的：

```text
streaming_content
```

和 Event 中的媒体 summary：

> 应复用相同的 `ContentSummary` / redaction rules。

这样：

```text
Event
RunState
logs
```

不会形成三个不同的媒体暴露边界。

---

### 8.7 Subscriber 获得的内容

默认 subscriber：

> **获得 public media-safe Event representation。**

不获得：

```text
raw BytesSource
full local file
raw audio/image payload
```

---

### 8.8 Event Sequence

每 Event：

```text
sequence
```

Run 内单调递增。

Parallel Tool events 按实际 execution order。

ToolResult transcript 仍按 call order。

---

### 8.9 Event History

v1：

> **AgentRun 生命周期内完整保留 public Event history。**

不实现 bounded truncation。

但：

```text
history stores only media-safe summaries
```

---

### 8.10 Replay + Live 原子性

必须原子：

```text
capture history snapshot
+
register subscriber
```

然后：

```text
replay snapshot
    ↓
live queue
```

保证：

```text
no duplicate
no gap
```

---

### 8.11 Multiple Subscribers

多个 subscriber：

```text
independent
own queue
own replay state
```

互不阻塞。

---

### 8.12 Slow Subscriber

queue 满：

```text
disconnect subscriber
```

不能阻塞 AgentRun。

之后调用方可以重新订阅并 replay。

---

### 8.13 Media Redaction

Events / RunState / logs / errors 默认不能包含：

```text
raw bytes
full local path
URL credentials
URL query
URL fragment
raw image/audio payload
```

统一：

```python
sanitize_media_reference(...)
```

至少：

```text
strip credentials
strip query
strip fragment
redact path root
```

---

### 8.14 EventStore Boundary

当前 text-oriented JSONL EventStore：

> **不构成 multimodal durable persistence。**

v1：

```text
in-memory public Event history
    supported

text-safe / redacted EventStore
    supported

raw multimodal durable persistence
    unsupported
```

不能：

```text
bytes → str()
```

然后声称可 round-trip。

---

### 8.15 Event Serialization

必须使用：

```text
dedicated Event codec
```

保存：

```text
redacted media summaries
```

不能保存 raw BytesSource。

---

### 8.16 Session Persistence

v1 不实现 Session persistence。

因此不定义：

```text
BytesSource persistent base64 schema
FileSource cross-host replay
UrlSource replay guarantee
blob storage
transcript migration schema
```

---

### 8.17 RunResult

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

---

### 8.18 final_message

表示：

> **本 Run 已 commit 的最后一个 AssistantMessage。**

可以包含：

```text
Text
Image
Audio
File
ToolCalls
mixed content
```

不保证是自然语言最终答案。

---

### 8.19 RunTerminationReason

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

普通 Media capability / resolution failure：

```text
MODEL_ERROR
```

但由 Run cancel / timeout 引起的 Resolver failure：

```text
CANCELLED / TIMED_OUT
```

保持原终止语义。

---

### 8.20 RunError

```python
@dataclass(frozen=True)
class RunError:
    code: str
    message: str
    retryable: bool = False
    cause_type: str | None = None
```

不能暴露：

```text
traceback
exception repr
secret
credential URL
full local path
```

---

### 8.21 RunConfig

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

不包含 MediaLimits。

规则：

```text
config=None
    → Agent.default_run_config

provided config
    → complete replacement
```

不做 field merge。

---

## 9. Compatibility Migration Contract

### 9.1 迁移原则

多模态迁移是：

> **内部 canonical representation 的破坏性变更，但 public text usage 尽可能兼容。**

目标：

```text
legacy public call
        ↓
compat normalize
        ↓
new canonical protocol
```

不维护两套 transcript。

---

### 9.2 Message 旧字段兼容

现有 public Message types 中已有的：

```text
timestamp
finish_reason
usage
model
tool_calls
```

等字段：

> **不得因为多模态迁移随意删除或改变语义。**

重点只迁移：

```text
content: str
```

到：

```text
content: tuple[MessageContent, ...]
```

---

### 9.3 旧构造器兼容

例如旧：

```python
UserMessage(
    "hello",
    timestamp=timestamp,
)
```

继续工作。

内部：

```text
"hello"
    ↓
TextContent("hello")
```

---

### 9.4 AssistantMessage 兼容

例如：

```python
AssistantMessage(
    "hello",
    tool_calls=tool_calls,
    finish_reason=finish_reason,
    usage=usage,
    model=model,
    timestamp=timestamp,
)
```

仍应保持原字段语义。

只有 content representation 发生 normalization。

---

### 9.5 位置参数兼容

如果当前已有：

```python
AssistantMessage("hello", tool_calls)
```

这种 public 使用：

> v1 compatibility cycle 应继续接受。

但新代码和新文档：

> 推荐 keyword arguments。

---

### 9.6 ToolExecutionResult Migration

现有：

```text
ToolExecutionResult
```

逐步迁移：

```text
ToolOutput
```

可以提供：

```text
alias
DeprecationWarning
compat adapter
```

一个过渡周期。

---

### 9.7 Speech Compatibility

现有 cascade Speech：

```text
AssistantMessage
      ↓
extract TextContent
      ↓
TTS
```

如果没有 TextContent：

```text
Speech layer decides ignore / reject
```

Kernel 不自动做 media → text 转换。

---

### 9.8 EventStore Compatibility

旧 text EventStore：

```text
text-safe compatibility mode
```

多模态 Event：

```text
redacted summary codec
```

禁止 raw bytes stringify。

---

## 10. Runtime Invariants

### Ownership

1. Agent 是 immutable reusable definition。
2. Session.messages 是唯一 canonical transcript。
3. `start()` 先获取 active ownership。
4. ownership 成功后才能 commit initial UserMessage。
5. failed `start()` 不污染 transcript。
6. start 成功后 eager execution。
7. terminal Run 自动释放 Session ownership。
8. 外部不能直接修改 transcript。
9. 一个 Run 只表示一次 execution。

### Media Configuration

10. MediaLimits 属于 Agent definition。
11. Session 创建时固定 capture MediaLimits。
12. Session 生命周期内 MediaLimits 不变。
13. RunConfig 不改变 canonical media validity。
14. MediaResolver 属于 Agent execution dependency。
15. shared MediaResolver 必须 concurrency-safe。

### Context / State

16. `RunContext != ModelContext`。
17. metadata 不隐式进入 ModelContext。
18. Resolver 可以读取 RunContext，但不能修改它。
19. Resolver 读取 metadata 不代表 metadata 可进入模型。
20. metadata 不进入默认 Event / Result / logs。
21. `Session.messages != RunState != Events`。
22. internal `_RunState` 不属于 canonical truth。
23. Public RunState 必须 immutable。
24. Public RunState 不暴露 raw media payload。
25. `_RunState` 可以持有 raw provisional media。
26. 状态机必须 `CREATED → RUNNING → exactly one terminal`。
27. terminal 后不能恢复。
28. single Tool failure 不写 RunState.error。
29. Run FAILED 才写 error。

### Canonical Message

30. MessageContent 是 closed union。
31. MediaSource 是 closed union。
32. unknown Content/Source 必须显式失败。
33. canonical content collection 一律 tuple。
34. public Sequence 必须 normalize。
35. str/bytes/bytearray 不作为 Sequence of Content。
36. MessageContent order 属于 canonical semantics。
37. UserMessage 至少一个有效 Content。
38. text-only UserMessage 至少一条非空白 text。
39. mixed UserMessage 可包含空 TextContent。
40. BytesSource non-empty。
41. BytesSource 受 max_inline_bytes 限制。
42. FileSource 依当前宿主 `pathlib.Path.is_absolute()` 语义为 absolute。
43. UrlSource absolute http/https。
44. Image MIME 为 image/*。
45. Audio MIME 为 audio/*。
46. File MIME 可一般 MIME。
47. MIME canonical form 不含参数。

### Media Semantics

48. BytesSource 是 snapshot-like inline payload。
49. FileSource 是 external live reference。
50. UrlSource 是 external live reference。
51. v1 不保证 external reference replay determinism。
52. source provenance 不等于 access authorization。
53. User/Tool/Provider/history 产生的 File/URL 都必须重新经 MediaResolver。
54. Kernel 不自动 OCR/STT/caption/conversion。
55. AudioContent.transcript 不自动 model-visible。
56. ImageContent.detail 只是 optional hint。
57. MediaResolver 默认不自动访问 File/URL。
58. `resolve()` 必须获得当前 RunContext。
59. `resolve()` 必须获得 canonical expected_media_type。
60. Resolver 检测 MIME 必须与 expected_media_type 比较，冲突为 MEDIA_TYPE_MISMATCH。
61. 同一 invocation 并行 resolve 不得改变 canonical content order。
62. ordinary sibling resolve failure 使用 first-observed primary failure，并 cleanup 已成功资源。
63. ResolvedMedia 是单 Provider consumption scope resource。
64. ResolvedMedia payload 在 close 前保持可用。
65. ResolvedMedia 必须显式声明 BORROWED / OWNED。
66. BORROWED payload 不得被 close 删除。
67. OWNED payload 应由 close best-effort cleanup。
68. close 必须 async/idempotent/best-effort。
69. Resolver 只 cleanup 自己拥有的资源。
70. user-provided FileSource.path 不得被删除。
71. Resolver 必须响应 cancellation。
72. Resolver ordinary failure → MODEL_ERROR。
73. Resolver deadline/user cancellation 保持 timeout/cancel semantics。

### Transcript

70. transcript 必须满足 formal grammar。
71. Tool Exchange 连续。
72. ToolResult count/order/call ID 对应。
73. Tool Exchange 中间不能插入 User。
74. ToolCall ID 同一 Assistant 内唯一。
75. orphan ToolResult 非法。
76. consecutive User 合法。
77. multimodal Assistant + tools 合法。
78. partial Model output 不进入 transcript。
79. history import 与 continue 使用同一 validator。

### Model / Capability

80. ModelCapabilities 是 coarse declaration。
81. exact Provider validation 属于 Adapter。
82. unsupported capability → MODEL_ERROR。
83. FILE capability 不代表所有文件。
84. ToolResult capability 单独声明。
85. output modality 在 commit 前验证。
86. unsupported final output 不 commit。
87. invalid provider-normalized output → MODEL_ERROR。
88. text/tool args 可 incremental streaming。
89. non-text media v1 complete-block only。
90. realtime frame 不属于 normal ModelStreamItem。

### Mixed Streaming

91. ModelStreamItem 顺序必须保持 Provider-normalized logical order；`ContentCompleted` 只允许承载非文本完整 block，Provider complete text block 必须归一化为 `TextDelta`。
92. TextDelta 累积到当前 text buffer。
93. 非文本 ContentCompleted 前必须 flush 当前 text buffer。
94. ContentCompleted 必须占据最终 content 的流顺序位置。
95. 非文本 ContentCompleted 后的新 TextDelta 必须开始新的 text buffer。
96. 中间有非文本 ContentCompleted 的 TextDelta 不得跨 block 合并。

### ToolCall Streaming

97. ToolCallDelta 使用 index 作为 provisional identity。
98. Provider 可在 stream 后期提供 id/name。
99. Adapter 必须按 index 聚合 ToolCall fragments。
100. stream 完成后才形成 canonical ToolCall。
101. canonical ToolCall 必须具有 valid id/name/arguments。
102. 缺 id/name/valid arguments → MODEL_ERROR。
103. Provider 无 ID 时 Adapter 可以生成 canonical ID。
104. AgentLoop / generic Kernel 不负责生成 Provider ToolCall identity。

### Model Event Timing

105. `ModelCompleted` 只能在 Assistant canonical commit 后产生。
106. 收到 ModelCompleted 时 Session.messages 已包含 Assistant。
107. Provider stream socket completion 不等于 public ModelCompleted。

### Control

108. steer/follow_up 先 pending。
109. observe != consume。
110. only safe-boundary commit。
111. early failure 也可形成 safe boundary。
112. pending control 按 receive sequence。
113. cancel > steer > follow-up。
114. cancel 后不启动新 Model。
115. cancel 后不启动 pending Tool。
116. normal terminal 不遗留 uncommitted controls。
117. terminal cancel no-op。
118. terminal steer/follow_up → RunFinishedError。
119. continue_run 创建新 Run。

### Cancellation

120. Run 有 cancellation token。
121. Tool 有 child token。
122. first cancellation reason wins。
123. Run cancel 传播 child。
124. policy 可 local cancel。
125. timeout 是 soft deadline。
126. RunTimedOut 在 cleanup 后。
127. non-cooperative Model/Tool/Resolver 可晚结束。

### Tool

128. ToolOutput != ToolCallOutcome。
129. ToolOutput.content 必须 normalize。
130. invalid ToolOutput → ToolCall FAILED。
131. invalid ToolOutput 不直接 Run FAILED。
132. is_error=True → FAILED。
133. Outcome → ToolResultMessage。
134. ToolResult 不含 mutable details。
135. outcome 顺序跟 call 顺序。
136. execution completion order 可不同。
137. Tool 可返回 Text/Image/Audio/File。
138. Tool success 不因 Model consumption failure 被改写。
139. STOP_BATCH 不取消 running sibling。
140. FAIL_RUN 请求 running sibling cancel。
141. non-cooperative Tool 可 COMPLETED。
142. single Tool failure 不自动 Run FAILED。

### Policy

143. factory=None → DefaultToolExecutionPolicy。
144. 每 Run 独立 policy。
145. default before_call ALLOW。
146. default failure STOP_BATCH。
147. default steer pending SKIP。
148. default steer running CONTINUE。
149. Policy 不做 task graph。
150. Policy 不做 resource scheduling。
151. v1 无 generic retry。

### Events / Privacy

152. 每 Run 恰好一个 terminal Event。
153. Event sequence 单调。
154. parallel Tool Event 按真实 execution 顺序。
155. ToolResult transcript 按 call order。
156. internal ModelStreamItem 可持 raw media。
157. public Event history 不持 raw media。
158. ContentCompleted raw object 不直接进入 Event history。
159. subscriber 默认获得 media-safe summary。
160. Public RunState 使用同一 media-safe summary policy。
161. Event history 完整保留到 Run release。
162. replay + live registration 原子。
163. slow subscriber 不阻塞 Run。
164. waiter cancellation 不取消 Run。
165. logs/events/errors 必须 media-redacted。
166. current EventStore 不构成 multimodal durable persistence。

---

## 11. 推荐实现顺序

### Phase 1 — Canonical Message Protocol

实现：

```text
MessageContent closed union
MediaSource closed union

TextContent
ImageContent
AudioContent
FileContent

MediaLimits

runtime type validation
normalization
MIME validation
TranscriptValidator
```

---

### Phase 2 — Compatibility Layer

实现：

```text
string → TextContent shorthand

legacy UserMessage fields
legacy AssistantMessage fields
legacy positional constructors

ToolExecutionResult compatibility
```

确保 text-only tests 继续通过。

---

### Phase 3 — Runtime Types

```text
RunContext
_RunState
public RunState
RunStatus
RunPhase

RunConfig
RunResult
RunError

CancellationToken
CancellationReason
```

---

### Phase 4 — Media Runtime

```text
MediaResolver
MediaOwnership
ResolvedMedia
MediaResolutionError
media sanitization
```

---

### Phase 5 — Model Capabilities

```text
Modality
ModelCapabilities
ModelCapabilityError
coarse validation
exact Adapter validation
```

---

### Phase 6 — Session / AgentRun

```text
atomic start
eager execution
active ownership
Session-fixed MediaLimits

read-only transcript
multi-await result
```

---

### Phase 7 — RunControl

```text
cancel
steer
follow_up

PendingControl
sequence
observe
safe commit
terminal flush
```

---

### Phase 8 — Tool Runtime

```text
ToolOutput
ToolCallOutcome
ToolExecutionBatchResult

Sequential
Parallel
max_concurrency

child cancellation
Default Policy
STOP_BATCH
FAIL_RUN
stable ordering
```

---

### Phase 9 — Model Streaming

```text
TextDelta
ToolCallDelta
ContentCompleted

provisional ToolCall builder
ordered content builder

mixed multimodal ordering
final Assistant build
```

---

### Phase 10 — AgentLoop Integration

```text
FrozenToolSet
ContextManager

MediaResolver

Model invocation
Assistant validation
Assistant commit

ToolExecutor
ToolResult commit

RunControl
termination
```

要求：

```text
no modality-specific AgentLoop control-flow branch
```

---

### Phase 11 — Event Layer

实现：

```text
ContentSummary
ModelDelta Event summary

media-safe RunState
media-safe Event history

replay
subscriber queues
redaction
```

---

### Phase 12 — Image Adapter

至少一个真实 Adapter：

```text
text input/output
text + image input
image ToolResult
```

通过真实 integration tests。

---

### Phase 13 — EventStore Migration

实现：

```text
safe Event codec
legacy text store compatibility
multimodal summary persistence
```

raw multimodal persistence 不进入 v1。

---

### Phase 14 — Semantic Tests

完成全部 semantic test matrix。

---

## 12. Semantic Tests

### 12.1 Canonical Types

```text
wrong TextContent.text type rejected

wrong ImageContent.detail type rejected

wrong AudioContent.transcript type rejected

BytesSource.data not bytes rejected

list accepted
tuple accepted

str Sequence rejected
bytes Sequence rejected

unknown Content rejected
unknown Source rejected
```

---

### 12.2 Media Validation

```text
empty BytesSource rejected
oversized BytesSource rejected

relative FileSource rejected
absolute FileSource accepted
cross-platform absolute-path rule follows current host pathlib semantics

invalid URL rejected
http accepted
https accepted

Image + image/* accepted
Image + audio/* rejected

Audio + audio/* accepted
Audio + image/* rejected

File + application/pdf accepted
```

---

### 12.3 UserMessage

```text
empty rejected

whitespace text-only rejected

empty Text + Image accepted

Image-only accepted
Audio-only accepted
File-only accepted
```

---

### 12.4 Transcript Grammar

```text
valid Tool Exchange

reversed result rejected
missing result rejected
orphan result rejected

duplicate ToolCall ID rejected

consecutive User accepted

Assistant + multimodal + ToolCall accepted

ToolResult Image accepted
ToolResult Audio accepted

content order preserved
```

---

### 12.5 Media Replay

```text
BytesSource stable

FileSource file modified between turns
    → live reference semantics

UrlSource expired before continue_run
    → resolve failure

history validation does not resolve media
```

---

### 12.6 MediaResolver RunContext

```text
resolver receives correct session_id

resolver receives correct run_id

resolver receives expected metadata view

resolver cannot alter Kernel RunContext semantics

concurrent Runs receive distinct RunContexts
```

---

### 12.7 MediaResolver Access

```text
BytesSource resolves

FileSource denied without resolver

UrlSource denied without resolver

allowed root accepted

path escape rejected

private network URL rejected by application resolver

redirect policy enforced

cancel during resolve

timeout during resolve
```

---

### 12.8 ResolvedMedia Ownership

```text
BORROWED FileSource path survives close

OWNED temp Path removed on close

BORROWED bytes not destroyed

OWNED bytes released

close idempotent

multiple close calls safe
```

---

### 12.9 ResolvedMedia Lifecycle

```text
payload usable until close

close after Provider success

close after Adapter encoding failure

close after Provider request failure

close after Provider response failure

close after cancellation

close after timeout

cleanup failure doesn't override primary result
```

---

### 12.10 Media Type Resolution

```text
canonical MIME + matching detected MIME
    → accepted

canonical MIME + conflicting detected MIME
    → MEDIA_TYPE_MISMATCH

canonical MIME=None
    → resolver may detect MIME

detected MIME does not mutate canonical Message

Adapter passes canonical media_type as expected_media_type to resolver
```

### 12.10.1 同一 Invocation 多媒体 Resolution

```text
multiple external media resolve in parallel
    → encoded provider content remains canonical order

one ordinary resolve fails
    → unfinished siblings cancelled
    → resolved siblings closed
    → first observed ordinary failure is primary

Run cancellation / timeout during parallel resolve
    → CANCELLED / TIMED_OUT dominates ordinary failure
```

---

### 12.11 Media Provenance

```text
User UrlSource
    → resolver

Tool UrlSource
    → resolver

Provider UrlSource
    → resolver

history-imported UrlSource
    → resolver
```

No provenance bypass.

---

### 12.12 Capabilities

```text
TEXT-only accepts text

TEXT-only rejects image

vision accepts image

FILE coarse capability + PDF accepted

FILE coarse capability + ZIP rejected

ToolResult IMAGE accepted

ToolResult AUDIO rejected

unsupported input
    → MODEL_ERROR

unsupported output
    → MODEL_ERROR
```

---

### 12.13 Mixed Multimodal Streaming

```text
TextDelta("A")
Image
TextDelta("B")

→

[
  Text("A"),
  Image,
  Text("B")
]
```

必须保证。

Provider complete TextContent block
    → normalized into TextDelta
    → never emitted as ContentCompleted(TextContent)

---

### 12.14 Adjacent Text Streaming

```text
TextDelta("A")
TextDelta("B")
Image
TextDelta("C")
TextDelta("D")

→

[
  Text("AB"),
  Image,
  Text("CD")
]
```

---

### 12.15 Multiple Media Blocks

```text
Text("A")
Image1
Image2
Text("B")
Audio
Text("C")
```

最终顺序必须严格一致。

---

### 12.16 ToolCall Streaming

覆盖：

```text
arguments before id

name before id

id before name

late id

late name

multiple ToolCalls interleaved by index

missing final id

missing final name

invalid final arguments
```

---

### 12.17 Provider Without ToolCall ID

```text
Provider has no ID
    ↓
Adapter generates canonical ID
    ↓
ID unique within AssistantMessage
```

Kernel AgentLoop 不生成。

---

### 12.18 Model Streaming

```text
TextDelta provisional

ToolCallDelta provisional

ContentCompleted raw internal only

ContentSummary public only

raw BytesSource absent from Event history

stream failure
    → no Assistant commit

successful stream
    → final validation
    → Session commit
    → ModelCompleted
```

---

### 12.19 ModelCompleted Timing

```text
subscriber receives ModelCompleted
    ↓
Session.messages already contains AssistantMessage
```

必须始终成立。

---

### 12.20 Public RunState Safety

```text
internal _RunState contains raw BytesSource

public RunState contains ContentSummary only

public RunState contains no raw bytes

public RunState contains no credential URL

public RunState contains no full local path
```

---

### 12.21 Session

```text
concurrent start only one succeeds

failed start no transcript mutation

invalid media input no transcript mutation

start eager

terminal releases ownership

Session uses fixed MediaLimits
```

---

### 12.22 Control

```text
steer immediately after start

steer with image

follow_up with audio

steer during Model

steer during MediaResolver

steer during sequential tools

steer during parallel tools

follow_up + steer receive order

cancel dominates

terminal steer rejected
terminal follow_up rejected
terminal cancel no-op

normal terminal leaves no uncommitted controls
```

---

### 12.23 Cancellation

```text
USER cancellation

TIMEOUT cancellation

first reason wins

Run cancel → Tool child

Run cancel → MediaResolver

policy local Tool cancel

non-cooperative Tool may complete

RunTimedOut after cleanup
```

---

### 12.24 ToolOutput

```text
string shorthand

text output

image output

audio output

file output

mixed output

invalid media → Tool FAILED

details not in ToolResult

is_error=True → FAILED
```

---

### 12.25 ToolPolicy

```text
factory=None → DefaultPolicy

before_call ALLOW

Tool FAILED → STOP_BATCH

PENDING steering → SKIP

RUNNING steering → CONTINUE

REJECT
SKIP
FAIL_RUN
```

---

### 12.26 Parallel Tools

```text
A B C start

B finishes first

events reflect B-first

BatchResult A B C

ToolResults A B C

STOP_BATCH:
pending skipped
running continue

FAIL_RUN:
pending skipped
running cancel requested
```

---

### 12.27 ToolResult Capability

```text
Tool COMPLETED with Image
        ↓
ToolResult committed
        ↓
next Model can't consume Image
        ↓
Run FAILED / MODEL_ERROR
        ↓
Tool stays COMPLETED
```

---

### 12.28 Context

```text
Model + Executor same FrozenToolSet

metadata never leaks

whole multimodal Message preserved

ToolExchange atomic

explicit model-visible app content works
```

---

### 12.29 Events

```text
exactly one terminal Event

late subscriber replay

terminal subscriber replay

multiple subscribers independent

snapshot/live no gap

slow subscriber disconnected

no raw media bytes

redacted path/URL
```

---

### 12.30 Result

```text
multiple awaiters

repeat result

waiter cancellation doesn't cancel Run

multimodal final_message preserved
```

---

### 12.31 `continue_run()`

```text
empty Session rejected

active Run rejected

dangling ToolCall rejected

invalid transcript rejected

valid multimodal history accepted

external media not resolved during validation

same Session MediaLimits used
```

---

### 12.32 Compatibility

```text
UserMessage("x")
    → TextContent

AssistantMessage("x")
    → TextContent

old timestamp preserved

old finish_reason preserved

old usage/model preserved

old ToolCall fields preserved

old positional call works

ToolExecutionResult compatibility works

text-only tests remain green

speech text extraction remains compatible
```

---

### 12.33 Real Adapter Acceptance

v1 必须至少：

```text
text E2E

text + image E2E

image ToolResult E2E
```

不能只依赖 mock capability tests。

---

## 13. 推荐代码结构

目录只作为建议：

```text
roboagent/
├── agent/
│   ├── agent.py
│   ├── session.py
│   ├── run.py
│   ├── loop.py
│   ├── control.py
│   ├── executor.py
│   ├── hooks.py
│   └── types.py
│
├── message/
│   ├── message.py
│   ├── content.py
│   ├── media.py
│   ├── normalize.py
│   └── validator.py
│
├── context/
│   └── manager.py
│
├── model/
│   ├── model.py
│   ├── capabilities.py
│   ├── stream.py
│   └── ...
│
├── runtime/
│   ├── context.py
│   ├── state.py
│   ├── event.py
│   ├── store.py
│   └── ...
│
└── tool/
    └── ...
```

如果当前类型已经位于：

```text
runtime/types.py
```

等现有位置：

> **不需要为了文档先移动代码。**

优先级：

```text
public semantics
    >
semantic tests
    >
directory organization
```

---

## 14. v1 完成标准

RoboAgent v1 最终定位：

> **Generic Async Modality-Agnostic Agent Runtime Kernel**

必须同时具备：

```text
provider-neutral
model-agnostic
tool-agnostic
modality-agnostic
async
embeddable
```

核心消息关系：

```text
Message
  ↓
ordered MessageContent[]
  ├── TextContent
  ├── ImageContent
  ├── AudioContent
  └── FileContent
```

媒体关系：

```text
MediaSource
  ├── BytesSource
  ├── FileSource
  └── UrlSource
          │
          ▼
     MediaResolver
          │
          ▼
     ResolvedMedia
       ├── payload
       ├── media_type
       ├── size
       └── ownership
          │
          ▼
       Adapter
```

输入流程：

```text
Raw Input
    ↓
Normalize
    ↓
Canonical Structure Validation
    ↓
Message Semantic Validation
    ↓
Canonical Transcript
    ↓
ContextManager
    ↓
ModelContext
    ↓
Coarse Capability Validation
    ↓
Exact Adapter Validation
    ↓
MediaResolver
    ↓
Provider
```

模型流：

```text
Provider Stream
      │
      ├── TextDelta
      ├── ToolCallDelta(index-based provisional identity)
      └── ContentCompleted(raw)
      │
      ▼
ordered stream builders
      ├── content builder
      └── ToolCall builder
      │
      ▼
final AssistantMessage
      ↓
Canonical Validation
      ↓
Output Capability Validation
      ↓
Session commit
      ↓
ModelCompleted
```

公共观测：

```text
internal _RunState / ModelStreamItem
        │
        ▼
   summarize / redact
        │
        ├── public RunState
        └── public Events
```

Tool：

```text
Tool
  ↓
ToolOutput
  ↓
Canonical Output Validation
  ↓
ToolCallOutcome
  ↓
ToolResultMessage
```

Control：

```text
Pending Control
      ↓
observe
      ↓
safe boundary
      ↓
consume / commit
```

Realtime：

```text
Realtime Media Stream
        │
        ▼
Realtime / Speech Adapter
        │
        ▼
Agent Turn / Model
```

Realtime transport 与 canonical transcript 永远保持分层。

最终原则：

> **多模态扩展 Runtime 的数据协议、媒体访问边界、Provider Adapter 和 Tool I/O，但不扩张 Agent Runtime 控制流。**

v1 必须至少完成：

```text
canonical Text/Image/Audio/File protocol

MediaLimits

MediaResolver with RunContext
ResolvedMedia ownership + lifecycle

ModelCapabilities
exact Adapter validation

ordered mixed multimodal streaming
ToolCall streaming normalization

Text/Image real E2E
Image ToolResult real E2E

safe Assistant commit
ModelCompleted commit ordering

ToolOutput / Outcome / ToolResult split

cancel / steer / follow-up

parallel Tool semantics

media-safe public RunState
media-safe Event history
Event replay

legacy text compatibility
```

只有当这些语义全部实现，并由 semantic tests 锁定后，RoboAgent v1 才可以视为真正稳定的长期 Agent Runtime Kernel。
