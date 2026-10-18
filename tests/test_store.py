from datetime import date
from pathlib import Path

import yaml
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from daykeep import store
from daykeep.models import DayMeta, DayRecord, Moment

D = date(2026, 10, 17)

RECORD = DayRecord(
    date=D,
    meta=DayMeta(
        title="实习与室友",
        mood="一般",
        tags=["实习", "宿舍"],
        summary="上午面试，晚上和室友聊了很久。",
    ),
    moments=[
        Moment(id=1, time="14:30", title="腾讯二面", content="问了很多网络的问题。", mood="不错"),
        Moment(id=2, time=None, title="室友争执", content="为了电费吵了几句。"),
    ],
)

EXPECTED_BODY = """\
# 2026-10-17 实习与室友

上午面试，晚上和室友聊了很久。

## 14:30 腾讯二面（不错）

问了很多网络的问题。

## 室友争执

为了电费吵了几句。
"""


def split_front_matter(text: str) -> tuple[dict, str]:
    assert text.startswith("---\n")
    front, rest = text[4:].split("\n---\n", 1)
    return yaml.safe_load(front), rest.lstrip("\n")


def test_render_markdown():
    front, body = split_front_matter(store.render_markdown(RECORD))
    assert front == {
        "date": "2026-10-17",
        "title": "实习与室友",
        "mood": "一般",
        "tags": ["实习", "宿舍"],
    }
    assert body == EXPECTED_BODY


def test_render_markdown_empty_day():
    _, body = split_front_matter(store.render_markdown(DayRecord(date=D)))
    assert body == "# 2026-10-17\n\n（今天还没有记录）\n"


def test_save_then_load_round_trip(tmp_path: Path):
    state = store.DayState.load(tmp_path, D)
    state.record = RECORD
    state.messages = [
        ModelRequest(parts=[UserPromptPart(content="[21:30] 今天面了腾讯")]),
        ModelResponse(parts=[TextPart("听起来很累，面试问了什么？")]),
    ]
    state.save()

    again = store.DayState.load(tmp_path, D)
    assert again.record == RECORD
    assert len(again.messages) == 2
    assert isinstance(again.messages[0], ModelRequest)
    assert isinstance(again.messages[1], ModelResponse)


def test_save_writes_markdown(tmp_path: Path):
    state = store.DayState.load(tmp_path, D)
    state.record = RECORD
    state.save()
    text = (tmp_path / "entries" / "2026-10-17.md").read_text(encoding="utf-8")
    assert "腾讯二面" in text


def test_save_leaves_no_temp_files(tmp_path: Path):
    state = store.DayState.load(tmp_path, D)
    state.record = RECORD
    state.save()
    assert list((tmp_path / ".daykeep" / "days").glob("*.tmp")) == []


def test_save_overwrites_previous(tmp_path: Path):
    state = store.DayState.load(tmp_path, D)
    state.record = RECORD
    state.save()
    state.record.meta.title = "改过的标题"
    state.save()
    assert store.DayState.load(tmp_path, D).record.meta.title == "改过的标题"


def test_load_missing_day_gives_empty_record(tmp_path: Path):
    state = store.DayState.load(tmp_path, D)
    assert state.record == DayRecord(date=D)
    assert state.messages == []


def test_list_days_newest_first(tmp_path: Path):
    for d in (date(2026, 10, 15), date(2026, 10, 17), date(2026, 10, 16)):
        s = store.DayState.load(tmp_path, d)
        s.save()
    assert store.list_days(tmp_path) == [date(2026, 10, 17), date(2026, 10, 16), date(2026, 10, 15)]


def test_list_days_without_data_dir(tmp_path: Path):
    assert store.list_days(tmp_path) == []


def test_read_entry_text_missing(tmp_path: Path):
    assert store.read_entry_text(tmp_path, D) is None
