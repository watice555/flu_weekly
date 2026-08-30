"""Resolve official report links; PDFs remain local and are never site assets."""

import argparse
import json
import re
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import CATALOG

LIST_URL = "https://ivdc.chinacdc.cn/cnic/zyzx/lgzb/"
OFFICIAL_HOST = "ivdc.chinacdc.cn"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def report_id(year, week):
    return f"{year}-W{week:02d}"


def official_url(value):
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and parsed.hostname == OFFICIAL_HOST


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_catalog(path=CATALOG):
    path = Path(path)
    if not path.exists():
        return {"schema_version": 1, "reports": {}}
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("schema_version") != 1 or not isinstance(result.get("reports"), dict):
        raise ValueError("Unsupported source catalog")
    return result


def record_source(year, week, detail_url, pdf_url, path=CATALOG):
    if not official_url(detail_url) or not official_url(pdf_url):
        raise ValueError("Report links must use the official host")
    catalog = load_catalog(path)
    key = report_id(year, week)
    catalog["reports"][key] = {
        **catalog["reports"].get(key, {}),
        "year": year,
        "week": week,
        "detail_url": detail_url,
        "pdf_url": pdf_url,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    atomic_json(path, catalog)


def make_session():
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    session.mount("https://", HTTPAdapter(max_retries=Retry(
        total=2, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )))
    return session


def get_html(session, url):
    if not official_url(url):
        raise ValueError("Unexpected source host")
    response = session.get(url, timeout=30)
    response.raise_for_status()
    if not official_url(response.url):
        raise ValueError("Unexpected redirect host")
    response.encoding = "utf-8"
    return response.text


def parse_index(html, page_url):
    result = {}
    for link in BeautifulSoup(html, "html.parser").select("a[href]"):
        title = link.get_text(" ", strip=True)
        match = re.fullmatch(r"\s*(20\d{2})\s*年?\s*第\s*(\d{1,2})\s*周\s*", title)
        if not match:
            continue
        year, week = map(int, match.groups())
        url = urljoin(page_url, link["href"])
        if official_url(url) and 1 <= week <= 53:
            result[report_id(year, week)] = {"year": year, "week": week, "title": title, "detail_url": url}
    return result


def parse_detail(html, url):
    soup = BeautifulSoup(html, "html.parser")
    candidates = []
    for link in soup.select("a[href]"):
        pdf_url = urljoin(url, link["href"])
        if official_url(pdf_url) and urlsplit(pdf_url).path.lower().endswith(".pdf"):
            candidates.append(("以此为准" in link.get_text(), pdf_url))
    if not candidates:
        raise ValueError(f"No PDF link on {url}")
    preferred = {value for corrected, value in candidates if corrected}
    all_urls = {value for _, value in candidates}
    choices = preferred or all_urls
    if len(choices) != 1:
        raise ValueError(f"Ambiguous PDF links on {url}")
    published = re.search(r"发布时间[：:]\s*(\d{4}-\d{2}-\d{2})", soup.get_text(" ", strip=True))
    return {"pdf_url": choices.pop(), "published_at": published[1] if published else None}


def sync_catalog(min_year=2024, path=CATALOG, refresh=False):
    catalog = load_catalog(path)
    with make_session() as session:
        first = get_html(session, LIST_URL)
        count = re.search(r"var\s+countPage\s*=\s*(\d+)", first)
        page_count = int(count[1]) if count else 1
        if not 1 <= page_count <= 100:
            raise ValueError("Unexpected official pagination")
        entries = {}
        for index in range(page_count):
            url = LIST_URL if index == 0 else urljoin(LIST_URL, f"index_{index}.htm")
            rows = parse_index(first if index == 0 else get_html(session, url), url)
            if not rows:
                raise ValueError(f"No report entries on {url}")
            entries.update({key: value for key, value in rows.items() if value["year"] >= min_year})
            if min(row["year"] for row in rows.values()) < min_year:
                break
            time.sleep(0.1)
        for index, (key, row) in enumerate(sorted(entries.items()), 1):
            old = catalog["reports"].get(key, {})
            if old.get("pdf_url") and old.get("detail_url") == row["detail_url"] and not refresh:
                continue
            detail = parse_detail(get_html(session, row["detail_url"]), row["detail_url"])
            catalog["reports"][key] = {
                **row, **detail,
                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            # Checkpoint each verified URL so an interrupted crawl can resume.
            atomic_json(path, catalog)
            if index % 20 == 0:
                print(f"Official links: {index}/{len(entries)}", flush=True)
            time.sleep(0.1)
    return len(catalog["reports"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch official report links (no PDF downloads).")
    parser.add_argument("--min-year", type=int, default=2024)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    print(f"Resolved {sync_catalog(args.min_year, args.catalog, args.refresh)} official report links")
