"""Shared helpers for the tests."""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as appmod  # noqa: E402,F401


def mk(c, name, **kw):
    r = c.post("/api/meals", json={"name": name, **kw})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def day(offset=0, base=date(2030, 1, 7)):  # 2030-01-07 is a Monday
    return (base + timedelta(offset)).isoformat()


def plan_of(c, start, end):
    return {r["day"]: r for r in c.get(f"/api/plan?start={start}&end={end}").get_json()}
