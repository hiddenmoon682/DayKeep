import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
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


# ---------- 回归：Review Finding A / B ----------


async def test_concurrent_chats_keep_both_turns(tmp_path: Path):
    """Finding A：读盘必须在锁内，否则并发的两次 chat 会互相覆盖、丢掉一整轮。

    非流式 function 里 await 一下，让第一个请求持锁期间第二个请求有时间跑完
    handler 主体——修复前它会在锁外读到旧快照，保存时把第一个请求的回合盖掉。
    """

    async def fn(messages, info):
        await asyncio.sleep(0.1)
        return ModelResponse(parts=[TextPart("辛苦了。")])

    model = FunctionModel(fn, stream_function=streaming(fn))
    async with client_for(settings_for(tmp_path), model) as client:
        await asyncio.gather(
            client.post("/api/chat", json={"text": "今天很累"}),
            client.post("/api/chat", json={"text": "今天加班"}),
        )
        today = (await client.get("/api/today")).json()

    users = [m["text"] for m in today["transcript"] if m["role"] == "user"]
    assert sorted(users) == ["今天加班", "今天很累"]


async def test_chat_does_not_retry_after_mutating_tool_already_ran(tmp_path: Path):
    """Finding B：工具已经落盘后再失败，绝不能重试整轮（否则重复写入）。"""
    stream_calls = []

    async def fn(messages, info):
        return ModelResponse(parts=[TextPart("好了。")])

    async def stream_fn(messages, info):
        stream_calls.append(messages)
        if len(stream_calls) == 1:
            # 第一轮：模型只调工具（不吐字），工具在体内立刻写盘
            yield {
                0: DeltaToolCall(
                    name="upsert_moment",
                    json_args='{"title": "腾讯二面", "content": "聊了 40 分钟"}',
                    tool_call_id="call-1",
                )
            }
            return
        # 工具已经跑完，provider 这才失败
        raise RuntimeError("maximum context length is 8192 tokens")

    model = FunctionModel(fn, stream_function=stream_fn)
    async with client_for(settings_for(tmp_path), model) as client:
        events = parse_sse((await client.post("/api/chat", json={"text": "记一下腾讯二面"})).text)

    state = store.DayState.load(tmp_path, clock.logical_date(datetime.now()))
    names = [name for name, _ in events]
    assert names[-1] == "error"
    assert "done" not in names
    # 一次 agent run 内两次模型调用（工具调用 + 失败），没有第二次尝试
    assert len(stream_calls) == 2
    assert [m.title for m in state.record.moments] == ["腾讯二面"]
