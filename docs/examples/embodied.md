# Embodied reference application

This is a reference application of RoboAgent primitives, not a Core specification.

Embodied Reference Agent 是 World/Runtime 的复杂验证场景，不是 Core API 的定义来源。以下 physical policy、readiness、post-action verification 和具体 integrations 只约束本 reference；其他 Agent 可以在一个 Model Turn 中合法执行多个外部动作。

通用 World、Context 与 causal Snapshot 契约见 [World V1.1](../world/world_v1.1.md)。具体入口、配置与运行命令见 [example README](../../examples/embodied/README.md)。RoboAgent Core Runtime 契约见 [V1.3](../roboagent_v1.3.md)。

# 1. Agent–World 闭环执行契约

本章定义 Grounding、Observation、Decision Snapshot、Revalidation、Physical Dispatch 和 Verification 的组合语义。

这些是行为契约，不是新的 subsystem。

---

## 1.1 Environment Grounding

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

## 1.2 Entity confirmed 不等于动作 ready

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

## 1.3 World Query 与 Observation Tool 分离

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

## 1.4 同步 Observation 是 V1.1 必需 reference path

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

## 1.5 异步 Observation 是可选扩展

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

## 1.6 Model Turn source WorldSnapshot

source Snapshot 是通用 causal-context primitive，契约见 [World V1.1](../world/world_v1.1.md#4-model-turn-causal-context)。它不是模型可见全部 entries 的证明。

## 1.7 ToolContext Snapshot 传播

同一 response 和 nested Tools 保留 message_id 与 source Snapshot；Child Agent 独立 capture。直接调用没有 Model causal context。本文 reference physical Tools 要求该 context 与自身 World 绑定一致，否则在副作用前拒绝。此要求不适用于所有 Core Tools。

---

## 1.8 新 Observation 必须形成新的 reasoning boundary

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

## 1.9 Reference physical Tool 的 decision evidence 必须完整

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

## 1.10 Selection evidence 完整性

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

## 1.11 Readiness evidence 完整性

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

## 1.12 验证语义事实，不验证旧 ClaimRef 是否仍活着

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

## 1.13 Grounding completeness 的边界

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

## 1.14 Pre-execution Revalidation

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

## 1.15 Model Turn 与 message_id

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

## 1.16 同一 Model Turn 只允许一个 physical action attempt

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

slot 是 embodied Host policy state，按 `(run_id, message_id)` 隔离，只由 reference `PhysicalPolicy` 维护。它不是通用 Runtime invariant，也不存放在 Core ExecutionTree 中。

同一 policy instance 必须显式注册为 `Agent.tool_policy` 与 `Agent.hooks`，并由该 Host 的所有 physical Tools 共用。Run hooks 绑定 Run 与 Model message identity；canonical ExecutionResource 在 Run/nested settlement 完成后清理 Host 数据。单个 Tool 或 wrapper 返回不得提前释放 consumed slot；下一次 Model reasoning boundary 可以开始新 slot。

wrapper 声明现有 `SERIAL` mode；reference policy 通过现有 `evaluate()` 与 ExecutionResource 拒绝同一 wrapper 内重叠的 nested 调用，不添加 Core scheduling flag 或另一套 Runtime。Core 只暴露已有 scope lifecycle，不识别 physical actions。

Child Agent 使用自己的 run_id、message_id 和 slot。多个 Run/Child Agent 对同一机器人发起动作时，仍必须通过 Host backend 的 controller/task ownership 检查；每轮 slot 不替代跨 Run 的物理资源互斥。

清理 slot 不表示旧物理任务已停止。UNKNOWN 任务的 task_id、控制权与后续状态查询继续由 Host backend 持有，直到满足 §1.17 的终态要求。

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

## 1.17 UNKNOWN、取消与控制权

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

## 1.18 Physical Verification 必须依赖动作后的 evidence closure

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

## 1.19 holding / inside / on

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

## 1.20 Place / task completion

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

# 2. Perception 与真实机器人集成契约

真实感知与控制继续属于 Host，但 V1.1 必须明确最低接口边界。

---

## 2.1 高频 sensor 不直接成为高频 World history

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

## 2.2 Camera 与 VisionBuffer

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

## 2.3 RGB、Depth、CameraInfo

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

## 2.4 TF

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

## 2.5 Cloud VLM

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

## 2.6 Entity association

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

## 2.7 TTL 与 freshness

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

## 2.8 mfr3duo bridge

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

# 3. Reference Implementation、测试与实施阶段

V1.1 必须通过真实闭环 reference 证明契约，而不是只通过文档或 deterministic demo。

---

## 3.1 MuJoCo Tabletop Reference

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

## 3.2 Reference Tools

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

## 3.3 标准闭环必须按真实 Model Turns 描述

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

## 3.4 Composite / execute_python 的边界

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

## 3.5 必测异常场景

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

## 3.6 证据等级

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

## 3.7 实施阶段

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

single physical action attempt per Turn (Host policy only)

explicit SERIAL wrappers / policy admission / resource cleanup

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

## 3.8 测试策略

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

# 4. 非目标与后续演进

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

## 4.1 WorldDependency

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

## 4.2 PostCondition

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

## 4.3 Scene Graph

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

## 4.4 Persistence 与 Memory

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

## 4.5 当前实现状态

本文是 embodied reference application 的契约，不是 RoboAgent Core specification。代码与验证证据必须分别报告。

当前仓库已实现四个 Phase 的代码路径：

- World：三元 identity、状态流时间演进、独立证据冲突、完整 candidate/provenance、POSSIBLE search、typed filters 与纯 Snapshot lookup；
- Context：protection/budget 三元 key 迁移，以及带类型标签的 filter 引用识别；
- Tool / Execution：source Snapshot、message_id、nested 传播、Host-owned 每轮 slot 与 canonical resource/settlement 清理；Host policy 复用 SERIAL 执行并限制 wrapper 的并发 nested 调用；
- Embodied reference：MuJoCo tabletop 的真实接触、抓取、释放，以及动作后 geometry/contact/velocity evidence 和稳定性验证；原 carriage 示例同步使用新的决策契约；
- Host integration：结构化云端感知、RGB-D/skew/历史 TF 验证、同步 Observation Tools，以及 roserver state/operations/status/stop 适配。

真实部署仍必须显式提供 capture、校准、clock resolver、association 和可信的实时 backend preflight。RGB-D 可见表面位置不足以证明 object-frame origin 或 orientation，adapter 默认只写 position；只有配置基于同一 capture 的可信局部几何 full-pose callback、同时证明物体原点与姿态时才写完整 pose，不能用默认 quaternion 或 VLM suggestion 伪造 readiness。roserver 当前 pose 轮询响应未保留采样时间，不能替代 preflight 或 post-action evidence；未配置可信 preflight 时不注册 manipulation Tools。其 place API 只表达 yaw，adapter 必须拒绝无法表达的 roll/pitch。

Clock reset 后，新 clock-domain identity 不会自动证明旧 domain 的 claims 已失效。旧 evidence 的有效性无法判定且与新值不兼容时，World 保持 UNKNOWN；Host 不得用 revision、到达顺序或错误的 supersedes 将其升级为 KNOWN。

本地 deterministic / MuJoCo 测试只能支持 Level 0–1。真实模型测试通过显式配置与 credentials 启用；真实 sensor/bridge 与 physical manipulation 的 Level 3–4 必须有实际部署证据，不能由接口代码或 fixtures 代替。

---

## 4.6 最终目标

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
