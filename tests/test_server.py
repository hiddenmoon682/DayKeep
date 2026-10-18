from datetime import datetime, timedelta
from pathlib import Path

import httpx
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from daykeep import clock, server, store
from daykeep.config import AppConfig, ModelConfig, Settings


def settings_for(tmp_path: Path, api_key: str = "k", budget: int = 100_000) -> Settings:
    return Settings(
        model=ModelConfig(base_url="https://x/v1", api_key=api_key, name="m", budget=budget),
        app=AppConfig(home=tmp_path, port=0, rollover_hour=4),
    )


def streaming(fn):
    """pydantic-ai 2.x 的 FunctionModel 只给了 function 时无法响应流式请求，
    这里把同一个 function 适配成 stream_function，逐字吐出让测试走真正的 delta 路径。"""

    async def stream_fn(messages, info):
        response = await fn(messages, info)
        for part in response.parts:
            content = getattr(part, "content", None)
            if isinstance(content, str):
                for ch in content:
                    yield ch

    return stream_fn


def echo_model(*texts: str):
    queue = list(texts) or ["嗨"]
    calls = []

    async def fn(messages, info):
        calls.append(messages)
        return ModelResponse(parts=[TextPart(queue[min(len(calls), len(queue)) - 1])])

    model = FunctionModel(fn, stream_function=streaming(fn))
    model.calls = calls
    return model


def parse_sse(body: str) -> list[tuple[str, str]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in lines:
            events.append((lines["event"], lines.get("data", "")))
    return events


def client_for(settings: Settings, model) -> httpx.AsyncClient:
    app = server.create_app(settings, model)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# ---------- 纯函数 ----------


def test_sse_format():
    assert server.sse("delta", {"text": "嗨"}) == 'event: delta\ndata: {"text": "嗨"}\n\n'


def test_strip_stamp():
    assert clock.strip_stamp("[21:30] 今天很累") == "今天很累"
    assert clock.strip_stamp("没有前缀") == "没有前缀"


def test_markdown_html_escapes_raw_html():
    html = server.render_markdown_html("<script>alert(1)</script>\n\n**粗**")
    assert "<script>" not in html
    assert "<strong>粗</strong>" in html


def test_transcript_uses_model_messages():
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    messages = [
        ModelRequest(parts=[UserPromptPart(content="[21:30] 今天很累")]),
        ModelResponse(parts=[TextPart("辛苦了。")]),
    ]
    assert server.transcript(messages) == [
        {"role": "user", "text": "今天很累"},
        {"role": "assistant", "text": "辛苦了。"},
    ]


# ---------- 接线 ----------


async def test_today_endpoint(tmp_path: Path):
    async with client_for(settings_for(tmp_path), TestModel()) as client:
        body = (await client.get("/api/today")).json()
    assert body["date"] == clock.logical_date(datetime.now()).isoformat()
    assert body["transcript"] == []
    assert body["configured"] is True


async def test_chat_streams_day_then_delta_then_done(tmp_path: Path):
    async with client_for(settings_for(tmp_path), echo_model("辛苦了。")) as client:
        response = await client.post("/api/chat", json={"text": "今天很累"})
        events = parse_sse(response.text)
    names = [name for name, _ in events]
    assert names[0] == "day"
    assert names[-1] == "done"
    assert "delta" in names


async def test_chat_persists_transcript(tmp_path: Path):
    async with client_for(settings_for(tmp_path), echo_model("辛苦了。")) as client:
        await client.post("/api/chat", json={"text": "今天很累"})
        body = (await client.get("/api/today")).json()
    assert body["transcript"][0] == {"role": "user", "text": "今天很累"}
    assert body["transcript"][1] == {"role": "assistant", "text": "辛苦了。"}


async def test_chat_without_api_key_reports_error_and_archives_nothing(tmp_path: Path):
    async with client_for(settings_for(tmp_path, api_key=""), echo_model("嗨")) as client:
        response = await client.post("/api/chat", json={"text": "你好"})
        events = parse_sse(response.text)
        today = (await client.get("/api/today")).json()
    assert events[-1][0] == "error"
    assert today["transcript"] == []


async def test_chat_rejects_oversized_message(tmp_path: Path):
    settings = settings_for(tmp_path, budget=1_000)
    async with client_for(settings, echo_model("嗨")) as client:
        response = await client.post("/api/chat", json={"text": "字" * 2_000})
    assert response.status_code == 413


async def test_chat_retries_once_with_half_budget_on_context_overflow(tmp_path: Path):
    calls = []

    async def fn(messages, info):
        calls.append(messages)
        if len(calls) == 1:
            raise RuntimeError("maximum context length is 8192 tokens")
        return ModelResponse(parts=[TextPart("好了。")])

    async with client_for(
        settings_for(tmp_path), FunctionModel(fn, stream_function=streaming(fn))
    ) as client:
        events = parse_sse((await client.post("/api/chat", json={"text": "你好"})).text)
    assert len(calls) == 2
    assert [name for name, _ in events][-1] == "done"


async def test_days_endpoints(tmp_path: Path):
    old = clock.logical_date(datetime.now()) - timedelta(days=2)
    state = store.DayState.load(tmp_path, old)
    state.save()
    async with client_for(settings_for(tmp_path), TestModel()) as client:
        days = (await client.get("/api/days")).json()
        one = await client.get(f"/api/days/{old.isoformat()}")
        missing = await client.get("/api/days/1999-01-01")
    assert days == [old.isoformat()]
    assert one.status_code == 200
    assert missing.status_code == 404
