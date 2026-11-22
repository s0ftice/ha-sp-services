"""Minimal asynchronous client for the SP Services portal API."""

from __future__ import annotations

from datetime import date
import logging
from typing import Any

from aiohttp import ClientError, ClientSession

from .const import (
    AUTH_BASE,
    AUDIENCE,
    CHART_URL,
    CLIENT_ID,
    SCOPE,
)

_LOGGER = logging.getLogger(__name__)


class SPApiError(Exception):
    """An SP API request failed."""


class SPAuthError(SPApiError):
    """Authentication failed or needs user action."""


class SPApi:
    """Owns token state and communicates with SP endpoints."""

    def __init__(self, session: ClientSession, account_number: str, tokens: dict[str, Any]):
        self._session = session
        self.account_number = account_number
        self.tokens = dict(tokens)

    async def _json(
        self,
        url: str,
        *,
        data: dict | None = None,
        token: str | None = None,
        id_token: str | None = None,
    ) -> Any:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if id_token:
            headers["X-id-token"] = id_token
        try:
            async with self._session.request("POST" if data is not None else "GET", url,
                                             json=data, headers=headers) as response:
                try:
                    body = await response.json(content_type=None)
                except (ValueError, ClientError):
                    body = {}
                if response.status >= 400:
                    error_body = body if isinstance(body, dict) else {}
                    code = error_body.get("error") or error_body.get("code") or "unknown"
                    if response.status == 403 and code in {"mfa_required", "mfa_registration_required"}:
                        return body
                    if response.status in (401, 403) and code in {
                        "invalid_grant", "invalid_token", "invalid_claim"
                    }:
                        raise SPAuthError(f"SP authentication rejected the request ({code})")
                    if response.status == 401:
                        raise SPAuthError("SP authentication rejected the request (HTTP 401)")
                    raise SPApiError(f"SP request failed (HTTP {response.status}, {code})")
                return body
        except (ClientError, TimeoutError) as err:
            raise SPApiError("Could not connect to SP Services") from err

    async def login(self, username: str, password: str) -> dict:
        """Start password grant; callers handle MFA if required."""
        body = await self._token_request({
            "grant_type": "http://auth0.com/oauth/grant-type/password-realm",
            "client_id": CLIENT_ID,
            "audience": AUDIENCE,
            "username": username,
            "password": password,
            "scope": SCOPE,
            "realm": "Username-Password-Authentication",
        })
        if body.get("error") in {"mfa_required", "mfa_registration_required"}:
            return body
        self._set_tokens(body)
        return body

    async def authenticators(self, mfa_token: str) -> list[dict]:
        data = await self._json(f"{AUTH_BASE}/mfa/authenticators", token=mfa_token)
        if isinstance(data, list):
            return data
        return data.get("authenticators", [])

    async def start_sms(self, mfa_token: str, authenticator_id: str) -> dict:
        return await self._json(f"{AUTH_BASE}/mfa/challenge", data={
            "client_id": CLIENT_ID,
            "mfa_token": mfa_token,
            "challenge_type": "oob",
            "authenticator_id": authenticator_id,
        })

    async def complete_sms(self, mfa_token: str, oob_code: str, binding_code: str) -> dict:
        body = await self._token_request({
            "grant_type": "http://auth0.com/oauth/grant-type/mfa-oob",
            "client_id": CLIENT_ID,
            "mfa_token": mfa_token,
            "oob_code": oob_code,
            "binding_code": binding_code,
        })
        if body.get("error"):
            raise SPAuthError(f"SP rejected the SMS verification ({body['error']})")
        self._set_tokens(body)
        return body

    async def refresh(self) -> None:
        refresh_token = self.tokens.get("refresh_token")
        if not refresh_token:
            raise SPAuthError("SP session expired; remove and add the integration again")
        body = await self._token_request({
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": refresh_token,
            "scope": self.tokens.get("scope", SCOPE),
        })
        if body.get("error"):
            raise SPAuthError("SP session expired; reconfigure the integration")
        self._set_tokens(body)

    async def _token_request(self, data: dict) -> dict:
        # Token requests require form encoding; API requests use JSON.
        try:
            async with self._session.post(f"{AUTH_BASE}/oauth/token", data=data,
                                          headers={"Accept": "application/json"}) as response:
                try:
                    body = await response.json(content_type=None)
                except (ValueError, ClientError):
                    body = {}
                if not isinstance(body, dict):
                    body = {}
                if response.status >= 500:
                    raise SPApiError(f"SP identity service failed (HTTP {response.status})")
                if response.status >= 400 and body.get("error") not in {
                    "mfa_required", "mfa_registration_required"
                }:
                    error = body.get("error") or "authentication_error"
                    description = body.get("error_description") or ""
                    raise SPAuthError(f"SP sign-in failed ({error}): {description}".rstrip())
                return body
        except (ClientError, TimeoutError) as err:
            raise SPApiError("Could not connect to SP identity service") from err

    def _set_tokens(self, body: dict) -> None:
        if not body.get("access_token"):
            raise SPAuthError("SP did not return an access token")
        self.tokens.update({
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token", self.tokens.get("refresh_token")),
            "id_token": body.get("id_token", self.tokens.get("id_token")),
            "scope": body.get("scope", self.tokens.get("scope", SCOPE)),
        })

    async def _ensure_auth(self) -> None:
        if not self.tokens.get("access_token"):
            await self.refresh()

    async def hourly(self, day: date) -> list[dict]:
        """Get one day's 48 half-hour readings, refreshing once on auth failure."""
        await self._ensure_auth()
        try:
            return await self._hourly_request(day)
        except SPAuthError as err:
            if "invalid_claim" in str(err):
                # This endpoint sometimes rejects a otherwise valid access token;
                # refreshing cannot fix account claim mismatches, so stop visibly.
                raise
            await self.refresh()
            return await self._hourly_request(day)

    async def _hourly_request(self, day: date) -> list[dict]:
        body = await self._json(CHART_URL, data={
            "charts:hourly": {"accountNos": [self.account_number], "date": day.isoformat()}
        }, token=self.tokens["access_token"], id_token=self.tokens.get("id_token"))
        chart = body.get("charts:hourly", {}) if isinstance(body, dict) else {}
        if chart.get("error"):
            raise SPApiError("SP returned an error for the electricity chart")
        electricity = (chart.get("data") or {}).get("electricity") or {}
        if electricity.get("unit") not in (None, "kWh"):
            raise SPApiError("SP returned electricity readings in an unexpected unit")
        rows = electricity.get("consumption") or []
        if not isinstance(rows, list):
            raise SPApiError("SP returned an unexpected electricity chart format")
        return rows
