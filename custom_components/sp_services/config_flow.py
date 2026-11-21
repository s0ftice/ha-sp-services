"""Config flow with SMS MFA for SP Services."""

from __future__ import annotations

import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SPApi, SPApiError, SPAuthError
from .const import (
    CONF_ACCOUNT_NUMBER,
    CONF_AUTHENTICATOR_ID,
    CONF_MFA_TOKEN,
    CONF_OOB_CODE,
    CONF_REFRESH_TOKEN,
    CONF_SCOPE,
    DOMAIN,
    SCOPE,
)

_LOGGER = logging.getLogger(__name__)


class SPServicesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up SP Services using its account login and optional SMS MFA."""

    VERSION = 1

    def __init__(self) -> None:
        self._tokens: dict = {}
        self._account_number: str | None = None
        self._username: str | None = None
        self._password: str | None = None
        self._mfa_token: str | None = None
        self._oob_code: str | None = None
        self._authenticator_id: str | None = None

    async def async_step_user(self, user_input: dict | None = None):
        """Collect login and account number, then start authentication."""
        errors = {}
        if user_input:
            account = user_input[CONF_ACCOUNT_NUMBER].strip()
            if not account.isdigit():
                errors[CONF_ACCOUNT_NUMBER] = "invalid_account"
            else:
                self._username = user_input[CONF_USERNAME].strip()
                self._password = user_input[CONF_PASSWORD]
                self._account_number = account
                api = SPApi(async_get_clientsession(self.hass), account, {})
                try:
                    result = await api.login(self._username, self._password)
                except SPAuthError as err:
                    self._password = None
                    _LOGGER.warning("SP Services sign-in failed: %s", err)
                    errors["base"] = "cannot_connect"
                except SPApiError:
                    self._password = None
                    errors["base"] = "cannot_connect"
                else:
                    self._password = None
                    if result.get("mfa_token"):
                        self._mfa_token = result["mfa_token"]
                        try:
                            factors = await api.authenticators(self._mfa_token)
                        except SPApiError:
                            errors["base"] = "cannot_connect"
                        else:
                            sms = [factor for factor in factors if str(factor.get("authenticator_type", "")).lower() in {"oob", "phone"}]
                            if not sms:
                                errors["base"] = "no_sms_factor"
                            else:
                                self._authenticator_id = sms[0].get("id")
                                return await self.async_step_mfa()
                    else:
                        self._tokens = api.tokens
                        return self._create_entry()

        schema = vol.Schema({
            vol.Required(CONF_USERNAME): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Required(CONF_ACCOUNT_NUMBER): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_mfa(self, user_input: dict | None = None):
        """Send an SMS challenge and collect its code."""
        errors = {}
        api = SPApi(async_get_clientsession(self.hass), self._account_number or "", {})
        if not self._oob_code:
            try:
                challenge = await api.start_sms(self._mfa_token or "", self._authenticator_id or "")
            except SPApiError:
                errors["base"] = "cannot_connect"
            else:
                self._oob_code = challenge.get("oob_code") or challenge.get("oobCode")
                if not self._oob_code:
                    errors["base"] = "invalid_auth"
        if self._oob_code:
            return await self.async_step_sms_code()
        return self.async_show_form(step_id="mfa", data_schema=vol.Schema({}), errors=errors)

    async def async_step_sms_code(self, user_input: dict | None = None):
        """Verify SMS code and validate the electricity chart access."""
        errors = {}
        if user_input:
            api = SPApi(async_get_clientsession(self.hass), self._account_number or "", {})
            try:
                await api.complete_sms(self._mfa_token or "", self._oob_code or "", user_input["sms_otp"].strip())
                # Ensure the account number and permission work before creating the entry.
                from datetime import datetime
                from zoneinfo import ZoneInfo
                await api.hourly(datetime.now(ZoneInfo("Asia/Singapore")).date())
            except SPAuthError:
                errors["base"] = "invalid_auth"
            except SPApiError as err:
                _LOGGER.warning("Could not validate SP electricity account: %s", err)
                errors["base"] = "cannot_connect"
            else:
                self._tokens = api.tokens
                return self._create_entry()

        return self.async_show_form(
            step_id="sms_code",
            data_schema=vol.Schema({vol.Required("sms_otp"): str}),
            errors=errors,
        )

    @callback
    def _create_entry(self):
        """Create a unique config entry without retaining the password."""
        return self.async_create_entry(
            title=f"SP Services ••••{(self._account_number or '')[-4:]}",
            data={
                CONF_USERNAME: self._username,
                CONF_ACCOUNT_NUMBER: self._account_number,
                "tokens": self._tokens,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SPServicesOptionsFlow()


class SPServicesOptionsFlow(config_entries.OptionsFlow):
    """Allow the polling interval to be changed."""

    async def async_step_init(self, user_input=None):
        from .const import CONF_POLL_MINUTES
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = self.config_entry.options.get(CONF_POLL_MINUTES, 30)
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required(CONF_POLL_MINUTES, default=current): vol.All(vol.Coerce(int), vol.Range(min=30, max=180))
        }))
