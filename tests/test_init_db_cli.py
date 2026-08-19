import subprocess
import sys
from pathlib import Path


def test_init_db_script_runs_from_repository_root():
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, "scripts/init_db.py"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "schema applied" in result.stdout
