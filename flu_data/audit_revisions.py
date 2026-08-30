"""Compare later textual ILI references against as-published weekly values.

This is an audit only: it never modifies the source PDFs or the main database.
"""

import argparse
import json
import logging
import re
import sqlite3
import statistics
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader

from . import DATABASE, REPORTS_DIR, ROOT
from .extract import file_hash
from .sources import atomic_json, report_id

REGIONS = {"south": "南方", "north": "北方"}
KINDS = {"previous_week": "前一周引用", "historical_same_week": "往年同期引用"}
PERCENT = r"(\d+(?:\.\d+)?)%"


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def tenths(value):
    result = Decimal(value) * 10
    if result != result.to_integral_value() or not 0 <= result <= 1000:
        raise ValueError(f"Invalid reported percentage: {value}")
    return int(result)


def parse_years(expression):
    years = [int(year) for year in re.findall(r"20\d{2}", expression)]
    residue = re.sub(r"20\d{2}", "", expression)
    if not years or re.sub(r"[年~\-—–、,和至及]", "", residue):
        raise ValueError(f"Unrecognized year expression: {expression}")
    if any(symbol in residue for symbol in "~-—–至"):
        if len(years) != 2 or not 0 < years[1] - years[0] <= 5:
            raise ValueError(f"Ambiguous year range: {expression}")
        return list(range(years[0], years[1] + 1))
    if len(years) != len(set(years)):
        raise ValueError(f"Repeated year label: {expression}")
    return years


def parse_references(text, year, week):
    """Parse one region's paragraph, preserving literal historical year labels."""
    text = compact(text)
    previous = list(re.finditer(r"(高于|低于|与)前一周水平\(" + PERCENT + r"\)(持平)?", text))
    result = []
    prior_date = date.fromisocalendar(year, week, 1) - timedelta(days=7)
    prior_year, prior_week, _ = prior_date.isocalendar()
    for match in previous:
        result.append({"kind": "previous_week", "target_id": report_id(prior_year, prior_week),
                       "target_year": prior_year, "target_week": prior_week,
                       "reference_tenths": tenths(match[2]), "relation": match[1], "clause": match[0]})
    historical = list(re.finditer(r"(高于|低于|与)(20\d{2}[^()%]*?)同期水平\(([^()]*)\)(持平)?", text))
    for match in historical:
        years = parse_years(match[2])
        values = re.findall(PERCENT, match[3])
        residue = re.sub(PERCENT, "", match[3])
        if re.sub(r"[、,和及]", "", residue) or len(years) != len(values):
            raise ValueError(f"Year/value count mismatch: {match[0]}")
        for target_year, value in zip(years, values, strict=True):
            if target_year >= year:
                raise ValueError(f"Reference is not historical: {match[0]}")
            result.append({"kind": "historical_same_week", "target_id": report_id(target_year, week),
                           "target_year": target_year, "target_week": week,
                           "reference_tenths": tenths(value), "relation": match[1], "clause": match[0]})
    # Each available report states the preceding week and three historical years.
    if len(previous) != 1 or sum(row["kind"] == "historical_same_week" for row in result) != 3:
        raise ValueError(f"Incomplete reference paragraph: {text}")
    keys = [(row["kind"], row["target_id"]) for row in result]
    if len(keys) != len(set(keys)):
        raise ValueError(f"Duplicate target reference: {text}")
    return result


def read_layout(path, cache):
    sha = file_hash(path)
    cache_path = Path(cache) / f"{sha}-layout-v1.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    logger = logging.getLogger("pypdf")
    old_level = logger.level
    logger.setLevel(logging.ERROR)
    try:
        reader = PdfReader(path)
        pages = [reader.pages[index].extract_text(extraction_mode="layout") or ""
                 for index in range(min(8, len(reader.pages)))]
    finally:
        logger.setLevel(old_level)
    atomic_json(cache_path, pages)
    return pages


