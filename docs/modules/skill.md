# Skill Module Design

## 1. 模块定位

`skill` 模块负责整个 `Skill` 子系统的 discovery、schema、runtime object、registry 与管理接口。

它是 RoboAgent 中最明确的能力管理模块，负责把外部定义的 `Skill` 转换为可被系统使用的运行时对象。

## 2. 当前结构

当前模块文件：

- [schema.py](/home/siyuey/workspace/openclaw/roboagent/roboagent/skill/schema.py)
- [skill.py](/home/siyuey/workspace/openclaw/roboagent/roboagent/skill/skill.py)
- [loader.py](/home/siyuey/workspace/openclaw/roboagent/roboagent/skill/loader.py)
- [registry.py](/home/siyuey/workspace/openclaw/roboagent/roboagent/skill/registry.py)
- [manager.py](/home/siyuey/workspace/openclaw/roboagent/roboagent/skill/manager.py)
- [errors.py](/home/siyuey/workspace/openclaw/roboagent/roboagent/skill/errors.py)
- [__init__.py](/home/siyuey/workspace/openclaw/roboagent/roboagent/skill/__init__.py)

## 3. 职责划分

### 3.1 schema.py

负责 `SkillSpec` 定义。

职责：

- 外部 `SKILL.md` frontmatter 结构建模
- 字段校验
- metadata 归一化
- portable skill contract 表达

### 3.2 skill.py

负责 `Skill` runtime object。

职责：

- 表达运行时 skill record
- 承载 source、entrypoint、enabled 等运行时属性
- 为 registry 和 manager 提供稳定值对象

### 3.3 loader.py

负责 discovery 与 loading。

职责：

- 扫描 source 目录
- 解析 `SKILL.md`
- 校验 frontmatter
- 构建 runtime `Skill`

### 3.4 registry.py

负责 runtime `Skill` 索引。

职责：

- register / unregister
- 唯一性控制
- list / lookup
- match / select 的底层支持

### 3.5 manager.py

负责对外统一接口。

职责：

- 协调 loader 与 registry
- 暴露 `load`、`reload`、`register`、`disable`、`select`
- 作为上层 Agent 调用的主要入口

### 3.6 errors.py

负责子系统异常语义。

职责：

- 为 loader、registry、manager 提供清晰错误边界
- 避免直接暴露底层库异常到上层调用方

## 4. 建议补充结构

当前模块还缺少一个关键组件：

- `executor.py`

建议职责：

- 输入校验
- `handler` 解析
- 权限检查
- 超时控制
- 输出校验
- telemetry

推荐补充后，模块职责将更完整：

- `schema`
  定义外部 contract
- `skill`
  定义 runtime object
- `loader`
  负责 discovery
- `registry`
  负责索引
- `manager`
  负责 orchestration
- `executor`
  负责 execution

## 5. 依赖方向

推荐依赖方向：

- `loader -> schema, skill, errors`
- `registry -> skill, loader, errors`
- `manager -> registry, loader, skill`

禁止依赖方向：

- `schema -> loader`
- `skill -> manager`
- `errors -> 其他复杂模块`

原因：

- `schema` 和 `skill` 应保持基础模型属性
- `manager` 位于更高层，不能被底层反向依赖
- `errors` 应保持最小依赖面

## 6. 当前实现成熟度

从现有代码看，`skill` 模块已经具备清晰的基础分层，是当前项目中结构最完整的子系统之一。

现阶段的主要不足在于：

- 缺少 `executor`
- 缺少结构化 `input_schema` / `output_schema` 的运行时接入
- 尚未与完整 telemetry、permission enforcement 打通

## 7. 后续演进建议

- 新增 `executor.py`
- 让 executable `Skill` 显式声明 `input_schema` 与 `output_schema`
- 增加 routing policy 和 execution policy 的分层
- 增加 version、status、replacement 的治理字段
- 增加更系统的集成测试
