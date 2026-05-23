"""
Persistência em SQLite. Uma tabela por entidade:
- quotes: cada cotação retornada (uma linha por voo + timestamp)
- alerts_sent: histórico de alertas para não disparar e-mail duplicado
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at      TEXT    NOT NULL,
    source          TEXT    NOT NULL,        -- 'travelpayouts' ou 'serpapi'
    origin          TEXT    NOT NULL,
    destination     TEXT    NOT NULL,
    outbound_date   TEXT    NOT NULL,
    return_date     TEXT,                    -- NULL para one-way
    direct          INTEGER NOT NULL DEFAULT 1,
    price           REAL    NOT NULL,
    currency        TEXT    NOT NULL,
    airline         TEXT,
    flight_number   TEXT,
    departure_time  TEXT,
    arrival_time    TEXT,
    duration_min    INTEGER,
    transfers       INTEGER DEFAULT 0,
    booking_link    TEXT,
    hub             TEXT,                    -- Aeroporto de conexao da ida (NULL para Travelpayouts legado)
    raw_json        TEXT
);

CREATE INDEX IF NOT EXISTS idx_quotes_route ON quotes(origin, destination, outbound_date);
CREATE INDEX IF NOT EXISTS idx_quotes_fetched ON quotes(fetched_at);

CREATE TABLE IF NOT EXISTS alerts_sent (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at         TEXT    NOT NULL,
    route_key       TEXT    NOT NULL,        -- ex: "GRU-FRA-2026-09-25-2026-10-11"
    price           REAL    NOT NULL,
    reason          TEXT
);

CREATE INDEX IF NOT EXISTS idx_alerts_route ON alerts_sent(route_key);
"""


