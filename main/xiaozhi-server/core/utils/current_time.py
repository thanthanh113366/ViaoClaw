"""
时间工具模块
提供统一的时间获取功能
"""

import cnlunar
from datetime import datetime
from zoneinfo import ZoneInfo

WEEKDAY_MAP = {
    "Monday": "星期一",
    "Tuesday": "星期二", 
    "Wednesday": "星期三",
    "Thursday": "星期四",
    "Friday": "星期五",
    "Saturday": "星期六",
    "Sunday": "星期日",
}


def get_configured_timezone() -> ZoneInfo:
    try:
        from config.config_loader import load_config

        config = load_config()
        timezone_name = (
            config.get("server", {}).get("timezone")
            or config.get("cron", {}).get("timezone")
            or "Asia/Ho_Chi_Minh"
        )
        return ZoneInfo(timezone_name)
    except Exception:
        return ZoneInfo("Asia/Ho_Chi_Minh")


def now() -> datetime:
    return datetime.now(get_configured_timezone())


def get_current_time() -> str:
    """
    获取当前时间字符串 (格式: HH:MM)
    """
    return now().strftime("%H:%M")


def get_current_date() -> str:
    """
    获取今天日期字符串 (格式: YYYY-MM-DD)
    """
    return now().strftime("%Y-%m-%d")


def get_current_weekday() -> str:
    """
    获取今天星期几
    """
    current = now()
    return WEEKDAY_MAP[current.strftime("%A")]


def get_current_lunar_date() -> str:
    """
    获取农历日期字符串
    """
    try:
        current = now()
        today_lunar = cnlunar.Lunar(current, godType="8char")
        return "%s年%s%s" % (
            today_lunar.lunarYearCn,
            today_lunar.lunarMonthCn[:-1],
            today_lunar.lunarDayCn,
        )
    except Exception:
        return "农历获取失败"


def get_current_time_info() -> tuple:
    """
    获取当前时间信息
    返回: (当前时间字符串, 今天日期, 今天星期, 农历日期)
    """
    current_time = get_current_time()
    today_date = get_current_date()
    today_weekday = get_current_weekday()
    lunar_date = get_current_lunar_date()
    
    return current_time, today_date, today_weekday, lunar_date
