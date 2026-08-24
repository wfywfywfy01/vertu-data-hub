import subprocess
from pathlib import Path

from scripts.backup_database import connection_environment, create_backup


def test_connection_environment_keeps_password_out_of_command_line():
    env = connection_environment(
        "postgresql://data%20hub:s%40cret@db.internal:5433/knowledge?sslmode=require"
    )

    assert env == {
        "PGHOST": "db.internal",
        "PGPORT": "5433",
        "PGDATABASE": "knowledge",
        "PGUSER": "data hub",
        "PGPASSWORD": "s@cret",
        "PGSSLMODE": "require",
    }


def test_create_backup_verifies_then_publishes(monkeypatch, tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        output = next((value.split("=", 1)[1] for value in command if value.startswith("--file=")), None)
        if output:
            Path(output).write_bytes(b"verified-backup")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", run)
    result = create_backup("postgresql://user:pass@db/data", tmp_path)

    assert result.read_bytes() == b"verified-backup"
    assert calls[1][0:2] == ["pg_restore", "--list"]
    assert not list(tmp_path.glob("*.part"))
