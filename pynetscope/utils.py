"""Utility helpers used across pyNetScope."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-api-key", "proxy-authorization"}
SENSITIVE_QUERY_KEYS = {"token", "access_token", "refresh_token", "apikey", "api_key", "password", "secret"}
SENSITIVE_PAYLOAD_KEYS = SENSITIVE_QUERY_KEYS | {"authorization", "cookie", "set-cookie"}

EndpointNormalizer = Callable[[str, str], str]


def normalize_endpoint(method: str, url: str) -> str:
    parts = urlsplit(url)
    path = re.sub(r"/\d+(?=/|$)", "/:id", parts.path or "/")
    return f"{method.upper()} {parts.netloc}{path}"


def endpoint_normalizer(
    *,
    collapse_numeric_ids: bool = True,
    collapse_uuid_ids: bool = True,
    include_query_keys: bool = False,
    rules: list[tuple[str, str]] | None = None,
) -> EndpointNormalizer:
    """Build a configurable endpoint normalizer."""

    uuid_pattern = re.compile(r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?=/|$)")

    compiled_rules = []
    if rules:
        for pattern, replacement in rules:
            compiled_rules.append((re.compile(pattern), replacement))

    def normalizer(method: str, url: str) -> str:
        parts = urlsplit(url)
        path = parts.path or "/"

        for pattern, replacement in compiled_rules:
            path = pattern.sub(replacement, path)

        if collapse_uuid_ids:
            path = uuid_pattern.sub("/:uuid", path)
        if collapse_numeric_ids:
            path = re.sub(r"/\d+(?=/|$)", "/:id", path)
        query = ""
        if include_query_keys and parts.query:
            keys = sorted(key for key, _ in parse_qsl(parts.query, keep_blank_values=True))
            query = "?" + "&".join(keys)
        return f"{method.upper()} {parts.netloc}{path}{query}"

    return normalizer


def scrub_headers(headers: dict[str, str] | None) -> dict[str, str]:
    safe = {}
    for key, value in (headers or {}).items():
        safe[key] = "[redacted]" if key.lower() in SENSITIVE_HEADERS else str(value)
    return safe


_pii_config = None

def get_pii_config() -> tuple[str, list[re.Pattern]]:
    global _pii_config
    if _pii_config is not None:
        return _pii_config

    import os
    mode = os.environ.get("PYNETSCOPE_PII_MODE", "redact").lower()
    patterns_raw = os.environ.get("PYNETSCOPE_PII_PATTERNS", "")
    patterns = []
    if patterns_raw:
        for p in patterns_raw.split(","):
            p = p.strip()
            if p:
                try:
                    patterns.append(re.compile(p))
                except Exception:
                    pass

    if os.path.exists("pynetscope.toml"):
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError:
                tomllib = None
        if tomllib:
            try:
                with open("pynetscope.toml", "rb") as f:
                    toml_cfg = tomllib.load(f)
                    if "pii_mode" in toml_cfg:
                        mode = str(toml_cfg["pii_mode"]).lower()
                    if "pii_patterns" in toml_cfg:
                        cfg_pats = toml_cfg["pii_patterns"]
                        if isinstance(cfg_pats, list):
                            for p in cfg_pats:
                                try:
                                    patterns.append(re.compile(p))
                                except Exception:
                                    pass
                        elif isinstance(cfg_pats, str):
                            for p in cfg_pats.split(","):
                                p = p.strip()
                                if p:
                                    try:
                                        patterns.append(re.compile(p))
                                    except Exception:
                                        pass
            except Exception:
                pass

    _pii_config = (mode, patterns)
    return _pii_config


def _get_scrubbed_value(val: Any, mode: str) -> str:
    if mode == "hash":
        s = str(val)
        return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()[:16]
    elif mode == "mask":
        s = str(val)
        if len(s) <= 4:
            return "*" * len(s) if s else "*****"
        return s[:2] + "*" * 5 + s[-2:]
    else:
        return "[redacted]"


def _apply_regex_patterns(text: str, mode: str, patterns: list[re.Pattern]) -> str:
    for pat in patterns:
        def repl(match: re.Match) -> str:
            return _get_scrubbed_value(match.group(0), mode)
        text = pat.sub(repl, text)
    return text


def scrub_payload(value: Any, *, max_chars: int = 2_000) -> Any:
    """Redact common secrets from structured payloads and cap large values."""
    mode, patterns = get_pii_config()
    
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except Exception:
            pass

    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            scrubbed_parsed = _scrub_payload_value(parsed, mode, patterns)
            scrubbed = json.dumps(scrubbed_parsed)
        except Exception:
            if "=" in value and not value.startswith(("{", "[")):
                try:
                    parsed_q = parse_qsl(value, keep_blank_values=True)
                    if parsed_q:
                        scrubbed_q = []
                        for k, v in parsed_q:
                            if k.lower() in SENSITIVE_PAYLOAD_KEYS:
                                scrubbed_q.append((k, _get_scrubbed_value(v, mode)))
                            else:
                                v_str = str(v)
                                if patterns:
                                    v_str = _apply_regex_patterns(v_str, mode, patterns)
                                scrubbed_q.append((k, v_str))
                        scrubbed = urlencode(scrubbed_q)
                    else:
                        scrubbed = value
                except Exception:
                    scrubbed = value
            else:
                if patterns:
                    scrubbed = _apply_regex_patterns(value, mode, patterns)
                else:
                    scrubbed = value
    else:
        scrubbed = _scrub_payload_value(value, mode, patterns)

    if isinstance(scrubbed, (dict, list, tuple, int, float, bool)) or scrubbed is None:
        encoded = json.dumps(scrubbed, default=str)
        if len(encoded) <= max_chars:
            return scrubbed
        return encoded[:max_chars] + "...[truncated]"
    text = str(scrubbed)
    return text if len(text) <= max_chars else text[:max_chars] + "...[truncated]"


def _scrub_payload_value(value: Any, mode: str, patterns: list[re.Pattern]) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                _get_scrubbed_value(item, mode)
                if str(key).lower() in SENSITIVE_PAYLOAD_KEYS
                else _scrub_payload_value(item, mode, patterns)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub_payload_value(item, mode, patterns) for item in value]
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, str):
        if patterns:
            value = _apply_regex_patterns(value, mode, patterns)
        return value
    if patterns and value is not None:
        val_str = str(value)
        val_scrubbed = _apply_regex_patterns(val_str, mode, patterns)
        if val_scrubbed != val_str:
            return val_scrubbed
    return value


def scrub_url(url: str) -> str:
    parts = urlsplit(url)
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        query.append((key, "[redacted]" if key.lower() in SENSITIVE_QUERY_KEYS else value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def fingerprint(method: str, url: str) -> str:
    raw = normalize_endpoint(method, scrub_url(url)).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def to_plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: to_plain(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(item) for item in value]
    return value
