"""Tests for log_sanitize redaction."""

from log_sanitize import redact_secrets_in_text


def test_redacts_api_key_query_param():
    url = (
        "500 Server Error for url: https://api.stlouisfed.org/fred/series/observations?"
        "series_id=GS10&api_key=SECRETKEY123&file_type=json"
    )
    out = redact_secrets_in_text(url, ("SECRETKEY123",))
    assert "SECRETKEY123" not in out
    assert "api_key=<redacted>" in out


def test_redacts_apikey_param():
    s = "Error: https://www.alphavantage.co/query?apikey=AVKEYHERE&limit=1"
    out = redact_secrets_in_text(s, ("AVKEYHERE",))
    assert "AVKEYHERE" not in out


def test_redacts_literal_extra_secrets():
    out = redact_secrets_in_text("prefix mysecret999 suffix", ("mysecret999",))
    assert "mysecret999" not in out
    assert "<redacted>" in out


def test_redacts_bearer():
    out = redact_secrets_in_text('Authorization: Bearer tok_ABC123', ())
    assert "tok_ABC123" not in out
