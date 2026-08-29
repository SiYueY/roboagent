from __future__ import annotations

import unittest

from roboagent.model.client import OpenAICompatibleChatModel
from roboagent.runtime import ModelContext, ModelRequest, ToolDefinition, UserMessage


class OpenAICompatibleTests(unittest.TestCase):
    def test_request_serialization_and_reserved_keyword_guard(self) -> None:
        model = OpenAICompatibleChatModel("test", temperature=0.2)
        request = ModelRequest("test", ModelContext("system", (UserMessage("hello"),), (ToolDefinition("map.read", "Read", {"type": "object"}),)))
        payload = model._payload(request)
        self.assertEqual(payload["stream_options"], {"include_usage": True})
        self.assertEqual(payload["tools"][0]["function"]["name"], "map.read")
        with self.assertRaises(ValueError): OpenAICompatibleChatModel("test", model_kwargs={"stream": False})


if __name__ == "__main__": unittest.main()
