"""
Long-term storage for recordings, in a single SQLite file on the Pi.

SQLite is a real SQL database that needs no server and no internet: it is part
of Python. The file lives in data/mulsecube.db next to the program, and can be
opened with the `sqlite3` command for ad-hoc queries.

Two tables:
  sessions - one row per recording (name, start, end, sampling interval)
  samples  - one row per sensor reading, tagged with the session it belongs to
"""
import os
import sqlite3
from contextlib import contextmanager

# MULSECUBE_DB lets you point the program at a different file (handy for testing).
DEFAULT_PATH = os.environ.get(
    "MULSECUBE_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "mulsecube.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    started      REAL    NOT NULL,          -- unix time (seconds)
    ended        REAL,                      -- NULL while the recording is running
    interval_s   REAL    NOT NULL,          -- seconds between samples
    sample_count INTEGER NOT NULL DEFAULT 0,
    last_ts      REAL
);

CREATE TABLE IF NOT EXISTS samples (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    ts           REAL    NOT NULL,          -- unix time (seconds); equal for one snapshot
    sensor_id    TEXT    NOT NULL,          -- which physical sensor (serial, pin or address)
    model        TEXT    NOT NULL,          -- e.g. DHT22
    measurement  TEXT    NOT NULL,          -- e.g. temperature
    unit         TEXT,
    value        REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_samples_session_ts ON samples(session_id, ts);
"""

SESSION_COLUMNS = "id, name, started, ended, interval_s, sample_count, last_ts"


class Database:
    def __init__(self, path=None):
        self.path = path or DEFAULT_PATH
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        conn = self._connect()
        try:
            # WAL lets the export read while the recorder is still writing, and
            # survives a power cut without corrupting the file.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------ plumbing --

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @contextmanager
    def _conn(self):
        conn = self._connect()
        try:
            with conn:                      # commit if all went well, roll back if not
                yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------ sessions --

    def create_session(self, name, started, interval_s):
        with self._conn() as c:
            cursor = c.execute(
                "INSERT INTO sessions (name, started, interval_s) VALUES (?, ?, ?)",
                (name, started, interval_s),
            )
            return cursor.lastrowid

    def end_session(self, session_id, ended):
        with self._conn() as c:
            c.execute("UPDATE sessions SET ended = ? WHERE id = ?", (ended, session_id))

    def get_session(self, session_id):
        with self._conn() as c:
            row = c.execute(
                f"SELECT {SESSION_COLUMNS} FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_sessions(self):
        """Newest first."""
        with self._conn() as c:
            rows = c.execute(
                f"SELECT {SESSION_COLUMNS} FROM sessions ORDER BY id DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def active_session(self):
        """The most recent session that has no end time (i.e. was still recording)."""
        with self._conn() as c:
            row = c.execute(
                f"SELECT {SESSION_COLUMNS} FROM sessions WHERE ended IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def close_other_active_sessions(self, keep_id):
        """Tidies up if more than one session was left open (it should never happen)."""
        with self._conn() as c:
            c.execute(
                "UPDATE sessions SET ended = COALESCE(last_ts, started) "
                "WHERE ended IS NULL AND id != ?",
                (keep_id,),
            )

    def delete_session(self, session_id):
        with self._conn() as c:
            c.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    # ------------------------------------------------------------- samples --

    def add_samples(self, session_id, rows):
        """
        rows: (ts, sensor_id, model, measurement, unit, value) tuples.
        Inserts them and updates the session's counters in one transaction.
        """
        if not rows:
            return
        with self._conn() as c:
            c.executemany(
                "INSERT INTO samples (session_id, ts, sensor_id, model, measurement, unit, value) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(session_id,) + tuple(r) for r in rows],
            )
            c.execute(
                "UPDATE sessions SET sample_count = sample_count + ?, "
                "last_ts = MAX(COALESCE(last_ts, 0), ?) WHERE id = ?",
                (len(rows), max(r[0] for r in rows), session_id),
            )

    def session_columns(self, session_id):
        """The distinct (sensor, measurement) series in a session, in order of first appearance."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT sensor_id, model, measurement, unit, MIN(id) AS first_id "
                "FROM samples WHERE session_id = ? "
                "GROUP BY sensor_id, model, measurement ORDER BY first_id",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def iter_samples(self, session_id):
        """Streams (ts, sensor_id, model, measurement, value) in time order, without loading it all."""
        conn = self._connect()
        try:
            cursor = conn.execute(
                "SELECT ts, sensor_id, model, measurement, value FROM samples "
                "WHERE session_id = ? ORDER BY ts, id",
                (session_id,),
            )
            for row in cursor:
                yield tuple(row)
        finally:
            conn.close()

    def count_timestamps(self, session_id):
        with self._conn() as c:
            return c.execute(
                "SELECT COUNT(DISTINCT ts) FROM samples WHERE session_id = ?", (session_id,)
            ).fetchone()[0]

    def column_stats(self, session_id):
        """Per-series count / min / max / average, computed by SQL."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT sensor_id, model, measurement, COUNT(*) AS n, MIN(value) AS lo, "
                "MAX(value) AS hi, AVG(value) AS mean FROM samples WHERE session_id = ? "
                "GROUP BY sensor_id, model, measurement ORDER BY MIN(id)",
                (session_id,),
            ).fetchall()
        return [dict(r) for r in rows]
