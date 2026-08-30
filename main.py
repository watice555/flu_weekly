# pip install requests beautifulsoup4
import json
import re
import shutil
from pathlib import Path
from typing import Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

LIST_URL = "https://ivdc.chinacdc.cn/cnic/zyzx/lgzb/"
SAVE_DIR = Path("flu_reports")
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
    soup = BeautifulSoup(html, "html.parser")

    for link in soup.select("a[href]"):
        href = link.get("href")
        if isinstance(href, str) and href.lower().endswith(".pdf"):
            return urljoin(detail_url, href)

    raise RuntimeError("未在详情页找到 PDF 链接")


def load_last_url() -> str:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8")).get(
            "last_pdf_url", ""
        )
    return ""


def save_last_url(url: str) -> None:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps({"last_pdf_url": url}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_report_filename(year: int, issue: int) -> str:
    return f"{year % 100:02d}-{issue:02d}.pdf"


def download_pdf(pdf_url: str, filename: str) -> Path:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    output_path = SAVE_DIR / filename

    with session.get(pdf_url, timeout=30, stream=True) as response:
        response.raise_for_status()
        with output_path.open("wb") as file_obj:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    file_obj.write(chunk)

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
    if pdf_url == last_url:
        print("没有新周报。")
        return

    filename = build_report_filename(year, issue)
    primary_path = download_pdf(pdf_url, filename)
    secondary_path = copy_to_secondary(primary_path)
    save_last_url(pdf_url)

    print(f"已下载: {primary_path}")
    print(f"已同步: {secondary_path}")
    print(f"PDF: {pdf_url}")
    print(f"详情: {detail_url}")


if __name__ == "__main__":
    main()
