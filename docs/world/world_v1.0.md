# RoboAgent Embodied Integration Architecture

**版本**：V2.4（实施契约合并版）\
**日期**：2026-10-11\
**适用仓库**：`SiYueY/roboagent`\
**文档性质**：目标架构与实施契约；已合并原实施契约补丁，本文为 Initial Embodied Release 的完整、独立实施规范。接口与示例表达目标契约，不表示仓库当前已经全部实现或验收。

**最高原则**：

> **One Agent, One Runtime, Persistent World Awareness, Composable Capabilities**

> **Primitives, not Features**

---

# 1. 架构定位与总体设计

## 1.1 RoboAgent 的统一定位

**RoboAgent 是一个统一的、具有持续环境感知与认知能力的通用 Agent。**

物理机器人只是 RoboAgent 能够观察、理解和操作的一类外部环境，不因此形成第二种 Agent、第二套 Runtime 或独立 Cognitive Architecture。

RoboAgent 参考 Pi Agent Harness 的 minimal、composable、explicit、extensible 和 `Primitives, not Features` 思想。Pi 当前仍将自身定义为包含 Agent Runtime、Tool Calling 和 State Management 的 Agent Harness。

但 RoboAgent **不以 Pi 当前 API、Runtime 结构或能力边界作为不可修改规范**。

当 Embodied 能力暴露出现有 RoboAgent 抽象不足时，应直接调整 RoboAgent 自身，而不是为了兼容旧结构在外围增加：

```text
EmbodiedRuntime
WorldRuntime
CognitiveRuntime
WorldAgent
RobotAgent
EmbodiedAgentLoop
RobotSession
WorldPlanner
```

等平行体系。

判断新 Primitive 是否应该进入 RoboAgent 的标准是：

1. 当前需求是否已经无法通过已有 Primitive 清晰组合完成；
2. 新概念是否具有稳定、明确、可测试的语义；
3. 是否有多个实际场景需要该能力；
4. 是否可以避免创建第二套状态、执行或推理体系。

---

## 1.2 本设计解决的问题

本设计不负责：

- 机器人硬件驱动；
- ROS2 Controller；
- MoveIt / Nav2；
- VLM、目标检测、ASR/TTS 模型训练；
- 通用 Scene Graph；
- 通用 World Model；
- 完整 Cognitive Architecture。

本设计解决更基础的 Agent–Environment Integration：

1. 外部环境可以在没有 Conversation、ToolCall 或 active Run 时持续变化；
2. RoboAgent 可以持续接收环境证据；
3. 环境状态拥有独立于 Session/Run 的生命周期；
4. Observation、Claim、Current Belief 和 Physical Ground Truth 必须区分；
5. 当前环境认知可以直接参与每个 Model Turn 的 Context；
6. Agent 可以通过已有 Tool 主动获取新的环境证据；
7. Agent 可以通过已有 Tool 改变物理环境；
8. Tool 返回成功不能自动证明物理目标已经完成；
9. 环境感知、模型推理和机器人控制可以运行在不同频率；
10. 系统必须在本地 CPU-only、允许 Cloud LLM/VLM/API 的环境中保持轻量和有界。

---

## 1.3 强制架构不变量

| ID | 不变量 | 含义 |
|---|---|---|
| A-01 | One Agent | 不增加 EmbodiedAgent、WorldAgent 或机器人专用 AgentLoop |
| A-02 | One Runtime | Session、Run、Tool、Execution、Effect 保持唯一实现 |
| A-03 | One Owner per State | Conversation 属于 Session；Execution 属于 Run/Execution；Environment Cognition 属于 World |
| A-04 | Evidence ≠ Truth | Observation、Claim、Current Belief 和真实物理世界不是同一个概念 |
| A-05 | Action ACK ≠ Physical Success | Tool/Controller ACK 不能替代物理结果验证 |
| A-06 | World ≠ Conversation | World 更新不依赖 UserMessage/ToolCall，World 状态不进入 canonical transcript |
| A-07 | Snapshot per Model Turn | 一次 Model Turn 使用固定 WorldSnapshot |
| A-08 | Freshness ≠ Revision | 数据时间有效性和 World 提交版本完全独立 |
| A-09 | No Ambient Privilege | 环境内容始终是低信任数据，不提升 Tool/模型权限 |
| A-10 | Bounded by Default | 历史、实体、媒体、队列、Token、并发和云请求必须有界 |
| A-11 | Source Identity ≠ Authorization | source 字符串不是身份凭据 |
| A-12 | Minimal First | 不提前建设 Memory/Belief/Knowledge/Graph/Planner/Provider Framework |

Initial Embodied Release 不新增：

```text
WorldMemoryManager
BeliefEngine
KnowledgeGraphRuntime
SituationManager
GlobalEventBus
RobotActionRuntime
PerceptionProviderRegistry
ContextSourceRegistry
AutonomousTriggerEngine
```

---

## 1.4 总体反馈链

RoboAgent Embodied 的主认知闭环：

```text
                  Physical / External World
                         │             ▲
                         │             │
                   Observation        Tool
                         │             │
                         ▼             │
                       Claim           │
                         │             │
                         ▼             │
                       World           │
                         │             │
                   WorldSnapshot       │
                         │             │
                         ▼             │
                      Context          │
                         │             │
                         ▼             │
                       Agent ──────────┘
```

它表达的是责任关系，而不是固定的机器人流水线。

用户直接提供图片、语音等输入时，可以走另一条 direct interaction path：

```text
User / Camera / Microphone
          │
          ▼
      Interaction
          │
          ▼
   Message / Content
          │
          ▼
        Agent
```

因此：

```text
Direct multimodal interaction
≠
Persistent environment cognition
```

两种路径可以共享物理设备，但语义不同。

---

## 1.5 状态所有权

Embodied 集成后，RoboAgent 需要明确三个 canonical state owner：

| 状态 | Canonical Owner | 含义 |
|---|---|---|
| Conversation State | `Session` | 用户、Assistant、Tool 的 canonical conversation |
| Execution State | `Run / Execution` | Tool 执行、Effect、错误、取消、不确定副作用 |
| Environment Cognition | `World` | RoboAgent 当前对外部环境的 best-known cognition |

必须保持：

```text
Session transcript
        ≠
Execution facts
        ≠
World state
```

例如：

```text
pick(cube_7)
→ Tool SUCCEEDED
```

不能自动推出：

```text
robot.holding = cube_7
```

后者必须由独立 Observation / Claim 支持。

---

## 1.6 World 生命周期与授权域

World 通常满足：

```text
World lifetime > Session lifetime > Run lifetime
```

Host 创建 World，并确定：

- `world_id`；
- 环境作用域；
- Observation / Claim source 授权；
- Resource limits；
- Media owner；
- Agent 与 World 的绑定关系。

建议：

```python
world = World(
    world_id="mfr3duo-sim-1",
    limits=world_limits,
    allowed_sources=("robot/state", "perception/qwen-vl"),
    allowed_scopes=(None, "workspace"),
    clock_resolver=host_clock_resolver,
)

agent = Agent(
    model=model,
    world=world,
    tool_registry=tool_registry,
    context_manager=context_manager,
    tool_policy=robot_tool_policy,
)
```

`Agent.world` 是 immutable Agent configuration 中的稳定引用；World 自身内部状态可以变化。

同一个 Agent instance 作为 Agent-as-Tool 时：

```text
same Agent instance
→ same World binding
```

不同 child Agent：

```text
different Agent
→ its own World binding
```

Initial Release 规定：

> 一个 Agent + World binding 属于一个明确 authorization domain。

World read、World write 和 Tool execution 是三个独立权限面。

能够读取机器人状态的 Agent：

```text
does not imply
```

拥有移动机器人、执行 shell 或修改文件的权限。

---

# 2. 设计参考与取舍

RoboAgent Embodied 并非从零设计，也不复制某一个现有框架。

设计综合参考了 Agent Harness、Embodied Agent、World State、长期环境记忆、主动感知和传统 Cognitive Architecture 等多个方向，再根据 RoboAgent 自身已有 Runtime 重新组合。

原则是：

> **借鉴机制与设计边界，不复制完整系统。**

---

## 2.1 主要直接参考

