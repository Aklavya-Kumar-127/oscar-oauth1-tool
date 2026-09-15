"""HTTP layer: the three legs, plus a signed-call helper.

Every request logs its signature base string, because when a call fails the
base string is the only artefact that explains why.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from .config import Settings
from .oauth1 import SignedRequest, parse_token_response, sign_request

log = logging.getLogger("oscar_oauth1")


class OAuthError(RuntimeError):
    """A leg returned something that was not a token response."""


@dataclass(frozen=True)
class TemporaryCredentials:
    oauth_token: str
    oauth_token_secret: str
    callback_confirmed: bool


@dataclass(frozen=True)
class ApiResult:
    status_code: int
    text: str
    headers: dict[str, str]
    base_string: str


class OscarOAuth1Client:
    def __init__(self, settings: Settings, timeout: float = 30.0):
        self.settings = settings
        self._http = httpx.Client(timeout=timeout, follow_redirects=False)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "OscarOAuth1Client":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _sign(self, method: str, url: str, **kwargs) -> SignedRequest:
        signed = sign_request(
            method=method,
            url=url,
            consumer_key=self.settings.consumer_key,
            consumer_secret=self.settings.consumer_secret,
            **kwargs,
        )
        log.info("%s %s\n  base_string: %s", method, url, signed.base_string)
        return signed

    def initiate(self) -> TemporaryCredentials:
        """Leg 1 — temporary credentials, signed with the consumer alone."""
        url = f"{self.settings.oauth_base}/initiate"
        signed = self._sign("POST", url, callback=self.settings.callback_url)

        # Accept: */* is deliberate. The token legs never answer JSON; asking
        # for it returns a 406 that looks nothing like an auth failure.
        response = self._http.post(
            url, headers={"Authorization": signed.header, "Accept": "*/*"}
        )
        parsed = parse_token_response(response.text)
        if parsed is None:
            raise OAuthError(
                f"initiate returned no token (HTTP {response.status_code}). "
                f"Base string was:\n{signed.base_string}\n"
                f"Body began: {response.text[:200]!r}"
            )

        return TemporaryCredentials(
            oauth_token=parsed["oauth_token"],
            oauth_token_secret=parsed.get("oauth_token_secret", ""),
            callback_confirmed=parsed.get("oauth_callback_confirmed") == "true",
        )

    def authorize_url(self, temporary_token: str) -> str:
        """Leg 2 — nothing is signed; this is a handoff to a login screen."""
        return f"{self.settings.oauth_base}/authorize?oauth_token={temporary_token}"

    def exchange(
        self, temporary_token: str, temporary_secret: str, verifier: Optional[str]
    ) -> dict[str, str]:
        """Leg 3 — signed with the temporary token and its secret.

        `verifier` is omitted entirely when None: an empty string still changes
        the base string.
        """
        url = f"{self.settings.oauth_base}/token"
        signed = self._sign(
            "POST",
            url,
            token=temporary_token,
            token_secret=temporary_secret,
            verifier=verifier,
        )
        response = self._http.post(
            url, headers={"Authorization": signed.header, "Accept": "*/*"}
        )
        parsed = parse_token_response(response.text)
        if parsed is None:
            raise OAuthError(
                f"token exchange returned no token (HTTP {response.status_code}). "
                f"Base string was:\n{signed.base_string}\n"
                f"Body began: {response.text[:200]!r}"
            )
        return parsed

    def call(
        self,
        method: str,
        url: str,
        token: str,
        token_secret: str,
        json_body: Optional[object] = None,
    ) -> ApiResult:
        """One independently signed API call. There is no session.

        A JSON body is sent but never signed — OAuth 1.0a folds a body into the
        base string only when it is application/x-www-form-urlencoded.
        """
        signed = self._sign(method, url, token=token, token_secret=token_secret)
        headers = {"Authorization": signed.header, "Accept": "application/json"}

        response = self._http.request(method, url, headers=headers, json=json_body)
        return ApiResult(
            status_code=response.status_code,
            text=response.text,
            headers=dict(response.headers),
            base_string=signed.base_string,
        )
