"""Application entry point for the RoboAgent browser chat example."""

from __future__ import annotations

import logging
import os
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
from roboagent.speech import SpeechConfig
from speech_server import ConversationRegistry, install_speech_route
from ui import chat_launch_options, create_demo

SYSTEM_PROMPT = "You are RoboAgent, a helpful AI assistant. Respond clearly and concisely."


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        import websockets  # noqa: F401 -- required by Uvicorn's WebSocket upgrade path.
    except ImportError as exc:
        raise RuntimeError(
            "The chat speech example requires the speech extra. Run: "
            "uv sync --extra gradio --extra speech"
        ) from exc
    config = load_app_config()
    agent = Agent(create_chat_model(registry=config.to_model_registry()), tools=(), system_prompt=SYSTEM_PROMPT)
    registry = ConversationRegistry()
    demo = create_demo(agent, speech_registry=registry)
    from fastapi import FastAPI
    import gradio as gr

    app = FastAPI()
    install_speech_route(app, registry, config.speech or SpeechConfig())
    app = gr.mount_gradio_app(app, demo, path="/", **chat_launch_options())
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

    import uvicorn
    port = int(os.getenv("ROBOAGENT_CHAT_PORT", "7860"))
    logging.info("Starting RoboAgent chat on port %s.", port)
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        **({"ssl_certfile": tls_options["ssl_certfile"], "ssl_keyfile": tls_options["ssl_keyfile"]} if tls_options else {}),
    )


if __name__ == "__main__":
    main()
