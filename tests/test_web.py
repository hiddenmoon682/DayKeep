from pathlib import Path

import httpx
from pydantic_ai.models.test import TestModel

from daykeep import server
from daykeep.config import AppConfig, ModelConfig, Settings


def client_for(tmp_path: Path) -> httpx.AsyncClient:
    settings = Settings(
        model=ModelConfig(base_url="https://x/v1", api_key="k", name="m"),
        app=AppConfig(home=tmp_path, port=0, rollover_hour=4),
    )
    app = server.create_app(settings, TestModel())
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_index_is_served(tmp_path: Path):
    async with client_for(tmp_path) as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "/static/app.js" in response.text


async def test_static_assets_are_served(tmp_path: Path):
    async with client_for(tmp_path) as client:
        js = await client.get("/static/app.js")
        css = await client.get("/static/style.css")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert css.status_code == 200 and "css" in css.headers["content-type"]


def test_build_model_reads_settings(tmp_path: Path):
    from daykeep.__main__ import build_model

    settings = Settings(
        model=ModelConfig(base_url="https://x/v1", api_key="k", name="gpt-4o-mini"),
        app=AppConfig(home=tmp_path),
    )
    model = build_model(settings)
    assert "gpt-4o-mini" in str(model.model_name)
