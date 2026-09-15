"""Local callback server driving the three-legged flow.

Run it, open /, click through. The temporary token secret lives in this
process and is never written into a page or a redirect.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import store
from .client import OAuthError, OscarOAuth1Client
from .config import load_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

app = FastAPI(title="Oscar OAuth 1.0a tool")

# Temporary credentials, keyed by temporary token. Server-side only: the secret
# must never reach the browser.
_pending: dict[str, str] = {}


def _client() -> OscarOAuth1Client:
    return OscarOAuth1Client(load_settings())


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    settings = load_settings()
    token = store.load(settings.token_file)
    if token is None:
        state = "<p>No access token stored.</p>"
    else:
        state = (
            f"<p>Access token stored, issued {token.age_seconds / 3600:.1f} h ago.<br>"
            "Oscar never returns a TTL, so age is the only expiry signal.</p>"
        )
    return f"""
    <h1>Oscar OAuth 1.0a tool</h1>
    <p>Host: <code>{settings.services_base}</code></p>
    {state}
    <p><a href="/oauth1/start">Start authorisation</a></p>
    <p><a href="/probe">Probe base paths</a> &middot;
       <a href="/api/specialist?specId=1">Call getProfessionalSpecialist</a></p>
    """


@app.get("/oauth1/start")
def start() -> RedirectResponse:
    with _client() as client:
        temp = client.initiate()
        _pending[temp.oauth_token] = temp.oauth_token_secret
        return RedirectResponse(client.authorize_url(temp.oauth_token))


@app.get("/oauth1/callback")
def callback(
    oauth_token: str = Query(...),
    oauth_verifier: Optional[str] = Query(None),
) -> JSONResponse:
    # A token this session did not request is a session-fixation attempt.
    temporary_secret = _pending.pop(oauth_token, None)
    if temporary_secret is None:
        raise HTTPException(400, "oauth_token does not match this session's request")

    settings = load_settings()
    with OscarOAuth1Client(settings) as client:
        try:
            parsed = client.exchange(oauth_token, temporary_secret, oauth_verifier)
        except OAuthError as exc:
            raise HTTPException(502, str(exc)) from exc

    record = store.save(
        settings.token_file, parsed["oauth_token"], parsed.get("oauth_token_secret", "")
    )
    return JSONResponse(
        {
            "status": "access token stored",
            "file": str(settings.token_file),
            "issued_at": record.issued_at,
        }
    )


@app.get("/probe")
def probe() -> JSONResponse:
    from .probe import probe_base_paths

    return JSONResponse(probe_base_paths(load_settings()))


@app.get("/api/specialist")
def specialist(specId: int = Query(...)) -> JSONResponse:
    settings = load_settings()
    token = store.load(settings.token_file)
    if token is None:
        raise HTTPException(409, "No access token — visit /oauth1/start first")

    url = f"{settings.services_base}/consults/getProfessionalSpecialist?specId={specId}"
    with OscarOAuth1Client(settings) as client:
        result = client.call("GET", url, token.oauth_token, token.oauth_token_secret)

    return JSONResponse(
        {
            "status_code": result.status_code,
            "body": result.text[:4000],
            "base_string": result.base_string,
        },
        status_code=200,
    )