def extract_references(pages, year, week, region, original_current):
    label = REGIONS[region]
    pattern = re.compile(rf"{year}年第{week}周(?:(?!ILI%|。).){{0,100}}?{label}省份哨点医院报告的ILI%为" + PERCENT)
    occurrences = []
    failures = []
    for page_no, raw_text in enumerate(pages, 1):
        text = compact(raw_text)
        for match in pattern.finditer(text):
            if tenths(match[1]) != original_current:
                raise ValueError(f"{year}-W{week:02d} {region}: layout/current database disagreement")
            remainder = text[match.end():]
            boundaries = [m.start() for expression in [r"。", r"\(图\d+\)", rf"{year}年第{week}周"]
                          if (m := re.search(expression, remainder))]
            end = match.end() + (min(boundaries) if boundaries else min(len(remainder), 350))
            paragraph = text[match.start():end]
            try:
                refs = parse_references(paragraph, year, week)
            except ValueError as error:
                failures.append({"page": page_no, "reason": str(error), "paragraph": paragraph})
                continue
            occurrences.append({"page": page_no, "paragraph": paragraph, "refs": refs})
    if not occurrences:
        raise ValueError(f"No complete reference paragraph: {year}-W{week:02d} {region}: {failures}")
    merged = defaultdict(list)
    for occurrence in occurrences:
        for ref in occurrence["refs"]:
            merged[(ref["kind"], ref["target_id"])].append((ref, occurrence))
    rows, conflicts = [], []
    for (kind, target), candidates in merged.items():
        values = {ref["reference_tenths"] for ref, _ in candidates}
        if len(values) != 1:
            conflicts.append({"kind": kind, "target_id": target,
                              "candidates": [{"page": occurrence["page"], "value": ref["reference_tenths"] / 10,
                                              "text": occurrence["paragraph"]} for ref, occurrence in candidates]})
            continue
        row = dict(candidates[-1][0])
        row["pages"] = sorted({occurrence["page"] for _, occurrence in candidates})
        row["evidence"] = [{"page": occurrence["page"], "text": occurrence["paragraph"]} for _, occurrence in candidates]
        rows.append(row)
    return rows, {"incomplete_occurrences": failures, "conflicting_references": conflicts}


def summarize(rows):
    paired = [row for row in rows if row["status"] == "matched"]
    changed = [row for row in paired if row["delta_tenths"] != 0]
    absolute = [abs(row["delta_tenths"]) / 10 for row in paired]
    changed_abs = [abs(row["delta_tenths"]) / 10 for row in changed]
    target_keys = {(row["target_id"], row["region"]) for row in paired}
    affected = {(row["target_id"], row["region"]) for row in changed}
    return {
        "references": len(rows), "paired": len(paired), "different": len(changed),
        "same": len(paired) - len(changed), "different_rate": len(changed) / len(paired) if paired else None,
        "unique_targets_compared": len(target_keys), "unique_targets_affected": len(affected),
        "mean_absolute_pp_all": statistics.mean(absolute) if absolute else None,
        "mean_absolute_pp_changed": statistics.mean(changed_abs) if changed_abs else None,
        "median_absolute_pp_changed": statistics.median(changed_abs) if changed_abs else None,
        "maximum_absolute_pp": max(absolute) if absolute else None,
        "mean_signed_pp_all": statistics.mean(row["delta_tenths"] / 10 for row in paired) if paired else None,
        "upward": sum(row["delta_tenths"] > 0 for row in paired),
        "downward": sum(row["delta_tenths"] < 0 for row in paired),
        "absolute_distribution": dict(sorted(Counter(f"{abs(row['delta_tenths']) / 10:.1f}" for row in paired).items(), key=lambda item: float(item[0]))),
    }


