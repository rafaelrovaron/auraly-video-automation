from __future__ import annotations

import json
import math
import re
from urllib.parse import unquote

from pydantic import JsonValue

MAX_SAFE_METADATA_BYTES = 64 * 1024
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_BASE64_LIKE_VALUE = re.compile(r"^[A-Za-z0-9+/_-]{256,}={0,2}$")
_JWT_LIKE_VALUE = re.compile(
    r"^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$"
)
_API_KEY_LIKE_VALUE = re.compile(
    r"(?i)(?:\bsk-[a-z0-9_-]{12,}|\bAIza[a-z0-9_-]{16,}|"
    r"\bgithub_pat_[a-z0-9_]{12,}|\bgh[pousr]_[a-z0-9]{12,}|\bxox[baprs]-[a-z0-9-]{12,})"
)
_MEDIA_BASE64_PREFIXES = (
    "iVBORw0KGgo",
    "/9j/",
    "R0lGOD",
    "UklGR",
    "SUQz",
    "T2dnUw",
)
_SENSITIVE_IDENTIFIER_SEGMENTS = frozenset(
    {
        "accesskey",
        "accesstoken",
        "apikey",
        "authorization",
        "bearer",
        "cookie",
        "credential",
        "credentials",
        "oauth",
        "password",
        "private",
        "privatekey",
        "secret",
        "secretkey",
        "signedurl",
        "token",
    }
)

_SENSITIVE_METADATA_KEYS = {
    "apikey",
    "auth",
    "authorization",
    "base64",
    "binary",
    "blob",
    "browserprofile",
    "cookie",
    "cookies",
    "oauthtoken",
    "password",
    "refreshtoken",
    "secret",
    "signedurl",
    "storagestate",
    "media",
    "mediablob",
    "token",
    "accesstoken",
}

_SENSITIVE_METADATA_SUFFIXES = (
    "accesskey",
    "apikey",
    "cookie",
    "credential",
    "credentials",
    "password",
    "privatekey",
    "secret",
    "secretkey",
    "signedurl",
    "token",
)

_GOAL_1_SENSITIVE_METADATA_KEYS = {
    "apikey",
    "auth",
    "authorization",
    "browserprofile",
    "cookie",
    "cookies",
    "oauthtoken",
    "password",
    "refreshtoken",
    "secret",
    "signedurl",
    "storagestate",
    "token",
    "accesstoken",
}

_GOAL_1_SIGNED_URL_MARKERS = (
    "?x-amz-signature=",
    "&x-amz-signature=",
    "?x-goog-signature=",
    "&x-goog-signature=",
    "?signature=",
    "&signature=",
    "?sig=",
    "&sig=",
)

_SIGNED_URL_MARKERS = (
    "?x-amz-signature=",
    "&x-amz-signature=",
    "?x-goog-signature=",
    "&x-goog-signature=",
    "?signature=",
    "&signature=",
    "?sig=",
    "&sig=",
    "data:image/",
    "data:audio/",
    "data:video/",
    "data:application/octet-stream",
    "-----begin private key-----",
    "authorization:",
    "bearer ",
    "api_key=",
    "apikey=",
    "cookie=",
    "password=",
    "secret=",
    "token=",
)


def _contains_sensitive_key(value: JsonValue) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = "".join(character for character in key.casefold() if character.isalnum())
            if (
                normalized in _SENSITIVE_METADATA_KEYS
                or normalized.endswith(_SENSITIVE_METADATA_SUFFIXES)
                or _contains_sensitive_key(child)
            ):
                return True
    if isinstance(value, list):
        return any(_contains_sensitive_key(child) for child in value)
    return False


