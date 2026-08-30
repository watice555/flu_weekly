import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import main


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.save = patch.object(main, "SAVE_DIR", self.root)
        self.save.start()
        self.addCleanup(self.save.stop)
        self.state = patch.object(main, "STATE_FILE", self.root / "state.json")
        self.state.start()
        self.addCleanup(self.state.stop)

    def response(self, chunks):
        response = Mock()
        response.iter_content.return_value = chunks
        manager = Mock()
        manager.__enter__ = Mock(return_value=response)
        manager.__exit__ = Mock(return_value=False)
        return manager

    def test_interrupted_download_preserves_existing_pdf(self):
        target = self.root / "26-34.pdf"
        target.write_bytes(b"%PDF-old")
        def chunks():
            yield b"%PDF-new"
            raise requests.ConnectionError("interrupted")
        with patch.object(main.session, "get", return_value=self.response(chunks())):
            with self.assertRaises(requests.ConnectionError):
                main.download_pdf("https://example.test/fixture.pdf", target.name)
        self.assertEqual(target.read_bytes(), b"%PDF-old")
        self.assertEqual(list(self.root.iterdir()), [target])

    def test_html_response_is_not_saved_as_pdf(self):
        with patch.object(main.session, "get", return_value=self.response([b"<html>Error</html>"])):
            with self.assertRaisesRegex(ValueError, "不是 PDF"):
                main.download_pdf("https://example.test/fixture.pdf", "26-34.pdf")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_revised_pdf_preserves_original_bytes(self):
        target = self.root / "26-34.pdf"
        target.write_bytes(b"%PDF-old")
        with patch.object(main.session, "get", return_value=self.response([b"%PDF-new"])):
            main.download_pdf("https://example.test/fixture.pdf", target.name)
        self.assertEqual(target.read_bytes(), b"%PDF-new")
        originals = list((self.root / "originals").glob("*.pdf"))
        self.assertEqual(len(originals), 1)
        self.assertEqual(originals[0].read_bytes(), b"%PDF-old")

    def test_unchanged_report_still_refreshes_database_without_copy(self):
        url = "https://ivdc.chinacdc.cn/cnic/fixture.pdf"
        (self.root / "26-34.pdf").write_bytes(b"%PDF-existing")
        main.save_last_url(url)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(main, "find_latest_detail", return_value=("https://ivdc.chinacdc.cn/cnic/fixture.htm",2026,34)))
            stack.enter_context(patch.object(main, "find_pdf_url", return_value=url))
            stack.enter_context(patch.object(main, "record_source"))
            build = stack.enter_context(patch.object(main, "rebuild", return_value={"reports":138,"observations":276}))
            download = stack.enter_context(patch.object(main, "download_pdf"))
            copy = stack.enter_context(patch.object(main, "copy_to_secondary"))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            main.main()
        build.assert_called_once()
        download.assert_not_called()
        copy.assert_not_called()

    def test_build_failure_does_not_advance_download_state(self):
        main.save_last_url("old-url")
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(main, "find_latest_detail", return_value=("https://ivdc.chinacdc.cn/cnic/fixture.htm",2026,34)))
            stack.enter_context(patch.object(main, "find_pdf_url", return_value="https://ivdc.chinacdc.cn/cnic/new.pdf"))
            stack.enter_context(patch.object(main, "download_pdf", return_value=self.root/"26-34.pdf"))
            copy = stack.enter_context(patch.object(main, "copy_to_secondary", return_value=self.root/"secondary.pdf"))
            stack.enter_context(patch.object(main, "record_source"))
            stack.enter_context(patch.object(main, "rebuild", side_effect=ValueError("invalid data")))
            with self.assertRaisesRegex(ValueError, "invalid data"):
                main.main()
        self.assertEqual(main.load_last_url(), "old-url")
        copy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
