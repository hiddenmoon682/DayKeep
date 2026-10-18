import os
import tomllib
from pathlib import Path

from pydantic import BaseModel

TEMPLATE = """\
[model]
base_url = "https://api.deepseek.com"
api_key = ""
name = "deepseek-chat"

# 下面两项通常不用填：上下文长度由 Pydantic AI 自动探测（model.context_window）。
# 只有模型不被认识，或者想主动限制每轮发送量时才覆盖。
# context_window = 1000000
# budget = 900000

[app]
port = 8765
rollover_hour = 4
"""


class ModelConfig(BaseModel):
    base_url: str
    api_key: str = ""
    name: str
    context_window: int | None = None
    budget: int | None = None


class AppConfig(BaseModel):
    home: Path = Path.home() / "DayKeep"
    port: int = 8765
    rollover_hour: int = 4


class Settings(BaseModel):
    model: ModelConfig
    app: AppConfig


def default_path() -> Path:
    return Path.home() / "DayKeep" / "config.toml"


def load(path: Path | None = None) -> Settings:
    path = path or default_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(TEMPLATE, encoding="utf-8")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    settings = Settings.model_validate(data)
    if override := os.environ.get("DAYKEEP_HOME"):
        settings.app.home = Path(override)
    return settings
