# RoboAgent V1.3

## Runtime Integration & Coding Reference Agent

**Status:** Stable
**Specification Level:** Implementation Specification
**Target Platform:** RoboAgent Core 跨平台；Coding Reference Agent 与 V1.3 `apply_patch` 强安全语义为 Linux/POSIX

---

# 1. 目标、原则与版本边界

## 1.1 版本目标

RoboAgent V1.3 的目标是：

> 在保持 RoboAgent 通用异步 Agent Runtime Kernel 定位不变的前提下，补齐复杂 Nested Execution 所需的通用 Runtime 协议，并通过 `examples/coding` 实现一个完整的 Coding Reference Agent，验证 Runtime 的组合性、稳定性、上下文管理能力、工具执行语义、取消语义和扩展边界。

V1.3 包含两个组成部分：

```text
RoboAgent Core
    ↓
Nested Execution Runtime

examples/coding
    ↓
Coding Reference Agent
```

两者属于同一个版本，但保持严格边界：

> Coding Reference Agent 必须建立在 RoboAgent 通用能力之上。

> 任何仅服务于 Python Code-as-Action、Coding CLI、软件工程 Prompt 或本地 Python interpreter 的能力，不得下沉到 RoboAgent Core，除非已经存在独立、跨应用的 Runtime 需求。

Coding Reference Agent 是 Runtime 的复杂验证场景，不是 Core API 的定义来源。

---

## 1.2 Core 准入原则

新增 abstraction 只有同时满足以下条件，才允许进入 `roboagent/`：

1. 删除 `examples/coding` 后，该 abstraction 仍具有明确独立价值；
2. 至少存在两个不同类型的 Runtime consumer；
3. 现有 Agent、Session、Run、Tool、Context、Policy、Approval、Hooks 等 abstraction 无法自然承载；
4. 不建立第二个 canonical 状态来源；
5. 不建立第二个执行事实来源；
6. 不要求 AgentLoop 感知具体应用策略；
7. 不因为 Coding Example 的实现方便而污染 Runtime；
8. 不为尚未出现的未来需求提前建立大型 subsystem；
9. 优先扩展已有边界，而不是创建功能重叠的新框架。

V1.3 新增的 Core 能力仅包括：

```text
ExecutionScope
ExecutionLineage
ExecutionContribution
ExecutionBudget
Composite Tool semantics
Nested Tool Execution
Settlement Barrier
ExecutionRecord
RetryBlocker
Agent-as-Tool
Artifact promotion protocol
```

其中 Nested Execution 已经具有两个相互独立的 consumer：

```text
Agent-as-Tool
    → Child Run

execute_python
    → CodeToolBridge
    → Nested Tool
```

因此：

```text
ExecutionScope
ExecutionContribution
ExecutionBudget
Cancellation / Deadline
Settlement
```

属于通用 Runtime，而不是 Coding-specific abstraction。

---

## 1.3 保持不变的 Runtime 基线

以下继续保持 RoboAgent 唯一 canonical Runtime：

```text
Agent
Session
Run
AgentLoop

Model
ModelProvider
ModelContext
ContextManager
PreparedContext

Tool
ToolRegistry
ToolCall
ToolExecutor
ToolExecutionPolicy
ToolResult

Approval
Hooks
Events
Effects
Skills
```

V1.3 禁止新增：

```text
第二套 AgentLoop
第二套 Session
第二套 Run
第二套 transcript
第二套 Model abstraction
第二套 Tool abstraction
第二套 ContextManager
第二套 ActionStep / ObservationStep
第二套 AgentMemory
第二套 Coding transcript
```

`Session transcript` 继续是 canonical conversation fact。

`RunState` 继续保持：

> immutable observable Run snapshot。

RunState 不承担：

```text
Python worker
Child Run handle
MCP connection
mutable cache
event sink
effect accumulator
cleanup registry
extension resource container
```

等资源所有权。

V1.3 不增加：

```python
RunState.extensions
```

作为 Runtime resource container。

---

## 1.4 AgentLoop 不理解 Coding

V1.3 的硬性不变量：

> AgentLoop 不得出现任何 Coding-specific branch。

禁止：

```python
if code_agent:
    ...

if code_action:
    ...

if python_executor:
    ...

if execute_python_result.is_final:
    ...

if final_answer:
    ...
```

AgentLoop 继续只理解：

```text
ModelResponse
AssistantMessage
ToolCall
ToolResult
final AssistantMessage
```

Coding-specific 行为只允许存在于：

```text
CodingSession
CodingModelAdapter
execute_python
Python worker
CodeToolBridge
Coding Prompt
Coding CLI
```

---

## 1.5 Canonical Transcript

Coding execution 仍然使用：

```text
AssistantMessage
    ToolCall(execute_python)

ToolResultMessage
    execute_python observation
```

不新增：

```text
CodeAction
ActionStep
ObservationStep
PythonMemoryStep
CodeExecutionMessage
```

Context compaction 继续操作 canonical transcript。

Interpreter state 不进入 transcript。

---

## 1.6 V1.3 明确不实现

本版本不实现：

```text
通用 CodeAgent Core class
CodeAction canonical protocol
通用 PythonExecutor
ExecutionEnvironment abstraction
Middleware framework
Planner framework
Todo framework
Long-term Memory
Handoff
Supervisor
Swarm
Workflow Graph
完整 Sandbox subsystem
PTY terminal
background shell session
remote code executor
durable Python interpreter state
full artifact framework
MCP runtime
generic workflow runtime
```

这些能力只有在后续出现独立、跨应用需求时再单独设计。

---

# 2. Nested Execution Runtime

Nested Execution Runtime 是 V1.3 Core 的主要变化。

它统一解决：

```text
Agent-as-Tool
Child Run
Composite Tool
execute_python → CodeToolBridge → Nested Tool
```

共同需要的：

```text
lineage
usage
effects
events
budget
deadline
cancellation
settlement
cleanup
resource ownership
execution evidence
retry safety
```

---

## 2.1 RunContext / ToolContext 公共接口

V1.3 正式扩展：

```text
RunContext
ToolContext
```

但必须保持 V1.0–V1.2 构造兼容性。

### RunContext

新增字段必须放在 dataclass 尾部并提供默认值：

```python
@dataclass(frozen=True)
class RunContext:
    # Existing V1.0–V1.2 fields
    ...
    execution: RunExecutionContext | None = None
```

### ToolContext

```python
@dataclass(frozen=True)
class ToolContext:
    run_id: str
    session_id: str
    cancellation: CancellationToken

    execution: ToolExecutionContext | None = None
```

旧代码：

```python
ToolContext(
    run_id=run_id,
    session_id=session_id,
    cancellation=token,
)
```

继续合法。

但：

> RoboAgent V1.3 Runtime 自己创建的 RunContext 和 ToolContext 必须始终提供非空 `execution`。

`execution=None` 只用于：

```text
V1.0–V1.2 旧代码
旧测试
第三方手工构造
```

当 Tool 在：

```text
execution is None
```

情况下尝试 Nested Execution 时，返回：

```text
nested_execution_unavailable
```

不得临时创建另一套 Runtime。

---

## 2.2 Context Identity 不变量

Runtime 创建的 ToolContext 必须满足：

```text
ToolContext.run_id
==
ToolContext.execution.lineage.execution_run_id
```

同时：

```text
ToolContext.cancellation
is
ToolContext.execution.cancellation
```

必须是同一个 cancellation view，不是两个独立状态。

Root Run：

```text
RunContext.execution.lineage.root_run_id
==
Run.id

RunContext.execution.lineage.execution_run_id
==
Run.id
```

Child Run：

```text
RunContext.execution.lineage.root_run_id
==
最外层 Root Run.id

RunContext.execution.lineage.execution_run_id
==
当前 Child Run.id
```

因此：

> 当前 Context 的普通 Run identity 永远指向当前 execution Run；root identity 通过 lineage 单独表达。

---

## 2.3 RunExecutionContext

冻结：

```python
class RunExecutionContext(Protocol):
    @property
    def lineage(self) -> ExecutionLineage:
        ...

    @property
    def cancellation(self) -> CancellationToken:
        ...

    @property
    def deadline(self) -> float | None:
        ...

    @property
    def budget(self) -> ExecutionBudgetView:
        ...

    # Runtime orchestration semantics; these do not expose Scope/Tree.
    def tool_context(self, executor: object, session_id: str) -> ToolExecutionContext:
        ...

    def contribute_usage(self, usage: UsageContribution) -> None:
        ...

    def mark_tool_calls_committed(self, call_ids: tuple[str, ...]) -> None:
        ...
```

RunExecutionContext 不提供：

```text
任意 execute_tool
任意 run_child_agent
ExecutionTree / ExecutionScope internal mutation
```

Nested execution 只能从 Tool execution 发起。

`AgentLoop`、`agent/*` 与 `tool/*` 不得访问 `_scope`、`_tree`，也不得导入
`ExecutionTree` / `ExecutionScope`；所有 bookkeeping 必须通过上述语义 API。

---

## 2.4 ToolExecutionContext

冻结：

```python
class ToolExecutionContext(Protocol):
    @property
    def lineage(self) -> ExecutionLineage:
        ...

    @property
    def cancellation(self) -> CancellationToken:
        ...

    @property
    def deadline(self) -> float | None:
        ...

    @property
    def budget(self) -> ExecutionBudgetView:
        ...

    async def execute_nested_tool(
        self,
        name: str,
        arguments: Mapping[str, JsonValue],
    ) -> ToolExecutionResult:
        ...

    async def run_child_agent(
        self,
        agent: Agent,
        task: str,
        *,
        session_factory: ChildSessionFactory | None = None,
        run_config: RunConfig | None = None,
    ) -> RunResult:
        ...

    def settlement_barrier(
        self,
        *,
        handler: SettlementHandler,
        timeout: float | None = None,
    ) -> AsyncContextManager[None]:
        ...

    def register_resource(
        self,
        resource: ExecutionResource,
    ) -> None:
        ...
```

公开 Nested Tool 执行结果明确命名：

```text
ToolExecutionResult
```

避免和 canonical ToolResult message 混淆。

---

## 2.5 ExecutionScope

每个 Root Run 创建：

```text
Root ExecutionScope
```

每个 canonical ToolCall 创建：

```text
Root Run Scope
    ↓
Tool Scope
```

Agent-as-Tool：

```text
Tool Scope
    ↓
Child Run Scope
```

CodeToolBridge：

```text
execute_python Tool Scope
    ↓
Nested Tool Scope
```

Child Run 必须加入已有 execution tree。

不得：

```text
建立第二棵 root execution tree
建立第二套 Event sequence
建立第二套 usage accumulator
```

---

## 2.6 ExecutionScope 生命周期

固定：

```text
OPEN
 ↓
CLOSING
 ↓
FROZEN
```

### OPEN

允许：

```text
创建 child scope
接受 execution request
注册 resource
普通 contribution
settlement contribution
```

### CLOSING

禁止：

```text
创建新 child
接受新 execution
注册新的业务 resource
```

仍允许已经 accepted 的执行完成：

```text
partial usage
realized effects
rollback result
worker termination result
cleanup errors
settlement contribution
```

因此：

> CLOSING 表示停止新的工作，不表示停止旧工作的结算。

### FROZEN

禁止：

```text
execution
child creation
resource registration
contribution
```

最终 RunResult 只能在 Root Scope `FROZEN` 后构建。

---

## 2.7 ExecutionLineage

冻结：

```python
@dataclass(frozen=True)
class ExecutionLineage:
    root_run_id: str
    execution_run_id: str

    scope_id: str
    parent_scope_id: str | None

    scope_depth: int
    agent_depth: int

    tool_call_id: str | None = None
    agent_tool_name: str | None = None
```

### root_run_id

最外层用户 Run。

### execution_run_id

当前真正执行该工作单元的 Run。

Child Agent：

```text
root_run_id != execution_run_id
```

### scope_depth

所有 Nested Execution 深度。

### agent_depth

只计算 Agent delegation。

例如：

```text
root
└─ execute_python
   └─ read_file
```

所有这些：

```text
agent_depth = 0
```

而：

```text
root Agent
└─ research Agent
   └─ specialist Agent
```

依次：

```text
0
1
2
```

---

## 2.8 Event Lineage

`AgentEvent` 增加末尾兼容字段：

```python
@dataclass(frozen=True)
class AgentEvent:
    ...
    lineage: ExecutionLineage | None = None
```

旧 subscriber 兼容规则：

```text
AgentEvent.run_id == lineage.root_run_id
```

Child 实际 Run：

```text
event.lineage.execution_run_id
```

Lineage 不写进业务 payload。

---

## 2.9 Root / Child Event 类型

Root：

```text
run.started
run.completed
run.failed
run.cancelled
```

Child：

```text
child_run.started
child_run.completed
child_run.failed
child_run.cancelled
```

只有：

```text
root run.completed
root run.failed
root run.cancelled
```

具有 terminal semantics。

Child terminal event 不允许关闭 Root Event stream。

---

## 2.10 Root-wide Sequence Allocators

每棵 execution tree 独立拥有：

```text
scope_sequence_allocator
event_sequence_allocator
record_sequence_allocator
```

三者：

```text
root-wide
monotonically increasing
tree-unique
彼此独立
```

不得复用同一个数字 namespace。

---

## 2.11 scope_sequence

每个 scope 在创建时获得：

```python
scope_sequence: int
```

来源：

```text
root scope_sequence_allocator
```

并发 ToolCall：

> 必须在 coroutine/task 创建前，按照 canonical ToolCall 顺序分配。

例如：

```text
ToolCall[0] → 10
ToolCall[1] → 11
ToolCall[2] → 12
```

不得按运行完成时间排序。

---

## 2.12 Event sequence

所有 Event：

```text
sequence
```

由：

```text
root event_sequence_allocator
```

统一生成。

Child Run 不允许从 1 重新计数。

---

## 2.13 ExecutionRecord.sequence

所有 ExecutionRecord：

```text
sequence
```

来自：

```text
root record_sequence_allocator
```

不能使用：

```text
event sequence
scope sequence
ContributionId.sequence
```

---

## 2.14 EffectIdentity

新增：

```python
@dataclass(frozen=True)
class EffectIdentity:
    scope_id: str
    sequence: int
```

ToolEffectRecord 增加：

```python
effect_id: EffectIdentity
```

其中：

```text
sequence
```

是 scope 内 effect-local monotonic sequence。

---

## 2.15 Effect 排序

最终：

```text
RunResult.effects
```

按照：

```text
(scope.scope_sequence, effect.effect_id.sequence)
```

排序。

因此并发完成时间不影响模型/调用方可观察 effect 顺序。

---

## 2.16 Tool Effect Reporting

新增：

```python
class ToolEffectReporting(str, Enum):
    LEAF = "leaf"
    COMPOSITE = "composite"
```

Tool：

```python
effect_reporting: ToolEffectReporting = ToolEffectReporting.LEAF
```

---

## 2.17 Leaf Tool

以下普通 Tool：

```text
read_file
write_file
edit_file
shell
```

使用：

```text
LEAF
```

ToolExecutor 按已有 leaf effect 语义处理。

---

## 2.18 Composite Tool

以下：

```text
Agent-as-Tool
execute_python
apply_patch
```

使用：

```text
COMPOSITE
```

ToolExecutor 不自动生成：

```text
outer fake side-effect record
```

---

## 2.19 CompositeToolOutcome

冻结：

```python
@dataclass(frozen=True)
class CompositeToolOutcome:
    content: tuple[ToolContent, ...]
    effects: tuple[ToolEffectRecord, ...] = ()
    records: tuple[SupplementalExecutionRecord, ...] = ()
```

