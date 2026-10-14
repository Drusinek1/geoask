import pytest
from fastapi import HTTPException

from app import Budget


def test_per_ip_limit():
    b = Budget(per_ip_per_hour=2, per_day=100)
    b.check("1.1.1.1")
    b.check("1.1.1.1")
    with pytest.raises(HTTPException) as e:
        b.check("1.1.1.1")
    assert e.value.status_code == 429
    b.check("2.2.2.2")  # a different visitor is unaffected


def test_daily_limit():
    b = Budget(per_ip_per_hour=100, per_day=3)
    for ip in ("a", "b", "c"):
        b.check(ip)
    with pytest.raises(HTTPException):
        b.check("d")


def test_window_expires(monkeypatch):
    import app as app_mod
    b = Budget(per_ip_per_hour=1, per_day=100)
    t = [1_000_000.0]
    monkeypatch.setattr(app_mod.time, "time", lambda: t[0])
    b.check("x")
    t[0] += 3601
    b.check("x")  # old entry aged out
