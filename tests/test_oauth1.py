"""Acceptance tests from the MD-1584 brief, reproduced character for character.

These must pass before any network code runs. A signing bug and a wrong
credential are indistinguishable over the wire.
"""

import pytest

from oscar_oauth1.oauth1 import (
    normalise_params,
    parse_token_response,
    percent_encode,
    sign_request,
    signature_base_string,
    signing_key,
    split_url,
)

# RFC 5849 section 3.4.1.1 worked example.
RFC_PARAMS = [
    ("b5", "=%3D"),
    ("a3", "a"),
    ("c@", ""),
    ("a2", "r b"),
    ("c2", ""),
    ("a3", "2 q"),
    ("oauth_consumer_key", "9djdj82h48djs9d2"),
    ("oauth_token", "kkk9d7dh3k39sjv7"),
    ("oauth_signature_method", "HMAC-SHA1"),
    ("oauth_timestamp", "137131201"),
    ("oauth_nonce", "7d8f3e4a"),
]

RFC_NORMALISED = (
    "a2=r%20b&a3=2%20q&a3=a&b5=%3D%253D&c%40=&c2=&"
    "oauth_consumer_key=9djdj82h48djs9d2&oauth_nonce=7d8f3e4a&"
    "oauth_signature_method=HMAC-SHA1&oauth_timestamp=137131201&"
    "oauth_token=kkk9d7dh3k39sjv7"
)

RFC_BASE_STRING = (
    "POST&http%3A%2F%2Fexample.com%2Frequest&"
    "a2%3Dr%2520b%26a3%3D2%2520q%26a3%3Da%26b5%3D%253D%25253D%26c%2540%3D%26c2%3D%26"
    "oauth_consumer_key%3D9djdj82h48djs9d2%26oauth_nonce%3D7d8f3e4a%26"
    "oauth_signature_method%3DHMAC-SHA1%26oauth_timestamp%3D137131201%26"
    "oauth_token%3Dkkk9d7dh3k39sjv7"
)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("A-Za-z0-9-._~", "A-Za-z0-9-._~"),   # over-encoding
        ("!*'()", "%21%2A%27%28%29"),          # encodeURIComponent raw
        ("r b", "r%20b"),                      # '+' for space
        ("=&?/@+", "%3D%26%3F%2F%40%2B"),      # a "safe characters" default
        ("é", "%C3%A9"),                  # codepoint instead of UTF-8 bytes
        ("c&d", "c%26d"),
        ("=%3D", "%3D%253D"),                  # already-encoded value
    ],
)
def test_percent_encode(value, expected):
    assert percent_encode(value) == expected


def test_percent_encode_none_is_empty():
    assert percent_encode(None) == ""


@pytest.mark.parametrize(
    "raw,base_uri,params",
    [
        (
            "https://example.com/oscar/ws/services/consults/getProfessionalSpecialist?specId=1",
            "https://example.com/oscar/ws/services/consults/getProfessionalSpecialist",
            [("specId", "1")],
        ),
        ("HTTPS://Example.COM:443/x", "https://example.com/x", []),
        ("HTTP://Example.COM:80/x", "http://example.com/x", []),
        ("http://localhost:8080/oscar/ws", "http://localhost:8080/oscar/ws", []),
    ],
)
def test_split_url(raw, base_uri, params):
    result = split_url(raw)
    assert result.base_uri == base_uri
    assert result.params == params


def test_split_url_drops_fragment():
    assert split_url("https://example.com/x?a=1#frag").base_uri == "https://example.com/x"


@pytest.mark.parametrize(
    "params,expected",
    [
        ([("b", "2"), ("a", "z"), ("a", "a")], "a=a&a=z&b=2"),
        ([("a3", "2 q"), ("a3", "a")], "a3=2%20q&a3=a"),
        (RFC_PARAMS, RFC_NORMALISED),
    ],
)
def test_normalise_params(params, expected):
    assert normalise_params(params) == expected


