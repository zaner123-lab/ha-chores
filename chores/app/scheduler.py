"""Daily scheduler.

At RESET_TIME each day (e.g. 04:00), close out yesterday by recording any
allowance payouts that were earned. This keeps the payout history accurate
even if HA was offline at midnight.
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import db, mqtt_client

log = logging.getLogger(__name__)

RESET_TIME = os.environ.get("RESET_TIME", "04:00")  # HH:MM


def close_out_yesterday() -> None:
    """Record allowance payouts for kids who hit their threshold yesterday."""
    yesterday = date.today() - timedelta(days=1)
    for user in db.list_users():
        if not user["is_kid"]:
            continue
        if db.has_payout(user["id"], yesterday):
            continue
        effort = db.user_effort_today(user["id"], yesterday)
        if effort >= user["allowance_threshold"] and user["allowance_amount"] > 0:
            db.record_payout(user["id"], yesterday, user["allowance_amount"], effort)
            log.info(
                "Recorded payout: %s earned %s for %d effort on %s",
                user["name"], user["allowance_amount"], effort, yesterday,
            )
    mqtt_client.publish_all_state()


def start() -> None:
    hour, minute = (int(p) for p in RESET_TIME.split(":"))
    scheduler = BackgroundScheduler()
    scheduler.add_job(close_out_yesterday, CronTrigger(hour=hour, minute=minute))
    # Also refresh state every 5 minutes so HA entities don't go stale
    scheduler.add_job(mqtt_client.publish_all_state, "interval", minutes=5)
    scheduler.start()
    log.info("Scheduler started (daily reset at %s)", RESET_TIME)
