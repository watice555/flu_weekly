"""Read the five report sources for one target week without rebuilding data."""

import argparse
import re
import sqlite3
import sys
import unicodedata
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

from . import DATABASE

SOURCE_COLUMNS = ("current_report", "next_week", "year_plus_1", "year_plus_2", "year_plus_3")


def parse_week(value):
    match = re.fullmatch(r"(20\d{2}|\d{2})\s*(?:/|-W)\s*(\d{1,2})", value.strip(), re.IGNORECASE)
    if not match:
        raise ValueError("请输入年/周，例如 24/7、2024/7 或 2024-W07。")
    year, week = map(int, match.groups())
    year = 2000 + year if year < 100 else year
    try:
        date.fromisocalendar(year, week, 1)
    except ValueError as error:
        raise ValueError(f"{year} 年不存在第 {week} 周。") from error
    return year, week


def read_values(database, year, week):
    path = Path(database).resolve()
    if not path.is_file():
        raise RuntimeError(f"数据库不存在：{path}\n请先在项目目录运行 .venv/bin/python -m flu_data.database。")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM weekly_source_values WHERE target_report_id = ?",
            (f"{year}-W{week:02d}",),
        ).fetchall()
    return {row["region"]: dict(row) for row in rows}


def display_width(text):
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def format_result(year, week, values):
    next_year, next_week, _ = (date.fromisocalendar(year, week, 1) + timedelta(days=7)).isocalendar()
    periods = [f"{year}-W{week:02d}", f"{next_year}-W{next_week:02d}",
               *(f"{year + offset}-W{week:02d}" for offset in (1, 2, 3))]
    rows = [["地区", "当期报告", "下一周报告", "后一年报告", "后两年报告", "后三年报告"],
            ["期次", *periods]]
    for region, label in [("south", "南方"), ("north", "北方")]:
        record = values.get(region, {})
        rows.append([label, *("—" if record.get(key) is None else f"{record[key]:.1f}%" for key in SOURCE_COLUMNS)])
    widths = [max(display_width(row[column]) for row in rows) for column in range(6)]
    lines = [f"\n{year} 年第 {week} 周 · 南北方 ILI%\n"]
    for index, row in enumerate(rows):
        lines.append(" | ".join(text + " " * (width - display_width(text)) for text, width in zip(row, widths)))
        if index == 1:
            lines.append("-+-".join("-" * width for width in widths))
    if not values:
        lines.append("\n该目标周暂无已收录的五类来源值。")
    lines.append("\n— 表示没有可用来源值，不是 0。网站仍只展示当期报告值。")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="只读查询某一周南北方 ILI% 的五类报告来源。")
    parser.add_argument("week", nargs="?", help="例如 24/7、2024/7 或 2024-W07；省略时交互输入")
    parser.add_argument("--database", type=Path, default=DATABASE, help="本地 SQLite 路径")
    args = parser.parse_args()
    try:
        while True:
            entered = args.week if args.week is not None else input("\n请输入目标年/周 [24/7]，输入 q 退出：").strip()
            if args.week is None and entered.lower() in ("q", "quit", "exit"):
                return 0
            try:
                year, week = parse_week(entered or "24/7")
            except ValueError as error:
                print(str(error), file=sys.stderr)
                if args.week is not None:
                    return 2
                continue
            values = read_values(args.database, year, week)
            print(format_result(year, week, values))
            if args.week is not None:
                return 0
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    except (RuntimeError, OSError, sqlite3.Error) as error:
        print(f"查询失败：{error}", file=sys.stderr)
        if isinstance(error, sqlite3.Error):
            print("请确认数据库含 weekly_source_values；可先运行 .venv/bin/python -m flu_data.database。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
