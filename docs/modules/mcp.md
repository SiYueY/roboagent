# MCP Module

The application owns each `MCPServer` connection. It connects and discovers
tools before constructing an `Agent`; the resulting ordinary `Tool` values are
registered before `ToolRegistry` is sealed. Runtime tool topology is immutable.

`MCPToolAdapter` maps ordered text, JSON, binary, and resource blocks into the
canonical `RawToolResult` pipeline. MCP tools therefore pass through the normal
policy, approval, hooks, execution, materialization, and effect semantics. A
transport-scoped resource without bytes fails explicitly instead of entering
the durable transcript as a remote URI.

`MCPToolPolicy` is immutable trusted-local configuration. Tools default to
`SIDE_EFFECTING`; remote metadata may raise risk but cannot lower it. An
uncertain side-effecting MCP call records `UNKNOWN`, while a read-only failure
records `FAILED`.

Connection failures use `mcp_connection_error`, malformed protocol data uses
`mcp_protocol_error`, and server-declared tool errors use `mcp_tool_error`.
Optional lifecycle observation emits `mcp.connected` and `mcp.disconnected`
without including client state or credentials.
