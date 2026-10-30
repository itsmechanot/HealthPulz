"""Outbreak alerts: detection after each analytics run, delivery to nodes, acknowledgement.

Detection checks the current and the previous ISO week in weekly_stats (cases
are often reported a few days after onset):
    EPIDEMIC  count > epidemic threshold  and count ≥ MIN_ALERT_CASES
    ALERT     count > alert threshold     and count ≥ MIN_ALERT_CASES
    any case of a single-case-alert disease (measles) → ALERT

There is one alert per barangay × disease × week. It is sent again only if it
escalates from ALERT to EPIDEMIC.

Delivery is store-and-forward: an alert goes to the nodes of the affected
barangay (scope LOCAL) and of its neighboring barangays (scope NEIGHBOR).
Nodes that are offline receive it when they reconnect (HELLO). Every delivery
and acknowledgement is recorded in alert_deliveries.
"""

import asyncio
import sqlite3
import uuid
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from rhu import db, rules
from rhu.config import MIN_ALERT_CASES
from rhu.hub import Hub

LEVEL_RANK = {"ALERT": 1, "EPIDEMIC": 2}


def classify(
    count: int, alert_threshold: float | None, epidemic_threshold: float | None, single_case_alert: bool
) -> tuple[str, float] | None:
    """Returns (level, threshold that was exceeded), or None if there is nothing to report."""
    if single_case_alert:
        return ("ALERT", 0.0) if count >= 1 else None
    if alert_threshold is None or count < MIN_ALERT_CASES:
        return None
    if epidemic_threshold is not None and count > epidemic_threshold:
        return "EPIDEMIC", epidemic_threshold
    if count > alert_threshold:
        return "ALERT", alert_threshold
    return None


def weeks_to_check(today: date) -> list[tuple[int, int]]:
    """(iso_year, iso_week) of the previous and the current week."""
    monday = today - timedelta(days=today.weekday())
    return [week.isocalendar()[:2] for week in (monday - timedelta(weeks=1), monday)]


def detect_alerts(db_path: Path, today: date) -> list[dict]:
    """Create new alerts and escalate existing ones. Returns the alerts that must be sent."""
    diseases = {d["disease_code"]: d for d in rules.diseases()}
    to_send = []
    with closing(db.connect(db_path)) as conn, conn:
        for year, week in weeks_to_check(today):
            rows = conn.execute(
                """
                SELECT barangay_id, disease_code, case_count, alert_threshold, epidemic_threshold
                FROM weekly_stats WHERE iso_year = ? AND iso_week = ?
                """,
                (year, week),
            ).fetchall()
            for row in rows:
                disease = diseases[row["disease_code"]]
                outcome = classify(
                    row["case_count"], row["alert_threshold"], row["epidemic_threshold"], disease["single_case_alert"]
                )
                if outcome is None:
                    continue
                level, threshold = outcome
                message = _describe(disease, row["barangay_id"], year, week, row["case_count"], level, threshold)
                alert_id = _save(conn, row, year, week, level, threshold, message)
                if alert_id:
                    to_send.append(alert_id)
        return [get_alert(conn, alert_id) for alert_id in to_send]


def _save(conn, row, year, week, level, threshold, message) -> str | None:
    """Insert or update the alert for this cell. Returns its id if it is new or escalated."""
    existing = conn.execute(
        "SELECT alert_id, level FROM alerts WHERE barangay_id = ? AND disease_code = ? AND iso_year = ? AND iso_week = ?",
        (row["barangay_id"], row["disease_code"], year, week),
    ).fetchone()

    if existing is None:
        alert_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO alerts (alert_id, disease_code, barangay_id, level, iso_year, iso_week,
                                observed, threshold, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (alert_id, row["disease_code"], row["barangay_id"], level, year, week,
             row["case_count"], threshold, message, db.utc_now()),
        )
        return alert_id

    rank, previous_rank = LEVEL_RANK[level], LEVEL_RANK[existing["level"]]
    if rank < previous_rank:
        return None  # keep the higher level that was already reported
    escalated = rank > previous_rank
    # Same level: refresh the count quietly. Escalation: reopen and send again.
    conn.execute(
        """
        UPDATE alerts SET level = ?, observed = ?, threshold = ?, message = ?,
                          status = CASE WHEN ? THEN 'OPEN' ELSE status END
        WHERE alert_id = ?
        """,
        (level, row["case_count"], threshold, message, escalated, existing["alert_id"]),
    )
    return existing["alert_id"] if escalated else None


def _describe(disease: dict, barangay_id: str, year: int, week: int, count: int, level: str, threshold: float) -> str:
    place = f"Brgy. {rules.barangays_by_id()[barangay_id]['name']}"
    if disease["single_case_alert"]:
        return f"{count} {disease['name'].lower()} case(s) in {place} in week {year}-W{week:02d}. Any case needs immediate investigation."
    return (
        f"{disease['name']} cases in {place} in week {year}-W{week:02d}: {count}, "
        f"above the {level.lower()} threshold of {threshold:.1f}."
    )


