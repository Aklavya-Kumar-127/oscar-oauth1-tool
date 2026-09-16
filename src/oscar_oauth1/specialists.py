"""The three specialist operations, shared by the CLI and the local UI.

One definition of each request, so the payload shape and the parameter rules
cannot drift between the two front ends. Everything observed against
medozai-dev is encoded here rather than left to the caller:

- `search` needs *both* referralNo and lastName; either alone answers 400.
- `add` rejects an empty streetAddress with a bare 400.
- `add` rejects a (lastName, referralNo) pair that already exists, with the
  same bare 400, so a 400 has two indistinguishable causes.

Every function returns the raw ApiResult. Nothing here decides what a failure
means for the caller; that is a presentation question and differs between a
terminal and a browser.
"""

from __future__ import annotations

import json
from typing import Optional
from urllib.parse import urlencode

from .client import ApiResult, OscarOAuth1Client
from .config import Settings
from .store import AccessToken

# The billing number. 000000 is the agreed placeholder, shared by the CLI
# default, the server's form default, and the form's own pre-filled value.
DEFAULT_REFERRAL_NO = "000000"


def get_specialist(
    client: OscarOAuth1Client,
    settings: Settings,
    token: AccessToken,
    spec_id: int,
) -> ApiResult:
    url = f"{settings.services_base}/consults/getProfessionalSpecialist?specId={spec_id}"
    return client.call("GET", url, token.oauth_token, token.oauth_token_secret)


def find_specialist(
    client: OscarOAuth1Client,
    settings: Settings,
    token: AccessToken,
    referral_no: str,
    last_name: str,
) -> ApiResult:
    """Exact lookup on the pair, not a search: one record or 404.

    Case-insensitive on lastName, but it does not prefix-match: "Flamingo"
    finds the record surnamed exactly that, not Flamingo-4.
    """
    query = urlencode({"referralNo": referral_no, "lastName": last_name})
    url = f"{settings.services_base}/professionalSpecialist/search?{query}"
    return client.call("GET", url, token.oauth_token, token.oauth_token_secret)


def add_specialist(
    client: OscarOAuth1Client,
    settings: Settings,
    token: AccessToken,
    first_name: str,
    last_name: str,
    clinic_name: str,
    phone_number: str = "",
    referral_no: str = DEFAULT_REFERRAL_NO,
) -> ApiResult:
    """Create a specialist. `clinic_name` lands in streetAddress, which is how
    Oscar's own admin UI labels the field, and must not be empty."""
    url = f"{settings.services_base}/professionalSpecialist/add"
    body = {
        "firstName": first_name,
        "lastName": last_name,
        "streetAddress": clinic_name,
        "phoneNumber": phone_number,
        "referralNo": referral_no,
    }
    # The body is deliberately not signed: OAuth 1.0a folds a body into the
    # base string only when it is form-encoded, and this one is JSON.
    return client.call(
        "POST", url, token.oauth_token, token.oauth_token_secret, json_body=body
    )


def record_id(record: dict) -> Optional[int]:
    """Pull the id out of an already-parsed record dict, or None.

    Accepts a plain int or a digit-only string: Oscar's own admin UI treats
    the id as numeric, but a legacy JSP layer serialising it as a string is
    not out of the question, and silently dropping it would be worse than
    coercing it.
    """
    value = record.get("id")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def specialist_id(body: str) -> Optional[int]:
    """The id both MD-1585 and MD-1586 ask to be recorded, or None."""
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return record_id(data)


def is_error_page(body: str) -> bool:
    """Oscar answers every failure with the same branded HTML page, in which
    the status line is the only signal. Callers use this to avoid rendering
    several kilobytes of it."""
    return body.lstrip().startswith("<")
