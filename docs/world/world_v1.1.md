# RoboAgent Agent–World Integration Architecture V1.1

**状态：Implementation Specification**

**基线：`docs/world/world_v1.0.md`**

**范围：Agent–World Integration / Embodied Grounding / Observation / Physical Closed Loop**

本文继承 `world_v1.0.md` 中未被明确修改的设计；若与本文冲突，以本文为准。

本文中的规范词：

- **MUST / 必须**：实现与验收的强制要求；
- **SHOULD / 应**：推荐默认行为，偏离时需要明确理由；
- **MAY / 可以**：可选扩展，不属于 V1.1 完成条件。

历史文档中对已迁移 `docs/design.md` 的引用应同步更新到当前 World 文档路径。

---

# 1. 定位、原则与总体架构

## 1.1 当前阶段

RoboAgent 已经形成三个明确的 canonical state owner：

```text
Conversation State
        ↓
      Session

Execution State
        ↓
 Run / Execution

Environment Cognition
        ↓
       World
```

World V1 已经具备：

```text
Observation
Claim
EvidenceRef
EntityRef
provenance
scope
clock-domain semantics
TTL / freshness
correction / supersedes

KNOWN
UNKNOWN
STALE
CONFLICTED

WorldSnapshot
world_query
world_find_entities
Context projection
```

因此 V1.1 的目标不再是继续建设一个更大的 World，而是补齐：

```text
Agent
World
Context
Tool
Execution
```

这些既有 primitives 之间缺失的组合契约，使 RoboAgent 能够稳定形成：

```text
Perceive
   ↓
Ground
   ↓
Act
   ↓
Observe
   ↓
Verify
```

的物理闭环。

V1.1 需要验证四类 embodied behavior：

```text
Environment Grounding
Active Observation
Decision-bound Revalidation
Physical Verification
```

它们是由已有 primitives 组合形成的行为能力，不对应新的四套 subsystem。

---

## 1.2 核心设计哲学

RoboAgent V1.1 必须继续延续 Pi Agent Harness 的：

```text
minimal
composable
explicit
extensible

Primitives, not Features
```

新增需求的设计顺序必须优先：

```text
reuse existing primitive
        ↓
compose existing primitives
        ↓
extend one existing primitive
        ↓
only then add one new generic primitive
```

而不是：

```text
new requirement
      ↓
new manager
new engine
new runtime
new subsystem
```

例如：

```text
World says STALE
+
Agent reasoning
+
Observation Tool
```

已经能够形成主动观察，因此当前不增加：

```text
ActivePerceptionManager
```

同样：

```text
decision WorldSnapshot
+
current World
+
Physical Tool
```

已经能够完成动作执行前再验证，因此当前不增加：

```text
WorldDependencyManager
PreconditionEngine
```

而：

```text
EntityRef
+
predicate
+
EntityRef-valued Claim
```

已经能够表达：

```text
cube_7.inside = basket_2
cube_7.on = table_1
cube_7.near = robot
```

因此 V1.1 不增加 Scene Graph subsystem。

---

## 1.3 Minimal

Core 只包含 Agent Harness 必须提供的通用机制。

如果已有 primitive 能够清晰表达某个行为，就不新增 Core abstraction。

例如：

```text
ToolEffect
+
Observation
+
World
+
Agent reasoning
```

已经能够实现动作后验证，因此当前不增加：

```text
VerificationEngine
PostConditionManager
```

Minimal 不意味着能力少，而是：

> 只把无法通过已有 primitive 清晰表达、且具有通用意义的最小机制加入 Core。

---

## 1.4 Composable

具身能力通过：

```text
World
Context
Agent
Tool
Execution
Observation
```

组合形成。

例如“抓取”不是 Core feature，而是：

```text
World query
↓
Agent grounding
↓
Observation Tool
↓
World update
↓
Physical Tool
↓
Observation Tool
↓
World
↓
Agent verification
```

同样的 primitives 还可以组合成：

```text
place
navigate
inspect
handover
search
open drawer
```

Core 不需要知道这些 feature 的完整业务语义。

---

## 1.5 Explicit

任何重要 capability 或状态变化都必须显式发生。

禁止：

```text
World UNKNOWN
→ hidden camera call

Tool succeeded
→ hidden World mutation

Agent has World
→ hidden perception permission

controller finished
→ assume physical success
```

必须：

```text
Agent
↓
explicit Tool
↓
Observation / Effect
↓
World / Execution
```

还必须保持：

```text
World read capability
≠
World write capability
≠
Physical execution capability
```

拥有 World 不等于拥有相机、VLM 或机器人控制权限。

---

## 1.6 Extensible

真实机器人和感知能力属于 Host / deployment：

```text
Camera
VLM
OpenCV
RGB-D
TF
ROS2
MoveIt
Nav2
Robot SDK
tracking
geometry
controller
```

RoboAgent Core 不依赖具体机器人、ROS2、MuJoCo、RealSense 或特定模型厂商。

新机器人只需要适配：

```text
Observation
Claim
Tool
```

这些稳定边界。

---

## 1.7 Core 与 Host 边界

Core：

```text
Agent
Session
Run / Execution
Context
Tool
Skill
World
```

Host / Deployment：

```text
Camera
VLM
RGB-D
TF
tracking
object association
robot state
motion planning
grasp planning
collision checking
controller
mfr3duo_ros2
```

原则：

```text
World = cognition
Tool = capability
Skill = guidance
Host = physical integration
```

---

## 1.8 总体架构

```text
                         Physical World
                              │
             ┌────────────────┼────────────────┐
             │                │                │
           Camera           RGB-D             ROS2
             │                │                │
             └────────── Host Adapters ───────┘
                              │
                      Observation / Claim
                              │
                              ▼
                           World
                              │
                       WorldSnapshot
                              │
                              ▼
                       ContextManager
                              │
                              ▼
                            Agent
                    ┌─────────┴─────────┐
                    │                   │
               World Query        Observation Tool
                    │                   │
                    └─────────┬─────────┘
                              │
                       Agent Reasoning
                              │
                              ▼
                       Physical Tool
                              │
                    source Snapshot
                              │
                       revalidation
                              │
                              ▼
                      physical backend
                              │
                              ▼
                         ToolEffect
                              │
                              ▼
                 independent Observation
                              │
                              ▼
                           World
                              │
                              ▼
                       next Model Turn
```

必须始终保持：

```text
Session transcript
≠
Execution facts
≠
World facts
```

---

# 2. World V1.1 数据、Resolution 与 Entity Search

V1.1 对 World 只做 Agent–World integration 所必须的增强。

主要目标：

```text
scope fidelity
deterministic resolution
typed entity filtering
candidate semantics
snapshot lookup
```

---

## 2.1 Scope 成为 WorldEntry identity 的组成部分

V1 中仅按：

```text
(subject, predicate)
```

聚合会丢失 scope。

V1.1 MUST 使用：

```text
(subject, predicate, scope)
```

作为 resolved entry 的 canonical identity。

建议增加：

```python
@dataclass(frozen=True, slots=True)
class WorldEntryKey:
    subject: EntityRef
    predicate: str
    scope: str | None
```

这是通用 value object，不是 subsystem。

`WorldEntry` MUST 保留：

```text
subject
predicate
scope
status
value
candidates
supporting_claims
evidence_refs
valid_at
```

Snapshot 构建也必须按：

```text
(subject, predicate, scope)
```

分别 resolve。

---

## 2.2 Scope 精确定义

V1.1 中：

```text
scope=None
```

表示：

> 精确的 unscoped namespace。

不是：

```text
all scopes
wildcard
inheritance
default scope
```

因此：

```python
await world.query(
    EntityRef("cube_7"),
    "pose",
    scope=None,
)
```

只查询 unscoped entry。

查询指定 scope：

```python
await world.query(
    EntityRef("cube_7"),
    "pose",
    scope="workspace",
)
```

V1.1 不提供隐式 cross-scope wildcard query。

同一 `EntityRef` 属于当前 `world_id` namespace。

