# DayKeep 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个本地运行的 AI 日记 Agent：用户与 AI 聊天，AI 自主把一天的事记成按天归档的 markdown 日记，`uv run daykeep` 一条命令即可启动。

**Architecture:** Pydantic AI v2 负责 Agent 循环、工具 schema 派生与流式事件；FastAPI 把流式事件翻译成 SSE 推给零构建的前端；日记以「当天一个 JSON 文件」为唯一数据源，另导出人类可读的 markdown；上下文窗口是归档的纯函数（按块裁剪，不存状态、不做 LLM 摘要）。

**Tech Stack:** Python 3.12、uv、pydantic-ai-slim[openai]、FastAPI、uvicorn、pydantic-settings、PyYAML、markdown-it-py、pytest、ruff

**Spec:** `docs/superpowers/specs/2026-10-17-daykeep-design.md`

## Global Constraints

- **Python 固定 3.12**。机器上 3.12 在 `D:\python\python.exe`（非 PATH 默认；PATH 上的 `python` 是 3.14 和 MSYS2 的 mingw python，都不能用）。因此仓库里必须有 `.python-version` 内容为 `3.12`，且 `pyproject.toml` 写 `requires-python = ">=3.12,<3.13"` —— 否则 uv 会挑 3.14。
- **`uv` 装在 `D:\python\Scripts\uv.exe`，可能不在 PATH**。每条 bash 命令先 `export PATH="/d/python/Scripts:$PATH"`。
- **数据根目录默认 `~/DayKeep`，绝不放仓库目录内**（避免用户提 PR 时把自己的日记提交上去）。测试一律用 `tmp_path` 覆盖。
- **服务只监听 `127.0.0.1`**。
- **界面文案全中文。**
- **工具就是带类型标注的普通函数**，schema 由 Pydantic AI 从签名派生 —— 禁止手写 JSON schema、禁止手写参数校验。
- **instructions 每轮随请求注入、不进入历史**，且一个逻辑日内逐字节不变（缓存稳定性）。禁止把会变的内容（当天日记全文）放进 instructions。
- **窗口是归档的纯函数**：不存窗口状态；同样的归档 + 预算必然算出同样的窗口。
- **所有写盘都是「先写临时文件再 `os.replace`」**。
- **逻辑日期由 deps 注入，模型不得自己填日期。**
- **不引入 tokenizer**：判超预算优先用 provider 上报的 `total_tokens`，缺失时才回退字符估算（CJK 每字符 1 token，其他每 4 字符 1 token）。
- 不做 LLM 摘要式上下文压缩。
- **Pydantic AI 的导入路径以安装版本为准**：事件类型（`PartDeltaEvent`、`TextPartDelta`、`FunctionToolResultEvent`）在 `pydantic_ai` 顶层有导出；消息类型（`ModelRequest`、`ModelResponse`、`UserPromptPart`、`TextPart`）在 `pydantic_ai.messages`。若某个名称导入失败，用 `uv run python -c "import pydantic_ai, pydantic_ai.messages as m; print(dir(pydantic_ai)); print(dir(m))"` 确认后再改，不要猜。

## 文件结构

| 文件 | 职责 |
|---|---|
| `pyproject.toml` | 依赖、`daykeep` 入口、pytest/ruff 配置 |
| `src/daykeep/clock.py` | `logical_date`、时间戳格式化。纯函数，无内部依赖 |
| `src/daykeep/config.py` | `Settings` 模型、读 TOML、首次运行生成模板、`DAYKEEP_HOME` 覆盖 |
| `src/daykeep/models.py` | `Mood`、`Moment`、`DayMeta`、`DayRecord`。只有数据结构 |
| `src/daykeep/store.py` | `DayRecord` 原子读写、markdown 渲染与导出、列出/读取历史日期 |
| `src/daykeep/window.py` | token 估算、裁剪、窗口渲染。纯函数，不碰文件 |
| `src/daykeep/agent.py` | `Agent` 定义、instructions、7 个工具、`Deps` |
| `src/daykeep/server.py` | FastAPI 路由、SSE 事件协议、请求级锁 |
| `src/daykeep/web/` | `index.html` / `app.js` / `style.css`，零构建 |
| `src/daykeep/__main__.py` | 读配置 → 起 uvicorn → 打开浏览器 |
| `tests/` | 与上表同名的测试文件 |

依赖方向（不可反向）：`clock` → 无；`config` → 无；`models` → 无；`store` → `models`；`window` → `models`；`agent` → `store, window, clock`；`server` → 全部。

---

## Task 1: 项目骨架、配置与逻辑日

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/daykeep/__init__.py`, `src/daykeep/clock.py`, `src/daykeep/config.py`
- Test: `tests/test_clock.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `clock.logical_date(now: datetime, rollover_hour: int = 4) -> date`
  - `clock.stamp(now: datetime) -> str`（返回 `"[21:30] "`）
  - `config.Settings`（字段 `model: ModelConfig`, `app: AppConfig`）
  - `config.ModelConfig`（`base_url: str`, `api_key: str = ""`, `name: str`, `context_window: int | None = None`, `budget: int | None = None`）
  - `config.AppConfig`（`home: Path`, `port: int = 8765`, `rollover_hour: int = 4`）
  - `config.load(path: Path | None = None) -> Settings`
  - `config.default_path() -> Path`（`~/DayKeep/config.toml`）

- [ ] **Step 1: 初始化仓库并写 pyproject**

```bash
export PATH="/d/python/Scripts:$PATH"
cd /d/C++Code/DayKeep && git init
```

`.python-version`（一行，末尾留着换行）：

```
3.12
```

`.gitignore`：

```
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
.ruff_cache/
```

`pyproject.toml`：

