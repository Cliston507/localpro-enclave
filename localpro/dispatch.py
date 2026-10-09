"""Core dispatch logic for LocalPro Enclave.

Kept independent of MCP so it can be unit-tested directly. The MCP layer in
``server.py`` is a thin wrapper over the ``Dispatcher`` methods.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from importlib import resources
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .tokens import Signer, TokenError

TZ = ZoneInfo("America/Los_Angeles")
WINDOW_HOURS = 2
QUOTE_TTL = timedelta(minutes=30)
TOKEN_GRACE = timedelta(hours=4)  # token stays valid this long after the window ends
SEARCH_DAYS = 7

TRADE_ALIASES = {
    "plumbing": "plumbing", "plumber": "plumbing", "pipe": "plumbing", "leak": "plumbing",
    "drain": "plumbing", "water heater": "plumbing",
    "electrical": "electrical", "electrician": "electrical", "electric": "electrical", "wiring": "electrical",
    "roofing": "roofing", "roofer": "roofing", "roof": "roofing",
    "hvac": "hvac", "heating": "hvac", "furnace": "hvac", "ac": "hvac", "air conditioning": "hvac",
    "heat pump": "hvac",
    "handyman": "handyman", "general repair": "handyman", "repair": "handyman",
}

# (call-out fee, low estimate, high estimate) in USD for a standard visit.
PRICING = {
    "plumbing": (89, 150, 450),
    "electrical": (99, 175, 500),
    "roofing": (129, 300, 1200),
    "hvac": (99, 200, 650),
    "handyman": (69, 100, 300),
}
EMERGENCY_MULTIPLIER = 1.5

URGENCY_VALUES = ("emergency", "standard")


class DispatchError(ValueError):
    """A user-facing error: bad input or an unavailable resource."""


@dataclass(frozen=True)
class Pro:
    id: str
    name: str
    trade: str
    cities: tuple[str, ...]
    rating: float
    reviews: int
    licensed: bool
    license_no: str | None
    emergency_24_7: bool
    hours: tuple[int, int]

    def public(self) -> dict[str, Any]:
        return {
            "pro_id": self.id,
            "name": self.name,
            "trade": self.trade,
            "rating": self.rating,
            "reviews": self.reviews,
            "license_no": self.license_no,
            "emergency_24_7": self.emergency_24_7,
            "service_area": list(self.cities),
        }


def load_pros(path: str | None = None) -> list[Pro]:
    if path:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    else:
        raw = json.loads(resources.files("localpro").joinpath("data/pros.json").read_text(encoding="utf-8"))
    return [
        Pro(
            id=p["id"], name=p["name"], trade=p["trade"], cities=tuple(c.lower() for c in p["cities"]),
            rating=float(p["rating"]), reviews=int(p["reviews"]), licensed=bool(p["licensed"]),
            license_no=p.get("license_no"), emergency_24_7=bool(p["emergency_24_7"]),
            hours=(int(p["hours"][0]), int(p["hours"][1])),
        )
        for p in raw["pros"]
    ]


def normalize_trade(trade: str) -> str:
    key = (trade or "").strip().lower()
    if key in TRADE_ALIASES:
        return TRADE_ALIASES[key]
    for alias, canonical in TRADE_ALIASES.items():
        if alias in key:
            return canonical
    raise DispatchError(f"Unknown trade '{trade}'. Supported trades: {', '.join(sorted(PRICING))}.")


def normalize_city(city: str) -> str:
    text = (city or "").strip().lower()
    for suffix in (", oregon", ", or", " oregon", " or"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    if not text:
        raise DispatchError("A city is required, for example 'Lincoln City'.")
    return text


def normalize_urgency(urgency: str) -> str:
    value = (urgency or "standard").strip().lower()
    if value in ("urgent", "asap", "now", "today", "emergency"):
        return "emergency"
    if value not in URGENCY_VALUES:
        raise DispatchError("Urgency must be 'emergency' or 'standard'.")
    return value


def describe_window(start: datetime, end: datetime, now: datetime) -> str:
    day = start.date()
    if day == now.date():
        label = "Today"
    elif day == (now + timedelta(days=1)).date():
        label = "Tomorrow"
    else:
        label = start.strftime("%A, %b %d").replace(" 0", " ")

    def fmt(t: datetime) -> str:
        return t.strftime("%I:%M %p").lstrip("0")

    return f"{label} {fmt(start)} to {fmt(end)}"


class Dispatcher:
    def __init__(
        self,
        db_path: str,
        signer: Signer,
        pros: list[Pro] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.signer = signer
        self.pros = {p.id: p for p in (pros if pros is not None else load_pros())}
        self._clock = clock or (lambda: datetime.now(TZ))
        self._lock = threading.Lock()
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS quotes (
                quote_id TEXT PRIMARY KEY, pro_id TEXT NOT NULL, trade TEXT NOT NULL,
                urgency TEXT NOT NULL, issue TEXT NOT NULL,
                window_start TEXT NOT NULL, window_end TEXT NOT NULL,
                call_fee INTEGER NOT NULL, low INTEGER NOT NULL, high INTEGER NOT NULL,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS work_orders (
                work_order_id TEXT PRIMARY KEY, quote_id TEXT NOT NULL UNIQUE, pro_id TEXT NOT NULL,
                trade TEXT NOT NULL, urgency TEXT NOT NULL, issue TEXT NOT NULL,
                customer_name TEXT NOT NULL, service_address TEXT NOT NULL,
                window_start TEXT NOT NULL, window_end TEXT NOT NULL,
                status TEXT NOT NULL, nonce TEXT NOT NULL,
                created_at TEXT NOT NULL, checked_in_at TEXT
            );
            """
        )
        self._db.commit()

    # ---------- helpers ----------

    def now(self) -> datetime:
        return self._clock().astimezone(TZ)

    def _pro(self, pro_id: str) -> Pro:
        pro = self.pros.get((pro_id or "").strip())
        if pro is None:
            raise DispatchError(f"No contractor with id '{pro_id}'.")
        return pro

    def _busy_windows(self, pro_id: str) -> list[tuple[datetime, datetime]]:
        rows = self._db.execute(
            "SELECT window_start, window_end FROM work_orders WHERE pro_id = ? AND status IN ('booked', 'checked_in')",
            (pro_id,),
        ).fetchall()
        return [(datetime.fromisoformat(r["window_start"]), datetime.fromisoformat(r["window_end"])) for r in rows]

    def _next_window(self, pro: Pro, urgency: str) -> tuple[datetime, datetime] | None:
        now = self.now()
        # Emergencies: earliest arrival about an hour out. Standard: at least 2 hours' notice.
        lead = timedelta(hours=1) if urgency == "emergency" else timedelta(hours=2)
        start = (now + lead).replace(minute=0, second=0, microsecond=0)
        if start < now + lead:
            start += timedelta(hours=1)
        busy = self._busy_windows(pro.id)
        limit = now + timedelta(days=SEARCH_DAYS)
        while start < limit:
            end = start + timedelta(hours=WINDOW_HOURS)
            after_hours_ok = urgency == "emergency" and pro.emergency_24_7
            in_hours = start.hour >= pro.hours[0] and (end.hour <= pro.hours[1] or pro.hours[1] == 24) and end.date() == start.date()
            if (in_hours or after_hours_ok) and not any(s < end and start < e for s, e in busy):
                return start, end
            start += timedelta(hours=1)
        return None

    # ---------- tools ----------

    def find_pros(self, trade: str, city: str, urgency: str = "standard", limit: int = 3) -> dict[str, Any]:
        trade_n = normalize_trade(trade)
        city_n = normalize_city(city)
        urgency_n = normalize_urgency(urgency)
        limit = max(1, min(int(limit), 5))
        now = self.now()
        candidates = [p for p in self.pros.values() if p.trade == trade_n and city_n in p.cities]
        excluded_unlicensed = sum(1 for p in candidates if not p.licensed)
        results = []
        for pro in sorted((p for p in candidates if p.licensed), key=lambda p: (-p.rating, -p.reviews)):
            window = self._next_window(pro, urgency_n)
            if window is None:
                continue
            info = pro.public()
            info["next_arrival_window"] = describe_window(window[0], window[1], now)
            info["window_start"] = window[0].isoformat()
            results.append(info)
        if urgency_n == "emergency":
            # For emergencies, the soonest arrival matters more than rating.
            results.sort(key=lambda r: r["window_start"])
        results = results[:limit]
        message = (
            f"Found {len(results)} licensed {trade_n} pro(s) serving {city_n.title()}."
            if results
            else f"No licensed {trade_n} pros are available in {city_n.title()} in the next {SEARCH_DAYS} days."
        )
        return {
            "trade": trade_n,
            "city": city_n.title(),
            "urgency": urgency_n,
            "pros": results,
            "excluded_unlicensed": excluded_unlicensed,
            "message": message,
        }

    def quote_job(self, pro_id: str, issue: str, urgency: str = "standard") -> dict[str, Any]:
        pro = self._pro(pro_id)
        urgency_n = normalize_urgency(urgency)
        issue = (issue or "").strip()
        if not issue:
            raise DispatchError("Describe the problem, for example 'water heater leaking'.")
        if not pro.licensed:
            raise DispatchError(f"{pro.name} is not license-verified, so LocalPro will not quote or book them.")
        with self._lock:
            window = self._next_window(pro, urgency_n)
            if window is None:
                raise DispatchError(f"{pro.name} has no open arrival window in the next {SEARCH_DAYS} days.")
            fee, low, high = PRICING[pro.trade]
            if urgency_n == "emergency":
                fee, low, high = (round(x * EMERGENCY_MULTIPLIER) for x in (fee, low, high))
            now = self.now()
            quote_id = "Q-" + secrets.token_hex(4).upper()
            expires = now + QUOTE_TTL
            self._db.execute(
                "INSERT INTO quotes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                (quote_id, pro.id, pro.trade, urgency_n, issue[:500], window[0].isoformat(), window[1].isoformat(),
                 fee, low, high, now.isoformat(), expires.isoformat()),
            )
            self._db.commit()
        return {
            "quote_id": quote_id,
            "pro_id": pro.id,
            "pro_name": pro.name,
            "trade": pro.trade,
            "urgency": urgency_n,
            "issue": issue[:500],
            "arrival_window": describe_window(window[0], window[1], now),
            "window_start": window[0].isoformat(),
            "window_end": window[1].isoformat(),
            "call_out_fee_usd": fee,
            "estimate_range_usd": [low, high],
            "estimate_text": f"${low} to ${high}, plus a ${fee} call-out fee",
            "expires_at": expires.isoformat(),
            "message": f"{pro.name} can arrive {describe_window(window[0], window[1], now)}. "
                       f"Estimated ${low} to ${high} plus a ${fee} call-out fee. This quote holds for 30 minutes.",
        }

    def book_job(self, quote_id: str, customer_name: str, service_address: str) -> dict[str, Any]:
        customer_name = (customer_name or "").strip()
        service_address = (service_address or "").strip()
        if not customer_name or not service_address:
            raise DispatchError("A customer name and service address are required to book.")
        with self._lock:
            row = self._db.execute("SELECT * FROM quotes WHERE quote_id = ?", ((quote_id or "").strip(),)).fetchone()
            if row is None:
                raise DispatchError(f"No quote with id '{quote_id}'. Ask for a new quote first.")
            now = self.now()
            if row["used"]:
                raise DispatchError("This quote was already used to book a job.")
            if datetime.fromisoformat(row["expires_at"]) < now:
                raise DispatchError("This quote has expired. Ask for a new quote.")
            start = datetime.fromisoformat(row["window_start"])
            end = datetime.fromisoformat(row["window_end"])
            if any(s < end and start < e for s, e in self._busy_windows(row["pro_id"])):
                raise DispatchError("That arrival window was just taken. Ask for a new quote.")
            pro = self._pro(row["pro_id"])
            wo_id = "WO-" + secrets.token_hex(4).upper()
            nonce = secrets.token_urlsafe(9)
            self._db.execute(
                "INSERT INTO work_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'booked', ?, ?, NULL)",
                (wo_id, row["quote_id"], pro.id, row["trade"], row["urgency"], row["issue"], customer_name[:120],
                 service_address[:240], row["window_start"], row["window_end"], nonce, now.isoformat()),
            )
            self._db.execute("UPDATE quotes SET used = 1 WHERE quote_id = ?", (row["quote_id"],))
            self._db.commit()
        exp = end + TOKEN_GRACE
        token = self.signer.sign({"wo": wo_id, "pro": pro.id, "exp": int(exp.timestamp()), "n": nonce})
        return {
            "work_order_id": wo_id,
            "status": "booked",
            "pro_id": pro.id,
            "pro_name": pro.name,
            "arrival_window": describe_window(start, end, now),
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "authorization_token": token,
            "token_expires_at": exp.isoformat(),
            "message": f"Booked. {pro.name} will arrive {describe_window(start, end, now)}. "
                       f"Work order {wo_id}. The technician must present the signed authorization to check in.",
        }

    def verify_work_order(self, token: str, pro_id: str, check_in: bool = False) -> dict[str, Any]:
        def fail(reason: str) -> dict[str, Any]:
            return {"verified": False, "reason": reason, "checked_in": False}

        try:
            payload = self.signer.verify(token)
        except TokenError as exc:
            return fail(str(exc))
        now = self.now()
        if int(payload.get("exp", 0)) < int(now.timestamp()):
            return fail("Token has expired.")
        if payload.get("pro") != (pro_id or "").strip():
            return fail("This work order was issued to a different contractor.")
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM work_orders WHERE work_order_id = ?", (payload.get("wo"),)
            ).fetchone()
            if row is None or row["nonce"] != payload.get("n"):
                return fail("Work order not found or token was replaced.")
            if row["status"] == "cancelled":
                return fail("Work order was cancelled.")
            if row["status"] == "checked_in" and check_in:
                return fail(f"Work order was already checked in at {row['checked_in_at']}. Tokens are single-use.")
            checked_in = False
            if check_in and row["status"] == "booked":
                self._db.execute(
                    "UPDATE work_orders SET status = 'checked_in', checked_in_at = ? WHERE work_order_id = ?",
                    (now.isoformat(), row["work_order_id"]),
                )
                self._db.commit()
                checked_in = True
            status = "checked_in" if checked_in else row["status"]
        pro = self._pro(row["pro_id"])
        start = datetime.fromisoformat(row["window_start"])
        end = datetime.fromisoformat(row["window_end"])
        return {
            "verified": True,
            "reason": "Signature valid, contractor matches, and the work order is active.",
            "checked_in": checked_in,
            "work_order": {
                "work_order_id": row["work_order_id"],
                "status": status,
                "pro_name": pro.name,
                "trade": row["trade"],
                "urgency": row["urgency"],
                "issue": row["issue"],
                "customer_name": row["customer_name"],
                "service_address": row["service_address"],
                "arrival_window": describe_window(start, end, now),
            },
        }