| 项目 / 工作 | RoboAgent 借鉴 | 明确不照搬 |
|---|---|---|
| **Pi Agent Harness** | Minimal Agent Runtime、Tool calling、State management、Composable primitives | 不受 Pi 当前非 Embodied API 边界限制 |
| **Thea** | 将 coding-agent harness 延伸到 Physical World；当前环境通过 Context 进入既有 Agent | 不强制 Scene Graph，不复制完整 Embodied Harness |
| **OpenRAL** | Typed world state、freshness/staleness、continuous state 与 reasoning 解耦 | 不复制 HAL→Sensors→World→Reasoning 固定层次 |
| **OpenETA** | perception→action→observation→verification causal loop | 不复制其完整 execution harness |
| **ROSClaw** | 执行回执不能代替 physical evidence | 不复制 daemon/receipt framework |
| **REAL** | 信息不足时 Agent 主动观察和消歧 | 不提前建设 ActivePerceptionManager |
| **DimOS** | 连续物理 stream 与 Agent callable capability 分离 | 不复制 Robot OS / Module / Blueprint Runtime |

Thea 当前明确定位为把 coding agents 带入 physical world，并要求部署方自行提供 robot SDK、sensing backend、Scene Graph backend 和 post-execution evidence。

DimOS 则更接近面向物理空间的 agentic operating system，可连接 camera、lidar、actuator 等物理输入与多种机器人平台。

RoboAgent 不选择其中任何一个作为完整模板，而只抽取：

```text
continuous environment state
current Context projection
Tool-based action
post-action evidence
```

四类稳定机制。

---

## 2.2 Environment Memory 与 Context 参考

| 项目 / 工作 | 主要启发 | RoboAgent 当前取舍 |
|---|---|---|
| **M3-Agent** | 实时视觉/音频可持续构建 entity-centric long-term memory | World lifecycle 独立于 Run，但 V1 不实现完整 long-term memory |
| **ReflectWorld / ReflectWorld-MM** | Persistent entities、evidence、provenance | 用于 EntityRef / EvidenceRef / correction 设计 |
| **STaR** | 完整 Memory 不应全部进入模型，需 task-conditioned retrieval | WorldSnapshot 与 Model Context 分离 |
| **多时间尺度 Memory 工作** | 不同时间尺度的数据可采用不同 representation | 当前只保留 bounded evidence/history |

M3-Agent 明确支持实时视觉与音频输入持续建立和更新长期记忆，并采用 entity-centric multimodal representation。

因此 RoboAgent 明确：

```text
World
≠
Context
≠
Conversation
```

World 保存持续认知；

Context 负责当前推理所需的有限投影。

---

## 2.3 长期 Cognitive Architecture 参考

KnowRob、CRAM、Soar 等传统系统用于校验长期边界，而不是当前代码蓝图。

它们说明：

```text
Observation
Knowledge
Situation
Memory
Planning
Execution
```

长期都可能重要。

但 RoboAgent 当前不建设完整 Cognitive Architecture。

特别需要保持：

```text
World does not plan.
World does not select Tools.
World does not become another Agent.
```

Agent 始终是唯一 general reasoning center。

---

## 2.4 综合取舍

| RoboAgent 当前设计 | 主要参考 |
|---|---|
| Primitives, not Features | Pi |
| 单一 AgentLoop / Tool / Skill | Pi、Thea |
| World 独立于 Conversation | OpenRAL、M3-Agent、ReflectWorld |
| World → bounded Context | Thea、OpenRAL、STaR |
| Entity / Evidence / provenance | ReflectWorld、M3-Agent |
| stale / unknown / conflict | OpenRAL |
| Agent 主动观察 | REAL、OpenRAL |
| Tool success ≠ Physical success | OpenETA、ROSClaw |
| sensor frequency 与 reasoning frequency 解耦 | OpenRAL、DimOS |
| Memory 后置 | M3-Agent、ReflectWorld、STaR |
| Knowledge 后置 | KnowRob、CRAM |
| 不建立第二个 cognitive brain | CRAM、Soar |

---

# 3. Environment Cognition：Observation、Claim 与 World

## 3.1 认知链条

核心链：

```text
Physical World
     │
     ▼
Observation
     │
     ▼
Claim
     │
     ▼
World Resolution
     │
     ▼
Current Belief
```

**Observation** 表示外部系统真正提供的 evidence。

**Claim** 表示基于 evidence 产生的结构化认知声明。

**Current Belief** 表示 World 在某个 evaluation time 下，根据当前有效 Claim 得到的 best-known result。

**Physical World** 不等于以上任何一种软件状态。

---

## 3.2 时间域

```python
@dataclass(frozen=True, slots=True)
class ObservationTime:
    clock_id: str
    nanoseconds: int
```

`clock_id` 标识一个可比较的时间域实例，而不仅是 clock 类型。

例如：

```text
host-monotonic:<boot-id>
camera:<device-session-id>
ros:/clock:<simulation-id>
mujoco:<simulation-run-id>
host-utc
```

以下默认不可比较：

```text
Host UTC
process monotonic
camera monotonic
ROS Time
MuJoCo simulation time
```

只有同一 clock domain 或存在明确映射时，才允许时间排序或 freshness 计算。

ROS Time reset、MuJoCo restart、device restart 必须使用新的 clock-domain identity，不能继续复用旧 `clock_id`。Host 提供当前时间域的方式见第 3.14 节 `ClockResolver` 契约。

---

## 3.3 Observation

```python
@dataclass(frozen=True, slots=True)
class ObservationRef:
    source: str
    id: str
```

```python
@dataclass(frozen=True, slots=True)
class Observation:
    ref: ObservationRef
    modality: str

    observed_at: ObservationTime
    received_at: datetime

    payload: JsonValue | ResourceRef
    scope: str | None = None
```

其中：

```text
observed_at
```

表示环境实际被采样的时间。

```text
received_at
```

只用于：

- latency；
- audit；
- diagnostics；
- metrics。

禁止用 received order 代替环境时间排序。

---

## 3.4 EntityRef

```python
@dataclass(frozen=True, slots=True)
class EntityRef:
    id: str
```

真正唯一 identity 是：

```text
(world_id, EntityRef)
```

EntityRef 不等价于：

- tracker ID；
- ROS frame；
- MuJoCo body；
- database ID。

单帧 identity 不确定时可以保持匿名候选。

错误 association 必须允许后续修正。

---

## 3.5 ClaimRef、EvidenceRef 与 Claim source

```python
@dataclass(frozen=True, slots=True)
class ClaimRef:
    id: str
```

```python
@dataclass(frozen=True, slots=True)
class EvidenceRef:
    kind: Literal["observation", "claim"]
    source: str | None
    id: str
```

Claim producer 必须明确记录，不能只根据 EvidenceRef 推断。

例如：

```text
Observation source:
camera/front

Claim source:
perception/qwen-vl
```

同一张图像完全可以被多个 perception backend 解读成不同 Claim。

因此：

```python
class ClaimKind(str, Enum):
    MEASUREMENT = "measurement"
    INTERPRETATION = "interpretation"
    INFERENCE = "inference"
    HYPOTHESIS = "hypothesis"
    PRIOR = "prior"
    PREDICTION = "prediction"
```

Claim：

```python
@dataclass(frozen=True, slots=True)
class Claim:
    ref: ClaimRef

    subject: EntityRef
    predicate: str
    value: JsonValue | EntityRef

    kind: ClaimKind
    source: str
    scope: str | None

    evidence_refs: tuple[EvidenceRef, ...]

    asserted_at: datetime
    valid_at: ObservationTime | None = None
    valid_for_ns: int | None = None

    supersedes: tuple[ClaimRef, ...] = ()
```

Claim source 是 producer identity。

Claim scope 表示适用空间、传感区域或局部环境范围。

不同 scope 的 Claim 不自动视为冲突。

Claim 默认时间有效窗口由以下两项定义：

```text
valid_at + valid_for_ns
```

`valid_for_ns=None` 表示该 Claim 本身不因时间自动过期，不表示永久真实；新的 Observation/Claim 仍可以替代它或与它发生冲突。

有效期策略由产生 Claim 的 Adapter 决定，例如：

```text
robot pose       short TTL
gripper state    short TTL
object pose      bounded TTL
battery          longer TTL
object color     often no automatic TTL
```

World 不根据 predicate 名称硬编码 TTL。Clock 与 Snapshot freshness 的处理见第 3.14 节。

---

## 3.6 Source Identity 与 Authorization