V1.1 不支持 cross-world EntityRef relation。

---

## 2.3 WorldSnapshot lookup

V1.1 MUST 提供纯只读 lookup：

```python
snapshot.lookup(
    WorldEntryKey(
        EntityRef("cube_7"),
        "pose",
        "workspace",
    )
)
```

找不到：

```text
return None
```

禁止构造 synthetic UNKNOWN entry。

`lookup()` 只能读取 Snapshot 自身。

禁止：

```text
snapshot.lookup()
→ current World
→ clock refresh
→ sensor
→ perception
```

Snapshot 一旦创建，就是 immutable source state。

---

## 2.4 Resolution canonical pipeline

Resolution MUST 使用固定的五阶段流程：

```text
1. supersedes filtering
        ↓
2. grounding classification
        ↓
3. temporal classification
        ↓
4. state-stream selection + support selection
        ↓
5. status arbitration + candidate construction
```

禁止在 temporal classification 之前直接对全部 claims 进行“最新值筛选”。

---

### 2.4.1 Supersedes filtering

所有被 supersede 的 claim 先从当前事实判断中排除。

它们仍保留在历史 provenance 中，但不参与当前 resolution。

---

### 2.4.2 Grounding classification

保持 V1.0 规则。

具有有效 observation / grounded claim support 的：

```text
MEASUREMENT
INTERPRETATION
INFERENCE
```

可以成为 grounded cognition。

未 grounded 的：

```text
HYPOTHESIS
PRIOR
PREDICTION
```

不能把 physical truth 推到 KNOWN。

---

### 2.4.3 Temporal classification

每个未 supersede claim 独立分类成：

```text
LIVE
EXPIRED
UNRESOLVED
```

`UNRESOLVED` 表示：

> 当前不能可靠确定该 claim 是否位于有效时间区间。

例如：

```text
clock unavailable
future valid_at cannot yet be resolved
```

但：

```text
两个 clock domain 无法比较谁更新
```

不等价于 UNRESOLVED。

如果两个 clock 都能分别证明自己的 claim 当前 LIVE，则它们都属于 LIVE。

---

### 2.4.4 同一状态流的自然变化

TTL 表示 evidence 的 freshness 上限，不表示对象在整个 TTL 区间内保持原值。自然状态变化继续沿用 V1.0 §3.9：使用新 Claim + 新 `valid_at`，不使用表示认知纠错的 `supersedes`。

在完成 temporal classification 后，MUST 先按以下字段划分状态流：

```text
subject × predicate × scope × source × grounding class × clock_id
```

这只是 resolution 的内部 grouping key，不新增公开 primitive。`source` 必须标识一个连贯的状态生产流；Host MUST 为独立观察流使用不同 source，不能为了消除冲突把它们伪装为同一流。

对同一状态流中具有 `valid_at`、且已经判定为 LIVE 或 EXPIRED 的 claims，MUST 选择最大 `valid_at.nanoseconds` 对应的全部 claims。时间相同的不同值必须同时保留。选择发生在 LIVE 与 EXPIRED 的并集内，不能分别取最新后让旧 LIVE 值重新成为当前状态。

例如，同一 source、同一 clock：

```text
T0: pose=P0, LIVE
T1: pose=P1, LIVE, T1 > T0

→ select P1
→ KNOWN(P1)
```

即使 P0 的 TTL 尚未到期，它也不再代表该状态流的当前值；P0 仍保留在历史 provenance 中，未被标记为错误。

如果 T1 的新状态已经 EXPIRED，而 T0 的旧状态仍在其原始 TTL 内：

```text
→ select T1
→ STALE
```

不得恢复 T0 为 KNOWN。

UNRESOLVED claims 不得替代上述已确定时间状态的流头；它们作为未决依据继续保留。没有 `valid_at` 的 claims 也不得参与 recency 替换。无法确定时间关系时，保留不同值进入仲裁，不能使用 commit/received order 猜测先后。

---

### 2.4.5 Grounded 与 non-grounded 分开筛选

例如：

```text
T1 grounded measurement:
color = red

T2 newer hypothesis:
color = blue
```

两者都 LIVE。

不能：

```text
newer hypothesis wins
→ blue
```

正确：

```text
grounded LIVE red
+
non-grounded LIVE blue
↓
KNOWN(red)
```

因为 hypothesis 不参与 grounded current-truth competition。

---

### 2.4.6 独立证据与同值支持

完成状态流选择后，不同 source、不同 clock，以及同一流头同一时间的不同值，必须分别保留。不同 grounded LIVE 流头支持不同值时，属于当前证据冲突：

```text
source A: LIVE red
source B: LIVE blue

→ preserve both
→ CONFLICTED
```

不能跨状态流用“哪个更新”删除独立证据。

流头选择后，按 temporal class 和 grounding class 保留依据，再由 §2.7 确定输出的 claim set。输出 claims 按类型安全的 value identity 聚合为 candidates，同值支持 MUST 聚合全部输出 claims 的 provenance。候选按 value identity 稳定排序，`supporting_claims` 按 ClaimRef.id 排序，`evidence_refs` 按 `(kind, source or "", id)` 排序并去重。

自然更新、独立证据冲突、同值支持聚合是三个不同步骤，均不得让 non-grounded claim 替代 grounded claim。

---

### 2.4.7 跨不可比较 clock

例如：

```text
LIVE red(clock A)
LIVE blue(clock B)
```

两个 clock 都能独立判断 claim 为 LIVE，但无法比较谁更新：

```text
preserve both
→ CONFLICTED
```

不能为了 latest-selection 强行选择一个。

---

## 2.5 Status arbitration

最终状态按以下顺序确定。

本节和后续矩阵中的 LIVE / EXPIRED / UNRESOLVED 均指 §2.4 选择后保留的 claims；不同值的 LIVE 冲突指独立状态流或同一流头同一时间的冲突，不包括已被正常时间演进替代的历史值。

### A. 多个不同 grounded LIVE values

```text
>= 2 distinct grounded LIVE values
→ CONFLICTED
```

即使同时存在 UNRESOLVED claim，也仍然是 CONFLICTED。

例如：

```text
LIVE red
LIVE blue
UNRESOLVED green

→ CONFLICTED
```

因为 live contradiction 已经确定存在。

---

### B. 恰好一个 grounded LIVE value

如果没有不兼容的 grounded UNRESOLVED：

```text
→ KNOWN(value)
```

例如：

```text
LIVE red
UNRESOLVED red

→ KNOWN(red)
```

如果存在可能支持不同 value 的 grounded UNRESOLVED：

```text
→ UNKNOWN
```

例如：

```text
LIVE red
UNRESOLVED blue

→ UNKNOWN
```

---

### C. 没有 grounded LIVE

如果存在 grounded UNRESOLVED：

```text
→ UNKNOWN
```

否则如果存在 grounded EXPIRED：

```text
→ STALE
```

否则：

```text
→ UNKNOWN
```

---

## 2.6 非 grounded claims

如果已经存在 grounded LIVE truth：

```text
grounded LIVE red
+
hypothesis blue

→ KNOWN(red)
```

non-grounded hypothesis/prior/prediction 不得把 grounded current truth 降级为 UNKNOWN 或 CONFLICTED。

如果没有 grounded truth，它们可以作为：

```text
UNKNOWN candidates
```

暴露。

---

## 2.7 Candidate set 必须解释最终状态

Candidate set 不能只保留某一种 temporal class。

核心原则：

> **任何参与最终状态仲裁、并可能改变最终值判断的 candidate，都不得被输出阶段丢弃。**

最终输出必须同时保持：

```text
status
value
candidate values
supporting claims
evidence refs
```

一致。

---

### UNKNOWN

例如：

```text
LIVE red
+
UNRESOLVED blue

→ UNKNOWN
→ value = None
→ candidates = {red, blue}
```

不能只返回 blue。

同样：

```text
EXPIRED red
+
UNRESOLVED blue

→ UNKNOWN
→ candidates = {red, blue}
```

如果：

