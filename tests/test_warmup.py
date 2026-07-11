from datetime import date, timedelta

from recruiter_outreach.db import Database
from recruiter_outreach.delivery.warmup import META_KEY, WarmupScheduler


def test_first_call_sets_start_date_and_returns_start_cap(db: Database):
    ws = WarmupScheduler(db, start_cap=20, daily_increment=15, ceiling=100, warmup_days=14)
    assert ws.today_cap() == 20
    assert db.get_meta(META_KEY) == date.today().isoformat()


def test_ramps_linearly_with_days_elapsed(db: Database):
    start = date.today() - timedelta(days=3)  # day 4
    db.set_meta(META_KEY, start.isoformat())
    ws = WarmupScheduler(db, start_cap=20, daily_increment=15, ceiling=100, warmup_days=14)
    assert ws.today_cap() == 20 + 15 * 3


def test_caps_at_ceiling(db: Database):
    start = date.today() - timedelta(days=3)
    db.set_meta(META_KEY, start.isoformat())
    ws = WarmupScheduler(db, start_cap=20, daily_increment=15, ceiling=50, warmup_days=14)
    assert ws.today_cap() == 50


def test_reaches_ceiling_after_warmup_days(db: Database):
    start = date.today() - timedelta(days=30)
    db.set_meta(META_KEY, start.isoformat())
    ws = WarmupScheduler(db, start_cap=20, daily_increment=15, ceiling=200, warmup_days=14)
    assert ws.today_cap() == 200
