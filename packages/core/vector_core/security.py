import hashlib
import hmac
import secrets

API_KEY_TAG = "vk"


def generate_api_key() -> tuple[str, str]:
    prefix = secrets.token_hex(4)
    secret = secrets.token_hex(16)
    return f"{API_KEY_TAG}_{prefix}_{secret}", prefix


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def safe_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())
