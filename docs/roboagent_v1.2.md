# RoboAgent V1.2 设计文档

**版本：V1.2**
**定位：Long-Horizon Foundations**
**基础版本：RoboAgent V1.1 Runtime Kernel**

---

# 1. 目标、范围与总体架构

## 1.1 背景

RoboAgent V1.1 已经完成基础 Agent Runtime Kernel，并建立了以下稳定边界：

```text
Agent
Session
Run
AgentLoop

Model
ModelContext

Tool
ToolRegistry
ToolExecutor
ToolExecutionPolicy
ToolEffectRecord

ContextManager

Hooks
Events
Skills

Cancellation
Timeout
steer / follow_up
```

V1.1 主要解决：

```text
Agent 能否正确、安全、确定性地运行？
```

其核心语义包括：

* `Agent / Session / Run` 的职责隔离；
* canonical transcript 作为唯一会话事实来源；
* ModelContext 只是模型可见投影；
* ToolExchange 原子提交；
* ToolResult 的模型可见结果与现实 ToolEffect 分离；
* `READ_ONLY / SIDE_EFFECTING` effect semantics；
* timeout / cancellation / generic exception 的保守副作用判断；
* `retry_safe`；
* steer / follow_up 的 pending queue 与 legal boundary；
* streaming protocol；
* Hooks 与 Events 职责分离；
* bounded concurrent Tool execution；
* 模型可见 ToolResult 顺序稳定；
* multimodal canonical content。

V1.2 不重新设计上述 Runtime Kernel。

V1.2 解决的是新的问题：

```text
Agent 能否持续完成长时间、多步骤任务？
```

长时间运行后，V1.1 会自然遇到：

```text
Session transcript 持续增长
        ↓
ModelContext 接近上下文窗口

ToolResult 可能非常大
        ↓
无法全部内联进 transcript / model request

任务产生大量中间产物
        ↓
需要稳定 Workspace

进程退出
        ↓
Session 无法继续

需要外部工具生态
        ↓
需要 MCP

机器人 SIDE_EFFECTING 工具
        ↓
部分操作需要人工审批
```

因此 V1.2 定义为：

```text
RoboAgent V1.2
Long-Horizon Foundations
```

目标仍然是：

```text
lightweight
provider-neutral
embeddable
Python Agent Runtime
```

而不是建设：

```text
Agent Platform
Workflow Engine
Multi-Agent Platform
AI Operating System
```

---

## 1.2 V1.2 分阶段交付

V1.2 分为两个独立冻结里程碑。

### V1.2a — Context & Durability

解决：

```text
上下文如何长期保持可用？
大型结果如何稳定外置？
Session 如何跨进程恢复？
```

包含：

```text
ContextRequest
PreparedContext
ModelContextSegment

ContextBudget
TokenEstimator
ContextSummary
Incremental Compaction

Workspace
RawToolResult
ToolResultMaterializer
ArtifactReferenceContent

SessionSnapshot
SessionSnapshotCodec
runtime_revision
durable_revision
last_pending_sequence

SessionRepository
CAS
LocalSessionRepository
```

### V1.2b — Ecosystem & Safety

解决：

```text
如何接入外部工具生态？
如何安全执行需要人工确认的工具？
```

包含：

```text
MCPClient
MCPToolAdapter
MCP result mapping
MCP trust model

ToolPolicyDecision
REQUIRE_APPROVAL
ApprovalRequest
ApprovalProvider

SERIAL / CONCURRENT approval semantics
Approval timeout
Security hardening
必要 observability
```

V1.2a 必须先完成 semantic acceptance 并冻结，再进入 V1.2b。

---

## 1.3 明确非目标

V1.2 不实现：

```text
Long-term Memory
Semantic Memory
User Memory
RAG
Vector Database

Planner Framework
Todo Runtime
Workflow Graph

Agent-as-Tool
SubAgent
Handoff
Multi-Agent
Swarm
Coordinator

Sandbox Platform
Container Runtime
Distributed Runtime

Persistent in-flight Run recovery
Run checkpoint resume
Tool execution replay
Crash-time side-effect reconciliation

Browser Automation Framework
Scheduler / Cron
Messaging Gateway

Plugin Marketplace
Skill Marketplace

Full Tracing Platform
Web UI
```

其中：

```text
Agent-as-Tool / Handoff / SubAgent
```

建议进入 V1.3。

```text
Memory / Sandbox / active Run recovery
```

建议进入更后续版本。

---

## 1.4 不变量与设计原则

### canonical transcript 始终是事实来源

必须保持：

```text
Session transcript
≠ ModelContext
≠ ContextSummary
≠ Workspace
≠ ToolEffect
≠ persistent byte representation
```

Session transcript 记录：

```text
已经正式提交的 canonical conversation facts
```

ContextManager 不允许直接修改 transcript。

Compaction 不允许删除、替换或重写 transcript。

---

### ContextManager 只计算，不拥有 Session 状态

V1.2 明确规定：

```text
ContextManager
=
pure runtime context preparation component
```

它可以：

```text
读取 ContextRequest
计算 ModelContext
计算 CompactionUpdate
返回 Usage delta
```

但不能：

```text
直接修改 Session
直接 revision++
直接持久化 Session
反向调用 Session.commit_*
```

状态修改仍属于 Session。

---

### Summary 不能获得 system 权限

Summary 来源于：

```text
UserMessage
AssistantMessage
ToolResultMessage
```

因此：

```text
Summary = derived conversation context
```

不是 system instruction。

禁止：

```text
ContextSummary
→ system prompt
```

否则可能把低权限历史内容提升到高权限 system instruction。

---

### Large ToolResult 必须在 commit 前完成 materialization

正确路径：

```text
Tool invoke
    ↓
RawToolResult
    ↓
ToolResultMaterializer
    ↓
canonical ToolContent
    ↓
ToolExecutionResult
    ↓
ToolExchange commit
```

而不是：

```text
commit 巨型 ToolResult
    ↓
ContextManager.prepare()
    ↓
再写 Workspace
```

---

### MCP 只是 Tool source

MCP Tool 必须进入现有执行体系：

```text
MCP
 ↓
Tool adapter
 ↓
ToolRegistry
 ↓
ToolExecutionPolicy
 ↓
Approval
 ↓
Hooks
 ↓
ToolExecutor
 ↓
ToolEffect
```

不得增加第二套 MCPExecutor。

---

### V1.2 只恢复 Session

支持：

```text
process exit
↓
load SessionSnapshot
↓
restore Session
↓
start new Run
```

不支持：

```text
process crash
↓
restore in-flight Run
↓
继续旧 await/tool execution
```

恢复后：

```text
active_run_id = None
```

---

### Runtime Kernel 状态机冻结，扩展协议允许演进

V1.2 不改变：

```text
Session ownership
Run lifecycle
AgentLoop turn lifecycle
ToolExchange atomic commit
ToolEffect truth model
steer/follow_up legal boundary
```

但允许升级：

```text
ContextManager protocol
ModelContext representation
Tool result representation
Tool policy result representation
```

只要升级是为了闭合 V1.2 必要语义。

---

# 2. ContextRequest、PreparedContext 与 Context Compaction

## 2.1 ContextRequest

V1.1：

```python
prepare(
    snapshot: ContextSnapshot,
    cancellation: CancellationToken,
) -> ModelContext
```

不足以支持 V1.2。

Compaction 需要知道：

```text
当前 Session ID
当前 canonical transcript
当前已有 ContextSummary
本轮 ModelSettings
ModelCapabilities
prompt
tools
skills
```

因此正式定义：

```python
@dataclass(frozen=True)
class ContextRequest:
    snapshot: ContextSnapshot

    model_settings: ModelSettings
    model_capabilities: ModelCapabilities

    current_compaction: ContextSummary | None
```

其中 `ContextSnapshot` 正式定义为（字段名与 V1.1 当前实现保持一致）：

```python
@dataclass(frozen=True)
class ContextSnapshot:
    session_id: str

    transcript: tuple[AgentMessage, ...]

    prompt: PromptInput | None

    tool_definitions: tuple[ToolDefinition, ...]

    skill_metadata: tuple[SkillMetadata, ...] = ()
```

`ContextRequest.model_settings` 必须来自当前 Run，而不是 Agent 的默认配置；否则
`max_output_tokens` 无法正确参与本轮 reserve 计算。

---

## 2.2 Workspace 不进入 ContextRequest

Workspace 是：

```text
service dependency
```

不是：

```text
immutable request data
```

因此不要：

```python
ContextRequest(
    workspace=workspace,
)
```

默认 ContextManager 只根据 `ArtifactReferenceContent` 中已经存在的 URI、preview、
media type 和 digest 构造投影，因此不需要读取 Workspace。

只有确实需要读取 artifact 内容的自定义 ContextManager，才可以在构造时注入只读
artifact resolver：

```python
CompactingContextManager(
    artifact_resolver=resolver,
    ...
)
```

这样：

```text
ContextRequest
=
本轮不可变计算输入

ContextManager instance
=
执行 context projection 所需要的只读 service dependencies
```

ContextManager 不拥有 Workspace 生命周期，也不能通过 resolver 写入或删除 artifact。

职责更明确。

---

## 2.3 PreparedContext

正式定义：

```python
@dataclass(frozen=True)
class PreparedContext:
    model_context: ModelContext

    usage_delta: Usage

    compaction_update: CompactionUpdate | None = None
```

含义：

### `model_context`

本轮模型真正看到的完整上下文。

### `usage_delta`

Context preparation 本身产生的 usage。

例如：

```text
summarizer model call
```

不包含后续主模型调用 usage。

### `compaction_update`

如果本次 prepare 新生成或更新了 compaction summary，则返回该更新。

ContextManager 不自己 commit。

---

## 2.4 CompactionUpdate

定义：

```python
@dataclass(frozen=True)
class CompactionUpdate:
    summary: ContextSummary | None

    expected_summary_digest: str | None
```

三种状态必须区分：

```text
PreparedContext.compaction_update is None
→ Session compaction state 不变

CompactionUpdate(summary=S, ...)
→ 替换为 S

CompactionUpdate(summary=None, ...)
→ 清除失效 summary
```

