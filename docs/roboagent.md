# RoboAgent Runtime

RoboAgent is a native asynchronous agent runtime. `runtime` contains typed
protocol values; `model` adapts OpenAI-compatible Chat Completions providers;
`tool` validates native tools; and `agent` owns the sequential loop and stateful
transcript facade. `Agent.run()` returns a terminal result, `stream()` emits
typed lifecycle events, and `subscribe()` installs best-effort observers.