```text
UNKNOWN
+
no evidence
```

则：

```text
status = UNKNOWN
value = None
candidates = ()
supporting_claims = ()
evidence_refs = ()
```

这必须和：

```text
UNKNOWN with candidates
```

保持区分。

---

### CONFLICTED

例如：

```text
LIVE red
LIVE blue
UNRESOLVED green
```

MUST 保留选择后全部 grounded LIVE 与 grounded UNRESOLVED candidates：

```text
red
blue
green
```

green 不能被丢弃；搜索 green 时仍属于 UNCERTAIN MATCH。grounded EXPIRED 与 non-grounded candidates 不进入 CONFLICTED 的 candidate set。`value=None`，supporting claims 和 evidence refs MUST 精确覆盖所输出 candidates 的全部选中依据，不能依赖未定义的 relevance 判断决定集合成员。

---

### STALE

如果只有 expired grounded values：

```text
→ STALE
```

candidates 包含 relevant expired values。

---

### KNOWN

```text
status = KNOWN
value = current resolved value
```

candidate/provenance 保留支持当前 truth 的依据。

---

## 2.8 Resolution 测试矩阵

所有测试 MUST 同时断言：

```text
status
value
candidate values
supporting_claims
evidence_refs
```

不能只断言 status。

至少覆盖：

```text
LIVE red
→ KNOWN(red)

LIVE red + LIVE red
→ KNOWN(red)

LIVE red + LIVE blue
→ CONFLICTED

LIVE red + LIVE blue + UNRESOLVED green
→ CONFLICTED
→ candidates {red, blue, green}

LIVE red + UNRESOLVED red
→ KNOWN(red)

LIVE red + UNRESOLVED blue
→ UNKNOWN
→ candidates {red, blue}

EXPIRED red
→ STALE

EXPIRED red + UNRESOLVED blue
→ UNKNOWN
→ candidates {red, blue}

UNRESOLVED red
→ UNKNOWN

grounded LIVE red + hypothesis blue
→ KNOWN(red)

grounded LIVE red + newer hypothesis blue
→ KNOWN(red)

hypothesis red only
→ UNKNOWN

all claims superseded
→ UNKNOWN / empty candidates

LIVE red(clock A)
+
LIVE blue(clock B)
→ CONFLICTED

same stream, T0 LIVE pose=P0 + T1 LIVE pose=P1, T1 > T0
→ KNOWN(P1), candidates {P1}, no supersedes

same stream, T0 LIVE pose=P0 + T1 EXPIRED pose=P1, T1 > T0
→ STALE, candidates {P1}, never restore P0

same stream, LIVE pose=P0 + future UNRESOLVED pose=P1
→ UNKNOWN, candidates {P0, P1}

independent sources, LIVE pose=P0 + LIVE pose=P1
→ CONFLICTED, candidates {P0, P1}

same stream and same valid_at, LIVE red + LIVE blue
→ CONFLICTED, candidates {red, blue}

same selected value supported by multiple independent claims
→ one candidate, all selected supporting claims and evidence refs
```

---

## 2.9 Entity Search 只负责实体匹配

必须严格区分：

```text
Entity Matching
≠
Action Readiness
```

例如：

```text
cube_7.category = cube  KNOWN
cube_7.color    = red   KNOWN
cube_7.pose     = P     STALE
```

查询：

```text
category=cube
color=red
```

必须得到：

```text
cube_7 = CONFIRMED
```

因为 pose 不属于 filter。

之后 Agent 再查询：

```text
cube_7.pose
```

发现 STALE，再决定观察。

World Search 禁止把：

```text
pose
reachability
holding
gripper state
```

等任务依赖隐式加入实体匹配。

---

## 2.10 EntityCandidate 契约

建议目标类型：

```python
class EntityMatchStatus(Enum):
    CONFIRMED = "confirmed"
    POSSIBLE = "possible"


@dataclass(frozen=True, slots=True)
class EntityCandidate:
    entity: EntityRef
    scope: str | None
    status: EntityMatchStatus
    matched: tuple[str, ...]
    uncertain: tuple[str, ...]
    missing: tuple[str, ...]
    supporting_claims: tuple[ClaimRef, ...]
```

其中：

```text
matched
uncertain
missing
```

必须按 predicate 字典序稳定排序。

`supporting_claims` 也必须使用稳定 canonical ordering。

---

## 2.11 POSSIBLE 的确定性规则

对每个 filter predicate 独立分类。

### KNOWN

```text
value matches
→ MATCH

value mismatches
→ EXCLUDE
```

### STALE

candidate set 包含请求值：

```text
→ UNCERTAIN MATCH
```

否则：

```text
→ EXCLUDE
```

不能为了找到匹配值回溯任意历史 claim。

### CONFLICTED

candidate set 包含请求值：

```text
→ UNCERTAIN MATCH
```

否则：

```text
→ EXCLUDE
```

### UNKNOWN

如果 candidate set 包含请求值：

```text
→ UNCERTAIN MATCH
```

如果已有 candidates 但不包含请求值：

```text
→ EXCLUDE
```

如果该 predicate 完全没有 evidence/candidate：

```text
→ MISSING
```

---

### Entity final classification

如果任何 filter：

```text
EXCLUDE
```

则 entity 不进入结果。

如果所有 filters：

```text
MATCH
```

则：

```text
CONFIRMED
```

如果不存在 EXCLUDE，且：

```text
至少一个 MATCH / UNCERTAIN MATCH
+
至少一个 UNCERTAIN MATCH / MISSING
```

则：

```text
POSSIBLE
```

如果所有 filters 都是 MISSING：

```text
不返回该 entity
```

空 filters MUST 拒绝。

---

## 2.12 Entity Search ordering 与 truncation

排序固定：

```text
CONFIRMED
before
POSSIBLE
```

同类别内部：

```text
EntityRef.id ascending
```

保留：

```text
limit
truncated
```

SHOULD 增加：

```text
total_candidates
```

唯一性不能只判断：

```python
len(candidates) == 1
```

只有：

```text
len(candidates) == 1
AND
truncated == False
```

才能证明当前返回集合中唯一。

如果：

```text
include_possible=False
```

只能证明 CONFIRMED candidate 的唯一性，不能证明不存在 POSSIBLE entity。

对物理任务中的保守 grounding，SHOULD 使用：

```text
include_possible=True
```

或继续观察。

默认：

```text
include_possible=False
```

保持 strict behavior。

---

## 2.13 EntityRef filter 必须类型安全

Python API 的 filter value 必须支持：

```text
JsonValue | EntityRef
```

例如：

```python
EntityRef("basket_2")
```

必须和普通 JSON：

```python
{"entity_ref": {"id": "basket_2"}}
```

保持不同。

Agent-facing Tool schema 推荐：

```json
{
  "filters": [
    {
      "predicate": "color",
      "value_kind": "json",
      "value": "red"
    },
    {
      "predicate": "inside",
      "value_kind": "entity_ref",
      "entity_id": "basket_2"
    }
  ],
  "scope": "workspace",
  "include_possible": true,
  "limit": 16
}
```

只有：

```text
value_kind = entity_ref
```

才能构造 EntityRef。

重复 predicate：

```text
MUST reject
```

不定义覆盖、AND、OR 等隐式语义。

---

# 3. Agent–World 闭环执行契约

本章定义 Grounding、Observation、Decision Snapshot、Revalidation、Physical Dispatch 和 Verification 的组合语义。

这些是行为契约，不是新的 subsystem。

---

## 3.1 Environment Grounding

自然语言：

```text
"把面前那个红色方块拿起来"
```

由 Agent 转成结构化条件：

```text
category=cube
color=red
```

然后：

```text
world_find_entities
```

World 不负责自然语言理解。

正确分支：

```text
0 candidates
→ observe / ask user

1 CONFIRMED
AND not truncated
→ entity resolution may proceed

multiple
POSSIBLE exists
or truncated
→ query more / observe / ask
```

禁止：

```text
select first
random select
silent guess
```

---

## 3.2 Entity confirmed 不等于动作 ready