Tool handler 返回类型：

```python
ToolHandlerReturn = ToolReturn | CompositeToolOutcome
```

Tool.execute() 必须支持两种返回类型。

---

## 2.20 SupplementalExecutionRecord

Composite Tool 不允许直接构造全局 ExecutionRecord sequence。

因此：

```python
@dataclass(frozen=True)
class SupplementalExecutionRecord:
    status: ExecutionRecordStatus
    error_code: str | None
    evidence: FrozenJsonObject | None
```

Runtime 接收后补全：

```text
sequence
root_run_id
execution_run_id
scope_id
record_type=SUMMARY
```

CompositeToolOutcome.records：

> 只能是 supplemental SUMMARY record。

不得替代 Runtime 自动生成的 outer TOOL terminal record。

---

## 2.21 Composite ToolExecutor Pipeline

Composite Tool 完整执行顺序：

```text
schema validation
↓
Policy
↓
Approval
↓
before_tool hooks
↓
handler
↓
CompositeToolOutcome type validation
↓
canonical content validation
↓
register returned effects
↓
register supplemental records
↓
after_tool hooks
↓
construct ToolExecutionResult
↓
outer ToolExchange commit
```

---

## 2.22 Composite content 不再 materialize

`CompositeToolOutcome.content` 已经是：

```text
canonical ToolContent
```

因此：

> ToolExecutor 不允许再次调用 RawToolResult → ToolContent 的 ToolResultMaterializer。

Materializer 仍只负责：

```text
ToolReturn / RawToolResult
→ canonical ToolContent
```

Composite handler 自己必须返回合法 canonical ToolContent。

---

## 2.23 Composite content validation

ToolExecutor 必须验证：

```text
每个 item 是合法 ToolContent
ArtifactReferenceContent 满足 canonical schema
JSON content 可序列化
inline content 满足大小约束
```

非法：

```text
invalid_composite_tool_content
```

---

## 2.24 Composite effect 唯一来源

### Agent-as-Tool

Child nested effects 已经在 Child Tool execution 时实时 contribution。

所以：

```python
CompositeToolOutcome.effects == ()
```

不得复制：

```text
Child RunResult.effects
```

### execute_python

CodeToolBridge nested tools 已经实时 contribution。

所以：

```text
execute_python outcome.effects == ()
```

### apply_patch

apply_patch 不通过 nested Tool 修改文件。

因此它通过：

```text
CompositeToolOutcome.effects
```

返回多个 per-file effects。

Runtime 再按：

```text
effect_id
```

进行防御性去重。

---

## 2.25 Static Effect 与 Runtime Effect

`Tool.effect_kind`：

> capability upper bound。

用于：

```text
Policy
Approval
UI capability display
```

Runtime Effect：

> invocation 实际发生了什么。

用于：

```text
RunResult.effects
retry_safe
audit/evidence
```

Restricted execute_python 即使 static：

```text
SIDE_EFFECTING
```

只读取文件时：

```text
runtime side effects = none
```

因此：

```text
retry_safe 可以为 true
```

---

## 2.26 ContributionId

新增：

```python
@dataclass(frozen=True)
class ContributionId:
    scope_id: str
    sequence: int
```

每个 scope 内 sequence 单调递增。

---

## 2.27 ExecutionContribution

```python
@dataclass(frozen=True)
class ExecutionContribution:
    contribution_id: ContributionId

    usage: UsageContribution | None = None
    effects: tuple[ToolEffectRecord, ...] = ()
    records: tuple[SupplementalExecutionRecord, ...] = ()
    cleanup_errors: tuple[CleanupError, ...] = ()
```

Accumulator exactly-once：

```text
同 contribution_id + 相同内容
→ duplicate，ignore

同 contribution_id + 不同内容
→ runtime invariant violation
```

---

## 2.28 Usage 三态

内部三态：

```text
ABSENT
UNKNOWN
KNOWN
```

定义：

```python
class UsageKnowledge(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class UsageContribution:
    state: UsageKnowledge
    usage: Usage | None
```

约束：

```text
KNOWN
→ usage != None

UNKNOWN
→ usage == None
```

`ExecutionContribution.usage is None`：

```text
ABSENT
```

---

## 2.29 Model invocation 与 Usage UNKNOWN

任何实际 Model invocation：

```text
已经进入 model/provider request
```

都必须贡献 Usage。

规则：

```text
ModelResponse.usage is Usage
→ KNOWN

ModelResponse.usage is None
→ UNKNOWN
```

ABSENT 仅表示：

```text
没有发生 Model invocation
```

例如：

```text
Run 在 Model 调用前被取消
```

---

## 2.30 Usage 部分未知字段

例如：

```python
Usage(
    input_tokens=None,
    output_tokens=10,
    total_tokens=None,
)
```

仍然属于：

```text
KNOWN
```

因为 Usage object 存在。

字段级 merge：

```text
int + int → sum

None + int → None
int + None → None
None + None → None
```

---

## 2.31 Usage aggregate

Accumulator 初始：

```text
ABSENT
```

规则：

```text
ABSENT + KNOWN
→ KNOWN

ABSENT + UNKNOWN
→ UNKNOWN

KNOWN + KNOWN
→ field-wise merge

KNOWN + UNKNOWN
→ UNKNOWN

UNKNOWN + anything
→ UNKNOWN
```

---

## 2.32 Usage exactly-once

Child Run model/context usage：

> 在发生时直接 contribution 到 root accumulator。

Agent-as-Tool 拿到 Child RunResult 后：

```text
禁止再次累加 child usage
```

Coding protocol retry 的所有实际 Provider request：

```text
全部计入 usage
```

Cancel/failure 前已报告的 partial usage：

```text
仍计入
```

---

## 2.33 UsageUpdated Event

只有：

```text
KNOWN
```

usage 才发送：

```text
UsageUpdated
```

UNKNOWN：

```text
不发送 UsageUpdated
```

ModelResponse：

```text
usage=None
```

由 AgentLoop/Runtime contribution adapter 转成：

```text
UsageContribution(UNKNOWN)
```

---

## 2.34 RunResult Usage

保留：

```text
RunResult.usage: Usage | None
```

新增兼容字段：

```python
usage_known: bool | None = None
```

映射：

```text
ABSENT
→ usage=None
→ usage_known=None

UNKNOWN
→ usage=None
→ usage_known=False

KNOWN
→ usage=<merged>
→ usage_known=True
```

---

## 2.35 Local final Usage

CodingModelAdapter local final：

```text
不是 Provider request
```

但它是合法 Adapter Model turn。

明确：

```text
KNOWN Usage(0, 0, 0)
```

并发送：

```text
UsageUpdated(0, 0, 0)
```

---

## 2.36 ExecutionBudgetConfig

属于：

```text
RunConfig.execution_budget
```

冻结：

```python
@dataclass(frozen=True)
class ExecutionBudgetConfig:
    max_agent_depth: int = 4
    max_child_runs: int = 32
    max_nested_tool_calls: int = 256
```

校验：

```text
type(value) is int
bool 拒绝
value >= 0
```

---

## 2.37 ExecutionBudgetView

```python
@dataclass(frozen=True)
class ExecutionBudgetView:
    max_agent_depth: int
    remaining_child_runs: int
    remaining_nested_tool_calls: int
```

只读。

---

## 2.38 Nested Tool budget 精确定义

`max_nested_tool_calls` 只统计：

> 通过 `ToolExecutionContext.execute_nested_tool()` 发起的 Tool execution。

因此：

```text
Root Agent canonical ToolCall
→ 不计

Child Agent 模型生成的普通 canonical ToolCall
→ 不计

execute_python → CodeToolBridge → execute_nested_tool()
→ 计

其它 Composite Tool → execute_nested_tool()
→ 计
```

Policy/Approval rejected nested request：

> 在 accepted 后仍消耗一次。

---

## 2.39 Budget 消耗时机

```text
schema validation
↓
duplicate request detection
↓
budget availability
↓
accepted
```

只有 accepted 后消耗。

```text
validation failure
→ 不消耗

budget rejection
→ 不消耗

duplicate IPC request
→ 不重复消耗

policy rejection
→ 消耗

approval rejection
→ 消耗

execution timeout
→ 消耗
```

---

## 2.40 Worker step limit

单个 execute_python step 另有：

```text
WorkerLimits.max_tool_requests_per_step
```

检查：

```text
step limit
↓
root nested tool budget
```

错误：

```text
python_tool_request_limit_exceeded
nested_tool_budget_exceeded
```

---

## 2.41 Cancellation

每个 scope 使用 linked cancellation。

内部记录：

```text
cancelled
reason
origin
```

origin：

```text
parent
timeout
external
runtime
```

Parent cancellation：

```text
向所有 descendants 传播
```

Child cancellation：

```text
默认不向 parent 反向传播
```

---

## 2.42 Deadline

ExecutionScope 保存：

```text
absolute monotonic deadline
```

实际：

```python
remaining = deadline - monotonic()
```

而不是重新叠加 relative timeout。

---

## 2.43 Child RunConfig 优先级

Child Run 使用：

```text
run_child_agent(run_config=...)
↓
Agent.as_tool(run_config=...)
↓
child Agent default RunConfig
```

effective deadline：

```text
min(
    parent Tool scope deadline,
    child RunConfig deadline
)
```

Outer Tool timeout 已包含在 parent Tool scope deadline 内。

---

## 2.44 RunConfig 完整 V1.3 字段

在 V1.2 RunConfig 原字段之后追加：

```python
@dataclass(frozen=True)
class RunConfig:
    # Existing V1.0–V1.2 fields
    ...

    execution_budget: ExecutionBudgetConfig = field(
        default_factory=ExecutionBudgetConfig
    )

    settlement_timeout: float = 10.0
    cleanup_timeout: float = 5.0

    max_execution_records: int = 4096
    max_record_evidence_bytes: int = 4096

    max_child_artifact_bytes: int = 64 * 1024 * 1024
```

---

## 2.45 RunConfig 校验

```text
settlement_timeout
→ int/float
→ bool 拒绝
→ > 0

cleanup_timeout
→ int/float
→ bool 拒绝
→ > 0

max_execution_records
→ type is int
→ > 0

max_record_evidence_bytes
→ type is int
→ > 0

max_child_artifact_bytes
→ type is int
→ > 0
```

---

## 2.46 SettlementHandler

Settlement Barrier 必须能真正 force-settle。

新增：

```python
class SettlementHandler(Protocol):
    async def settle(self) -> None:
        ...

    async def force_settle(self) -> None:
        ...
```

---

## 2.47 Settlement Barrier API

冻结：

```python
def settlement_barrier(
    self,
    *,
    handler: SettlementHandler,
    timeout: float | None = None,
) -> AsyncContextManager[None]:
    ...
```

如果 timeout 未给：

```text
使用 RunConfig.settlement_timeout
```

默认：

```text
10.0 s
```

---

## 2.48 Settlement Barrier 适用范围

只用于：

```text
已经开始、必须收敛的外部状态变化
```

例如：

```text
apply_patch commit
apply_patch rollback
worker terminate
worker reap
```

不得把普通长时业务工作全部放进 barrier。

---

## 2.49 Settlement Cancellation

进入 Barrier 后：

```text
新的 cancellation 被记录
```

但：

```text
不会立即打断 settlement
```

Runtime 必须先尝试：

```text
handler.settle()
```

---

## 2.50 Settlement timeout

如果 settle 超过：

```text
timeout
```

Runtime：

```text
调用 handler.force_settle()
```

force_settle 也失败/超时：

```text
settlement_timeout
```

并增加：

```text
RetryBlocker.SETTLEMENT_UNCERTAIN
```

Run：

```text
FAILED
retry_safe=false
```

---

## 2.51 Settlement body exception

Barrier body exception：

> 正常向 Tool execution 传播。

但退出 barrier 前：

```text
必须执行 settle / force_settle
```

Settlement 本身失败：

```text
settlement_failed
```

---

## 2.52 ExecutionResource

```python
class ExecutionResource(Protocol):
    async def close(self) -> None:
        ...

    async def force_close(self) -> None:
        ...
```

`force_close()` 可：

```python
raise NotImplementedError
```

只允许 OPEN scope：

```text
register_resource()
```

---

## 2.53 CleanupError

```python
@dataclass(frozen=True)
class CleanupError:
    scope_id: str
    resource_type: str
    code: str
    message: str
    forced: bool
```

---

## 2.54 Cleanup timeout

默认：

```text
RunConfig.cleanup_timeout = 5.0 s
```

Cleanup：

```text
LIFO
```

流程：

```text
close()
↓
timeout/failure
↓
force_close() if supported
```

---

## 2.55 Generic Cleanup Guarantee

Runtime 对 generic third-party resource 只能保证：

```text
尝试 graceful close
尝试 force close
记录 uncertainty
```

不能保证 OS 层任何任意资源一定消失。

只有 Runtime 明确拥有：

```text
terminate
kill
reap
```

能力的 Coding Worker，才有“无 worker process 残留”强保证。

---

## 2.56 Root completion order

固定：

```text
stop accepting new execution
↓
root scope → CLOSING
↓
cancel descendants if required
↓
wait active settlement barriers
↓
cleanup resources
↓
collect settlement contribution
↓
root scope → FROZEN
↓
freeze usage/effects/records/retry blockers
↓
on_run_end
↓
root terminal event
↓
RunResult
```

`on_run_end` 必须看到最终 aggregate state。

---

## 2.57 ExecutionRecordStatus

最终 RunResult 中不暴露 STARTED record。

冻结：

```python
class ExecutionRecordStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"
```

未能收敛 execution：

```text
UNKNOWN
```

---

## 2.58 ExecutionRecordType

```python
class ExecutionRecordType(str, Enum):
    TOOL = "tool"
    SUMMARY = "summary"
```

---

## 2.59 ExecutionRecord

```python
@dataclass(frozen=True)
class ExecutionRecord:
    sequence: int
    record_type: ExecutionRecordType

    root_run_id: str
    execution_run_id: str
    scope_id: str

    tool_call_id: str | None
    tool_name: str | None

    arguments_digest: str | None
    arguments_preview: FrozenJsonObject | None

    status: ExecutionRecordStatus
    error_code: str | None

    evidence: FrozenJsonObject | None
```

一次 Tool execution：

> 最终只产生一个 `record_type=TOOL` terminal record。

---

## 2.60 ExecutionRecord evidence 超限

`max_record_evidence_bytes` 同时限制：

```text
arguments_preview
evidence
```

行为：

### arguments_preview 超限

```text
arguments_preview = None
```

digest 保留。

### evidence 超限

不截断 JSON。

固定：

```text
evidence = {
    "omitted": true,
    "digest": "sha256:...",
    "size": <serialized-byte-size>
}
```

如果连这个固定 envelope 理论上也超限：

```text
evidence = None
```

不得修改 Tool execution status。

Evidence 太大：

> 不代表 execution UNKNOWN。

---

## 2.61 ExecutionRecord Public API

RunResult 增加末尾字段：

```python
execution_records: tuple[ExecutionRecord, ...] = ()
execution_records_complete: bool = True
```

---

## 2.62 Record overflow

默认：

```text
max_execution_records = 4096
```

到达上限：

1. 已有 records 保留；
2. 最后可用 slot 写：

```text
record_type=SUMMARY
status=UNKNOWN
error_code="execution_record_overflow"
```

3. 后续 detailed records 停止；
4. 设置：

```text
execution_records_complete=false
```

---

## 2.63 Audit completeness

如果：

```text
execution_records_complete=true
```

