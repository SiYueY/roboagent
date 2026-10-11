# RoboAgent World V1.1

**状态：Implementation Specification**

**历史参考（非规范性）：[`world_v1.0.md`](world_v1.0.md)**

**范围：Optional external-environment cognition / Context projection / causal source context**

本文是独立、完整的 Generic World specification，取代历史 `world_v1.0.md` 的 Core World 部分。历史文档仅保留设计背景，不是规范性 Core specification；本文不开放继承其中未覆盖的规则。当前通用规则在本文中明确列出。具体 embodied reference 契约见 [Embodied reference application](../examples/embodied.md)，不定义 RoboAgent Core API。

# 1. 定位与 ownership

RoboAgent 是通用 Agent Harness。World 是可选的 external-environment cognition primitive，可表达服务、文档、账户、页面、应用对象及物理对象的证据与当前认知；EntityRef 不预设领域类型。

- Session 拥有 conversation / transcript / pending inputs。
- Run / Execution 拥有 execution lifecycle、effects、nested work、settlement 和 resources。
- World 拥有 Observation、Claim、Evidence、Scope、Clock、TTL、Resolution 和 immutable Snapshot。
- ContextManager 拥有模型可见的 World projection、排序、截断及预算。
- Host 拥有采集、身份关联、具体 predicate、权限、外部资源、业务 readiness、revalidation 和 outcome verification。

World 不启动 Run，不获取传感器或服务数据，不拥有 controller，不制定 application policy。World query 是只读匹配；Observation Tools 是显式能力。

`One Agent, One Runtime` 与 `Primitives, not Features` 同时适用于所有 reference application。Chat、Coding、Embodied、Web 等都是 primitives 的 consumer，不能以 reference implementation 的便利性定义 Core。

以下内容不是 Core 不变量：每轮只允许一个 physical action、完整 state readiness、控制权、RGB-D/TF、抓放、动作后 evidence barrier。这些规则只约束明确选择该策略的 Host/reference application。

## 1.1 生命周期、身份与授权

World 可以由同一 Host 的多个 Agent/Session 显式共享，其 lifetime 独立于单次 Run。结束或取消 Run、清理 Session、发生 Tool failure，都不能自动清空 World 或修改 evidence。Agent 的 `world=None` 不创建隐式 World，也不注册任何 query/observation Tool。

Host 认证 writer，并绑定允许的 `world_id`、source 与 scope。World 不是同进程 security boundary：source 字符串表示 provenance，不是 credential；它只检查 configured membership 和数据不变量，不新增 SourceManager、ACL、RBAC 或 authenticated service。

```python
World(
    world_id: str,
    *,
    limits: WorldLimits | None = None,
    clock_resolver: ClockResolver | None = None,
    allowed_sources: Sequence[str] = (),
    allowed_scopes: Sequence[str | None] = (None,),
)
```

默认不允许任何 source 写入；默认只接受 scope=None。Entity identity 是 `(world_id, EntityRef.id)`，没有 reserved entity 名称。EntityRef、ClaimRef 和 source/scope identity 是非空字符串；结构化数据使用不可变值对象和 frozen JSON。

## 1.2 Observation、Claim 与 Evidence

Observation 是 evidence envelope，World 不解释任意 payload，也不解析领域数据。Claim 是 Host/Adapter 显式提交的 semantic cognition input；Observation source 与 Claim producer source 可以不同。

| 类型 | 字段 / 语义 |
|---|---|
| `ObservationTime` | `clock_id: str`, `nanoseconds: int`；非负整数采样时间 |
| `ObservationRef` | `source: str`, `id: str`；Observation identity |
| `EntityRef` | `id: str`；World 内的实体 identity |
| `ClaimRef` | `id: str`；World 内的 Claim identity |
| `EvidenceRef` | `kind: Literal["observation", "claim"]`, `source: str | None`, `id: str` |
| `Observation` | `ref`, `modality`, `observed_at`, `received_at`, `payload: JsonValue | ResourceRef`, `scope: str | None = None` |
| `Claim` | `ref`, `subject`, `predicate`, `value: JsonValue | EntityRef`, `kind`, `source`, `scope`, `evidence_refs`, `asserted_at`, `valid_at: ObservationTime | None = None`, `valid_for_ns: int | None = None`, `supersedes: tuple[ClaimRef, ...] = ()` |

