# Third-party notices

## smolagents

- Upstream repository: https://github.com/huggingface/smolagents
- Exact upstream commit: `30bb1161095dbae2271e6bc3cc4c219cc3897a57`
- License: Apache License 2.0
- Original source paths reviewed/adapted:
  - `src/smolagents/local_python_executor.py`
  - `src/smolagents/utils.py` (`parse_code_blobs`)
  - `src/smolagents/cli.py`
  - `src/smolagents/monitoring.py`
- Local destinations:
  - `examples/coding/evaluator.py`
  - `examples/coding/protocol.py`
  - `examples/coding/model_adapter.py`
  - `examples/coding/display.py`
  - `examples/coding/cli.py`

Local modifications replace all smolagents Agent, Model, Tool, Memory, and
planning runtime ownership with RoboAgent's canonical runtime. Tool calls were
adapted to use a process worker, length-prefixed JSON IPC, `CodeToolBridge`, and
`ToolExecutionContext.execute_nested_tool()`. Import policy is the fixed V1.3
allowlist; interpreter state is scoped to a CodingSession worker generation;
`final_answer` has RoboAgent's strict value union and uncatchable completion
semantics. The upstream permissive code parser was not copied: it was rewritten
to implement the stricter RoboAgent fence grammar. Rich CLI presentation ideas
are adapted to consume RoboAgent events and results only.

The Apache License 2.0 text is available at:
https://www.apache.org/licenses/LICENSE-2.0