ExecutionRecord 可用于完整 bounded audit。

如果：

```text
false
```

Claim 结果必须允许：

```text
verified
contradicted
unverifiable
```

不能把“找不到 record”解释为“操作成功”。

---

## 2.64 ToolRecordRedactor

Tool 增加：

```python
record_redactor: ToolRecordRedactor | None = None
```

```python
class ToolRecordRedactor(Protocol):
    def __call__(
        self,
        arguments: FrozenJsonObject,
    ) -> FrozenJsonObject | None:
        ...
```

调用时机：

```text
schema validation success
↓
record construction
```

Redactor 抛异常：

```text
arguments_preview=None
```

不影响 Tool execution。

---

## 2.65 EffectCertainty

```python
class EffectCertainty(str, Enum):
    CERTAIN = "certain"
    CERTAIN_NO_EFFECT = "certain_no_effect"
    UNKNOWN = "unknown"
```

---

## 2.66 EffectCertainty 合法矩阵

`ToolEffectRecord.__post_init__` 必须保证：

| ToolEffectStatus | 合法 EffectCertainty             |
| ---------------- | ------------------------------ |
| `SUCCEEDED`      | `CERTAIN`                      |
| `UNKNOWN`        | `UNKNOWN`                      |
| `FAILED`         | `CERTAIN_NO_EFFECT`, `UNKNOWN` |
| `CANCELLED`      | `CERTAIN_NO_EFFECT`, `UNKNOWN` |
| `TIMED_OUT`      | `CERTAIN_NO_EFFECT`, `UNKNOWN` |

禁止：

```text
SUCCEEDED + UNKNOWN
SUCCEEDED + CERTAIN_NO_EFFECT
UNKNOWN + CERTAIN
UNKNOWN + CERTAIN_NO_EFFECT
```

---

## 2.67 Effect commit

Nested effect 发生后：

```text
transcript_committed=false
```

只有最外层 canonical ToolExchange：

```text
commit 到 root Session transcript
```

后：

```text
transcript_committed=true
```

Child Session transcript commit：

> 不改变该字段。

---

## 2.68 Outer commit failure

如果副作用真实发生，但 outer ToolExchange commit 失败：

```text
effect 保留
transcript_committed=false
```

不得丢弃真实 effect。

---

## 2.69 RetryBlocker

不是所有 retry uncertainty 都能映射成 ToolEffect。

新增：

```python
class RetryBlockerCode(str, Enum):
    SETTLEMENT_UNCERTAIN = "settlement_uncertain"
    TRUSTED_EXECUTION = "trusted_execution"
    CLEANUP_UNCERTAIN = "cleanup_uncertain"


@dataclass(frozen=True)
class RetryBlocker:
    code: RetryBlockerCode
    scope_id: str
    message: str
```

RunResult：

```python
retry_blockers: tuple[RetryBlocker, ...] = ()
```

---

## 2.70 retry-safe 唯一公式

```python
retry_safe = (
    not retry_blockers
    and all(
        effect_retry_safe(effect)
        for effect in effects
    )
)
```

### READ_ONLY effect

不影响 retry-safe。

### SIDE_EFFECTING SUCCEEDED

仅当：

```text
certainty == CERTAIN
AND
transcript_committed == true
```

才 safe。

### FAILED/CANCELLED/TIMED_OUT

只有：

```text
certainty == CERTAIN_NO_EFFECT
```

才 safe。

### UNKNOWN

永远 unsafe。

---

## 2.71 Settlement uncertainty

Settlement 无法确认，即使：

```text
effects == ()
```

也增加：

```text
RetryBlocker.SETTLEMENT_UNCERTAIN
```

因此不会错误得到：

```text
all([]) == true
```

导致 retry-safe=true。

---

## 2.72 Trusted Mode effect 语义

Trusted Python 可以直接：

```python
open()
os.*
subprocess.*
```

这些行为 Runtime 无法完整观察。

因此 Trusted：

```text
execute_python.effect_kind = SIDE_EFFECTING
```

固定。

不根据 exposed tools 推导。

---

## 2.73 Trusted execution RetryBlocker

Trusted execute_python 一旦真正开始执行用户 Python：

```text
add RetryBlocker(
    TRUSTED_EXECUTION
)
```

因此：

```text
retry_safe=false
```

无论：

```text
SUCCESS
FAILED
CANCELLED
```

均如此。

Trusted Mode 明确：

> 不提供完整 Runtime Effect audit。

> 不提供 retry-safe 保证。

---

# 3. Agent-as-Tool

## 3.1 定位

Agent-as-Tool 是 V1.3 唯一的通用 delegation primitive。

它不是：

```text
Handoff
Supervisor
Swarm
Workflow
Agent graph
```

---

## 3.2 API

```python
agent.as_tool(
    name="research",
    description="Research the task independently.",
    session_factory=None,
    run_config=None,
)
```

---

## 3.3 Input schema

固定：

```json
{
  "type": "object",
  "properties": {
    "task": {
      "type": "string",
      "minLength": 1
    }
  },
  "required": ["task"],
  "additionalProperties": false
}
```

Runtime 额外：

```text
task.strip() != ""
```

否则：

```text
invalid_arguments
```

---

## 3.4 ChildSessionContext

冻结：

```python
@dataclass(frozen=True)
class ChildSessionContext:
    root_session_id: str

    workspace: Workspace
    materializer: ToolResultMaterializer

    artifact_reader: ArtifactReader
    artifact_destination: ArtifactDestination

    repository: SessionRepository | None

    diagnostic_metadata: FrozenJsonObject
```

不暴露：

```text
完整 parent Session
parent transcript
parent pending queue
```

---

## 3.5 ChildSessionFactory

```python
class ChildSessionFactory(Protocol):
    async def create(
        self,
        *,
        parent: ChildSessionContext,
        agent: Agent,
    ) -> Session:
        ...
```

---

## 3.6 Default Child Session

`session_factory=None`：

1. 创建新的 ephemeral Session；
2. 绑定 requested child Agent；
3. transcript 为空；
4. pending queue 为空；
5. repository=`None`；
6. 使用 parent-compatible materializer；
7. 使用 parent-approved workspace；
8. 使用 parent artifact reader/destination；
9. 生命周期只覆盖当前 Agent Tool invocation。

---

## 3.7 Custom Session validation

Factory 返回后 Runtime 检查：

```text
session.agent == requested Agent
not closed
no active Run
transcript empty
pending queue empty
workspace valid
materializer valid
```

失败：

```text
invalid_child_session
```

---

## 3.8 Factory ownership

Factory 还没返回 Session 就抛错：

```text
child_session_creation_failed
```

Runtime 无 Session 可 close。

一旦 factory 成功返回 Session：

> ownership 转移给 Agent Tool Runtime。

即使 Session validation 失败：

```text
Runtime 也必须尝试 close()
```

---

## 3.9 Child Run 启动入口

Agent Tool handler 不访问 parent Session。

只调用：

```python
await context.execution.run_child_agent(
    agent,
    task,
    session_factory=...,
    run_config=...,
)
```

Runtime 内部负责：

```text
parent Session resolution
child Session creation
Child Run creation
execution tree attachment
lineage
usage
effects
events
budget
deadline
cancellation
cleanup
```

Runtime SPI 固定为一个入口，child 构造逻辑不得散落到 AgentLoop 或
ToolExecutor 的其它分支：

```python
@dataclass(frozen=True)
class ChildRunRequest:
    agent: Agent
    task: str
    session_factory: object | None = None
    run_config: RunConfig | None = None

@dataclass(frozen=True)
class ChildRunResult:
    result: RunResult

class ChildRunExecutor(Protocol):
    async def run_child(
        self,
        request: ChildRunRequest,
        parent: RuntimeToolExecutionContext,
    ) -> ChildRunResult:
        ...
```

归属规则固定为：child 使用独立 ephemeral/custom-isolated Session；child
transcript 不写入 parent Session，只有 materialized child output 作为父 Tool
result 进入 parent transcript。模型 usage 与真实 effects 归属 child scope，root
aggregate 各累计一次；Agent Tool 不生成第二份 usage 或伪 outer effect。

---

## 3.10 Child Run start failure

Session 合法但 Child Run 无法创建/启动：

```text
child_run_start_failed
```

然后：

```text
cleanup
close child Session
```

---

## 3.11 Child RunConfig 优先级

```text
run_child_agent(run_config=...)
↓
Agent.as_tool(run_config=...)
↓
Child Agent default RunConfig
```

---

## 3.12 Child deadline

```text
min(
    parent Agent-Tool scope deadline,
    resolved child RunConfig deadline
)
```

---

## 3.13 Child budget

Root：

```text
max_agent_depth = 4
max_child_runs = 32
```

Root：

```text
agent_depth = 0
```

进入 Child：

```text
+1
```

尝试超过：

```text
agent_depth=4
→ next delegation would be 5
```

返回：

```text
agent_depth_exceeded
```

Child Run 总数超过：

```text
max_child_runs
```

返回：

```text
child_run_budget_exceeded
```

---

## 3.14 Agent Tool static effect

Agent Tool：

```text
effect_reporting=COMPOSITE
```

静态 effect：

> 检查 child Agent 已冻结的 immediate ToolRegistry。

任一：

```text
SIDE_EFFECTING
```

则 Agent Tool：

```text
SIDE_EFFECTING
```

否则：

```text
READ_ONLY
```

不递归遍历 Agent graph。

Nested Agent Tool 已经拥有自己的冻结 effect_kind。

---

## 3.15 Approval

必须同时满足：

```text
Parent Agent Tool Policy/Approval
AND
Child actual Tool Policy/Approval
```

Outer Approval：

> 只表示允许启动 Child Agent capability。

不代表自动批准：

```text
write_file
shell
apply_patch
```

等 nested side effects。

---

## 3.16 Child COMPLETED

Child Run 正常完成后：

```text
validate output
↓
promote required artifacts
↓
construct parent-visible ToolContent
↓
cleanup child resources
↓
close Child Session
↓
return CompositeToolOutcome
```

---

## 3.17 Child FAILED

返回：

```text
ToolErrorInfo(
    code="child_execution_failed"
)
```

Child 已发生：

```text
usage
effects
records
```

继续保留在 root contribution 中。

之后：

```text
cleanup
close Child Session
```

---

## 3.18 Parent cancellation

如果 Child 因 Parent Run cancellation 被取消：

> 作为 cancellation propagation。

Agent Tool：

```text
CANCELLED
```

不转换为普通 ToolError。

---

## 3.19 External child cancellation

如果 Child 被其它外部路径取消而 Parent 未取消：

```text
child_cancelled
```

---

## 3.20 Child cleanup errors

Child COMPLETED 但 child cleanup 失败：

```text
child_cleanup_failed
```

Outer Agent Tool：

> 失败。

原因：

> invocation 生命周期未完整收敛。

如果 cleanup uncertainty 可能影响外部状态：

```text
RetryBlocker.CLEANUP_UNCERTAIN
```

---

## 3.21 Child output

当前：

```text
RunResult.output: AssistantMessage | None
```

不能直接 `str()`。

必须遍历：

```text
RunResult.output.content
```

---

## 3.22 Child Text

```text
TextContent
→ ToolTextContent
```

---

## 3.23 Child JSON

明确使用 canonical：

```text
JsonContent
→ ToolJsonContent
```

不是模糊的“JSON-compatible content”。

---

## 3.24 Image / Audio / File

```text
ImageContent
AudioContent
FileContent
```

全部：

```text
materialize
↓
promote
↓
ArtifactReferenceContent
```

---

## 3.25 Child ArtifactReferenceContent

如果 Child AssistantMessage 本身已经包含：

```text
ArtifactReferenceContent
```

也不能直接透传 Child URI。

必须：

```text
验证 source
↓
stream read
↓
promote 到 parent destination
↓
产生新的 parent-visible ArtifactReferenceContent
```

---

## 3.26 Child output missing

```text
RunResult.output is None
```

返回：

```text
child_output_missing
```

---

## 3.27 ArtifactReader

现有 Workspace：

```text
read() -> bytes
write(bytes)
```

不足以完成大 artifact streaming。

因此新增：

```python
class ArtifactReader(Protocol):
    async def iter_bytes(
        self,
        reference: ArtifactReferenceContent,
        *,
        chunk_size: int,
    ) -> AsyncIterator[bytes]:
        ...
```

---

## 3.28 ArtifactDestination

```python
class ArtifactDestination(Protocol):
    async def create_temp(
        self,
        *,
        media_type: str | None,
    ) -> ArtifactWriter:
        ...
```

---

## 3.29 ArtifactWriter

```python
class ArtifactWriter(Protocol):
    async def write(
        self,
        chunk: bytes,
    ) -> None:
        ...

    async def publish(
        self,
    ) -> ArtifactReferenceContent:
        ...

    async def abort(
        self,
    ) -> None:
        ...
```

`publish()`：

> 对可见性必须 atomic。

---

## 3.30 Artifact promotion size

来自：

```text
RunConfig.max_child_artifact_bytes
```

默认：

```text
64 MiB
```

超过：

```text
child_artifact_too_large
```

---

## 3.31 Artifact streaming

```text
create_temp()
↓
iter_bytes()
↓
writer.write(chunk)
↓
calculate digest
↓
verify source digest if available
↓
writer.publish()
```

禁止一次性：

```text
read all bytes into memory
```

---

## 3.32 Promotion cancellation

Cancellation：

```text
stop reader
↓
writer.abort()
```

Temp artifact 不可见。

只有：

```text
publish()
```

成功后，artifact 才可被 Parent ToolResult 引用。

---

## 3.33 Digest mismatch

Source 有 digest 时：

```text
必须验证
```

不一致：

```text
child_artifact_digest_mismatch
```

---

## 3.34 Promotion failure

任一 required output promotion 失败：

```text
child_output_materialization_failed
```

之前已经 publish 的其它 artifacts：

> 可以保留在 parent destination，但本次 Agent Tool 仍失败。

后续由 artifact GC / owning session lifecycle 处理。

---

## 3.35 Promotion success + cleanup failure

Artifact 已 publish 后 Child cleanup 失败：

```text
artifact 保留
outer Tool = child_cleanup_failed
```

---

# 4. Coding Reference Agent

## 4.1 定位

目录：

```text
examples/coding/
```

名称：

> RoboAgent Coding Agent

文档称谓：

> Coding Reference Agent

它不是：

```text
roboagent.CodeAgent
```

---

## 4.2 推荐目录

```text
examples/coding/
├── README.md
├── NOTICE.md
├── __main__.py
├── cli.py
├── harness.py
├── model_adapter.py
├── executor.py
├── worker.py
├── protocol.py
├── bridge.py
├── prompts.py
└── display.py
```

实际实现可以合并文件。

不得为了目录形式创建无意义 abstraction。

---

## 4.3 Coding Reference Agent 的上游代码复用原则

`examples/coding` 是：

> Reference Application。

不是 RoboAgent Runtime Core。

因此 V1.3 明确允许：

> 对成熟开源 Coding Agent 实现中的 application-layer 代码进行直接参考、改编、派生或 vendoring，避免从零重新实现已经被验证过的 Coding Harness 能力。

V1.3 首选参考实现：

```text
smolagents
https://github.com/huggingface/smolagents
```

主要原因：

```text
Python 实现
Code-as-Action 路线成熟
LocalPythonExecutor / AST evaluator 已存在
final_answer() 语义成熟
persistent interpreter state 已有实现
CLI / Rich terminal UX 可直接参考
代码规模相对可控
```

RoboAgent 不要求为了“原创实现”而重新发明这些能力。

