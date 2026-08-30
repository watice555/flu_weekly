# pip install requests beautifulsoup4
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from flu_data import REPORTS_DIR
from flu_data.database import rebuild
from flu_data.extract import file_hash
from flu_data.sources import atomic_json, parse_detail, record_source

LIST_URL = "https://ivdc.chinacdc.cn/cnic/zyzx/lgzb/"
SAVE_DIR = REPORTS_DIR
SECONDARY_SAVE_DIR = Path.home() / "Nutstore Files" / "Nutstore" / "新冠等" / "周报"
STATE_FILE = SAVE_DIR / "state.json"

session = requests.Session()
session.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36"
        )
    }
)


def get_html(url: str) -> str:
    response = session.get(url, timeout=20)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def find_latest_detail() -> Tuple[str, int, int]:
    html = get_html(LIST_URL)
    soup = BeautifulSoup(html, "html.parser")

    for link in soup.select("a[href]"):
        text = link.get_text(strip=True)
        href = link.get("href")
        match = re.search(r"(?P<year>\d{4}).{0,8}?(?P<issue>\d+).{0,6}?周", text)
        if isinstance(href, str) and match:
            year = int(match.group("year"))
            issue = int(match.group("issue"))
            return urljoin(LIST_URL, href), year, issue

    raise RuntimeError("未在列表页找到周报详情链接")


def find_pdf_url(detail_url: str) -> str:
    html = get_html(detail_url)
    return parse_detail(html, detail_url)["pdf_url"]


def load_last_url() -> str:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8")).get(
            "last_pdf_url", ""
        )
    return ""


def save_last_url(url: str) -> None:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    atomic_json(STATE_FILE, {"last_pdf_url": url})


def build_report_filename(year: int, issue: int) -> str:
    return f"{year % 100:02d}-{issue:02d}.pdf"


def download_pdf(pdf_url: str, filename: str) -> Path:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = SAVE_DIR / filename

    temporary = None
    try:
        with session.get(pdf_url, timeout=30, stream=True) as response:
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(dir=SAVE_DIR, delete=False) as file_obj:
                temporary = Path(file_obj.name)
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        file_obj.write(chunk)
        with temporary.open("rb") as stream:
            if not stream.read(5).startswith(b"%PDF-"):
                raise ValueError("官网响应不是 PDF，保留已有文件")
        if output_path.exists() and file_hash(output_path) != file_hash(temporary):
            originals = SAVE_DIR / "originals"
            originals.mkdir(exist_ok=True)
            old_version = originals / f"{output_path.stem}-{file_hash(output_path)}.pdf"
            if not old_version.exists():
                shutil.copy2(output_path, old_version)
        temporary.replace(output_path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    return output_path


def copy_to_secondary(path: Path) -> Path:
    SECONDARY_SAVE_DIR.mkdir(parents=True, exist_ok=True)
    secondary_path = SECONDARY_SAVE_DIR / path.name
    shutil.copy2(path, secondary_path)
    return secondary_path


def main() -> None:
    detail_url, year, issue = find_latest_detail()
    pdf_url = find_pdf_url(detail_url)

    last_url = load_last_url()
    if pdf_url == last_url and (SAVE_DIR / build_report_filename(year, issue)).is_file():
        record_source(year, issue, detail_url, pdf_url)
        result = rebuild()
        print("没有新周报。")
        print(f"数据库：{result['reports']} 期 / {result['observations']} 条南北方观测")
        return

    filename = build_report_filename(year, issue)
    primary_path = download_pdf(pdf_url, filename)
    record_source(year, issue, detail_url, pdf_url)
    result = rebuild()
    secondary_path = copy_to_secondary(primary_path)
    save_last_url(pdf_url)

    print(f"已下载: {primary_path}")
    print(f"已同步: {secondary_path}")
    print(f"PDF: {pdf_url}")
    print(f"详情: {detail_url}")
    print(f"数据库：{result['reports']} 期 / {result['observations']} 条南北方观测")


if __name__ == "__main__":
    main()
