"""Read current-week values from report text; never infer points from plots."""

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader

PARSER_VERSION = "1"
REGIONS = {"south": "南", "north": "北"}


@dataclass(frozen=True)
class Observation:
    region: str
    tenths: int
    pages: list
    evidence: list


@dataclass(frozen=True)
class Report:
    year: int
    week: int
    number: int
    start: str
    end: str
    observations: list
    warnings: list = field(default_factory=list)


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_pages(pages, year, week):
    compact = [re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)) for text in pages]
    prefix = rf"{year}年第{week}周"
    dates = set()
    date_pattern = prefix + (
        r"\((20\d{2})年(\d{1,2})月(\d{1,2})日[-－—–−~至]"
        r"(20\d{2})年(\d{1,2})月(\d{1,2})日\)"
    )
    for text in compact:
        for match in re.finditer(date_pattern, text):
            parts = list(map(int, match.groups()))
            start, end = date(*parts[:3]), date(*parts[3:])
            dates.add((start, end))
    if not dates:
        raise ValueError(f"{year}-W{week:02d}: missing or conflicting printed date ranges")
    valid_dates = {(start, end) for start, end in dates
                   if start == date.fromisocalendar(year, week, 1) and (end - start).days == 6}
    if len(valid_dates) != 1:
        raise ValueError(f"{year}-W{week:02d}: printed dates disagree with the report week")
    start, end = valid_dates.pop()
    warnings = []
    if len(dates) > 1:
        warnings.append({"code": "printed_date_conflict", "message": "报告内日期表述不一致，采用与周次一致的七天区间；全部原文保留。",
                         "printed_ranges": sorted([a.isoformat(), b.isoformat()] for a, b in dates)})
    # A cover can retain a hidden text layer from an older report. Obtain the
    # issue number from pages that actually contain this week's ILI statement.
    relevant = [text for text in compact if re.search(prefix + r".{0,150}?ILI%为", text)]
    numbers = {int(value) for text in relevant for value in re.findall(r"第(\d{3,5})期", text)}
    if len(numbers) != 1:
        raise ValueError(f"{year}-W{week:02d}: missing or conflicting report number")
    all_numbers = {int(value) for text in compact for value in re.findall(r"第(\d{3,5})期", text)}
    if all_numbers != numbers:
        warnings.append({"code": "other_issue_numbers", "message": "其他页面含不同期号，采用含当周 ILI 正文页面的一致期号。",
                         "other_numbers": sorted(all_numbers - numbers)})
    observations = []
    for region, label in REGIONS.items():
        pattern = prefix + rf".{{0,100}}?{label}方省份哨点医院报告的ILI%为(\d+(?:\.\d+)?)%"
        matches = []
        for page_number, text in enumerate(compact, 1):
            for match in re.finditer(pattern, text):
                value = Decimal(match[1]) * 10
                if value != value.to_integral_value() or not 0 <= value <= 1000:
                    raise ValueError(f"{year}-W{week:02d}: invalid ILI percentage")
                end_of_sentence = text.find("。", match.end())
                stop = min(end_of_sentence + 1 if end_of_sentence >= 0 else match.end(), match.end() + 240)
                matches.append((int(value), page_number, text[match.start():stop]))
        if not matches or len({value for value, _, _ in matches}) != 1:
            raise ValueError(f"{year}-W{week:02d}: missing or conflicting {region} current-week values")
        observations.append(Observation(
            region=region,
            tenths=matches[0][0],
            pages=sorted({page for _, page, _ in matches}),
            evidence=[{"page": page, "text": text} for _, page, text in matches],
        ))
    return Report(year, week, numbers.pop(), start.isoformat(), end.isoformat(), observations, warnings)


def extract_report(path):
    path = Path(path)
    match = re.fullmatch(r"(\d{2})-(\d{2})\.pdf", path.name, re.IGNORECASE)
    if not match:
        raise ValueError(f"Unsupported report filename: {path.name}; expected YY-WW.pdf")
    year, week = 2000 + int(match[1]), int(match[2])
    # Older official PDFs contain unused malformed object pointers. Text must
    # still pass date, range and duplicate-observation checks below.
    logger = logging.getLogger("pypdf")
    old_level = logger.level
    logger.setLevel(logging.ERROR)
    try:
        reader = PdfReader(path)
        pages = [reader.pages[index].extract_text() or "" for index in range(min(8, len(reader.pages)))]
    finally:
        logger.setLevel(old_level)
    return parse_pages(pages, year, week)