以下字段：

```text
source = "robot/force_sensor"
```

不能作为认证凭据。

source identity 必须由 Host / Trusted Adapter 绑定。Model、Tool 参数或不可信 JSON 不允许自由指定可信 sensor identity。

Initial Release 明确：

> **World 不是同进程内部的 security boundary。**

职责划分：

```text
Host / Trusted Adapter
        │
        ├── authenticate caller
        ├── assign source identity
        ├── enforce allowed world_id
        └── enforce allowed scope
        │
        ▼
      World
        │
        ├── validate schema
        ├── validate configured source membership
        ├── validate scope
        └── enforce World invariants
```

因此：

- Host 控制 Adapter identity 对应的 allowed world_id、source 与 scope；
- World 验证配置中的 source membership、scope 和数据不变量；
- source 字符串本身不是身份凭据；
- Initial Release 不新增 SourceManager、Credential、RBAC 或 ACL Framework；
- 如果未来出现跨进程 World Service，再增加真正的 authenticated writer identity。

---

## 3.7 Observation 与 Claim 的职责边界

必须保持：

> **World 不解释任意 Observation.payload。**

Observation：

```text
evidence envelope
```

Claim：

```text
canonical semantic cognition input
```

例如：

```text
Camera pixels
   ↓
Observation
```

之后：

```text
VLM / OpenCV / RGB-D
   ↓
Claim
```

才产生：

```text
cube_7.color = red
cube_7.on = table_1
```

World 不内置：

```text
Vision parser
Audio parser
Robot state parser
```

---

## 3.8 高频 Measurement

高频 joint state、odometry、battery 等仍然使用：

```text
raw source
   ↓
Observation
   ↓
MEASUREMENT Claim
   ↓
World
```

Adapter 可以：

```text
latest-only
sampling
coalescing
change threshold
drop-old
```

避免每一帧永久进入 history。

---

## 3.9 状态变化与纠错必须区分

现实自然发生变化：

```text
T0:
cube.location = table

T1:
cube.location = basket
```

这是两个在不同时间均正确的 Claim。

不使用：

```text
supersedes
```

认知纠错：

```text
Claim 101:
cube.color = orange

Claim 102:
cube.color = red
supersedes = (101,)
```

表示 Claim 101 当时就是错误的。

因此：

| 情况 | 处理 |
|---|---|
| 现实状态改变 | 新 Claim + 新 valid_at |
| 旧识别错误 | 新 Claim + supersedes |
| 旧 Claim 过期 | STALE，不表示错误 |
| 两条有效 Claim 冲突 | CONFLICTED |
| identity association 错误 | replacement correction |
| 纯撤销且无替代值 | Initial Release 暂不支持 |

Initial Release 明确：

> `supersedes` 支持 replacement correction，不提供独立 bare retraction API。

如果未来真实出现纯撤销需求，再新增明确 Primitive。

---

## 3.10 World 公共 API

```python
class World:
    async def observe(
        self,
        observation: Observation,
        *,
        claims: Sequence[Claim] = (),
    ) -> WorldReceipt:
        ...

    async def add_claims(
        self,
        claims: Sequence[Claim],
    ) -> WorldReceipt:
        ...

    async def snapshot(self) -> WorldSnapshot:
        ...

    async def query(
        self,
        entity: EntityRef,
        predicate: str,
    ) -> WorldAnswer:
        ...

    async def find_entities(
        self,
        filters: Mapping[str, JsonValue],
        *,
        limit: int = 16,
    ) -> EntitySearchResult:
        ...
```

Entity search 的正式结果契约：

```python
@dataclass(frozen=True, slots=True)
class EntityCandidate:
    entity: EntityRef
    matched: FrozenJsonObject
    supporting_claims: tuple[ClaimRef, ...]


@dataclass(frozen=True, slots=True)
class EntitySearchResult:
    world_id: str
    revision: int

    candidates: tuple[EntityCandidate, ...]
    truncated: bool
```

`find_entities()` 只做：

```text
structured deterministic matching
```

不做：

```text
natural-language grounding
embedding retrieval
object detection
VLM reasoning
entity tracking
```

例如：

```python
world.find_entities(
    {
        "category": "cube",
        "color": "red",
    }
)
```

如果多个实体符合条件，在 `limit` 与 WorldLimits 限制内返回匹配候选，不静默选择一个；若因结果上限省略候选，必须通过 `EntitySearchResult.truncated` 明确表达。由 Agent 决定进一步观察或消歧。

---

## 3.11 WorldReceipt 与原子事务

```python
@dataclass(frozen=True, slots=True)
class WorldReceipt:
    world_id: str
    revision: int
    changed: bool

    observation_ref: ObservationRef | None
    committed_claim_refs: tuple[ClaimRef, ...]
```

`changed` 表示：

> World 的 canonical evidence/claim set 是否发生提交变化。

它不表示物理世界是否变化。

事务规则：

| 输入 | 结果 |
|---|---|
| 同 Observation + 同同步 Claim 集合重试 | 幂等成功，revision 不变 |
| 同 Observation ID + 不同 payload | identity conflict |
| 同 Observation ID + 不同同步 Claim 集合 | transaction conflict |
| 同 ClaimRef + 同内容 | 幂等成功 |
| 同 ClaimRef + 不同内容 | identity conflict |
| Claim 引用不存在 evidence | 整批拒绝 |
| 一批 Claim 中任意一条无效 | 整批拒绝 |
| late perception Claim | 只能通过 `add_claims()` |

因此：

```text
observe()
=
immutable atomic ingestion transaction
```

新的异步 Claim 必须通过 `add_claims()` 追加。

World 使用稳定、可测试的错误 code，至少包括：

```text
invalid_observation
observation_conflict

invalid_claim
claim_conflict
evidence_not_found

source_not_allowed
scope_not_allowed

world_capacity_exceeded
clock_unavailable
```

不建立复杂异常继承层次。错误必须满足：

- 对外 code 稳定；
- message 不包含敏感资源内容；
- 非法事务不产生 partial commit；
- 非法事务不改变 revision。

---

## 3.12 Current Belief 默认规则

```python
class WorldStatus(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    STALE = "stale"
    CONFLICTED = "conflicted"
```

默认仲裁：

| 条件 | WorldStatus |
|---|---|
| 有充分、有效且无实质冲突的 evidence | KNOWN |
| 缺乏足够 evidence | UNKNOWN |
| 只有已经过期的最后已知值 | STALE |
| 多个有效 Claim 无法可靠裁决 | CONFLICTED |
| 只有 Hypothesis/Prior/Prediction | 不得成为实时物理 KNOWN |
| 不同 source 的时间不可比较且结论冲突 | CONFLICTED |
| 当前有效性无法确定 | UNKNOWN 或可证明的 STALE |

`KNOWN` 只表示：

> 在当前规则和 evidence 下具有足够支持。

不等于绝对 Ground Truth。

---

## 3.13 WorldAnswer 与冲突候选

```python
@dataclass(frozen=True, slots=True)
class WorldCandidate:
    value: JsonValue | EntityRef
    supporting_claims: tuple[ClaimRef, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    valid_at: ObservationTime | None
```

World current cognition 中的 entry 契约：

```python
@dataclass(frozen=True, slots=True)
class WorldEntry:
    subject: EntityRef
    predicate: str

    status: WorldStatus
    value: JsonValue | EntityRef | None

    candidates: tuple[WorldCandidate, ...]

    supporting_claims: tuple[ClaimRef, ...]
    evidence_refs: tuple[EvidenceRef, ...]

    valid_at: ObservationTime | None
```

规则：

- `KNOWN`：`value` 为当前值，`candidates` 通常为空；
- `UNKNOWN`：`value=None`；
- `STALE`：保留最后已知 `value` 和 provenance，但不得被解释为当前有效事实；
- `CONFLICTED`：`value=None`，冲突信息通过 `candidates` 提供；
- WorldEntry 只表达当前 cognition，不承担长期历史存储。

World query 返回：

```python
@dataclass(frozen=True, slots=True)
class WorldAnswer:
    world_id: str
    revision: int

    status: WorldStatus

    value: JsonValue | EntityRef | None
    candidates: tuple[WorldCandidate, ...]

    supporting_claims: tuple[ClaimRef, ...]
    evidence_refs: tuple[EvidenceRef, ...]

    valid_at: ObservationTime | None
```

