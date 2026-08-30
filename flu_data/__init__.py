"""Extract published weekly ILI observations without estimating chart pixels."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "flu_reports"
DATABASE = ROOT / "data" / "flu.sqlite3"
CATALOG = ROOT / "data" / "sources.json"
EXPORT = ROOT / "site" / "data" / "ili.json"
