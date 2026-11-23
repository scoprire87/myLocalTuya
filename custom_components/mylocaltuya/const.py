"""Constants for myLocalTuya."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "mylocaltuya"

# Same app credentials used by the official Tuya integration for the
# Smart Life "QR code" login (no Tuya IoT developer account required).
TUYA_CLIENT_ID = "HA_3y9q4ak7g4ephrvke"
TUYA_SCHEMA = "haauthorize"
TUYA_QR_PREFIX = "tuyaSmart--qrLogin?token="

CONF_USER_CODE = "user_code"
CONF_TOKEN_INFO = "token_info"
CONF_TERMINAL_ID = "terminal_id"
CONF_ENDPOINT = "endpoint"
CONF_UID = "uid"
CONF_USERNAME = "username"
CONF_DEVICES = "devices"

# Keys of a stored device
DEV_ID = "id"
DEV_NAME = "name"
DEV_LOCAL_KEY = "local_key"
DEV_CATEGORY = "category"
DEV_PRODUCT_ID = "product_id"
DEV_PRODUCT_NAME = "product_name"
DEV_SUB = "sub"
DEV_HOST = "host"
DEV_VERSION = "version"
DEV_MANUAL_HOST = "manual_host"
DEV_DPS = "dps"
DEV_PARENT = "parent_id"
DEV_NODE_ID = "node_id"
DEV_RAW_KEYS = "cloud_fields"

# Tuya categories of gateways/hubs (Zigbee, BLE, mesh, water-timer hubs)
GATEWAY_CATEGORIES = {"wg", "wg2", "wgsxj", "wfcon", "zigbee", "blegw", "wnykq_gw"}

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

PROTOCOL_VERSIONS = ["3.3", "3.4", "3.5", "3.1"]

# Local connection tuning (seconds)
HEARTBEAT_INTERVAL = 10
POLL_INTERVAL = 60
CONNECTION_DEAD_AFTER = 35
RECONNECT_MIN_DELAY = 5
RECONNECT_MAX_DELAY = 60
# After this many consecutive failed connections we ask the cloud for a
# fresh local key (rate limited by CLOUD_REFRESH_MIN_INTERVAL).
FAILURES_BEFORE_CLOUD_REFRESH = 3
CLOUD_REFRESH_MIN_INTERVAL = 3600
# How long the config flow waits for UDP broadcasts to find IP/version.
DISCOVERY_WAIT = 8
