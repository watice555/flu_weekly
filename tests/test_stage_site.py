import json
import tempfile
import unittest
from pathlib import Path

from flu_data.stage_site import PUBLIC_FILES, stage_site


class StageSiteTests(unittest.TestCase):
    def test_only_public_files_are_copied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "source", root / "public"
            for name in PUBLIC_FILES:
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"schema_version":1,"reports":[{"id":"fixture"}]}) if name.endswith("json") else "fixture")
            for name in ["secret.pdf", ".env", "data/database.sqlite3", "private.txt"]:
                (source / name).write_text("must never be published")
            stage_site(output, source)
            self.assertEqual({str(path.relative_to(output)) for path in output.rglob("*") if path.is_file()}, set(PUBLIC_FILES))
            with self.assertRaisesRegex(ValueError, "must be empty"):
                stage_site(output, source)


if __name__ == "__main__":
    unittest.main()
