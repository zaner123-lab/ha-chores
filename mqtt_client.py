"""MQTT client that publishes chore state to Home Assistant via discovery.

For each kid, publishes:
    sensor.chores_<user>_effort_today        - integer effort points today
    sensor.chores_<user>_reward_today        - currency reward earned today
    binary_sensor.chores_<user>_allowance    - on if today's threshold met
    sensor.chores_<user>_remaining           - count of due chores not yet done

Also publishes:
    sensor.chores_open_remaining             - count of open chores remaining

When state changes (after marking/unmarking a completion), call
`publish_all_state()` to refresh HA.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date
from typing import Optional

import paho.mqtt.client as mqtt

from . import db

log = logging.getLogger(__name__)

MQTT_HOST = os.environ.get("MQTT_HOST", "core-mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USER = os.environ.get("MQTT_USERNAME", "")
MQTT_PASS = os.environ.get("MQTT_PASSWORD", "")
DISCOVERY_PREFIX = os.environ.get("MQTT_DISCOVERY_PREFIX", "homeassistant")
STATE_PREFIX = os.environ.get("MQTT_STATE_PREFIX", "chores")
CURRENCY = os.environ.get("CURRENCY_SYMBOL", "$")

DEVICE_INFO = {
    "identifiers": ["ha_chores_addon"],
    "name": "Household Chores",
    "manufacturer": "ha-chores",
    "model": "Chore Tracker",
}

_client: Optional[mqtt.Client] = None
_lock = threading.Lock()


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")


def start() -> None:
    """Connect to MQTT and publish discovery configs for all current entities."""
    global _client
    if _client is not None:
        return
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ha-chores-addon")
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        client.loop_start()
        _client = client
        log.info("MQTT connected to %s:%s", MQTT_HOST, MQTT_PORT)
        publish_discovery()
        publish_all_state()
    except Exception as e:  # noqa: BLE001
        log.warning("MQTT connection failed: %s (continuing without HA entities)", e)
        _client = None


def stop() -> None:
    global _client
    if _client:
        _client.loop_stop()
        _client.disconnect()
        _client = None


def _publish(topic: str, payload, retain: bool = True) -> None:
    if _client is None:
        return
    if not isinstance(payload, str):
        payload = json.dumps(payload)
    with _lock:
        _client.publish(topic, payload, qos=1, retain=retain)


def publish_discovery() -> None:
    """Publish HA MQTT discovery configs for every kid + open-chores sensor."""
    if _client is None:
        return

    # Open chores remaining (global)
    _publish(
        f"{DISCOVERY_PREFIX}/sensor/{STATE_PREFIX}_open_remaining/config",
        {
            "name": "Open Chores Remaining",
            "unique_id": f"{STATE_PREFIX}_open_remaining",
            "state_topic": f"{STATE_PREFIX}/open_remaining",
            "icon": "mdi:format-list-checkbox",
            "device": DEVICE_INFO,
        },
    )

    for user in db.list_users():
        if not user["is_kid"]:
            continue
        slug = _slug(user["name"])
        base = f"{STATE_PREFIX}/{slug}"

        # Effort today
        _publish(
            f"{DISCOVERY_PREFIX}/sensor/{STATE_PREFIX}_{slug}_effort_today/config",
            {
                "name": f"Chores {user['name']} Effort Today",
                "unique_id": f"{STATE_PREFIX}_{slug}_effort_today",
                "state_topic": f"{base}/effort_today",
                "unit_of_measurement": "pts",
                "icon": "mdi:star-circle",
                "device": DEVICE_INFO,
            },
        )

        # Reward today
        _publish(
            f"{DISCOVERY_PREFIX}/sensor/{STATE_PREFIX}_{slug}_reward_today/config",
            {
                "name": f"Chores {user['name']} Reward Today",
                "unique_id": f"{STATE_PREFIX}_{slug}_reward_today",
                "state_topic": f"{base}/reward_today",
                "unit_of_measurement": CURRENCY,
                "icon": "mdi:cash",
                "device": DEVICE_INFO,
            },
        )

        # Allowance earned (binary sensor)
        _publish(
            f"{DISCOVERY_PREFIX}/binary_sensor/{STATE_PREFIX}_{slug}_allowance/config",
            {
                "name": f"Chores {user['name']} Allowance Earned",
                "unique_id": f"{STATE_PREFIX}_{slug}_allowance",
                "state_topic": f"{base}/allowance_earned",
                "payload_on": "ON",
                "payload_off": "OFF",
                "icon": "mdi:piggy-bank",
                "device": DEVICE_INFO,
            },
        )

        # Remaining chores (specific to this user)
        _publish(
            f"{DISCOVERY_PREFIX}/sensor/{STATE_PREFIX}_{slug}_remaining/config",
            {
                "name": f"Chores {user['name']} Remaining",
                "unique_id": f"{STATE_PREFIX}_{slug}_remaining",
                "state_topic": f"{base}/remaining",
                "unit_of_measurement": "chores",
                "icon": "mdi:checkbox-marked-circle-outline",
                "device": DEVICE_INFO,
            },
        )


def publish_all_state(today: Optional[date] = None) -> None:
    """Publish current state for all entities. Cheap to call frequently."""
    if _client is None:
        return
    today = today or date.today()
    chores = db.list_chores()

    # Open chores remaining
    open_due = [c for c in chores if c["assignment_type"] == "open" and db.chore_is_due(c, today)]
    open_remaining = sum(
        1 for c in open_due if db.is_open_chore_complete(c["id"], today) is None
    )
    _publish(f"{STATE_PREFIX}/open_remaining", str(open_remaining))

    for user in db.list_users():
        if not user["is_kid"]:
            continue
        slug = _slug(user["name"])
        base = f"{STATE_PREFIX}/{slug}"

        effort = db.user_effort_today(user["id"], today)
        reward = db.user_reward_today(user["id"], today)
        threshold_met = effort >= user["allowance_threshold"]

        _publish(f"{base}/effort_today", str(effort))
        _publish(f"{base}/reward_today", f"{reward:.2f}")
        _publish(f"{base}/allowance_earned", "ON" if threshold_met else "OFF")

        # Count remaining: specific chores assigned to this user, plus open chores still open
        remaining = 0
        for c in chores:
            if not db.chore_is_due(c, today):
                continue
            if c["assignment_type"] == "specific":
                if user["id"] in c["assigned_user_ids"] and not db.is_chore_complete_for_user(
                    c["id"], user["id"], today
                ):
                    remaining += 1
            else:  # open
                if db.is_open_chore_complete(c["id"], today) is None:
                    remaining += 1
        _publish(f"{base}/remaining", str(remaining))
