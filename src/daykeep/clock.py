import re
from datetime import date, datetime, timedelta

_STAMP = re.compile(r"^\[\d{2}:\d{2}\] ")


def logical_date(now: datetime, rollover_hour: int = 4) -> date:
    """凌晨 rollover_hour 之前算前一天，这样熬夜写日记不会把一天切成两半。"""
    return (now - timedelta(hours=rollover_hour)).date()


def stamp(now: datetime) -> str:
    return now.strftime("[%H:%M] ")


def strip_stamp(text: str) -> str:
    """界面显示用：去掉写进历史里的时间前缀。"""
    return _STAMP.sub("", text, count=1)
