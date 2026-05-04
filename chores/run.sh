#!/usr/bin/with-contenv sh
# Read add-on options from /data/options.json (provided by Supervisor)
CONFIG_PATH=/data/options.json

export LOG_LEVEL=$(jq -r '.log_level // "info"' $CONFIG_PATH)
export RESET_TIME=$(jq -r '.reset_time // "04:00"' $CONFIG_PATH)
export CURRENCY_SYMBOL=$(jq -r '.currency_symbol // "$"' $CONFIG_PATH)
export MQTT_DISCOVERY_PREFIX=$(jq -r '.mqtt_discovery_prefix // "homeassistant"' $CONFIG_PATH)
export MQTT_STATE_PREFIX=$(jq -r '.mqtt_state_prefix // "chores"' $CONFIG_PATH)

# Pull MQTT broker info from Supervisor
MQTT_INFO=$(curl -sSL -H "Authorization: Bearer $SUPERVISOR_TOKEN" http://supervisor/services/mqtt)
export MQTT_HOST=$(echo "$MQTT_INFO" | jq -r '.data.host // "core-mosquitto"')
export MQTT_PORT=$(echo "$MQTT_INFO" | jq -r '.data.port // 1883')
export MQTT_USERNAME=$(echo "$MQTT_INFO" | jq -r '.data.username // ""')
export MQTT_PASSWORD=$(echo "$MQTT_INFO" | jq -r '.data.password // ""')

# Persistent storage for SQLite
export DB_PATH=/data/chores.db

echo "Starting Household Chores add-on (log_level=$LOG_LEVEL, mqtt=$MQTT_HOST:$MQTT_PORT)"

cd /app
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8099 --log-level "$LOG_LEVEL"
