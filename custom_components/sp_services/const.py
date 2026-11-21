"""Constants for the SP Services integration."""

from datetime import timedelta

DOMAIN = "sp_services"
CONF_ACCOUNT_NUMBER = "account_number"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_MFA_TOKEN = "mfa_token"
CONF_OOB_CODE = "oob_code"
CONF_AUTHENTICATOR_ID = "authenticator_id"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_ACCESS_TOKEN = "access_token"
CONF_ID_TOKEN = "id_token"
CONF_SCOPE = "scope"
CONF_POLL_MINUTES = "poll_minutes"
CONF_TOKENS = "tokens"

AUTH_BASE = "https://identity.spdigital.sg"
CHART_URL = "https://c-api-gateway.tkg.spdigital.io/skalbox/api"
CLIENT_ID = "z1tl6I1V6HI201ule9tmSALb97hw8Biu"
AUDIENCE = "https://profile.up.spdigital.sg/"
SCOPE = "openid profile email user_metadata offline_access"

DEFAULT_POLL_INTERVAL = timedelta(minutes=30)
HISTORY_DAYS = 3
PLATFORMS = ["sensor"]
