# Config Module Design

## 1. 模块定位

`config` 模块负责定义 RoboAgent 的结构化配置对象与配置装载边界。

它是系统的基础支撑模块，主要解决：

- 模型配置如何表达
- `Skill` 配置如何表达
- sub-agent 配置如何表达
- 默认值、校验规则和来源优先级如何统一

## 2. 设计职责

`config` 模块应承担：

- 配置 schema 定义
- 配置默认值与字段约束
- 配置对象的加载与校验
- 配置来源合并规则

`config` 模块不应承担：

- 业务执行逻辑
- `Skill` registry 管理
- Agent runtime graph 构建

## 3. 当前模块结构

当前已有文件：

- [model_config.py](/home/siyuey/workspace/openclaw/roboagent/roboagent/config/model_config.py)
- [skill_config.py](/home/siyuey/workspace/openclaw/roboagent/roboagent/config/skill_config.py)
- [subagent_config.py](/home/siyuey/workspace/openclaw/roboagent/roboagent/config/subagent_config.py)

这些文件目前仍基本为空，说明模块已预留职责边界，但尚未形成完整配置体系。

## 4. 建议结构

推荐子结构：

- `model_config.py`
  模型 provider、model name、temperature、token limit、timeout 等
- `skill_config.py`
  skill sources、enable/disable、loading policy、permission policy 等
- `subagent_config.py`
  sub-agent 拓扑、角色、能力范围、协作方式等
- `loader.py`
  从文件、环境变量、CLI 参数装载配置
- `resolver.py`
  合并多来源配置并生成最终 runtime config

## 5. 配置设计原则

- 配置必须结构化
- 配置必须可验证
- 配置必须支持默认值
- 配置必须支持来源追踪
- 配置必须与运行时代码解耦

推荐使用：

- `Pydantic BaseModel`
- `Field`
- `Enum`
- `Path`

## 6. 依赖方向

推荐依赖方向：

- `agent -> config`
- `skill -> config` 仅限读取公共配置对象

禁止依赖方向：

- `config -> agent`
- `config -> skill runtime internals`

原因：

- `config` 应保持基础设施属性，不反向依赖上层运行时

## 7. 后续演进建议

- 为每个配置文件补齐对应 `BaseModel`
- 增加统一的 `AppConfig`
- 增加配置加载器与来源优先级规则
- 为配置模块补单元测试，确保字段校验和默认值稳定
