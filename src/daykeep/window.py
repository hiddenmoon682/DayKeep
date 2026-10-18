from collections.abc import Sequence

from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart

from daykeep.models import DayRecord
from daykeep.store import render_markdown

MIN_TURNS = 30
CHUNK_TURNS = 20
TARGET_RATIO = 0.7
HEADROOM = 32_000


def estimate_tokens(text: str) -> int:
    """CJK 每字符算 1 个 token，其余每 4 字符算 1 个。故意不用 tokenizer：确定、可测。"""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk) // 4


def resolve_budget(context_window: int | None, explicit_budget: int | None) -> int:
    if explicit_budget is not None:
        return explicit_budget
    if context_window is not None:
        return context_window - HEADROOM
    raise ValueError("既没有探测到 context_window，也没有配置 budget；请在 config.toml 里补一个")


def turn_starts(messages: Sequence[ModelMessage]) -> list[int]:
    """一轮的起点 = 带用户发言的 ModelRequest 的位置。"""
    starts = []
    for i, message in enumerate(messages):
        if isinstance(message, ModelRequest) and any(
            isinstance(part, UserPromptPart) for part in message.parts
        ):
            starts.append(i)
    return starts


def size_of(messages: Sequence[ModelMessage]) -> int:
    """优先用 provider 上报的 total_tokens；拿不到才回退字符估算。"""
    for message in reversed(messages):
        usage = getattr(message, "usage", None)
        if usage is not None and usage.total_tokens:
            return usage.total_tokens
    return sum(estimate_tokens(_text_of(m)) for m in messages)


def _text_of(message: ModelMessage) -> str:
    parts = []
    for part in message.parts:
        content = getattr(part, "content", None)
        if isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)


def trim(
    messages: Sequence[ModelMessage],
    budget: int,
    min_turns: int = MIN_TURNS,
    target_ratio: float = TARGET_RATIO,
    chunk: int = CHUNK_TURNS,
) -> tuple[list[ModelMessage], int]:
    """超预算时从最老的一端丢整轮。

    丢的轮数必须是 chunk 的整数倍 —— 这条是本函数唯一容易被写错的地方。
    因为入参始终是完整归档，若按"能塞进 target 的最小起点"来丢，归档每长一轮、
    起点就往后挪一轮，窗口前缀每轮都变，prompt cache 永远命中不了。
    对齐到固定块之后，起点在一次裁剪后的若干轮内钉死不动，前缀才稳定。

    只能整块地丢，凑不满一整块就一块都不丢：宁可让这一次请求超出 budget，
    也不能让起点每轮挪一轮。budget 比模型真实窗口低 32k，超出 budget 不等于超出窗口。
    """
    if size_of(messages) <= budget:
        return list(messages), 0
    target = int(budget * target_ratio)
    starts = turn_starts(messages)
    if len(starts) <= 1:
        return list(messages), 0

    max_chunks = (len(starts) - min_turns) // chunk
    drop_turns = max_chunks * chunk
    for k in range(1, max_chunks + 1):
        if size_of(messages[starts[k * chunk] :]) <= target:
            drop_turns = k * chunk
            break

    return list(messages[starts[drop_turns] :]), drop_turns


def with_notice(
    messages: Sequence[ModelMessage], record: DayRecord, dropped: int
) -> list[ModelMessage]:
    if not dropped:
        return list(messages)

    notice = (
        f"（本日更早的 {dropped} 轮对话已移出你的上下文，完整原文仍在本地的当天归档里，"
        f"需要时用 search_today_chat 查找。）\n\n当前日记内容：\n{render_markdown(record)}"
    )
    first = messages[0]
    assert isinstance(first, ModelRequest)
    head = ModelRequest(parts=[UserPromptPart(content=notice), *first.parts])
    return [head, *messages[1:]]