```toml
[project]
name = "daykeep"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
    "pydantic-ai-slim[openai]>=2.0.0",
    "fastapi>=0.115",
    "uvicorn>=0.32",
    "pydantic-settings>=2.6",
    "pyyaml>=6.0",
    "markdown-it-py>=3.0",
]

[project.scripts]
daykeep = "daykeep.__main__:main"

[dependency-groups]
dev = ["pytest>=8.3", "pytest-asyncio>=0.24", "httpx>=0.27", "ruff>=0.7"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/daykeep"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
src = ["src", "tests"]
```

- [ ] **Step 2: 安装依赖并确认 Python 版本**

Run: `export PATH="/d/python/Scripts:$PATH" && cd /d/C++Code/DayKeep && uv sync && uv run python -c "import sys; print(sys.version)"`
Expected: 打印 **3.12.x**（不是 3.14）。若打印的是 3.14，说明 `.python-version` 没生效，检查该文件内容和 `requires-python`。

- [ ] **Step 3: 写 clock 的失败测试**

`tests/test_clock.py`：

```python
from datetime import date, datetime

from daykeep.clock import logical_date, stamp


def test_before_rollover_belongs_to_previous_day():
    assert logical_date(datetime(2026, 10, 18, 3, 59), 4) == date(2026, 10, 17)


def test_at_rollover_belongs_to_new_day():
    assert logical_date(datetime(2026, 10, 18, 4, 0), 4) == date(2026, 10, 18)


def test_midday_belongs_to_same_day():
    assert logical_date(datetime(2026, 10, 18, 23, 50), 4) == date(2026, 10, 18)


def test_stamp_format():
    assert stamp(datetime(2026, 10, 18, 21, 30)) == "[21:30] "
```

- [ ] **Step 4: 跑测试确认失败**

Run: `uv run pytest tests/test_clock.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'daykeep.clock'`

- [ ] **Step 5: 实现 clock**

`src/daykeep/__init__.py` 留空。`src/daykeep/clock.py`：

```python
from datetime import date, datetime, timedelta


def logical_date(now: datetime, rollover_hour: int = 4) -> date:
    """凌晨 rollover_hour 之前算前一天，这样熬夜写日记不会把一天切成两半。"""
    return (now - timedelta(hours=rollover_hour)).date()


def stamp(now: datetime) -> str:
    return now.strftime("[%H:%M] ")
```

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run pytest tests/test_clock.py -v`
Expected: 4 passed

- [ ] **Step 7: 写 config 的失败测试**

`tests/test_config.py`：

```python
from pathlib import Path

import pytest

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
```

- [ ] **Step 8: 跑测试确认失败**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL，`AttributeError: module 'daykeep.config' has no attribute 'load'`

- [ ] **Step 9: 实现 config**

`src/daykeep/config.py`：

```python
import os
import tomllib
from pathlib import Path

from pydantic import BaseModel

TEMPLATE = """\
[model]
base_url = "https://api.deepseek.com"
api_key = ""
name = "deepseek-chat"

# 下面两项通常不用填：上下文长度由 Pydantic AI 自动探测（model.context_window）。
# 只有模型不被认识，或者想主动限制每轮发送量时才覆盖。
# context_window = 1000000
# budget = 900000

[app]
port = 8765
rollover_hour = 4
"""


class ModelConfig(BaseModel):
    base_url: str
    api_key: str = ""
    name: str
    context_window: int | None = None
    budget: int | None = None


class AppConfig(BaseModel):
    home: Path = Path.home() / "DayKeep"
    port: int = 8765
    rollover_hour: int = 4


class Settings(BaseModel):
    model: ModelConfig
    app: AppConfig


def default_path() -> Path:
    return Path.home() / "DayKeep" / "config.toml"


def load(path: Path | None = None) -> Settings:
    path = path or default_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(TEMPLATE, encoding="utf-8")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    settings = Settings.model_validate(data)
    if override := os.environ.get("DAYKEEP_HOME"):
        settings.app.home = Path(override)
    return settings
```

- [ ] **Step 10: 跑测试确认通过**

Run: `uv run pytest tests/test_config.py -v`
Expected: 4 passed

- [ ] **Step 11: 提交**

```bash
git add .gitignore .python-version pyproject.toml uv.lock src/daykeep tests/test_clock.py tests/test_config.py
git commit -m "feat: 项目骨架、配置加载与逻辑日"
```

---

## Task 2: 数据模型与存储

**Files:**
- Create: `src/daykeep/models.py`, `src/daykeep/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `clock`（不需要，日期由调用方给）
- Produces:
  - `models.Mood`（`Literal["很好", "不错", "一般", "低落", "糟糕"]`）
  - `models.Moment`（`id: int`, `time: str | None`, `title: str`, `content: str`, `mood: Mood | None`）
  - `models.DayMeta`（`title: str | None`, `mood: Mood | None`, `tags: list[str]`, `summary: str | None`）
  - `models.DayRecord`（`date: date`, `meta: DayMeta`, `moments: list[Moment]`）
  - `store.DayState`：`home: Path`, `date: date`, `record: DayRecord`, `messages: list[ModelMessage]`，且有 `DayState.load(home, d) -> DayState` 与 `state.save() -> None`
  - `store.render_markdown(record: DayRecord) -> str`
  - `store.list_days(home: Path) -> list[date]`（新到旧）
  - `store.read_entry_text(home: Path, d: date) -> str | None`

**注意：** `DayRecord` 不包含对话消息。消息是 Pydantic AI 的框架类型，单独用 `ModelMessagesTypeAdapter` 转换，避免框架类型渗进应用数据模型。落盘时两者写进**同一个** JSON 文件（一次原子写）。

- [ ] **Step 1: 写失败测试**

`tests/test_store.py`：

