from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, text

from auraly_pipeline.campaigns.persistence import sqlite_url


_STARTUP_SCRIPT = """
import sys
import time
from pathlib import Path

from auraly_pipeline.jobs.service import JobService

database = Path(sys.argv[1])
ready = Path(sys.argv[2])
go = Path(sys.argv[3])
ready.touch()
while not go.exists():
    time.sleep(0.01)
service = JobService.for_database(database)
service.close()
"""


def test_concurrent_first_startup_serializes_alembic_migration(tmp_path: Path) -> None:
    database_path = tmp_path / "auraly.db"
    go = tmp_path / "go"
    processes: list[subprocess.Popen[str]] = []
    for index in range(6):
        ready = tmp_path / f"ready-{index}"
        processes.append(
            subprocess.Popen(
                [sys.executable, "-c", _STARTUP_SCRIPT, str(database_path), str(ready), str(go)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )

    deadline = time.monotonic() + 10
    while len(list(tmp_path.glob("ready-*"))) < len(processes):
        assert time.monotonic() < deadline, (
            "startup subprocesses did not reach the migration barrier"
        )
        time.sleep(0.01)
    go.touch()

    failures: list[str] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=30)
        if process.returncode != 0:
            failures.append(f"exit={process.returncode}\nstdout={stdout}\nstderr={stderr}")
    assert failures == []

    engine = create_engine(sqlite_url(database_path))
    with engine.connect() as connection:
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == (
            "0004_image_domain"
        )
    engine.dispose()
