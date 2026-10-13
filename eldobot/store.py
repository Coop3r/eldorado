import sqlite3
from pathlib import Path


class Store:
    """Remembers what has already been announced, so restarts don't repeat alerts."""

    def __init__(self, path: str | Path = "eldobot.db"):
        self._db = sqlite3.connect(path)
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen (
                kind TEXT NOT NULL, id TEXT NOT NULL, state TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (kind, id)
            );
            CREATE TABLE IF NOT EXISTS seeded (kind TEXT PRIMARY KEY);
            """
        )

    def close(self) -> None:
        self._db.close()

    def is_seeded(self, kind: str) -> bool:
        return self._db.execute("SELECT 1 FROM seeded WHERE kind = ?", (kind,)).fetchone() is not None

    def mark_seeded(self, kind: str) -> None:
        with self._db:
            self._db.execute("INSERT OR IGNORE INTO seeded (kind) VALUES (?)", (kind,))

    def get_state(self, kind: str, id: str) -> str | None:
        """Return the stored state, or None if this id has never been seen."""
        row = self._db.execute(
            "SELECT state FROM seen WHERE kind = ? AND id = ?", (kind, id)
        ).fetchone()
        return row[0] if row else None

    def set_state(self, kind: str, id: str, state: str = "") -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO seen (kind, id, state) VALUES (?, ?, ?) "
                "ON CONFLICT (kind, id) DO UPDATE SET state = excluded.state",
                (kind, id, state),
            )
