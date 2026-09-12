"""Fire the GitHub Actions workflows on a fixed Pacific-time schedule from inside the web app.

GitHub's own cron dropped or delayed most of our runs, so the site (which is up around the clock on
Railway) triggers ``workflow_dispatch`` itself: the story batch at 19:00 America/Los_Angeles and the
newsletter at 20:00. Active only when GITHUB_DISPATCH_TOKEN is set (a fine-grained token with
Actions: read and write on the repo).

Each job claims one ``settings`` row per day (``dispatch:<job>:<YYYY-MM-DD>``) before calling GitHub,
so two gunicorn workers, or a restart, never dispatch the same job twice. A job that was missed while
the app was down is still fired if the app comes back within CATCH_UP hours of its target time.
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from sqlalchemy.exc import IntegrityError

from src.models import Setting, db

log = logging.getLogger(__name__)

CATCH_UP = timedelta(hours=3)
TICK_SECONDS = 60

JOBS = [
    {"name": "stories", "workflow": "content.yml", "setting": "SCHEDULE_STORIES",
     "inputs": {"count": "5", "status": "published", "guard": "true"}},
    {"name": "newsletter", "workflow": "newsletter.yml", "setting": "SCHEDULE_NEWSLETTER",
     "inputs": {"edition": "daily", "guard": "true"}},
]


def _target(job, config, now):
    hh, mm = (config.get(job["setting"]) or "00:00").split(":")
    return now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)


def due_jobs(config, now=None):
    """Jobs whose target time today has passed but not by more than CATCH_UP; `now` is tz-aware."""
    now = now or datetime.now(ZoneInfo(config["SCHEDULE_TZ"]))
    return [j for j in JOBS if _target(j, config, now) <= now < _target(j, config, now) + CATCH_UP]


def claim(job, day):
    """Atomically mark today's run of `job` as ours; False when another worker got there first."""
    key = f"dispatch:{job['name']}:{day}"
    db.session.add(Setting(key=key, value="claimed"))
    try:
        db.session.commit()
        return True
    except IntegrityError:
        db.session.rollback()
        return False


def release(job, day):
    Setting.query.filter_by(key=f"dispatch:{job['name']}:{day}").delete()
    db.session.commit()


def dispatch(job, config):
    url = f"https://api.github.com/repos/{config['GITHUB_REPO']}/actions/workflows/{job['workflow']}/dispatches"
    r = requests.post(url, json={"ref": config.get("GITHUB_REF", "main"), "inputs": job["inputs"]}, timeout=30,
                      headers={"Authorization": f"Bearer {config['GITHUB_DISPATCH_TOKEN']}",
                               "Accept": "application/vnd.github+json", "User-Agent": "patriot-daily-alerts"})
    return r.status_code


def tick(app, now=None):
    """One pass: dispatch every due job that nobody has dispatched today. Returns the names fired."""
    config = app.config
    fired = []
    with app.app_context():
        now = now or datetime.now(ZoneInfo(config["SCHEDULE_TZ"]))
        day = now.strftime("%Y-%m-%d")
        for job in due_jobs(config, now):
            if not claim(job, day):
                continue
            try:
                status = dispatch(job, config)
            except requests.RequestException as e:
                log.warning("scheduler: %s dispatch failed (%s); will retry", job["name"], e)
                release(job, day)
                continue
            if status == 204:
                db.session.get(Setting, f"dispatch:{job['name']}:{day}").value = f"dispatched {now.isoformat()}"
                db.session.commit()
                log.info("scheduler: dispatched %s (%s)", job["name"], job["workflow"])
                fired.append(job["name"])
            else:
                log.warning("scheduler: GitHub returned %s for %s; will retry", status, job["name"])
                release(job, day)
    return fired


def status(app):
    """Today's and yesterday's dispatch records, for the /api/scheduler endpoint."""
    with app.app_context():
        rows = Setting.query.filter(Setting.key.like("dispatch:%")).order_by(Setting.key.desc()).limit(10).all()
        now = datetime.now(ZoneInfo(app.config["SCHEDULE_TZ"]))
        return {
            "enabled": bool(app.config.get("GITHUB_DISPATCH_TOKEN")),
            "timezone": app.config["SCHEDULE_TZ"],
            "now": now.isoformat(timespec="seconds"),
            "jobs": {j["name"]: {"workflow": j["workflow"], "at": app.config.get(j["setting"]),
                                 "due_now": j in due_jobs(app.config, now)} for j in JOBS},
            "recent": {r.key: r.value for r in rows},
        }


def start(app):
    """Run `tick` every minute in a daemon thread (one per gunicorn worker; the claim row dedupes)."""
    if not app.config.get("GITHUB_DISPATCH_TOKEN") or app.config.get("TESTING"):
        return None

    def loop():
        time.sleep(15)  # let the worker finish booting
        while True:
            try:
                tick(app)
            except Exception:  # noqa: BLE001 - never let the scheduler thread die
                log.exception("scheduler tick failed")
            time.sleep(TICK_SECONDS)

    t = threading.Thread(target=loop, name="workflow-scheduler", daemon=True)
    t.start()
    log.info("scheduler: started (%s)", ", ".join(f"{j['name']} {app.config.get(j['setting'])}" for j in JOBS))
    return t
