"""RFC 5849 (OAuth 1.0a) signing primitives.

Pure functions only: no network, no filesystem, no framework imports. Every
mistake in OAuth 1.0a arrives as the same unmessaged 401, so these are proven
offline against the RFC 3.4.1.1 worked example before a request is ever sent.

There is no "OAuth 1.1". The "a" revision is the one that adds oauth_verifier.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Iterable, NamedTuple, Optional
from urllib.parse import quote, unquote, urlsplit

Param = tuple[str, str]

_DEFAULT_PORTS = {"http": 80, "https": 443}

SIGNATURE_METHOD = "HMAC-SHA1"
OAUTH_VERSION = "1.0"


class SplitUrl(NamedTuple):
    base_uri: str
    params: list[Param]


@dataclass(frozen=True)
class SignedRequest:
    header: str
    base_string: str
    signature: str


def percent_encode(value: object) -> str:
    """RFC 3986 encoding: unreserved is A-Z a-z 0-9 - . _ ~ and nothing else.

    quote() encodes the UTF-8 bytes with uppercase hex, which is what the spec
    wants; the explicit safe="~" documents that ~ must survive.
    """
    if value is None:
        return ""
    return quote(str(value), safe="~", encoding="utf-8")


def _parse_query(query: str) -> list[Param]:
    """Decode a query string to raw pairs, keeping duplicates and blank values.

    unquote rather than unquote_plus: a literal '+' in a token or secret is far
    more likely than an intended space, and corrupting a credential is the
    expensive failure here.
    """
    pairs: list[Param] = []
    for chunk in query.split("&"):
        if not chunk:
            continue
        key, _, value = chunk.partition("=")
        pairs.append((unquote(key), unquote(value)))
    return pairs


def split_url(raw_url: str) -> SplitUrl:
    """Normalised base URI plus the query parameters that must be signed."""
    parts = urlsplit(raw_url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()

    authority = host
    port = parts.port
    if port is not None and port != _DEFAULT_PORTS.get(scheme):
        authority = f"{host}:{port}"

    return SplitUrl(f"{scheme}://{authority}{parts.path}", _parse_query(parts.query))


def normalise_params(params: Iterable[Param]) -> str:
    """Encode first, then sort by encoded key and encoded value. Order matters:
    sorting before encoding reorders anything non-alphanumeric."""
    encoded = sorted((percent_encode(k), percent_encode(v)) for k, v in params)
    return "&".join(f"{k}={v}" for k, v in encoded)


def signature_base_string(method: str, base_uri: str, params: Iterable[Param]) -> str:
    """The normalised parameter string is encoded a second time here, which is
    where sequences like %253D come from."""
    return "&".join(
        (
            method.upper(),
            percent_encode(base_uri),
            percent_encode(normalise_params(params)),
        )
    )


def signing_key(consumer_secret: str, token_secret: str = "") -> str:
    """The '&' is always present, including on leg 1 with no token secret yet."""
    return f"{percent_encode(consumer_secret)}&{percent_encode(token_secret)}"


def sign_request(
    method: str,
    url: str,
    consumer_key: str,
    consumer_secret: str,
    token: Optional[str] = None,
    token_secret: str = "",
    callback: Optional[str] = None,
    verifier: Optional[str] = None,
    nonce: Optional[str] = None,
    timestamp: Optional[int] = None,
) -> SignedRequest:
    """Sign one request. `url` must be the full URL, query string included.

    A JSON body is never folded into the signature — OAuth 1.0a includes a body
    only when it is application/x-www-form-urlencoded, and every write on
    Oscar's services layer is JSON.
    """
    oauth_params: dict[str, str] = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": secrets.token_hex(16) if nonce is None else str(nonce),
        "oauth_signature_method": SIGNATURE_METHOD,
        "oauth_timestamp": str(int(time.time()) if timestamp is None else timestamp),
        "oauth_version": OAUTH_VERSION,
    }
    if callback is not None:
        oauth_params["oauth_callback"] = callback
    if token is not None:
        oauth_params["oauth_token"] = token
    if verifier is not None:
        oauth_params["oauth_verifier"] = verifier

    base_uri, query_params = split_url(url)
    base_string = signature_base_string(
        method, base_uri, [*query_params, *oauth_params.items()]
    )

    digest = hmac.new(
        signing_key(consumer_secret, token_secret).encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    signature = base64.b64encode(digest).decode("ascii")

    header_params = sorted({**oauth_params, "oauth_signature": signature}.items())
    header = "OAuth " + ", ".join(
        f'{percent_encode(k)}="{percent_encode(v)}"' for k, v in header_params
    )
    return SignedRequest(header=header, base_string=base_string, signature=signature)


def parse_token_response(body: Optional[str]) -> Optional[dict[str, str]]:
    """Both token legs answer form-urlencoded; failures often arrive as an HTML
    page with a 200. Return None rather than raising."""
    if not body:
        return None
    text = body.strip()
    if not text or text.startswith("<"):
        return None

    pairs: dict[str, str] = {}
    for chunk in text.split("&"):
        key, sep, value = chunk.partition("=")
        if sep:
            pairs[unquote(key)] = unquote(value)

    return pairs if "oauth_token" in pairs else None