例如：

```text
cube_7
→ grounding CONFIRMED
```

但：

```text
cube_7.pose = STALE
```

仍然意味着：

```text
pick not ready
```

所以必须：

```text
Entity Grounding
↓
EntityRef
↓
Action-specific state query
↓
Action readiness
```

---

## 3.3 World Query 与 Observation Tool 分离

World Query：

```text
world_query
world_find_entities
```

只能读取已有 cognition。

不能：

```text
capture camera
call VLM
move robot
write World
```

Observation Tool：

```text
observe_scene
observe_object
locate_object
observe_robot_state
observe_gripper
```

负责获得新 evidence。

READ_ONLY / SIDE_EFFECTING 根据实际行为决定，而不是根据名字决定。

---

## 3.4 同步 Observation 是 V1.1 必需 reference path

同步 Observation MUST：

```text
capture
↓
perception
↓
Observation + Claims
↓
world.observe(...)
↓
commit
↓
Tool returns
```

如果 Tool 返回：

```text
status=committed
```

必须保证对应 World transaction 已完成。

推荐：

```json
{
  "status": "committed",
  "world_id": "mfr3duo-1",
  "world_revision": 103,
  "observation_ref": {
    "source": "perception/front",
    "id": "obs-42"
  }
}
```

---

## 3.5 异步 Observation 是可选扩展

V1.1 MAY 支持 async perception，但 reference path 不依赖它。

允许：

### Captured-first

```text
capture Observation
↓
commit Observation
↓
submit remote perception referencing ObservationRef
↓
task_id
↓
remote result
↓
world.add_claims()
```

### Result-owned

```text
submit
↓
remote result
↓
world.observe(observation, claims)
```

必须原子提交 Observation 和 Claims。

禁止：

```text
add_claims()
```

引用尚不存在的 ObservationRef。

如果支持 async task，Host SHOULD 提供：

```text
get_observation_task(task_id)
```

状态至少：

```text
accepted
running
succeeded
failed
cancelled
timed_out
```

callback 必须幂等并可去重。

迟到结果必须保留真实 `observed_at` 和 TTL，因此到达时可能已 STALE。

World 更新本身不得自动启动新的 Run。

---

## 3.6 Model Turn source WorldSnapshot

`decision_world_snapshot` 的正式语义是：

> 当前 Model Turn 构建 Context 时使用的 World source snapshot。

不是：

> 模型最终看到的全部完整事实。

真实路径：

```text
WorldSnapshot S100
        ↓
Context projection
        ↓
budget / selection / truncation
        ↓
ContextDataSegment
        ↓
Model
```

因此：

```text
decision_world_snapshot
```

只能证明 ToolCall 来源于以 S100 为 source snapshot 的 Model Turn。

不能证明 S100 中每个 entry 都被模型看到。

V1.1 不引入 partial snapshot。

如果未来需要审计哪些 entry 真正进入模型，可以增加 projection trace，但不属于 V1.1 必需能力。

---

## 3.7 ToolContext Snapshot 传播

`ToolContext` MUST 增加：

```python
decision_world_snapshot: WorldSnapshot | None
```

ToolExecutor 重建 ToolContext 时必须保留：

```text
run_id
session_id
message_id
decision_world_snapshot
```

---

### 同一 Model Turn

同一 model response 产生的所有 ToolCalls：

```text
MUST share the same message_id
MUST share the same decision_world_snapshot
```

---

### Nested Tool / Composite Tool

没有新的 Model Turn 时：

```text
execute_nested_tool(...)
```

子 Tool MUST 继承：

```text
run_id
message_id
decision_world_snapshot
```

Composite Tool、`execute_python` 内的 canonical nested Tool 调用同理。

禁止 nested Tool 自己重新抓 snapshot。

---

### Child Agent

Child Agent 是新的 reasoning boundary。

因此 parent 的：

```text
decision_world_snapshot
```

不能作为 child Agent 的 decision snapshot。

Child Agent 每个 Model Turn 必须根据自己的：

```text
child_agent.world
```

重新 capture。

即使 parent 和 child 绑定同一个 World instance，也要重新 capture。

---

### world=None

如果 Agent 没有 World：

```text
decision_world_snapshot = None
```

---

### Direct Tool invocation

没有经过 Model Turn 的直接 Tool invocation：

```text
decision_world_snapshot = None
```

依赖 decision context 的 reference physical Tool MUST 在 side effect 前拒绝：

```text
decision_context_missing
```

---

### World mismatch

Host physical Tool 如果绑定：

```text
world_id=A
```

但：

```text
decision_world_snapshot.world_id=B
```

MUST 在物理副作用前拒绝：

```text
world_mismatch
```

Generic Tool Runtime 不负责猜测 Tool 属于哪个 World。

---

## 3.8 新 Observation 必须形成新的 reasoning boundary

这是 V1.1 的核心不变量。

假设：

```text
Model Turn M1
source snapshot = S100
```

M1 中：

```text
observe_object
```

产生：

```text
claim_201
World S101
```

同一个 M1 内 nested：

```text
pick(
  decision_claim_refs=[claim_201]
)
```

MUST 被拒绝，因为：

```text
claim_201 ∉ S100
```

因此：

> 任何会改变 Agent 高层动作决策依据的新 Observation，都必须先返回 AgentLoop，再进入新的 Model Turn。

正确：

```text
M1
source S100
↓
observe
↓
World S101
↓
Tool returns
↓

M2
capture S101
↓
Agent reasons
↓
Physical Tool
```

禁止：

```text
nested observe
↓
hidden snapshot refresh
↓
nested physical action
```

---

### Revalidation 与重新 reasoning 的边界

Physical backend 可以在 dispatch 前做：

```text
fresh sensor read
TF check
collision check
controller readiness check
```

这些属于：

```text
revalidation / low-level safety
```

可以拒绝动作。

但不能偷偷改变：

```text
抓哪个对象
去哪个目标
用户指的是谁
```

即：

```text
revalidation may reject
```

但不能：

```text
revalidation silently replan high-level intent
```

---

## 3.9 Reference physical Tool 的 decision evidence 必须完整

`decision_claim_refs` 不能只是任意几个合法 ClaimRef。

Reference physical Tool 必须验证两类依据：

```text
目标为什么被选中
+
动作执行需要哪些 World facts
```

不新增 `WorldDependency` Core primitive。

---

### Selection evidence

例如 pick：

```text
target category
target color
other semantic filters used for grounding
```

### Action-readiness evidence

例如：

```text
target pose
coordinate frame
holding state
container state
```

具体 required roles 由 Host Tool 定义。

---

### Reference schema

reference Tool 的参数可以等价表示为：

```json
{
  "target_entity": "cube_7",
  "decision_evidence": {
    "selection": {
      "scope": "workspace",
      "filters": [
        {
          "predicate": "category",
          "value_kind": "json",
          "value": "cube"
        },
        {
          "predicate": "color",
          "value_kind": "json",
          "value": "red"
        }
      ],
      "claim_refs": [
        "claim-category-cube",
        "claim-color-red"
      ]
    },
    "readiness": [
      {
        "role": "target_pose",
        "subject": "cube_7",
        "predicate": "pose",
        "scope": "workspace",
        "claim_refs": [
          "claim-pose"
        ]
      }
    ]
  }
}
```

这只是 Tool-specific structured input，不升级为 Core framework。

---

## 3.10 Selection evidence 完整性

Tool MUST 验证：

1. selection 的 entity 就是 target entity；
2. 所有 predicate 都对应 source Snapshot 中相同：
   ```text
   subject
   predicate
   scope
   ```
3. source Snapshot 中对应 entry 的状态全部是：
   ```text
   KNOWN
   ```
4. source value 与 selection filter 一致；
5. claim refs 能支持这些 source facts；
6. Agent 声明的 selection constraints 没有被遗漏。

因此：

```text
source:
color = CONFLICTED(red, blue)

Agent:
select red candidate

current:
color = KNOWN(red)
```

仍然不能执行。

因为：

