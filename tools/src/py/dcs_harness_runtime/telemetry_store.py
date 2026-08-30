"""Per-session SQLite persistence for normalized factual telemetry."""

from __future__ import annotations

import re
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


SESSION_ID_PATTERN = re.compile(r"[0-9]+")


class TelemetryWriter:
    """The collector-owned writer for one DCS-gRPC session database."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def append(self, snapshot: Mapping[str, Any]) -> None:
        units = snapshot["units"]
        snapshot_values = (
            snapshot["snapshot_id"],
            snapshot["session_id"],
            snapshot["mission_time"],
            snapshot["captured_at"],
            snapshot["capture_duration_ms"],
            snapshot["unit_count"],
            int(snapshot["partial"]),
        )
        sample_values = [self._sample_values(sample) for sample in units]
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """
                INSERT INTO snapshots (
                    snapshot_id, session_id, mission_time, captured_at,
                    capture_duration_ms, unit_count, partial
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                snapshot_values,
            )
            self.connection.executemany(
                """
                INSERT INTO unit_samples (
                    snapshot_id, session_id, mission_time, captured_at,
                    instance_id, unit_id, unit_name, unit_type, category,
                    coalition, country, group_id, group_name,
                    x_m, y_m, z_m, vx_mps, vy_mps, vz_mps,
                    heading_deg, ground_speed_mps, vertical_speed_mps,
                    life, life_initial, fuel_fraction, in_air, player_name
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                sample_values,
            )
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    @staticmethod
    def _sample_values(sample: Mapping[str, Any]) -> tuple[Any, ...]:
        unit = sample["unit"]
        group = sample["group"]
        position = sample["position"]
        velocity = sample["velocity"]
        in_air = sample["in_air"]
        return (
            sample["snapshot_id"],
            sample["session_id"],
            sample["mission_time"],
            sample["captured_at"],
            sample["instance_id"],
            unit["id"],
            unit["name"],
            unit["type"],
            unit["category"],
            unit["coalition"],
            unit["country"],
            group["id"],
            group["name"],
            position["x_m"],
            position["y_m"],
            position["z_m"],
            velocity["x_mps"],
            velocity["y_mps"],
            velocity["z_mps"],
            sample["heading_deg"],
            sample["ground_speed_mps"],
            sample["vertical_speed_mps"],
            sample["life"],
            sample["life_initial"],
            sample["fuel_fraction"],
            None if in_air is None else int(in_air),
            sample["player_name"],
        )


class TelemetryStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    snapshot_id INTEGER PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    mission_time REAL NOT NULL,
                    captured_at TEXT NOT NULL,
                    capture_duration_ms REAL NOT NULL,
                    unit_count INTEGER NOT NULL,
                    partial INTEGER NOT NULL CHECK (partial IN (0, 1))
                );

                CREATE TABLE IF NOT EXISTS unit_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id INTEGER NOT NULL,
                    session_id TEXT NOT NULL,
                    mission_time REAL NOT NULL,
                    captured_at TEXT NOT NULL,
                    instance_id TEXT NOT NULL,
                    unit_id INTEGER NOT NULL,
                    unit_name TEXT,
                    unit_type TEXT NOT NULL,
                    category TEXT NOT NULL,
                    coalition TEXT NOT NULL,
                    country TEXT,
                    group_id INTEGER NOT NULL,
                    group_name TEXT NOT NULL,
                    x_m REAL NOT NULL,
                    y_m REAL NOT NULL,
                    z_m REAL NOT NULL,
                    vx_mps REAL NOT NULL,
                    vy_mps REAL NOT NULL,
                    vz_mps REAL NOT NULL,
                    heading_deg REAL,
                    ground_speed_mps REAL NOT NULL,
                    vertical_speed_mps REAL NOT NULL,
                    life REAL,
                    life_initial REAL,
                    fuel_fraction REAL,
                    in_air INTEGER CHECK (in_air IS NULL OR in_air IN (0, 1)),
                    player_name TEXT,
                    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id)
                );

                CREATE INDEX IF NOT EXISTS unit_samples_snapshot_idx
                    ON unit_samples(snapshot_id);
                CREATE INDEX IF NOT EXISTS unit_samples_instance_time_idx
                    ON unit_samples(instance_id, mission_time);
                CREATE INDEX IF NOT EXISTS unit_samples_unit_name_time_idx
                    ON unit_samples(unit_name, mission_time);
                CREATE INDEX IF NOT EXISTS unit_samples_group_name_time_idx
                    ON unit_samples(group_name, mission_time);
                CREATE INDEX IF NOT EXISTS unit_samples_coalition_time_idx
                    ON unit_samples(coalition, mission_time);
                """
            )

    @contextmanager
    def writer(self) -> Iterator[TelemetryWriter]:
        connection = self._connect()
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield TelemetryWriter(connection)
        finally:
            connection.close()

    def count(self) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()
        return int(row[0])

    def resume_state(self) -> dict[str, Any]:
        """Return only the state needed to continue IDs after a Harness restart."""
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            latest = connection.execute(
                "SELECT MAX(snapshot_id) AS snapshot_id FROM snapshots"
            ).fetchone()
            latest_id = latest["snapshot_id"]
            complete = connection.execute(
                """
                SELECT MAX(snapshot_id) AS snapshot_id
                FROM snapshots
                WHERE partial = 0
                """
            ).fetchone()
            complete_id = complete["snapshot_id"]
            active_rows = connection.execute(
                """
                SELECT snapshot_id, unit_id, unit_name, instance_id
                FROM unit_samples
                WHERE snapshot_id >= ?
                ORDER BY snapshot_id, id
                """,
                (complete_id or 0,),
            ).fetchall()
            identity_rows = connection.execute(
                """
                SELECT DISTINCT unit_id, unit_name, instance_id
                FROM unit_samples
                """
            ).fetchall()

        active_instances: dict[int, tuple[str | None, str]] = {}
        for row in active_rows:
            active_instances[int(row["unit_id"])] = (
                row["unit_name"],
                str(row["instance_id"]),
            )
        generations: dict[tuple[int, str | None], int] = {}
        for row in identity_rows:
            instance_id = str(row["instance_id"])
            try:
                generation = int(instance_id.rsplit(":", 1)[1])
            except (IndexError, ValueError):
                continue
            key = (int(row["unit_id"]), row["unit_name"])
            generations[key] = max(generations.get(key, 0), generation)
        return {
            "next_snapshot_id": int(latest_id or 0) + 1,
            "active_instances": active_instances,
            "generations": generations,
        }

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5.0)


class TelemetryStoreCatalog:
    """Creates and reopens one telemetry database per DCS-gRPC session."""

    def __init__(self, root: Path, repository_root: Path) -> None:
        self.root = root.resolve()
        self.repository_root = repository_root.resolve()

    def select(self, session_id: str) -> TelemetryStore:
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError("DCS session ID is not a filesystem-safe integer.")
        self.root.mkdir(parents=True, exist_ok=True)
        existing = sorted(self.root.glob(f"*_{session_id}.sqlite"))
        if existing:
            path = existing[-1]
        else:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            path = self.root / f"{timestamp}_{session_id}.sqlite"
        store = TelemetryStore(path)
        store.initialize()
        return store

    def display_path(self, store: TelemetryStore) -> str:
        return store.path.resolve().relative_to(self.repository_root).as_posix()
