from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

_ENV_VAR_RE = re.compile(r"\$\{(\w+)\}")
_CONFIG: dict[str, Any] | None = None


def _resolve_env_vars(value: Any) -> Any:
    """Replace ${VAR} placeholders with environment variable values."""
    if isinstance(value, str):
        def _replacer(match: re.Match) -> str:
            var_name = match.group(1)
            env_val = os.environ.get(var_name)
            if env_val is None:
                raise ValueError(
                    f"Environment variable '{var_name}' is not set. "
                    f"Set it before running: export {var_name}=<value>"
                )
            return env_val
        return _ENV_VAR_RE.sub(_replacer, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]
    return value


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load and cache config from YAML, resolving env-var placeholders."""
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG

    if path is None:
        path = Path(__file__).resolve().parent.parent / "config.yaml"
    else:
        path = Path(path)

    with open(path) as f:
        raw = yaml.safe_load(f)

    _CONFIG = _resolve_env_vars(raw)
    return _CONFIG


def get(key: str) -> Any:
    """Shortcut to fetch a single top-level config value."""
    cfg = load_config()
    if key not in cfg:
        raise KeyError(f"Missing config key: '{key}'")
    return cfg[key]