对于 `KNOWN`：

```text
value = current value
```

对于 `CONFLICTED`：

```text
value = None
candidates = bounded conflicting candidates
```

这样 Agent 才能决定应该重新观察哪个来源或是否需要用户消歧。

---

## 3.14 Revision 与 Freshness

这是硬性不变量：

> **World revision 只表示 canonical data commit version。**

> **Freshness 是基于 evaluation time 对 evidence 有效性的判断。**

例如：

```text
10:00:00
revision = 100
cube.location = KNOWN

10:00:10
revision = 100
cube.location = STALE
```

完全合法。

硬性规则：

- revision 只在 canonical World data commit 时变化；
- 时间自然流逝不增加 revision；
- Snapshot 创建时计算并冻结 freshness；
- 相同 revision 在不同 capture time 可以得到不同 freshness。

因此：

```text
(world_id, revision)
```

不能作为 Current Belief 的永久 cache key。

Freshness 只能在：

```text
evaluation time
```

和：

```text
Claim.valid_at
```

位于同一可比较 clock domain 或存在映射时计算。

例如：

```text
simulation paused
wall clock advances
simulation clock does not advance
```

不能让仿真状态因为 wall time 自动 stale。

World 不直接使用 wall clock 判断任意 Claim freshness。Host 提供轻量 clock resolver：

```python
class ClockResolver(Protocol):
    def now(self, clock_id: str) -> ObservationTime | None:
        ...
```

要求：

- 返回与 `clock_id` 同一时间域的当前时间；
- 无法提供该时间域当前时间时返回 `None`；
- 不在 World 内部实现 ROS/MuJoCo/camera 时钟同步服务。

如果 Claim 有 TTL，但当前无法获得可比较的 clock，**不得猜测 freshness**；应保守产生 `UNKNOWN`，或保持其他能够明确证明的状态。

Clock reset/restart 必须通过新的 clock-domain identity 表达，不能把旧 `clock_id` 下的时间当作新时间域继续比较。

---

## 3.15 WorldSnapshot

```python
@dataclass(frozen=True, slots=True)
class WorldSnapshot:
    world_id: str
    revision: int
    captured_at: datetime

    entries: tuple[WorldEntry, ...]
```

WorldSnapshot 表示：

> 某个 revision 在某个 evaluation time 下完整的 current World cognition view。

要求：

```text
immutable
internally consistent
bounded by World limits
```

这里**不增加 `truncated`**。

因为 Snapshot 表示 World current state 本身，而不是 Model Context projection。

如果 World 本身超过允许容量，应由 World limits 在写入时：

```text
reject / evict / coalesce
```

而不是让 `snapshot()` 随机返回 partial truth。

截断只发生在：

```text
WorldSnapshot
→ ContextDataSegment
```

阶段。

---

## 3.16 Evidence Retention 与 ResourceRef

World history 有界，但必须维护 referential integrity。

仍被以下内容引用的 Evidence metadata：

```text
Current Belief
active Claim
correction chain
```

不能直接删除。

大型媒体 bytes 可以过期，但保留：

```text
identity
source
observed_at
media_type
size
digest
availability
```

ResourceRef：

```python
@dataclass(frozen=True, slots=True)
class ResourceRef:
    uri: str
    media_type: str | None
    size: int | None
    digest: str | None
```

World 不负责：

```text
download
decode
delete
open arbitrary URI
```

媒体资源由：

```text
Host
Observation Source
Media Store
```

管理。

---

# 4. World 与 Agent Context 的原生集成

## 4.1 Context 的新职责

当前 RoboAgent Context 主要来自：

```text
Prompt
Conversation
Tools
Skills
```

Embodied 增加：

```text
Current Environment Cognition
```

因此 ContextManager 必须从 conversation-centric projection 演进为：

> **完整 Model Context 的统一准备者。**

---

## 4.2 最终数据流

V2.4 采用：

```text
Agent
  └── world: World | None
             │
             ▼
          AgentLoop
             │
             ├── capture Session state
             │
             └── capture WorldSnapshot once
                         │
                         ▼
                   ContextRequest
                  ├── Conversation
                  ├── WorldSnapshot
                  ├── Tools / Skills
                  └── Model settings
                         │
                         ▼
               ContextManager.prepare()
                  ├── World relevance selection
                  ├── World projection
                  ├── whole-request budget
                  └── transcript compaction
                         │
                         ▼
                    ModelContext
                         │
                         ▼
                       Model
```

职责：

| 组件 | 唯一职责 |
|---|---|
| World | Evidence / Claims / Current Belief |
| AgentLoop | 捕获 immutable inputs，协调 Model/Tool |
| ContextManager | relevance、projection、budget、compaction |
| ModelContext | 已准备好的最终模型输入 |
| Model Adapter | Provider 编码 |

AgentLoop 只捕获 immutable inputs：

```text
capture Session state
→ capture WorldSnapshot once
→ build ContextRequest
```

AgentLoop 不解释 WorldEntry、不执行 entity relevance ranking、不提前把 WorldSnapshot 序列化成文本，也不自行裁剪 World。

ContextManager 是唯一 Context Projection Owner：

```text
select relevant World state
→ project World context
→ apply whole-request budget
→ compact Conversation
```

---

## 4.3 ContextRequest

建议：

```python
@dataclass(frozen=True, slots=True)
class ContextRequest:
    snapshot: ContextSnapshot

    model_settings: ModelSettings
    model_capabilities: ModelCapabilities

    current_compaction: ContextSummary | None

    world_snapshot: WorldSnapshot | None = None
```

`ContextSnapshot` 继续表示 canonical conversation-related state。

`WorldSnapshot` 与它并列，表示本轮独立捕获的 environment input。

---

## 4.4 ContextDataSegment

不再使用：

```text
RuntimeContextData
→ RuntimeContextSegment
```

两层重复结构。

统一为：

```python
@dataclass(frozen=True, slots=True)
class ContextDataSegment:
    source: str
    source_id: str | None
    revision: int | None

    captured_at: datetime

    text: str

    truncated: bool = False
    omitted_count: int = 0
```

World：

```text
source = "world"
source_id = world_id
revision = snapshot.revision
```

这里的：

```text
truncated
```

仅表示：

> 本轮 Model Context 并未包含 WorldSnapshot 中全部 Entry。

它绝不表示 WorldSnapshot 本身不完整。

---

## 4.5 ContextManager 的 World projection

`ContextManager.prepare()` 在固定 WorldSnapshot 上执行：

```text
task-relevant entity selection
recent/active entity selection
Tool-relevant state selection
conflict/stale/unknown preservation
serialization
budget allocation
```

Initial Release 不实现 LLM selector、embedding retrieval 或 relevance service。ContextManager 使用简单确定性策略，优先级如下：

```text
1. 当前机器人 / self 的关键状态；
2. 当前 Conversation 最近明确引用的 EntityRef；
3. 当前可用 Tool 明确依赖的相关状态；
4. 与上述实体有关的 UNKNOWN / STALE / CONFLICTED 状态；
5. 最近更新的其余实体；
6. 以稳定 deterministic ordering 填充剩余 budget。
```

如果 Conversation 中只有“桌面上的红色方块”等自然语言描述，而没有 EntityRef，ContextManager 不执行自然语言实体检索。

Agent 可以使用当前 bounded World context 中已有候选，或显式调用由 Host 注册的 World search Tool。自然语言 grounding 仍属于 Agent。

可以使用 private helper：

```text
_project_world_context(...)
```

如果实现明显变大，可以移动到：

```text
context/world.py
```

但不建立：

```text
WorldContextManager
ContextSourceRegistry
WorldProjectionRegistry
```

---

## 4.6 Model Turn 一致性

每个 Model Turn：

```text
1. cancellation check

2. capture Session state

3. capture WorldSnapshot exactly once

4. build ContextRequest

5. ContextManager.prepare()

6. possible compaction retry

7. reuse same WorldSnapshot

8. invoke model
```

Tool 执行后进入下一 Model Turn：

```text
capture new WorldSnapshot
```

同一 Model Turn 中：

```text
World revision is fixed.
```

---

## 4.7 Token Budget 的唯一权威

最终预算：

```text
System / Runtime Instructions
+
Tool Definitions
+
Skill Metadata
+
Relevant World Context
+
Conversation / Summary
=
Complete Model Input
```