```text
the original physical decision
was made from uncertain evidence
```

必须拒绝：

```text
decision_source_not_ready
```

然后进入新的 Model Turn，再基于新的 KNOWN red 做决定。

---

## 3.11 Readiness evidence 完整性

每种 reference physical Tool MUST 定义 required evidence roles。

例如 pick 最少：

```text
target_selection
target_pose
```

如果还依赖：

```text
holding
container
workspace state
```

也必须显式声明。

缺少 required role：

```text
decision_evidence_incomplete
```

引用其他 subject：

```text
decision_evidence_mismatch
```

source entry 为：

```text
STALE
UNKNOWN
CONFLICTED
```

则：

```text
decision_source_not_ready
```

都必须发生在物理副作用之前。

---

## 3.12 验证语义事实，不验证旧 ClaimRef 是否仍活着

例如：

```text
claim_1: color=red
↓ superseded
claim_2: color=red
```

当前语义仍相容：

```text
PASS
```

但：

```text
claim_1: color=red
↓ superseded
claim_2: color=blue
```

则：

```text
CHANGED
```

ClaimRef 是决策 provenance，不是要求同一个 claim 永远保持 current。

---

## 3.13 Grounding completeness 的边界

Physical Tool 不重新解释用户自然语言。

所以 Tool 不负责证明：

```text
“红色方块”
```

是否被 LLM 正确解析成：

```text
category=cube
color=red
```

这是 Agent grounding 的责任。

但一旦 Agent 声明：

```text
category=cube
color=red
```

作为 selection filters，reference Tool 必须完整验证这些声明，不允许只传 category 而漏掉 color。

职责保持：

```text
Agent owns grounding

Tool owns execution-time validation
```

---

## 3.14 Pre-execution Revalidation

最终 revalidation MUST 发生在：

```text
approval
high-level planning
```

之后，但在：

```text
actual physical dispatch
```

之前。

Host/backend SHOULD 尽量缩小：

```text
final validation
→
dispatch
```

窗口。

统一结果：

```text
VALID
STALE
CHANGED
AMBIGUOUS
UNAVAILABLE
UNSAFE
```

只有：

```text
VALID
```

允许 dispatch。

---

### Identity

必须确认当前：

```text
EntityRef
```

仍对应原本的 physical instance。

identity correction / association change 如果破坏原语义：

```text
CHANGED
```

---

### Decision facts

所有显式 decision evidence 对应的当前事实必须：

```text
KNOWN
AND
compatible with decision value
```

只要属于决策依据，就必须检查。

---

### Pose

不能只比较 XYZ。

Host Tool MUST 定义：

```text
position tolerance
orientation tolerance
frame
unit
maximum age
```

统一单位：

```text
position: metre
angle: radian
time: ObservationTime / ns
```

具体 tolerance 属于 Tool/backend config，不进入 World global policy。

---

### Frame / TF

如果比较 pose 不在同一 frame：

```text
MUST use valid transform for the relevant observation time
```

禁止默认：

```text
latest TF
identity transform
```

无法可靠获得：

```text
UNAVAILABLE
```

---

### Time

World TTL 是最低 freshness gate。

Physical Tool MAY 使用更严格：

```text
max_dependency_age
```

但不能放宽已经 STALE 的事实。

跨 clock 无法可靠比较且 Host 无可信映射：

```text
UNAVAILABLE
```

---

### Backend state

真正 dispatch 前 Host 还必须验证：

```text
controller ownership
task ownership
robot readiness
resource availability
reachability
collision constraints
```

这些不是 World 的职责。

---

## 3.15 Model Turn 与 message_id

一个 Model Turn 定义为：

> 一次 model invocation 产生的一个 canonical assistant/model-response message。

其 identity 为：

```text
(run_id, message_id)
```

Tool result 返回后，如果 Agent 再次调用模型：

```text
new model invocation
→ new Model Turn
→ new message_id
```

即使仍在同一 Run 内。

---

## 3.16 同一 Model Turn 只允许一个 physical action attempt

同一个 `(run_id, message_id)` 中，最多允许一个 independent physical world-mutating action attempt。

这不是 Generic Tool Runtime 的新 Runtime，而是 Host physical execution policy。

---

### Canonical top-level order

同一个 model response 中：

```text
call[0]
call[1]
call[2]
...
```

按模型返回的 canonical order 排序。

不能由：

```text
async scheduling
completion order
lock race
```

改变。

---

### Physical-capable wrapper

能够通过 canonical nested execution 调用 physical Tool 的 wrapper，例如允许物理 nested Tool 的：

```text
execute_python
Composite Tool
```

必须被 Host / Tool policy 显式分类为：

```text
physical-capable
```

如果 wrapper 未声明为 physical-capable：

```text
nested physical Tool invocation
MUST be rejected
```

不能运行时隐式升级权限。

---

### Physical lane

同一 Model Turn 中：

```text
direct physical Tools
+
physical-capable wrappers
```

必须进入同一个 deterministic physical lane。

physical lane 按：

```text
top-level canonical ToolCall order
```

串行处理。

普通、不能 transitively dispatch physical action 的 read-only Tool 可以继续并发。

---

### 现有接口映射与状态 ownership

physical lane 是上述顺序约束的名称，不是新的队列、调度器或 Runtime。

Reference implementation MUST 使用现有接口落实：

- Host 在构造并注册 Tool 时显式绑定 direct physical、physical-capable wrapper 或普通 Tool 分类；分类不进入 World，也不由 Tool 名称或 `ToolEffectKind.SIDE_EFFECTING` 猜测。
- direct physical Tool 和 physical-capable wrapper MUST 配置现有 `Tool.execution_mode = ToolExecutionMode.SERIAL`。现有 `ToolExecutor.execute()` 在 batch 含 SERIAL Tool 时按 canonical order 串行处理整个 batch，reference path 允许普通 read-only Tool 随该 batch 串行执行。
- Host 通过现有 `ToolExecutionPolicy.evaluate(call, tool, context)` 检查分类与 wrapper 调用权限；physical Tool 在 execution-time validation 入口消费 slot，并完成 evidence/backend 验证。policy 只负责允许或拒绝，不新增排序 API，也不在 batch preparation 时提前消费 physical slot。
- wrapper 必须等待每次 canonical nested Tool 调用完成后才开始下一次；MUST 拒绝并发 nested physical-capable 调用。Nested 调用沿用当前顺序上下文，不重新获取外层 lane 的执行权，避免 wrapper 等待自身的 nested Tool 而死锁。

slot 是执行状态，MUST 归属于现有 Run/Execution，按 `(run_id, message_id)` 隔离。其最小数据是 unused/consumed 状态与首次 physical attempt 的 call identity；wrapper ancestry 也沿现有 execution context 传播。Host policy 和 Tool 使用同一轮执行状态，不能各自创建独立 slot。

Phase 2 可以为现有 execution/context 补充这一最小状态及访问路径，不得新增 Manager、Registry、Runtime 或平行 execution store。slot 必须覆盖完整 top-level batch 和 nested settlement；单个 Tool 或 wrapper 返回不得提前清除它。Model Turn 完成并且 nested work 已 settled 后释放该轮数据，Run 结束时完成剩余清理。

Child Agent 使用自己的 run_id、message_id 和 slot。多个 Run/Child Agent 对同一机器人发起动作时，仍必须通过 Host backend 的 controller/task ownership 检查；每轮 slot 不替代跨 Run 的物理资源互斥。

清理 slot 不表示旧物理任务已停止。UNKNOWN 任务的 task_id、控制权与后续状态查询继续由 Host backend 持有，直到满足 §3.17 的终态要求。

普通 read-only Tool 与 physical lane 同时并发是可选优化；reference implementation 不以实现混合 batch 的并发调度为完成条件。

---

### Nested execution order

例如：

```text
1. execute_python(...)
   └─ later nested pick(...)

2. place_object(...)
```

必须先处理 top-level call 1。

如果它内部真正触发：

```text
pick(...)
```