def get_alert(conn: sqlite3.Connection, alert_id: str) -> dict:
    return dict(conn.execute("SELECT * FROM alerts WHERE alert_id = ?", (alert_id,)).fetchone())


def recipients(db_path: Path, barangay_id: str) -> list[tuple[str, str]]:
    """(node_id, scope) of every live node in the barangay (LOCAL) and its neighbors (NEIGHBOR)."""
    barangays = [barangay_id, *rules.barangays_by_id()[barangay_id]["neighbors"]]
    with closing(db.connect(db_path)) as conn:
        rows = conn.execute(
            f"SELECT node_id, barangay_id FROM nodes WHERE kind = 'LIVE' AND barangay_id IN ({','.join('?' * len(barangays))})",
            barangays,
        ).fetchall()
    return [(r["node_id"], "LOCAL" if r["barangay_id"] == barangay_id else "NEIGHBOR") for r in rows]


def pending_for_node(db_path: Path, node_id: str, today: date) -> list[tuple[dict, str]]:
    """Unresolved alerts of the checked weeks that this node should see but hasn't acknowledged."""
    with closing(db.connect(db_path)) as conn:
        node = conn.execute("SELECT barangay_id FROM nodes WHERE node_id = ?", (node_id,)).fetchone()
        if node is None:
            return []
        own = node["barangay_id"]
        relevant = {own} | {b["barangay_id"] for b in rules.barangays() if own in b["neighbors"]}
        weeks = weeks_to_check(today)
        rows = conn.execute(
            f"""
            SELECT a.* FROM alerts a
            LEFT JOIN alert_deliveries d ON d.alert_id = a.alert_id AND d.node_id = ?
            WHERE a.status != 'RESOLVED' AND d.acked_at IS NULL
              AND a.barangay_id IN ({','.join('?' * len(relevant))})
              AND ((a.iso_year = ? AND a.iso_week = ?) OR (a.iso_year = ? AND a.iso_week = ?))
            ORDER BY a.created_at
            """,
            (node_id, *relevant, *weeks[0], *weeks[1]),
        ).fetchall()
    return [(dict(r), "LOCAL" if r["barangay_id"] == own else "NEIGHBOR") for r in rows]


def mark_delivered(db_path: Path, alert_id: str, node_ids: list[str]) -> None:
    now = db.utc_now()
    with closing(db.connect(db_path)) as conn, conn:
        conn.executemany(
            """
            INSERT INTO alert_deliveries (alert_id, node_id, delivered_at) VALUES (?, ?, ?)
            ON CONFLICT (alert_id, node_id) DO UPDATE SET delivered_at = excluded.delivered_at
            """,
            [(alert_id, node_id, now) for node_id in node_ids],
        )


def acknowledge(db_path: Path, alert_id: str, node_id: str) -> bool:
    """Record that a node acknowledged an alert. False if the alert doesn't exist."""
    try:
        with closing(db.connect(db_path)) as conn, conn:
            conn.execute(
                """
                INSERT INTO alert_deliveries (alert_id, node_id, acked_at) VALUES (?, ?, ?)
                ON CONFLICT (alert_id, node_id) DO UPDATE SET acked_at = excluded.acked_at
                """,
                (alert_id, node_id, db.utc_now()),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def to_message(alert: dict, scope: str) -> dict:
    return {
        "type": "ALERT",
        "scope": scope,
        **{key: alert[key] for key in (
            "alert_id", "level", "disease_code", "barangay_id", "iso_year", "iso_week",
            "observed", "threshold", "message", "created_at",
        )},
    }


class AlertService:
    """Connects detection and delivery to the WebSocket hub. Runs on the event loop;
    database work is done in threads."""

    def __init__(self, db_path: Path, hub: Hub) -> None:
        self.db_path = db_path
        self.hub = hub
        self._lock = asyncio.Lock()  # the scheduler and a manual run must not detect at the same time

    async def check_and_publish(self, today: date | None = None) -> list[dict]:
        async with self._lock:
            alerts = await asyncio.to_thread(detect_alerts, self.db_path, today or date.today())
            for alert in alerts:
                targets = await asyncio.to_thread(recipients, self.db_path, alert["barangay_id"])
                await self._send(alert, targets)
        return alerts

    async def send_pending(self, node_id: str, today: date | None = None) -> None:
        """Called when a node connects: deliver what it missed while offline."""
        for alert, scope in await asyncio.to_thread(pending_for_node, self.db_path, node_id, today or date.today()):
            await self._send(alert, [(node_id, scope)])

    async def acknowledge(self, alert_id: str, node_id: str) -> bool:
        return await asyncio.to_thread(acknowledge, self.db_path, alert_id, node_id)

    async def _send(self, alert: dict, targets: list[tuple[str, str]]) -> None:
        delivered = []
        for node_id, scope in targets:
            if self.hub.is_connected(node_id):
                await self.hub.send_to_node(node_id, to_message(alert, scope))
                delivered.append(node_id)
        if delivered:
            await asyncio.to_thread(mark_delivered, self.db_path, alert["alert_id"], delivered)
