"""FastAPI application for the Household Chores add-on."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, mqtt_client, scheduler

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper())
log = logging.getLogger(__name__)

CURRENCY = os.environ.get("CURRENCY_SYMBOL", "$")
BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    mqtt_client.start()
    scheduler.start()
    yield
    mqtt_client.stop()


app = FastAPI(title="Household Chores", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


# ---------- Helpers for templates ----------

def _build_today_view(on_date: date) -> dict:
    """Return data structure rendered by index.html."""
    users = [dict(u) for u in db.list_users()]
    chores = db.list_chores()
    user_by_id = {u["id"]: u for u in users}

    open_chores = []
    chores_by_user: dict[int, list] = {u["id"]: [] for u in users}

    for c in chores:
        if not db.chore_is_due(c, on_date):
            continue
        if c["assignment_type"] == "open":
            done_by = db.is_open_chore_complete(c["id"], on_date)
            open_chores.append({
                "chore": c,
                "done_by_user_id": done_by,
                "done_by_name": user_by_id[done_by]["name"] if done_by in user_by_id else None,
            })
        else:  # specific
            for uid in c["assigned_user_ids"]:
                if uid not in chores_by_user:
                    continue
                done = db.is_chore_complete_for_user(c["id"], uid, on_date)
                chores_by_user[uid].append({"chore": c, "done": done})

    # Stats per user
    stats = {}
    for u in users:
        effort = db.user_effort_today(u["id"], on_date)
        reward = db.user_reward_today(u["id"], on_date)
        stats[u["id"]] = {
            "effort": effort,
            "reward": reward,
            "threshold_met": u["is_kid"] and effort >= u["allowance_threshold"],
        }

    return {
        "users": users,
        "open_chores": open_chores,
        "chores_by_user": chores_by_user,
        "stats": stats,
        "on_date": on_date,
        "currency": CURRENCY,
    }


# ---------- Pages ----------

@app.get("/", response_class=HTMLResponse)
def index(request: Request, d: Optional[str] = None):
    on_date = date.fromisoformat(d) if d else date.today()
    ctx = _build_today_view(on_date)
    ctx.update({
        "active_tab": "today",
        "prev_date": (on_date - timedelta(days=1)).isoformat(),
        "next_date": (on_date + timedelta(days=1)).isoformat(),
        "is_today": on_date == date.today(),
    })
    return templates.TemplateResponse(request, "index.html", ctx)


@app.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    return templates.TemplateResponse(request, "admin.html", {
        "active_tab": "admin",
        "users": [dict(u) for u in db.list_users()],
        "chores": db.list_chores(),
        "currency": CURRENCY,
    })


# ---------- Completion actions ----------

@app.post("/complete")
def complete_chore(
    chore_id: int = Form(...),
    user_id: int = Form(...),
    due_date: str = Form(...),
):
    d = date.fromisoformat(due_date)
    chore = db.get_chore(chore_id)
    if not chore:
        raise HTTPException(404, "chore not found")

    # Validation: open chores anyone can claim; specific chores only assigned users
    if chore["assignment_type"] == "specific" and user_id not in chore["assigned_user_ids"]:
        raise HTTPException(400, "user not assigned to this chore")

    # Open chores: only one user gets credit per due_date
    if chore["assignment_type"] == "open":
        existing = db.is_open_chore_complete(chore_id, d)
        if existing is not None and existing != user_id:
            raise HTTPException(409, "already completed by another user")

    db.mark_complete(chore_id, user_id, d)
    mqtt_client.publish_all_state(d)
    return RedirectResponse(f"/?d={due_date}", status_code=303)


@app.post("/uncomplete")
def uncomplete_chore(
    chore_id: int = Form(...),
    user_id: int = Form(...),
    due_date: str = Form(...),
):
    d = date.fromisoformat(due_date)
    db.unmark_complete(chore_id, user_id, d)
    mqtt_client.publish_all_state(d)
    return RedirectResponse(f"/?d={due_date}", status_code=303)


# ---------- User CRUD (admin) ----------

@app.post("/admin/users")
def admin_create_user(
    name: str = Form(...),
    is_kid: Optional[str] = Form(None),
    color: str = Form("#7c9cff"),
    allowance_amount: float = Form(0),
    allowance_threshold: int = Form(10),
):
    db.create_user(
        name=name.strip(),
        is_kid=bool(is_kid),
        color=color,
        allowance_amount=allowance_amount,
        allowance_threshold=allowance_threshold,
    )
    mqtt_client.publish_discovery()
    mqtt_client.publish_all_state()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/users/{user_id}")
def admin_update_user(
    user_id: int,
    name: str = Form(...),
    is_kid: Optional[str] = Form(None),
    color: str = Form("#7c9cff"),
    allowance_amount: float = Form(0),
    allowance_threshold: int = Form(10),
):
    db.update_user(
        user_id=user_id,
        name=name.strip(),
        is_kid=bool(is_kid),
        color=color,
        allowance_amount=allowance_amount,
        allowance_threshold=allowance_threshold,
    )
    mqtt_client.publish_discovery()
    mqtt_client.publish_all_state()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/users/{user_id}/delete")
def admin_delete_user(user_id: int):
    db.delete_user(user_id)
    mqtt_client.publish_all_state()
    return RedirectResponse("/admin", status_code=303)


# ---------- Chore CRUD (admin) ----------

@app.post("/admin/chores")
async def admin_create_chore(request: Request):
    return await _save_chore(request, chore_id=None)


@app.post("/admin/chores/{chore_id}")
async def admin_update_chore(chore_id: int, request: Request):
    return await _save_chore(request, chore_id=chore_id)


@app.post("/admin/chores/{chore_id}/delete")
def admin_delete_chore(chore_id: int):
    db.delete_chore(chore_id)
    mqtt_client.publish_all_state()
    return RedirectResponse("/admin", status_code=303)


async def _save_chore(request: Request, chore_id: Optional[int]):
    form = await request.form()
    name = str(form.get("name", "")).strip()
    description = str(form.get("description", ""))
    effort = int(form.get("effort") or 1)
    reward_amount = float(form.get("reward_amount") or 0)
    frequency_type = str(form.get("frequency_type") or "daily")
    assignment_type = str(form.get("assignment_type") or "open")
    active = bool(form.get("active"))

    # Frequency data depends on type
    if frequency_type == "weekly":
        days = form.getlist("weekdays") if hasattr(form, "getlist") else form.getall("weekdays")
        frequency_data = ",".join(str(d) for d in days)
    elif frequency_type == "monthly":
        frequency_data = str(form.get("month_day") or "1")
    elif frequency_type == "interval":
        frequency_data = str(form.get("interval_days") or "1")
    else:
        frequency_data = ""

    # Assigned users (only meaningful for "specific")
    assigned_user_ids: list[int] = []
    if assignment_type == "specific":
        raw = form.getlist("assigned_user_ids") if hasattr(form, "getlist") else form.getall("assigned_user_ids")
        assigned_user_ids = [int(x) for x in raw]

    if not name:
        raise HTTPException(400, "name is required")

    db.upsert_chore(
        chore_id=chore_id,
        name=name,
        description=description,
        effort=effort,
        reward_amount=reward_amount,
        frequency_type=frequency_type,
        frequency_data=frequency_data,
        assignment_type=assignment_type,
        active=active,
        assigned_user_ids=assigned_user_ids,
    )
    mqtt_client.publish_all_state()
    return RedirectResponse("/admin", status_code=303)


# ---------- JSON API (for HA scripts/automations) ----------

@app.get("/api/state")
def api_state():
    today = date.today()
    return {
        "date": today.isoformat(),
        "users": [dict(u) for u in db.list_users()],
        "chores": db.list_chores(),
        "completions": [dict(c) for c in db.completions_for_date(today)],
    }


@app.post("/api/complete")
def api_complete(payload: dict):
    chore_id = int(payload["chore_id"])
    user_id = int(payload["user_id"])
    d = date.fromisoformat(payload.get("due_date", date.today().isoformat()))
    new = db.mark_complete(chore_id, user_id, d)
    mqtt_client.publish_all_state(d)
    return JSONResponse({"ok": True, "newly_completed": new})