则该 nested pick 成为本 Turn 的第一个 physical action attempt。

随后：

```text
place_object
→ fresh_decision_required
```

Nested traversal 使用：

```text
depth-first execution within that top-level call
```

在 wrapper 返回前，不开始下一个 physical-lane top-level call。

---

### Slot 消费时机

physical slot 在：

> canonical traversal 第一次到达 physical world-mutating Tool，并开始 execution-time validation 时

被消费。

不是等 actuator 真正 dispatch 后才消费。

因此：

```text
first physical call
→ precondition FAILED
```

也不能自动尝试同 Turn 第二个 physical action。

必须回到新的 Agent reasoning boundary。

这样防止：

```text
action A cannot run
→ silently try action B
```

---

### Wrapper 未触发 physical action

如果 physical-capable wrapper 完成但从未真正进入 nested physical Tool：

```text
slot remains unused
```

下一个 canonical physical-lane call 可以成为该 Turn 的 physical attempt。

---

### UNKNOWN / FAILED / CANCELLED

physical slot 一旦被消费：

```text
UNKNOWN
FAILED
CANCELLED
SUCCEEDED
```

都不会释放给同 Turn 的其他 physical action。

---

## 3.17 UNKNOWN、取消与控制权

必须保持：

```text
Run.cancel()
≠
physical robot stopped
```

如果 physical action 可能在 RPC 返回、超时或断连后继续，Host backend MUST 提供：

```text
task_id
get_task_status
cancel / stop
```

如果：

```text
controller accepted
↓
network disconnected
```

结果必须：

```text
ToolEffectStatus.UNKNOWN
```

在任何新的可能冲突 physical action 前，Host 必须证明旧任务：

```text
completed
failed with no continuing effect
cancelled and stopped
```

之一。

否则：

```text
do not retry
do not dispatch conflicting action
```

必要时要求 operator intervention。

---

## 3.18 Physical Verification 必须依赖动作后的 evidence closure

必须明确：

```text
new WorldSnapshot
≠
new physical evidence
```

以及：

```text
new World revision
≠
target re-observed
```

battery 更新、迟到 VLM 都可能改变 revision。

所以 physical verification 必须依赖：

```text
post-action evidence
```

---

### Completion barrier

Physical backend MUST 提供动作完成 barrier。

优先使用：

```text
ObservationTime
```

或 Host 可以证明单调的 capture clock。

验证 Observation 必须能够证明：

```text
sampled after action completion barrier
```

仅：

```text
received_at > completion
```

不够，因为设备可能返回旧 buffer frame。

跨 clock domain 无法证明先后：

```text
verification remains UNKNOWN
```

---

### Evidence closure

所有用于生成 physical verification Claim 的动态 evidence 都必须满足：

```text
observed_after(action_completion_barrier)
```

例如：

```text
robot.holding = cube_7
```

如果依赖：

```text
gripper aperture
gripper effort/contact
cube pose
gripper pose
co-motion
```

这些动态信息都必须来自 barrier 之后。

允许复用静态信息：

```text
camera intrinsics
fixed calibration
robot kinematic description
object nominal geometry
```

但不能：

```text
new gripper observation
+
old cube pose
→ new holding
```

如果 verification Claim 经由其他 Claims 推导，动态 evidence chain 也必须递归满足 barrier。

World 只保存 provenance；是否满足 barrier 属于 Host verification contract。

---

## 3.19 holding / inside / on

### holding

禁止从：

```text
controller finished
```

或：

```text
gripper closed
```

单独推出：

```text
robot.holding = cube_7
```

Host adapter 应根据：

```text
aperture / effort / contact
+
target identity
+
relative motion / co-motion
+
object tracking
```

产生：

```text
INTERPRETATION Claim
```

MuJoCo reference SHOULD 至少结合：

```text
contact
+
relative transform / co-motion
```

---

### inside

必须根据 post-action geometry：

```text
object geometry
receptacle interior geometry
pose
configured tolerance
```

确定。

### on

必须根据：

```text
support surface
horizontal overlap
vertical relation
contact / height tolerance
```

确定。

这些 deterministic geometry rules 属于 Host adapter，不属于 World。

Physical Tool 本身不得因为命令完成就直接写：

```text
holding=True
inside=True
on=True
```

---

## 3.20 Place / task completion

reference tabletop 中：

```text
cube inside basket
+
robot still holding cube
```

不能认为 place 成功。

place 完成至少要求：

```text
target inside receptacle
AND
target released
AND
target stable
```

语义上：

```text
cube_7.inside = basket_2     KNOWN

robot.holding != cube_7      KNOWN

placement stability          verified
```

`released` 不能只来自：

```text
commanded gripper=open
```

必须来自 post-action evidence。

---

### MuJoCo stability

reference example SHOULD 定义：

```text
object remains inside receptacle
for >= 2 post-action observations

AND

linear velocity < configured threshold

AND

angular velocity < configured threshold

AND

no gripper attachment/contact
indicating continued holding
```

连续 observation 之间必须有正时间间隔。

参数：

```text
settle_duration
max_linear_velocity
max_angular_velocity
```

属于 example/backend config，不属于 World global policy。

---

# 4. Perception 与真实机器人集成契约

真实感知与控制继续属于 Host，但 V1.1 必须明确最低接口边界。

---

## 4.1 高频 sensor 不直接成为高频 World history

必须保持：

```text
Sensor Sampling Frequency
        ≠
Semantic World Update Frequency
        ≠
Agent Reasoning Frequency
```

例如：

```text
JointState       500 Hz
Camera            30 Hz
Depth             30 Hz
tracking        5~30 Hz
Cloud VLM       demand driven
Agent           task driven
```

Host adapter MUST 使用：

```text
latest-only
coalescing
sampling
change threshold
```

等机制。

禁止：

```text
500 Hz JointState
→ 500 Claims/s
```

---

## 4.2 Camera 与 VisionBuffer

Camera：

```text
30 FPS
↓
VisionBuffer
↓
latest / nearest frame
```

只有：

```text
Agent requests observation
or
Host has explicit semantic refresh policy
```

才进入：

```text
OpenCV
Cloud VLM
RGB-D
```

不能把每个 frame 都转换成 Agent Turn。

---

## 4.3 RGB、Depth、CameraInfo

RGB-D adapter MUST 明确同步：

```text
RGB timestamp
Depth timestamp
CameraInfo
TF timestamp
```

如果硬件提供 aligned/synchronized streams：

```text
use hardware relation
```

否则 Host MUST 配置：

```text
max_rgb_depth_skew
```

超过阈值：

```text
geometry observation fails
```

不得强行融合。

统一几何单位：

```text
distance: metre
pixel: pixel
orientation: normalized quaternion
```

原始 depth 单位在 adapter 边界转换。

---

## 4.4 TF

3D geometry Claim MUST 携带：

```text
frame_id
observed_at
```

TF lookup 必须针对对应 observation time。

禁止：

```text
historical TF missing
→ silently use latest TF
```

如果 deployment 有可信策略认为 latest 可替代 requested time，必须显式配置并记录 provenance。

---

## 4.5 Cloud VLM

Cloud VLM 负责：

```text
what
```

例如：

```text
category
color
semantic attribute
coarse semantic relation
```

不负责 trusted robot XYZ。

结构化输出 SHOULD 至少包含：

```text
source ObservationRef
local detection/candidate id
category
semantic attributes
2D region / bbox when available
```

禁止：

```text
VLM prose
→ arbitrary XYZ
→ robot motion
```

正确链：

```text
VLM semantic target
+
RGB-D
+
intrinsics
+
TF
↓
3D geometry Claim
```

---

## 4.6 Entity association

Host perception backend 负责：

```text
detection
↓
tracking / association
↓
stable EntityRef
```

World 不实现 ObjectTracker。

如果无法确认两个 observation 是同一 instance：

```text
do not force merge
```

误关联通过：

```text
correction
supersedes
```

修正。

---

## 4.7 TTL 与 freshness

不同 source / predicate SHOULD 使用不同 TTL。

例如：