def test_signature_base_string_rfc_example():
    assert (
        signature_base_string("POST", "http://example.com/request", RFC_PARAMS)
        == RFC_BASE_STRING
    )


@pytest.mark.parametrize(
    "args,expected",
    [
        (("cs",), "cs&"),
        (("cs", "ts"), "cs&ts"),
        (("a b", "c&d"), "a%20b&c%26d"),
    ],
)
def test_signing_key(args, expected):
    assert signing_key(*args) == expected


# --- sign_request behavioural assertions ---------------------------------

FIXED = dict(consumer_key="key", consumer_secret="secret", nonce="n", timestamp=1)
URL_WITH_QUERY = "https://example.com/ws/getProfessionalSpecialist?specId=1"
URL_WITHOUT_QUERY = "https://example.com/ws/getProfessionalSpecialist"
CALLBACK = "http://localhost:3000/oauth1/callback"


def test_query_parameters_are_signed():
    assert "specId%3D1" in sign_request("GET", URL_WITH_QUERY, **FIXED).base_string


def test_signing_the_path_only_gives_a_different_signature():
    with_query = sign_request("GET", URL_WITH_QUERY, **FIXED).signature
    without_query = sign_request("GET", URL_WITHOUT_QUERY, **FIXED).signature
    assert with_query != without_query


def test_header_shape_excludes_application_parameters():
    header = sign_request("GET", URL_WITH_QUERY, **FIXED).header
    assert header.startswith("OAuth ")
    assert 'oauth_signature_method="HMAC-SHA1"' in header
    assert 'oauth_version="1.0"' in header
    assert "specId" not in header


def test_leg_one_carries_callback_and_no_token_or_verifier():
    header = sign_request("POST", "https://example.com/oscar/ws/oauth/initiate",
                          callback=CALLBACK, **FIXED).header
    assert 'oauth_callback="http%3A%2F%2Flocalhost%3A3000%2Foauth1%2Fcallback"' in header
    assert "oauth_verifier" not in header
    assert "oauth_token=" not in header


def test_leg_three_carries_token_and_verifier():
    header = sign_request("POST", "https://example.com/oscar/ws/oauth/token",
                          token="tmp", token_secret="s", verifier="v123", **FIXED).header
    assert 'oauth_verifier="v123"' in header
    assert 'oauth_token="tmp"' in header


def test_fixed_nonce_and_timestamp_are_deterministic():
    first = sign_request("GET", URL_WITH_QUERY, **FIXED)
    second = sign_request("GET", URL_WITH_QUERY, **FIXED)
    assert first.signature == second.signature
    assert first.header == second.header


def test_missing_token_secret_still_signs_with_trailing_ampersand():
    """Leg 1 has no token secret; the key must still end with '&'."""
    assert sign_request("POST", "https://example.com/initiate", **FIXED).signature == (
        sign_request("POST", "https://example.com/initiate", token_secret="", **FIXED).signature
    )


def test_json_body_is_never_folded_into_the_signature():
    """sign_request takes no body argument at all, by construction."""
    import inspect

    assert "body" not in inspect.signature(sign_request).parameters


# --- token response parsing ----------------------------------------------

@pytest.mark.parametrize(
    "body,expected",
    [
        (
            "oauth_token=abc&oauth_token_secret=xyz&oauth_callback_confirmed=true",
            {"oauth_token": "abc", "oauth_token_secret": "xyz", "oauth_callback_confirmed": "true"},
        ),
        (
            "oauth_token=a%20b&oauth_token_secret=c%26d",
            {"oauth_token": "a b", "oauth_token_secret": "c&d"},
        ),
        ("<!DOCTYPE html><html><title>401</title>", None),
        ("  <html>", None),
        ("error=invalid_consumer", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_token_response(body, expected):
    assert parse_token_response(body) == expected
