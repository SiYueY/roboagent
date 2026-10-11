"""Run the CPU-only embodied reference composition."""

import asyncio
import argparse

from .reference import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="MuJoCo physical closed-loop reference"
    )
    parser.add_argument(
        "--cloud",
        action="store_true",
        help="Use the configured default model for grounding",
    )
    arguments = parser.parse_args()
    model = None
    if arguments.cloud:
        from roboagent.model import create_model

        from roboagent.config import load_app_config

        model = create_model(registry=load_app_config().to_model_registry())
    asyncio.run(main(model))