核心原则：

> 能安全复用成熟代码，就优先复用。

同时：

> 上游 implementation 可以复用，但 RoboAgent Runtime 边界不能被替换。

---

## 4.4 可以直接参考、改编或迁移的 smolagents 能力

以下属于 Coding Harness / application layer，可优先从 smolagents 参考、复制并适配：

```text
CodeAgent 的 Code-as-Action 行为策略

LocalPythonExecutor

Python AST evaluator

statement evaluator

expression evaluator

authorized imports

restricted imports

persistent Python variable state

Python execution result formatting

Python exception formatting

final_answer() handling

Code parser

Python fenced-code parsing

CLI

Rich Console rendering

Rich code display

Tool-call display

Observation display

Error rendering

Interactive prompt UX

Coding session terminal presentation
```

如果 smolagents 某段实现与 V1.3 本文契约一致：

> 可以直接 vendoring，再做最小必要适配。

不要求重新手写等价 implementation。

---

## 4.5 优先评估迁移的文件

实现 `examples/coding` 时，以下本地模块应首先检查 smolagents 是否已有可直接复用实现：

```text
examples/coding/cli.py

examples/coding/display.py

examples/coding/executor.py

examples/coding/worker.py 中 evaluator 部分

examples/coding/protocol.py 中 parser 部分

examples/coding/prompts.py 中 Code-as-Action 提示策略
```

其中：

```text
executor / evaluator
```

是最优先迁移对象。

其次：

```text
CLI / display
```

应尽量利用上游已经成熟的 terminal UX。

---

## 4.6 不允许迁移为 RoboAgent Runtime 的 smolagents 部分

不得用 smolagents 以下 abstraction 替换 RoboAgent Runtime：

```text
MultiStepAgent

CodeAgent Runtime Loop

AgentMemory

ActionStep / TaskStep 等 step memory runtime

Model abstraction

Tool abstraction

Tool registry runtime

Planning runtime

Monitoring runtime

Agent step lifecycle
```

RoboAgent 必须继续使用自身 canonical：

```text
Agent
Session
Run
AgentLoop
Model
ContextManager
Tool
ToolExecutor
ToolExecutionPolicy
Approval
Hooks
Events
Effects
```

因此：

```text
可以迁移 Coding Harness implementation
不能迁移 smolagents Runtime architecture
```

---

## 4.7 禁止整体搬运 CodeAgent Runtime

禁止：

```text
smolagents.CodeAgent
    ↓
直接作为 RoboAgent Coding Agent runtime
```

正确方式：

```text
smolagents CodeAgent 中成熟的
parser / evaluator / prompt / UI 行为
        ↓
抽取 / vendoring / adapt
        ↓
examples/coding
        ↓
RoboAgent AgentLoop 仍是唯一执行循环
```

RoboAgent Coding Reference Agent 不建立：

```text
第二个 step loop
第二个 memory loop
第二个 planning loop
```

---

## 4.8 CLI 可以直接参考或复制

`examples/coding/cli.py` 和 `display.py` 属于 application layer。

因此：

> 可以直接以 smolagents CLI / Rich terminal implementation 为基础复制和修改。

优先复用：

```text
Rich Console

code syntax highlighting

Markdown rendering

Tool invocation rendering

Observation panel

Error panel

Final answer rendering

interactive prompt

terminal status / spinner

streaming UX
```

但必须改造其数据来源。

CLI 必须消费 RoboAgent 的：

```text
Run Events
RunResult
Approval
Steer
Cancellation
ExecutionLineage
ExecutionRecord
```

而不是保留 smolagents 自己的：

```text
AgentMemory
ActionStep
MultiStepAgent callback
```

---

## 4.9 CLI 迁移边界

可以复制：

```text
布局
颜色方案
Rich component
Panel/Table/Text 构造
用户交互方式
代码展示逻辑
错误格式化
```

不得复制为运行逻辑：

```text
smolagents Agent step loop
Tool dispatch
Model invocation loop
Memory append
Planning loop
```

因此 CLI 的正确依赖关系：

```text
RoboAgent Runtime
      ↓
Run Events
      ↓
examples/coding display adapter
      ↓
Rich Console
```

而不是：

```text
Rich CLI
   ↓
自己驱动 Agent loop
```

---

## 4.10 Python Executor 复用原则

Python evaluator 是 V1.3 最适合直接从 smolagents 迁移的部分。

优先迁移：

```text
AST parsing

statement dispatch

expression dispatch

assignment

loops

if

function-like supported constructs

container operations

authorized imports

variable state

error formatting

final_answer handling
```

但是迁移后必须重构能力调用路径。

smolagents executor 中如果存在：

```text
直接调用 smolagents Tool
```

必须替换为：

```text
Python worker
    ↓
IPC ToolRequest
    ↓
CodeToolBridge
    ↓
ToolExecutionContext.execute_nested_tool()
    ↓
RoboAgent ToolExecutor
```

---

## 4.11 Executor 不允许绕过 RoboAgent ToolExecutor

任何从 smolagents 迁移的 executor 代码都不得保留：

```python
tool(**arguments)
```

直接执行 canonical Tool handler 的路径。

所有真实 Tool 调用必须：

```text
经过 RoboAgent ToolExecutor
```

从而保留：

```text
schema validation
Policy
Approval
Hooks
Events
Effects
Cancellation
Deadline
Budget
ExecutionRecord
```

---

## 4.12 smolagents interpreter state 可以参考

可以复用 smolagents：

```text
persistent variable dictionary
AST evaluator state
import state
execution environment bookkeeping
```

但其 lifecycle 必须适配本文定义的：

```text
CodingSession-handle-scoped
process-local
non-durable
worker-generation-scoped
```

不能直接沿用上游与 AgentMemory 绑定的生命周期。

---

## 4.13 final_answer 可以参考或迁移

smolagents `final_answer()` 行为：

> 可以作为 RoboAgent Coding Reference Agent 的直接实现参考。

甚至：

> 可以 vendoring 其成熟 evaluator handling 后做适配。

但最终必须满足本文冻结的更严格要求：

```text
final_answer 是 evaluator intrinsic

普通 except BaseException 不能取消 completion

finally 必须执行

后续普通 statement 不执行

输出必须转换为 RoboAgent final union

最终仍通过 AgentLoop 的 canonical final AssistantMessage 完成 Run
```

如果上游实现与这些语义不完全一致：

> 修改 vendored implementation。

不得反过来修改 RoboAgent protocol 去迁就上游。

---

## 4.14 Parser 可以参考或迁移

如果 smolagents 已有成熟 Code parser / fenced-code parser：

> 可以直接参考或迁移 scanner 实现。

但最终 parser 行为必须以本文为准。

至少必须覆盖：

```text
zero Python block

one Python block

multiple Python blocks

empty Python block

malformed Python block

python3

python attributes

LF

CRLF

EOF closing

closing fence length

non-Python fences
```

如果上游 parser：

```text
宽松接受 python3
允许多个代码块
允许不完整 fence
```

则必须适配为 RoboAgent V1.3 grammar。

---

## 4.15 Prompt 可以参考 smolagents

Coding prompt 属于 application policy。

因此可以参考 smolagents CodeAgent prompt 中成熟的行为约束，例如：

```text
使用代码作为 action

通过工具观察环境

执行后读取 observation

失败后修正

最终通过 final_answer 完成
```

但 RoboAgent Coding Prompt 必须额外符合本项目约束：

```text
调查后修改

修改前读取相关源码

优先最小修改

尊重已有 architecture/style

使用 Tool 验证事实

修改后运行测试

测试失败继续诊断

不要声称执行过未执行的命令

结束前确认修改与测试状态

避免无关重构
```

Restricted Mode 还必须说明：

```text
filesystem
shell
network
external capability
```

只能通过 RoboAgent Tools。

---

## 4.16 smolagents 之外的参考项目

smolagents 是 V1.3 Coding implementation 的首选直接代码来源。

其它项目主要作为设计参考：

### Pi

参考：

```text
Coding Harness 与 Runtime 分层
CLI/TUI application layer
```

不直接要求代码迁移。

### OpenAI Agents SDK

参考：

```text
Agent-as-Tool
tool execution
approval
shell/apply_patch
```

### Hermes Agent

参考：

```text
terminal UX
approval UX
session interaction
```

### DeepAgents / DeerFlow

参考：

```text
长任务 harness
subagent
context engineering
```

但不迁移其 workflow/middleware Runtime 到 RoboAgent Core。

---

## 4.17 上游优先、协议优先

实现决策顺序：

```text
成熟上游已有符合需求的 implementation
→ 优先复用

上游实现稍有差异但可适配
→ vendoring + 修改

上游实现与 RoboAgent Runtime contract 冲突
→ RoboAgent protocol 优先

没有成熟上游实现
→ 本地实现
```

原则：

> 不为了“自己写”而重新发明成熟功能。

同时：

> 不为了“复制方便”而改变 RoboAgent Runtime 边界。

---

## 4.18 可以复制实现，不能复制 Runtime 边界

最终原则可以简化为：

```text
可以复制实现
不能复制 Runtime 边界
```

例如：

```text
smolagents LocalPythonExecutor
        ↓
vendoring / adapt
        ↓
RoboAgent Python Worker evaluator
```

允许。

```text
smolagents CLI
        ↓
vendoring / adapt
        ↓
RoboAgent Coding CLI
```

允许。

```text
smolagents MultiStepAgent
        ↓
替换 RoboAgent AgentLoop
```

禁止。

---

## 4.19 上游 License 与 Attribution

任何：

```text
直接复制
vendoring
明显派生
大段改编
```

自 smolagents 的代码，都必须遵守其适用开源许可证要求。

必须保留适用：

```text
copyright notice
license notice
Apache-2.0 notice
```

---

## 4.20 NOTICE.md

必须建立：

```text
examples/coding/NOTICE.md
```

至少记录：

```text
Upstream repository
Upstream commit/tag
Original source path
Local destination path
License
Modification summary
```

示例：

```text
Upstream:
  https://github.com/huggingface/smolagents

Commit:
  <commit-sha>

Source:
  src/smolagents/local_python_executor.py

Destination:
  examples/coding/executor.py

License:
  Apache-2.0

Changes:
  - removed smolagents Tool abstraction
  - routed tool calls through RoboAgent CodeToolBridge
  - moved execution into worker process
  - adapted persistent state to CodingSession lifecycle
  - adapted final_answer to RoboAgent execute_python protocol
```

---

## 4.21 Vendored source header

直接迁移的 source file 应在文件头保留必要：

```text
Upstream source
Copyright
License
Local modification notice
```

不得去掉上游 attribution 后声称完全原创。

---

## 4.22 Upstream pinning

迁移代码必须记录：

```text
具体 commit SHA
或明确 tag + commit
```

不能只写：

```text
latest smolagents
```

否则后续无法审计代码来源。

---

## 4.23 Upstream 更新策略

V1.3 不自动同步 upstream。

后续更新：

```text
人工查看 upstream diff
↓
确认仍符合 RoboAgent contract
↓
选择性同步
↓
更新 NOTICE
```

禁止：

```text
自动覆盖本地 adapted executor/parser/CLI
```

---

## 4.24 CodingSession

每个 CodingSession handle 独立拥有：

```text
RoboAgent Session
derived Coding Agent
CodingModelAdapter
execute_python Tool instance
Python worker client
worker_generation
interpreter state
reset state
ToolRegistry overlay
observation artifact ownership
```

多个 CodingSession 即使共享同一个 base Agent：

```text
worker 不共享
interpreter 不共享
execute_python closure 不共享
provider budget state 不共享
```

---

## 4.25 CodingSession 正式 API

```python
class CodingSession:
    @property
    def session(self) -> Session:
        ...

    async def run(
        self,
        message: UserMessage | str,
        *,
        run_config: RunConfig | None = None,
    ) -> RunResult:
        ...

    async def steer(
        self,
        message: UserMessage | str,
    ) -> None:
        ...

    async def close(
        self,
    ) -> None:
        ...
```

---

## 4.26 create_coding_session

冻结：

```python
coding_session = create_coding_session(
    base_agent,
    ...
)
```

职责：

1. derive immutable Agent composition；
2. 创建当前 CodingSession 专属 CodingModelAdapter；
3. 创建当前 CodingSession 专属 execute_python Tool；
4. 构造 ToolRegistry overlay；
5. 创建普通 RoboAgent Session；
6. 初始化 CodingSession state；
7. lazy-start worker。

禁止：

```text
global session_id → worker registry
```

---

## 4.27 CodingRunState

Provider call budget 是：

> 每个 Coding Run。

因此：

```python
@dataclass
class CodingRunState:
    run_id: str
    max_provider_calls: int
    provider_calls_used: int = 0
```

它属于：

```text
Coding application state
```

不是 Core RunState。

---

## 4.28 Per-Run identity

`CodingSession.run()`：

1. 创建新的 Coding Run；
2. 创建对应 CodingRunState；
3. Adapter 在该 Run 生命周期绑定此 state；
4. Run 完成后销毁 state。

因此 CodingModelAdapter：

> 不从 ModelContext 猜 run_id。

---

## 4.29 max_provider_calls

默认：

```text
16
```

必须：

```text
type(value) is int
bool 拒绝
>= 1
```

所有实际 Provider call 都计入：

```text
正常 action generation
protocol retry
final+steer 后重新调用
```

Local final：

```text
不消耗
```

超过：

```text
coding_provider_budget_exceeded
```

---

## 4.30 Interpreter state

定义：

> process-local, CodingSession-handle-scoped, non-durable interpreter state。

同一个 CodingSession 多次 Run：

```text
默认复用
```

Durable Session restore：

```text
transcript 恢复
worker state 不恢复
```

---

## 4.31 Worker reset

以下 reset：

```text
worker crash
worker generation protocol failure
active execution force-kill
durable restore
```

然后：

```text
worker_generation += 1
pending_reset_notice = true
```

---

## 4.32 Reset notice

下一次 Provider request 必须看到：

```text
Interpreter state has been reset.
Previously created Python variables are no longer available.
```

Provider 成功完成一个合法 response 后：

```text
clear notice
```

Provider timeout/failure/cancel：

```text
notice 保留
```

---

## 4.33 Provider 不看到 canonical tools

Provider-visible：

```text
tools = ()
```

Provider 不看到：

```text
read_file
write_file
shell
apply_patch
execute_python
```

native ToolDefinition。

Tool abilities 以：

```text
Python callable signatures
```

写入 Prompt。

---

## 4.34 Tool Signature Prompt

在：

```text
CodingSession/Agent construction
```

时加入 canonical PromptInput。

必须早于：

```text
ContextManager.prepare()
```

因此 tool signature token：

```text
纳入 V1.2 ContextBudget
```

不能在 Budget 计算后追加无界说明。

---

## 4.35 Provider Context Projection

CodingModelAdapter 接收：

```text
Prepared ModelContext
```

并把 canonical segments 投影成 Provider 能可靠理解的普通角色内容。

Canonical Session transcript 本身不修改。

---

## 4.36 Message Segment Projection

普通：

```text
System/Developer/User/Assistant
```

保持其原始权限语义。

Coding execute_python ToolExchange：

```text
Assistant:
Python action:
<code>

User-like:
Observation ...
```

---

## 4.37 SummarySegment

投影为：

```text
Conversation summary:
<summary>
```

权限：

> 保持 SummarySegment 在 V1.2 中已有的 ModelContext privilege。

不得因为 projection 自动升级为更高 system 权限。

---

## 4.38 WorkspaceReferenceSegment

投影：

```text
Workspace reference:
<bounded reference metadata>
```