`expected_summary_digest` 是 prepare 时看到的旧 summary digest；旧 summary 不存在时为
`None`。`Session.commit_compaction()` 必须在更新前比较当前值，避免提交基于过期
working-context state 的结果。不匹配时本次 prepare 结果作废并重新 prepare。

AgentLoop 收到：

```text
PreparedContext.compaction_update
```

后负责让 Session 完成正式状态提交。

---

## 2.5 AgentLoop 中的调用顺序

V1.2 固定：

```text
capture current Session state
    ↓
ContextRequest
    ↓
ContextManager.prepare()
    ↓
PreparedContext
    ↓
merge usage_delta（即使后续 commit/persist 失败也计费）
    ↓
if compaction_update is not None:
    Session.commit_compaction()
    revision++
    persist if enabled
    ↓
Model request
```

即：

> 一个实际被 Model request 使用的新 summary，必须先成为 Session runtime state。

禁止：

```text
Model request 使用 Summary N
↓
Summary N 尚未 commit
```

---

## 2.6 Compaction commit 的意义

ContextSummary 不属于 canonical transcript，但属于：

```text
Session working-context state
```

因此：

```text
Session transcript
=
conversation truth

Session compaction
=
derived durable working-context state
```

二者事实层级不同，但均由 Session 拥有。

---

## 2.7 Compaction commit 不改变 transcript

`Session.commit_compaction()`：

```text
只更新 current_compaction
revision++
```

清除失效 summary 同样是一次 working-context state mutation，也必须 revision++ 并按
persistent Session 规则保存。

不能：

```text
append summary message
remove old messages
rewrite ToolResult
```

---

## 2.8 ModelContextSegment

V1.2 不再要求：

```text
ModelContext.messages
=
canonical AgentMessage only
```

定义封闭 union：

```python
ModelContextSegment: TypeAlias = (
    MessageSegment
    | SummarySegment
    | WorkspaceReferenceSegment
)
```

禁止使用空 `Protocol` 允许第三方任意 segment 类型注入。

任何新的 model context segment 类型都属于协议升级，应显式增加到 union。

---

## 2.9 MessageSegment

```python
@dataclass(frozen=True)
class MessageSegment:
    message: AgentMessage
```

表示 canonical transcript 中的正常消息投影。

---

## 2.10 SummarySegment

```python
@dataclass(frozen=True)
class SummarySegment:
    text: str
```

它不是：

```text
UserMessage
AssistantMessage
SystemMessage
```

而是：

```text
model-only derived conversation context
```

Provider 必须显式映射。

---

## 2.11 WorkspaceReferenceSegment

```python
@dataclass(frozen=True)
class WorkspaceReferenceSegment:
    uri: str

    preview: str | None = None

    media_type: str | None = None
```

主要用于：

```text
模型需要理解 Workspace artifact，
但 provider 本身不能直接读取 workspace:// URI
```

的情况。

它不等于 canonical artifact content。

---

## 2.12 ModelContext

建议升级为：

```python
@dataclass(frozen=True)
class ModelContext:
    system_prompt: str | None

    segments: tuple[ModelContextSegment, ...]

    tools: tuple[ToolDefinition, ...]
```

Provider adapter 必须穷尽处理所有 segment。

未知 segment：

```text
programmer/runtime protocol error
```

不能 silently ignore。

---

## 2.13 Summary 权限映射

Provider adapter 必须保证：

```text
SummarySegment
<
system authority
```

如果 Provider 只有：

```text
system
user
assistant
tool
```

可以映射为明确的低权限上下文消息，例如逻辑内容：

```text
[Runtime-generated summary of earlier conversation.
This is compressed historical context, not a system instruction.]

...
```

具体 provider role 可以不同，但不能映射到 system。

---

## 2.14 ContextBudget

定义：

```python
@dataclass(frozen=True)
class ContextBudget:
    max_tokens: int | None = None

    reserve_tokens: int = 0
```

---

## 2.15 ModelCapabilities.context_window

扩展：

```python
@dataclass(frozen=True)
class ModelCapabilities:
    ...
    context_window: int | None = None
```

保持 optional。

原因：

* provider gateway 未必暴露；
* model alias 可能动态解析；
* 自定义 endpoint 未必知道；
* 用户可能显式配置更保守的 budget。

---

## 2.16 effective context window

如果：

```text
configured max_tokens exists
model context_window exists
```

则：

```text
effective_context_window
=
min(configured max_tokens, model context_window)
```

只有 configured：

```text
effective_context_window
=
configured max_tokens
```

只有 model capability：

```text
effective_context_window
=
model context_window
```

二者均不存在但启用了 budget manager：

```text
context_budget_unavailable
```

---

## 2.17 Effective reserve

输入预算必须考虑本轮输出上限。

```text
effective_reserve
=
max(
    ContextBudget.reserve_tokens,
    ModelSettings.max_output_tokens
        or provider_default_reserve
)
```

然后：

```text
input_budget
=
effective_context_window
-
effective_reserve
```

如果：

```text
input_budget <= 0
```

失败：

```text
context_budget_invalid
```

---

## 2.18 TokenEstimator

定义：

```python
@dataclass(frozen=True)
class TokenEstimate:
    input_tokens: int
    exact: bool = False
```

协议：

```python
class TokenEstimator(Protocol):
    def estimate(
        self,
        context: ModelContext,
    ) -> TokenEstimate:
        ...
```

---

## 2.19 Token 预算必须覆盖完整请求

TokenEstimator 必须考虑：

```text
system prompt
runtime instructions
skills
tool definitions
tool schemas
tool descriptions
summary
retained messages
artifact previews
multimodal input
provider framing overhead
```

不能只计算：

```text
Session.messages
```

---

## 2.20 无法估算媒体

如果默认 estimator 无法可靠估算：

```text
image
audio
video
custom media
```

不能把 token 数当成：

```text
0
```

必须：

```text
TokenEstimationError
```

或者使用保守上界。

Provider-specific estimator 可以实现更准确估算。

---

## 2.21 ContextSummary

正式定义：

```python
@dataclass(frozen=True)
class ContextSummary:
    source_start: int

    source_end_exclusive: int

    source_digest: str

    text: str

    summary_format_version: int

    summarizer_id: str | None = None
```

Usage 不必重复保存在 ContextSummary 内。

因为：

```text
Usage
=
Run accounting
```

summary 本身只需要描述 derived context state。

Compaction 生成时的 usage 通过：

```text
PreparedContext.usage_delta
```

返回。

---

## 2.22 source range

Summary 覆盖：

```text
messages[
    source_start:
    source_end_exclusive
]
```

必须从：

```text
ContextGroup boundary
```

开始和结束。

不能切割完整 ToolExchangeBlock。

V1.2 只允许 prefix compaction，因此固定：

```text
source_start = 0
```

允许任意非零 start 会产生“summary 之前的消息究竟保留还是丢弃”的额外语义，V1.2
不引入这种复杂度。

---

## 2.23 source_digest

`source_digest` 必须基于：

```text
原始 canonical messages
```

而不是 summary text。

例如：

```text
sha256(
    canonical_encode(
        messages[start:end]
    )
)
```

使用与持久化相同的 canonical message codec。

---

## 2.24 Summary reuse

ContextManager 复用当前 summary 前必须：

```text
重新计算 canonical source digest
```

如果：

```text
digest mismatch
```

则：

```text
summary invalid
↓
discard for this preparation
↓
recompute if needed
```

不能继续使用过期摘要。

---

## 2.25 summary_format_version

它表示：

```text
summary semantic format
```

与：

```text
SessionSnapshot.schema_version
```

独立。

例如：

```text
Summary format v1
=
free-form summary

Summary format v2
=
Goals
Constraints
Decisions
Facts
Pending Work
Workspace References
```

版本变化可以使旧 summary 失效。

---

## 2.26 Summary durable ownership

不定义公开：

```text
CompactionStore
```

Non-persistent Session：

```text
current_compaction
存在 Session 内存状态
```

Persistent Session：

```text
current_compaction
包含在 SessionSnapshot
```

只有 SessionSnapshot 是 durable owner。

---

## 2.27 ContextGroup

所有裁剪和压缩都基于：

```text
ContextGroup
```

内部逻辑类型：

```text
SingleMessageGroup
ToolExchangeGroup
```

ToolExchange：

```text
AssistantMessage(tool_calls)
ToolResultMessage
ToolResultMessage
...
```

必须整体处理。

---

## 2.28 Incremental Compaction

假设已有：

```text
Summary S1
covers [0, k)
```

需要继续压缩：

```text
[0, m)
```

禁止重新将：

```text
canonical [0,m)
```

全部发送给 summarizer。

正确输入：

```text
existing Summary S1[0,k)
+
canonical groups[k,m)
```

得到：

```text
Summary S2[0,m)
```

但：

```text
S2.source_digest
```

仍必须根据：

```text
canonical messages[0:m)
```

计算。

因此：

```text
summary generation input
≠
summary source digest input
```

这是故意设计。

---

## 2.29 Retained tail

Compaction 不使用固定：

```text
keep last N messages
```

作为主要策略。

应根据 token budget 选择：

```text
从最旧 ContextGroup 开始压缩，
尽可能保留最近完整 groups，
直到 projected ModelContext 满足 target budget。
```

并设置 minimum：

```text
至少保留最近一个完整 user turn
```

一个 user turn 指：

```text
最近 UserMessage
+
之后产生的 Assistant / ToolExchange
```

直到当前尾部。

---

## 2.30 Compaction target

为了避免每次刚超过 hard budget 就重新 compact，建议：

```python
@dataclass(frozen=True)
class CompactionPolicy:
    target_ratio: float = 0.7

    min_recent_turns: int = 1
```

Compaction 后目标：

```text
estimated_input_tokens
<=
input_budget * target_ratio
```

而不是只做到：

```text
input_budget - 1
```

---

## 2.31 Summarizer 自身预算

Summarizer 可以使用与主模型不同的 Model、ModelSettings 和 TokenEstimator。它必须拥有
自己的 context window / reserve 配置，不能错误复用主模型的 `ModelCapabilities`。

第一次 compaction 时，选择的 canonical prefix 必须适合 summarizer 输入预算；增量
compaction 时，以下内容也必须适合 summarizer 输入预算：

```text
old summary
+
new canonical groups
+
summary instructions
```

如果无法构造合法 summarizer request，失败：

```text
context_compaction_error
reason=summarizer_input_too_large
```

