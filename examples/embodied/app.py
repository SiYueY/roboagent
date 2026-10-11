"""Run the CPU-only embodied reference composition."""

import asyncio

from .mujoco import main

if __name__ == "__main__":
    asyncio.run(main())