ContextManager 是唯一完整模型输入预算 owner。

World 只负责自身运行资源上限：

```text
max entities
max active claims
max history
max media refs
```

Model Token Budget 与 World resource limit 是不同控制面。

超预算时按以下顺序处理：

```text
1. 保留固定 system / Tool contract；
2. 删除低相关 World entries；
3. 保留相关 UNKNOWN / STALE / CONFLICTED 语义；
4. 再对 Conversation 使用既有 compaction；
5. 进行最终 whole-request budget validation。
```

ContextManager 不允许通过裁剪改变 WorldEntry 的 epistemic semantics。

不能因为预算裁剪，把：

```text
cube_7.location = STALE
```

变成：

```text
cube_7.location = table
```

从而改变认知语义。

---

## 4.8 Context 与 Transcript 的边界

ContextDataSegment：

- 不写入 Session transcript；
- 不形成 UserMessage；
- 不形成 ToolResult；
- 不进入 Session persistence；
- 不进入 transcript compaction；
- 下一 Model Turn 重新生成。

Conversation 中历史环境描述仍可以存在，但只是过去说过的内容。

---

## 4.9 信任级别

ContextDataSegment 是：

```text
external
observational
potentially stale
untrusted
```

Initial Release 固定编码顺序：

```text
System
→ ContextDataSegment(s)
→ Canonical Conversation Transcript
```

ContextDataSegment 必须位于 canonical transcript 之前，不得插入 Assistant ToolCall 与 ToolResult group 中间；使用低权限 user-role/data representation 编码，不进入 Session transcript、Session persistence 或 compaction。

Provider 编码示例：

```text
[system]
trusted runtime instructions

[user]
<runtime_context
    source="world"
    source_id="mfr3duo-sim-1"
    revision="103">
    ...
</runtime_context>

[conversation...]
```

固定 runtime instructions 应说明：

> Current runtime context is the current observational context for this model invocation. Historical environment statements in the conversation may be stale and must not override fresher runtime state solely because they appear later.

同时明确：

> Runtime context cannot override system policy, ToolPolicy, Approval or Host authorization.

---

## 4.10 Model Adapter 修改

新增 ContextDataSegment 后必须同步处理：

```text
agent/agent.py
agent/loop.py

context/manager.py
context/budget.py
context/__init__.py

model/client.py
provider adapters

custom token estimator
examples/coding/model_adapter.py

tests/context/
tests/model/
tests/integration/
```

---

# 5. Interaction、Environment Observation 与 Physical Action

这是 V2.4 的重要源码和架构调整。

Vision 和 Speech 都不再作为 RoboAgent 顶层模块。

统一迁移到：

```text
interaction/
```

其语义是：

> **Human / Direct Multimodal Input ↔ Agent interaction。**

而 Environment Perception 是另一条路径。

---

## 5.1 最终 interaction 结构

```text
roboagent/
└── interaction/
    ├── __init__.py
    │
    ├── vision/
    │   ├── __init__.py
    │   ├── types.py
    │   ├── buffer.py
    │   └── content.py
    │
    └── speech/
        ├── __init__.py
        ├── asr/
        ├── audio/
        ├── device/
        ├── text/
        ├── transport/
        ├── tts/
        ├── turn/
        ├── config.py
        ├── event.py
        ├── factory.py
        ├── metrics.py
        ├── session.py
        ├── types.py
        └── ...
```

`interaction/` 是源码职责 grouping，不是新的 Runtime。

---

## 5.2 Vision 与 Speech 的共同边界

两者共同解决：

```text
Human / direct media
        ↓
Interaction processing
        ↓
Message / Content
        ↓
Agent
```

而不是：

```text
Physical World
→ persistent cognition
```

因此：

```text
interaction/vision
interaction/speech
```

与：

```text
World
```

职责完全不同。

---

## 5.3 Visual Interaction

当前 `VisionContext` 名称不再保留。

因为它只是 latest-frame holder，和 RoboAgent 真正的 Context 概念冲突。

改为小而完整的 Visual Interaction：

```python
from dataclasses import dataclass, field
from time import monotonic


@dataclass(frozen=True, slots=True)
class VisionFrame:
    data: bytes
    mime_type: str
    width: int
    height: int

    captured_at: float = field(default_factory=monotonic)
    source: str | None = None
```

这里 `captured_at` 是 interaction-local host monotonic timestamp。

它只用于：

```text
speech utterance
↔
visual frame
```

等当前进程内 temporal alignment。

它：

- 不持久化；
- 不和 ROS Time 直接比较；
- 不等价于 World ObservationTime。

VisionFrame 是 direct interaction media primitive，不需要持久 evidence identity，也不增加强制 `id`。

ObservationRef 表达持久 World evidence identity；两者不能混淆。

如果该 frame 要进入 World，Observation Adapter 必须创建独立的 ObservationRef，并显式构造正确的 ObservationTime。

---

## 5.4 VisionBuffer

```python
class VisionBuffer:
    def push(self, frame: VisionFrame) -> None:
        ...

    def latest(self) -> VisionFrame | None:
        ...

    def nearest(
        self,
        timestamp: float,
        *,
        max_age: float | None = None,
    ) -> VisionFrame | None:
        ...

    def clear(self) -> None:
        ...
```

内部使用：

```text
bounded deque
```

支持：

- latest frame；
- nearest frame by timestamp；
- bounded retention；
- frame expiry。

不缓存无限视频。Vision 最小完成范围必须覆盖 `push()`、`latest()`、`nearest()`、`clear()`、bounded retention、max-age filtering，并具备单元测试；Frame → ImageContent 转换还必须覆盖 MIME validation 和 media-size validation。

---

## 5.5 VisionFrame → ImageContent

提供统一转换函数：

```python
def image_content_from_frame(
    frame: VisionFrame,
) -> ImageContent:
    ...
```

负责：

- MIME validation；
- media size limits；
- byte source；
- metadata；
- ImageContent creation。

Direct visual interaction：

```text
Camera / Browser / Upload
        ↓
VisionFrame
        ↓
VisionBuffer
        ↓
image_content_from_frame()
        ↓
ImageContent
        ↓
UserMessage
        ↓
Agent
```

---

## 5.6 不创建 VisionSession / VisionProvider

当前不新增：

```text
VisionSession
VisionProvider
VisionRegistry
VisionRuntime
CameraRegistry
```

SpeechSession 存在是因为 Speech 真实拥有：

```text
streaming audio
VAD
turn detection
ASR
TTS
barge-in
transport lifecycle
```

Vision 当前不具备同等级 runtime lifecycle。

目录结构对称：

```text
interaction/vision
interaction/speech
```

不意味着内部实现必须机械对称。

---

## 5.7 Speech

当前成熟的 Speech 实现整体迁移：

```text
roboagent/speech/
        ↓
roboagent/interaction/speech/
```

保留：

```text
ASR
Audio processing
Turn Detection
SpeechSession
Transport
TTS
Metrics
```

核心语义仍然是：

```text
Human speech
    ↓
ASR
    ↓
UserMessage
    ↓
Agent
    ↓
TTS
    ↓
Human
```

不把 SpeechSession 扩张成环境 Audio state owner。

---

## 5.8 Environment Observation

环境持续感知统一走：

```text
External Source
      ↓
Observation Adapter
      ↓
Observation / Claim
      ↓
World
```

来源可以是：

```text
camera
RGB-D
LiDAR
environment microphone
robot state
force sensor
simulation
human annotation
cloud perception
```

这些 adapter 初始优先放在：

```text
Host
integration repository
examples/embodied
```

而不是重新创建：

```text
roboagent/sensing/
```

通用感知 framework。

---

## 5.9 Vision 的双路径

同一个 Camera 可以同时服务两种语义：

```text
                     Camera
                    /      \
                   /        \
        Direct Interaction   Environment Perception
                │                    │
 interaction/vision            Observation Adapter
                │                    │
          ImageContent         Observation / Claim
                │                    │
              Agent                 World
```

Audio 同理：

```text
                   Microphone
                  /          \
                 /            \
 interaction/speech        audio event adapter
         │                        │
    UserMessage              Observation
         │                        │
       Agent                    World
```

---

## 5.10 主动 Observation Tool

World Python API 不自动等于 Agent capability。Initial Release 不允许 Agent 获得隐式 World access：

> **No Ambient Capabilities**

