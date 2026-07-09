"""Constants for the Alfa Lebanon integration (web-portal transport)."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "alfa_lb"

CONF_MOBILE = "mobile"
CONF_PASSWORD = "password"

DEFAULT_SCAN_INTERVAL = timedelta(minutes=30)

ATTRIBUTION = "Data provided by Alfa (alfa.com.lb)"
MANUFACTURER = "Alfa Telecom Lebanon"

# Web portal (ASP.NET MVC, cookie-session + anti-forgery token).
PORTAL_BASE = "https://www.alfa.com.lb"
LOGIN_PATH = "/en/account/login"
CONSUMPTION_PATH = "/en/account/getconsumptionasync"
SERVICES_PATH = "/en/account/manage-services/getmyservicesasync"
EXPIRY_PATH = "/en/account/getexpirydate"
LAST_RECHARGE_PATH = "/en/account/getlastrecharge"
EXCHANGE_RATE_PATH = "/en/shared/exchangerate"

# --- add-on session-file transport ---
# The alfa-session add-on writes the raw portal JSON here; the integration
# reads it. Both /share paths are visible to HA Core.
SESSION_FILE = "/share/alfa_lb/latest.json"
# WHY duplicated elsewhere: homeassistant/packages/alfa_lb_recovery.yaml (the
# HomeLab repo, not this one) hardcodes this same path as a shell_command
# literal ("touch /share/alfa_lb/refresh.request") because YAML can't import
# this Python constant. If this path ever changes, update that file too.
REFRESH_REQUEST = "/share/alfa_lb/refresh.request"

# The add-on refetches every 30 min; treat the file as stale after 60 min
# (2x cadence) so a single missed run does not flap the sensors.
STALE_AFTER = timedelta(minutes=60)

# Reading a local file is cheap, so poll more often than the add-on writes —
# this picks up on-demand refreshes and recovers from `unavailable` quickly.
FILE_POLL_INTERVAL = timedelta(minutes=10)
