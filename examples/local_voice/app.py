"""Run RoboAgent voice with a local ALSA microphone and speaker."""
from __future__ import annotations

import asyncio
import logging

from roboagent.agent import Agent
from roboagent.config import load_app_config
from roboagent.model import create_model
from roboagent.speech.errors import SpeechConfigurationError
from roboagent.speech.factory import create_local_transport, create_speech_session


async def main() -> None:
    config = load_app_config()
    if config.speech is None:
        raise SpeechConfigurationError("Configure the speech section before running local voice.")
    transport, capture_format, render_format = create_local_transport(config.speech)
    agent = Agent(create_model(registry=config.to_model_registry()))
    session = create_speech_session(session=agent.new_session(), transport=transport,
                                    config=config.speech, capture_format=capture_format,
                                    render_format=render_format)
    try:
        await session.run()
    finally:
        await session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except SpeechConfigurationError as exc:
        raise SystemExit(f"Local voice configuration error: {exc}") from exc
