from datetime import datetime, timedelta

import pytest

from localpro.dispatch import TZ, Dispatcher, DispatchError, normalize_trade
from localpro.tokens import Signer

SECRET = b"x" * 32


class Clock:
    def __init__(self, start: datetime):
        self.t = start

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


@pytest.fixture
def clock():
    # Friday Oct 9, 2026, 10:15 AM Pacific
    return Clock(datetime(2026, 10, 9, 10, 15, tzinfo=TZ))


@pytest.fixture
def d(tmp_path, clock):
    return Dispatcher(db_path=str(tmp_path / "t.db"), signer=Signer(SECRET), clock=clock)


def book(d, pro_id="pro-101", urgency="emergency"):
    q = d.quote_job(pro_id, "water heater leaking", urgency)
    return q, d.book_job(q["quote_id"], "Test Customer", "123 Main St, Lincoln City, OR")


def test_trade_aliases():
    assert normalize_trade("Plumber") == "plumbing"
    assert normalize_trade("my furnace is out") == "hvac"
    with pytest.raises(DispatchError):
        normalize_trade("astrology")


def test_find_pros_excludes_unlicensed_and_filters_city(d):
    r = d.find_pros("plumbing", "Lincoln City, OR", "emergency", limit=5)
    ids = [p["pro_id"] for p in r["pros"]]
    assert "pro-502" not in ids  # unlicensed
    assert r["excluded_unlicensed"] == 1
    assert set(ids) == {"pro-101", "pro-102"}
    assert all(p["next_arrival_window"] for p in r["pros"])


def test_find_pros_no_match(d):
    r = d.find_pros("roofing", "Waldport")
    assert r["pros"] == [] and "No licensed" in r["message"]


def test_emergency_quote_costs_more_and_arrives_sooner(d):
    std = d.quote_job("pro-101", "slow drain", "standard")
    emg = d.quote_job("pro-101", "burst pipe", "emergency")
    assert emg["call_out_fee_usd"] > std["call_out_fee_usd"]
    assert emg["estimate_range_usd"][1] > std["estimate_range_usd"][1]
    assert emg["window_start"] < std["window_start"]


def test_after_hours_only_for_24_7_pros(d, clock):
    clock.t = datetime(2026, 10, 9, 22, 30, tzinfo=TZ)  # 10:30 PM
    emg = d.quote_job("pro-101", "flooding", "emergency")  # 24/7 pro
    assert datetime.fromisoformat(emg["window_start"]).hour == 23 or datetime.fromisoformat(emg["window_start"]).hour == 0
    std = d.quote_job("pro-102", "flooding", "emergency")  # business hours only
    assert datetime.fromisoformat(std["window_start"]).hour >= 8


def test_book_and_verify_flow(d):
    q, b = book(d)
    assert b["status"] == "booked" and b["work_order_id"].startswith("WO-")
    v = d.verify_work_order(b["authorization_token"], "pro-101")
    assert v["verified"] and not v["checked_in"]
    v2 = d.verify_work_order(b["authorization_token"], "pro-101", check_in=True)
    assert v2["verified"] and v2["checked_in"] and v2["work_order"]["status"] == "checked_in"


def test_token_single_use(d):
    _, b = book(d)
    assert d.verify_work_order(b["authorization_token"], "pro-101", check_in=True)["verified"]
    again = d.verify_work_order(b["authorization_token"], "pro-101", check_in=True)
    assert not again["verified"] and "single-use" in again["reason"]


def test_wrong_contractor_rejected(d):
    _, b = book(d)
    v = d.verify_work_order(b["authorization_token"], "pro-102")
    assert not v["verified"] and "different contractor" in v["reason"]


def test_tampered_token_rejected(d):
    _, b = book(d)
    payload, sig = b["authorization_token"].split(".")
    forged = Signer(b"y" * 32).sign({"wo": b["work_order_id"], "pro": "pro-102", "exp": 9999999999, "n": "x"})
    assert not d.verify_work_order(forged, "pro-102")["verified"]
    flipped = payload[:-1] + ("A" if payload[-1] != "A" else "B") + "." + sig
    assert not d.verify_work_order(flipped, "pro-101")["verified"]
    assert not d.verify_work_order("garbage", "pro-101")["verified"]


def test_expired_token_rejected(d, clock):
    _, b = book(d)
    clock.advance(days=2)
    v = d.verify_work_order(b["authorization_token"], "pro-101")
    assert not v["verified"] and "expired" in v["reason"]


def test_quote_expires_and_is_single_use(d, clock):
    q = d.quote_job("pro-201", "outlet sparking", "emergency")
    d.book_job(q["quote_id"], "A", "1 St")
    with pytest.raises(DispatchError, match="already used"):
        d.book_job(q["quote_id"], "A", "1 St")
    q2 = d.quote_job("pro-201", "outlet sparking", "emergency")
    clock.advance(minutes=31)
    with pytest.raises(DispatchError, match="expired"):
        d.book_job(q2["quote_id"], "A", "1 St")


def test_no_double_booking(d):
    q1 = d.quote_job("pro-301", "roof leak", "emergency")
    q2 = d.quote_job("pro-301", "roof leak", "emergency")
    assert q1["window_start"] == q2["window_start"]
    d.book_job(q1["quote_id"], "A", "1 St")
    with pytest.raises(DispatchError, match="just taken"):
        d.book_job(q2["quote_id"], "B", "2 St")
    q3 = d.quote_job("pro-301", "roof leak", "emergency")
    assert q3["window_start"] > q1["window_start"]


def test_unlicensed_cannot_be_quoted(d):
    with pytest.raises(DispatchError, match="not license-verified"):
        d.quote_job("pro-502", "leak", "emergency")


def test_booking_requires_details(d):
    q = d.quote_job("pro-101", "leak", "standard")
    with pytest.raises(DispatchError):
        d.book_job(q["quote_id"], "", "")
