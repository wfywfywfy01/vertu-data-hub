"""Create and verify an atomic PostgreSQL custom-format backup."""
from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


def connection_environment(database_url: str) -> dict[str, str]:
    parsed = urlsplit(database_url)
    if not parsed.scheme.startswith("postgresql") or not parsed.hostname or not parsed.path.strip("/"):
        raise ValueError("DATABASE_URL must be a PostgreSQL URL with host and database")
    env = {
        "PGHOST": parsed.hostname,
        "PGPORT": str(parsed.port or 5432),
        "PGDATABASE": unquote(parsed.path.lstrip("/")),
        "PGUSER": unquote(parsed.username or ""),
        "PGPASSWORD": unquote(parsed.password or ""),
    }
    sslmode = parse_qs(parsed.query).get("sslmode", [])
    if sslmode:
        env["PGSSLMODE"] = sslmode[0]
    return env


def create_backup(
    database_url: str,
    backup_dir: Path,
    *,
    retention: int = 14,
    pg_dump: str = "pg_dump",
    pg_restore: str = "pg_restore",
) -> Path:
    if retention < 1:
        raise ValueError("retention must be at least 1")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = backup_dir / f"vertu_data_hub_{stamp}.dump"
    temporary = target.with_suffix(".dump.part")
    env = {**os.environ, **connection_environment(database_url)}
    try:
        subprocess.run(
            [pg_dump, "--format=custom", "--no-owner", "--no-acl", f"--file={temporary}"],
            check=True,
            env=env,
        )
        subprocess.run(
            [pg_restore, "--list", str(temporary)],
            check=True,
            env=env,
            stdout=subprocess.DEVNULL,
        )
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise RuntimeError("pg_dump produced an empty backup")
        temporary.replace(target)
        target.chmod(0o600)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    backups = sorted(backup_dir.glob("vertu_data_hub_*.dump"), key=lambda path: path.stat().st_mtime)
    for expired in backups[:-retention]:
        expired.unlink()
    return target


def main() -> None:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    path = create_backup(
        database_url,
        Path(os.environ.get("BACKUP_DIR", "/backups")),
        retention=int(os.environ.get("BACKUP_RETENTION", "14")),
        pg_dump=os.environ.get("PG_DUMP_COMMAND", "pg_dump"),
        pg_restore=os.environ.get("PG_RESTORE_COMMAND", "pg_restore"),
    )
    print(f"backup verified: {path.name} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