```python
from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from daykeep import store
from daykeep.models import DayMeta, DayRecord, Moment

D = date(2026, 10, 17)

RECORD = DayRecord(
    date=D,
    meta=DayMeta(title="实习与室友", mood="一般", tags=["实习", "宿舍"], summary="上午面试，晚上和室友聊了很久。"),
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'daykeep.models'`

- [ ] **Step 3: 实现 models**

`src/daykeep/models.py`：

```python
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Mood = Literal["很好", "不错", "一般", "低落", "糟糕"]


class Moment(BaseModel):
    id: int
    time: str | None = None
    title: str
    content: str
    mood: Mood | None = None


class DayMeta(BaseModel):
    title: str | None = None
    mood: Mood | None = None
    tags: list[str] = Field(default_factory=list)
    summary: str | None = None


class DayRecord(BaseModel):
    date: date
    meta: DayMeta = Field(default_factory=DayMeta)
    moments: list[Moment] = Field(default_factory=list)
```

- [ ] **Step 4: 实现 store**

`src/daykeep/store.py`：

```python
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelMessage

from daykeep.models import DayRecord


def days_dir(home: Path) -> Path:
    return home / ".daykeep" / "days"


def entries_dir(home: Path) -> Path:
    return home / "entries"


def day_path(home: Path, d: date) -> Path:
    return days_dir(home) / f"{d.isoformat()}.json"


def entry_path(home: Path, d: date) -> Path:
    return entries_dir(home) / f"{d.isoformat()}.md"


def render_markdown(record: DayRecord) -> str:
    front: dict[str, object] = {"date": record.date.isoformat()}
    if record.meta.title:
        front["title"] = record.meta.title
    if record.meta.mood:
        front["mood"] = record.meta.mood
    if record.meta.tags:
        front["tags"] = record.meta.tags

    heading = record.date.isoformat()
    if record.meta.title:
        heading += f" {record.meta.title}"
    lines = ["---", yaml.safe_dump(front, allow_unicode=True, sort_keys=False).strip(), "---", "", f"# {heading}", ""]
    if record.meta.summary:
        lines += [record.meta.summary, ""]
    if not record.moments:
        lines += ["（今天还没有记录）", ""]
    for m in record.moments:
        head = f"## {m.time} {m.title}" if m.time else f"## {m.title}"
        if m.mood:
            head += f"（{m.mood}）"
        lines += [head, "", m.content, ""]
    return "\n".join(lines)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class DayState:
    home: Path
    date: date
    record: DayRecord = field(default_factory=DayRecord)
    messages: list[ModelMessage] = field(default_factory=list)

    @classmethod
    def load(cls, home: Path, d: date) -> "DayState":
        path = day_path(home, d)
        if not path.exists():
            return cls(home=home, date=d, record=DayRecord(date=d))
        raw = path.read_text(encoding="utf-8")
        import json

        doc = json.loads(raw)
        return cls(
            home=home,
            date=d,
            record=DayRecord.model_validate(doc["day"]),
            messages=ModelMessagesTypeAdapter.validate_python(doc["messages"]),
        )

    def save(self) -> None:
        import json

        from pydantic_core import to_jsonable_python

        doc = {
            "day": self.record.model_dump(mode="json"),
            "messages": to_jsonable_python(self.messages),
        }
        _atomic_write(day_path(self.home, self.date), json.dumps(doc, ensure_ascii=False, indent=2))
        _atomic_write(entry_path(self.home, self.date), render_markdown(self.record))


def list_days(home: Path) -> list[date]:
    directory = days_dir(home)
    if not directory.exists():
        return []
    found = [date.fromisoformat(p.stem) for p in directory.glob("*.json")]
    return sorted(found, reverse=True)


def read_entry_text(home: Path, d: date) -> str | None:
    path = entry_path(home, d)
    return path.read_text(encoding="utf-8") if path.exists() else None
```

（`json` / `to_jsonable_python` 的 import 提到模块顶部更整洁，放在函数里只是为了让读取顺序更清楚，实现时可以上提。）

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_store.py -v`
Expected: 10 passed

- [ ] **Step 6: 提交**

```bash
git add src/daykeep/models.py src/daykeep/store.py tests/test_store.py
git commit -m "feat: 日记数据模型与按天原子存储"
```

---

## Task 3: 上下文窗口（纯函数）

**Files:**
- Create: `src/daykeep/window.py`
- Test: `tests/test_window.py`

**Interfaces:**
- Consumes: `models.DayRecord`、Pydantic AI 的消息类型
- Produces:
  - `window.estimate_tokens(text: str) -> int`
  - `window.resolve_budget(context_window: int | None, explicit_budget: int | None) -> int`
  - `window.size_of(messages: list[ModelMessage]) -> int`
  - `window.turn_starts(messages) -> list[int]`
  - `window.trim(messages, budget) -> tuple[list[ModelMessage], int]`（第二个值是丢掉的轮数）
  - `window.with_notice(messages, record: DayRecord, dropped: int) -> list[ModelMessage]`

**不变量（本任务的核心，必须有测试锁住）：** 裁剪结果的第一条一定是带用户发言的 `ModelRequest`；永远不出现「有工具调用、没有工具结果」的历史；结果是入参的纯函数（不读文件、不存状态）；两次裁剪之间窗口只在末尾追加，因此前一轮的窗口必然是后一轮窗口的前缀。

- [ ] **Step 1: 写失败测试**

`tests/test_window.py`：

```python
from datetime import date

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

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
    response = ModelResponse(parts=[TextPart("嗨")], usage={"input_tokens": 10, "output_tokens": 5})
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
    messages = many_turns(200)
    trimmed, dropped = window.trim(messages, budget=1_000)
    assert len(window.turn_starts(trimmed)) == window.MIN_TURNS


