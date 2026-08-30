"""Application entry point for the RoboAgent browser chat example."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from roboagent.agent import Agent
from roboagent.config import load_app_config
from roboagent.model import create_chat_model
from ui import create_demo

SYSTEM_PROMPT = "You are RoboAgent, a helpful AI assistant. Respond clearly and concisely."


def create_agent() -> Agent:
    """Build the immutable application Agent from the RoboAgent config."""
    config = load_app_config()
    return Agent(create_chat_model(registry=config.to_model_registry()), tools=(), system_prompt=SYSTEM_PROMPT)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    demo = create_demo(create_agent())
    # Gradio's default Ctrl+C handler joins the server thread while active
    # streaming requests finish. For this local development server, force the
    # Uvicorn shutdown path so Ctrl+C returns promptly instead.
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        prevent_thread_lock=True,
    )
    if demo.server is not None:
        demo.server.force_exit = True
    demo.block_thread()


if __name__ == "__main__":
    main()