V1.2 不通过无限递归或隐式丢弃消息规避该失败。

---

## 2.32 Compaction 基本流程

```text
ContextRequest
    ↓
validate current summary
    ↓
build candidate context
    ↓
estimate
    ↓
within budget?
    ├─ yes
    │    ↓
    │ PreparedContext
    │
    └─ no
         ↓
choose ContextGroup range
         ↓
existing summary + new groups
         ↓
summarizer
         ↓
cancellation check
         ↓
validate summary
         ↓
build CompactionUpdate
         ↓
build projected ModelContext
         ↓
estimate again
         ↓
within target?
    ├─ yes
    │    ↓
    │ PreparedContext
    └─ no
         ↓
explicit failure
```

---

## 2.33 Compaction cancellation

如果：

```text
old Summary S1
↓
start summarizer for S2
↓
Run cancelled
```

必须：

```text
S1 remains current
S2 discarded
no partial Session update
no partial persistence
```

只有 summarizer 完成、结果有效、cancellation check 通过后，才返回 CompactionUpdate。

---

## 2.34 无法解决的 Context overflow

必须明确处理。

### Static overhead 已超过 input budget

```text
system
+
runtime prompt
+
skills
+
tool schemas
>
input_budget
```

失败：

```text
context_budget_exceeded
reason=static_overhead
```

---

### 单个不可分割 ContextGroup 太大

例如完整 ToolExchangeBlock 单独超过预算：

```text
context_budget_exceeded
reason=atomic_group_too_large
```

---

### Compaction 后仍超预算

一次合理 incremental compaction 后：

```text
summary
+
minimum retained tail
+
static overhead
>
input_budget
```

失败。

V1.2 不无限递归 summary-of-summary。

---

### Token 无法估算

失败：

```text
token_estimation_error
```

禁止默认继续。

---

# 3. RawToolResult、Workspace 与 Artifact Materialization

## 3.1 为什么需要 RawToolResult

V1.1 Tool return 主要是：

```text
ToolTextContent
ToolJsonContent
```

但 V1.2 MCP 可能产生：

```text
text
structured JSON
bytes
image
audio
resource
multiple blocks
```

如果 `ToolResultMaterializer` 只接受 canonical `ToolContent`，binary/resource 在成为 ToolContent 之前没有合法表示。

因此 V1.2 引入：

```text
RawToolResult
```

---

## 3.2 RawToolContent

建议：

```python
RawToolContent: TypeAlias = (
    ToolTextContent
    | ToolJsonContent
    | BinaryToolContent
    | ResourceToolContent
)
```

其中：

```python
@dataclass(frozen=True)
class BinaryToolContent:
    data: bytes

    media_type: str
```

```python
@dataclass(frozen=True)
class ResourceToolContent:
    uri: str

    data: bytes | None = None

    media_type: str | None = None
```

---

## 3.3 RawToolResult

定义：

```python
@dataclass(frozen=True)
class RawToolResult:
    content: tuple[RawToolContent, ...]
```

即使普通 native Tool 只有一个 text block，也统一为：

```text
tuple length = 1
```

这样可以天然支持 MCP multi-content result。

为了保持 V1.1 Tool source compatibility，ToolExecutor 必须接受两种返回值并立即归一化：

```text
legacy ToolContent
→ RawToolResult(content=(legacy_content,))

RawToolResult
→ 原样进入 materialization
```

V1.2 不要求已有 native Tool 全部修改 handler 返回类型。其他返回类型继续视为
`ToolContractError`。

---

## 3.4 多 Content Block 顺序

必须保持原始顺序：

```text
text
image
text
resource
```

不能：

```text
分类后重排
只取第一个文本
拼成无结构大字符串
```

最终 canonical Tool content 顺序必须可追踪到 RawToolResult 原顺序。

---

## 3.5 ArtifactReferenceContent

V1.2 只定义一个 canonical artifact reference 类型：

```python
@dataclass(frozen=True)
class ArtifactReferenceContent:
    uri: str

    media_type: str | None

    size: int

    digest: str

    preview: str | None = None
```

必须验证：URI 使用 `workspace://` scheme 且可规范化；`size >= 0`；digest 使用受支持
算法的规范小写十六进制格式；preview 有独立 hard size bound；media type（若存在）
满足 canonical MIME 规则。

不再定义：

```text
ToolArtifactContent
```

和：

```text
ArtifactReferenceContent
```

两套近似类型。

---

## 3.6 ArtifactReferenceContent 属于 MessageContent

扩展：

```python
MessageContent: TypeAlias = (
    TextContent
    | JsonContent
    | ImageContent
    | AudioContent
    | ArtifactReferenceContent
    | ...
)
```

它是 canonical message protocol 的正式组成部分。

---

## 3.7 ToolContent

Tool 的 canonical output 允许：

```python
ToolContent: TypeAlias = (
    ToolTextContent
    | ToolJsonContent
    | ArtifactReferenceContent
)
```

如果当前项目已直接复用 MessageContent，可进一步统一，但 V1.2 不要求重写整个内容层。

关键是：

```text
ArtifactReferenceContent
```

必须只有一套 canonical 类型。

---

## 3.8 ToolExecutionResult 多 block

V1.2 正式升级：

```python
@dataclass(frozen=True)
class ToolExecutionResult:
    call_id: str
    name: str

    content: tuple[ToolContent, ...] | None = None
    error: ToolErrorInfo | None = None
```

而不是：

```text
单个 ToolContent
```

这样：

```text
MCP multi-content
native multimodal tool
artifact + preview
```

都可以自然表示。

V1.1 单一结果迁移为：

```text
tuple length = 1
```

属于机械兼容升级。

必须保持 V1.1 的互斥不变量：`content` 与 `error` 恰好一个存在。成功的空结果使用
`content=()`，不能用 `None` 表示；失败结果必须使用 `content=None, error=...`。

---

## 3.9 result_message() 映射

必须正式规定：

```text
ToolTextContent
→ TextContent

ToolJsonContent
→ JsonContent
  或当前项目稳定 canonical JSON content

ArtifactReferenceContent
→ ArtifactReferenceContent
```

保持 tuple 顺序。

不得再默认：

```text
所有 ToolContent 都转换成 TextContent
```

---

## 3.10 Workspace

Workspace 是：

```text
Agent working storage
```

不是：

```text
Memory
Sandbox
Transcript
```

用于：

```text
large ToolResult
binary resource
images
logs
maps
reports
robot diagnostics
intermediate artifacts
```

---

## 3.11 Workspace Protocol

```python
class Workspace(Protocol):
    @property
    def durable(self) -> bool:
        ...

    async def read(
        self,
        path: str,
    ) -> bytes:
        ...

    async def write(
        self,
        path: str,
        data: bytes,
        *,
        media_type: str | None = None,
    ) -> WorkspaceEntry:
        ...

    async def stat(
        self,
        path: str,
    ) -> WorkspaceEntry:
        ...

    async def list(
        self,
        path: str = ".",
    ) -> Sequence[WorkspaceEntry]:
        ...

    async def delete(
        self,
        path: str,
    ) -> None:
        ...
```

`durable=True` 表示 `write()` 成功返回时，数据已经越过该 backend 声明的
crash-durability boundary，而不只是进入进程缓冲区。

Workspace backend 还必须提供内部 URI/path 转换规则：

```text
workspace://blobs/sha256/<digest> ↔ backend-relative blob path
workspace://files/<normalized path> ↔ backend-relative file path
```

URI authority、percent-encoding、空路径、重复分隔符和 `.`/`..` 必须规范化后再进入后端；
Agent 提供的 URI 不能直接作为宿主文件路径使用。该 resolver 可以保持 internal。

V1.2 不定义：

```text
shell
execute
patch
grep
sandbox
```

---

## 3.12 WorkspaceEntry

```python
@dataclass(frozen=True)
class WorkspaceEntry:
    path: str

    size: int

    media_type: str | None = None

    digest: str | None = None
```

---

## 3.13 默认 Workspace

提供：

```text
InMemoryWorkspace
LocalWorkspace
```

`InMemoryWorkspace`：

```text
tests
non-persistent task
short-lived Session
durable=False
```

`LocalWorkspace`：

```text
persistent local applications
robot host
developer workstation
durable=True
```

LocalWorkspace 的 durable write 至少使用：

```text
unique temp → flush/fsync → atomic replace → fsync parent → return
```

Persistent Session 若可能将 artifact reference 写入 transcript，默认必须拒绝
`durable=False` 的 Workspace。只有应用显式选择 non-durable artifact mode 时才允许该
组合，并接受恢复后的 `workspace_artifact_missing`。

---

## 3.14 Workspace path security

LocalWorkspace 必须防止：

```text
../
absolute path escape
symlink escape
root traversal
```

如果安全目标只是防止 Agent 提交路径逃逸，流程至少：

```text
normalize path
↓
resolve candidate
↓
resolve symlinks where appropriate
↓
verify candidate remains under root
```

上述 check-then-open 流程不能抵御本地进程在检查后替换 symlink 的 TOCTOU。如果 backend
声明能够抵御同机恶意并发修改，则必须使用 `dir_fd/openat`、`O_NOFOLLOW` 等逐段打开
策略；否则必须明确 threat model 只覆盖 Agent-controlled path，不覆盖恶意本地进程竞争。

失败：

```text
workspace_permission_error
```

---

## 3.15 ToolResultMaterializer

定义：

```python
class ToolResultMaterializer(Protocol):
    async def materialize(
        self,
        raw: RawToolResult,
        *,
        call: ToolCall,
        context: ToolContext,
        cancellation: CancellationToken,
    ) -> tuple[ToolContent, ...]:
        ...
```

默认：

```text
InlineToolResultMaterializer
```

V1.2 提供：

```text
WorkspaceToolResultMaterializer
```

---

## 3.16 ToolOutputLimits

现有单个：

```text
max_output_bytes
```

不能同时承担：

```text
绝对安全上限
+
inline context 上限
```

定义：

```python
@dataclass(frozen=True)
class ToolOutputLimits:
    max_raw_bytes: int

    max_inline_bytes: int
```

要求：

```text
0 < max_inline_bytes <= max_raw_bytes
```

---

## 3.17 Output limit 语义

```text
serialized raw size
<= max_inline_bytes
```

则：

