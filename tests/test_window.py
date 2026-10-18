from datetime import date

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RequestUsage

from daykeep import window
from daykeep.models import DayMeta, DayRecord, Moment


def turn(text: str, reply: str = "嗯") -> list:
    """一轮对话 = 一条带用户发言的 ModelRequest + 一条 ModelResponse。"""
    return [
        ModelRequest(parts=[UserPromptPart(content=text)]),
        ModelResponse(parts=[TextPart(reply)]),
    ]


def many_turns(n: int, size: int = 200) -> list:
    out = []
    for i in range(n):
        out += turn(f"第{i}轮 " + "字" * size)
    return out


def test_estimate_tokens_cjk():
    assert window.estimate_tokens("汉字") == 2


def test_estimate_tokens_ascii():
    assert window.estimate_tokens("abcdefgh") == 2


def test_resolve_budget_prefers_explicit():
    assert window.resolve_budget(1_000_000, 50_000) == 50_000


def test_resolve_budget_from_window():
    assert window.resolve_budget(1_000_000, None) == 968_000


def test_resolve_budget_requires_something():
    with pytest.raises(ValueError):
        window.resolve_budget(None, None)


def test_size_of_uses_reported_tokens_when_present():
    messages = turn("你好")
    response = ModelResponse(
        parts=[TextPart("嗨")], usage=RequestUsage(input_tokens=10, output_tokens=5)
    )
    assert window.size_of([messages[0], response]) == 15


def test_no_trim_when_under_budget():
    messages = many_turns(5)
    trimmed, dropped = window.trim(messages, budget=100_000)
    assert trimmed == messages
    assert dropped == 0


def test_trim_starts_at_turn_boundary():
    messages = many_turns(200)
    trimmed, dropped = window.trim(messages, budget=20_000)
    assert dropped > 0
    assert isinstance(trimmed[0], ModelRequest)
    assert any(isinstance(p, UserPromptPart) for p in trimmed[0].parts)
    assert window.size_of(trimmed) <= int(20_000 * 0.7)


def test_trim_never_orphans_a_tool_call():
    """每一轮的 ModelRequest/ModelResponse 必须成对出现，不能从回复中间切开。"""
    messages = many_turns(200)
    trimmed, _ = window.trim(messages, budget=20_000)
    assert isinstance(trimmed[0], ModelRequest)
    assert len(trimmed) % 2 == 0


def test_trim_keeps_at_least_min_turns():
    """最近 30 轮落在 target 与 budget 之间时，下限生效：只丢到剩 30 轮。"""
    messages = many_turns(200, size=300)
    trimmed, dropped = window.trim(messages, budget=10_000)
    assert dropped == 200 - window.MIN_TURNS
    assert len(window.turn_starts(trimmed)) == window.MIN_TURNS


def test_trim_drops_whole_chunk_for_cache_stability():
    """连续两次裁剪之间，前一轮的窗口是后一轮窗口的前缀。"""
    messages = many_turns(200)
    first, _ = window.trim(messages, budget=20_000)
    grown = messages + turn("新的一轮")
    second, _ = window.trim(grown, budget=20_000)
    assert second[: len(first)] == first


def test_trim_chunk_alignment_holds_for_full_size_growth():
    """追加一个与其它轮同规模的整轮后，丢的轮数必须一动不动。

    上一条用 5 个 token 的 turn("新的一轮") 追加，恰好让"能塞进 target 的轮数"
    多出一轮，把起点后移与轮数增加相互抵消，于是连"最小起点"的错实现也能通过。
    换成整轮才能锁住块对齐这条不变量：错实现会 132->133，对齐则停在 140。
    """
    messages = many_turns(200)
    first, dropped_first = window.trim(messages, budget=20_000)
    grown = messages + turn("新的一轮 " + "字" * 200)
    second, dropped_second = window.trim(grown, budget=20_000)
    assert dropped_first % window.CHUNK_TURNS == 0
    assert dropped_second == dropped_first
    assert second[: len(first)] == first


def test_with_notice_noop_when_nothing_dropped():
    messages = many_turns(3)
    assert window.with_notice(messages, DayRecord(date=date(2026, 10, 17)), 0) == messages


def test_with_notice_includes_entry_text():
    record = DayRecord(
        date=date(2026, 10, 17),
        meta=DayMeta(summary="今天很累。"),
        moments=[Moment(id=1, title="腾讯二面", content="问了网络。")],
    )
    messages = many_turns(3)
    out = window.with_notice(messages, record, 12)
    assert out[0] is not messages[0]
    assert "12 轮" in out[0].parts[0].content
    assert "腾讯二面" in out[0].parts[0].content
    assert out[1:] == messages[1:]
