# Household Chores

Track family chores with effort points, rewards, and per-kid allowances.

## Setup

1. Install and start the **Mosquitto broker** add-on (needed for HA entity exposure).
2. Start this add-on. Click **Open Web UI**.
3. Go to **Admin** → create users (mark kids as kids; set their allowance amount and threshold).
4. Create chores. Choose:
   - Effort points (1+) and optional currency reward.
   - Frequency: daily / weekly (pick weekdays) / monthly (day of month) / every N days.
   - Assignment: anyone (open) or specific people.
5. Open **Today** to mark chores complete.

## HA entities

After the add-on starts and a kid user exists, HA will auto-create:

- `sensor.chores_<name>_effort_today`
- `sensor.chores_<name>_reward_today`
- `binary_sensor.chores_<name>_allowance_earned`
- `sensor.chores_<name>_remaining`
- `sensor.chores_open_remaining` (global)

Use these in automations / Lovelace dashboards.

## Configuration

```yaml
log_level: info
reset_time: "04:00"
currency_symbol: "$"
mqtt_discovery_prefix: homeassistant
mqtt_state_prefix: chores
```

The reset time is when the previous day is closed out and any earned allowance
is recorded.
