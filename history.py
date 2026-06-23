import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "history.db"

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS readings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,
    soc         INTEGER,
    ppv         REAL,
    pac         REAL,
    plocal_load REAL,
    pcharge1    REAL,
    pdischarge1 REAL,
    price_kwh   REAL,
    price_desc  TEXT,
    cloud_pct   INTEGER,
    solar_wm2   REAL,
    temperature REAL
);

CREATE TABLE IF NOT EXISTS decisions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           INTEGER NOT NULL,
    action       TEXT,
    reason       TEXT,
    priority     INTEGER,
    was_override INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_readings_ts  ON readings(ts);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts);
"""


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as c:
        c.executescript(_SCHEMA)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def write_reading(state, prices=None, weather=None) -> None:
    current = next((p for p in (prices or []) if not p.is_forecast), None)
    ts = int(datetime.now(timezone.utc).timestamp())
    with _conn() as c:
        c.execute("""
            INSERT INTO readings
              (ts, soc, ppv, pac, plocal_load, pcharge1, pdischarge1,
               price_kwh, price_desc, cloud_pct, solar_wm2, temperature)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            ts,
            state.soc, state.ppv, state.pac, state.plocal_load,
            state.pcharge1, state.pdischarge1,
            current.per_kwh if current else None,
            current.descriptor if current else None,
            weather.cloud_cover if weather else None,
            weather.solar_radiation if weather else None,
            weather.temperature if weather else None,
        ))


def write_decision(decision, was_override: bool = False) -> None:
    ts = int(datetime.now(timezone.utc).timestamp())
    with _conn() as c:
        c.execute("""
            INSERT INTO decisions (ts, action, reason, priority, was_override)
            VALUES (?,?,?,?,?)
        """, (ts, decision.action, decision.reason, decision.priority, int(was_override)))


def _bucket(hours: int) -> int:
    if hours <= 24:  return 0        # raw ~5-min data
    if hours <= 168: return 1800     # 30-min averages for 7d
    return 14400                     # 4-hour averages for 30d


def query_readings(hours: int = 24) -> list:
    since = int(datetime.now(timezone.utc).timestamp()) - hours * 3600
    b = _bucket(hours)
    with _conn() as c:
        if b == 0:
            rows = c.execute(
                "SELECT * FROM readings WHERE ts >= ? ORDER BY ts", (since,)
            ).fetchall()
        else:
            rows = c.execute(f"""
                SELECT
                    (ts/{b})*{b}              AS ts,
                    ROUND(AVG(soc))           AS soc,
                    ROUND(AVG(ppv),1)         AS ppv,
                    ROUND(AVG(pac),1)         AS pac,
                    ROUND(AVG(plocal_load),1) AS plocal_load,
                    ROUND(AVG(pcharge1),1)    AS pcharge1,
                    ROUND(AVG(pdischarge1),1) AS pdischarge1,
                    ROUND(AVG(price_kwh),2)   AS price_kwh,
                    price_desc,
                    ROUND(AVG(cloud_pct))     AS cloud_pct,
                    ROUND(AVG(solar_wm2),1)   AS solar_wm2,
                    ROUND(AVG(temperature),1) AS temperature
                FROM readings WHERE ts >= ?
                GROUP BY ts/{b}
                ORDER BY ts
            """, (since,)).fetchall()
    return [dict(r) for r in rows]


def query_decisions(hours: int = 24) -> list:
    since = int(datetime.now(timezone.utc).timestamp()) - hours * 3600
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM decisions WHERE ts >= ? ORDER BY ts DESC", (since,)
        ).fetchall()
    return [dict(r) for r in rows]
