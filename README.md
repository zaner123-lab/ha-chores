# Household Chores — Home Assistant Add-on

A self-hosted chore tracker that runs as an HA add-on. UI is exposed through
HA Ingress (sidebar entry, works in the Companion app on phone/tablet/desktop).
State publishes to HA via MQTT discovery, so chore counts, effort points, and
allowance status all become real HA entities you can use in automations.

**This is the add-on repository.** Add the URL of this repo to HA's add-on store
to install it. Quick install instructions below.

## What it does

- One place to define and schedule all chores
- Two assignment modes per chore:
  - **Open** — anyone can claim/complete it once per due date (e.g. *Vacuum stairs*)
  - **Specific** — assigned to one or more users; each must complete their own
    copy (e.g. *Clean bedroom* assigned to a specific kid)
- Frequencies: daily, weekly (specific weekdays), monthly (day of month),
  every-N-days
- Per-chore effort points and optional currency reward
- Per-kid allowance with daily effort threshold (e.g. "10 effort points = $5")
- Allowance is paid when the threshold is met; payouts are recorded daily and
  exposed as HA sensors
- Mobile-friendly Today view, fuller Admin view for configuration

## Install

1. In Home Assistant, go to **Settings → Add-ons → Add-on Store**.
2. Click the three-dot menu → **Repositories**, paste this repo's URL.
3. Find **Household Chores** in the store, click **Install**.
4. After install, on the add-on page:
   - Make sure the **Mosquitto broker** add-on is installed and running
     (needed for HA entity exposure).
   - Set **Start on boot**, **Watchdog**, and click **Start**.
   - Enable **Show in sidebar**.
5. Open it from the sidebar. First time: go to **Admin** → add people, then
   chores.

## HA entities created (per kid)

- `sensor.chores_<name>_effort_today` — integer effort points earned today
- `sensor.chores_<name>_reward_today` — currency reward earned today
- `binary_sensor.chores_<name>_allowance_earned` — `on` if today's threshold met
- `sensor.chores_<name>_remaining` — count of due chores not yet done

Plus globally: `sensor.chores_open_remaining` — count of open chores still
unclaimed today.

## Example automations

A few ideas for what to do with the entities. See `examples/` for full YAML.

- Trigger a "you got allowance!" announcement on `binary_sensor.chores_*_allowance_earned`
  going `on`.
- A reminder TTS at dinner time if `sensor.chores_*_remaining` > 0.
- A Lovelace dashboard showing today's effort progress with horizontal bar
  cards.

## Architecture

- Python 3 / FastAPI backend, server-rendered Jinja templates (no JS build step)
- SQLite stored at `/data/chores.db` (persists across add-on updates)
- MQTT client publishes HA discovery topics under `homeassistant/.../config`
  and state under `chores/...`
- Daily scheduler at configurable `reset_time` records allowance payouts for
  the day that just ended

## Add-on options

| Option | Default | Notes |
| --- | --- | --- |
| `log_level` | `info` | trace, debug, info, notice, warning, error, fatal |
| `reset_time` | `04:00` | When to close the prior day and record payouts |
| `currency_symbol` | `$` | Display only |
| `mqtt_discovery_prefix` | `homeassistant` | Match HA's MQTT integration |
| `mqtt_state_prefix` | `chores` | Topic prefix for state updates |