```text
robot pose
object pose
semantic color
semantic category
```

生命周期不同。

TTL 属于 Claim / source contract。

不建立一个全局 World timeout。

Physical Tool 可以使用更严格：

```text
max_dependency_age
```

---

## 4.8 mfr3duo bridge

RoboAgent 不要求 `mfr3duo_ros2` 本身变成 Agent Framework。

bridge 最少分为：

```text
State side
Action side
Task-control side
```

### State side

提供 Agent 真正需要的 state：

```text
base pose
arm state
gripper state
controller readiness
active physical task
```

不要求全部 joint telemetry 持久写入 World。

### Action side

只有底层真实存在 capability 时才注册 Tool，例如：

```text
pick_object
place_object
navigate_to
```

不能为了满足上层 API 而伪造不存在的能力。

### Task-control side

长期动作至少需要：

```text
task_id
status
cancel / stop
terminal state query
```

以支撑 UNKNOWN 与 Run cancellation。

---

# 5. Reference Implementation、测试与实施阶段

V1.1 必须通过真实闭环 reference 证明契约，而不是只通过文档或 deterministic demo。

---

## 5.1 MuJoCo Tabletop Reference

新增最小场景：

```text
table
red cube
blue cube
basket
simple manipulator / gripper
camera
```

目标不是做高级 manipulation，而是验证：

```text
grounding
observation
World update
decision binding
revalidation
physical execution
post-action evidence
verification
```

必须使用真实 MuJoCo physics。

禁止通过：

```text
set object pose directly
set holding=True directly
```

伪造 pick/place 成功。

---

## 5.2 Reference Tools

至少：

```text
world_query
world_find_entities

observe_scene
observe_object

pick_object
place_object
```

可以根据需要增加：

```text
observe_gripper
locate_object
get_task_status
cancel_task
```

---

## 5.3 标准闭环必须按真实 Model Turns 描述

任务：

```text
Put the red cube in the basket.
```

不能把多个 Tool result 消费过程都写成同一个 Turn。

正确示例：

```text
M1
Agent → world_find_entities(
    category=cube,
    color=red,
    include_possible=true
)

Tool → cube_1 CONFIRMED
       truncated=false
```

Agent 读取结果后，新的模型调用：

```text
M2
Agent → world_query(cube_1.pose)

Tool → STALE
```

Agent 读取结果后：

```text
M3
Agent → observe_object(cube_1)

Tool:
capture
→ geometry
→ World commit
```

Observation 进入新的 reasoning boundary：

```text
M4
new source WorldSnapshot

Agent → pick_object(
    target=cube_1,
    decision_evidence={
        selection:
            category=cube
            color=red
        readiness:
            target_pose
    }
)

Tool:
validate source evidence
→ compare current World
→ backend validation
→ physical dispatch
→ controller result
```

然后重新观察：

```text
M5
Agent → observe_manipulation_state(cube_1)

Host:
fresh post-action evidence
→ World:
robot.holding = cube_1
```

新的 World 再进入：

```text
M6
Agent → place_object(
    target=cube_1,
    receptacle=basket_1,
    decision_evidence=...
)

Tool:
revalidate
→ physical dispatch
```

之后：

```text
M7
Agent → observe_manipulation_state(...)

Host:
post-action evidence closure
→ cube_1.inside = basket_1
→ robot no longer holding cube_1
→ placement stable
```

最后：

```text
M8
Agent verifies:
inside
AND
released
AND
stable

→ task complete
```

如果 Context 本身已经包含足够信息，可以省略某些显式 `world_query` ToolCall。

但原则保持：

> 任何基于前一个 Tool result 重新进行模型决策的步骤，都属于新的 Model Turn。

---

## 5.4 Composite / execute_python 的边界

Composite Tool 内部如果只是：

```text
read
compute
format
```

可以留在同一 Model Turn。

但如果内部：

```text
observe
↓
获得新的 environment evidence
```

这些 evidence 不能被同一个 Model Turn 的新 high-level physical decision 使用。

必须：

```text
return to AgentLoop
↓
new model invocation
↓
new message_id
↓
new WorldSnapshot
```

---

## 5.5 必测异常场景

至少覆盖：

```text
two confirmed matching objects

confirmed + possible candidates

truncated entity search

all filter predicates missing

duplicate search predicate

STALE target pose

CONFLICTED semantic property

expired + unresolved candidates

LIVE red + UNRESOLVED blue

LIVE red + LIVE blue + UNRESOLVED green

same-source pose update before previous TTL expires

newer stream head expired while older state remains within TTL

independent sources report different LIVE poses

same-stream equal-time conflicting values

CONFLICTED candidate membership and provenance are deterministic

cross-clock live conflict

newer non-grounded claim
with older grounded LIVE truth

target moves after decision

semantic target correction after decision

same semantic value replaced by newer Claim

unrelated World revision change

decision World ID mismatch

missing decision evidence

incomplete selection evidence

source selection entry not KNOWN

nested Tool snapshot propagation

child Agent captures fresh snapshot

observe then nested physical Tool in same Turn

execute_python with nested pick
before direct place_object

multiple physical ToolCalls in one Model Turn

physical wrapper without physical-capable permission

concurrent nested physical-capable calls rejected

nested physical Tool does not reacquire outer lane ownership

slot survives wrapper return and is isolated between Model Turns / Runs

UNKNOWN task remains blocked after Run slot cleanup

missing historical TF

RGB/depth skew exceeds max_rgb_depth_skew

controller ACK then disconnect

Run cancellation while task active

trajectory completed but grasp failed

new World revision without new target evidence

new gripper evidence combined with old object pose

object inside basket but still held

camera 30 FPS without 30 Agent turns
```

---

## 5.6 证据等级

验收必须区分不同 evidence level。

### Level 0 — Runtime Contract

使用：

```text
ScriptedModel / deterministic Model
```

验证：

```text
World
Context
AgentLoop
ToolExecutor
scope
resolution
candidate semantics
snapshot propagation
nested execution
physical dispatch gate
```

只能证明 Runtime contract。

---

### Level 1 — Physics Closed Loop

使用真实 MuJoCo：

```text
contact
motion
grasp
release
post-action observation
holding
inside
stability
```

不能直接修改 object pose/holding。

这一层证明 physical closed-loop contract。

---

### Level 2 — Real Model Grounding

使用真实云端 LLM/VLM，在 MuJoCo 或 sensor capture 上验证：

```text
natural-language grounding
ambiguity handling
active observation choice
```

ScriptedModel 不能代替。

无 credentials 的 CI 可以 skip，但必须独立统计。

---

### Level 3 — Real Sensor / Bridge Integration

验证：

```text
RGB
Depth
CameraInfo
TF
mfr3duo state bridge
Host timing
World ingestion
```

接口代码存在不能等同于真实 bridge 验证。

---

### Level 4 — Real Physical Manipulation

只有真正存在：

```text
physical robot
manipulation backend
post-action physical evidence
```

并完成 pick/place 闭环时，才可以声称真实机器人 manipulation 已验证。

---

## 5.7 实施阶段

### Phase 1 — World Contract Hardening

必须完成：

```text
WorldEntryKey

scope-preserving Snapshot

scope-aware query

WorldSnapshot.lookup()

resolution canonical pipeline

grounded/non-grounded split

same-source temporal state evolution without supersedes

no revival of older state after the stream head expires

independent-source / equal-time conflict preservation

mixed-state matrix

candidate/provenance completeness

EntityMatchStatus

POSSIBLE deterministic rules

typed EntityRef filters

duplicate predicate rejection

stable candidate ordering

truncation / uniqueness semantics
```

同时同步更新所有仍使用：

```text
(subject, predicate)
```

二元 key 的 Context / protection / budget consumer。

---

### Phase 2 — Decision Context Propagation

完成：

```text
Model Turn source WorldSnapshot
↓
ToolContext
```

覆盖：

