from datetime import date, timedelta
from pathlib import Path

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models import Model

from daykeep.models import DayMeta, DayRecord, Moment, Mood
from daykeep.store import DayState, list_days, load_record, read_entry_text, render_markdown

Deps = DayState

WEEKDAYS = "一二三四五六日"


def next_moment_id(record: DayRecord) -> int:
    return max((m.id for m in record.moments), default=0) + 1


def upsert(
    record: DayRecord,
    id: int | None,
    time: str | None,
    title: str,
    content: str,
    mood: Mood | None,
) -> Moment:
    if id is not None:
        for moment in record.moments:
            if moment.id == id:
                moment.time = time
                moment.title = title
                moment.content = content
                moment.mood = mood
                return moment
    moment = Moment(
        id=id or next_moment_id(record), time=time, title=title, content=content, mood=mood
    )
    record.moments.append(moment)
    return moment


def drop(record: DayRecord, id: int) -> Moment:
    for moment in record.moments:
        if moment.id == id:
            record.moments.remove(moment)
            return moment
    raise KeyError(id)


def recap_block(home: Path, today: date, days: int = 7) -> str:
    lines = []
    for offset in range(1, days + 1):
        d = today - timedelta(days=offset)
        record = load_record(home, d)
        if record is None or (not record.moments and not record.meta.summary):
            continue
        parts = [d.isoformat()]
        parts.append(record.meta.title or "（无标题）")
        if record.meta.mood:
            parts.append(record.meta.mood)
        detail = record.meta.summary or "；".join(m.title for m in record.moments)
        lines.append(f"- {' ｜ '.join(parts)}：{detail}")
    if not lines:
        return ""
    return "最近几天的记录：\n" + "\n".join(lines)


def search_in_messages(messages: list[ModelMessage], query: str, max_hits: int = 5) -> str:
    hits = []
    for i, message in enumerate(messages):
        if not isinstance(message, ModelRequest):
            continue
        text = "\n".join(
            part.content
            for part in message.parts
            if isinstance(part, UserPromptPart) and isinstance(part.content, str)
        )
        if query and query.lower() in text.lower():
            reply = ""
            if i + 1 < len(messages):
                reply = _response_text(messages[i + 1])
            hits.append(f"用户：{text}\n你：{reply}")
            if len(hits) >= max_hits:
                break
    if not hits:
        return f"未找到与「{query}」相符的内容。"
    return "\n\n".join(hits)


def _response_text(message: ModelMessage) -> str:
    parts = [getattr(p, "content", "") for p in message.parts]
    return "\n".join(p for p in parts if isinstance(p, str))


def search_in_entries(
    home: Path, today: date, query: str, days_back: int = 30, max_hits: int = 5
) -> str:
    hits = []
    for d in list_days(home):
        if d >= today or (today - d).days > days_back:
            continue
        text = read_entry_text(home, d)
        if not text or query.lower() not in text.lower():
            continue
        index = text.lower().index(query.lower())
        snippet = text[max(0, index - 80) : index + 120].replace("\n", " ")
        hits.append(f"{d.isoformat()}：…{snippet}…")
        if len(hits) >= max_hits:
            break
    if not hits:
        return f"未找到与「{query}」相符的过往记录。"
    return "\n".join(hits)


def instructions(recap: str, today: date) -> str:
    weekday = WEEKDAYS[today.weekday()]
    return f"""\
你是 DayKeep，陪用户记录一天的朋友。

怎么聊：像朋友一样自然，先回应再说别的，不要审问式地连问。用户说到值得记的事，就把它记下来，不用问"要不要记"，也不用在回复里声明"我记下了"——界面会显示。
怎么记：调用 upsert_moment。同一件事的后续补充一定带上原来的 id 去更新，不要新建一条。用户说"这个别记"就调用 delete_moment。对这一天的整体感受有结论时调用 set_day_meta 写 summary。
不确定时怎么办：用户提到你不确定的内容（"那个"、"刚才说的"）时，先用 search_today_chat 搜；聊到过去几天的事，先看下面的回顾，需要细节用 read_entry，找具体话题用 search_entries。搜不到就直接问，不要编。

今天的日期是 {today.isoformat()}（星期{weekday}）。今天的日记只有这一篇，需要看已经记了什么就用 read_today_entry。
{recap}"""


def build_agent(model: Model) -> Agent[Deps, str]:
    a: Agent[Deps, str] = Agent(model, deps_type=Deps)

    @a.instructions
    async def with_recap(ctx: RunContext[Deps]) -> str:
        return instructions(recap_block(ctx.deps.home, ctx.deps.date), ctx.deps.date)

    @a.tool
    async def upsert_moment(
        ctx: RunContext[Deps],
        title: str,
        content: str,
        id: int | None = None,
        time: str | None = None,
        mood: Mood | None = None,
    ) -> str:
        """记录或更新今天日记里的一件事。补充已有的事必须传 id。"""
        moment = upsert(ctx.deps.record, id, time, title, content, mood)
        ctx.deps.save()
        return f"已记录 #{moment.id} {moment.title}"

    @a.tool
    async def delete_moment(ctx: RunContext[Deps], id: int) -> str:
        """从今天的日记里删掉一条记录。"""
        try:
            moment = drop(ctx.deps.record, id)
        except KeyError:
            existing = "、".join(f"#{m.id} {m.title}" for m in ctx.deps.record.moments) or "无"
            raise ModelRetry(f"没有 id={id} 的记录。现有的记录：{existing}") from None
        ctx.deps.save()
        return f"已删除 #{moment.id} {moment.title}"

    @a.tool
    async def set_day_meta(
        ctx: RunContext[Deps],
        title: str | None = None,
        mood: Mood | None = None,
        tags: list[str] | None = None,
        summary: str | None = None,
    ) -> str:
        """更新今天日记的标题、整体心情、标签或一天总结。只更新传进来的字段。"""
        meta: DayMeta = ctx.deps.record.meta
        if title is not None:
            meta.title = title
        if mood is not None:
            meta.mood = mood
        if tags is not None:
            meta.tags = tags
        if summary is not None:
            meta.summary = summary
        ctx.deps.save()
        return "已更新今天日记的整体信息"

    @a.tool
    async def read_today_entry(ctx: RunContext[Deps]) -> str:
        """读取今天日记的完整内容，每条记录都带 id。"""
        return render_markdown(ctx.deps.record)

    @a.tool
    async def search_today_chat(ctx: RunContext[Deps], query: str) -> str:
        """在今天完整的对话记录里搜索关键词，返回命中的整轮原文。"""
        return search_in_messages(ctx.deps.messages, query)

    @a.tool
    async def read_entry(ctx: RunContext[Deps], date: date) -> str:
        """读取某一天日记的完整内容。"""
        text = read_entry_text(ctx.deps.home, date)
        return text or f"{date.isoformat()} 那天没有记录。"

    @a.tool
    async def search_entries(ctx: RunContext[Deps], query: str, days_back: int = 30) -> str:
        """在过去若干天的日记正文里搜索关键词。"""
        return search_in_entries(ctx.deps.home, ctx.deps.date, query, days_back)

    return a