def _contains_sensitive_value(value: JsonValue) -> bool:
    if isinstance(value, str):
        decoded = value
        for _ in range(3):
            decoded_value = unquote(decoded)
            if decoded_value == decoded:
                break
            decoded = decoded_value
        normalized = decoded.casefold()
        return (
            any(marker in normalized for marker in _SIGNED_URL_MARKERS)
            or _JWT_LIKE_VALUE.fullmatch(decoded) is not None
            or _API_KEY_LIKE_VALUE.search(decoded) is not None
        )
    if isinstance(value, dict):
        return any(_contains_sensitive_value(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_sensitive_value(child) for child in value)
    return False


def _contains_non_finite_number(value: JsonValue) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_contains_non_finite_number(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_non_finite_number(child) for child in value)
    return False


def _contains_embedded_base64(value: JsonValue) -> bool:
    if isinstance(value, str):
        return _BASE64_LIKE_VALUE.fullmatch(value) is not None or value.startswith(
            _MEDIA_BASE64_PREFIXES
        )
    if isinstance(value, dict):
        return any(_contains_embedded_base64(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_embedded_base64(child) for child in value)
    return False


def _goal_1_contains_sensitive_key(value: JsonValue) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = "".join(character for character in key.casefold() if character.isalnum())
            if (
                normalized in _GOAL_1_SENSITIVE_METADATA_KEYS
                or normalized.endswith(_SENSITIVE_METADATA_SUFFIXES)
                or _goal_1_contains_sensitive_key(child)
            ):
                return True
    if isinstance(value, list):
        return any(_goal_1_contains_sensitive_key(child) for child in value)
    return False


def _goal_1_contains_signed_url(value: JsonValue) -> bool:
    if isinstance(value, str):
        normalized = value.casefold()
        return any(marker in normalized for marker in _GOAL_1_SIGNED_URL_MARKERS)
    if isinstance(value, dict):
        return any(_goal_1_contains_signed_url(child) for child in value.values())
    if isinstance(value, list):
        return any(_goal_1_contains_signed_url(child) for child in value)
    return False


def validate_goal_1_campaign_metadata(value: JsonValue, field_name: str) -> None:
    """Preserve the released Goal 1 Campaign metadata contract exactly."""
    if _goal_1_contains_sensitive_key(value):
        raise ValueError(f"{field_name} contains a forbidden sensitive key")
    if _goal_1_contains_signed_url(value):
        raise ValueError(f"{field_name} contains forbidden sensitive data")
    if _contains_non_finite_number(value):
        raise ValueError(f"{field_name} contains a non-finite number")


def validate_safe_metadata(value: JsonValue, field_name: str) -> None:
    """Reject metadata that is unsafe to persist or serialize as strict JSON."""
    if _contains_non_finite_number(value):
        raise ValueError(f"{field_name} contains a non-finite number")
    serialized_size = len(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    if serialized_size > MAX_SAFE_METADATA_BYTES:
        raise ValueError(f"{field_name} exceeds the safe metadata size limit")
    if _contains_sensitive_key(value):
        raise ValueError(f"{field_name} contains a forbidden sensitive key")
    if _contains_sensitive_value(value):
        raise ValueError(f"{field_name} contains forbidden sensitive data")
    if _contains_embedded_base64(value):
        raise ValueError(f"{field_name} contains embedded base64 data")


def validate_safe_error_message(value: str, field_name: str = "error_message") -> None:
    if len(value) > 512:
        raise ValueError(f"{field_name} must not exceed 512 characters")
    if "\n" in value or "\r" in value:
        raise ValueError(f"{field_name} must be a single sanitized line")
    validate_safe_metadata({"message": value}, "error")
    normalized = value.casefold()
    if any(
        marker in normalized
        for marker in (
            "api key",
            "apikey",
            "authorization:",
            "bearer ",
            "cookie=",
            "credential=",
            "password=",
            "secret=",
            "token=",
            "\\users\\",
            "/home/",
        )
    ):
        raise ValueError(f"{field_name} contains unsafe diagnostic details")


def validate_safe_identifier(value: str, field_name: str, *, max_length: int) -> str:
    if not value or len(value) > max_length or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a safe identifier")
    normalized_segments = {
        segment.casefold().replace("-", "")
        for segment in re.split(r"[._:-]+", value)
        if segment
    }
    if normalized_segments.intersection(_SENSITIVE_IDENTIFIER_SEGMENTS):
        raise ValueError(f"{field_name} contains a sensitive marker")
    compact = "".join(character for character in value.casefold() if character.isalnum())
    if any(marker in compact for marker in _SENSITIVE_IDENTIFIER_SEGMENTS):
        raise ValueError(f"{field_name} contains a sensitive marker")
    return value
