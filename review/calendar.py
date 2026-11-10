"""通达信官方年度休市表的严格适配；未知年度不得猜测开市。"""
import calendar
import re
from datetime import date, datetime

def official_holiday_calendar(html, year, month):
    match = re.search(r'<textarea[^>]*id=["\']data["\'][^>]*>(.*?)</textarea>', html, re.S | re.I)
    if not match:
        raise ValueError("通达信官方休市表结构改变")
    holidays, names = set(), set()
    for line in match.group(1).strip().splitlines():
        fields = line.strip().split("|")
        if len(fields) != 6 or fields[2] != "中国" or not fields[0].startswith(str(year)):
            continue
        if fields[5] != "1" or not all(m in fields[3] for m in ("深圳市场", "上海市场", "北京市场", "中金所")):
            raise ValueError("休市表的市场范围或休市类型不完整")
        day = datetime.strptime(fields[0], "%Y%m%d").date()
        if day in holidays:
            raise ValueError("官方休市表日期重复")
        holidays.add(day)
        names.add(fields[1])
    expected = {"元旦", "春节", "清明节", "劳动节", "端午节", "中秋节", "国庆节"}
    if not expected.issubset(names) or len(holidays) < 15:
        raise ValueError(f"{year} 年休市表尚不完整，不能推断开市")
    # 已获取整年节假日表后加入周末休市；调休工作日不等于证券交易日。
    return [{"date": (day := date(year, month, n)).isoformat(),
             "is_open": day.weekday() < 5 and day not in holidays}
            for n in range(1, calendar.monthrange(year, month)[1] + 1)]


