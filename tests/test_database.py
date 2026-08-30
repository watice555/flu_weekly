import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from flu_data.database import export_data, import_reports
from flu_data.extract import parse_pages
from flu_data.sources import parse_detail, parse_index


def pages(south="4.5", north="2.8"):
    text = f"""第923期
    2026 年第 34 周（2026 年 8 月 17 日－2026 年 8 月 23 日），
    南方省份哨点医院报告的 ILI%为 {south}%，低于前一周水平（4.8%），
    高于2023年、2024年和2025年同期水平（4.1%、3.6%和3.2%）。
    2026年第34周，北方省份哨点医院报告的ILI%为{north}%，低于前一周水平（2.9%）。"""
    return [text, text]


class ExtractionTests(unittest.TestCase):
    def test_current_values_and_duplicate_evidence(self):
        report = parse_pages(pages(), 2026, 34)
        self.assertEqual((report.start, report.end), ("2026-08-17", "2026-08-23"))
        self.assertEqual([item.tenths for item in report.observations], [45, 28])
        self.assertEqual(report.observations[0].pages, [1, 2])
        self.assertIn("2023年", report.observations[0].evidence[0]["text"])

    def test_conflicting_abstract_and_body_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "conflicting south"):
            parse_pages([pages()[0], pages(south="4.6")[1]], 2026, 34)

    def test_comparative_values_cannot_replace_missing_current_value(self):
        with self.assertRaisesRegex(ValueError, "missing or conflicting south"):
            parse_pages([text.replace("ILI%为 4.5%", "ILI%未报告") for text in pages()], 2026, 34)

    def test_wrong_filename_week_and_wrong_printed_dates_are_rejected(self):
        with self.assertRaises(ValueError):
            parse_pages(pages(), 2026, 33)
        with self.assertRaisesRegex(ValueError, "printed dates disagree"):
            parse_pages([text.replace("8 月 17", "8 月 18") for text in pages()], 2026, 34)

    def test_out_of_range_percent_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid ILI"):
            parse_pages(pages(south="101.0"), 2026, 34)

    def test_one_printed_date_typo_uses_valid_range_with_a_warning(self):
        report = parse_pages([pages()[0], pages()[1].replace("8 月 17", "8 月 12")], 2026, 34)
        self.assertEqual(report.start, "2026-08-17")
        self.assertEqual(report.warnings[0]["code"], "printed_date_conflict")
        self.assertEqual(len(report.warnings[0]["printed_ranges"]), 2)

    def test_hidden_old_cover_number_does_not_override_body_number(self):
        report = parse_pages(["总第923期 原模板第900期", *pages()], 2026, 34)
        self.assertEqual(report.number, 923)
        self.assertEqual(report.warnings[0]["other_numbers"], [900])

    def test_year_boundary(self):
        texts = [text.replace("2026 年第 34", "2026 年第 1")
                 .replace("2026年第34", "2026年第1")
                 .replace("2026 年 8 月 17 日", "2025 年 12 月 29 日")
                 .replace("2026 年 8 月 23 日", "2026 年 1 月 4 日") for text in pages()]
        self.assertEqual(parse_pages(texts, 2026, 1).start, "2025-12-29")


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.reports = self.root / "reports"
        self.reports.mkdir()
        self.pdf = self.reports / "26-34.pdf"
        self.pdf.write_bytes(b"fixture-version-1")
        self.db = self.root / "data.sqlite3"
        self.catalog = self.root / "sources.json"
        self.output = self.root / "public.json"
        self.source = "https://ivdc.chinacdc.cn/cnic/zyzx/lgzb/202608/report.htm"
        self.catalog.write_text(json.dumps({"schema_version": 1, "reports": {
            "2026-W34": {"detail_url": self.source, "pdf_url": self.source + ".pdf"}}}))

    def build(self):
        return import_reports(self.reports, self.db, self.catalog)

    def test_idempotent_import_and_version_retention(self):
        with patch("flu_data.database.extract_report", return_value=parse_pages(pages(), 2026, 34)):
            self.assertEqual(self.build()["changed"], 1)
        with patch("flu_data.database.extract_report", side_effect=AssertionError("cached PDF reparsed")):
            self.assertEqual(self.build()["changed"], 0)
        self.pdf.write_bytes(b"fixture-version-2")
        with patch("flu_data.database.extract_report", return_value=parse_pages(pages(south="4.6"), 2026, 34)):
            counts = self.build()
        self.assertEqual((counts["reports"], counts["versions"], counts["observations"]), (1, 2, 2))
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 4)
            self.assertEqual(connection.execute("SELECT ili_percent FROM current_observations WHERE region='south'").fetchone()[0], 4.6)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_batch_failure_preserves_previous_data(self):
        with patch("flu_data.database.extract_report", return_value=parse_pages(pages(), 2026, 34)):
            self.build()
        self.pdf.write_bytes(b"replacement")
        (self.reports / "26-35.pdf").write_bytes(b"bad-new-report")
        with patch("flu_data.database.extract_report", side_effect=[parse_pages(pages(south="4.6"), 2026, 34), ValueError("bad PDF")]):
            with self.assertRaisesRegex(ValueError, "bad PDF"):
                self.build()
        export_data(self.db, self.output)
        self.assertEqual(json.loads(self.output.read_text())["reports"][0]["south"], 4.5)

    def test_public_export_has_official_links_no_local_paths_and_preserves_gap(self):
        earlier = replace(parse_pages(pages(), 2026, 34), week=32, start="2026-08-03", end="2026-08-09", number=921)
        (self.reports / "26-32.pdf").write_bytes(b"earlier")
        with patch("flu_data.database.extract_report", side_effect=[earlier, parse_pages(pages(), 2026, 34)]):
            self.build()
        coverage = export_data(self.db, self.output)
        payload = json.loads(self.output.read_text())
        self.assertEqual(coverage["missing_weeks"], ["2026-W33"])
        self.assertEqual(payload["reports"][-1]["source"]["detail_url"], self.source)
        self.assertNotIn(str(self.root), self.output.read_text())
        self.assertNotIn("filename", self.output.read_text())

    def test_invalid_link_rolls_back_import(self):
        self.catalog.write_text(json.dumps({"schema_version": 1, "reports": {"2026-W34": {"detail_url": "javascript:alert(1)"}}}))
        with patch("flu_data.database.extract_report", return_value=parse_pages(pages(), 2026, 34)):
            with self.assertRaisesRegex(ValueError, "Invalid official"):
                self.build()


class SourceTests(unittest.TestCase):
    def test_official_index_and_relative_links(self):
        result = parse_index('<a href="./202608/report.htm">2026 第34周</a><a href="https://example.org/x">2026 第33周</a>',
                             "https://ivdc.chinacdc.cn/cnic/zyzx/lgzb/")
        self.assertEqual(list(result), ["2026-W34"])

    def test_revised_pdf_is_preferred_and_ambiguity_fails(self):
        url = "https://ivdc.chinacdc.cn/cnic/zyzx/lgzb/202608/report.htm"
        result = parse_detail('<a href="old.pdf">旧版</a><a href="fixed.pdf">以此为准.pdf</a>', url)
        self.assertTrue(result["pdf_url"].endswith("fixed.pdf"))
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            parse_detail('<a href="one.pdf">一</a><a href="two.pdf">二</a>', url)


if __name__ == "__main__":
    unittest.main()
