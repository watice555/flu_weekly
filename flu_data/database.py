"""Versioned local SQLite archive and a small public, PDF-free JSON export."""

import argparse
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import CATALOG, DATABASE, EXPORT, REPORTS_DIR
from .extract import PARSER_VERSION, extract_report, file_hash
from .source_values import SCHEMA as SOURCE_SCHEMA, import_references
from .sources import LIST_URL, atomic_json, load_catalog, official_url, report_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    year INTEGER NOT NULL,
    week INTEGER NOT NULL CHECK(week BETWEEN 1 AND 53),
    active_sha256 TEXT NOT NULL,
    detail_url TEXT,
    pdf_url TEXT,
    UNIQUE(year, week)
);
CREATE TABLE IF NOT EXISTS report_versions (
    sha256 TEXT PRIMARY KEY,
    report_id TEXT NOT NULL REFERENCES reports(id),
    filename TEXT NOT NULL,
    report_number INTEGER NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
    report_sha256 TEXT NOT NULL REFERENCES report_versions(sha256),
    region TEXT NOT NULL CHECK(region IN ('south', 'north')),
    ili_tenths INTEGER NOT NULL CHECK(ili_tenths BETWEEN 0 AND 1000),
    pages_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY(report_sha256, region)
);
CREATE TABLE IF NOT EXISTS report_flags (
    report_sha256 TEXT PRIMARY KEY REFERENCES report_versions(sha256),
    warnings_json TEXT NOT NULL
);
CREATE VIEW IF NOT EXISTS current_observations AS
SELECT r.id AS report_id, r.year, r.week, v.period_start, v.period_end,
       o.region, o.ili_tenths / 10.0 AS ili_percent, v.report_number,
       v.sha256, o.pages_json, o.evidence_json, r.detail_url, r.pdf_url
