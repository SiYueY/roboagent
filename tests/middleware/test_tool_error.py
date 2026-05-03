from __future__ import annotations

import unittest

from langchain.agents.middleware.types import ToolCallRequest

from roboagent.middleware import ToolErrorHandlingMiddleware


class ToolErrorHandlingMiddlewareTests(unittest.TestCase):
    def test_wrap_tool_call_converts_exception_to_tool_message(self) -> None:
        middleware = ToolErrorHandlingMiddleware()
        request = ToolCallRequest(
            tool_call={"name": "map.read", "args": {}, "id": "call-1", "type": "tool_call"},
            tool=None,
            state={},
            runtime=None,
        )

        def handler(_: ToolCallRequest):
            raise RuntimeError("map unavailable")

        result = middleware.wrap_tool_call(request, handler)

        self.assertEqual(result.tool_call_id, "call-1")
        self.assertEqual(result.status, "error")
        self.assertIn("map.read", result.content)
        self.assertIn("map unavailable", result.content)


if __name__ == "__main__":
    unittest.main()