```text
inline canonical ToolContent
```

如果：

```text
max_inline_bytes
<
raw size
<=
max_raw_bytes
```

则：

```text
materialize to Workspace
```

如果：

```text
raw size > max_raw_bytes
```

失败：

```text
tool_output_too_large
```

---

## 3.18 Binary / resource materialization

例如：

```text
BinaryToolContent
```

默认：

```text
Workspace.write()
↓
ArtifactReferenceContent
```

Resource 如果只有 URI 而没有可读取数据，不能伪装成 `ArtifactReferenceContent`，因为
后者要求可验证的 size 和 digest。V1.2 必须二选一：

```text
fetch resource bytes and materialize
or
return a bounded ToolTextContent containing the external URI
```

对于：

```text
MCP temporary resource
transport-scoped resource
embedded bytes
```

必须 materialize 成稳定 Workspace artifact。

---

## 3.19 Digest-addressed artifact

自动 materialization 推荐：

```text
workspace://blobs/sha256/<digest>
```

好处：

```text
stable identity
dedup
integrity checking
跨 prepare 不重复写
跨进程恢复可定位
```

`workspace://blobs/sha256/...` 对象一经创建必须 immutable。通用 `Workspace.delete()`
不能在不知道引用关系的情况下删除共享 digest blob。V1.2 不实现引用计数或 GC；
materialization 中途失败遗留的无引用 digest blob可以保留。

用户显式工作文件可以是：

```text
workspace://files/report.md
```

---

## 3.20 Tool invocation 与 materialization 的语义边界

完整流程固定：

```text
Tool invocation
    ↓
invocation outcome determined
    ↓
RawToolResult
    ↓
materialization
    ↓
ToolExecutionResult
```

ToolEffect 代表：

```text
现实 Tool invocation 是否发生/成功
```

Materialization 代表：

```text
Runtime 是否成功构造可提交结果
```

两者必须严格分离。

---

## 3.21 Materialization 失败后的 ToolEffect

如果 Tool invocation **正常返回**：

```text
physical execution
=
SUCCEEDED
```

即使：

```text
Workspace.write()
```

随后失败。

因此固定：

```text
ToolEffectRecord
=
SUCCEEDED
transcript_committed=False

ToolBatch
=
aborted with RunError.code=tool_materialization_error
```

对于 SIDE_EFFECTING Tool：

```text
retry_safe=False
```

这是硬语义。

Materialization failure 不作为一个可继续提交的普通 ToolExecutionResult 返回。否则
AgentLoop 会提交 error ToolResultMessage，并把该 effect 标记为
`transcript_committed=True`，与上述语义冲突。ToolExecutor 必须抛出携带该
`ToolEffectRecord` 的 `ToolBatchAborted`；整个尚未提交的 Assistant ToolCall exchange
保持未提交。`tool_output_too_large` 使用同一规则。

---

## 3.22 UNKNOWN 什么时候使用

只有 Tool invocation 本身无法判断效果时，例如：

```text
timeout
cancel after execution started
transport disconnect
remote MCP uncertain failure
generic exception after possible mutation
```

才使用：

```text
ToolEffectStatus.UNKNOWN
```

不得因为结果存储失败就把已确认成功的 invocation 降为 UNKNOWN。

---

## 3.23 ToolEffect success evidence

如果当前 `ToolEffectRecord.SUCCEEDED` 要求 content，则该 content 不能再解释为：

```text
最终 model-visible full result
```

应解释为：

```text
bounded execution evidence
```

例如：

```text
short preview
digest
result type
artifact metadata
```

必须有 hard size bound。

禁止把 10 MB 原始 ToolResult 塞入 ToolEffectRecord。

V1.2 保持 `ToolEffectRecord.content` 的单一 `ToolContent` 形态以减少兼容破坏。对于
multi-block 或超大 RawToolResult，ToolExecutor 在 materialization 前生成一个 bounded
`ToolJsonContent` evidence，至少包含：

```text
raw result digest
block count / block kinds
bounded preview（若可安全生成）
```

因此 materialization 即使失败，SUCCEEDED effect 仍有合法且有界的 content。最终
model-visible multi-content result 与 effect evidence 是两个不同对象。

---

## 3.24 Materialization 失败示例

```text
move_arm()
    ↓
robot reports success
    ↓
returns diagnostic JSON 500 KiB
    ↓
Workspace disk full
```

结果：

```text
ToolEffect:
    SUCCEEDED
    transcript_committed=False

Run:
    FAILED
    error=tool_materialization_error

Run retry_safe:
    False
```

这是 V1.2 固定行为。

---

## 3.25 Persistent Session 与 Workspace durability

如果 transcript 中存在：

```text
ArtifactReferenceContent(
    uri="workspace://..."
)
```

则该 artifact 对恢复后的 Session 仍可能被使用。

因此 Persistent Session 默认必须配：

```text
durable Workspace
```

例如：

```text
LocalWorkspace
```

默认不得配：

```text
InMemoryWorkspace
```

只有显式启用前述 non-durable artifact mode 时例外。

---

## 3.26 Missing artifact

恢复后 artifact 不存在：

```text
workspace_artifact_missing
```

canonical transcript 不修改。

不能：

```text
删除引用
替换成 ""
跳过 block
```

---

# 4. SessionSnapshot、Codec 与 Durability

## 4.1 SessionSnapshot

正式定义：

```python
@dataclass(frozen=True)
class SessionSnapshot:
    schema_version: int

    session_id: str

    revision: int

    last_pending_sequence: int

    messages: tuple[AgentMessage, ...]

    pending: tuple[PendingInput, ...]

    compaction: ContextSummary | None = None

    metadata: FrozenJsonObject = field(
        default_factory=FrozenJsonObject
    )
```

具体 frozen JSON 类型优先复用当前 RoboAgent 已有 JSON canonical 类型。

`schema_version >= 1`、`revision >= 0`、`last_pending_sequence >= 0`。Snapshot 中的 tuple、
JSON 和嵌套内容必须在构造时完成 defensive copy/freeze，不能依赖调用方之后不修改。

---

## 4.2 Snapshot 不保存 Runtime object

不得保存：

```text
Agent
Model
Tool instances
Hooks
Event subscribers

asyncio.Lock
thread locks

CancellationToken
active task
active_run_id

MCPClient
ApprovalProvider
Workspace object
```

这些由新的 Runtime environment 注入。

---

## 4.3 Runtime revision 与 durable revision

Session 内必须区分：

```python
_runtime_revision: int
_durable_revision: int | None
```

### `_runtime_revision`

表示：

```text
当前内存 Session state 的 revision
```

### `_durable_revision`

表示：

```text
最近一次 Repository 确认成功持久化的 revision
```

新建且从未持久化的 Session 使用 `None`。恢复的 Session 初始化为
`snapshot.revision`。二者可以不同。

---

## 4.4 为什么需要两个 revision

例如：

```text
runtime = 10
durable = 10

mutation
↓
runtime = 11

persist 11 fails
↓
durable = 10

next mutation
↓
runtime = 12
```

此时应该：

```text
save snapshot revision 12
expected_revision = 10
```

不是：

```text
expected_revision = 11
```

因为 Repository 从未拥有 11。

---

## 4.5 Session revision 增长点

以下变化增加 `_runtime_revision`：

```text
initial Run input committed

pending steer enqueued

pending follow_up enqueued

pending consumed into transcript

assistant final response committed

ToolExchange committed

compaction state updated

persistent metadata changed
```

一个 logical atomic mutation：

```text
revision += 1
```

不能一次 mutation 内随机增加多次。

---

## 4.6 Pending input sequence

`last_pending_sequence` 表示 V1.1 `_sequence` 已经分配给 steer/follow_up receipt 的最大值：

```text
pending control input 的全序关系
```

例如：

```text
steer sequence 10
follow_up sequence 11
next pending receipt sequence 12
```

Run initial message 在 V1.1 中没有 InputReceipt，也不占用该 sequence。它通过 Run 启动时
捕获的 pending-through boundary 保证“旧 pending 先于 initial input”。V1.2 保持这一
语义，不为了持久化额外改变 Session input protocol。

该值与 Session revision 无关。

---

## 4.7 Restore 后 sequence

恢复后：

```text
next_pending_sequence
=
last_pending_sequence + 1
```

不能回到 0。

必须继续保证：

```text
old pending input
before
newer Run initial input
```

---

## 4.8 SessionRepository

定义：

```python
class SessionRepository(Protocol):
    async def load(
        self,
        session_id: str,
    ) -> SessionSnapshot | None:
        ...

    async def save(
        self,
        snapshot: SessionSnapshot,
        *,
        expected_revision: int | None,
    ) -> int:
        ...

    async def delete(
        self,
        session_id: str,
        *,
        expected_revision: int,
    ) -> None:
        ...
```

成功返回：

```text
persisted revision
```

通常等于：

```text
snapshot.revision
```

`expected_revision=None` 表示调用方要求 repository 中该 Session 不存在，用于首次保存。
若记录已经存在则冲突。整数表示 update CAS。这样不会混淆“不存在”和“已存在且
revision=0”。

`delete()` 也必须在同一个 repository CAS/lock 边界内检查 revision；它是显式持久化
删除，不等同于关闭内存 Session。

---

## 4.9 CAS 语义

Repository 当前：

```text
revision = R
```

只有：

```text
expected_revision == R
```

时允许保存。

否则：

```text
SessionConflictError
```

不允许 last-write-wins。

对于不存在的记录，当前 revision 逻辑值是 `None`，只有
`expected_revision is None` 才允许首次创建。

新 Session 从 `_runtime_revision=0, _durable_revision=None` 开始。可以显式保存 revision
0 的空 Session，也可以等第一次 mutation 后直接以 revision 1 或更高版本首次保存；
两种情况都使用 `expected_revision=None`。从 repository restore 后，runtime 和 durable
revision 均初始化为 snapshot revision。

---

## 4.10 Revision 可以跳跃

Repository 不要求：

```text
new_revision == old_revision + 1
```

允许：

```text
10 → 12
```

因为 11 可能是：

```text
runtime 存在过
但未成功 durable
```

要求只需要：

```text
new_revision > current durable revision
```

且：

```text
expected_revision == current durable revision
```

---

## 4.11 Session-local persistence serialization

Session 可以使用：

