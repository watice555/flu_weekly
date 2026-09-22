import json
import tempfile
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from flu_data import publish_site as publisher
from flu_data.stage_site import PUBLIC_FILES


class PublishTests(unittest.TestCase):
    def test_timestamp_ignored_but_values_and_assets_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in PUBLIC_FILES:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('asset')
            data = root / 'data/ili.json'
            data.write_text(json.dumps({'generated_at': 'old', 'reports': [{'north': 3.1}]}))
            original = publisher.fingerprint(root)
            data.write_text(json.dumps({'reports': [{'north': 3.1}], 'generated_at': 'new'}))
            self.assertEqual(original, publisher.fingerprint(root))
            data.write_text(json.dumps({'reports': [{'north': 3.2}]}))
            self.assertNotEqual(original, publisher.fingerprint(root))
            changed = publisher.fingerprint(root)
            (root / 'app.mjs').write_text('new asset')
            self.assertNotEqual(changed, publisher.fingerprint(root))

    def finish(self, conclusion='success', verification_error=None):
        state = {'last_fingerprint': 'old', 'pending': {
            'tag': 'test', 'sha256': 'zip', 'fingerprint': 'new', 'hashes': {}}}
        run = {'databaseId': 123, 'status': 'completed', 'conclusion': conclusion}
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(publisher, 'STATE', Path(tmp) / 'state.json'), \
             patch.object(publisher, 'find_run', return_value=run), \
             patch.object(publisher, 'wait_run', return_value=run), \
             patch.object(publisher, 'gh', return_value='[{"tagName":"test"}]') as gh, \
             patch.object(publisher, 'verify_online', side_effect=verification_error) as verify:
            if conclusion != 'success' or verification_error:
                with self.assertRaises(RuntimeError):
                    publisher.finish_pending(state)
                self.assertEqual(state['last_fingerprint'], 'old')
            else:
                publisher.finish_pending(state)
                self.assertEqual(state['last_fingerprint'], 'new')
            if verification_error:
                self.assertIn('pending', state)
                gh.assert_not_called()
            else:
                self.assertNotIn('pending', state)
                gh.assert_any_call('release', 'delete', 'test', '--yes', '--cleanup-tag')
            if conclusion != 'success':
                verify.assert_not_called()

    def test_success_requires_online_verification(self):
        self.finish()

    def test_failed_run_does_not_advance_success(self):
        self.finish('failure')

    def test_online_mismatch_preserves_pending_for_retry(self):
        self.finish(verification_error=RuntimeError('mismatch'))

    def test_timeout_preserves_pending(self):
        state = {'pending': {'tag': 'test'}}
        with patch.object(publisher, 'find_run', return_value={'status': 'in_progress'}), \
             patch.object(publisher, 'wait_run', side_effect=RuntimeError('timeout')):
            with self.assertRaises(RuntimeError):
                publisher.finish_pending(state)
        self.assertIn('pending', state)

    def test_download_failure_stops_publish_in_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'scripts').mkdir()
            (root / '.venv/bin').mkdir(parents=True)
            wrapper = root / 'scripts/update-and-publish.sh'
            shutil.copyfile(publisher.ROOT / 'scripts/update-and-publish.sh', wrapper)
            python = root / '.venv/bin/python'
            python.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> calls\nif [[ "$1" == main.py ]]; then exit 7; fi\n')
            python.chmod(0o755)
            result = subprocess.run(['bash', str(wrapper)], capture_output=True)
            self.assertEqual(result.returncode, 7)
            self.assertEqual((root / 'calls').read_text().splitlines(), ['main.py'])
            python.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> calls\n')
            (root / 'calls').unlink()
            result = subprocess.run(['bash', str(wrapper)], capture_output=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual((root / 'calls').read_text().splitlines(),
                             ['main.py', '-m flu_data.publish_site'])

    def test_no_change_and_dry_run_never_use_github(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / 'data/state.json'
            state.parent.mkdir()
            state.write_text(json.dumps({'last_fingerprint': 'same'}))
            with patch.object(publisher, 'ROOT', root), patch.object(publisher, 'STATE', state), \
                 patch.object(publisher, 'stage_site'), \
                 patch.object(publisher, 'fingerprint', return_value='same'), \
                 patch.object(publisher, 'gh') as gh:
                publisher.publish()
                publisher.publish(dry_run=True)
                gh.assert_not_called()


if __name__ == '__main__':
    unittest.main()
