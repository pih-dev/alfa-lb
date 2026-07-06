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