`ClaimKind` 是 closed enum：MEASUREMENT、INTERPRETATION、INFERENCE、HYPOTHESIS、PRIOR、PREDICTION。前三类必须提供 evidence_refs；非 grounded hypotheses/priors/predictions 不能仅凭 source 名称或声明升级为 grounded KNOWN。完整 Grounding 与 Resolution 规则见 §2.4–2.7。

Observation evidence 必须引用已提交或同一事务中提交的 `ObservationRef(source, id)`，source 不能为 None。Claim evidence 必须引用存在的 ClaimRef；source 若提供，必须匹配该 Claim producer。Evidence 与 correction 组成的依赖图必须无环，允许引用同批次中的 Claim。

`value` 的 closed union 区分 EntityRef relation 和任意 JSON，即使 JSON 恰好长得像 EntityRef 也不视为 relation。JSON 输出使用 `value_kind="entity_ref"` 与 `value={"entity_ref": {"id": "account_1"}}`，或 `value_kind="json"` 保留该区别。

## 1.3 时间、Clock 与 Freshness

`observed_at` 是外部系统实际采样的时间；`received_at`、`asserted_at` 和 `captured_at` 是有时区的 datetime，供审计/采集记录使用，不能替代采样时间排序。`clock_id` 表示可比较的时间域实例；Host process/service restart 或 clock reset 必须分配新 identity。

```python
class ClockResolver(Protocol):
    def now(self, clock_id: str) -> ObservationTime | None: ...
```

不同 clock domain 默认不可比较，World 不隐式建立映射。Host 必须提供一致的 resolver；每个 snapshot 对一个 domain 使用同一 evaluation time。resolver 不可用时相关时间有效性为 UNKNOWN；返回错误 domain 或执行失败报 `clock_unavailable`。未来的 `valid_at` 为 UNRESOLVED，即使没有 TTL 也不能当成 LIVE。

`valid_for_ns` 是非负整数，配置 TTL 时必须有 `valid_at`；freshness 依据同一 domain 的 `valid_at + valid_for_ns`，不依据 receive order。None 表示不自动按 TTL 过期，不表示永久真实。World 不根据 predicate/source 名称硬编码 TTL。有效 clock 下，时间到期可以让 Snapshot 状态改变，而 canonical revision 不变。

## 1.4 公共 API 与原子事务

```python
async def observe(observation: Observation, *, claims: Sequence[Claim] = ()) -> WorldReceipt: ...
async def add_claims(claims: Sequence[Claim]) -> WorldReceipt: ...
async def snapshot() -> WorldSnapshot: ...
async def query(entity: EntityRef, predicate: str, *, scope: str | None = None) -> WorldAnswer: ...
async def find_entities(
    filters: Mapping[str, JsonValue | EntityRef], *, scope: str | None = None,
    include_possible: bool = False, limit: int = 16,
) -> EntitySearchResult: ...
```

`observe()` 原子提交 immutable Observation 与同步 Claim set；迟到的解释使用 `add_claims()`，必须保留原始采样时间和 provenance。World 不把 Observation payload 自动转换为 Claim。

| 输入 | 事务结果 |
|---|---|
| 同 Observation identity、同内容、同同步 Claim set 重试 | 幂等成功，revision 不变 |
| 同 Observation identity，不同内容或不同同步 Claim set | `observation_conflict` |
| 同 ClaimRef、同内容重试 | 幂等成功 |
| 同 ClaimRef、不同内容 | `claim_conflict` |
| Evidence 缺失或 source 不匹配 | `evidence_not_found` |
| 任意无效条目、循环依赖、source/scope 不允许或容量溢出 | 整批拒绝，不产生 partial commit，不改变 revision |

`supersedes` 表示认知纠错，不表示正常状态流更新；必须引用已存在且同一 `(subject, predicate, scope)` 的 Claim。它不能跨 scope 修正或自行引用。

`WorldReceipt` 包含 `world_id`、`revision`、`changed`、`observation_ref: ObservationRef | None` 和 `committed_claim_refs`。changed 只表示 canonical evidence/claim set 的提交变化，不证明外部世界发生变化。每个有变化的事务仅增加一次 revision，重复事务不增加。