FROM reports r JOIN report_versions v ON v.sha256 = r.active_sha256
JOIN observations o ON o.report_sha256 = v.sha256;
"""


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    connection.executescript(SOURCE_SCHEMA)
    return connection


def import_reports(reports_dir=REPORTS_DIR, database=DATABASE, catalog_path=CATALOG):
    files = sorted(Path(reports_dir).glob("*.pdf"))
    if not files:
        raise ValueError("No PDF reports found; leaving existing data unchanged")
    catalog = load_catalog(catalog_path)["reports"]
    connection = connect(database)
    changed = 0
    references_changed = 0
    try:
        with connection:
            for path in files:
                sha = file_hash(path)
                current_changed = False
                existing = connection.execute("SELECT * FROM report_versions WHERE sha256 = ?", (sha,)).fetchone()
                if existing and existing["parser_version"] == PARSER_VERSION:
                    key = existing["report_id"]
                    # Detect accidental renaming even when the bytes are cached.
                    if existing["filename"] != path.name:
                        raise ValueError(f"Duplicate or renamed report: {path.name}")
                    connection.execute("UPDATE reports SET active_sha256 = ? WHERE id = ?", (sha, key))
                else:
                    report = extract_report(path)
                    key = report_id(report.year, report.week)
                    connection.execute(
                        "INSERT INTO reports(id,year,week,active_sha256) VALUES(?,?,?,?) "
                        "ON CONFLICT(id) DO UPDATE SET active_sha256=excluded.active_sha256",
                        (key, report.year, report.week, sha),
                    )
                    connection.execute(
                        "INSERT INTO report_versions VALUES(?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(sha256) DO UPDATE SET parser_version=excluded.parser_version, "
                        "report_number=excluded.report_number,period_start=excluded.period_start, "
                        "period_end=excluded.period_end,imported_at=excluded.imported_at",
                        (sha, key, path.name, report.number, report.start, report.end, PARSER_VERSION,
                         datetime.now(timezone.utc).isoformat(timespec="seconds")),
                    )
                    connection.execute("INSERT OR REPLACE INTO report_flags VALUES(?,?)",
                                       (sha, json.dumps(report.warnings, ensure_ascii=False)))
                    for obs in report.observations:
                        connection.execute(
                            "INSERT INTO observations VALUES(?,?,?,?,?) "
                            "ON CONFLICT(report_sha256,region) DO UPDATE SET ili_tenths=excluded.ili_tenths, "
                            "pages_json=excluded.pages_json,evidence_json=excluded.evidence_json", (
                                sha, obs.region, obs.tenths, json.dumps(obs.pages),
                                json.dumps(obs.evidence, ensure_ascii=False),
                            ),
                        )
                    changed += 1
                    current_changed = True
                source = catalog.get(key, {})
                for field in ("detail_url", "pdf_url"):
                    value = source.get(field)
                    if value is not None:
                        if not official_url(value):
                            raise ValueError(f"Invalid official {field} for {key}")
                        connection.execute(f"UPDATE reports SET {field} = ? WHERE id = ?", (value, key))
                references_changed += import_references(connection, path, sha, force=current_changed)
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        counts = {
            "reports": connection.execute("SELECT COUNT(*) FROM reports").fetchone()[0],
            "versions": connection.execute("SELECT COUNT(*) FROM report_versions").fetchone()[0],
            "observations": connection.execute("SELECT COUNT(*) FROM current_observations").fetchone()[0],
            "changed": changed,
            "references_changed": references_changed,
            "references": connection.execute(
                "SELECT COUNT(*) FROM observation_sources WHERE reference_kind != 'current_report'"
            ).fetchone()[0],
            "source_targets": connection.execute("SELECT COUNT(*) FROM weekly_source_values").fetchone()[0],
        }
        return counts
    finally:
        connection.close()


def export_data(database=DATABASE, output=EXPORT):
    database = Path(database)
    if not database.exists():
        raise ValueError("Build the database before exporting")
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT * FROM current_observations ORDER BY year,week,region").fetchall()
        flags = {row[0]: json.loads(row[1]) for row in connection.execute("SELECT * FROM report_flags")}
    finally:
        connection.close()
    records = {}
    for row in rows:
        season_year = row["year"] if row["week"] >= 14 else row["year"] - 1
        record = records.setdefault(row["report_id"], {
            "id": row["report_id"], "year": row["year"], "week": row["week"],
            "start_date": row["period_start"], "end_date": row["period_end"],
            "season_start_year": season_year, "season": f"{season_year}-{season_year + 1}",
            "source": {"report_number": row["report_number"], "detail_url": row["detail_url"],
                       "pdf_url": row["pdf_url"], "sha256": row["sha256"], "pages": {}},
            "quality_notes": flags.get(row["sha256"], []),
        })
        record[row["region"]] = row["ili_percent"]
        record["source"]["pages"][row["region"]] = json.loads(row["pages_json"])
    values = list(records.values())
    if not values or any("south" not in row or "north" not in row for row in values):
        raise ValueError("Each public week must contain both regions")
    missing = []
    cursor = date.fromisoformat(values[0]["start_date"])
    last = date.fromisoformat(values[-1]["start_date"])
    while cursor <= last:
        year, week, _ = cursor.isocalendar()
        key = report_id(year, week)
        if key not in records:
            missing.append(key)
        cursor += timedelta(days=7)
    payload = {
        "schema_version": 1, "metric": "ili_percent", "source_id": "cnic",
        "unit": "%", "regions": {"south": "南方省份", "north": "北方省份"},
        "basis": "current_report", "season_start_week": 14,
        "definition": "流感样病例占哨点医院门急诊病例总数的百分比（ILI%）",
        "method": "提取各期报告正文中的当周值；不从曲线估读，不用后续同期引用覆盖，不插值。",
        "official_index_url": LIST_URL,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "coverage": {"first": values[0]["id"], "last": values[-1]["id"],
                     "reports": len(values), "observations": len(rows), "missing_weeks": missing,
                     "unresolved_links": sum(not row["source"]["detail_url"] for row in values)},
        "reports": values,
    }
    atomic_json(output, payload)
    return payload["coverage"]


def rebuild(reports_dir=REPORTS_DIR, database=DATABASE, catalog_path=CATALOG, output=EXPORT):
    result = import_reports(reports_dir, database, catalog_path)
    result["coverage"] = export_data(database, output)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build SQLite and the public JSON from local PDFs (offline).")
    parser.add_argument("--reports", type=Path, default=REPORTS_DIR)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--catalog", type=Path, default=CATALOG)
    parser.add_argument("--output", type=Path, default=EXPORT)
    args = parser.parse_args()
    print(json.dumps(rebuild(args.reports, args.database, args.catalog, args.output), ensure_ascii=False, indent=2))