Adapter：

```text
不得主动读取整个 referenced artifact/file
```

除非 canonical ContextManager 已经 materialize 对应内容。

---

## 4.39 Unsupported Segment

未知 ModelContext segment：

```text
unsupported_coding_context_segment
```

不得静默 drop。

---

## 4.40 Provider observation projection：Text

```text
Observation (text):
<raw text>
```

---

## 4.41 JSON

```text
Observation (json):
<canonical deterministic JSON>
```

---

## 4.42 Artifact

```text
Observation (artifact):
uri: ...
media_type: ...
size: ...
digest: ...
preview: ...
```

---

## 4.43 Tool Error

```text
Observation (tool_error):
code: ...
retryable: true|false
message: ...
```

---

## 4.44 Multiple ToolContent blocks

```text
Observation block 1:
...

Observation block 2:
...
```

保持原顺序。

不使用：

```text
可被输出注入闭合的 XML-like delimiter
```

---

## 4.45 Provider projection reserve

固定：

```text
projection_reserve_tokens = 256
```

由当前 Provider/ContextManager token estimator 估算。

Priority：

```text
1. interpreter reset fact
2. protocol correction
3. wrapper labels
```

超过：

1. wrapper 使用最小固定文本；
2. correction 使用最短模板；
3. reset_reason 截断但 reset fact 保留；
4. 仍超：

```text
coding_projection_budget_exceeded
```

不得静默突破 ContextBudget。

---

## 4.46 PreparedContext recent tail capability

PreparedContext 增加：

```python
recent_tail_complete: bool = True
```

语义：

> 当前 ModelContext 是否完整保留最新 canonical conversation group。

默认 V1.2 ContextManager：

```text
True
```

自定义 ContextManager 如果把最新 group 全部替换为 summary：

```text
False
```

---

## 4.47 Coding final recognition compatibility

CodingModelAdapter 在：

```text
recent_tail_complete == false
```

时：

```text
禁止 local-final recognition
```

并返回：

```text
coding_context_tail_unavailable
```

不得猜：

```text
“看不到 final marker 就是假定没有”
```

---

## 4.48 CodingModelAdapter capabilities

```text
tool_calling = True
parallel_tool_calls = False
```

Provider 本身：

```text
不要求 native tool calling
```

Adapter 自身至少输出：

```text
TEXT
ARTIFACT/FILE（final artifact 时）
```

---

## 4.49 Python fence grammar

只识别严格 fenced Python block。

Opening：

```text
0~3 个前导空格
至少三个 `
紧跟 python（case-insensitive）
python 后只能有空白
直到行结束
```

Closing：

```text
0~3 个前导空格
反引号数量 >= opening
其后只能空白
行结束或 EOF
```

支持：

```text
LF
CRLF
EOF directly after closing fence
```

---

## 4.50 Non-Python fences

合法：

````text
```javascript
...
```
````

视作：

```text
普通 assistant text
```

不属于 parser error。

如果没有 Python fence：

```text
zero Python block
```

---

## 4.51 `python attr`

例如：

````text
```python attr
...
```
````

因为显式声明 Python 但 grammar 非法：

```text
malformed_python_block
```

---

## 4.52 python3

````text
```python3
```
````

不是 `python` identifier。

作为：

```text
普通非-Python fence
```

---

## 4.53 Code parser outcomes

零 Python block：

```text
normal final text
FinishReason.STOP
```

一个 Python block：

```text
execute_python ToolCall
FinishReason.TOOL_CALL
```

多个：

```text
multiple_python_blocks
```

空：

```text
empty_python_block
```

未闭合：

```text
malformed_python_block
```

---

## 4.54 LENGTH / CONTENT_FILTER

Provider response：

```text
FinishReason.LENGTH
FinishReason.CONTENT_FILTER
```

禁止执行任何 Python block。

即使 fenced block 看起来完整。

---

## 4.55 Native Provider ToolCall

Provider 返回 native ToolCall：

```text
coding_provider_tool_call_not_allowed
```

不透传、不自动执行、不自动转换。

---

## 4.56 Protocol retry

默认：

```text
max_protocol_retries = 1
```

Invalid：

```text
buffer Provider attempt
↓
validate
↓
invalid
↓
Adapter-local correction
↓
retry
```

Correction 不进入 canonical transcript。

---

## 4.57 Retry streaming buffering

强制：

> Provider attempt 在被确认合法之前，不得向 canonical stream emit 任何 response event。

每次 attempt：

```text
完整 buffering
↓
finish reason validation
↓
parser validation
```

Invalid attempt：

```text
不发 ResponseStarted
不发 TextDelta
不发 ToolCall events
```

但 usage：

```text
仍计入
```

最终合法 attempt：

```text
Adapter 重新合成唯一 canonical stream
```

只有一个：

```text
ResponseStarted
ResponseCompleted
```

---

## 4.58 Python code 与 TextDelta

Python fenced block：

> 不进入 canonical AssistantMessage.content。

Code 只进入：

```text
ToolCall.arguments.code
```

UI 可从：

```text
ToolCallArgumentsDelta
```

显示。

Python fence 外 natural-language reasoning：

```text
可以进入 AssistantMessage.content
```

---

## 4.59 execute_python

Tool 只存在：

```text
examples/coding
```

不进入 Core Builtin。

Schema：

```json
{
  "type": "object",
  "properties": {
    "code": {
      "type": "string",
      "minLength": 1
    }
  },
  "required": ["code"],
  "additionalProperties": false
}
```

---

## 4.60 execute_python effect mode

```text
effect_reporting = COMPOSITE
```

Restricted：

```text
如果 exposed Python Tool 中任一 SIDE_EFFECTING
→ static SIDE_EFFECTING
否则 READ_ONLY
```

Trusted：

```text
永远 SIDE_EFFECTING
```

---

## 4.61 execute_python control protocol

```text
roboagent.coding.execute_python/v1
```

普通：

```json
{
  "protocol": "roboagent.coding.execute_python/v1",
  "execution_status": "ok",
  "is_final": false,
  "observation": "...",
  "observation_file": null,
  "interpreter_reset": false,
  "reset_reason": null
}
```

Control envelope：

```text
永远 inline
strictly bounded
不可整体 materialize
```

---

## 4.62 Large observation size flow

```text
worker stdout
↓
max_stdout_bytes
↓
construct observation
↓
max_observation_bytes
↓
inline_observation_bytes
```

默认：

```text
inline_observation_bytes = 16 KiB
```

---

## 4.63 Observation artifact storage

超过 inline threshold：

> Host CodingSession 写入专用临时目录。

```text
.roboagent/artifacts/
```

这属于：

```text
Harness-owned observation storage
```

不产生用户 filesystem ToolEffect。

---

## 4.64 Observation ID

格式：

```text
obs_<session-random-id>_<monotonic-counter>
```

不得使用可冲突随机短 ID。

---

## 4.65 Observation session quota

CodingSession：

```text
max_observation_artifact_bytes_per_session
=
256 MiB
```

超：

```text
observation_storage_limit_exceeded
```

---

## 4.66 Observation artifact lifecycle

写入成功后：

```text
ORPHANED
```

当对应 execute_python ToolExchange 成功 commit：

```text
REFERENCED
```

如果 commit 失败：

```text
保持 ORPHANED
```

CodingSession.close：

```text
删除所有自身拥有的 observation artifacts
```

包括：

```text
ORPHANED
REFERENCED
```

因为它们只属于 process-local CodingSession observation cache。

Durable Session restore：

> 不保证旧 observation artifacts 仍存在。

---

## 4.67 final_answer

`final_answer()`：

> AST evaluator intrinsic。

不是普通 Python callable。

Restricted 与 Trusted：

```text
使用相同 completion semantics
```

Trusted 不切裸 `exec()`。

---

## 4.68 final_answer control flow

第一次成功调用：

```text
设置 out-of-band completion state
```

随后：

```text
当前 expression 剩余 operand 不执行
后续普通 statement 不执行
control flow unwind
finally blocks 执行
```

`except BaseException`：

```text
不能清除 evaluator completion state
```

---

## 4.69 final supported types

```text
None
str
bool
int
float
JSON-compatible list
JSON-compatible dict
ArtifactHandle
```

拒绝：

```text
custom object
generator
callable
file object
exception
NaN
Infinity
-Infinity
```

错误：

```text
final_answer_not_serializable
```

---

## 4.70 ArtifactHandle

冻结：

```python
@dataclass(frozen=True)
class ArtifactHandle:
    uri: str
    media_type: str | None
    size: int
    digest: str
    preview: str | None
```

`digest`：

```text
必填
```

如果 canonical ArtifactReference 没有 digest：

> Host 在映射为 ArtifactHandle 前计算。

无法计算：

```text
artifact_digest_unavailable
```

---

## 4.71 final text schema

```json
{
  "kind": "text",
  "value": "done"
}
```

---

## 4.72 final json schema

```json
{
  "kind": "json",
  "value": {
    "ok": true
  }
}
```

---

## 4.73 final artifact schema

```json
{
  "kind": "artifact",
  "value": {
    "uri": "...",
    "media_type": "...",
    "size": 100,
    "digest": "sha256:...",
    "preview": null
  }
}
```

---

## 4.74 final empty schema

```json
{
  "kind": "empty",
  "value": null
}
```

`kind` 和 value 类型不匹配：

```text
invalid_final_envelope
```

---

## 4.75 Final execute_python envelope

```json
{
  "protocol": "roboagent.coding.execute_python/v1",
  "execution_status": "ok",
  "is_final": true,
  "final": {
    "kind": "text",
    "value": "done"
  }
}
```

---

## 4.76 Final recognition

只有：

```text
最新 canonical group 是 execute_python ToolExchange
ToolResult status == SUCCESS
protocol exact match
is_final == true
recent_tail_complete == true
其后没有 UserMessage / steer
```

才能 local final。

---

## 4.77 final + steer

```text
execute_python(is_final=true)
↓
steer arrives
```

final marker：

```text
失效
```

下一轮重新调用 Provider。

如果 provider budget 已耗尽：

```text
coding_provider_budget_exceeded
```

---

## 4.78 Local final

不调用 Provider。

产生合法：

```text
ResponseStarted
TextDelta*
UsageUpdated(0)
ResponseCompleted
```

FinishReason：

```text
STOP
```

Usage：

```text
Usage(0,0,0)
```

after-model hooks：

```text
正常执行
```

---

# 5. Python Worker、IPC 与安全

## 5.1 Worker ownership

Worker：

```text
属于 CodingSession handle
```

Run scope 只拥有：

```text
active execution request
IPC execution lease
nested Tool tasks
```

正常 Run 完成：

```text
释放 execution lease
保留 idle worker
```

---

## 5.2 Worker reset conditions

```text
worker crash
IPC generation-fatal failure
active execution force-killed
CodingSession close
durable restore
```

Cancel 一个没有 active worker execution 的 Run：

> 不杀 idle worker。

---

## 5.3 Worker process

使用独立：

```text
process
```

不使用：

```text
asyncio.to_thread()
```

作为可取消隔离。

---

## 5.4 Worker limits

默认：

```text
startup_timeout                    = 10 s
execution_timeout                  = 120 s

max_code_bytes                     = 64 KiB
max_stdout_bytes                   = 64 KiB
max_observation_bytes              = 64 KiB
inline_observation_bytes           = 16 KiB
max_final_output_bytes             = 64 KiB

max_ipc_frame_bytes                = 1 MiB
max_tool_requests_per_step         = 64

max_observation_artifact_bytes_per_session = 256 MiB
```

数值配置：

```text
bool 拒绝
必须 > 0
```

---

## 5.5 Effective worker deadline

```text
min(
    Run/root scope deadline,
    execute_python Tool scope deadline,
    now + execution_timeout
)
```

Nested Tool：

```text
min(
    current worker execution deadline,
    nested Tool deadline
)
```

---

## 5.6 Worker startup

```text
spawn
↓
hello
↓
accepted
↓
ready
```

整个流程受：

```text
startup_timeout
```

约束。

超时：

```text
worker_startup_timeout
```

终止/reap generation。

---

## 5.7 IPC framing

```text
4-byte unsigned big-endian length
+
UTF-8 JSON payload
```

---

## 5.8 IPC common envelope

```json
{
  "protocol": "roboagent.coding.worker/v1",
  "worker_generation": 1,
  "execution_id": "",
  "request_id": "",
  "type": "hello",
  "payload": {}
}
```

---

## 5.9 hello identity

```text
worker_generation: required
execution_id: ""
request_id: ""
```

Direction：

```text
worker → host
```

---

## 5.10 accepted identity

```text
worker_generation required
execution_id=""
request_id=""
```

Direction：

```text
host → worker
```

---

## 5.11 ready identity

```text
worker_generation required
execution_id=""
request_id=""
```

Direction：

```text
worker → host
```

---

## 5.12 execute identity

```text
worker_generation required
execution_id non-empty
request_id non-empty
```

`request_id`：

> execute request identity。

---

## 5.13 execute payload

```json
{
  "code": "...",
  "tool_names": [
    "read_file",
    "search_files"
  ]
}
```

---

## 5.14 tool_request identity

```text
worker_generation required
execution_id same as active execution
request_id unique within execution
```

---

## 5.15 tool_request payload

```json
{
  "tool_name": "read_file",
  "arguments": {
    "path": "README.md"
  }
}
```

---

## 5.16 tool_response identity

必须复用对应：

```text
worker_generation
execution_id
request_id
```

---

## 5.17 Tool value union：text

```json
{
  "kind": "text",
  "value": "..."
}
```

---

## 5.18 Tool value union：json

```json
{
  "kind": "json",
  "value": {
    "foo": "bar"
  }
}
```

---

## 5.19 Tool value union：artifact

```json
{
  "kind": "artifact",
  "value": {
    "uri": "...",
    "media_type": "...",
    "size": 100,
    "digest": "sha256:...",
    "preview": null
  }
}
```

---

## 5.20 Tool value union：tuple

```json
{
  "kind": "tuple",
  "value": [
    {
      "kind": "text",
      "value": "..."
    },
    {
      "kind": "json",
      "value": {
        "ok": true
      }
    }
  ]
}
```

Tuple item：

```text
不能再次是 tuple
```

---

## 5.21 Tool response success

```json
{
  "ok": true,
  "value": {
    "kind": "text",
    "value": "..."
  }
}
```

---

## 5.22 Tool response error

```json
{
  "ok": false,
  "error": {
    "code": "...",
    "message": "...",
    "retryable": false
  }
}
```

---

## 5.23 execution_result identity

```text
execution_id = original execute execution_id
request_id = original execute request_id
```

---

## 5.24 execution_result payload

```json
{
  "execution_status": "ok",
  "stdout": "...",
  "is_final": false,
  "final": null,
  "interpreter_generation": 1
}
```

Final 使用：

```text
与 final_answer 完全相同的 text/json/artifact/empty union
```

不建第二种 encoding。

---

## 5.25 cancel identity

```text
execution_id = active execution
request_id = execute request ID
```

---

## 5.26 shutdown identity

```text
execution_id=""
request_id=""
```

---

## 5.27 IPC required field validation

以下任何情况：

```text
required field missing
wrong JSON type
invalid enum
identity invalid
illegal empty/non-empty ID
```

均：

```text
ipc_protocol_error
```

---

## 5.28 Direction validation

例如：

```text
worker → execute
host → execution_result
```

均 generation-fatal：

```text
ipc_protocol_error
```

---

## 5.29 Duplicate ToolRequest

同：

```text
execution_id
request_id
payload
```

重复收到：

```text
返回缓存 response
```

不重新执行、不重新 budget。

同 request_id 但 payload 不同：

```text
ipc_protocol_error
generation fatal
```

---

## 5.30 Stale messages

拒绝：

```text
旧 worker_generation
已结束 execution_id
unknown request_id
重复 execution_result
```

---

## 5.31 EOF

Idle worker EOF：

```text
worker exited
```

Active execution EOF：

```text
executor_failure
```

Partial frame：

```text
ipc_protocol_error
generation fatal
```

---

## 5.32 Unknown optional fields

```text
ignore
```

Unknown message type：

```text
generation fatal
```

---

## 5.33 IPC fatality classification

### Execution-fatal

合法协议中的：

```text
tool error
user code error
execution timeout
```

只结束当前 execution。

### Generation-fatal

```text
malformed frame
invalid UTF-8
invalid JSON
unknown message type
direction violation
identity mismatch
conflicting duplicate request
invalid payload schema
stale generation message on active channel
```

Worker generation 不再可信：

```text
terminate/reap
generation += 1
```

---

## 5.34 Host bidirectional loop

Host 发 execute 后：

```text
while active:
    receive frame

    tool_request
        → validate
        → execute nested Tool
        → send tool_response

    execution_result
        → complete execution

    generation-fatal
        → terminate worker
