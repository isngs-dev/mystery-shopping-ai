from app.config import Settings
from app.services.ai.anthropic_provider import _key_shape


def _clean(v):
    return Settings(anthropic_api_key=v).anthropic_api_key


def test_pasted_keys_are_cleaned_like_a_host_environment_page_would_leave_them():
    k = "sk-ant-api03-AbCdEf_123-xyz"
    assert _clean(k) == k
    assert _clean(f'  "{k}"  ') == k                     # quotes and spaces around it
    assert _clean(f"'{k}'\n") == k                       # single quotes and a line break
    assert _clean(f"ANTHROPIC_API_KEY={k}") == k         # the whole line was pasted
    assert _clean(f"Bearer {k}") == k
    assert _clean("sk-ant-api03-AbCd\nEf_123-xyz") == k   # a wrapped paste
    assert _clean("   ") is None and _clean(None) is None


def test_the_rejected_key_message_shows_the_shape_but_never_the_key():
    msg = _key_shape("sk-ant-api03-SECRETSECRETSECRET")
    assert "sk-ant-" in msg and "31 characters" in msg and "SECRET" not in msg
    assert "does not start with sk-ant-" in _key_shape("abc123")