World query Tool 必须由 Host 显式注册。推荐提供最小 helper：

```python
create_world_tools(world)
```

至少生成：

```text
world_query
world_find_entities
```

二者均为 `READ_ONLY`，只查询已有 World state；不得捕获 Camera、调用 VLM、移动机器人、生成新的 Observation 或修改 World。

必须区分：

```text
World Query Tool
≠
Environment Observation Tool
```

例如：

```text
world_find_entities
    → search existing cognition

observe_scene
    → acquire new evidence

locate_object
    → may acquire perception / geometry
```

后两者由 Host / Robot integration 显式注册。

Tool 的 Effect 不能根据名字判断。

例如：

| Tool 行为 | 推荐 Effect |
|---|---|
| 读取已缓存 World state | READ_ONLY |
| 分析已缓存 image | READ_ONLY |
| 读取无物理副作用的 camera frame | 按设备契约判断 |
| 转动 camera 获取视角 | SIDE_EFFECTING |
| 移动底盘探索 | SIDE_EFFECTING |
| 机械臂动作 | SIDE_EFFECTING |

`observe_scene` 的名字本身不能决定它是 READ_ONLY。

---

## 5.11 同步与异步观察 Tool

必须区分：

```text
Observation Requested
        ↓
Observation Captured
        ↓
Perception Completed
        ↓
Claim Committed
```

同步 Tool：

```text
capture
→ perception
→ Claim committed
→ Tool returns
```

返回时可以提供：

```text
observation_ref
world_revision
```

异步 Tool：

```text
submit cloud perception
→ Tool returns task_id
→ result arrives later
→ add_claims()
```

此时 Tool 返回：

```text
accepted / running
task_id
```

不能声称：

```text
World already updated
```

---

## 5.12 Entity resolution

用户通常不知道 EntityRef。

例如：

> “抓取桌面上的红色方块。”

Agent 可以：

1. 从 ContextDataSegment 中找到候选；
2. 调 World structured search；
3. 调 Observation Tool 获取更多 evidence；
4. 如果仍然有多个候选，要求用户消歧。

World 的 `find_entities()` 不做自然语言 grounding。

自然语言 grounding 始终属于 Agent。

---

## 5.13 几何与可行动位置

禁止：

```text
VLM language output
→ arbitrary XYZ
→ robot motion
```

正确链：

```text
semantic target
    ↓
2D recognition
    ↓
depth / RGB-D
    ↓
camera intrinsics
    ↓
TF
    ↓
time-stamped 3D estimate
    ↓
Claim
```

职责：

```text
semantic model
→ which object

geometry
→ where

robot backend
→ whether reachable/safe

physical feedback
→ whether action succeeded
```

---

## 5.14 Physical Action Tool

Robot 动作继续使用现有 Tool：

```text
navigate_to
pick
place
cancel_task
stop
```

不建立：

```text
RobotActionRuntime
EmbodiedAction
ActionManager
```

必须区分：

```text
ToolInvocationAccepted
ControllerCommandAccepted
ControllerExecutionFinished
PhysicalGoalVerified
```

---

## 5.15 执行前状态再验证

WorldSnapshot 一致：

```text
does not imply
```

动作执行时环境仍然有效。

例如：

```text
T0  cube at A
T1  Agent plans pick
T2  cube moved
T3  Tool executes
```

所以 Robot Tool 在真正执行前必须校验其依赖状态：

| 校验项 | 目的 |
|---|---|
| Entity identity | 避免错误对象 |
| Target pose | 避免旧位置 |
| Observation freshness | 避免 stale state |
| Coordinate frame | 防止 frame mismatch |
| TF validity | 确保变换对应目标时间 |
| Reachability | 确认运动学可达 |
| Collision constraints | 确认安全 |
| Robot state | 确认机器人当前可执行 |

不能只用：

```text
world revision changed?
```

判断是否继续。

因为无关 battery/sensor 更新也会改变 revision。

---

## 5.16 UNKNOWN side effect

如果：

```text
controller accepted
network lost
Tool timeout
```

必须视为：

```text
UNKNOWN
```

而不是失败后直接重试。

正确流程：

```text
UNKNOWN
   ↓
get_task_status / get_robot_state
   ↓
Observation
   ↓
World
   ↓
Agent decides
```

---

## 5.17 Run cancellation

```text
Run.cancel()
≠
physical robot stopped
```

真实停止必须通过：

```text
task_id
cancel request
controller ACK
safe state verification
```

完成。

---

## 5.18 Skill

Skill 只描述能力组合：

```text
if object location stale:
    request observation

if manipulation outcome uncertain:
    query robot state

if candidates conflict:
    collect more evidence
```

Skill 不：

- 保存 World；
- 拥有 Camera；
- 写 Current Belief；
- 维护 physical execution state。

---

# 6. 并发、资源、安全与异常恢复

## 6.1 World concurrency

Initial Release 规定：

> 同一个 World 及直接共享它的 Session / Observation writer 运行在同一个 asyncio event loop。

使用短时：

```python
asyncio.Lock
```

锁内只允许：

```text
final validation
atomic commit
index update
revision update
snapshot state capture
```

禁止：

```text
Model request
VLM request
network I/O
media download
large serialization
RGB-D processing
```

---

## 6.2 snapshot 性能

`snapshot()` 必须：

```text
local
bounded
non-network
short critical section
```

不需要独立 CancellationToken。

---

## 6.3 高频 source

允许：

```text
latest-only
bounded queue
coalescing
sampling
change threshold
```

必须解耦：

```text
Sensor Sampling Frequency
Semantic World Update Frequency
LLM Reasoning Frequency
```

例如：

```text
30 FPS Camera
≠
30 VLM requests/s
≠
30 Agent Turns/s
```

---

## 6.4 World update 不自动启动 Agent

保持：

```text
World updated
≠
Session.start()
```

未来自主激活由 Host：

```text
world change
    ↓
Host policy
    ↓
Session.start()
```

决定。

---

## 6.5 Resource limits

Initial Release 至少提供以下 WorldLimits 契约：

```python
@dataclass(frozen=True, slots=True)
class WorldLimits:
    max_entities: int = 1024
    max_observations: int = 4096
    max_claims: int = 8192
    max_evidence_metadata: int = 8192
    max_payload_bytes: int = 1_048_576
    max_media_refs: int = 1024
    max_search_results: int = 64
```

具体默认值可以根据现有测试和实现合理调整，但所有限制必须有确定默认值，禁止无限增长。达到限制必须显式 reject、evict 或 coalesce，不新增 Memory GC Framework。

World 及 Host/Adapter 所拥有的资源边界还必须覆盖：

```text
max entities
max anonymous candidates
max observations
max claims
max active claims
max evidence metadata
max media refs
max JSON size
max observation queue
max cloud concurrency
max cloud request rate
```

达到限制时：

```text
reject
evict
coalesce
drop-old
```

必须明确。

---

## 6.6 Evidence retention

| 情况 | 处理 |
|---|---|
| 原始 media 超过 retention | 删除 bytes，保留 minimal metadata |
| 历史 Claim 不再被 current/correction 引用 | 可淘汰 |
| active Claim 依赖旧 evidence | 保留 minimal provenance |
| ResourceRef 已失效 | 标记不可复核 |
| World 达到容量上限 | 显式清理或拒绝 |

不建设复杂 Memory GC Framework。

---

## 6.7 CPU-only

Initial Release 保证：

```text
local CPU only
```

本地可使用：

```text
OpenCV
RGB-D geometry
camera calibration
TF
basic tracking
audio DSP
```

Cloud：

```text
LLM
VLM
ASR/TTS
```

可以按需调用。

不依赖：

```text
local GPU
```

作为基础部署要求。

---

## 6.8 ROS2 / MuJoCo

RoboAgent Python >= 3.12，而 ROS2 Humble 常见于 Python 3.10。

因此不要求直接 import ROS2 Humble Python stack。

推荐：

```text
RoboAgent
    │
Host / Bridge
    │
ROS2 / mfr3duo_ros2
```

或：

```text
RoboAgent
    │
MuJoCo adapter
```

---

## 6.9 安全控制面

Host / Trusted Adapter 负责 caller authentication、source attribution、allowed world_id 与 allowed scope；World 负责 schema、配置中的 source membership、scope 和数据不变量验证。World 不是同进程内部的 security boundary，不承担 Credential、RBAC 或 ACL Framework 职责。