class Storage:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            # Migracao in-place: adiciona coluna 'hub' se DB antigo nao tem
            cols = {row[1] for row in conn.execute("PRAGMA table_info(quotes)").fetchall()}
            if "hub" not in cols:
                conn.execute("ALTER TABLE quotes ADD COLUMN hub TEXT")

    # ----- Inserção -----

    def save_quote(
        self,
        source: str,
        origin: str,
        destination: str,
        outbound_date: str,
        return_date: Optional[str],
        direct: bool,
        price: float,
        currency: str,
        airline: Optional[str] = None,
        flight_number: Optional[str] = None,
        departure_time: Optional[str] = None,
        arrival_time: Optional[str] = None,
        duration_min: Optional[int] = None,
        transfers: int = 0,
        booking_link: Optional[str] = None,
        hub: Optional[str] = None,
        raw: Optional[dict] = None,
        fetched_at: Optional[str] = None,
    ):
        fetched_at = fetched_at or datetime.utcnow().isoformat(timespec="seconds")
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO quotes (
                    fetched_at, source, origin, destination,
                    outbound_date, return_date, direct, price, currency,
                    airline, flight_number, departure_time, arrival_time,
                    duration_min, transfers, booking_link, hub, raw_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    fetched_at, source, origin, destination,
                    outbound_date, return_date, 1 if direct else 0, price, currency,
                    airline, flight_number, departure_time, arrival_time,
                    duration_min, transfers, booking_link, hub,
                    json.dumps(raw, ensure_ascii=False) if raw else None,
                ),
            )

    def save_alert(self, route_key: str, price: float, reason: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO alerts_sent (sent_at, route_key, price, reason) VALUES (?,?,?,?)",
                (datetime.utcnow().isoformat(timespec="seconds"), route_key, price, reason),
            )

    # ----- Consultas -----

    def get_latest_quotes(self, source: str = "travelpayouts") -> list[dict]:
        """Retorna a cotação mais recente por (rota, datas) para uma fonte."""
        sql = """
        SELECT q.*
        FROM quotes q
        INNER JOIN (
            SELECT origin, destination, outbound_date,
                   COALESCE(return_date,'') AS rd,
                   MAX(fetched_at) AS mfa
            FROM quotes
            WHERE source = ?
            GROUP BY origin, destination, outbound_date, rd
        ) latest
        ON q.origin = latest.origin
           AND q.destination = latest.destination
           AND q.outbound_date = latest.outbound_date
           AND COALESCE(q.return_date,'') = latest.rd
           AND q.fetched_at = latest.mfa
        WHERE q.source = ?
        ORDER BY q.price ASC
        """
        with self._conn() as conn:
            cur = conn.execute(sql, (source, source))
            return [dict(row) for row in cur.fetchall()]

    def get_cheapest_per_route(self, source: str = "travelpayouts") -> list[dict]:
        """Para cada rota+datas, retorna a linha de menor preço (último sweep)."""
        latest = self.get_latest_quotes(source)
        # Agrupar e pegar mínimo
        groups: dict[tuple, dict] = {}
        for q in latest:
            key = (q["origin"], q["destination"], q["outbound_date"], q.get("return_date") or "")
            if key not in groups or q["price"] < groups[key]["price"]:
                groups[key] = q
        return sorted(groups.values(), key=lambda x: x["price"])

    def get_historical_min(
        self,
        origin: str,
        destination: str,
        outbound_date: str,
        return_date: Optional[str],
        source: str = "travelpayouts",
        exclude_current_sweep: bool = True,
    ) -> Optional[float]:
        """Menor preço histórico para essa rota+datas."""
        params = [source, origin, destination, outbound_date]
        sql = """
            SELECT MIN(price) FROM quotes
            WHERE source = ? AND origin = ? AND destination = ? AND outbound_date = ?
        """
        if return_date:
            sql += " AND return_date = ?"
            params.append(return_date)
        else:
            sql += " AND return_date IS NULL"

        if exclude_current_sweep:
            # Exclui a última hora (sweep atual)
            cutoff = (datetime.utcnow() - timedelta(hours=1)).isoformat(timespec="seconds")
            sql += " AND fetched_at < ?"
            params.append(cutoff)

        with self._conn() as conn:
            cur = conn.execute(sql, params)
            row = cur.fetchone()
            return row[0] if row and row[0] is not None else None

    def get_history(
        self,
        origin: str,
        destination: str,
        outbound_date: str,
        return_date: Optional[str] = None,
        source: str = "travelpayouts",
    ) -> list[dict]:
        """Série temporal: cotação mais barata por sweep, ordenada por tempo."""
        params = [source, origin, destination, outbound_date]
        sql = """
            SELECT fetched_at, MIN(price) AS price
            FROM quotes
            WHERE source = ? AND origin = ? AND destination = ? AND outbound_date = ?
        """
        if return_date:
            sql += " AND return_date = ?"
            params.append(return_date)
        else:
            sql += " AND return_date IS NULL"
        sql += " GROUP BY fetched_at ORDER BY fetched_at ASC"

        with self._conn() as conn:
            cur = conn.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]

    def get_latest_by_hub(
        self,
        origin: str,
        destination: str,
        outbound_date: str,
        return_date: Optional[str],
        source: str = "travelpayouts",
    ) -> list[dict]:
        """Para cada hub, retorna a cotacao mais recente (e a mais barata, em empate)."""
        with self._conn() as conn:
            cur = conn.execute(
                """
                SELECT q.* FROM quotes q
                INNER JOIN (
                    SELECT hub, MAX(fetched_at) AS mfa
                    FROM quotes
                    WHERE source = ? AND origin = ? AND destination = ?
                      AND outbound_date = ? AND COALESCE(return_date,'') = COALESCE(?,'')
                      AND hub IS NOT NULL
                    GROUP BY hub
                ) latest ON q.hub = latest.hub AND q.fetched_at = latest.mfa
                WHERE q.source = ? AND q.origin = ? AND q.destination = ?
                  AND q.outbound_date = ? AND COALESCE(q.return_date,'') = COALESCE(?,'')
                  AND q.hub IS NOT NULL
                ORDER BY q.price ASC
                """,
                (source, origin, destination, outbound_date, return_date,
                 source, origin, destination, outbound_date, return_date),
            )
            # Pega o mais barato por hub (em caso de varios voos no mesmo sweep)
            seen = {}
            for row in cur.fetchall():
                r = dict(row)
                h = r["hub"]
                if h not in seen or r["price"] < seen[h]["price"]:
                    seen[h] = r
            return sorted(seen.values(), key=lambda x: x["price"])

    def get_history_by_hub(
        self,
        origin: str,
        destination: str,
        outbound_date: str,
        return_date: Optional[str],
        source: str = "travelpayouts",
    ) -> dict[str, list[dict]]:
        """Para cada hub, serie temporal {hub: [{day, min_price}, ...]}."""
        with self._conn() as conn:
            cur = conn.execute(
                """
                SELECT hub, fetched_at AS ts, MIN(price) AS price
                FROM quotes
                WHERE source = ? AND origin = ? AND destination = ?
                  AND outbound_date = ? AND COALESCE(return_date,'') = COALESCE(?,'')
                  AND hub IS NOT NULL
                GROUP BY hub, ts
                ORDER BY hub, ts
                """,
                (source, origin, destination, outbound_date, return_date),
            )
            out: dict[str, list[dict]] = {}
            for row in cur.fetchall():
                out.setdefault(row["hub"], []).append({"ts": row["ts"], "price": row["price"]})
            return out

    def get_historical_min_by_hub(
        self,
        origin: str,
        destination: str,
        outbound_date: str,
        return_date: Optional[str],
        hub: str,
        source: str = "travelpayouts",
    ) -> Optional[float]:
        with self._conn() as conn:
            cur = conn.execute(
                """
                SELECT MIN(price) FROM quotes
                WHERE source = ? AND origin = ? AND destination = ?
                  AND outbound_date = ? AND COALESCE(return_date,'') = COALESCE(?,'')
                  AND hub = ?
                """,
                (source, origin, destination, outbound_date, return_date, hub),
            )
            row = cur.fetchone()
            return row[0] if row and row[0] is not None else None

    def get_alert_count_recent(self, route_key: str, hours: int = 24) -> int:
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat(timespec="seconds")
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM alerts_sent WHERE route_key = ? AND sent_at >= ?",
                (route_key, cutoff),
            )
            return cur.fetchone()[0]