def test_trim_drops_whole_chunk_for_cache_stability():
    """连续两次裁剪之间，前一轮的窗口是后一轮窗口的前缀。"""
    messages = many_turns(200)
    first, _ = window.trim(messages, budget=20_000)
    grown = messages + turn("新的一轮")
    second, _ = window.trim(grown, budget=20_000)
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_window.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'daykeep.window'`

- [ ] **Step 3: 实现 window**

`src/daykeep/window.py`：

```python
from collections.abc import Sequence

from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart

from daykeep.models import DayRecord

MIN_TURNS = 30
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
) -> tuple[list[ModelMessage], int]:
    if size_of(messages) <= budget:
        return list(messages), 0
    target = int(budget * target_ratio)
    starts = turn_starts(messages)
    if len(starts) <= 1:
        return list(messages), 0

    min_keep = starts[-min_turns] if len(starts) >= min_turns else starts[0]
    fit = next((s for s in starts if s > 0 and size_of(messages[s:]) <= target), None)

    if size_of(messages[min_keep:]) > budget:
        # 最近 min_turns 轮本身就超预算，只能继续往下丢
        keep_from = fit if fit is not None else starts[-1]
    else:
        keep_from = min(fit, min_keep) if fit is not None else min_keep

    dropped = sum(1 for s in starts if s < keep_from)
    return list(messages[keep_from:]), dropped


def with_notice(
    messages: Sequence[ModelMessage], record: DayRecord, dropped: int
) -> list[ModelMessage]:
    if not dropped:
        return list(messages)
    from daykeep.store import render_markdown

    notice = (
        f"（本日更早的 {dropped} 轮对话已移出你的上下文，完整原文仍在本地的当天归档里，"
        f"需要时用 search_today_chat 查找。）\n\n当前日记内容：\n{render_markdown(record)}"
    )
    first = messages[0]
    assert isinstance(first, ModelRequest)
    head = ModelRequest(parts=[UserPromptPart(content=notice), *first.parts])
    return [head, *messages[1:]]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_window.py -v`
Expected: 13 passed。
若 `test_size_of_uses_reported_tokens_when_present` 失败，说明 `ModelResponse` 不接受 `usage=` 关键字或 `total_tokens` 字段名不同，把该测试改成构造真实 usage 的等价写法（其余测试不依赖它）。

- [ ] **Step 5: 提交**

```bash
git add src/daykeep/window.py tests/test_window.py
git commit -m "feat: 上下文窗口裁剪（纯函数，无状态、按块丢、缓存友好）"
```

---

## Task 4: Agent、instructions 与 7 个工具

**Files:**
- Create: `src/daykeep/agent.py`
- Modify: `src/daykeep/store.py`（新增 `load_record`）
- Test: `tests/test_agent.py`

**Interfaces:**
- Consumes: `store.DayState`、`window`、`clock`、`models`
- Produces:
  - `agent.Deps` = `store.DayState`
  - `agent.load_record` 不在本模块；`store.load_record(home: Path, d: date) -> DayRecord | None`
  - 纯函数：`next_moment_id(record) -> int`、`upsert(record, id, time, title, content, mood) -> Moment`、`drop(record, id) -> Moment`、`recap_block(home, today, days=7) -> str`、`search_in_messages(messages, query, max_hits=5) -> str`、`search_in_entries(home, today, query, days_back=30, max_hits=5) -> str`、`instructions(recap, today) -> str`
  - `build_agent(model) -> Agent[DayState, str]`

**设计要点：** 所有逻辑都写成**纯函数**，`@agent.tool` 只是三行包装 —— 这样绝大部分行为测试不经过模型就能跑。instructions 用 `@agent.instructions` 从 deps 现算：逻辑日 + 最近 7 天回顾。回顾只读过去的日子（当天不参与），一个逻辑日内逐字节不变，缓存安全。

- [ ] **Step 1: 给 store 补 `load_record`**

在 `src/daykeep/store.py` 末尾追加：

```python
def load_record(home: Path, d: date) -> DayRecord | None:
    path = day_path(home, d)
    if not path.exists():
        return None
    import json

    return DayRecord.model_validate(json.loads(path.read_text(encoding="utf-8"))["day"])
```

- [ ] **Step 2: 写失败测试**

`tests/test_agent.py`：

```python
from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from daykeep import agent, store
from daykeep.models import DayMeta, DayRecord, Moment

D = date(2026, 10, 17)


def make_state(tmp_path: Path) -> store.DayState:
    return store.DayState.load(tmp_path, D)


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
    record = DayRecord(date=D, moments=[Moment(id=1, title="a", content="x"), Moment(id=5, title="b", content="y")])
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
    state.record = DayRecord(date=old, moments=[Moment(id=1, title="实习投递", content="投了五家。")])
    state.save()
    out = agent.search_in_entries(tmp_path, D, "实习")
    assert old.isoformat() in out
    assert "投了五家" in out


def test_search_in_entries_respects_days_back(tmp_path: Path):
    old = D - timedelta(days=40)
    state = store.DayState.load(tmp_path, old)
    state.record = DayRecord(date=old, moments=[Moment(id=1, title="实习投递", content="投了五家。")])
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
    assert "第2天" in block and "小事" in block      # 没摘要时退化成 moment 标题
    assert D.isoformat() not in block                # 当天不参与回顾


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
        ModelResponse(parts=[ToolCallPart("upsert_moment", {"id": None, "time": "14:30", "title": "腾讯二面", "content": "问了网络。", "mood": "不错"})]),
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
    assert "腾讯" not in _window_text(trimmed)          # 已不在窗口里
    assert "腾讯二面" in agent.search_in_messages(messages, "腾讯")   # 但完整归档里查得到
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/test_agent.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'daykeep.agent'`

- [ ] **Step 4: 实现 agent**

`src/daykeep/agent.py`：

```python
from datetime import date, timedelta
from pathlib import Path

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart

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
    moment = Moment(id=id or next_moment_id(record), time=time, title=title, content=content, mood=mood)
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
            part.content for part in message.parts
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


