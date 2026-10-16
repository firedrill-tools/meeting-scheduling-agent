"""Transactional synthetic calendar and confirmation outbox."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("UTC timestamp with Z or +00:00 required")
    return parsed.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class CalendarStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
                INSERT OR IGNORE INTO meta(key,value) VALUES('revision',0);
                CREATE TABLE IF NOT EXISTS people (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL,
                    timezone TEXT NOT NULL, work_start INTEGER NOT NULL, work_end INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS busy (
                    person_id TEXT NOT NULL, start_utc TEXT NOT NULL, end_utc TEXT NOT NULL,
                    event_id TEXT
                );
                CREATE TABLE IF NOT EXISTS meetings (
                    event_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL, start_utc TEXT NOT NULL, end_utc TEXT NOT NULL,
                    participant_ids TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    event_id TEXT PRIMARY KEY, recipients_json TEXT NOT NULL, message TEXT NOT NULL
                );
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self, fixture_path: str | Path) -> None:
        fixture = json.loads(Path(fixture_path).read_text())
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT COUNT(*) FROM people").fetchone()[0]:
                raise ValueError("calendar already initialized")
            for person in fixture["people"]:
                ZoneInfo(person["timezone"])
                db.execute("INSERT INTO people VALUES(?,?,?,?,?,?)", (
                    person["id"], person["name"], person["email"], person["timezone"],
                    person["work_start"], person["work_end"],
                ))
            for block in fixture["busy"]:
                db.execute("INSERT INTO busy VALUES(?,?,?,NULL)", (
                    block["person_id"], iso(parse_utc(block["start_utc"])), iso(parse_utc(block["end_utc"])),
                ))

    def _revision(self, db) -> int:
        return db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]

    def _people(self, db, participant_ids: list[str]) -> list[dict]:
        if not participant_ids or len(participant_ids) != len(set(participant_ids)):
            raise ValueError("unique participant IDs required")
        people = []
        for person_id in participant_ids:
            row = db.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()
            if row is None:
                raise ValueError(f"unknown participant: {person_id}")
            people.append(dict(row))
        return people

    def _available(self, db, participant_ids: list[str], start: datetime, end: datetime) -> bool:
        for person in self._people(db, participant_ids):
            zone = ZoneInfo(person["timezone"])
            local_start, local_end = start.astimezone(zone), end.astimezone(zone)
            if local_start.date() != local_end.date() or local_start.weekday() >= 5:
                return False
            start_clock=timedelta(hours=local_start.hour,minutes=local_start.minute,seconds=local_start.second,microseconds=local_start.microsecond)
            end_clock=timedelta(hours=local_end.hour,minutes=local_end.minute,seconds=local_end.second,microseconds=local_end.microsecond)
            if start_clock < timedelta(hours=person["work_start"]):
                return False
            if end_clock > timedelta(hours=person["work_end"]):
                return False
            for row in db.execute("SELECT start_utc,end_utc FROM busy WHERE person_id=?", (person["id"],)):
                if start < parse_utc(row["end_utc"]) and end > parse_utc(row["start_utc"]):
                    return False
        return True

    def list_slots(self, day: str, participant_ids: list[str], duration_minutes: int = 30) -> dict:
        requested_day = date.fromisoformat(day)
        if duration_minutes < 15 or duration_minutes > 120 or duration_minutes % 15:
            raise ValueError("duration must be 15-120 minutes in 15-minute steps")
        with self._db() as db:
            self._people(db, participant_ids)
            start = datetime.combine(requested_day, datetime.min.time(), timezone.utc)
            slots = []
            for offset in range(0, 24 * 60, 30):
                candidate = start + timedelta(minutes=offset)
                end = candidate + timedelta(minutes=duration_minutes)
                if self._available(db, participant_ids, candidate, end):
                    slots.append(iso(candidate))
            return {"revision": self._revision(db), "slots_utc": slots,
                    "participant_ids": participant_ids, "duration_minutes": duration_minutes}

    def book(self, request_id: str, start_utc: str, duration_minutes: int,
             participant_ids: list[str], expected_revision: int) -> dict:
        if not request_id.strip():
            raise ValueError("request_id required")
        if duration_minutes < 15 or duration_minutes > 120 or duration_minutes % 15:
            raise ValueError("invalid duration")
        start = parse_utc(start_utc)
        if start.minute % 30 or start.second or start.microsecond:
            raise ValueError("start must be on the listed half-hour UTC grid")
        end = start + timedelta(minutes=duration_minutes)
        payload = json.dumps({"start_utc": iso(start), "duration_minutes": duration_minutes,
                              "participant_ids": sorted(participant_ids)}, sort_keys=True)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT * FROM meetings WHERE request_id=?", (request_id,)).fetchone()
            if prior:
                if prior["payload_json"] != payload:
                    raise ValueError("request_id reused for a different meeting")
                return dict(prior)
            if self._revision(db) != expected_revision:
                raise ValueError("calendar revision changed; list slots again")
            if not self._available(db, participant_ids, start, end):
                raise ValueError("slot unavailable; list slots again")
            event_id = str(uuid.uuid4())
            db.execute("INSERT INTO meetings VALUES(?,?,?,?,?,?)", (
                event_id, request_id, payload, iso(start), iso(end), json.dumps(sorted(participant_ids)),
            ))
            for person_id in participant_ids:
                db.execute("INSERT INTO busy VALUES(?,?,?,?)", (person_id, iso(start), iso(end), event_id))
            db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")
            return dict(db.execute("SELECT * FROM meetings WHERE event_id=?", (event_id,)).fetchone())

    def add_busy(self, person_id: str, start_utc: str, end_utc: str) -> None:
        start, end = parse_utc(start_utc), parse_utc(end_utc)
        if end <= start:
            raise ValueError("busy end must follow start")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._people(db, [person_id])
            db.execute("INSERT INTO busy VALUES(?,?,?,NULL)", (person_id, iso(start), iso(end)))
            db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")

    def find_request(self, request_id: str) -> dict | None:
        with self._db() as db:
            event=db.execute("SELECT * FROM meetings WHERE request_id=?",(request_id,)).fetchone()
            return dict(event) if event else None

    def confirm(self, event_id: str, *, request_id: str | None = None) -> dict:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            event = db.execute("SELECT * FROM meetings WHERE event_id=?", (event_id,)).fetchone()
            if event is None:
                raise ValueError("unknown meeting; cannot send confirmation")
            if request_id is not None and event["request_id"] != request_id:
                raise ValueError("meeting belongs to another request; cannot confirm it")
            prior = db.execute("SELECT * FROM outbox WHERE event_id=?", (event_id,)).fetchone()
            if prior:
                return dict(prior)
            ids = json.loads(event["participant_ids"])
            recipients = [person["email"] for person in self._people(db, ids)]
            message = f"Meeting confirmed for {event['start_utc']} to {event['end_utc']} UTC."
            db.execute("INSERT INTO outbox VALUES(?,?,?)", (event_id, json.dumps(recipients), message))
            return dict(db.execute("SELECT * FROM outbox WHERE event_id=?", (event_id,)).fetchone())

    def snapshot(self) -> dict:
        with self._db() as db:
            return {
                "revision": self._revision(db),
                "people": [dict(row) for row in db.execute("SELECT * FROM people ORDER BY id")],
                "meetings": [dict(row) for row in db.execute("SELECT * FROM meetings ORDER BY start_utc")],
                "outbox": [dict(row) for row in db.execute("SELECT * FROM outbox ORDER BY event_id")],
            }