WorldError 提供稳定 code，不在错误消息中暴露 resource contents。公共错误包括 `invalid_observation`、`observation_conflict`、`invalid_claim`、`claim_conflict`、`evidence_not_found`、`source_not_allowed`、`scope_not_allowed`、`world_capacity_exceeded`、`clock_unavailable`。

## 1.5 Snapshot、查询与显式 Tools

WorldSnapshot 是完整、有界、immutable、internally consistent 的 current cognition view，字段为 `world_id`、`revision`、`captured_at`、`entries: tuple[WorldEntry, ...]`。没有 partial Snapshot 或 Snapshot.truncated；投影截断只在 Context 层发生。不能只按 revision 缓存 freshness。

`WorldEntry` 包含 `subject`、`predicate`、`scope`、`status`、`value`、`candidates`、`supporting_claims`、`evidence_refs` 和 `valid_at`；entry identity、lookup 与候选规则见第 2 章。`WorldAnswer` 返回 world_id/revision 及同样的 status/value/candidates/provenance/time 字段。不存在的 key 返回 UNKNOWN、None 和空候选/支持集合。

`WorldCandidate` 保留 `value`、`supporting_claims`、`evidence_refs`、`valid_at`。非 KNOWN entry/answer 的 value 必须为 None，候选不能只保留一个赢家而抹去 conflict/uncertainty。

`create_world_tools(world)` 显式创建只读 `world_query` 和 `world_find_entities`，绑定一个已有 World，不认证或创建 writer、不采集新 evidence、不改变环境。Tool filters 使用带类型标签的数组，Python API 使用 mapping，详见 §2.13。Snapshot/World/Context 数据不能授予新的 Tool capability。

## 1.6 容量与 Resource retention

所有 WorldLimits 都是确定的正整数：

| Limit | 默认值 |
|---|---:|
| max_entities | 1024 |
| max_observations | 4096 |
| max_claims | 8192 |
| max_evidence_metadata | 8192 |
| max_payload_bytes | 1,048,576 |
| max_media_refs | 1024 |
| max_search_results | 64 |

当前 release 达到容量时拒绝写入，不自动 eviction/coalescing，也不引入 GC Framework。max_evidence_metadata 覆盖 retained Observations + Claims；max_payload_bytes 同时约束单条及 retained serialized data 的总量。`find_entities.limit` 必须在 1..max_search_results 以内。

`ResourceRef` 的字段是 `uri`、`media_type: str | None`、`size: int | None`、`digest: str | None`、`available: bool = True`。大型 bytes 由 Host 存储和管理；World 只保留引用/metadata，不 fetch、open、decode、download 或 delete URI。媒体不可用仍可保留 identity/provenance，`available=False` 不代表证据图消失；Host 用新 evidence 报告后续变化。

必须保留 Claim/evidence/correction chain 的 referential integrity。当前没有 World persistence、media store 或自动 retention service；这些边界不从历史文档继承额外实现要求。

## 1.7 并发与操作边界

同一 World 及其直接 writer/reader 必须运行在同一 asyncio event loop。World 用短时 lock 保护最终校验、atomic commit、revision update 和 Snapshot capture；锁内不能执行 Model request、外部网络 I/O、media processing 或大型 serialization。