def build_agent(model) -> Agent[Deps, str]:
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

    @a.tool_plain
    async def read_today_entry(ctx: RunContext[Deps]) -> str:
        """读取今天日记的完整内容，每条记录都带 id。"""
        return render_markdown(ctx.deps.record)

    @a.tool_plain
    async def search_today_chat(ctx: RunContext[Deps], query: str) -> str:
        """在今天完整的对话记录里搜索关键词，返回命中的整轮原文。"""
        return search_in_messages(ctx.deps.messages, query)

    @a.tool_plain
    async def read_entry(ctx: RunContext[Deps], date: date) -> str:
        """读取某一天日记的完整内容。"""
        text = read_entry_text(ctx.deps.home, date)
        return text or f"{date.isoformat()} 那天没有记录。"

    @a.tool_plain
    async def search_entries(ctx: RunContext[Deps], query: str, days_back: int = 30) -> str:
        """在过去若干天的日记正文里搜索关键词。"""
        return search_in_entries(ctx.deps.home, ctx.deps.date, query, days_back)

    return a
```

注意：instructions 只通过 `@a.instructions` 注册一次（它带回顾块）。不要同时在 `Agent(...)` 里再传 `instructions=`，否则会重复注入，缓存前缀也会被破坏。

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_agent.py -v`
Expected: 16 passed

- [ ] **Step 6: 跑全量测试**

Run: `uv run pytest -v`
Expected: 全绿

- [ ] **Step 7: 提交**

```bash
git add src/daykeep/agent.py src/daykeep/store.py tests/test_agent.py
git commit -m "feat: Agent 定义、跨天回顾与 7 个日记工具"
```

---

## Task 5: 服务端与 SSE 协议

**Files:**
- Create: `src/daykeep/server.py`
- Modify: `src/daykeep/clock.py`（新增 `strip_stamp`）
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `config.Settings`、`agent.build_agent`、`store`、`window`、`clock`
- Produces:
  - `server.create_app(settings: Settings, model) -> FastAPI`
  - `server.sse(event: str, data: dict) -> str`
  - `server.render_markdown_html(text: str) -> str`
  - `server.transcript(messages) -> list[dict]`
  - `clock.strip_stamp(text: str) -> str`

**SSE 事件表**（spec 第 7.2 节）：
| event | data |
|---|---|
| `day` | `{"date": "2026-10-17"}` |
| `delta` | `{"text": "..."}` |
| `tool` | `{"label": "已记录 #1 腾讯二面"}` |
| `entry` | `{"html": "..."}` |
| `trimmed` | `{"dropped_turns": 12}` |
| `done` | `{}` |
| `error` | `{"message": "...", "retryable": true}` |

- [ ] **Step 1: 给 clock 补 `strip_stamp`**

在 `src/daykeep/clock.py` 追加：

```python
import re

_STAMP = re.compile(r"^\[\d{2}:\d{2}\] ")


def strip_stamp(text: str) -> str:
    """界面显示用：去掉写进历史里的时间前缀。"""
    return _STAMP.sub("", text, count=1)
```

- [ ] **Step 2: 写失败测试**

`tests/test_server.py`：

```python
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
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


def echo_model(*texts: str):
    queue = list(texts) or ["嗨"]
    calls = []

    async def fn(messages, info):
        calls.append(messages)
        return ModelResponse(parts=[TextPart(queue[min(len(calls), len(queue)) - 1])])

    model = FunctionModel(fn)
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

    async with client_for(settings_for(tmp_path), FunctionModel(fn)) as client:
        events = parse_sse((await client.post("/api/chat", json={"text": "你好"})).text)
    assert len(calls) == 2
    assert [name for name, _ in events][-1] == "done"


async def test_days_endpoints(tmp_path: Path):
    old = clock.logical_date(datetime.now()) - timedelta(days=2)
    state = store.DayState.load(tmp_path, old)
    state.save()
    async with client_for(settings_for(tmp_path), TestModel()) as client:
        days = (await client.get("/api/days")).json()
        one = (await client.get(f"/api/days/{old.isoformat()}"))
        missing = (await client.get("/api/days/1999-01-01"))
    assert days == [old.isoformat()]
    assert one.status_code == 200
    assert missing.status_code == 404
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/test_server.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'daykeep.server'`

- [ ] **Step 4: 实现 server**

`src/daykeep/server.py`：

