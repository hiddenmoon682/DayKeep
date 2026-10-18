from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from daykeep import agent, store
from daykeep.models import DayMeta, DayRecord, Moment

D = date(2026, 10, 17)


def make_state(tmp_path: Path) -> store.DayState:
    """已落盘的空日记。删除失败时没有任何 save，故断言前磁盘上必须先有一天。"""
    state = store.DayState.load(tmp_path, D)
    state.save()
    return state


def turn(text: str, reply: str = "嗯") -> list:
    """一轮对话 = 一条带用户发言的 ModelRequest + 一条 ModelResponse。"""
    return [
        ModelRequest(parts=[UserPromptPart(content=text)]),
        ModelResponse(parts=[TextPart(reply)]),
    ]


def scripted(*responses):
    calls = []

    async def fn(messages, info):
        calls.append(messages)
        return responses[min(len(calls), len(responses)) - 1]

    model = FunctionModel(fn)
    model.calls = calls
    return model


# ---------- 纯函数 ----------


def test_next_moment_id_empty():
    assert agent.next_moment_id(DayRecord(date=D)) == 1


def test_next_moment_id_after_existing():
    record = DayRecord(
        date=D, moments=[Moment(id=1, title="a", content="x"), Moment(id=5, title="b", content="y")]
    )
    assert agent.next_moment_id(record) == 6


def test_upsert_creates_with_given_id():
    record = DayRecord(date=D)
    moment = agent.upsert(record, None, "14:30", "腾讯二面", "问了网络。", "不错")
    assert moment.id == 1
    assert record.moments == [moment]


def test_upsert_updates_existing_instead_of_duplicating():
    record = DayRecord(date=D)
    agent.upsert(record, None, None, "腾讯二面", "问了网络。", None)
    agent.upsert(record, 1, None, "腾讯二面", "问了网络和操作系统。", "不错")
    assert len(record.moments) == 1
    assert record.moments[0].content == "问了网络和操作系统。"
    assert record.moments[0].mood == "不错"


def test_drop_removes_and_raises_on_missing():
    record = DayRecord(date=D, moments=[Moment(id=3, title="a", content="x")])
    assert agent.drop(record, 3).id == 3
    assert record.moments == []
    with pytest.raises(KeyError) as exc:
        agent.drop(record, 3)
    assert "3" in str(exc.value)


def test_search_in_messages_finds_turn(tmp_path: Path):
    messages = [
        ModelRequest(parts=[UserPromptPart(content="[09:10] 早上面了腾讯二面")]),
        ModelResponse(parts=[TextPart("感觉怎么样？")]),
        ModelRequest(parts=[UserPromptPart(content="[21:00] 晚上吃了火锅")]),
        ModelResponse(parts=[TextPart("不错啊")]),
    ]
    out = agent.search_in_messages(messages, "腾讯")
    assert "腾讯二面" in out
    assert "火锅" not in out


def test_search_in_messages_reports_miss():
    assert "未找到" in agent.search_in_messages([], "不存在的词")


def test_search_in_entries(tmp_path: Path):
    old = D - timedelta(days=3)
    state = store.DayState.load(tmp_path, old)
    state.record = DayRecord(
        date=old, moments=[Moment(id=1, title="实习投递", content="投了五家。")]
    )
    state.save()
    out = agent.search_in_entries(tmp_path, D, "实习")
    assert old.isoformat() in out
    assert "投了五家" in out


def test_search_in_entries_respects_days_back(tmp_path: Path):
    old = D - timedelta(days=40)
    state = store.DayState.load(tmp_path, old)
    state.record = DayRecord(
        date=old, moments=[Moment(id=1, title="实习投递", content="投了五家。")]
    )
    state.save()
    assert "未找到" in agent.search_in_entries(tmp_path, D, "实习", days_back=30)


def test_recap_block_lists_recent_days(tmp_path: Path):
    for offset, summary in ((1, "面试很累。"), (2, None)):
        d = D - timedelta(days=offset)
        state = store.DayState.load(tmp_path, d)
        state.record = DayRecord(
            date=d,
            meta=DayMeta(title=f"第{offset}天", mood="一般", summary=summary),
            moments=[Moment(id=1, title="小事", content="内容")],
        )
        state.save()
    block = agent.recap_block(tmp_path, D)
    assert "第1天" in block and "面试很累。" in block
    assert "第2天" in block and "小事" in block  # 没摘要时退化成 moment 标题
    assert D.isoformat() not in block  # 当天不参与回顾


def test_recap_block_empty_when_no_history(tmp_path: Path):
    assert agent.recap_block(tmp_path, D) == ""


def test_instructions_are_stable_and_contain_date():
    text = agent.instructions("（回顾）", D)
    assert D.isoformat() in text
    assert "（回顾）" in text
    assert agent.instructions("（回顾）", D) == text


# ---------- 经模型的接线测试 ----------


def test_upsert_tool_writes_to_disk(tmp_path: Path):
    model = scripted(
        ModelResponse(
            parts=[
                ToolCallPart(
                    "upsert_moment",
                    {
                        "id": None,
                        "time": "14:30",
                        "title": "腾讯二面",
                        "content": "问了网络。",
                        "mood": "不错",
                    },
                )
            ]
        ),
        ModelResponse(parts=[TextPart("记下了。")]),
    )
    a = agent.build_agent(model)
    state = make_state(tmp_path)
    a.run_sync("[21:30] 今天面了腾讯", deps=state)
    again = store.DayState.load(tmp_path, D)
    assert [m.title for m in again.record.moments] == ["腾讯二面"]
    assert "腾讯二面" in (tmp_path / "entries" / "2026-10-17.md").read_text(encoding="utf-8")


def test_delete_missing_id_makes_model_retry(tmp_path: Path):
    model = scripted(
        ModelResponse(parts=[ToolCallPart("delete_moment", {"id": 99})]),
        ModelResponse(parts=[TextPart("那条记录不存在。")]),
    )
    a = agent.build_agent(model)
    a.run_sync("删掉第 99 条", deps=make_state(tmp_path))
    assert len(model.calls) == 2
    assert store.load_record(tmp_path, D).moments == []


def _window_text(messages) -> str:
    return "\n".join(
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(getattr(part, "content", None), str)
    )


def test_recall_finds_turn_dropped_from_window():
    """被窗口丢弃的早期原文，仍然能被 search_today_chat 找回 —— 这是不做压缩的前提。"""
    from daykeep import window

    messages = turn("[09:10] 早上面了腾讯二面，问了一堆网络的问题")
    for i in range(80):
        messages += turn(f"第{i}轮 " + "字" * 300)

    trimmed, dropped = window.trim(messages, budget=20_000)
    assert dropped > 0
    assert "腾讯" not in _window_text(trimmed)  # 已不在窗口里
    assert "腾讯二面" in agent.search_in_messages(messages, "腾讯")  # 但完整归档里查得到
