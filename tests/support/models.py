"""Deterministic Model fakes independent of reference applications."""

from roboagent.context import ModelContext
from roboagent.message import AssistantMessage
from roboagent.model import (
    FinishReason,
    ModelCapabilities,
    ModelResponse,
    ModelSettings,
    ResponseCompleted,
    ResponseStarted,
    ToolCallCompleted,
    ToolCallStarted,
)


class ScriptedModel:
    """Replay exact responses through the generic Model stream protocol."""

    capabilities = ModelCapabilities(tool_calling=True)

    def __init__(self, *messages: AssistantMessage):
        self.messages = iter(messages)
        self.contexts: list[ModelContext] = []

    async def stream(
        self, context: ModelContext, settings: ModelSettings | None = None
    ):
        self.contexts.append(context)
        message = next(self.messages)
        yield ResponseStarted("test", 0)
        sequence = 1
        for index, call in enumerate(message.tool_calls):
            yield ToolCallStarted(sequence, index, call.id, call.name)
            sequence += 1
            yield ToolCallCompleted(sequence, index, call)
            sequence += 1
        yield ResponseCompleted(
            sequence,
            ModelResponse(
                message,
                FinishReason.TOOL_CALL if message.tool_calls else FinishReason.STOP,
            ),
        )