```python
import asyncio
import json
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from markdown_it import MarkdownIt
from pydantic_ai import FunctionToolResultEvent, PartDeltaEvent, TextPartDelta
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
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
                part.content for part in message.parts if isinstance(getattr(part, "content", None), str)
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
        d, state = today_state()
        budget = window.resolve_budget(model.context_window, settings.model.budget)
        if window.estimate_tokens(text) > budget // 2:
            raise HTTPException(413, "这条太长了，分几次发")

        async def stream():
            async with lock:
                yield sse("day", {"date": d.isoformat()})
                if not settings.model.api_key:
                    yield sse("error", {"message": "还没有配置 API key，请填写配置里的 api_key 后重启", "retryable": False})
                    return
                prompt = stamp(datetime.now()) + text
                candidates = [budget, budget // 2] if budget > 8_000 else [budget]
                for index, attempt_budget in enumerate(candidates):
                    window_messages, dropped = window.trim(state.messages, attempt_budget)
                    if index == 0 and dropped:
                        yield sse("trimmed", {"dropped_turns": dropped})
                    window_messages = window.with_notice(window_messages, state.record, dropped)
                    started = False
                    try:
                        async with agent_instance.run_stream(
                            prompt, message_history=window_messages, deps=state
                        ) as run:
                            async for event in run:
                                if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                                    started = True
                                    yield sse("delta", {"text": event.delta.content_delta})
                                elif isinstance(event, FunctionToolResultEvent):
                                    label = str(getattr(event.part, "content", ""))
                                    yield sse("tool", {"label": label})
                                    if event.part.tool_name in MUTATING_TOOLS:
                                        yield sse(
                                            "entry",
                                            {"html": render_markdown_html(store.render_markdown(state.record))},
                                        )
                        state.messages.extend(run.new_messages())
                        state.save()
                        break
                    except Exception as exc:  # noqa: BLE001
                        # 已经吐过字就不再重试，否则用户会看到重复内容
                        if index < len(candidates) - 1 and not started:
                            continue
                        yield sse("error", {"message": f"调用模型失败：{exc}", "retryable": True})
                        return
                yield sse("done", {})

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_server.py -v`
Expected: 11 passed。
若 `run.new_messages()` 或 `agent.run_stream(...)` 的事件类型名与上面不一致，按 Pydantic AI v2 的实际 API 调整（`run_stream` 返回上下文管理器，`new_messages()` 取其新增消息）。

- [ ] **Step 6: 跑全量测试并提交**

Run: `uv run pytest -v`
Expected: 全绿

```bash
git add src/daykeep/server.py src/daykeep/clock.py tests/test_server.py
git commit -m "feat: FastAPI + SSE 服务端与事件协议"
```

---

## Task 6: 前端与启动入口

**Files:**
- Create: `src/daykeep/web/index.html`, `src/daykeep/web/app.js`, `src/daykeep/web/style.css`
- Create: `src/daykeep/__main__.py`
- Modify: `src/daykeep/server.py`（挂载静态目录）
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `server.create_app`、`config.load`
- Produces:
  - `__main__.build_model(settings: Settings)`（返回 Pydantic AI 的模型对象）
  - `__main__.main() -> None`
  - 静态路由 `/static/*`

- [ ] **Step 1: 在 server 里挂静态目录**

`src/daykeep/server.py` 的 imports 补上：

```python
from fastapi.staticfiles import StaticFiles
```

在 `create_app` 里、路由之前加：

```python
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
```

- [ ] **Step 2: 写失败测试**

`tests/test_web.py`：

```python
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
```

- [ ] **Step 3: 跑测试确认失败**

Run: `uv run pytest tests/test_web.py -v`
Expected: FAIL，找不到 `src/daykeep/web/index.html`

- [ ] **Step 4: 写前端三件套**

`src/daykeep/web/index.html`：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>DayKeep</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header>
    <span id="date"></span>
    <select id="history"><option value="">往日…</option></select>
    <button id="back" hidden>回到今天</button>
  </header>
  <div id="banner" hidden></div>
  <main>
    <section id="chat">
      <div id="transcript"></div>
      <div id="composer">
        <textarea id="input" rows="3" placeholder="今天怎么样？（Enter 发送，Shift+Enter 换行）"></textarea>
        <button id="send">发送</button>
      </div>
    </section>
    <aside id="diary"></aside>
  </main>
  <script src="/static/app.js"></script>
