"""Store later quotations separately and expose five sources per target week."""

import json
from datetime import datetime, timezone

from . import ROOT
from .audit_revisions import extract_references, read_layout
from .extract import file_hash

REFERENCE_PARSER_VERSION = "1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS report_references (
    report_sha256 TEXT NOT NULL,
    region TEXT NOT NULL CHECK(region IN ('south', 'north')),
    kind TEXT NOT NULL CHECK(kind IN ('previous_week', 'historical_same_week')),
    target_year INTEGER NOT NULL,
    target_week INTEGER NOT NULL CHECK(target_week BETWEEN 1 AND 53),
    ili_tenths INTEGER NOT NULL CHECK(ili_tenths BETWEEN 0 AND 1000),
    relation TEXT NOT NULL,
    clause TEXT NOT NULL,
    pages_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY(report_sha256, region, kind, target_year, target_week),
    FOREIGN KEY(report_sha256, region) REFERENCES observations(report_sha256, region)
);
CREATE TABLE IF NOT EXISTS reference_extractions (
    report_sha256 TEXT PRIMARY KEY REFERENCES report_versions(sha256),
    parser_version TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    diagnostics_json TEXT NOT NULL
);
CREATE VIEW IF NOT EXISTS observation_sources AS
SELECT r.id AS target_report_id, r.year, r.week, o.region,
       'current_report' AS source_role, 'current_report' AS reference_kind,
       r.id AS source_report_id, r.year AS source_year, r.week AS source_week,
       v.sha256 AS source_sha256, v.report_number,
       o.ili_tenths, o.ili_tenths / 10.0 AS ili_percent,
       o.pages_json, o.evidence_json, r.detail_url, r.pdf_url
FROM reports r JOIN report_versions v ON v.sha256 = r.active_sha256
JOIN observations o ON o.report_sha256 = v.sha256
UNION ALL
SELECT printf('%04d-W%02d', q.target_year, q.target_week),
       q.target_year, q.target_week, q.region,
       CASE WHEN q.kind = 'previous_week' THEN 'next_week'
            WHEN r.year - q.target_year = 1 THEN 'year_plus_1'
            WHEN r.year - q.target_year = 2 THEN 'year_plus_2'
            WHEN r.year - q.target_year = 3 THEN 'year_plus_3'
            ELSE 'outside_window' END,
       q.kind, r.id, r.year, r.week, v.sha256, v.report_number,
       q.ili_tenths, q.ili_tenths / 10.0,
       q.pages_json, q.evidence_json, r.detail_url, r.pdf_url
FROM reports r JOIN report_versions v ON v.sha256 = r.active_sha256
JOIN report_references q ON q.report_sha256 = v.sha256;
CREATE VIEW IF NOT EXISTS weekly_source_values AS
SELECT target_report_id, year, week, region,
       MAX(CASE WHEN source_role = 'current_report' THEN ili_percent END) AS current_report,
       MAX(CASE WHEN source_role = 'next_week' THEN ili_percent END) AS next_week,
       MAX(CASE WHEN source_role = 'year_plus_1' THEN ili_percent END) AS year_plus_1,
       MAX(CASE WHEN source_role = 'year_plus_2' THEN ili_percent END) AS year_plus_2,
       MAX(CASE WHEN source_role = 'year_plus_3' THEN ili_percent END) AS year_plus_3
FROM observation_sources
WHERE source_role != 'outside_window'
GROUP BY target_report_id, year, week, region;
"""


def import_references(connection, path, sha, *, force=False,
                      cache=ROOT / "tmp" / "revision-audit-text"):
    """Use the caller's transaction; retain quotations from older PDF versions."""
    existing = connection.execute(
        "SELECT parser_version FROM reference_extractions WHERE report_sha256 = ?", (sha,)
    ).fetchone()
    if existing and existing[0] == REFERENCE_PARSER_VERSION and not force:
        return False
    report = connection.execute(
        "SELECT r.year, r.week FROM reports r JOIN report_versions v ON v.report_id = r.id "
        "WHERE v.sha256 = ?", (sha,)
    ).fetchone()
    originals = connection.execute(
        "SELECT region, ili_tenths FROM observations WHERE report_sha256 = ?", (sha,)
    ).fetchall()
    if report is None or {row[0] for row in originals} != {"south", "north"}:
        raise ValueError("Both original regional observations are required before reference import")
    pages = read_layout(path, cache)
    if file_hash(path) != sha:
        raise ValueError(f"Source PDF changed during import: {path.name}")
    year, week = report
    connection.execute("DELETE FROM report_references WHERE report_sha256 = ?", (sha,))
    diagnostics = {}
    for region, current in originals:
        refs, notes = extract_references(pages, year, week, region, current)
        cited_years = sorted({row["target_year"] for row in refs if row["kind"] == "historical_same_week"})
        expected_years = list(range(year - 3, year))
        if cited_years != expected_years:
            notes["year_label_set"] = cited_years
            notes["usual_preceding_three_years"] = expected_years
        for ref in refs:
            value = ref["reference_tenths"]
            expected_relation = "高于" if current > value else "低于" if current < value else "与"
            if ref["relation"] != expected_relation:
                notes.setdefault("comparison_word_inconsistent", []).append({
                    "target_id": ref["target_id"], "current_tenths": current,
                    "reference_tenths": value, "clause": ref["clause"], "pages": ref["pages"],
                })
            connection.execute("INSERT INTO report_references VALUES(?,?,?,?,?,?,?,?,?,?)", (
                sha, region, ref["kind"], ref["target_year"], ref["target_week"], value,
                ref["relation"], ref["clause"], json.dumps(ref["pages"]),
                json.dumps(ref["evidence"], ensure_ascii=False),
            ))
        diagnostics[region] = notes
    connection.execute("INSERT OR REPLACE INTO reference_extractions VALUES(?,?,?,?)", (
        sha, REFERENCE_PARSER_VERSION, datetime.now(timezone.utc).isoformat(timespec="seconds"),
        json.dumps(diagnostics, ensure_ascii=False),
    ))
    return True