```

不能只：

```python
await execution_result()
```

---

## 5.35 Cancellation 后 ToolRequest

一旦 execution 进入 cancelling：

新 ToolRequest：

```text
不执行
```

返回：

```json
{
  "ok": false,
  "error": {
    "code": "execution_cancelled",
    "message": "Execution is cancelling.",
    "retryable": false
  }
}
```

不视为 protocol violation。

---

## 5.36 Cancellation sequence

```text
mark execution cancelling
↓
stop accepting executable ToolRequest
↓
cancel active host-side nested tools
↓
send cancel
↓
bounded wait
↓
SIGTERM worker process group
↓
bounded wait
↓
SIGKILL
↓
wait/reap
↓
close IPC
↓
collect settled effects
↓
release execution lease
```

Terminate/reap 使用：

```text
Settlement Barrier
```

---

## 5.37 Restricted Mode

默认：

```text
restricted
```

目标：

> 防止正常模型生成的 Python 代码直接绕过 RoboAgent Tool capability boundary。

它不是 hostile-code sandbox。

---

## 5.38 Restricted process environment

```text
cwd = isolated temporary directory

remove provider/API secrets

ignore PYTHONPATH

ignore PYTHONSTARTUP

disable user site

avoid user-controlled sitecustomize

close unrelated FDs
```

---

## 5.39 Restricted builtins

禁止：

```text
open
exec
eval
compile
__import__
input
breakpoint
```

以及 evaluator 已知 dangerous reflection traversal。

---

## 5.40 Restricted imports

固定 allowlist：

```text
math
statistics
json
re
datetime
collections
itertools
functools
```

V1.3 不允许 CLI 任意扩大。

---

## 5.41 Trusted Mode

显式：

```bash
--unsafe-python
```

CLI：

```text
Trusted Python execution is not sandboxed.
Python code may access host resources outside the RoboAgent workspace.
```

Trusted 仍使用：

```text
AST evaluator
```

不是裸 exec。

---

## 5.42 Trusted process group

Worker 使用独立 process group。

Cancel 保证：

> 终止仍属于该 worker process group 的进程。

不保证：

```text
setsid
daemonize
外部 supervisor 接管
```

后的 descendants。

---

## 5.43 Python Tool Schema

只支持 JSON Schema 2020-12 明确子集。

---

## 5.44 Nullable

Nullable 使用：

```json
{
  "type": [
    "string",
    "null"
  ]
}
```

不使用：

```text
nullable=true
```

---

## 5.45 Scalar schema

支持：

```text
string
integer
number
boolean
null
```

---

## 5.46 Array schema

必须：

```text
type=array
items 存在
items 递归满足支持子集
```

没有 items：

```text
tool_not_python_callable
```

---

## 5.47 Object schema

支持：

```text
type=object
properties
required
additionalProperties=false
```

`required`：

```text
必须是 properties 的子集
```

V1.3 不支持：

```text
additionalProperties=true
additionalProperties=<schema>
```

---

## 5.48 Enum

enum value：

```text
必须符合声明 type
```

否则该 Tool 不暴露。

---

## 5.49 Default

Default：

```text
必须通过同一 schema 验证
```

Python wrapper 在省略字段时注入 default。

之后 canonical Tool args：

> 仍经过 ToolExecutor schema validation。

---

## 5.50 不支持的 schema

```text
oneOf
anyOf
allOf
recursive reference
patternProperties
free-form additionalProperties
```

Tool 不暴露给 Python。

---

## 5.51 Positional args

按：

```text
schema properties declaration order
```

映射。

Prompt 推荐模型使用 keyword args。

---

## 5.52 Alias sanitize

先 Unicode：

```text
NFKC normalize
```

如果已经符合：

```regex
^[A-Za-z_][A-Za-z0-9_]*$
```

且：

```text
不是 Python keyword
不是 reserved
不是 dunder
```

则直接使用。

否则：

1. NFKC normalize；
2. 非 ASCII `[A-Za-z0-9_]` 替换 `_`；
3. 连续 `_` 压缩；
4. 数字开头前置 `_`；
5. 空结果使用 `_tool`；
6. keyword 后缀 `_tool`；
7. alias comparison 区分大小写；
8. exact collision 时不暴露；
9. 不自动追加数字解决 collision。

例如：

```text
git-diff    → git_diff
123.tool    → _123_tool
foo...bar   → foo_bar
```

---

## 5.53 Reserved aliases

禁止：

```text
final_answer
RoboAgentToolError
ArtifactHandle
dunder name
restricted builtin/module names
```

---

## 5.54 execute_python 不暴露给 Worker

即使 registry 中有：

```text
execute_python
```

worker allowlist：

```text
必须排除
```

Host 也再次验证。

---

## 5.55 Agent-as-Tool 可暴露

如果：

```text
schema 可投影
alias 合法
```

Agent Tool 可以成为 Python callable。

仍受：

```text
Agent depth
Child Run budget
Policy
Approval
```

约束。

---

## 5.56 ToolResult → Python

单 Text：

```text
→ str
```

单 Json：

```text
→ JSON-compatible Python value
```

Artifact：

```text
→ ArtifactHandle
```

多个 blocks：

```text
→ tuple
```

Tool error：

```text
→ RoboAgentToolError
```

---

## 5.57 RoboAgentToolError

```python
class RoboAgentToolError(Exception):
    code: str
    message: str
    retryable: bool
```

Python catch 之后：

```text
ExecutionRecord 不删除
actual effects 不删除
```

---

## 5.58 Python user-code errors

```text
SyntaxError
NameError
TypeError
ZeroDivisionError
...
```

属于：

```text
execution observation
```

而不是 Tool infrastructure failure。

Outer Tool：

```text
SUCCESS
```

Envelope：

```json
{
  "execution_status": "error",
  "is_final": false
}
```

---

## 5.59 Infrastructure failure

以下才：

```text
executor_failure
```

```text
worker crash
worker startup failure
IPC corruption
generation protocol failure
host bridge failure
```

已发生 nested effects：

```text
仍保留
```

---

# 6. Filesystem 与 apply_patch

## 6.1 定位

`apply_patch` 是：

> 通用 workspace-constrained filesystem Builtin。

不是 Coding Runtime subsystem。

---

## 6.2 API

```python
@dataclass(frozen=True)
class ApplyPatchConfig:
    filesystem: FilesystemConfig

    max_patch_bytes: int = 256 * 1024
    max_files: int = 64
    max_file_bytes: int = 4 * 1024 * 1024
    max_result_bytes: int = 64 * 1024
```

---

## 6.3 create tool

```python
create_apply_patch_tool(
    ApplyPatchConfig(
        filesystem=FilesystemConfig(...)
    )
)
```

---

## 6.4 Configuration validation

所有 int：

```text
type(value) is int
bool 拒绝
value > 0
```

---

## 6.5 Tool Definition

Name：

```text
apply_patch
```

Description：

> Apply a structured UTF-8 text patch to files inside the configured workspace.

Schema：

```json
{
  "type": "object",
  "properties": {
    "patch": {
      "type": "string",
      "minLength": 1
    }
  },
  "required": ["patch"],
  "additionalProperties": false
}
```

---

## 6.6 Limits units

```text
max_patch_bytes
→ patch UTF-8 encoded bytes

max_files
→ unique normalized targets

max_file_bytes
→ max(preimage bytes, postimage bytes)

max_result_bytes
→ serialized model-visible ToolContent bytes
```

---

## 6.7 Tool effect

```text
effect_reporting = COMPOSITE
effect_kind = SIDE_EFFECTING
```

---

## 6.8 Platform

V1.3 强 workspace/symlink/atomic replacement 保证：

```text
Linux/POSIX only
```

非 POSIX：

```text
apply_patch_platform_unsupported
```

未来可增加平台 backend。

---

## 6.9 Container grammar

```text
*** Begin Patch
<section>+
*** End Patch
```

至少一个 section。

最后：

```text
*** End Patch
```

之后允许：

```text
EOF
或一个 final LF/CRLF
```

不得有其它非空内容。

---

## 6.10 Header exact prefixes

固定：

```text
*** Add File: 
*** Update File: 
*** Delete File: 
```

冒号后：

```text
恰好一个 grammar delimiter space
```

Path：

> 从 delimiter 之后开始。

例如：

```text
*** Add File: foo.py
```

path：

```text
foo.py
```

---

## 6.11 Duplicate target

一个 normalized target：

```text
只允许一个 section
```

任何重复：

```text
duplicate_patch_target
```

不支持：

```text
compatible duplicate section
```

---

## 6.12 Add File

```text
*** Add File: path
+line
+line
```

每个 body 普通 line：

```text
必须以 + 开头
```

允许：

```text
+
```

代表空行。

不允许：

```text
context/remove line
```

---

## 6.13 Add target exists

Pre-validation：

```text
目标必须不存在
```

已存在：

```text
patch_target_exists
```

commit 前：

> 再次确认目标仍不存在。

外部并发创建：

```text
patch_target_changed
```

不得覆盖。

---

## 6.14 Add parent directory

不自动创建 parent directory。

不存在：

```text
parent_directory_missing
```

---

## 6.15 Add mode

默认：

```text
0o644 & ~process_umask
```

实现不得为了计算它临时修改全局 process umask。

使用安全 create API 得到等价结果。

---

## 6.16 Delete File

```text
*** Delete File: path
```

Delete section：

```text
不允许 body
```

目标：

```text
必须存在
必须为 regular UTF-8 text file
```

不存在：

```text
patch_target_missing
```

---

## 6.17 Update File

```text
*** Update File: path
@@
 context
-old
+new
```

---

## 6.18 Update mode

Update：

```text
严格保留 existing mode
```

---

## 6.19 Hunk header

```text
@@
```

必须独占一行。

允许 trailing whitespace。

V1.3：

```text
不支持 @@ -1,4 +1,4 @@
```

line-number syntax。

---

## 6.20 Hunk lines

Context：

```text
<space><text>
```

Remove：

```text
-<text>
```

Add：

```text
+<text>
```

---

## 6.21 No final newline marker

```text
\ No newline at end of file
```

只能紧跟一个：

```text
context
remove
add
```

逻辑行。

不得连续出现两个 marker。

---

## 6.22 Multi-hunk order

第一个 hunk：

```text
在 original preimage 上匹配
```

后续 hunk：

```text
在前一个 hunk 产生的 postimage 上匹配
```

顺序固定。

---

## 6.23 Hunk non-empty

每个 hunk 至少含一个：

```text
context
remove
add
```

line。

---

## 6.24 Unique matching

Hunk context：

```text
必须唯一匹配
```

0 match：

```text
patch_conflict
```

> 1 match：

```text
patch_conflict
```

不做 fuzzy apply。

---

## 6.25 Path grammar

Path：

```text
UTF-8
relative
non-empty
```

拒绝：

```text
absolute
NUL
embedded newline
"." component
".." component
trailing separator
ambiguous repeated separator
trailing whitespace
```

---

## 6.26 NUL

即使字符串整体 valid UTF-8，只要包含：

```text
U+0000
```

也返回：

```text
unsupported_binary_content
```

Patch input/file contents/path 都拒绝 NUL。

---

## 6.27 Encoding

只支持：

```text
UTF-8 text
```

拒绝：

```text
UTF-8 BOM
invalid UTF-8
binary content
```

---

## 6.28 Newline counting

先识别：

```text
CRLF 作为一个 newline unit
```

其中 LF：

```text
不再次计数
```

---

## 6.29 Dominant newline

```text
LF count > CRLF count
→ LF

CRLF count > LF count
→ CRLF

tie
→ first newline unit encountered

没有 newline
→ LF
```

---

## 6.30 Add newline

新增文件默认：

```text
LF
```

除非 no-final-newline marker 明确最后行无 terminator。

---

## 6.31 Update newline

未修改区域：

```text
尽可能保留原 bytes
```

新增/替换行：

```text
使用 dominant newline
```

---

## 6.32 Transaction pipeline

固定：

```text
parse
↓
normalize targets
↓
validate lexical paths
↓
workspace/symlink validation
↓
load preimages + modes
↓
calculate preimage digests
↓
detect target aliases
↓
prepare all postimages
↓
predict bounded model-visible result
↓
revalidate targets
↓
stage
↓
Settlement Barrier
    ↓
    commit
    or rollback
