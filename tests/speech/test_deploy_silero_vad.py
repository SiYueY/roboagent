from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "deploy_silero_vad.sh"


class DeploySileroVADScriptTests(unittest.TestCase):
    def test_script_has_valid_bash_syntax(self) -> None:
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_help_does_not_require_network_or_dependencies(self) -> None:
        result = subprocess.run(["bash", str(SCRIPT), "--help"], check=True, text=True, capture_output=True)
        self.assertIn("--verify-only", result.stdout)
        self.assertIn("--destination", result.stdout)


if __name__ == "__main__":
    unittest.main()