```text
ToolExecutor reconstruction
message_id preservation
multiple ToolCalls
nested Tools
Composite Tools
execute_python nested Tools
Child Agent
direct Tool invocation
world=None
world mismatch
physical-capable wrapper classification
physical lane
canonical ordering
single physical slot
existing SERIAL Tool / ToolExecutionPolicy mapping
Run/Execution ownership and settlement cleanup
sequential nested wrappers without lane reacquisition
```

完成标准：

```text
ToolContext reconstruction MUST preserve:
run_id
session_id
message_id
decision_world_snapshot
```

---

### Phase 3 — Closed-loop Embodied Reference

完成：

```text
sync Observation Tool

new reasoning boundary after observation

complete selection evidence

complete readiness evidence

source evidence must be KNOWN

pre-dispatch revalidation

single physical action attempt per Turn

UNKNOWN handling

task status / cancellation

completion barrier

post-action evidence closure

holding / inside / released / stability

MuJoCo physical pick/place
```

完成标准：

```text
observe → new evidence
MUST NOT
be consumed by a physical action
until a new Model Turn captures it
```

以及：

```text
task completion
MUST depend on post-action evidence
not merely a newer World revision
```

---

### Phase 4 — Real Integration

接入：

```text
Cloud VLM
RGB-D
CameraInfo
TF
entity association
mfr3duo bridge
```

严格按照本文 Host contract。

交付时必须明确达到：

```text
Level 2
Level 3
Level 4
```

中的哪一级。

不能把接口代码存在等同于真实物理验证。

---

## 5.8 测试策略

各 Phase 开发期间只执行必要：

```text
unit
smoke
targeted integration
```

全部完成后统一执行：

```text
World tests
Context tests
AgentLoop tests
ToolExecutor tests
nested execution regression
MuJoCo end-to-end
failure scenarios
cloud integration where credentials exist
bridge integration where environment exists
ruff
mypy
compile
dependency lock checks
```

最终重点验收：

```text
scope never disappears

resolution is deterministic

state-stream selection never lets
non-grounded claims override grounded truth

natural state evolution does not misuse supersedes

expired stream head never revives older state

independent-source and equal-time LIVE conflicts are preserved

CONFLICTED includes all selected grounded LIVE / UNRESOLVED candidates

UNKNOWN candidate set explains UNKNOWN status

cross-clock non-orderability
does not erase independently known freshness

truncated search never proves uniqueness

entity matching != action readiness

one Model Turn has one source WorldSnapshot

nested Tool never silently refreshes decision snapshot

child Agent captures its own snapshot

new observation requires a new reasoning boundary

decision grounding evidence is complete and explicit

source decision facts must have been KNOWN

same semantic fact may survive Claim replacement

physical dispatch order is deterministic

nested physical calls share the same physical lane

physical lane reuses existing SERIAL execution and Host policy

slot ownership follows Run/Execution through nested settlement

one Model Turn performs at most one physical action attempt

Tool success != physical success

new revision != new physical evidence

post-action verification uses evidence closure

UNKNOWN physical effects are never blindly retried

high-rate sensor streams
do not become high-rate Agent reasoning
```

---

# 6. 非目标与后续演进

V1.1 明确不增加：

```text
SceneGraph subsystem
SpatialMemory
EntityResolverManager
ActivePerceptionManager
ResolutionManager
GroundingManager
WorldDependencyManager
PreconditionEngine
PostConditionManager
VerificationEngine
PhysicalRuntime
RobotActionRuntime
long-term World persistence
episodic memory
semantic memory
multi-robot shared World
```

剩余问题优先通过已有 primitives 的契约组合解决：

```text
WorldEntry
WorldSnapshot
EntityCandidate
ClaimRef
ToolContext
message_id
ToolEffect
Observation
AgentLoop
```

---

## 6.1 WorldDependency

如果未来：

```text
pick
place
navigate
handover
```

都开始重复携带：

```text
WorldEntryKey
ClaimRef
expected value
freshness policy
comparison rule
```

再评估：

```text
WorldDependency
```

当前仍通过：

```text
ClaimRef
WorldSnapshot
current World
Tool implementation
```

完成。

---

## 6.2 PostCondition

如果大量 Tool 都重复：

```text
fresh physical evidence
↓
deterministic predicate
↓
success / failure
```

再考虑：

```text
PostCondition
```

V1.1 继续由：

```text
Host evidence
+
World
+
Agent reasoning
```

完成。

---

## 6.3 Scene Graph

只有：

```text
EntityRef
+
predicate
+
EntityRef-valued relation
```

不能满足真实任务中的：

```text
graph traversal
relation chaining
subgraph query
```

时，再评估 Scene Graph。

不能因为参考项目使用 Scene Graph，就提前复制。

---

## 6.4 Persistence 与 Memory

未来 World persistence 必须保持：

```text
deserialize
≠
truth restored
```

恢复 evidence 后必须重新判断：

```text
clock validity
freshness
source validity
```

旧 physical fact 通常恢复成：

```text
STALE
or
UNKNOWN
```

而不是直接 KNOWN。

长期 episodic / semantic knowledge 应属于 Memory，而不是 current World。

---

## 6.5 当前实现状态

本文仍是 V1.1 的规范契约，代码与验证证据必须分别报告。

当前仓库已实现四个 Phase 的代码路径：

- World：三元 identity、状态流时间演进、独立证据冲突、完整 candidate/provenance、POSSIBLE search、typed filters 与纯 Snapshot lookup；
- Context：protection/budget 三元 key 迁移，以及带类型标签的 filter 引用识别；
- Tool / Execution：source Snapshot、message_id、nested 传播、每轮 slot 与 settlement 清理；Host policy 复用 SERIAL 执行并限制 wrapper 的并发 nested 调用；
- Embodied reference：MuJoCo tabletop 的真实接触、抓取、释放，以及动作后 geometry/contact/velocity evidence 和稳定性验证；原 carriage 示例同步使用新的决策契约；
- Host integration：结构化云端感知、RGB-D/skew/历史 TF 验证、同步 Observation Tools，以及 roserver state/operations/status/stop 适配。

真实部署仍必须显式提供 capture、校准、clock resolver、association 和可信的实时 backend preflight。RGB-D 可见表面位置不足以证明 object-frame origin 或 orientation，adapter 默认只写 position；只有配置基于同一 capture 的可信局部几何 full-pose callback、同时证明物体原点与姿态时才写完整 pose，不能用默认 quaternion 或 VLM suggestion 伪造 readiness。roserver 当前 pose 轮询响应未保留采样时间，不能替代 preflight 或 post-action evidence；未配置可信 preflight 时不注册 manipulation Tools。其 place API 只表达 yaw，adapter 必须拒绝无法表达的 roll/pitch。

Clock reset 后，新 clock-domain identity 不会自动证明旧 domain 的 claims 已失效。旧 evidence 的有效性无法判定且与新值不兼容时，World 保持 UNKNOWN；Host 不得用 revision、到达顺序或错误的 supersedes 将其升级为 KNOWN。

本地 deterministic / MuJoCo 测试只能支持 Level 0–1。真实模型测试通过显式配置与 credentials 启用；真实 sensor/bridge 与 physical manipulation 的 Level 3–4 必须有实际部署证据，不能由接口代码或 fixtures 代替。

---

## 6.6 最终目标

完成 V1.1 后，RoboAgent 应形成：

```text
Physical World
       ↓
Host Evidence
       ↓
World
       ↓
bounded Context
       ↓
Agent
       ↓
explicit Tool
       ↓
decision-bound revalidation
       ↓
physical effect
       ↓
fresh independent evidence
       ↓
World
       ↓
next Agent decision
```

整个 Core 仍然保持：

```text
small
explicit
composable
provider-neutral
robot-neutral
```

具身能力来自：

```text
World
+
Context
+
Tool
+
Execution
+
Agent reasoning
```

的组合，而不是另一套机器人 Runtime。

V1.1 完成后，RoboAgent 应从：

```text
Agent with World
```

演进为：

```text
minimal Embodied Agent Harness
```

同时继续遵循最核心的设计原则：

> **Primitives, not Features.**