### Environment data

验证：

```text
source
scope
schema
size
authorization
resource
```

### World access

控制：

```text
read
write
source identity
scope
```

### Tool execution

继续使用：

```text
ToolPolicy
Approval
Effect
Execution
```

### Physical safety

由 Controller 执行：

```text
joint limits
velocity/force limits
collision
workspace
emergency stop
task cancellation
physical verification
```

四者不能混成一个“Agent safety”。

---

## 6.10 Fault semantics

| 故障 | 默认处理 |
|---|---|
| Cloud perception timeout | 保留旧 evidence，必要时 STALE |
| VLM parse failure | 不产生 semantic Claim |
| Camera unavailable | source unavailable，不推断不存在 |
| TF/depth unavailable | 不产生 actionable 3D pose |
| duplicate Observation | 幂等 |
| old result late | 历史保留，不覆盖新 state |
| valid Claims conflict | CONFLICTED |
| World access failure | 显式 unavailable |
| Tool UNKNOWN | 先查询外部状态 |
| Run cancelled | 不清空 World，不宣称 robot stopped |
| World restart | 从 UNKNOWN 恢复 |
| media expired | provenance 保留，resource unavailable |

---

# 7. 源码组织、迁移、实施与测试

## 7.1 最终源码结构

建议：

```text
roboagent/
├── agent/
│   ├── agent.py
│   ├── loop.py
│   ├── session.py
│   └── ...
│
├── context/
│   ├── manager.py
│   ├── budget.py
│   └── ...
│
├── model/
├── runtime/
├── tool/
├── skill/
│
├── world/
│   ├── __init__.py
│   ├── types.py
│   └── world.py
│
├── interaction/
│   ├── __init__.py
│   │
│   ├── vision/
│   │   ├── __init__.py
│   │   ├── types.py
│   │   ├── buffer.py
│   │   └── content.py
│   │
│   └── speech/
│       ├── __init__.py
│       ├── asr/
│       ├── audio/
│       ├── device/
│       ├── text/
│       ├── transport/
│       ├── tts/
│       ├── turn/
│       ├── config.py
│       ├── event.py
│       ├── factory.py
│       ├── metrics.py
│       ├── session.py
│       ├── types.py
│       └── ...
│
├── config/
├── message.py
└── mcp.py
```

不新增：

```text
roboagent/embodied/
roboagent/sensing/
```

作为第二套框架。

---

## 7.2 目录迁移

迁移前布局：

```text
roboagent/vision/
roboagent/speech/
```

统一迁移：

```text
roboagent/interaction/vision/
roboagent/interaction/speech/
```

Speech：

> 以迁移为主，保持现有内部能力。

Vision：

> 在迁移同时完成最小必要补齐。

包括：

```text
VisionFrame
VisionBuffer
temporal frame lookup
ImageContent conversion
validation
bounded retention
tests
```

不保留 `VisionContext` 名称。

本次为明确的 breaking import migration；同步修改 repository 内部 imports、config、examples、tests 和 documentation。

不保留 `roboagent.vision`、`roboagent.speech` compatibility shim 或 deprecated re-export。旧 import path 不再作为公共路径存在。

---

## 7.3 Public API

推荐：

```python
from roboagent.interaction.vision import (
    VisionBuffer,
    VisionFrame,
    image_content_from_frame,
)

from roboagent.interaction.speech import (
    SpeechConfig,
    SpeechSession,
)
```

Initial Release 统一采用以上新路径。本项目尚未发布，本次迁移不考虑旧 API、旧 import path 或兼容层；不保留 deprecated re-export 或长期双入口。

World query helper 使用：

```python
from roboagent.world import World, create_world_tools
```

`create_world_tools(world)` 由 Host 显式注册到既有 ToolRegistry，不随 `Agent.world` 绑定自动注册。

---

## 7.4 Phase 0 — Baseline

确认当前：

```text
Agent
Session
Run
AgentLoop
ContextManager
CompactingContextManager
ModelContext
Tool
Execution
Vision
Speech
```

真实 public behavior 和 tests。

迁移前所有测试通过。

---

## 7.5 Phase 1 — World Contracts

实现：

```text
ObservationTime
ObservationRef
Observation
ResourceRef

EntityRef

ClaimRef
EvidenceRef
ClaimKind
Claim

WorldStatus
WorldCandidate
WorldEntry
WorldAnswer
EntityCandidate
EntitySearchResult
WorldSnapshot
WorldReceipt
WorldLimits
ClockResolver
World
```

必须覆盖：

```text
source/scope
authorization
idempotency
atomic transactions
late claim
clock domains
freshness
conflict
correction
entity search
evidence retention
valid_for_ns
stable World error codes
```

WorldEntry、EntityCandidate、EntitySearchResult、WorldLimits、valid_for_ns、ClockResolver 与稳定错误 code 必须在本 Phase 同时完成，不留给后续阶段临时补设计。

---

## 7.6 Phase 2 — Context Integration

实现：

```text
Agent.world

ContextRequest.world_snapshot

ContextDataSegment

ContextManager World projection

whole-request budget

compaction fixed-snapshot semantics

provider encoding
```

要求：

```text
AgentLoop only captures WorldSnapshot

ContextManager decides what Model sees

World data does not enter transcript

same Model Turn reuses same snapshot

deterministic V1 relevance policy

whole-request token budgeting

ContextDataSegment before transcript
```

必须验证 AgentLoop 只捕获 inputs、ContextManager 执行 World projection，以及 compaction retry 复用同一个 WorldSnapshot。

---

## 7.7 Phase 3 — Interaction restructuring

迁移：

```text
vision
speech
    ↓
interaction/
```

Vision 补齐：

```text
VisionBuffer
frame lookup
bounded retention
ImageContent conversion
tests
```

Speech 保持：

```text
ASR
TTS
Turn
Transport
SpeechSession
```

验证现有：

```text
chat image path
speech ASR→Agent→TTS
```

行为不回退。

必须验证旧 `roboagent.vision`、`roboagent.speech` import path 已从实现及其 consumers 中清除，不再作为公共路径存在，不提供 compatibility shim。

---

## 7.8 Phase 4 — Deterministic Closed Loop

使用：

```text
Fake Observation Source
Fake Perception
Fake Robot Tool
Fake physical feedback
```

验证：

```text
World update without Run

Entity search

Agent requests observation

async/sync observation completion

Tool changes environment

new Observation updates World

next Model Turn sees new cognition

Tool ACK does not become physical truth
```

---

## 7.9 Phase 5 — Real Environment Integration

按实际需要接：

```text
Cloud VLM
RGB-D
TF
MuJoCo
mfr3duo bridge
Robot Tools
environment audio
```

重点测试：

```text
semantic entity
→ geometry
→ pre-execution validation
→ physical action
→ independent evidence
→ verification
```

---

## 7.10 Phase 6 — Hardening

完成：

```text
concurrency
backpressure
resource limits
source authorization
prompt injection
UNKNOWN side effect
robot cancellation
media expiry
multi-session sharing
nested Agent permissions
performance measurement
```

---

## 7.11 核心验收测试