```python
_persist_lock: asyncio.Lock
```

其职责只有：

```text
同一进程同一 Session 的 persistence operations 串行
```

它不保护：

```text
transcript mutation
pending queue
Run ownership
```

调用方不能在 mutation 后各自携带旧 snapshot 排队，并假设 asyncio lock 一定按 revision
顺序唤醒。正式算法是：

```text
mutation 完成
↓
acquire _persist_lock
↓
短暂 acquire runtime state locks
↓
capture 当前最新 immutable snapshot
↓
release runtime state locks
↓
save latest snapshot using current _durable_revision
↓
release _persist_lock
```

较新的 snapshot 可以包含并取代多个较旧 mutation。若实现仍允许传入 candidate snapshot，
则 `candidate.revision <= _durable_revision` 必须作为“已被更新 snapshot 覆盖”跳过，不能
反向写入或报告 CAS conflict。

---

## 4.12 Repository I/O 不持有 Runtime state lock

禁止：

```text
_transcript_lock held
↓
await repository.save()
```

正确：

```text
_persist_lock
↓
runtime lock
↓
capture latest immutable snapshot
↓
release runtime lock
↓
Repository CAS
↓
release _persist_lock
```

---

## 4.13 Persistence race

如果 snapshot 11 正在持久化期间 runtime 已经进入 12：

```text
save 11 success
↓
durable_revision = 11
```

然后必须允许：

```text
save latest 12
expected_revision = 11
```

实现可以：

```text
每次 mutation enqueue persistence
```

也可以：

```text
coalesce 到最新 snapshot
```

V1.2 不要求每个中间 revision 都写盘。

但最终 successful save 必须准确更新 durable revision。

任何等待 durability 的公开 API，只要成功保存的 snapshot revision 不小于该 API 自身
mutation revision，即可返回成功；不要求它自己的中间 snapshot 单独落盘。

---

## 4.14 Persistence failure 不 rollback

假设：

```text
ToolExchange runtime commit succeeded
revision 20 → 21
↓
save 21 failed
```

不得：

```text
rollback transcript
```

此时：

```text
runtime_revision = 21
durable_revision = 20
```

如果进程 crash，恢复只能得到 20。

这是明确接受的 durability 模型。

---

## 4.15 Run persistence failure

在启用 persistent Session 时，默认：

```text
required persistence boundary failure
→ Run FAILED
→ RunError.code = session_persistence_error
```

但已经发生的：

```text
canonical commit
physical ToolEffect
```

不得伪造回滚。

---

## 4.16 steer / follow_up persistence failure

如果：

```text
await session.follow_up(...)
```

已经成功写入内存 pending：

```text
runtime_revision++
```

随后持久化失败：

```text
SessionPersistenceError
```

返回给调用方。

但：

```text
pending 仍存在内存
```

不能自动删除。

---

## 4.17 SessionSnapshotCodec

Repository 不应自己理解复杂 Runtime dataclass。

正式增加内部协议：

```python
class SessionSnapshotCodec(Protocol):
    def encode(
        self,
        snapshot: SessionSnapshot,
    ) -> bytes:
        ...

    def decode(
        self,
        data: bytes,
    ) -> SessionSnapshot:
        ...
```

默认：

```text
JsonSessionSnapshotCodec
```

可以保持 internal，不必作为顶层 public API。

---

## 4.18 Codec type discriminator

所有 union 类型必须有稳定 discriminator。

例如：

```json
{
  "type": "user_message",
  "content": [...]
}
```

```json
{
  "type": "artifact_reference",
  "uri": "...",
  "digest": "..."
}
```

不得依赖：

```text
Python module/class name
```

作为 storage schema。

---

## 4.19 Enum 编码

枚举使用稳定 string：

```json
"effect_kind": "side_effecting"
```

禁止使用：

```text
0
1
2
```

ordinal。

---

## 4.20 时间编码

V1.1 canonical Message timestamp 是有限 `float` Unix seconds。V1.2 codec 保持该类型，
编码为能够 round-trip 回同一 Python float 的 JSON number（等价于 Python `repr(float)`
精度），并拒绝 NaN / Infinity。

不能先转换成固定微秒精度 RFC 3339 再恢复，否则可能破坏 transcript exact round-trip
和 source digest。未来若 canonical timestamp 类型升级为 datetime，再通过新的 snapshot
schema version 引入 RFC 3339。

---

## 4.21 Bytes 编码

如果 Snapshot 中某种 canonical content 允许 inline bytes：

```json
{
  "encoding": "base64",
  "data": "..."
}
```

必须设置 hard size limit。

大型 binary 应优先进入 Workspace，而不是 Snapshot JSON。

---

## 4.22 Canonical JSON

用于：

```text
snapshot
source_digest
approval arguments_digest
```

的 digest-oriented canonical JSON 必须统一：

```text
UTF-8
stable field names
sorted object keys
stable separators
reject NaN
reject Infinity
```

避免不同路径产生不同 digest。

这不改变 V1.1 `FrozenJsonObject` 保留 insertion order、provider/display serialization
保留原顺序的语义。实现应增加专用于 digest/storage canonicalization 的编码入口，而不是
静默改变现有 `canonical_json_dumps()` 的可见输出。decode 后对象仍恢复原有 canonical
字段顺序；需要保序的 FrozenJsonObject 应编码为稳定 ordered entries，而不能只依赖
sorted object keys 回推原顺序。

---

## 4.23 CanonicalMessageCodec

建议将消息 canonical 编码逻辑抽成内部组件：

```text
AgentMessage
↓
CanonicalMessageCodec
↓
canonical bytes
```

用于：

```text
SessionSnapshotCodec
ContextSummary.source_digest
```

避免两套 serialization semantics。

---

## 4.24 Unknown codec type

decode 遇到未知：

```text
message type
content type
enum
schema version
```

必须：

```text
session_version_unsupported
或
session_corrupted
```

不能：

```text
忽略未知字段对应的核心内容
drop unsupported message
```

---

## 4.25 Session.restore

推荐：

```python
snapshot = await repository.load(session_id)

session = Session.restore(
    agent=agent,
    snapshot=snapshot,
    repository=repository,
    workspace=workspace,
)
```

Repository 只负责 data。

Session 负责构造 runtime state。

---

## 4.26 Restore validation

必须执行：

```text
schema_version
session_id
TranscriptValidator
pending validation
sequence validation
compaction validation
artifact structure validation
```

---

## 4.27 Pending validation

要求：

```text
pending[i].session_id
==
snapshot.session_id
```

```text
pending.sequence
strictly increasing
```

```text
no duplicate sequence
```

```text
last_pending_sequence
>=
max(pending.sequence)
```

---

## 4.28 active Run

恢复后强制：

```text
active_run_id = None
```

不恢复 old Run ownership。

---

## 4.29 Compaction restore

如果：

```text
summary source_digest mismatch
```

不认为整个 Session corrupted。

处理：

```text
restore canonical transcript
discard summary
current_compaction = None
```

下一次 ContextManager 可以重新生成。

---

## 4.30 LocalSessionRepository

Local backend 必须同时解决：

```text
crash-safe write
+
cross-process CAS
```

只使用：

```text
os.replace()
```

不足以实现 CAS。

---

## 4.31 Local CAS 使用 per-session lock file

推荐：

```text
sessions/
├── <session-id>.json
└── <session-id>.lock
```

保存流程：

```text
open lock file
↓
flock(EXCLUSIVE)
↓
read current durable snapshot/revision
↓
compare expected_revision
↓
encode new snapshot
↓
write unique temp file
↓
flush
↓
fsync temp
↓
os.replace(temp, target)
↓
fsync parent directory
↓
release flock
```

首次 save 使用相同 lock，确认 target 不存在后才写入。`delete(expected_revision=R)` 也必须
获取相同 per-session lock，读取并比较 R，再删除 target 并 fsync parent。这样 save、首次
create 和 delete 共享一个线性化点。

---

## 4.32 临时文件必须唯一

禁止：

```text
session.tmp
```

多个 writer 共用。

推荐：

```text
.<session-id>.<pid>.<uuid>.tmp
```

并保证 temp file 与 target 位于同一 filesystem，以维持 rename atomicity。

---

## 4.33 Local backend 范围

V1.2 LocalSessionRepository 主要面向：

```text
Linux / POSIX
```

使用 advisory `flock`。

其他平台可以：

```text
unsupported
```

或由未来 backend 实现。

不要为了跨平台抽象牺牲本地 CAS 正确性。

---

# 5. MCP 与 Tool 适配

## 5.1 MCP 定位

MCP 是：

```text
Tool discovery + transport + invocation source
```

不是新的 Runtime。

---

## 5.2 MCP lifecycle

固定：

```text
connect MCP
↓
discover tools
↓
adapt
↓
register into ToolRegistry
↓
construct Agent
↓
registry sealed
```

Agent 运行期间不支持：

```text
dynamic add/remove MCP tools
```

Tool topology 变化需要重新构造 Agent。

---

## 5.3 MCPClient

```python
class MCPClient(Protocol):
    async def connect(self) -> None:
        ...

    async def list_tools(
        self,
    ) -> Sequence[MCPToolDefinition]:
        ...

    async def call_tool(
        self,
        name: str,
        arguments: FrozenJsonObject,
        cancellation: CancellationToken,
    ) -> MCPToolResult:
        ...

    async def close(self) -> None:
        ...
```

Client 必须在 cancellation 后停止本地等待并尽力发送 transport/protocol cancellation；
协议不支持或无法确认远端停止时，SIDE_EFFECTING 调用继续按 `UNKNOWN` 处理，不能因为
本地 coroutine 已取消就推断远端未执行。

---

## 5.4 MCP client ownership

Agent 不拥有 MCP connection lifecycle。

推荐：

```python
async with MCPServer(...) as server:
    tools = await server.tools()

    registry = ToolRegistry(...)
    ...

    agent = Agent(
        model=model,
        tool_registry=registry,
    )
```

应用负责 MCP server 生命周期。

---

## 5.5 MCPToolAdapter

```text
MCPToolDefinition
↓
MCPToolAdapter
↓
RoboAgent Tool
```

ToolExecutor 不需要知道 Tool 来自 MCP。

---

## 5.6 MCP Tool effect trust model

默认：

```text
MCP Tool
=
SIDE_EFFECTING
```

