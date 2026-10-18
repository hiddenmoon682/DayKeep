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
