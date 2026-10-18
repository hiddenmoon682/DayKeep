import json
import os
from dataclasses import dataclass, field
from datetime import date as Date
from pathlib import Path

import yaml
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelMessage
from pydantic_core import to_jsonable_python

from daykeep.models import DayRecord


def days_dir(home: Path) -> Path:
    return home / ".daykeep" / "days"


def entries_dir(home: Path) -> Path:
    return home / "entries"


def day_path(home: Path, d: Date) -> Path:
    return days_dir(home) / f"{d.isoformat()}.json"


def entry_path(home: Path, d: Date) -> Path:
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
    lines = [
        "---",
        yaml.safe_dump(front, allow_unicode=True, sort_keys=False).strip(),
        "---",
        "",
        f"# {heading}",
        "",
    ]
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
    date: Date
    record: DayRecord
    messages: list[ModelMessage] = field(default_factory=list)

    @classmethod
    def load(cls, home: Path, d: Date) -> "DayState":
        path = day_path(home, d)
        if not path.exists():
            return cls(home=home, date=d, record=DayRecord(date=d))
        raw = path.read_text(encoding="utf-8")
        doc = json.loads(raw)
        return cls(
            home=home,
            date=d,
            record=DayRecord.model_validate(doc["day"]),
            messages=ModelMessagesTypeAdapter.validate_python(doc["messages"]),
        )

    def save(self) -> None:
        doc = {
            "day": self.record.model_dump(mode="json"),
            "messages": to_jsonable_python(self.messages),
        }
        _atomic_write(day_path(self.home, self.date), json.dumps(doc, ensure_ascii=False, indent=2))
        _atomic_write(entry_path(self.home, self.date), render_markdown(self.record))


def list_days(home: Path) -> list[Date]:
    directory = days_dir(home)
    if not directory.exists():
        return []
    found = [Date.fromisoformat(p.stem) for p in directory.glob("*.json")]
    return sorted(found, reverse=True)


def read_entry_text(home: Path, d: Date) -> str | None:
    path = entry_path(home, d)
    return path.read_text(encoding="utf-8") if path.exists() else None


def load_record(home: Path, d: Date) -> DayRecord | None:
    path = day_path(home, d)
    if not path.exists():
        return None
    import json

    return DayRecord.model_validate(json.loads(path.read_text(encoding="utf-8"))["day"])
