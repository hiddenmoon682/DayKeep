from datetime import date, datetime, timedelta


def logical_date(now: datetime, rollover_hour: int = 4) -> date:
    """凌晨 rollover_hour 之前算前一天，这样熬夜写日记不会把一天切成两半。"""
    return (now - timedelta(hours=rollover_hour)).date()


def stamp(now: datetime) -> str:
    return now.strftime("[%H:%M] ")
