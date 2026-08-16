"""应用配置加载。"""

from __future__ import annotations

import os
from pathlib import Path


def load_config(config_path: str | None = None) -> dict:
    """加载配置文件。"""
    if config_path is None:
        config_path = str(Path(__file__).parent.parent / "config.yaml")

    import yaml

    with open(config_path) as f:
        config = yaml.safe_load(f)

    return _expand_env_vars(config)


def _expand_env_vars(obj):
    """递归展开配置中的环境变量。"""
    if isinstance(obj, str):
        if obj.startswith(") and obj.endswith("):
            return os.getenv(obj[2:-1], "")
        return obj
    if isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_vars(item) for item in obj]
    return obj