↓
publish final effects
```

---

## 6.33 Alias detection

拒绝：

```text
normalized duplicate
same inode/hardlink
platform case-fold alias
```

错误：

```text
duplicate_patch_target
```

---

## 6.34 Symlink safety

拒绝：

```text
target symlink
parent symlink workspace escape
```

Linux/POSIX 实现优先：

```text
dirfd/openat
O_NOFOLLOW
validated parent components
```

---

## 6.35 Commit 前 parent revalidation

Commit 前必须再次验证：

```text
parent still exists
parent chain未变成symlink escape
target assumptions仍成立
alias assumptions仍成立
```

失败：

```text
patch_target_changed
```

---

## 6.36 Optimistic concurrency

Preimage 读取时记录：

```text
SHA-256 digest
```

Commit 前：

```text
再次验证 digest
```

变化：

```text
patch_target_changed
```

这是：

> optimistic concurrency。

不承诺抵抗 adversarial writer 在最后 validation 和 replace 之间持续 race。

---

## 6.37 Digest format

```text
sha256:<64 lowercase hexadecimal>
```

Add：

```text
before_digest=null
```

Delete：

```text
after_digest=null
```

---

## 6.38 Result size 必须 commit 前闭合

apply_patch result 只能包含：

```text
paths
operations
counts
truncated marker
```

不包含完整 diff。

在 commit 前：

> 构造或计算最终 serialized result upper bound。

如果即使 bounded summary 都超过：

```text
max_result_bytes
```

返回：

```text
patch_result_too_large
```

不得发生 side effect。

---

## 6.39 Bounded result fallback

允许：

```json
{
  "files": [],
  "files_omitted": 64,
  "added": 20,
  "modified": 40,
  "deleted": 4,
  "truncated": true
}
```

Fallback 也必须在 commit 前证明：

```text
<= max_result_bytes
```

---

## 6.40 Settlement Handler for patch

apply_patch 必须提供具体：

```text
PatchSettlementHandler
```

给：

```python
context.execution.settlement_barrier(
    handler=...
)
```

它必须知道：

```text
commit state
rollback journal
staged paths
preimages
```

因此 Runtime 在 timeout 时有真正的：

```text
force_settle()
```

入口。

---

## 6.41 Commit cancellation

进入 settlement barrier 后：

```text
cancellation 被记录
```

但 Run 不允许在：

```text
commit / rollback
```

尚未收敛时返回 cancellation。

---

## 6.42 Rollback

Commit 中途失败：

```text
必须尝试 rollback
```

---

## 6.43 Rollback success

如果 logical file state 已恢复：

```text
返回原 patch failure
```

不发布成功 side-effect ToolEffect。

---

## 6.44 Rollback incomplete

```text
patch_rollback_failed
```

每个 target：

```text
restored
unchanged
committed
unknown
```

这些是 patch settlement states，不是 ExecutionRecordStatus。

---

## 6.45 Rollback summary record

```json
{
  "patch_target_states": [
    {
      "path": "foo.py",
      "state": "restored"
    }
  ]
}
```

作为：

```text
SupplementalExecutionRecord / SUMMARY
```

---

## 6.46 Rollback committed target effect

```text
ToolEffectStatus.SUCCEEDED
certainty=CERTAIN
```

Evidence：

```json
{
  "path": "foo.py",
  "rollback_state": "committed"
}
```

---

## 6.47 Rollback unknown effect

```text
ToolEffectStatus.UNKNOWN
certainty=UNKNOWN
```

并：

```text
retry_safe=false
```

---

## 6.48 restored / unchanged

不产生 side-effect success ToolEffect。

只产生：

```text
rollback SUMMARY evidence
```

---

## 6.49 Effect observation boundary

V1.3 filesystem effect truth 明确定义为：

```text
logical file content
file existence
file mode
```

不包括：

```text
inode identity
mtime
ctime
ownership
ACL
xattrs
filesystem allocation layout
```

因此 rollback 后：

```text
content/existence/mode
```

恢复，即可视为 V1.3 logical rollback success。

即使 mtime/inode 改变，也不产生 V1.3 logical ToolEffect。

---

## 6.50 Success effects

整个 patch 成功后：

> 每个被修改文件一个 effect。

```json
{
  "path": "foo.py",
  "operation": "modified",
  "before_digest": "sha256:...",
  "after_digest": "sha256:..."
}
```

Operation：

```text
added
modified
deleted
```

---

## 6.51 Multi-file success effect publish

Effects：

> 只有整个 invocation commit 成功后统一 publish。

不得：

```text
第一个文件成功就先 publish effect
```

---

# 7. 测试、CI 与交付

## 7.1 测试原则

核心协议测试必须：

```text
deterministic
fake-model based
fake-tool based
fake-worker based
```

普通 PR CI：

```text
不依赖真实 LLM
```

---

## 7.2 Context compatibility tests

必须：

```text
旧 RunContext 构造仍合法
旧 ToolContext 三参数构造仍合法

Runtime-created RunContext.execution != None
Runtime-created ToolContext.execution != None

ToolContext.run_id == execution_run_id
ToolContext.cancellation identity一致
```

---

## 7.3 RunConfig tests

覆盖：

```text
V1.3所有默认值
旧字段顺序
新字段追加

bool rejection
zero rejection
negative rejection

execution_budget
settlement_timeout
cleanup_timeout
max_execution_records
max_record_evidence_bytes
max_child_artifact_bytes
```

---

## 7.4 Scope tests

```text
OPEN
CLOSING
FROZEN

CLOSING可以settlement contribution
CLOSING拒绝new execution
FROZEN拒绝all contribution
```

---

## 7.5 Sequence tests

```text
scope_sequence root-wide unique
event sequence root-wide unique
record sequence root-wide unique

三类sequence互不复用

并发Tool scope按canonical ToolCall order
```

---

## 7.6 Contribution tests

```text
ContributionId unique

same ID same content
→ ignore

same ID different content
→ invariant violation
```

---

## 7.7 Usage tests

```text
ABSENT
UNKNOWN
KNOWN

partial-known Usage

ModelResponse.usage=None
→ UNKNOWN

no Model call
→ ABSENT

RunResult.usage mapping
RunResult.usage_known mapping

protocol retry usage
cancelled partial usage

UNKNOWN不发UsageUpdated
local final发UsageUpdated(0)
```

---

## 7.8 Composite Tool tests

```text
CompositeToolOutcome type accepted

canonical ToolContent validation

Composite content不再次materialize

Agent-as-Tool nested effects不重复

execute_python nested effects不重复

apply_patch outcome effects被注册

outer Tool terminal record自动生成

supplemental records只允许SUMMARY

after_tool hook failure后effect保留
```

---

## 7.9 Effect tests

```text
EffectIdentity stable
effect ordering deterministic
root commit通过effect_id更新

EffectCertainty合法矩阵

invalid certainty combination拒绝
```

---

## 7.10 Retry-safe truth table

```text
READ_ONLY success
READ_ONLY failure

SIDE_EFFECTING success + committed
SIDE_EFFECTING success + uncommitted

FAILED + CERTAIN_NO_EFFECT
FAILED + UNKNOWN

CANCELLED + CERTAIN_NO_EFFECT
CANCELLED + UNKNOWN

TIMED_OUT + CERTAIN_NO_EFFECT
TIMED_OUT + UNKNOWN

UNKNOWN effect

Settlement RetryBlocker
TrustedExecution RetryBlocker
CleanupUncertain RetryBlocker
```

---

## 7.11 Settlement tests

```text
normal settle

cancellation during barrier

settlement timeout

force_settle success

force_settle failure

body exception

settlement handler exception

effects为空但RetryBlocker存在
→ retry_safe=false

Run不得在barrier未结束前terminal
```

---

## 7.12 Cleanup tests

```text
resource registration OPEN only

LIFO cleanup

graceful close

force_close

cleanup timeout

force_close unsupported

CleanupError

terminal event在cleanup之后
```

---

## 7.13 ExecutionRecord tests

```text
一Tool一terminal TOOL record

SUMMARY record

record sequence

arguments digest

redactor

redactor failure fail-closed

arguments_preview size

evidence oversize envelope

record overflow

execution_records_complete false
```

---

## 7.14 Audit truth tests

当：

```text
execution_records_complete=true
```

claim 可：

```text
verified
contradicted
```

当 false：

```text
允许 unverifiable
```

---

## 7.15 Agent-as-Tool input tests

```text
empty task
whitespace task
normal task
```

---

## 7.16 Child factory tests

```text
default factory

factory throws

custom factory valid

wrong Agent

closed Session

active Run

non-empty transcript

non-empty pending queue

invalid Session ownership + close
```

---

## 7.17 Child lifecycle tests

```text
child start failure

child COMPLETED

child FAILED

parent cancellation

external child cancellation

promotion before cleanup

cleanup failure

session close ordering
```

---

## 7.18 Child usage/effect tests

```text
child usage exactly-once

child effects exactly-once

child failed effects retained

Agent Tool outcome.effects empty
```

---

## 7.19 Artifact promotion tests

```text
Text/Json不promotion

Image
Audio
File
ArtifactReference

streaming copy

64MiB limit

cancel → abort temp

digest mismatch

atomic publish

promotion success + cleanup failure

child URI不直接透传
```

---

## 7.20 smolagents migration tests / checks

如果实现直接迁移 smolagents 代码，至少增加静态检查：

```text
examples/coding 中不得依赖 smolagents.MultiStepAgent

不得依赖 smolagents.CodeAgent Runtime loop

不得依赖 smolagents.AgentMemory 作为 canonical state

不得使用 smolagents ToolExecutor 替代 RoboAgent ToolExecutor

不得直接调用 migrated Tool handler

Vendored executor 中 Tool invocation 必须走 CodeToolBridge

NOTICE.md 必须存在

NOTICE.md 必须记录 pinned upstream commit
```

---

## 7.21 CLI migration tests

如果 CLI 来源于 smolagents：

```text
CLI只消费 RoboAgent public events/results

CLI不直接驱动 Model call

CLI不直接调 Tool handler

CLI不维护第二份 transcript

CLI不维护第二份 Agent step memory

CLI steer使用 RoboAgent Session/Run API

CLI cancellation使用 RoboAgent Run cancellation
```

---

## 7.22 Executor migration tests

如果 evaluator vendored from smolagents：

```text
persistent state 属于 CodingSession

worker reset 清空 interpreter state

Tool调用走IPC

final_answer符合RoboAgent intrinsic语义

restricted imports符合本文allowlist

上游代码中任何host filesystem直接访问在Restricted模式不可用
```

---

## 7.23 Parser migration tests

如果 parser 参考 smolagents：

```text
必须通过RoboAgent全部fence grammar tests

上游额外宽松行为不能自动保留

python3不作为Python action

python attributes malformed

multiple Python blocks按本文报错
```

---

## 7.24 CodingSession identity tests

```text
两个CodingSession同base Agent
→ worker隔离
→ interpreter隔离

每次run创建新CodingRunState

provider counter每Run reset

同Session active Run invariant
```

---

## 7.25 Provider projection tests

```text
normal messages

execute_python ToolExchange

Text observation

Json observation

Artifact observation

Tool error

multiple contents

SummarySegment

WorkspaceReferenceSegment

unsupported segment
```

---

## 7.26 Context tail tests

```text
recent_tail_complete=true
→ final recognition allowed

false
→ coding_context_tail_unavailable
```

---

## 7.27 Projection budget tests

```text
normal reserve

long reset reason

protocol correction

wrapper minimization

still over 256
→ coding_projection_budget_exceeded
```

---

## 7.28 Parser tests

```text
zero Python block

one Python block

multiple blocks

empty block

unclosed block

javascript fence → text

python3 fence → text

python attr → malformed

LF

CRLF

EOF closing

closing indentation

closing trailing text

longer opening fence
matching/longer closing fence
```

---

## 7.29 FinishReason tests

```text
Python action
→ TOOL_CALL

normal final
→ STOP

local final
→ STOP

LENGTH
→ no execution

CONTENT_FILTER
→ no execution
```

---

## 7.30 Adapter retry tests

```text
invalid attempt完全buffer

invalid attempt无ResponseStarted

invalid attempt无TextDelta泄漏

retry usage计入

最终只有一套canonical response events
```

---

## 7.31 Provider budget tests

```text
normal provider call

protocol retry

final+steer regeneration

local final no count

per-Run reset

budget exceeded
```

---

## 7.32 Interpreter reset tests

```text
worker crash reset

forced cancel reset

restore reset

Provider failure后notice保留

Provider success后notice清除
```

---

## 7.33 execute_python tests

```text
schema

Restricted static effect

Trusted static SIDE_EFFECTING

Trusted execution RetryBlocker

user-code error → Tool success observation

worker crash → executor_failure
```

---

## 7.34 final_answer tests

```text
str

Json scalar

Json dict/list

ArtifactHandle

None

custom object rejected

NaN rejected

Infinity rejected

current expression停止

later statement不执行

finally执行

BaseException不能吞final

latest ToolExchange

final + steer

local final no Provider

local final Usage(0)

artifact digest required
```

---

## 7.35 Observation artifact tests

```text
inline threshold

atomic write

ID uniqueness

session quota

ToolExchange commit → REFERENCED

commit failure → ORPHANED

CodingSession.close cleanup

durable restore不依赖旧file

no user filesystem ToolEffect
```

---

## 7.36 Worker startup tests

```text
spawn

hello

accepted

ready

hello timeout

ready timeout

protocol mismatch
```

---

## 7.37 IPC message tests

全部：

```text
hello
accepted
ready
execute
tool_request
tool_response
execution_result
cancel
shutdown
error
```

---

## 7.38 IPC identity tests

```text
handshake empty IDs

execute non-empty IDs

execution_result matches execute

tool_response matches request

stale generation

stale execution

unknown request
```

---

## 7.39 IPC ToolValue tests

```text
text
json
artifact
tuple

nested tuple rejected

missing kind

invalid value
```

---

## 7.40 IPC FinalValue tests

```text
text
json
artifact
empty

kind/value mismatch
```

---

## 7.41 IPC fatality tests

```text
malformed frame → generation fatal

invalid JSON → generation fatal

direction violation → generation fatal

identity mismatch → generation fatal

conflicting duplicate → generation fatal

normal Tool error → execution-level

user-code error → execution-level
```

---

## 7.42 Duplicate request tests

```text
same request + same payload
→ cached response

same request + different payload
→ protocol error

budget not double counted
```

---

## 7.43 Cancellation IPC tests

```text
new ToolRequest during cancelling
→ execution_cancelled response

nested tool cancelled

SIGTERM

SIGKILL escalation

reap
```

---

## 7.44 Restricted tests

```text
open unavailable

os unavailable

pathlib unavailable

subprocess unavailable

dynamic import unavailable

secret env removed

PYTHONPATH ignored

PYTHONSTARTUP ignored

user site disabled

temp cwd
```

不是 evaluator escape security proof。

---

## 7.45 Python schema tests

```text
nullable type union

array items required

nested arrays

nested object

additionalProperties=false

additionalProperties=true rejected

invalid required

enum

invalid enum

default

invalid default
```

---

## 7.46 Alias tests

```text
valid ASCII

hyphen

Unicode normalization

digit prefix

multiple illegal chars

empty result

Python keyword

dunder

reserved name

collision
```

---

## 7.47 CodeToolBridge tests

```text
allowed tool

execute_python rejected

non-exposed tool rejected

positional args

keyword args

defaults

ToolTextContent

ToolJsonContent

ArtifactHandle

tuple

RoboAgentToolError

caught Tool error

nested budget

step budget
```

---

## 7.48 apply_patch grammar tests

```text
exact header delimiter

Add

Update

Delete

duplicate target

multi-hunk

multi-file

empty patch

End Patch + EOF

End Patch + newline

trailing content reject
```

---

## 7.49 apply_patch encoding/path tests

```text
UTF-8

BOM reject

invalid UTF-8

NUL reject

absolute path

dot

dotdot

trailing separator

repeated separator

trailing whitespace

embedded newline
```

---

## 7.50 apply_patch newline tests

```text
LF

CRLF

mixed LF dominant

mixed CRLF dominant

tie first newline

no newline

No newline marker
```

---

## 7.51 apply_patch mode tests

```text
Add default mode

Update mode preserve

Delete rollback mode restore
```

---

## 7.52 apply_patch concurrency tests

```text
Update changed after preimage

Delete changed

Add target concurrently created

parent directory removed

parent becomes symlink

hardlink alias

case-fold alias
```

---

## 7.53 apply_patch settlement tests

```text
normal commit

cancel before barrier

cancel during barrier

settle timeout

force rollback success

force rollback failure
```

---

## 7.54 apply_patch rollback tests

```text
full restore

restored

unchanged

committed

unknown

SUMMARY record

UNKNOWN effect

retry_safe false
```

---

## 7.55 apply_patch result tests

```text
normal result

predicted result too large

bounded fallback summary

