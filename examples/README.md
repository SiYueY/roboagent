# RoboAgent 示例

示例是独立、可直接运行的参考实现：每个示例只展示一个 RoboAgent 能力或
外部集成方式，并且只使用 RoboAgent 的公开 API。

每个示例使用 `snake_case` 目录，包含独立的 `README.md` 和 `app.py` 入口。
示例特有依赖必须声明为 optional dependency；在重复代码真正造成维护成本前，
不建立共享示例框架。

当前示例：

- [chat](chat/README.md)：使用 Gradio UI 的流式多轮浏览器聊天。
- [coding](coding/README.md)：V1.3 进程隔离 CodingSession、Rich CLI 与集成评估。