</body>
</html>
```

`src/daykeep/web/style.css`：

```css
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.7 system-ui, "Microsoft YaHei", sans-serif; color: #222; }
header { display: flex; gap: 12px; align-items: center; padding: 10px 16px; border-bottom: 1px solid #e5e5e5; }
#date { font-weight: 600; }
#banner { padding: 10px 16px; background: #fff4e5; border-bottom: 1px solid #f0d9b5; color: #7a5200; }
main { display: grid; grid-template-columns: 1fr 1fr; height: calc(100vh - 52px); }
#chat { display: flex; flex-direction: column; border-right: 1px solid #e5e5e5; min-width: 0; }
#transcript { flex: 1; overflow-y: auto; padding: 16px; }
.msg { margin-bottom: 12px; white-space: pre-wrap; }
.msg.user { color: #1a4b8c; }
.msg.assistant { color: #222; }
.note { color: #999; font-size: 13px; margin: 6px 0; }
#composer { display: flex; gap: 8px; padding: 12px; border-top: 1px solid #e5e5e5; }
#input { flex: 1; resize: none; font: inherit; padding: 8px; }
#send { padding: 0 18px; }
#diary { overflow-y: auto; padding: 20px 28px; background: #fafafa; }
#diary h1 { font-size: 20px; } #diary h2 { font-size: 16px; margin-top: 20px; }
@media (max-width: 800px) { main { grid-template-columns: 1fr; } #diary { display: none; } }
```

`src/daykeep/web/app.js`：

```js
const $ = (id) => document.getElementById(id);
let streaming = false;
let viewing = null;
let configured = true;
let openReply = null;

function append(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  $("transcript").appendChild(div);
  $("transcript").scrollTop = $("transcript").scrollHeight;
  return div;
}

function note(text) {
  const div = document.createElement("div");
  div.className = "note";
  div.textContent = text;
  $("transcript").appendChild(div);
  $("transcript").scrollTop = $("transcript").scrollHeight;
}

function setEntry(html) { $("diary").innerHTML = html; }

async function loadToday() {
  const data = await (await fetch("/api/today")).json();
  viewing = null;
  configured = data.configured;
  $("back").hidden = true;
  $("date").textContent = data.date;
  $("banner").hidden = configured;
  if (!configured) {
    $("banner").textContent = "还没有配置 API key：请填写 ~/DayKeep/config.toml 里的 api_key，然后重启。";
  }
  $("transcript").innerHTML = "";
  for (const m of data.transcript) append(m.role, m.text);
  setEntry(data.entry_html);
}

async function loadHistoryList() {
  const days = await (await fetch("/api/days")).json();
  const sel = $("history");
  sel.innerHTML = '<option value="">往日…</option>';
  for (const d of days) sel.add(new Option(d, d));
}

async function showDay(day) {
  if (!day) { await loadToday(); await loadHistoryList(); return; }
  const res = await fetch(`/api/days/${day}`);
  if (!res.ok) return;
  const data = await res.json();
  viewing = day;
  $("date").textContent = day;
  $("back").hidden = false;
  setEntry(data.entry_html);
}

function applyEvent(name, data) {
  if (name === "day") {
    $("date").textContent = data.date;
  } else if (name === "delta") {
    if (!openReply) openReply = append("assistant", "");
    openReply.textContent += data.text;
    $("transcript").scrollTop = $("transcript").scrollHeight;
  } else if (name === "tool") {
    note("✎ " + data.label);
  } else if (name === "entry") {
    if (!viewing) setEntry(data.html);
  } else if (name === "trimmed") {
    note(`更早的 ${data.dropped_turns} 轮对话已不在 AI 的直接记忆中，需要时它会自己查找`);
  } else if (name === "done") {
    openReply = null;
  } else if (name === "error") {
    note("⚠ " + data.message);
    openReply = null;
  }
}

async function send() {
  const text = $("input").value.trim();
  if (!text || streaming || !configured) return;
  streaming = true;
  $("send").disabled = true;
  $("input").value = "";
  append("user", text);
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (res.status === 413) {
      note("⚠ 这条太长了，分几次发");
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop();
      for (const block of blocks) {
        const lines = block.split("\n");
        const name = (lines.find((l) => l.startsWith("event: ")) || "").slice(7);
        const raw = (lines.find((l) => l.startsWith("data: ")) || "").slice(6);
        if (!name) continue;
        applyEvent(name, raw ? JSON.parse(raw) : {});
      }
    }
  } catch (err) {
    note("⚠ 网络错误，请重试");
  } finally {
    streaming = false;
    openReply = null;
    $("send").disabled = false;
  }
}

$("send").onclick = send;
$("input").onkeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
};
$("history").onchange = (e) => showDay(e.target.value);
$("back").onclick = () => loadToday();
loadToday();
loadHistoryList();
```

- [ ] **Step 5: 写启动入口**

`src/daykeep/__main__.py`：

```python
import threading
import webbrowser

import uvicorn
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from daykeep import config, server


def build_model(settings: config.Settings) -> OpenAIChatModel:
    provider = OpenAIProvider(
        base_url=settings.model.base_url,
        api_key=settings.model.api_key or "not-configured",
    )
    return OpenAIChatModel(settings.model.name, provider=provider)


def main() -> None:
    path = config.default_path()
    settings = config.load(path)
    if not settings.model.api_key:
        print(f"还没有配置 API key，请填写 {path} 里的 api_key 后重启。")
    settings.app.home.mkdir(parents=True, exist_ok=True)
    app = server.create_app(settings, build_model(settings))
    url = f"http://127.0.0.1:{settings.app.port}"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=settings.app.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run pytest tests/test_web.py -v`
Expected: 3 passed

- [ ] **Step 7: 跑全量测试**

Run: `uv run pytest -v`
Expected: 全绿

- [ ] **Step 8: 在浏览器里实际走一遍（必做，不能跳过）**

先在 `~/DayKeep/config.toml` 里填好自己的 `base_url` / `api_key` / `name`，然后：

```bash
uv run daykeep
```

浏览器应自动打开 `http://127.0.0.1:8765`，依次确认：

1. 发一句"今天面了腾讯二面，被问了一堆网络的问题" → 回复流式出现，不是一次性蹦出来。
2. 右侧日记栏自动出现"腾讯二面"那条，且聊天里有一行浅色的"✎ 已记录 #1 …"。
3. 再说"把刚才那条改成网络和操作系统" → 右侧是**更新**而不是新增第二条。
4. 刷新页面 → 对话记录和日记都还在。
5. 顶部"往日"下拉里选一个有记录的历史日期 → 右侧切成那天的只读日记，"回到今天"按钮出现；点它回到今天。
6. **断开网络或把 api_key 改错再发一条** → 界面出现错误提示，且刷新后这条消息不在记录里（失败不落盘）。

第 6 条是这轮唯一无法自动化的部分，必须人工或浏览器自动化确认。如果这一步没做，不要说"完成"。

- [ ] **Step 9: 提交**

```bash
git add src/daykeep/web src/daykeep/__main__.py src/daykeep/server.py tests/test_web.py
git commit -m "feat: 零构建前端、启动入口与静态资源"
```

---

## Task 7: 分享收尾（README 与静态检查）

**Files:**
- Create: `README.md`, `tests/test_readme.py`

**Interfaces:**
- Consumes: 无
- Produces: 无

- [ ] **Step 1: 写失败测试**

`tests/test_readme.py`：

```python
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"


def test_readme_documents_key_facts():
    text = README.read_text(encoding="utf-8")
    assert "uv run daykeep" in text          # 一行启动
    assert "~/DayKeep" in text               # 数据位置
    assert "api_key" in text                 # 必填项
    assert "第三方" in text                   # 隐私说明
    assert "被覆盖" in text                   # md 文件不要手改
    assert "budget" in text                  # 延迟/费用的取舍
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_readme.py -v`
Expected: FAIL，`FileNotFoundError: README.md`

- [ ] **Step 3: 写 README**

`README.md`：

```markdown
# DayKeep

一个在本地运行的 AI 日记 Agent。你和 AI 聊天，它把一天里值得记的事写进当天的日记。

## 隐私

程序和日记都在这台机器上（默认 `~/DayKeep`），日记是纯 markdown，你可以随时打开、备份、删掉。
**但聊天内容会发送给你自己配置的第三方 API** —— 这是它的工作方式，请按自己的接受程度选择服务商。

## 快速开始

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

    uv run daykeep

首次运行会在 `~/DayKeep/config.toml` 生成配置模板。填好 `api_key` 后重启即可。

## 配置

    [model]
    base_url = "https://api.deepseek.com"   # 任意 OpenAI 兼容服务
    api_key = ""                            # 必填
    name = "deepseek-chat"
    # context_window = 1000000              # 一般不用填
    # budget = 900000                       # 一般不用填

    [app]
    port = 8765
    rollover_hour = 4

- 上下文长度由 Pydantic AI 自动探测，不用手填。只有在模型不被认识、或你想主动限制每轮发送量时才覆盖 `context_window` / `budget`。
- **调小 `budget` 的取舍**：AI 会更早忘掉当天较早的对话（原文不会丢，它会自己用 search_today_chat 查回来），换来更低的延迟和费用。默认值贴着模型上限给，正常使用不会触发。
- `rollover_hour` 默认 4：凌晨 4 点前记的都算前一天，熬夜写日记不会被切成两天。
- 也可以用环境变量 `DAYKEEP_API_KEY` 覆盖 `api_key`。
- **不要把 `config.toml` 提交到任何仓库。**

## 数据在哪

    ~/DayKeep/
      config.toml
      entries/2026-10-17.md              # 给人看的日记
      .daykeep/days/2026-10-17.json      # 程序用的数据（对话原文 + 结构化日记）

`entries/*.md` 每次记完会重新生成，**改它会在下次生成时被覆盖** —— 想改内容请直接跟 AI 说。
它可以直接用 Obsidian 打开。

## 路线图

- [ ] 桌面应用（pywebview 套壳，复用同一份前端）
- [ ] 界面里的配置页
```

（README 里的代码块用 4 空格缩进而非 ``` 围栏，是为了避免和外层 plan 的围栏冲突；实际写文件时用常规围栏。）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_readme.py -v`
Expected: 1 passed

- [ ] **Step 5: 静态检查**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: 无告警。有告警就修，不要用 `# noqa` 掩盖。

- [ ] **Step 6: 全量测试**

Run: `uv run pytest -v`
Expected: 全绿（约 45 个用例）

- [ ] **Step 7: 验证"别人 clone 后能不能跑"**

```bash
cd /tmp && rm -rf daykeep-clone && git clone /d/C++Code/DayKeep daykeep-clone
cd daykeep-clone && uv run python -c "from daykeep.__main__ import main; print('ok')"
```

Expected: 打印 `ok`，且 `data/`、`entries/` 之类**没有**出现在仓库里（`git status` 干净）。

- [ ] **Step 8: 提交**

```bash
git add README.md tests/test_readme.py
git commit -m "docs: README（上手、配置、隐私与数据位置）"
```

---

## 自审结果

**1. Spec 覆盖**：spec 第 2~9 节的每一项都能对到任务 —— 技术栈与依赖（T1）、数据模型与存储（T2）、逻辑日（T1）、上下文窗口（T3、T5、T6）、Agent 与 7 个工具（T4）、请求流程与 SSE（T5）、界面（T6）、配置（T1、T5）、测试策略（散在 T1~T7）。

初稿自审时发现并已修掉的四处缺口：

- **§7.3「服务商返回上下文超长 → 强制裁剪一半、自动重试一次」没落到代码**。已在 T5 的 chat 处理器里加了两档预算的循环，并且**只有在这一轮还没吐过字时才重试**（否则用户会看到重复内容）。补了对应用例。
- **§10 的「召回测试」缺失**。已在 T4 补 `test_recall_finds_turn_dropped_from_window`：先断言被丢弃的原文确实不在窗口里，再断言 `search_in_messages` 能从完整归档里找到它。这条测试是「不做压缩」这个决定的前提，不能没有。
- **T5 初稿里 `window.trim` 被调用了两遍**（循环改造后的残留），已删。
- **T6 的 app.js 里 `delta` 事件会新建一条消息而不是追加、发送前的横幅判断写错**，已改成维护 `openReply` 引用 + `configured` 标志。

**2. 占位符扫描**：无 TBD / TODO / "自行补充"。计划里保留的几处「若 API 名称不一致就调整」是**外部依赖漂移的应对说明**（我已通过 context7 核对过 v2 的 API 形态，但没法在不安装的情况下 100% 确认导入路径），不是没写完的步骤；Global Constraints 里给了一句可执行的核对命令。

**3. 类型一致性**：逐项核过 —— `store.DayState`（agent 用作 `Deps`）、`window.trim/with_notice/size_of/turn_starts/resolve_budget/estimate_tokens/MIN_TURNS`、`store.render_markdown/list_days/read_entry_text/load_record/day_path`、`agent.build_agent/recap_block/instructions/search_in_messages/search_in_entries/upsert/drop/next_moment_id`、`server.create_app/sse/render_markdown_html/transcript`、`clock.logical_date/stamp/strip_stamp`、`config.load/default_path/Settings/ModelConfig/AppConfig`。工具名 `upsert_moment` / `delete_moment` / `set_day_meta` / `read_today_entry` / `search_today_chat` / `read_entry` / `search_entries` 在 T4 定义、在 T5 的 `MUTATING_TOOLS` 里引用、在 spec 第 6.2 节一致。

**已知的、故意留下的不确定性**：T4/T5 里少数 Pydantic AI 的运行时细节（`run.new_messages()` 的取值时机、`ModelResponse(usage=...)` 的字段名）我只通过文档核对、没有实际执行验证。相关用例失败时按报错调整测试写法，不要改设计。