result never fails after side effect solely due to size
```

---

## 7.56 Integration evaluation

真实模型测试不进入普通 PR CI。

运行：

```text
manual
nightly
release
```

---

## 7.57 Repository Understanding

必须真实执行：

```text
find
search
read
```

并正确解释调用链。

---

## 7.58 Bug Fix

Fixture：

```text
initial failing tests
```

必须：

```text
test
↓
inspect
↓
read/search
↓
edit/patch
↓
test
↓
pass
```

---

## 7.59 Feature Change

```text
理解现有实现
↓
最小功能修改
↓
增加测试
↓
test pass
```

---

## 7.60 Long Context

人为制造：

```text
large source
large logs
multi-turn edit/test
```

必须：

```text
compaction_count > 0
```

并继续正确完成任务。

---

## 7.61 Failure Recovery

覆盖：

```text
Python SyntaxError
Tool schema error
shell non-zero
missing file
patch conflict
worker crash
timeout
```

---

## 7.62 Steering

Steer：

```text
不要修改 module A
```

commit 后：

```text
后续 filesystem runtime effects
不得再修改 module A
```

---

## 7.63 Cancellation

Cancel 完成：

```text
无 active Child Run
无 active nested Tool
无 active execution lease
无当前 execution-owned worker process
```

Idle CodingSession worker：

> 如果与当前 Run 无 active execution，可以保留。

---

## 7.64 Claim verification

最终回答声称：

```text
pytest passed
modified foo.py
```

必须从：

```text
ExecutionRecord
ToolEffectRecord
```

找到证据。

如果 records incomplete：

```text
unverifiable
```

不能伪装 verified。

---

## 7.65 CI

至少：

```bash
ruff check roboagent tests examples/coding
```

新增：

```text
Nested Runtime
Coding adapter
worker
IPC
bridge
```

纳入 mypy。

---

## 7.66 Packaging

Coding optional dependency：

```text
roboagent[coding]
```

至少：

```text
rich
```

smolagents：

```text
不作为 RoboAgent Runtime dependency
```

如果使用其代码：

```text
vendored + adapted
```

---

## 7.67 smolagents dependency 原则

V1.3 推荐：

> 不把整个 `smolagents` package 作为 Coding Agent 的运行时依赖。

原因：

```text
避免引入第二套 Agent Runtime

避免依赖 smolagents Tool/Model/Memory abstraction

减少依赖体积

避免 upstream Runtime API变化影响RoboAgent
```

优先：

```text
选择性 vendoring
```

需要的成熟 implementation。

---

## 7.68 Attribution

必须：

```text
examples/coding/NOTICE.md
```

记录：

```text
upstream repository
commit/tag
source files
license
local modifications
```

---

## 7.69 License regression check

CI / review checklist 应确认：

```text
vendored 文件保留必要 license header

NOTICE source path仍准确

upstream commit存在

删除vendored代码时同步更新NOTICE
```

---

## 7.70 Platform

Coding Reference Agent：

```text
Linux/POSIX only
```

V1.3 apply_patch strong semantics：

```text
Linux/POSIX only
```

Nested Execution Core：

```text
尽量保持平台无关
```

---

## 7.71 CLI

源码树：

```bash
python -m examples.coding
```

支持：

```text
single task
interactive
streaming
code display
tool display
observation display
approval
steer
follow-up
Ctrl-C cancellation
```

CLI 实现：

> 可以直接参考或迁移 smolagents CLI / Rich UI。

但：

> 必须以 RoboAgent Run Events 和 public Runtime API 为数据源。

---

## 7.72 CLI approval UX

显示：

```text
Tool name
bounded arguments preview
ExecutionLineage
effect capability
Agent delegation path
```

Agent Tool outer approval 必须明确：

> 这里只允许启动 Child Agent；其后具体 side-effecting Tool 仍可能再次请求批准。

---

## 7.73 CLI exit codes

```text
0 completed
1 failed
2 cancelled
3 startup/config error
```

---

## 7.74 CLI shutdown

```text
cancel active Run
↓
await settlement / cleanup
↓
close CodingSession
↓
terminate/reap worker
↓
cleanup observation artifacts
↓
close Provider client
↓
close Event subscription
```

---

# 8. 实施计划、验收条件与最终不变量

## 8.1 Phase 1 — Nested Execution Core

实现：

```text
RunContext.execution
ToolContext.execution

backward-compatible construction

ExecutionScope
ExecutionLineage

root-wide sequence allocators

EffectIdentity
ContributionId
ExecutionContribution

UsageKnowledge
UsageContribution
RunResult.usage_known

ExecutionBudget
ExecutionBudgetView

ToolEffectReporting
CompositeToolOutcome
SupplementalExecutionRecord

EffectCertainty
RetryBlocker
retry_safe predicate

SettlementHandler
Settlement Barrier

ExecutionResource
CleanupError

ExecutionRecord
record overflow
record redaction
record completeness

Event lineage
child_run events

RunConfig V1.3 fields
```

---

## 8.2 Phase 1 完成条件

必须全部通过：

```text
V1/V1.1/V1.2 regression

Context backward compatibility

identity invariants

scope lifecycle

sequence uniqueness

Contribution exactly-once

Usage tri-state

Composite Tool flow

effect identity/order

EffectCertainty matrix

retry-safe truth table

Settlement timeout/force-settle

Cleanup

ExecutionRecord public outlet

Audit completeness
```

---

## 8.3 Phase 2 — Agent-as-Tool & apply_patch

实现：

```text
Agent.as_tool

ChildSessionContext
ChildSessionFactory
default factory

run_child_agent

Child lifecycle

Child Run failure mapping

Child output mapping

ArtifactReader
ArtifactDestination
ArtifactWriter

streaming promotion

apply_patch
ApplyPatchConfig
strict grammar
transaction
settlement
rollback
effects
```

---

## 8.4 Phase 2 完成条件

必须：

```text
AgentLoop未增加delegation branch

Agent Tool完全通过ToolExecutor

Child Session完全隔离

Child Run挂入parent execution tree

usage exactly-once

effects exactly-once

approval两层独立

depth/breadth有限

deadline正确

FAILED/CANCELLED lifecycle明确

ArtifactReference也必须promotion

streaming promotion可取消

apply_patch deterministic tests全部通过
```

---

## 8.5 Phase 3 — Coding Harness

只在：

```text
examples/coding/
```

实现：

```text
CodingSession API
CodingRunState

CodingModelAdapter

Provider segment projection

Tool signatures

projection reserve

PreparedContext recent_tail_complete

strict fence parser

buffered protocol retry

provider budget

execute_python

observation artifacts

final_answer intrinsic

Python worker

startup timeout

IPC wire protocol

ToolValue/FinalValue union

CodeToolBridge

Restricted Mode
Trusted Mode

interpreter reset
```

---

## 8.6 Phase 3 上游复用要求

Phase 3 开始前必须先对 smolagents 做一次 source audit。

至少检查：

```text
LocalPythonExecutor

CodeAgent parser

final_answer implementation

authorized imports

interpreter state

CLI

Rich UI
```

然后将每项标记：

```text
REUSE
ADAPT
REWRITE
NOT APPLICABLE
```

原则：

```text
REUSE / ADAPT 优先
REWRITE 需要说明原因
```

避免 Codex 无意义重新实现成熟能力。

---

## 8.7 Phase 3 禁止事项

即使上游代码非常成熟，也禁止迁移：

```text
MultiStepAgent runtime

CodeAgent execution loop

AgentMemory

smolagents Model runtime

smolagents Tool runtime

smolagents planning lifecycle
```

进入 RoboAgent execution path。

---

## 8.8 Phase 3 完成条件

必须：

```text
Provider无需native ToolCalling

ModelContext全部已支持segment有投影

未知segment确定失败

Context tail完整性可检测

Tool signatures在ContextBudget内

Adapter retry不污染stream

Python action FinishReason=TOOL_CALL

Provider budget是per Run

execute_python不暴露给worker

Host重新验证tool allowlist

final_answer不可被catch吞掉

finally执行

local final不调用Provider

Trusted Mode永远retry-unsafe

worker startup/cancel/reap确定

IPC schema完整

smolagents复用边界满足本文要求

NOTICE/License完整
```

---

## 8.9 Phase 4 — CLI & Evaluation

实现：

```text
Rich CLI
Approval UX
stream display
code display
observation display

Steering
Cancellation

Repository Understanding
Bug Fix
Feature Change
Long Context
Failure Recovery

README
NOTICE
```

CLI：

> 应优先基于 smolagents 已有 Rich UI / terminal UX 改造，而不是重新从零设计。

---

## 8.10 Core Definition of Done

必须满足：

```text
公开 Context API 唯一且兼容旧构造

RunConfig 完整冻结

Nested execution 只有一套

scope/event/record sequence唯一

Contribution exactly-once

Nested usage exactly-once

Usage UNKNOWN可穿越Model边界

Composite Tool处理流程闭合

Composite content不二次materialize

Nested effects不重复

Effect identity稳定

Retry-safe有唯一可执行公式

Settlement有force-settle入口

Settlement uncertainty即使无ToolEffect也retry-unsafe

Trusted执行明确retry-unsafe

ExecutionRecord有明确生命周期、overflow、evidence限制

Cleanup在terminal之前完成
```

---

## 8.11 Agent-as-Tool Definition of Done

```text
ChildSessionFactory ownership明确

invalid custom Session会close

Child start failure可表达

Child COMPLETED/FAILED/CANCELLED映射明确

promotion → cleanup → close顺序明确

cleanup failure outer Tool失败

Child ArtifactReference不可直接透传

Artifact promotion streaming

promotion size/cancel/digest语义完整
```

---

## 8.12 Coding Definition of Done

```text
CodingSession拥有正式run/steer/close API

per-Run provider identity明确

Provider Context projection覆盖Message/Summary/WorkspaceReference

PreparedContext可表达recent tail completeness

Parser不支持fence分类确定

Protocol retry buffering不破坏stream

Provider call预算per Run

Observation storage有session quota与lifecycle

Final artifact schema唯一

ArtifactHandle digest必填

Worker startup timeout

Cancellation中新ToolRequest行为确定

IPC required identity完整

ToolValue union完整

FinalValue union完整

fatality classification唯一

Python schema子集唯一

Alias算法唯一

CLI可复用smolagents成熟实现

Evaluator优先复用smolagents实现

Parser可复用但行为以RoboAgent协议为准

smolagents Runtime没有进入RoboAgent Runtime

NOTICE/License完整
```

---

## 8.13 apply_patch Definition of Done

```text
Tool schema唯一

header delimiter唯一

container EOF语义唯一

每个target一个section

Add/Delete/Update body grammar唯一

multi-hunk顺序唯一

NUL与binary语义唯一

mode语义唯一

newline counting唯一

commit前Add target再次验证

parent/symlink再次验证

Linux/POSIX保证明确

result大小在commit前闭合

bounded fallback summary

rollback effect mapping唯一

logical filesystem effect边界明确
```

---

## 8.14 Integration Definition of Done

Coding Reference Agent 必须稳定完成：

```text
Repository Understanding
Bug Fix
Feature Implementation
Test / Debug Loop
Long Context + Compaction
Failure Recovery
Steering
Cancellation
```

Long Context：

```text
compaction_count > 0
```

必须实际发生。

---

## 8.15 最终架构

```text
                         RoboAgent
                    Generic Runtime Kernel
                              │
                       Agent / Session
                              │
                             Run
                              │
                     Root ExecutionScope
                              │
       ┌──────────────────────┼───────────────────────┐
       │                      │                       │
     Model                ToolExecutor          ContextManager
                              │
                 ┌────────────┴─────────────┐
                 │                          │
             Leaf Tool                Composite Tool
                                            │
                       ┌────────────────────┼─────────────────┐
                       │                    │                 │
                  Agent-as-Tool       execute_python      apply_patch
                       │                    │
                   Child Run          Python Worker
                       │                    │
              Child ExecutionScope     CodeToolBridge
                                            │
                                  execute_nested_tool()
                                            │
                                       ToolExecutor
                                            │
                                     Nested Tool Scope
```

---

## 8.16 Coding implementation 来源关系

```text
                   smolagents
                       │
          ┌────────────┼───────────────┐
          │            │               │
       Evaluator     Parser         CLI / Rich UI
          │            │               │
          └────── vendoring/adapt ─────┘
                       │
                       ↓
                examples/coding
                       │
                       ↓
                RoboAgent Runtime
```

注意箭头方向：

```text
smolagents implementation
        ↓
examples/coding
```

而不是：

```text
smolagents Runtime
        ↓
RoboAgent Core
```

---

## 8.17 最终不变量：Runtime 不理解 Coding

AgentLoop 永远不知道：

```text
Python
CodingSession
CodeAction
final_answer
Worker IPC
smolagents
```

---

## 8.18 最终不变量：Nested Execution 只有一套

```text
Child Run
Nested Tool
Composite Tool
```

全部共享：

```text
ExecutionScope
Lineage
Contribution
Budget
Cancellation
Deadline
Settlement
Cleanup
```

---

## 8.19 最终不变量：Transcript 是 conversation fact

以下都不能替代 transcript：

```text
Event
ExecutionRecord
ToolEffectRecord
Interpreter State
RunState
smolagents AgentMemory
```

---

## 8.20 最终不变量：Runtime Effect 是副作用事实

Static effect：

```text
描述 capability
```

Runtime effect：

```text
描述可观察实际副作用
```

对于不可观察的 Trusted host side effects：

```text
通过 RetryBlocker 明确降低保证
```

而不是伪造“无副作用”。

---

## 8.21 最终不变量：Audit 完整性显式表达

```text
RunResult.execution_records_complete
```

决定 ExecutionRecord 是否可视为完整 bounded audit evidence。

---

## 8.22 最终不变量：Worker 属于 CodingSession

Worker 不进入通用：

```text
Session resource registry
RunState
Agent
```

避免为了 Coding Example 污染 Runtime。

---

## 8.23 最终不变量：Restricted Python 不是 Sandbox

Restricted：

> capability-restricted evaluator。

Trusted：

> unsafe local execution。

真正 hostile code：

```text
需要未来独立 OS/container sandbox
```

---

## 8.24 最终不变量：上游复用不改变 Runtime 所有权

即使 `examples/coding` 中：

```text
70%
80%
甚至更多
```

代码来自 smolagents 的直接改编，也不得改变：

```text
AgentLoop ownership

ToolExecutor ownership

Session transcript ownership

ContextManager ownership

Policy/Approval ownership

Effects/Audit ownership
```

成熟实现可以大量复用。

Runtime authority 不能转移。

---

## 8.25 V1.3 Complete

只有以下全部完成：

```text
Phase 1 deterministic tests 全通过

Phase 2 Agent-as-Tool / filesystem tests 全通过

Phase 3 Coding protocol tests 全通过

V1.0–V1.2 regression 全通过

smolagents migration audit 完成

NOTICE / license compliance 完成

Integration evaluation 达标

Long-context compaction evaluation 达标

Cancellation / settlement tests 达标

Runtime-owned force-closeable resources无残留
```

才能标记：

> **RoboAgent V1.3 Complete**

---

## 8.26 最终定位

V1.3 完成后 RoboAgent 仍然不是：

```text
Coding Framework
Deep Agent Framework
Workflow Framework
Multi-Agent Graph Framework
```

而是：

> **一个能够稳定承载 Coding、Research、Robot、Voice、Multimodal 等复杂 Agent Harness 的通用异步 Agent Runtime Kernel。**

`examples/coding` 的作用是：

> **用一个足够复杂的真实 Agent Harness，证明 RoboAgent Runtime 的通用性、可组合性、上下文能力、Nested Execution 语义和生命周期边界，而不是迫使 RoboAgent Runtime 理解 Coding。**

同时，Coding Reference Agent 的实现原则明确为：

> **成熟上游已有的 Coding Harness implementation 应优先复用；尤其是 smolagents 的 Python evaluator、parser、`final_answer()`、interpreter state、CLI 和 Rich UI，不应无意义从零重写。**

最终原则：

```text
可以复制实现
不能复制 Runtime 边界

可以 vendoring Coding Harness
不能 vendoring 第二套 Agent Runtime

上游 implementation 优先
RoboAgent protocol 最终优先
```
