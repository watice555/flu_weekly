"""Generate a local, self-contained table of every week's five source values."""

import argparse
import json
import sqlite3
import sys
import tempfile
import webbrowser
from contextlib import closing
from datetime import datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from . import DATABASE, ROOT
from .query_sources import SOURCE_COLUMNS
from .sources import official_url

OUTPUT = ROOT / "data" / "source_table" / "index.html"


def read_table(database):
    path = Path(database).resolve()
    if not path.is_file():
        raise ValueError("数据库不存在，请先运行 .venv/bin/python -m flu_data.database。")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        # Keep values and their source links within the same read snapshot.
        connection.execute("BEGIN")
        rows = [dict(row) for row in connection.execute(
            "SELECT * FROM weekly_source_values ORDER BY year,week,CASE region WHEN 'south' THEN 0 ELSE 1 END"
        )]
        sources = {}
        for raw in connection.execute("SELECT * FROM observation_sources WHERE source_role != 'outside_window'"):
            row = dict(raw)
            key = (row["target_report_id"], row["region"], row["source_role"])
            if key in sources:
                raise ValueError(f"同一个来源存在多个数值，无法生成表格：{key}")
            sources[key] = row
    return rows, sources


def render_table(rows, sources):
    rendered = []
    weeks = sorted({row["target_report_id"] for row in rows})
    groups = {key: index % 2 for index, key in enumerate(weeks)}
    for row in rows:
        key, region = row["target_report_id"], row["region"]
        label = "南方" if region == "south" else "北方"
        cells = [f'<th scope="row">{escape(key)}</th>', f'<td><span class="region {region}">{label}</span></td>']
        for column in SOURCE_COLUMNS:
            value = row[column]
            if value is None:
                cells.append('<td class="missing" aria-label="暂无来源值" title="暂无来源值"></td>')
                continue
            source = sources[(key, region, column)]
            if value != source["ili_percent"]:
                raise ValueError(f"数值与来源不一致：{key} {region} {column}")
            pages = json.loads(source["pages_json"])
            title = f"来源：{source['source_report_id']} 报告；PDF 第 {'、'.join(map(str, pages))} 页"
            number = f'<span class="value">{value:.1f}</span>'
            url = source["pdf_url"]
            if url and official_url(url):
                href = url.split("#", 1)[0] + (f"#page={pages[0]}" if pages else "")
                content = f'<a href="{escape(href, quote=True)}" target="_blank" rel="noopener noreferrer" title="{escape(title, quote=True)}">{number}</a>'
            else:
                content = f'<span title="{escape(title, quote=True)}">{number}</span>'
            cells.append(f'<td>{content}</td>')
        rendered.append(
            f'<tr data-week="{escape(key, quote=True)}" data-year="{row["year"]}" '
            f'data-region="{region}" class="group-{groups[key]}">' + "".join(cells) + "</tr>"
        )
    years = sorted({row["year"] for row in rows})
    year_options = "".join(f'<option value="{year}">{year} 年</option>' for year in years)
    generated = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S Asia/Shanghai")
    template = Path(__file__).with_suffix(".html").read_text(encoding="utf-8")
    return (template.replace("<!-- ROWS -->", "\n".join(rendered))
            .replace("<!-- YEARS -->", year_options)
            .replace("<!-- GENERATED -->", generated)
            .replace("<!-- COUNTS -->", f"{len(weeks)} 个目标周 · {len(rows)} 行南北方数据"))


def write_table(database=DATABASE, output=OUTPUT):
    rows, sources = read_table(database)
    html = render_table(rows, sources)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(html)
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return output.resolve()


def main():
    parser = argparse.ArgumentParser(description="生成全部周次的五类来源表格，只读本地数据库。")
    parser.add_argument("--open", dest="open_browser", action="store_true", help="生成后用默认浏览器打开")
    parser.add_argument("--no-open", dest="open_browser", action="store_false", help="仅生成表格，不自动打开")
    parser.set_defaults(open_browser=False)
    args = parser.parse_args()
    try:
        output = write_table()
        print(f"全部周次表格已生成：{output}")
        if args.open_browser and not webbrowser.open(output.as_uri()):
            print("未能自动打开浏览器，请双击上面的 HTML 文件。", file=sys.stderr)
        return 0
    except (ValueError, OSError, sqlite3.Error) as error:
        print(f"生成表格失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