只有：

```text
trusted local configuration
```

可以声明 READ_ONLY。

---

## 5.7 Remote metadata 只能升级风险

风险顺序：

```text
READ_ONLY
<
SIDE_EFFECTING
```

如果：

```text
local=READ_ONLY
remote=SIDE_EFFECTING
```

最终：

```text
SIDE_EFFECTING
```

如果：

```text
local=SIDE_EFFECTING
remote=READ_ONLY
```

仍：

```text
SIDE_EFFECTING
```

如果：

```text
only remote says READ_ONLY
```

默认仍：

```text
SIDE_EFFECTING
```

Remote MCP server 不能自行降低本地安全分类。

---

## 5.8 MCP Tool result

MCP adapter 必须把结果转换为：

```text
RawToolResult
```

而不是直接绕过 materializer 生成 ToolResultMessage。

---

## 5.9 MCP text

```text
MCP text block
→ ToolTextContent
→ RawToolResult block
```

---

## 5.10 MCP structured content

JSON-compatible：

```text
→ ToolJsonContent
```

保持原 block 顺序。

---

## 5.11 MCP binary/image/audio

转换：

```text
BinaryToolContent
```

然后走：

```text
ToolResultMaterializer
→ Workspace
→ ArtifactReferenceContent
```

MCP adapter 不自己创建第二套 artifact lifecycle。

---

## 5.12 MCP resource

如果 resource 包含数据：

```text
ResourceToolContent
→ materializer
```

如果只是 remote URI 且生命周期无法保证：

```text
必须 materialize 或显式失败
```

不能把 transport-scoped URI 直接永久写进 canonical transcript。

---

## 5.13 MCP multi-content

MCP：

```text
text
image
text
resource
```

最终：

```text
RawToolResult.content
```

保持：

```text
1 → 2 → 3 → 4
```

materialize 后的 canonical ToolContent 也保持对应顺序。

---

## 5.14 MCP isError

不能简单解释为：

```text
physical Tool failed
```

### READ_ONLY Tool

```text
isError=true
→ FAILED
```

### SIDE_EFFECTING Tool

默认：

```text
isError=true
→ UNKNOWN
```

除非 trusted local adapter 能明确证明：

```text
operation definitely did not occur
```

才可以：

```text
FAILED
```

---

## 5.15 MCP transport disconnect

Tool call 已开始后断线：

### READ_ONLY

```text
FAILED
```

### SIDE_EFFECTING

```text
UNKNOWN
```

因为远端操作可能已经执行。

---

# 6. Tool Policy、Approval 与 Batch Execution

## 6.1 ToolDecision

保留 V1.1：

```text
ALLOW
REJECT
FAIL_RUN
```

增加：

```text
REQUIRE_APPROVAL
```

定义：

```python
class ToolDecision(Enum):
    ALLOW = "allow"

    REJECT = "reject"

    FAIL_RUN = "fail_run"

    REQUIRE_APPROVAL = "require_approval"
```

---

## 6.2 ToolPolicyDecision

Policy 返回结构化结果：

```python
@dataclass(frozen=True)
class ToolPolicyDecision:
    action: ToolDecision

    reason: str | None = None
```

而不是单独 enum。

为保持第三方 V1.1 Policy source compatibility，V1.2 ToolExecutor 接受：

```python
ToolDecision | ToolPolicyDecision
```

收到 legacy `ToolDecision` 时立即归一化为
`ToolPolicyDecision(action=decision)`。新的 Policy 应返回结构化类型；旧 enum 返回形式
至少保留整个 V1.x。

---

## 6.3 Policy 执行顺序

保持 V1.1：

```text
Tool lookup
↓
Policy
↓
argument validation
↓
Approval if required
↓
before_tool
↓
Tool execution
↓
materialization
↓
after_tool
```

Policy 仍在 validation 前，以允许：

```text
unknown Tool
```

进入 security policy。

Approval 必须在 validation 后。

---

## 6.4 ApprovalRequest

定义：

```python
@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str

    run_id: str

    session_id: str

    tool_call_id: str

    tool_name: str

    arguments: FrozenJsonObject

    arguments_digest: str

    reason: str | None = None
```

---

## 6.5 Immutable arguments

Approval 使用：

```text
FrozenJsonObject
```

而不是普通 mutable Mapping。

Approval request 一旦创建，参数不得发生变化。

---

## 6.6 arguments_digest

使用统一 canonical JSON 编码计算：

```text
sha256(
    canonical_json(arguments)
)
```

用于：

```text
UI
remote approval
cross-process response validation
```

Approval response 必须与：

```text
approval_id
arguments_digest
```

匹配。

---

## 6.7 ApprovalDecision 与 ApprovalResponse

```python
class ApprovalDecision(Enum):
    APPROVE = "approve"

    REJECT = "reject"
```

Provider 必须返回带绑定信息的响应，而不是只返回裸 enum：

```python
@dataclass(frozen=True)
class ApprovalResponse:
    approval_id: str
    arguments_digest: str
    decision: ApprovalDecision
```

ToolExecutor 必须比较 response 与原 request 的 `approval_id`、`arguments_digest`；不匹配
时产生 `approval_mismatch`，Tool 不启动，也不产生 ToolEffect。

---

## 6.8 ApprovalProvider

```python
class ApprovalProvider(Protocol):
    async def request(
        self,
        request: ApprovalRequest,
        cancellation: CancellationToken,
    ) -> ApprovalResponse:
        ...
```

不传：

```text
timeout
```

给 Provider。

Timeout 由 ToolExecutor 强制。

---

## 6.9 ApprovalSettings

```python
@dataclass(frozen=True)
class ApprovalSettings:
    timeout: float | None = None
```

ToolExecutor 使用自己的 timeout/cancellation primitive 包围：

```text
ApprovalProvider.request()
```

不能依赖 Provider 自律。

---

## 6.10 三种 timeout

明确分离：

### Run timeout

覆盖：

```text
Context
Model
Policy
Validation
Approval
Tool execution
```

### Approval timeout

只覆盖：

```text
等待人类批准
```

### Tool execution timeout

只有 Tool 真正启动后才开始。

Approval 等待时间不消耗 Tool execution timeout。

---

## 6.11 Approval cancellation

Run cancelled while waiting：

```text
ApprovalProvider request cancelled
Tool never starts
```

结果：

```text
no ToolEffectRecord
```

---

## 6.12 Approval reject

```text
Tool never starts
```

模型可以获得：

```text
tool_rejected
```

结果。

但：

```text
RunResult.effects
```

不增加 ToolEffectRecord。

---

## 6.13 Approval timeout

同样：

```text
Tool never started
```

因此：

```text
no ToolEffectRecord
```

这是硬不变量。

---

## 6.14 不统一修改所有 ToolBatch 为两阶段

V1.2 必须保持 V1.1 SERIAL execution semantics。

不能把所有 batch 都改成：

```text
prepare all
↓
execute all
```

因为这会改变 policy 观察状态和 FAIL_RUN 行为。

---

## 6.15 SERIAL batch

维持：

```text
call 1
  lookup
  policy
  validate
  approval
  execute
  materialize

call 2
  lookup
  policy
  validate
  approval
  execute
  materialize
```

即：

```text
prepare call N
紧邻
execute call N
```

因此后续 Policy 可以观察前一个 SERIAL Tool 的现实/runtime 状态变化。

---

## 6.16 Mixed batch

保持当前 V1.1 scheduling semantics。

V1.2 不借 Approval 机会重新定义 mixed concurrency。

所有 call 在现有 scheduler 确定的执行边界上：

```text
per-call prepare
↓
approval if needed
↓
execute according to existing schedule
```

---

## 6.17 全 CONCURRENT batch

只有：

```text
整个可执行 batch
满足当前 V1.1 全并发条件
```

时使用：

```text
ordered preparation
↓
bounded concurrent execution
```

Preparation 按模型 ToolCall 原始顺序：

```text
call 1 policy/validation/approval
call 2 policy/validation/approval
call 3 policy/validation/approval
```

全部 preparation 成功后：

```text
approved executable calls
↓
bounded concurrent execution
```

---

## 6.18 为什么 concurrent approval 顺序固定

避免：

```text
同时弹 3 个机器人动作审批
```

并保证：

```text
approval presentation order
=
model ToolCall order
```

提高：

```text
可理解性
审计稳定性
测试确定性
```

---

## 6.19 FAIL_RUN — SERIAL

例如：

```text
call1
→ execute success

call2
→ policy FAIL_RUN
```

结果：

```text
call1 effect remains
Run fails at call2
```

不回滚 call1。

这是 V1.1 语义。

---

## 6.20 FAIL_RUN — all CONCURRENT

如果 preparation：

```text
call1 → ALLOW
call2 → FAIL_RUN
```

由于 concurrent batch 尚未启动：

```text
整个尚未启动 batch 不执行
```

这是允许的，因为全 concurrent batch 的 preparation 本身就是 batch start barrier。

---

## 6.21 REJECT

REJECT：

```text
产生正常 Tool rejection result
```

不自动停止 unrelated calls。

SERIAL / concurrent 都保持 V1.1 当前 rejection semantics。

---

# 7. Persistence、Context、Tool 与 Runtime 集成

## 7.1 Context 流程

```text
Session
  │
  ├─ transcript
  ├─ current_compaction
  └─ revision
        ↓
capture state
        ↓
ContextRequest
        ↓
ContextManager.prepare()
        ↓
PreparedContext
       /              \
ModelContext     CompactionUpdate
       ↓              ↓
merge usage    Session.commit_compaction
                      ↓
            Session.commit_compaction
                      ↓
               runtime_revision++
                      ↓
                 persist CAS
                      ↓
                Model request
```

---

## 7.2 Tool 流程

```text
ToolCall
   ↓
Policy
   ↓
Validation
   ↓
Approval if required
   ↓
Tool invocation
   ↓
ToolEffect evidence fixed
   ↓
RawToolResult
   ↓
ToolResultMaterializer
   ↓
ToolContent[]
   ↓
ToolExecutionResult
   ↓
ToolBatchResult
   ↓
ToolResultMessage
   ↓
ToolExchange atomic commit
   ↓
effect.transcript_committed=True
```

---

## 7.3 Tool materialization error

```text
Tool invocation succeeded
↓
materialization failed
```

