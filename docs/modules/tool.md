# Tool Module Design

## 1. 模块定位

`tool` 模块负责 `BaseTool` 的管理语义，而不是执行语义。

它的目标是把 LangChain `BaseTool` 包装成 RoboAgent 可管理的运行时对象，并提供统一的注册、查询、解析与绑定前准备接口。

当前阶段该模块是一个独立子系统：

- 不接管 LangChain/LangGraph 的默认 tool 执行链
- 不直接依赖 `agent`、`skill`、`config` 等上层模块
- 只负责 tool metadata、registry、resolution 与 manager facade

## 2. 当前结构

当前模块文件：

- [schema.py](/home/siyuey/workspace/openclaw/roboagent/roboagent/tool/schema.py)
- [tool.py](/home/siyuey/workspace/openclaw/roboagent/roboagent/tool/tool.py)
- [registry.py](/home/siyuey/workspace/openclaw/roboagent/roboagent/tool/registry.py)
- [resolver.py](/home/siyuey/workspace/openclaw/roboagent/roboagent/tool/resolver.py)
- [manager.py](/home/siyuey/workspace/openclaw/roboagent/roboagent/tool/manager.py)
- [errors.py](/home/siyuey/workspace/openclaw/roboagent/roboagent/tool/errors.py)
- [__init__.py](/home/siyuey/workspace/openclaw/roboagent/roboagent/tool/__init__.py)

## 3. 职责划分

### 3.1 schema.py

负责 `ToolSpec` 定义。

职责：

- 描述 tool 的管理元数据
- 校验 `name`、`description`、`group`、`source`
- 归一化 `allowed_agents`
- 明确 direct / deferred 可见性相关字段

### 3.2 tool.py

负责 `Tool` runtime object。

职责：

- 持有真实 `BaseTool`
- 持有复制后的管理元数据
- 提供可见性和可用性判断
- 保持与 `ToolSpec` 的单向构造关系

### 3.3 registry.py

负责 runtime `Tool` 索引。

职责：

- 统一 `register(...)` 接口，支持单个和批量注册
- 维护 name 唯一性
- 提供 `get`、`require`、`list_all`、`clear`
- 保持存储层与解析层分离

### 3.4 resolver.py

负责上下文解析。

职责：

- 根据 `agent_id` / `subagent_id` 计算可见工具
- 应用 `activated_allowed_tools`
- 应用 `parent_allowed_tools`
- 输出 `direct_tools` 与 `deferred_tools`

### 3.5 manager.py

负责对外统一接口。

职责：

- 协调 `ToolRegistry` 与 `ToolResolver`
- 暴露 `register`、`list_tools`、`resolve_tools`、`get_tools`
- 为未来接入 `agent` 组装逻辑提供稳定入口

### 3.6 errors.py

负责子系统异常语义。

职责：

- 定义统一错误边界
- 区分注册失败、重复注册、缺失 tool 等场景
- 避免向上层泄漏零散的基础异常

## 4. 依赖方向

推荐依赖方向：

- `schema -> errors`
- `tool -> schema, errors`
- `registry -> tool, errors`
- `resolver -> tool`
- `manager -> registry, resolver, schema, tool, errors`

禁止依赖方向：

- `schema -> manager`
- `tool -> manager`
- `registry -> manager`
- `resolver -> manager`

原因：

- `schema` 和 `tool` 是基础模型
- `registry` 是存储层
- `resolver` 是策略层
- `manager` 位于模块最外层，只负责 orchestration

## 5. 核心 API

### 5.1 ToolRegistry

`ToolRegistry.register(...)` 统一支持两种形式：

```python
registry.register(tool)
registry.register([tool_a, tool_b])
```

设计原因：

- 对外减少批量接口命名分叉
- 与 `ToolManager.register(...)` 保持一致
- 对调用方暴露单一稳定入口

### 5.2 ToolManager

`ToolManager.register(...)` 统一支持：

```python
manager.register(base_tool, spec)
manager.register([(base_tool_a, spec_a), (base_tool_b, spec_b)])
```

`ToolManager.get_tools(...)` 返回直接可绑定的 `list[BaseTool]`，供未来上层在不改变执行链的前提下接入。

## 6. 当前实现成熟度

当前 `tool` 模块已经具备以下特征：

- schema 与 runtime object 分离
- registry / resolver / manager 职责清晰
- 异常类型具备领域语义
- 对外 API 基本稳定

当前仍有意保留的边界：

- 不负责 tool execution
- 不负责 permission / approval / budget
- 不负责 config-driven loading
- 不负责 discovery tool 的实际 runtime 注入

## 7. 后续演进建议

- 增加 `ResolutionContext`，替代裸参数列表
- 增加 `ToolSource` 抽象，用于 builtin / config / MCP 等来源统一接入
- 如果未来出现更复杂的批量注册策略，优先增加独立 builder 或 source 层，而不是重新引入并列注册入口
- 增加单元测试，覆盖注册冲突、allowlist 收窄、deferred 分桶等关键路径
