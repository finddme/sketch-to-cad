import subprocess
import sys
from pathlib import Path

import pytest

RUN_PY = Path(__file__).resolve().parent.parent / "skills" / "sketch-to-cad" / "scripts" / "run.py"


@pytest.fixture
def run_cli():
    def _run(*args):
        return subprocess.run(
            [sys.executable, str(RUN_PY), *map(str, args)],
            capture_output=True,
            text=True,
        )

    return _run