则：

```text
effect status remains SUCCEEDED
transcript_committed=False
ToolExecutor aborts batch with tool_materialization_error
```

AgentLoop 不得把 physical success 改成 UNKNOWN。

---

## 7.4 Persistence 流程

```text
runtime mutation
    ↓
_runtime_revision++
    ↓
capture SessionSnapshot
    ↓
release Session state locks
    ↓
_persist_lock
    ↓
Repository.save(
    expected_revision=_durable_revision
)
    ↓
success
    ↓
_durable_revision = snapshot.revision
```

---

## 7.5 Persistence failed

```text
runtime state remains
durable_revision unchanged
```

后续新的 snapshot 可直接从：

```text
old durable revision
```

CAS 保存最新 runtime revision。

---

## 7.6 Required persistence boundaries

至少以下稳定边界必须触发 durability：

```text
pending input accepted

Run initial user input committed

pending consumed

Assistant final committed

ToolExchange committed

compaction committed

Run terminal state if session metadata changed
```

具体可以 coalesce，但对外返回成功前需要满足 API 的 durability contract。

---

## 7.7 Durable API contract

对于显式启用 persistent Session：

```text
await session.steer()
await session.follow_up()
```

如果返回成功，应表示：

```text
input accepted in runtime
+
required persistence completed
```

如果 persistence 失败：

```text
raise SessionPersistenceError
```

即使内存 state 已经变化。

---

## 7.8 Run terminal 与 persistence

Run 因 persistence failure FAILED，不代表：

```text
之前的 ToolEffect 被撤销
之前的 transcript commit 被撤销
```

RunResult 必须反映：

```text
真实已发生 effect
+
persistence error
```

---

## 7.9 Usage

最终：

```text
RunResult.usage
=
sum(Context preparation usage_delta)
+
sum(main model response usage)
```

Workspace I/O：

```text
不计 token
```

MCP server 内部自己调用 LLM：

```text
不计 RoboAgent Run usage
```

除非未来 MCP 协议显式暴露可聚合 usage，V1.2 不处理。

---

## 7.10 Events

V1.2 只增加必要 observation events。

沿用 V1.1 `AgentEvent.type` 的小写点分字符串风格，不新增一套 PascalCase event class。

### Context

```text
context.compaction_completed
context.compaction_failed
```

ContextManager 是纯计算协议，AgentLoop 只能在看到 `CompactionUpdate` 后可靠发出 completed，
或在捕获明确 compaction error 后发出 failed。V1.2 不为了一个 started 事件向
ContextManager 注入 mutable Run event bus。

### Persistence

```text
session.persisted
session.persistence_failed
```

### Approval

```text
approval.requested
approval.resolved
```

### MCP lifecycle

```text
mcp.connected
mcp.disconnected
```

Event payload 必须保持 JSON-safe，并对 Approval arguments、MCP credentials、artifact
preview 等潜在敏感内容执行省略或有界脱敏。

普通 Workspace read/write 不进入 core EventBus。

---

## 7.11 Error taxonomy

### Context

```text
context_budget_unavailable
context_budget_invalid
context_budget_exceeded
context_compaction_error
token_estimation_error
```

### Workspace / materialization

```text
workspace_error
workspace_not_found
workspace_permission_error
workspace_artifact_missing
tool_output_too_large
tool_materialization_error
```

### Persistence

```text
session_persistence_error
session_conflict
session_corrupted
session_version_unsupported
```

### MCP

```text
mcp_connection_error
mcp_protocol_error
mcp_tool_error
```

### Approval

```text
approval_rejected
approval_timeout
approval_error
approval_mismatch
```

---

# 8. API、实施顺序与验收标准

## 8.1 Runtime composition root

V1.2 必须明确依赖由谁拥有，不能要求应用构造一个随后又被 Run 忽略的 ToolExecutor。
固定归属如下：

```text
Agent（可跨 Session 共享）
├─ Model / ToolRegistry / ContextManager / ToolPolicy / Hooks
├─ ApprovalProvider
└─ ApprovalSettings

Session（每个任务实例）
├─ SessionRepository
├─ Workspace
└─ ToolResultMaterializer

Run
└─ 使用 Agent + Session 的上述依赖构造 ToolExecutor
```

推荐构造形式：

```python
agent = Agent(
    model=model,
    tool_registry=registry,
    context_manager=context_manager,
    approval_provider=approval_provider,
    approval_settings=approval_settings,
)

session = Session(
    agent=agent,
    repository=repository,
    workspace=workspace,
    result_materializer=WorkspaceToolResultMaterializer(
        workspace=workspace,
        limits=limits,
    ),
)
```

`Run._execute()` 构造 ToolExecutor 时必须显式传入
`session.result_materializer`、`agent.approval_provider` 和 `agent.approval_settings`。

Workspace 属于 Session，使同一个 Agent 可以服务多个相互隔离的 Workspace。若
WorkspaceToolResultMaterializer 绑定的不是该 Session 的 Workspace，Session 构造必须
拒绝。ContextManager 默认只投影 artifact metadata，不持有 Workspace；需要读取 artifact
的自定义 manager 必须使用同一 Workspace 的只读 resolver。

恢复时重新注入 repository、Workspace、materializer 和其他 Runtime services；这些对象
不进入 SessionSnapshot。

---

## 8.2 推荐 Public API

### Context

```text
ContextRequest
PreparedContext
CompactionUpdate

ContextBudget
TokenEstimate
TokenEstimator

ContextSummary
CompactionPolicy
CompactingContextManager

ModelContextSegment
MessageSegment
SummarySegment
WorkspaceReferenceSegment
```

---

### Tool / Workspace

```text
RawToolResult
RawToolContent
BinaryToolContent
ResourceToolContent

ArtifactReferenceContent

ToolOutputLimits

ToolResultMaterializer
InlineToolResultMaterializer
WorkspaceToolResultMaterializer

Workspace
WorkspaceEntry
InMemoryWorkspace
LocalWorkspace
```

部分 Raw 类型可以保持内部，如果 MCP 是可选 extra；但语义必须正式定义。

---

### Persistence

```text
SessionSnapshot
SessionRepository

InMemorySessionRepository
LocalSessionRepository
```

`SessionSnapshotCodec`、`CanonicalMessageCodec` 可以保持 internal，但必须有稳定规格。

---

### MCP

```text
MCPClient
MCPServer
MCPToolAdapter
MCPToolPolicy
```

---

### Approval

```text
ToolPolicyDecision

ApprovalRequest
ApprovalDecision
ApprovalResponse
ApprovalSettings
ApprovalProvider
```

---

## 8.3 推荐目录

尽量沿当前结构小幅扩展：

```text
roboagent/
├── agent/
├── context/
│   ├── manager.py
│   ├── budget.py
│   ├── compaction.py
│   └── estimator.py
│
├── model/
│
├── runtime/
│
├── tool/
│   ├── tool.py
│   ├── executor.py
│   ├── materializer.py
│   └── approval.py
│
├── skill/
│
├── workspace/
│   ├── workspace.py
│   ├── memory.py
│   └── local.py
│
├── persistence/
│   ├── session.py
│   ├── codec.py
│   ├── memory.py
│   └── local.py
│
└── mcp/
    ├── client.py
    ├── adapter.py
    └── transport.py
```

不增加：

```text
ContextManagerV12
LongHorizonRuntime
WorkspaceManager
PersistenceManager
MCPManager
ApprovalManager
```

---

## 8.4 V1.2a 实施顺序

### Phase A1 — Context protocol

只实现：

```text
ContextRequest
PreparedContext
CompactionUpdate
ModelContextSegment
provider mapping
```

目标：

```text
先解决 derived context 如何合法进入模型请求。
```

这一阶段不做自动 compaction。

---

### Phase A2 — Budget & Compaction

实现：

```text
ContextBudget
ModelCapabilities.context_window
TokenEstimator
ContextSummary
CompactionPolicy
incremental compaction
summary digest
compaction commit
```

完成后先做 semantic tests。

---

### Phase A3 — Tool materialization & Workspace

实现：

```text
RawToolResult
multi-content ToolExecutionResult
ArtifactReferenceContent
ToolOutputLimits
ToolResultMaterializer
Workspace
LocalWorkspace
```

重点先保证 ToolEffect semantics 不回归。

---

### Phase A4 — Persistence

实现：

```text
SessionSnapshot
runtime_revision
durable_revision
last_pending_sequence

Snapshot codec

SessionRepository
CAS

LocalSessionRepository + flock
Session.restore
```

---

### Phase A5 — Integration acceptance

只：

```text
修语义测试
补文档
跑完整 suite
```

不再增加 V1.2a 功能。

然后：

```text
FREEZE V1.2a
```

---

## 8.5 V1.2b 实施顺序

### Phase B1 — MCP

实现：

```text
MCP client
discovery
adapter
RawToolResult mapping
effect trust
connection lifecycle
```

---

### Phase B2 — Approval

实现：

```text
ToolPolicyDecision
REQUIRE_APPROVAL
ApprovalRequest
ApprovalProvider
Approval timeout

SERIAL execution semantics
all-CONCURRENT preparation semantics
```

---

### Phase B3 — Security & Observability

补：

```text
MCP trust tests
approval mismatch
events
error taxonomy
documentation
```

然后：

```text
FREEZE V1.2
```

---

## 8.6 V1.2a 编码前 P0 Gate

V1.2a 不允许开始大规模编码，直到以下协议在文档中冻结：

```text
[ ] ContextRequest exact fields

[ ] PreparedContext exact semantics

[ ] CompactionUpdate commit ordering

[ ] ModelContextSegment closed union

[ ] Summary provider privilege semantics

[ ] Incremental compaction algorithm

[ ] Summary source digest algorithm

[ ] Retained-tail rules

[ ] RawToolResult representation

[ ] ToolExecutionResult multi-content representation

[ ] ArtifactReferenceContent single canonical type

[ ] ToolResultMaterializer lifecycle

[ ] Materialization failure effect semantics

[ ] ToolEffect success evidence semantics

[ ] runtime_revision semantics

[ ] durable_revision semantics

[ ] last_pending_sequence restore semantics

[ ] SessionSnapshot codec format

[ ] LocalRepository flock/CAS algorithm
```

本文已经对上述项目给出规范。

---

