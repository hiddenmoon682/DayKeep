import asyncio
import json
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from markdown_it import MarkdownIt
from pydantic_ai import FunctionToolResultEvent, PartDeltaEvent, PartStartEvent, TextPartDelta
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from daykeep import store, window
from daykeep.agent import build_agent
from daykeep.clock import logical_date, stamp, strip_stamp
from daykeep.config import Settings

WEB_DIR = Path(__file__).parent / "web"
MUTATING_TOOLS = {"upsert_moment", "delete_moment", "set_day_meta"}
_md = MarkdownIt("commonmark", {"html": False})


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def render_markdown_html(text: str) -> str:
    return _md.render(text)


def transcript(messages: list[ModelMessage]) -> list[dict]:
    out = []
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    out.append({"role": "user", "text": strip_stamp(part.content)})
        elif isinstance(message, ModelResponse):
            text = "\n".join(
                part.content
                for part in message.parts
                if isinstance(getattr(part, "content", None), str)
            )
            if text:
                out.append({"role": "assistant", "text": text})
    return out


def create_app(settings: Settings, model) -> FastAPI:
    app = FastAPI()
    agent_instance = build_agent(model)
    lock = asyncio.Lock()

    def today_state():
        d = logical_date(datetime.now(), settings.app.rollover_hour)
        return d, store.DayState.load(settings.app.home, d)

    @app.get("/")
    async def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/api/today")
    async def today():
        d, state = today_state()
        return {
            "date": d.isoformat(),
            "transcript": transcript(state.messages),
            "entry_html": render_markdown_html(store.render_markdown(state.record)),
            "configured": bool(settings.model.api_key),
        }

    @app.get("/api/days")
    async def days():
        return [d.isoformat() for d in store.list_days(settings.app.home)]

    @app.get("/api/days/{day}")
    async def day_entry(day: str):
        try:
            d = date.fromisoformat(day)
        except ValueError:
            raise HTTPException(404) from None
        text = store.read_entry_text(settings.app.home, d)
        if text is None:
            raise HTTPException(404)
        return {"date": day, "entry_html": render_markdown_html(text)}

    @app.post("/api/chat")
    async def chat(payload: dict):
        text = (payload or {}).get("text", "")
        budget = window.resolve_budget(model.context_window, settings.model.budget)
        if window.estimate_tokens(text) > budget // 2:
            raise HTTPException(413, "这条太长了，分几次发")

        async def stream():
            # 读盘必须在锁内：否则两个并发的 chat 会各自读到同一份旧快照，
            # 后写的那次会把前一次的整轮对话覆盖掉（回合静默丢失）。
            async with lock:
                d, state = today_state()
                yield sse("day", {"date": d.isoformat()})
                if not settings.model.api_key:
                    yield sse(
                        "error",
                        {
                            "message": "还没有配置 API key，请填写配置里的 api_key 后重启",
                            "retryable": False,
                        },
                    )
                    return
                prompt = stamp(datetime.now()) + text
                candidates = [budget, budget // 2] if budget > 8_000 else [budget]
                for index, attempt_budget in enumerate(candidates):
                    window_messages, dropped = window.trim(state.messages, attempt_budget)
                    if index == 0 and dropped:
                        yield sse("trimmed", {"dropped_turns": dropped})
                    window_messages = window.with_notice(window_messages, state.record, dropped)
                    emitted = False
                    try:
                        # deps 始终带完整归档，message_history 只带裁剪后的窗口；
                        # 否则 search_today_chat 看不到被裁掉的原文。
                        async with agent_instance.run_stream_events(
                            prompt, message_history=window_messages, deps=state
                        ) as run:
                            async for event in run:
                                # 首个正文块随 PartStartEvent 到达，后续才是 PartDeltaEvent，
                                # 两者都要发，否则第一段文字会被丢掉。
                                if isinstance(event, PartStartEvent) and isinstance(
                                    event.part, TextPart
                                ):
                                    if event.part.content:
                                        emitted = True
                                        yield sse("delta", {"text": event.part.content})
                                elif isinstance(event, PartDeltaEvent) and isinstance(
                                    event.delta, TextPartDelta
                                ):
                                    emitted = True
                                    yield sse("delta", {"text": event.delta.content_delta})
                                elif isinstance(event, FunctionToolResultEvent):
                                    # 工具已经落盘（每个 mutating 工具在体内 save），
                                    # 这一轮就绝不能重试，否则副作用会执行两遍。
                                    emitted = True
                                    label = str(getattr(event.part, "content", ""))
                                    yield sse("tool", {"label": label})
                                    if event.part.tool_name in MUTATING_TOOLS:
                                        yield sse(
                                            "entry",
                                            {
                                                "html": render_markdown_html(
                                                    store.render_markdown(state.record)
                                                )
                                            },
                                        )
                        state.messages.extend(run.new_messages())
                        state.save()
                        break
                    except Exception as exc:  # noqa: BLE001
                        # 已经吐过内容（文字或工具结果）就不再重试，
                        # 否则用户会看到重复内容、工具副作用也会执行两遍
                        if index < len(candidates) - 1 and not emitted:
                            continue
                        yield sse("error", {"message": f"调用模型失败：{exc}", "retryable": True})
                        return
                yield sse("done", {})

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app
