import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from flu_data.database import SCHEMA, rebuild
from flu_data.extract import file_hash, parse_pages


class SourceValueTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.reports = self.root / "reports"
        self.reports.mkdir()
        self.db = self.root / "archive.sqlite3"
        self.output = self.root / "ili.json"
        self.catalog = self.root / "sources.json"
        self.catalog.write_text('{"schema_version":1,"reports":{}}')
        for name, reader in [
            ("flu_data.database.extract_report", self.extract_current),
            ("flu_data.source_values.read_layout", lambda path, cache: json.loads(path.read_text())),
        ]:
            mocked = patch(name, side_effect=reader)
            mocked.start()
            self.addCleanup(mocked.stop)

    def extract_current(self, path):
        year, week = map(int, path.stem.split("-"))
        return parse_pages(json.loads(path.read_text()), 2000 + year, week)

    def write_report(self, year, week, south="4.0", north="3.0", previous="4.0", historical=None):
        start = date.fromisocalendar(year, week, 1)
        end = start + timedelta(days=6)
        historical = historical or {year - 3: "1.0", year - 2: "2.0", year - 1: "3.0"}
        years = "、".join(f"{value}年" for value in historical)
        values = "、".join(f"{value}%" for value in historical.values())
        text = f"第900期 {year}年第{week}周（{start.year}年{start.month}月{start.day}日－{end.year}年{end.month}月{end.day}日）。"
        for region, value in [("南方", south), ("北方", north)]:
            text += (f"{year}年第{week}周，{region}省份哨点医院报告的ILI%为{value}%，"
                     f"低于前一周水平（{previous}%），高于{years}同期水平（{values}）。")
        path = self.reports / f"{year % 100:02d}-{week:02d}.pdf"
        path.write_text(json.dumps([text, text], ensure_ascii=False))
        return path

    def build(self):
        return rebuild(self.reports, self.db, self.catalog, self.output)

    def rows(self, sql, parameters=()):
        with sqlite3.connect(self.db) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(sql, parameters)]

    def target(self, key, region="south"):
        return self.rows("SELECT * FROM weekly_source_values WHERE target_report_id=? AND region=?", (key, region))[0]

    def test_equal_values_remain_separate_and_future_sources_are_null(self):
        self.write_report(2024, 7)
        self.write_report(2024, 8, previous="4.0")
        self.write_report(2025, 7, historical={2022: "1.0", 2023: "2.0", 2024: "4.2"})
        self.write_report(2026, 7, historical={2023: "1.0", 2024: "4.3", 2025: "3.0"})
        counts = self.build()
        row = self.target("2024-W07")
        self.assertEqual([row[key] for key in ["current_report", "next_week", "year_plus_1", "year_plus_2", "year_plus_3"]],
                         [4.0, 4.0, 4.2, 4.3, None])
        self.assertEqual(self.target("2024-W07", "north")["current_report"], 3.0)
        sources = self.rows("SELECT * FROM observation_sources WHERE target_report_id='2024-W07' AND region='south'")
        self.assertEqual(len(sources), 4)
        self.assertEqual({r["source_report_id"] for r in sources}, {"2024-W07", "2024-W08", "2025-W07", "2026-W07"})
        self.assertTrue(all(json.loads(r["pages_json"]) == [1, 2] and len(json.loads(r["evidence_json"])) == 2 for r in sources))
        self.assertEqual(counts["references"], 32)
        with patch("flu_data.source_values.read_layout", side_effect=AssertionError("cached PDF reparsed")):
            cached = self.build()
        self.assertEqual((cached["changed"], cached["references_changed"]), (0, 0))
        self.write_report(2027, 7, historical={2024: "4.4", 2025: "2.0", 2026: "3.0"})
        self.assertEqual(self.build()["references_changed"], 1)
        self.assertEqual(self.target("2024-W07")["year_plus_3"], 4.4)
        self.assertEqual(self.target("2024-W07")["current_report"], 4.0)

    def test_unarchived_original_is_null_and_never_added_to_public_series(self):
        self.write_report(2026, 7, historical={2023: "1.0", 2024: "4.3", 2025: "3.0"})
        self.build()
        row = self.target("2024-W07")
        self.assertIsNone(row["current_report"])
        self.assertIsNone(row["year_plus_1"])
        self.assertEqual(row["year_plus_2"], 4.3)
        payload = json.loads(self.output.read_text())
        self.assertEqual([row["id"] for row in payload["reports"]], ["2026-W07"])
        self.write_report(2024, 7)
        self.build()
        self.assertEqual(self.target("2024-W07")["current_report"], 4.0)

    def test_following_week_crosses_iso_year_and_preserves_week_53(self):
        self.write_report(2020, 53, south="6.0")
        self.write_report(2021, 1, previous="6.1")
        self.build()
        self.assertEqual(self.target("2020-W53")["next_week"], 6.1)
        self.assertIsNone(self.target("2020-W53")["year_plus_1"])
        self.assertEqual(self.rows("SELECT source_report_id FROM observation_sources WHERE target_report_id='2020-W53' AND source_role='next_week' AND region='south'")[0]["source_report_id"], "2021-W01")

    def test_literal_anomalous_year_is_not_shifted_into_three_year_window(self):
        self.write_report(2024, 1)
        self.write_report(2025, 1, historical={2021: "1.0", 2022: "2.0", 2023: "3.0"})
        self.build()
        self.assertIsNone(self.target("2024-W01")["year_plus_1"])
        row = self.rows("SELECT * FROM observation_sources WHERE target_report_id='2021-W01' AND source_year=2025 AND region='south'")[0]
        self.assertEqual((row["source_role"], row["ili_percent"]), ("outside_window", 1.0))
        notes = [json.loads(r["diagnostics_json"]) for r in self.rows("SELECT diagnostics_json FROM reference_extractions")]
        self.assertTrue(any(r["south"].get("year_label_set") == [2021, 2022, 2023] for r in notes))

    def test_changed_pdf_retains_old_reference_version_without_changing_target(self):
        self.write_report(2024, 7)
        path = self.write_report(2025, 7, historical={2022: "1.0", 2023: "2.0", 2024: "4.2"})
        self.build()
        before = file_hash(path)
        self.write_report(2025, 7, historical={2022: "1.0", 2023: "2.0", 2024: "4.4"})
        counts = self.build()
        self.assertEqual((counts["versions"], counts["references"], counts["references_changed"]), (3, 16, 1))
        self.assertEqual(len(self.rows("SELECT * FROM report_references")), 24)
        self.assertEqual(self.rows("SELECT ili_tenths FROM report_references WHERE report_sha256=? AND region='south' AND target_year=2024", (before,))[0]["ili_tenths"], 42)
        self.assertEqual(self.target("2024-W07")["year_plus_1"], 4.4)
        self.assertEqual(self.target("2024-W07")["current_report"], 4.0)
        self.assertEqual(self.rows("PRAGMA foreign_key_check"), [])
        with patch("flu_data.database.PARSER_VERSION", "future-parser"):
            self.assertEqual(self.build()["references_changed"], 2)

    def test_reference_parse_failure_rolls_back_whole_batch_and_public_export(self):
        self.write_report(2024, 7)
        self.build()
        public_before = self.output.read_bytes()
        self.write_report(2024, 7, south="4.9")
        bad = self.write_report(2024, 8)
        bad.write_text(bad.read_text().replace("同期水平", "无法辨认"))
        with self.assertRaisesRegex(ValueError, "No complete reference"):
            self.build()
        self.assertEqual(self.target("2024-W07")["current_report"], 4.0)
        self.assertEqual(self.output.read_bytes(), public_before)
        self.assertEqual(len(self.rows("SELECT * FROM reports")), 1)
        self.assertEqual(len(self.rows("SELECT * FROM report_references")), 8)

    def test_conflicting_quotation_stays_null_with_diagnostics(self):
        self.write_report(2024, 7)
        path = self.write_report(2024, 8, previous="4.1")
        texts = json.loads(path.read_text())
        texts[1] = texts[1].replace("前一周水平（4.1%）", "前一周水平（4.2%）")
        path.write_text(json.dumps(texts, ensure_ascii=False))
        self.build()
        self.assertIsNone(self.target("2024-W07")["next_week"])
        notes = json.loads(self.rows("SELECT diagnostics_json FROM reference_extractions WHERE report_sha256=?", (file_hash(path),))[0]["diagnostics_json"])
        self.assertEqual(notes["south"]["conflicting_references"][0]["target_id"], "2024-W07")

    def test_existing_database_is_backfilled_without_replacing_originals(self):
        self.write_report(2024, 7)
        self.write_report(2024, 8, previous="4.1")
        # Reproduce the schema and imports available before this migration.
        with sqlite3.connect(self.db) as connection:
            connection.executescript(SCHEMA)
        with patch("flu_data.database.import_references", return_value=False):
            self.build()
        originals = self.rows("SELECT * FROM observations ORDER BY report_sha256,region")
        payload = json.loads(self.output.read_text())
        with patch("flu_data.database.extract_report", side_effect=AssertionError("original unnecessarily reparsed")):
            result = self.build()
        self.assertEqual((result["changed"], result["references_changed"]), (0, 2))
        self.assertEqual(originals, self.rows("SELECT * FROM observations ORDER BY report_sha256,region"))
        after = json.loads(self.output.read_text())
        payload.pop("generated_at")
        after.pop("generated_at")
        self.assertEqual(payload, after)


if __name__ == "__main__":
    unittest.main()