Snapshot 和 query 是 local、bounded、non-network 操作，无需独立 CancellationToken。World 更新不触发 Session.start/Run。Host 自行控制采样、队列、外部请求频率与并发；这些策略不属于 World Core。

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
    EntityRef("service_7"),
    "state",
    scope=None,
)
```

只查询 unscoped entry。

查询指定 scope：

```python
await world.query(
    EntityRef("service_7"),
    "state",
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
        EntityRef("service_7"),
        "state",
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

Grounding 按本节的 evidence-support 规则确定，不依赖 Claim 名称、producer 名称或历史文档。

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

不能把 grounded fact 推到 KNOWN。

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

TTL 表示 evidence 的 freshness 上限，不表示对象在整个 TTL 区间内保持原值。自然状态变化使用新 Claim + 新 `valid_at`，不使用表示认知纠错的 `supersedes`。

在完成 temporal classification 后，MUST 先按以下字段划分状态流：

```text
subject × predicate × scope × source × grounding class × clock_id
```

这只是 resolution 的内部 grouping key，不新增公开 primitive。`source` 必须标识一个连贯的状态生产流；Host MUST 为独立观察流使用不同 source，不能为了消除冲突把它们伪装为同一流。

对同一状态流中具有 `valid_at`、且已经判定为 LIVE 或 EXPIRED 的 claims，MUST 选择最大 `valid_at.nanoseconds` 对应的全部 claims。时间相同的不同值必须同时保留。选择发生在 LIVE 与 EXPIRED 的并集内，不能分别取最新后让旧 LIVE 值重新成为当前状态。

例如，同一 source、同一 clock：

```text
T0: state=P0, LIVE
T1: state=P1, LIVE, T1 > T0

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
environment = production

T2 newer hypothesis:
environment = staging
```

两者都 LIVE。

不能：

```text
newer hypothesis wins
→ staging
```

正确：

```text
grounded LIVE production
+
non-grounded LIVE staging
↓
KNOWN(production)
```

因为 hypothesis 不参与 grounded current-truth competition。

---

### 2.4.6 独立证据与同值支持

完成状态流选择后，不同 source、不同 clock，以及同一流头同一时间的不同值，必须分别保留。不同 grounded LIVE 流头支持不同值时，属于当前证据冲突：

```text
source A: LIVE production
source B: LIVE staging

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
LIVE production(clock A)
LIVE staging(clock B)
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
LIVE production
LIVE staging
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
LIVE production
UNRESOLVED production

→ KNOWN(production)
```

如果存在可能支持不同 value 的 grounded UNRESOLVED：

```text
→ UNKNOWN
```

例如：

```text
LIVE production
UNRESOLVED staging

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
grounded LIVE production
+
hypothesis staging

→ KNOWN(production)
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
LIVE production
+
UNRESOLVED staging

→ UNKNOWN
→ value = None
→ candidates = {production, staging}
```

不能只返回 staging。

同样：

```text
EXPIRED production
+
UNRESOLVED staging

→ UNKNOWN
→ candidates = {production, staging}
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
LIVE production
LIVE staging
UNRESOLVED green
```

MUST 保留选择后全部 grounded LIVE 与 grounded UNRESOLVED candidates：

```text
production
staging
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
LIVE production
→ KNOWN(production)

LIVE production + LIVE production
→ KNOWN(production)

LIVE production + LIVE staging
→ CONFLICTED

LIVE production + LIVE staging + UNRESOLVED green
→ CONFLICTED
→ candidates {production, staging, green}

LIVE production + UNRESOLVED production
→ KNOWN(production)

LIVE production + UNRESOLVED staging
→ UNKNOWN
→ candidates {production, staging}

EXPIRED production
→ STALE

EXPIRED production + UNRESOLVED staging
→ UNKNOWN
→ candidates {production, staging}

UNRESOLVED production
→ UNKNOWN

grounded LIVE production + hypothesis staging
→ KNOWN(production)

grounded LIVE production + newer hypothesis staging
→ KNOWN(production)

hypothesis production only
→ UNKNOWN

all claims superseded
→ UNKNOWN / empty candidates

LIVE production(clock A)
+
LIVE staging(clock B)
→ CONFLICTED

same stream, T0 LIVE state=P0 + T1 LIVE state=P1, T1 > T0
→ KNOWN(P1), candidates {P1}, no supersedes

same stream, T0 LIVE state=P0 + T1 EXPIRED state=P1, T1 > T0
→ STALE, candidates {P1}, never restore P0

same stream, LIVE state=P0 + future UNRESOLVED state=P1
→ UNKNOWN, candidates {P0, P1}

independent sources, LIVE state=P0 + LIVE state=P1
→ CONFLICTED, candidates {P0, P1}

same stream and same valid_at, LIVE production + LIVE staging
→ CONFLICTED, candidates {production, staging}

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
service_7.category = service  KNOWN
service_7.environment    = production   KNOWN
service_7.state     = P     STALE
```

查询：

```text
category=service
environment=production
```

必须得到：

```text
service_7 = CONFIRMED
```

因为 state 不属于 filter。

之后 Agent 再查询：

```text
service_7.state
```

发现 STALE，再决定观察。

World Search 禁止把：

```text
state
reachability
owner
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

对外部环境任务中的保守 grounding，SHOULD 使用：

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
EntityRef("account_2")
```

必须和普通 JSON：

```python
{"entity_ref": {"id": "account_2"}}
```

保持不同。

Agent-facing Tool schema 推荐：

```json
{
  "filters": [
    {
      "predicate": "environment",
      "value_kind": "json",
      "value": "production"
    },
    {
      "predicate": "account",
      "value_kind": "entity_ref",
      "entity_id": "account_2"
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

# 3. Context projection

每个 Model Turn 在构建 Context 时 capture 一个 immutable WorldSnapshot，通过 `ContextRequest.world_snapshot: WorldSnapshot | None` 传给 ContextManager，包括 compaction 重试。ContextManager 只负责投影，不改变 Session transcript 或 World。

`ContextDataSegment` 的公共字段为 `source: str`、`source_id: str | None`、`revision: int | None`、`captured_at: datetime`、`text: str`、`truncated: bool = False`、`omitted_count: int = 0`。World projection 使用 source="world"、source_id=world_id 与 Snapshot revision/time。省略 entries 时必须显式报告 truncated/omitted_count，并且 `truncated == (omitted_count > 0)`；不能把删减后的投影伪装成完整 Snapshot。

ContextDataSegment 必须属于 ModelContext 的 closed segment union。所有 ContextManager、预算估算、Model validation、provider 编码与 reference projection consumer 都必须处理它，不接受任意新 segment 类型。Model adapter 将其编码为明确标记的 untrusted observational data，不能当成 system instructions，也不能插进完整 Tool exchange 内。

排序依据必须显式、领域无关：

1. transcript 中结构化 entity references，以及 Tool schema 的 `x-world-entity-ref` 标注。
2. Host-authored `x-world-entities` dependencies。
3. 相关实体的 uncertainty、最近一次显式提及。
4. entry recency 与稳定排序。

`self`、`robot` 及任何其他 EntityRef 名称都不是 reserved identity，不获得默认优先级或 protection。Host 希望某实体一直相关时，必须显式声明 schema dependency。自然语言文本不进行隐藏的 grounding。

World-backed composition 默认至多 64 entries，并保留所有显式相关的 uncertain entries。预算覆盖完整 Model input；无 context window 时默认 8192 tokens。无法容纳必须保留的数据时报告 context budget error，不静默抹去 uncertainty。

World data 使用 `ContextDataSegment`，位于 transcript 前并标记为 untrusted observational data。它不能覆盖 system policy、ToolPolicy、Approval 或 Host authorization。Tool 成功并不一定验证外部 outcome；UNKNOWN effect 在重试前必须 reconcile 外部状态；取消 Run 不证明外部 operation 已停止。

# 4. Model Turn causal context

## 4.1 Model Turn source WorldSnapshot

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

## 4.2 ToolContext Snapshot 传播

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

是否要求 source Snapshot、是否拒绝直接调用，以及如何与当前 World 比较，由具体 Host Tool 决定。Core 不猜测 Tool 的 World 绑定，也不引入领域-specific readiness 或每轮动作数量限制。

同一 response 可以合法地调用多个产生外部副作用的 Tool；SERIAL / CONCURRENT、Policy、Approval、Effect 与 nested settlement 仍遵循现有 Runtime 契约。

---

# 5. 实施与验证

World V1.1 的 Core 范围是：三元 identity、精确 Scope、Resolution pipeline、完整 candidate/provenance、typed Entity Search、Snapshot lookup、Context projection 和 source Snapshot propagation。

公共契约必须覆盖 exact-scope lookup、mixed-state Resolution、clock restart/unavailability、stream-head expiry、independent evidence conflicts、typed filters、POSSIBLE/truncation、Context protection、同一 response 与 nested Tools 的 source 传播、Child Agent 独立 capture、world=None 和 direct invocation。

Core 不维护 application action slot，也不为某种 wrapper 建立第二个 scheduler。Runtime 的 nested work、SERIAL batch、resources 与 settlement 保持通用语义。具体 Host 策略测试及部署证据见 [Embodied reference application](../examples/embodied.md)。

Persistence、Memory、Scene Graph、Planner 和推测性 WorldDependency/PostCondition abstraction 不属于本次实现范围。