## 8.7 V1.2b 编码前 P0 Gate

```text
[ ] MCP discovery occurs before Agent construction

[ ] Remote metadata cannot lower effect risk

[ ] MCP RawToolResult mapping

[ ] MCP multi-content ordering

[ ] MCP error / disconnect effect semantics

[ ] ToolDecision backward compatibility

[ ] ApprovalRequest immutable canonical arguments

[ ] Approval arguments_digest

[ ] ApprovalResponse identity validation

[ ] Approval timeout enforced by ToolExecutor

[ ] SERIAL batch keeps V1.1 semantics

[ ] mixed batch keeps V1.1 semantics

[ ] all-CONCURRENT ordered preparation semantics

[ ] FAIL_RUN semantics for serial/concurrent cases
```

---

## 8.8 Context semantic tests

至少：

```text
ContextRequest receives ModelSettings

ContextRequest receives ModelCapabilities

ContextManager cannot mutate Session directly

no compaction below threshold

automatic compaction above threshold

summary committed before corresponding model request

summary source digest match

summary digest mismatch rejected

summary format mismatch rejected

incremental compaction uses old summary + new groups

source digest still covers original canonical range

ToolExchangeBlock never split

minimum recent user turn retained

static overhead exceeds budget

single atomic group exceeds budget

summary + minimum tail still exceeds budget

unestimable modality

configured max > model hard context

max_output_tokens affects reserve

compaction cancellation produces no update

compaction failure preserves previous summary

invalid current summary below threshold returns a clear CompactionUpdate

compaction usage appears in RunResult.usage

SummarySegment never mapped as system authority
```

---

## 8.9 Tool / Workspace tests

```text
single text RawToolResult

multiple RawToolResult blocks preserve order

small result remains inline

large text result becomes artifact

binary result becomes artifact

resource result becomes artifact

max_raw_bytes enforced

max_inline_bytes triggers materialization

same blob produces stable digest URI

materialization occurs once only

ArtifactReferenceContent survives result_message mapping

provider sees artifact projection

path traversal rejected

absolute escape rejected

symlink escape rejected

artifact digest mismatch

missing artifact explicit failure

persistent artifact readable after restore

durable Workspace write survives crash boundary

digest blob is immutable and generic delete cannot remove it
```

---

## 8.10 Materialization / effect tests

```text
READ_ONLY tool success + materialization failure
→ effect SUCCEEDED

SIDE_EFFECTING tool success + materialization failure
→ effect SUCCEEDED

SIDE_EFFECTING materialization failure
→ transcript_committed False

SIDE_EFFECTING materialization failure
→ retry_safe False

tool timeout before known completion
→ existing V1.1 semantics unchanged

tool exception with unknown side effect
→ UNKNOWN

materialization failure never changes known physical success to UNKNOWN
```

---

## 8.11 Persistence tests

```text
snapshot round-trip

all canonical message types round-trip

ArtifactReferenceContent round-trip

enum stable encoding

datetime stable encoding

bytes size limit

unknown type rejected

unsupported schema rejected

canonical message digest stable

summary digest stable after round-trip

pending restored

last_pending_sequence restored

new input sequence remains monotonic

duplicate pending sequence rejected

out-of-order pending rejected

wrong pending session_id rejected

active_run_id not restored

compaction valid restore

compaction digest mismatch discarded

runtime_revision advances

durable_revision only advances on save success

revision 11 save failure
then revision 12 saves with expected durable 10

stale CAS rejected

first save uses expected_revision None

second create of same session conflicts

delete checks expected revision under the same lock

two processes race same session

only one writer succeeds

unique temp files

atomic replace leaves valid file

persist failure does not rollback runtime transcript

follow_up accepted but persist failed

steer accepted but persist failed

restore after failed persistence returns last durable state

out-of-order local persistence waiters cannot write an older snapshot

coalesced save satisfies every included mutation durability waiter
```

---

## 8.12 MCP tests

```text
connect
discover
close

tool mapping

tool registry sealed after Agent creation

text block mapping

JSON mapping

binary mapping

resource mapping

multi-content ordering

unknown content type

MCP READ_ONLY isError → FAILED

MCP SIDE_EFFECTING isError → UNKNOWN

READ_ONLY disconnect → FAILED

SIDE_EFFECTING disconnect → UNKNOWN

remote metadata READ_ONLY cannot lower default risk

local READ_ONLY + remote SIDE_EFFECTING
→ SIDE_EFFECTING

MCP tool passes Policy

MCP tool passes Approval

MCP tool passes Hooks

MCP tool uses normal ToolResultMaterializer
```

---

## 8.13 Approval tests

```text
ALLOW

REJECT

FAIL_RUN

REQUIRE_APPROVAL + approve

REQUIRE_APPROVAL + reject

ApprovalProvider exception

ApprovalProvider invalid result

ApprovalResponse approval_id mismatch

ApprovalResponse arguments_digest mismatch

approval timeout

Run timeout during approval

Run cancel during approval

approval timeout produces no ToolEffect

approval reject produces no ToolEffect

approval request contains FrozenJsonObject

approval request contains session_id

arguments digest stable

approval digest mismatch rejected

parameter mutation cannot reuse approval

SERIAL:
call1 executes before call2 policy

SERIAL:
call1 executes even if later call2 FAIL_RUN

mixed batch preserves V1.1 schedule

all-CONCURRENT:
approval requested in ToolCall order

all-CONCURRENT:
FAIL_RUN prevents batch start

all-CONCURRENT:
approved calls execute bounded concurrently

model-visible ToolResult order remains original call order
```

---

## 8.14 V1.2a 完成标准

V1.2a 只有全部满足才算完成：

```text
1. ContextManager 使用 ContextRequest。

2. PreparedContext 能返回 usage_delta 与 compaction_update。

3. ContextManager 不直接修改 Session。

4. 新 summary 在对应模型请求前完成 Session commit。

5. ModelContext 使用封闭 ModelContextSegment union。

6. Summary 不获得 system privilege。

7. Context budget 覆盖完整模型请求。

8. Incremental compaction 不重新展开完整旧 transcript。

9. ToolExchangeBlock 不被切断。

10. Summary 有稳定 source digest 和 format version。

11. Compaction durable owner 只有 SessionSnapshot。

12. RawToolResult 支持多 content block。

13. ArtifactReferenceContent 是唯一 canonical artifact reference。

14. Large ToolResult 在 ToolExchange commit 前 materialize。

15. Tool raw 与 inline output limit 分离。

16. Materialization failure 不改变已知 physical success。

17. Session 同时维护 runtime_revision 和 durable_revision。

18. pending sequence 可恢复并继续单调递增。

19. Repository 使用 CAS 检测 stale writer。

20. LocalSessionRepository 使用跨进程 flock + unique temp + atomic replace。

21. Snapshot 使用稳定 codec。

22. Persistent restore 不恢复 active Run。

23. 全部 V1.1 semantic tests 继续通过。

24. 全部 V1.2a semantic tests 通过。
```

---

## 8.15 V1.2b 完成标准

```text
1. MCP Tool 全部适配成普通 RoboAgent Tool。

2. MCP 不新增执行器。

3. MCP discovery 在 Agent seal registry 前完成。

4. MCP remote metadata 无法自行降低 effect risk。

5. MCP binary/resource 使用统一 RawToolResult/materializer。

6. MCP multi-content 顺序稳定。

7. MCP side-effect uncertainty 沿用 V1.1 ToolEffect 语义。

8. ToolDecision 保留 ALLOW / REJECT / FAIL_RUN 并增加 REQUIRE_APPROVAL。

9. ToolPolicyDecision 可以携带 reason。

10. Approval 使用不可变 canonical arguments。

11. Approval 与 exact arguments digest 绑定。

12. Approval timeout 由 ToolExecutor 强制。

13. Approval reject/timeout/cancel 不产生 ToolEffect。

14. SERIAL ToolBatch 保持 V1.1 prepare→execute 顺序。

15. mixed ToolBatch 保持 V1.1 scheduling semantics。

16. 只有 all-CONCURRENT batch 使用 ordered preparation barrier。

17. all-CONCURRENT 获批后继续 bounded concurrent execution。

18. FAIL_RUN 对 serial 与 concurrent 场景语义明确。

19. 全部 V1.2a tests 保持通过。

20. 全部 V1.2b semantic tests 通过。
```

---

# 最终架构边界

V1.2 完成后的 RoboAgent 应保持：

```text
                  Application / Robot Agent
                           │
                 RoboAgent V1.2 Layer
                           │
       ┌───────────────────┼───────────────────┐
       │                   │                   │
Context & Durability    Ecosystem            Safety
       │                   │                   │
ContextRequest             MCP             Approval
PreparedContext
ContextBudget
Compaction
Workspace
Materialization
Persistence
       │
       └───────────────────┬───────────────────┘
                           │
                   RoboAgent V1.1
                    Runtime Kernel
                           │
       ┌───────────────────┼───────────────────┐
       │                   │                   │
     Agent              Session               Run
       │                   │                   │
     Model           Canonical state       AgentLoop
                                               │
                                          ToolExecutor
```

V1.1 解决：

```text
Can the agent run correctly?
```

V1.2 解决：

```text
Can the agent keep working effectively
and durably on long-running tasks?
```

V1.3 再考虑：

```text
Can agents compose with other agents?
```

更后续版本再考虑：

```text
Memory
Sandbox
Run recovery
distributed execution
robot ecosystem
```

V1.2 最核心的不是新增多少类，而是冻结以下六组基础协议：

```text
ContextRequest
      ↓
PreparedContext
      ↓
ContextManager 只计算，不改 Session


ModelContextSegment
      ↓
Summary / Workspace reference
合法进入模型上下文


RawToolResult
      ↓
ToolResultMaterializer
      ↓
ArtifactReferenceContent


Tool invocation evidence
      ↓
与 materialization success/failure 分离


runtime_revision
+
durable_revision
+
last_pending_sequence
      ↓
SessionSnapshot + CAS


ToolPolicyDecision
+
Approval
      ↓
保持 V1.1 SERIAL / CONCURRENT semantics
```

只要这六组协议完成并通过语义测试，RoboAgent V1.2 就具备稳定的 long-horizon foundation，同时仍然保持 V1.1 已经建立起来的 Runtime Kernel 边界和轻量化设计。
