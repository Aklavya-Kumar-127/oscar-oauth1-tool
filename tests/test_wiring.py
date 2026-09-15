"""Non-network wiring checks: URL construction and token persistence."""

from pathlib import Path

from oscar_oauth1.client import OscarOAuth1Client
from oscar_oauth1.config import Settings
from oscar_oauth1 import store


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        host="https://emr.example.com",
        context_path="/oscar",
        consumer_key="key",
        consumer_secret="secret",
        callback_url="http://localhost:3000/oauth1/callback",
        token_file=tmp_path / "token.json",
    )


def test_derived_bases(tmp_path):
    settings = _settings(tmp_path)
    assert settings.oauth_base == "https://emr.example.com/oscar/ws/oauth"
    assert settings.services_base == "https://emr.example.com/oscar/ws/services"


def test_authorize_url_is_unsigned(tmp_path):
    with OscarOAuth1Client(_settings(tmp_path)) as client:
        url = client.authorize_url("tmp123")
    assert url == "https://emr.example.com/oscar/ws/oauth/authorize?oauth_token=tmp123"
    assert "oauth_signature" not in url


def test_token_round_trip_records_issue_time(tmp_path):
    path = tmp_path / "token.json"
    saved = store.save(path, "at", "as")
    loaded = store.load(path)
    assert loaded == saved
    assert loaded.age_seconds >= 0


def test_missing_and_corrupt_token_files_return_none(tmp_path):
    assert store.load(tmp_path / "absent.json") is None
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("not json", encoding="utf-8")
    assert store.load(corrupt) is None
