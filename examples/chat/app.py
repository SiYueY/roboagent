"""Application entry point for the RoboAgent browser chat example."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CERTIFICATE_PATH = Path(__file__).with_name("certs") / "roboagent-cert.pem"
PRIVATE_KEY_PATH = Path(__file__).with_name("certs") / "roboagent-key.pem"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from roboagent.agent import Agent
from roboagent.config import load_app_config
from roboagent.model import create_chat_model
from ui import chat_launch_options, create_demo

SYSTEM_PROMPT = "You are RoboAgent, a helpful AI assistant. Respond clearly and concisely."


def create_agent() -> Agent:
    """Build the immutable application Agent from the RoboAgent config."""
    config = load_app_config()
    return Agent(create_chat_model(registry=config.to_model_registry()), tools=(), system_prompt=SYSTEM_PROMPT)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    demo = create_demo(create_agent())
    tls_options: dict[str, object] = {}
    if CERTIFICATE_PATH.is_file() and PRIVATE_KEY_PATH.is_file():
        tls_options = {
            "ssl_certfile": str(CERTIFICATE_PATH),
            "ssl_keyfile": str(PRIVATE_KEY_PATH),
            "ssl_verify": False,
        }
        logging.info("Starting RoboAgent chat with the local HTTPS certificate.")
    elif CERTIFICATE_PATH.exists() or PRIVATE_KEY_PATH.exists():
        logging.warning("Both local HTTPS certificate files are required; starting with HTTP instead.")

    # Gradio's default Ctrl+C handler joins the server thread while active
    # streaming requests finish. For this local development server, force the
    # Uvicorn shutdown path so Ctrl+C returns promptly instead.
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        prevent_thread_lock=True,
        **chat_launch_options(),
        **tls_options,
    )
    if demo.server is not None:
        demo.server.force_exit = True
    demo.block_thread()


if __name__ == "__main__":
    main()
