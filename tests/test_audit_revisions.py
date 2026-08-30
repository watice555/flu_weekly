import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flu_data.audit_revisions import audit, extract_references, parse_references, parse_years, summarize, tenths


def paragraph(region="south", year=2026, week=34, current="4.5", previous="4.8"):
    label = "南方" if region == "south" else "北方"
    return (f"{year}年第{week}周，{label}省份哨点医院报告的ILI%为{current}%，"
            f"低于前一周水平（{previous}%），高于2023年、2024年和2025年同期水平（4.1%、3.6%和3.2%）。")


class ReferenceParsingTests(unittest.TestCase):
    def test_explicit_labels_and_separate_comparison_groups(self):
        text = paragraph().replace("高于2023年、2024年和2025年同期水平（4.1%、3.6%和3.2%）",
                                   "高于2023年和2025年同期水平（4.1%和3.2%），低于2024年同期水平（6.2%）")
        refs = parse_references(text, 2026, 34)
        self.assertEqual({r["target_id"]: r["reference_tenths"] for r in refs},
                         {"2026-W33": 48, "2023-W34": 41, "2025-W34": 32, "2024-W34": 62})

    def test_year_range_and_53_week_boundary(self):
        text = paragraph(year=2021, week=1).replace("2023年、2024年和2025年", "2018～2020年")
        refs = parse_references(text, 2021, 1)
        self.assertEqual([r["target_id"] for r in refs], ["2020-W53", "2018-W01", "2019-W01", "2020-W01"])

    def test_unusual_year_labels_are_not_shifted(self):
        text = paragraph(year=2025, week=1).replace("2023年、2024年和2025年", "2021~2023年")
        self.assertEqual([r["target_year"] for r in parse_references(text, 2025, 1)], [2024, 2021, 2022, 2023])

    def test_ambiguous_or_incomplete_clauses_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "count mismatch"):
            parse_references(paragraph().replace("4.1%、", ""), 2026, 34)
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            parse_references("低于前一周水平（4.8%）", 2026, 34)
        for expression in ["2023年2023年", "2024~2022年", "2023年或2024年"]:
            with self.assertRaises(ValueError):
                parse_years(expression)
        with self.assertRaisesRegex(ValueError, "Repeated"):
            parse_references(paragraph().replace("2024年", "2023年"), 2026, 34)

    def test_exact_decimal_precision(self):
        self.assertEqual(tenths("3.6"), 36)
        for value in ["3.65", "101", "-0.1"]:
            with self.assertRaises(ValueError):
                tenths(value)

    def test_duplicate_summary_body_are_counted_once_and_region_isolated(self):
        text = paragraph() + paragraph("north", current="2.8", previous="2.9")
        refs, diagnostics = extract_references([text, paragraph()], 2026, 34, "south", 45)
        self.assertEqual(len(refs), 4)
        self.assertTrue(all(r["pages"] == [1, 2] for r in refs))
        self.assertTrue(all("北方" not in item["text"] for r in refs for item in r["evidence"]))
        self.assertFalse(any(diagnostics.values()))

    def test_conflicting_repeat_is_excluded_and_partial_copy_is_recorded(self):
        refs, diagnostics = extract_references([paragraph(), paragraph(previous="4.9")], 2026, 34, "south", 45)
        self.assertEqual(len(refs), 3)
        self.assertEqual(diagnostics["conflicting_references"][0]["target_id"], "2026-W33")
        partial = paragraph().split("，高于2023")[0] + "。"
        refs, diagnostics = extract_references([partial, paragraph()], 2026, 34, "south", 45)
        self.assertEqual(len(refs), 4)
        self.assertEqual(len(diagnostics["incomplete_occurrences"]), 1)
        self.assertTrue(all(r["pages"] == [2] for r in refs))

    def test_current_value_disagreement_stops_audit(self):
        with self.assertRaisesRegex(ValueError, "database disagreement"):
            extract_references([paragraph(current="4.6")], 2026, 34, "south", 45)

    def test_quote_counts_are_distinct_from_affected_targets(self):
        rows = [{"status": "matched", "target_id": "2024-W01", "region": "south", "delta_tenths": d}
                for d in [1, -3, 0]]
        rows.append({"status": "original_not_archived"})
        stats = summarize(rows)
        self.assertEqual((stats["references"], stats["paired"], stats["different"]), (4, 3, 2))
        self.assertEqual((stats["unique_targets_compared"], stats["unique_targets_affected"]), (1, 1))
        self.assertAlmostEqual(stats["mean_absolute_pp_changed"], 0.2)
        self.assertEqual(stats["absolute_distribution"], {"0.0": 1, "0.1": 1, "0.3": 1})


class ReadOnlyAuditTests(unittest.TestCase):
    def test_gap_unmatched_year_labels_and_database_preservation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            db = root / "archive.sqlite3"
            layouts = {}
            with sqlite3.connect(db) as connection:
                connection.execute("""CREATE TABLE current_observations (
                    report_id TEXT, year INTEGER, week INTEGER, period_start TEXT,
                    region TEXT, ili_percent REAL, report_number INTEGER, sha256 TEXT,
                    detail_url TEXT, pdf_url TEXT, pages_json TEXT, evidence_json TEXT)""")
                for year, week, start in [(2024, 52, "2024-12-23"), (2025, 1, "2024-12-30"), (2025, 3, "2025-01-13")]:
                    name = f"{year % 100:02d}-{week:02d}.pdf"
                    content = name.encode()
                    (root / name).write_bytes(content)
                    text = ""
                    for region in ["south", "north"]:
                        p = paragraph(region, year, week).replace("2023年、2024年和2025年", "2021~2023年")
                        text += p
                        connection.execute("INSERT INTO current_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                           (f"{year}-W{week:02d}", year, week, start, region, 4.5, 1,
                                            hashlib.sha256(content).hexdigest(), None, None, "[1,2]",
                                            json.dumps([{"page": 1, "text": p}])))
                    layouts[name] = [text, text]
            before = db.read_bytes()
            with patch("flu_data.audit_revisions.read_layout", side_effect=lambda path, _: layouts[path.name]):
                result = audit(db, root, root / "cache")
            self.assertEqual(result["coverage"]["missing_internal"], ["2025-W02"])
            self.assertEqual(len(result["coverage"]["missing_in_first_displayed_season"]), 38)
            self.assertEqual((result["summary"]["all"]["references"], result["summary"]["all"]["paired"]), (24, 2))
            self.assertTrue(any(i.get("year_label_set") == [2021, 2022, 2023] for i in result["issues"]))
            self.assertEqual(db.read_bytes(), before)
            (root / "24-52.pdf").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "no longer matches"):
                audit(db, root)


if __name__ == "__main__":
    unittest.main()
