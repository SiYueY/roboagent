from .tool import InvalidToolOutputError, Tool, ToolCallContext, ToolInvocation, ToolOutput

# v1 keeps the former public name as a thin compatibility alias.  Both names
# normalize into the single canonical ToolOutput representation.
ToolExecutionResult = ToolOutput

__all__ = ["Tool", "ToolCallContext", "ToolInvocation", "ToolOutput", "ToolExecutionResult", "InvalidToolOutputError"]
