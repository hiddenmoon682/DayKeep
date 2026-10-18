from pathlib import Path

from daykeep import config


def test_generates_template_when_missing(tmp_path: Path):
    path = tmp_path / "config.toml"
    settings = config.load(path)
    assert path.exists()
    assert "[model]" in path.read_text(encoding="utf-8")
    assert settings.model.name == "deepseek-chat"
    assert settings.model.api_key == ""


def test_reads_values(tmp_path: Path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[model]\nbase_url = "https://x/v1"\napi_key = "k"\nname = "m"\n'
        "\n[app]\nport = 9999\nrollover_hour = 5\n",
        encoding="utf-8",
    )
    settings = config.load(path)
    assert settings.model.api_key == "k"
    assert settings.app.port == 9999
    assert settings.app.rollover_hour == 5


def test_home_defaults_under_user_home(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DAYKEEP_HOME", raising=False)
    path = tmp_path / "config.toml"
    settings = config.load(path)
    assert settings.app.home == Path.home() / "DayKeep"


def test_env_home_overrides(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DAYKEEP_HOME", str(tmp_path / "elsewhere"))
    settings = config.load(tmp_path / "config.toml")
    assert settings.app.home == tmp_path / "elsewhere"


def test_env_api_key_overrides(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DAYKEEP_API_KEY", "from-env")
    settings = config.load(tmp_path / "config.toml")
    assert settings.model.api_key == "from-env"