| ID | 场景 | 必须结果 |
|---|---|---|
| T01 | `world=None` | 原纯文本 Agent 行为不变 |
| T02 | 无 Run 时 `World.observe()` | World 可更新，不启动 LLM |
| T03 | 多 Session 共享 World | 共享同一 cognition |
| T04 | 不同 world_id | 不串 Entity/Evidence |
| T05 | 相同 Observation transaction 重试 | 幂等 |
| T06 | 相同 Observation ID 不同 payload | 拒绝 |
| T07 | 相同 Observation 不同同步 Claim 集合 | 拒绝 |
| T08 | Claim batch 一条非法 | 全批拒绝 |
| T09 | late Cloud Claim | 不覆盖新 state |
| T10 | source producer 不同 | provenance 可区分 |
| T11 | scope 不同 | 不自动判冲突 |
| T12 | 不同时钟域 | 无 mapping 不做伪比较 |
| T13 | revision 不变但时间流逝 | freshness 可变化 |
| T14 | simulation pause | wall time 不使 sim state 错误 stale |
| T15 | hypothesis/prediction only | 不产生 physical KNOWN |
| T16 | correction | superseded Claim 不再作为 current basis |
| T17 | physical state changed | 不错误使用 supersedes |
| T18 | 多候选实体 | `find_entities()` 返回多个候选 |
| T19 | World conflict | WorldAnswer 保留 candidates |
| T20 | WorldSnapshot | 完整一致，不 partial truncate |
| T21 | Context projection truncate | ContextDataSegment 明确 truncated |
| T22 | same Model Turn compaction retry | 固定 WorldSnapshot |
| T23 | Context over budget | 优先裁剪低相关 World context |
| T24 | stale/conflicted relevant state | 不因裁剪丢失关键状态标签 |
| T25 | World data | 不进入 transcript/summary |
| T26 | malicious World text | ToolPolicy/Approval 不改变 |
| T27 | VisionBuffer nearest frame | 与 utterance 时间正确关联 |
| T28 | Vision buffer overflow | 有界淘汰 |
| T29 | direct image | ImageContent path 正常 |
| T30 | Speech migration | ASR→Agent→TTS 行为不回退 |
| T31 | observation Tool sync | 返回时 Claim 已提交 |
| T32 | observation Tool async | 返回 task id，不伪称完成 |
| T33 | observe Tool moves camera | SIDE_EFFECTING |
| T34 | action target moved | 执行前重新验证或拒绝 |
| T35 | unrelated World revision changed | 不错误 abort action |
| T36 | Tool UNKNOWN | 不盲重试 |
| T37 | Run cancel | 不宣称机器人已停 |
| T38 | fake trusted source | source authorization 拒绝 |
| T39 | shared World | 不自动共享 Tool 权限 |
| T40 | Agent-as-Tool | 继承 World binding，但不扩大 Tool authority |
| T41 | Claim TTL 未到期 | KNOWN 保持 |
| T42 | Claim TTL 到期但无新 commit | revision 不变，Snapshot 中变 STALE |
| T43 | clock domain 当前时间不可获得 | 不猜 freshness |
| T44 | MuJoCo pause | wall time 不导致 simulation Claim 错误过期 |
| T45 | clock epoch/device restart | 新 clock_id，不与旧时间比较 |
| T46 | World Context relevance | deterministic，同输入得到同结果 |
| T47 | Context natural-language target 无 EntityRef | ContextManager 不偷偷做 NL grounding |
| T48 | `world_find_entities` | 只查询现有 World，不新增 evidence |
| T49 | `observe_scene` | 与 World query Tool 语义明确分离 |
| T50 | ContextDataSegment 编码 | 位于 transcript 前且不破坏 Tool exchange |
| T51 | 旧 `roboagent.vision` import | 不再作为公共路径存在 |
| T52 | 旧 `roboagent.speech` import | 不再作为公共路径存在 |
| T53 | VisionFrame 进入 World | Adapter 创建独立 ObservationRef/ObservationTime |
| T54 | World transaction 失败 | error code 稳定且 revision 不变 |

---

## 7.12 非目标与演进门槛

| 能力 | Initial Release | 扩展条件 |
|---|---|---|
| Long-term Episodic Memory | bounded history | 跨长期 Task/Session 检索成为真实需求 |
| Semantic Memory | 不实现 | 需要稳定、可审计语义知识 |
| Bayesian Belief | deterministic conflict rules | 多来源概率融合成为现实瓶颈 |
| Scene Graph | Entity + predicate | 复杂空间/关系查询达到实际规模 |
| Active Perception Engine | Agent + Tool | 信息增益调度无法由现有组合表达 |
| Prediction | Agent hypothesis | 出现可靠 action-conditioned predictor |
| World Model | 不实现 | 有明确收益和 benchmark |
| ContextSource Framework | `Agent.world` 单输入 | 第二个动态 source 重复逻辑明显 |
| World Service | 同进程 | 跨进程和高可靠恢复成为硬需求 |
| VisionSource Protocol | Host push frame | 多个真实 source implementation 出现 |
| Global Event Bus | 不实现 | 多订阅者环境事件成为刚需 |
| Autonomous Activation | Host policy | 常驻触发/仲裁复杂度出现 |
| MCP | Tool + Skill 为主 | 外部异构 Tool interoperability 成为需要 |

---

## 7.13 Codex 实施约束

本文已经包含 Initial Embodied Release 的完整架构与实施契约。实施时必须完整读取本文，不再依赖独立补丁，也不能把文档示例误认为当前仓库实现。

本文要求优先于与其冲突的 current implementation details；但现有 repository behavior、测试和公共契约只在本文明确要求修改时改变。

Codex 必须：

- 按 Phase 顺序完成全部实施；
- 在开始每个 Phase 前检查当前代码、测试与现有接口；
- 保持现有 Agent / Session / Run / Tool / Execution 唯一实现；
- 不新增本文未要求的 Framework、Manager、Registry 或 Runtime；
- 对文档未定义的小型实现细节采用最简单、确定性、可测试的方案；
- 不通过新增复杂抽象解决局部问题；
- 所有新增公共契约必须有单元测试；
- 所有 Context/Model 修改必须覆盖完整 closed-union consumers；
- 所有目录迁移同步修改 tests、examples、config、documentation 和内部 imports；
- 完成 Phase 0–6，而不是只实现 World 数据类型。

如果发现真正无法同时满足的设计冲突，优先保持 One Agent、One Runtime、One Owner per State、Evidence ≠ Truth、Action ACK ≠ Physical Success 等核心架构不变量，并明确报告冲突，不自行创建新的平行架构。

---

# 8. 最终架构结论

RoboAgent Embodied Integration 的目标，不是给 RoboAgent 增加一套机器人专用认知框架，而是让现有统一 Agent 获得正确的环境认知、直接多模态交互和物理闭环语义。

最终架构：

```text
                      RoboAgent
                         │
          ┌──────────────┼───────────────┐
          │              │               │
       Agent          Context          World
          │                              ▲
          │                              │
          │                           Claim
          │                              ▲
          │                              │
          │                         Observation
          │                              ▲
          │                              │
          │                       External World
          │                              ▲
          │                              │
          └────────── Tool ──────────────┘
```

直接用户交互：

```text
User / Direct Media
        │
        ▼
    Interaction
   ├── Vision
   └── Speech
        │
        ▼
 Message / Content
        │
        ▼
      Agent
```

持续环境认知：

```text
Camera / Audio / Robot / Simulation
                │
                ▼
       Observation Adapter
                │
                ▼
        Observation / Claim
                │
                ▼
              World
```

因此最终需要长期坚持以下边界：

```text
Interaction owns direct human/multimodal interaction.

Sources produce evidence.

Claims express cognition.

World owns environment cognition.

World does not decide what the model sees.

ContextManager owns relevance projection and model budget.

Agent remains the single general reasoning center.

Tools request information or change the environment.

Skills guide composition.

Tool success does not equal physical truth.

Physical results return as independent evidence.
```

RoboAgent 继续只有：

> **One Agent. One Runtime.**

但同时拥有：

> **Persistent World Awareness.**

Vision 和 Speech 作为 RoboAgent 原生的直接多模态交互能力统一归入：

```text
interaction/
```

而环境视觉、环境音频、Robot State 等持续感知则通过 Observation / Claim 接入 World。

长期 Memory、Belief、Knowledge、Scene Graph、Active Perception 和 World Model 可以在真实复杂度出现后建立在这些 Primitive 之上，但不会成为 Initial Release 的前置框架。

Initial Release 的实施决策已经固定：

- WorldEntry、Entity search 与 WorldLimits 使用正式数据契约；
- freshness 由 Claim validity 与 clock domain 决定，不由 revision 或 wall clock 猜测；
- Host 负责 source attribution / authorization，World 负责数据与配置边界验证；
- AgentLoop 只捕获 WorldSnapshot，ContextManager 负责 relevance projection 和完整 Model Budget；
- World query 是 Host 显式注册的 Tool，不是 Agent ambient capability；
- Vision / Speech 统一迁移到 `interaction/`，不保留旧 import 兼容层；
- VisionFrame 属于直接交互，ObservationRef 属于持久 World evidence；
- V1 selection、matching、arbitration 优先使用 deterministic rules，不新增隐藏的 LLM/embedding subsystem。

本文作为 RoboAgent Embodied Initial Release 的完整、独立实施规范。

最终工程原则保持：

> **One Agent, One Runtime, Persistent World Awareness, Composable Capabilities.**

> **Primitives, not Features.**

> **Design broadly; implement minimally.**