def audit(database=DATABASE, reports_dir=REPORTS_DIR, cache=ROOT / "tmp" / "revision-audit-text"):
    connection = sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        baseline = {}
        for raw in connection.execute("SELECT * FROM current_observations ORDER BY year,week,region"):
            row = dict(raw)
            row["tenths"] = tenths(str(row["ili_percent"]))
            baseline[(row["report_id"], row["region"])] = row
    finally:
        connection.close()
    if not baseline:
        raise ValueError("No original observations available for audit")
    references, issues = [], []
    page_cache = {}
    for (key, region), original in baseline.items():
        if key not in page_cache:
            path = Path(reports_dir) / f"{original['year'] % 100:02d}-{original['week']:02d}.pdf"
            if file_hash(path) != original["sha256"]:
                raise ValueError(f"{key}: source PDF no longer matches the database")
            page_cache[key] = read_layout(path, cache)
        refs, diagnostics = extract_references(page_cache[key], original["year"], original["week"], region, original["tenths"])
        if any(diagnostics.values()):
            issues.append({"report_id": key, "region": region, **diagnostics})
        cited_years = {ref["target_year"] for ref in refs if ref["kind"] == "historical_same_week"}
        expected = set(range(original["year"] - 3, original["year"]))
        if cited_years != expected:
            issues.append({"report_id": key, "region": region, "year_label_set": sorted(cited_years),
                           "usual_preceding_three_years": sorted(expected),
                           "note": "年份标签与本批报告通常采用的前三年集合不同；按原文保留，不猜测改年。"})
        for ref in refs:
            target = baseline.get((ref["target_id"], region))
            row = {
                **ref, "citing_report": key, "citing_year": original["year"], "citing_week": original["week"],
                "region": region, "citing_report_number": original["report_number"],
                "citing_sha256": original["sha256"], "citing_url": original["detail_url"],
                "citing_pdf_url": original["pdf_url"], "current_tenths": original["tenths"],
                "status": "matched" if target else "original_not_archived",
                "original_tenths": target["tenths"] if target else None,
                "original_url": target["detail_url"] if target else None,
                "original_pdf_url": target["pdf_url"] if target else None,
                "original_pages": json.loads(target["pages_json"]) if target else None,
                "original_evidence": json.loads(target["evidence_json"]) if target else None,
                "original_sha256": target["sha256"] if target else None,
                "delta_tenths": ref["reference_tenths"] - target["tenths"] if target else None,
            }
            expected_relation = "高于" if original["tenths"] > ref["reference_tenths"] else "低于" if original["tenths"] < ref["reference_tenths"] else "与"
            row["comparison_word_consistent"] = ref["relation"] == expected_relation
            if not row["comparison_word_consistent"]:
                issues.append({"report_id": key, "region": region, "target_id": ref["target_id"],
                               "code": "comparison_word_inconsistent", "pages": ref["pages"],
                               "current_tenths": original["tenths"], "reference_tenths": ref["reference_tenths"],
                               "clause": ref["clause"],
                               "note": "高于/低于/持平措辞与该句已显示数值不相符；只记录，不修正数值。"})
            references.append(row)
    keys = sorted({key for key, region in baseline})
    first = date.fromisoformat(next(iter(baseline.values()))["period_start"])
    last = max(date.fromisoformat(row["period_start"]) for row in baseline.values())
    missing_internal = []
    cursor = first
    while cursor <= last:
        year, week, _ = cursor.isocalendar()
        key = report_id(year, week)
        if key not in keys:
            missing_internal.append(key)
        cursor += timedelta(days=7)
    first_year, first_week, _ = first.isocalendar()
    season_start_year = first_year if first_week >= 14 else first_year - 1
    cursor = date.fromisocalendar(season_start_year, 14, 1)
    missing_first_season = []
    while cursor < first:
        year, week, _ = cursor.isocalendar()
        missing_first_season.append(report_id(year, week))
        cursor += timedelta(days=7)
    paired = [row for row in references if row["status"] == "matched"]
    grouped = defaultdict(list)
    for row in paired:
        grouped[(row["target_id"], row["region"])].append(row)
    trajectories = []
    for (target_id, region), rows in sorted(grouped.items()):
        rows.sort(key=lambda row: row["citing_report"])
        versions = sorted({rows[0]["original_tenths"], *(row["reference_tenths"] for row in rows)})
        trajectories.append({"target_id": target_id, "region": region, "original_tenths": rows[0]["original_tenths"],
                             "distinct_tenths": versions, "reference_count": len(rows),
                             "latest_report": rows[-1]["citing_report"], "latest_reference_tenths": rows[-1]["reference_tenths"],
                             "max_absolute_delta_tenths": max(abs(row["delta_tenths"]) for row in rows),
                             "reference_series": [{key: row[key] for key in ["citing_report", "kind", "reference_tenths", "delta_tenths", "pages"]} for row in rows]})
    return {
        "schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "coverage": {"first_report": keys[0], "last_report": keys[-1], "reports": len(keys),
                     "original_observations": len(baseline), "missing_internal": missing_internal,
                     "missing_in_first_displayed_season": missing_first_season},
        "summary": {"all": summarize(references),
                    "by_kind": {kind: summarize([row for row in references if row["kind"] == kind]) for kind in KINDS},
                    "by_region_and_kind": {f"{region}/{kind}": summarize([row for row in references if row["kind"] == kind and row["region"] == region]) for region in REGIONS for kind in KINDS},
                    "historical_by_region_and_target_year": {
                        f"{region}/{year}": summarize([row for row in references if row["kind"] == "historical_same_week" and row["region"] == region and row["target_year"] == year])
                        for region in REGIONS for year in sorted({row["target_year"] for row in paired if row["kind"] == "historical_same_week"})},
                    "by_target_year": {str(year): summarize([row for row in references if row["target_year"] == year]) for year in sorted({row["target_year"] for row in references})}},
        "issues": issues, "references": references, "trajectories": trajectories,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit later textual references against original values (offline, read-only).")
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--reports", type=Path, default=REPORTS_DIR)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "revision_audit")
    args = parser.parse_args()
    result = audit(args.database, args.reports)
    atomic_json(args.output / "audit.json", result)
    from .audit_report import write_report
    write_report(result, args.output)
    print(json.dumps({"coverage": result["coverage"], "summary": result["summary"], "issues": result["issues"]}, ensure_ascii=False, indent=2))
