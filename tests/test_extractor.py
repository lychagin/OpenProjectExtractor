import os
import sys
import csv
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock
import unittest

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))


class TestGetApiHeaders(unittest.TestCase):
    def setUp(self):
        # Import after setting up module path
        import extractor
        self.extractor = extractor

    def test_returns_valid_headers(self):
        self.extractor.API_TOKEN = 'test_token_123'
        headers = self.extractor.get_api_headers()
        self.assertEqual(headers['Authorization'], 'Bearer test_token_123')
        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertEqual(headers['Accept'], 'application/json')

    def test_raises_without_token(self):
        self.extractor.API_TOKEN = None
        with self.assertRaises(ValueError):
            self.extractor.get_api_headers()


class TestFetchWorkPackages(unittest.TestCase):
    def setUp(self):
        import extractor
        self.extractor = extractor

    def test_fetches_bugs_successfully(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            '_meta': {'count': 2},
            '_embedded': {
                'elements': [
                    {'id': 1, 'subject': 'Bug 1'},
                    {'id': 2, 'subject': 'Bug 2'}
                ]
            },
            '_links': {}
        }

        with patch('extractor.requests.get') as mock_get:
            mock_get.return_value = mock_response
            data = self.extractor.fetch_work_packages()
            self.assertEqual(len(data['_embedded']['elements']), 2)
            mock_get.assert_called_once()

    def test_handles_http_401(self):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.raise_for_status.side_effect = Exception('401')

        with patch('extractor.requests.get') as mock_get:
            mock_get.return_value = mock_response
            with self.assertRaises(Exception):
                self.extractor.fetch_work_packages()

    def test_handles_http_404(self):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.raise_for_status.side_effect = Exception('404')

        with patch('extractor.requests.get') as mock_get:
            mock_get.return_value = mock_response
            with self.assertRaises(Exception):
                self.extractor.fetch_work_packages()


class TestExtractBugs(unittest.TestCase):
    def setUp(self):
        import extractor
        self.extractor = extractor

    def test_extract_bugs_from_single_page(self):
        mock_data = {
            '_meta': {'count': 2},
            '_embedded': {
                'elements': [
                    {'id': 1, 'subject': 'Bug 1'},
                    {'id': 2, 'subject': 'Bug 2'}
                ]
            },
            '_links': {}
        }

        with patch.object(self.extractor, 'fetch_work_packages', return_value=mock_data):
            bugs = self.extractor.extract_bugs()
            self.assertEqual(len(bugs), 2)
            self.assertEqual(bugs[0]['id'], 1)
            self.assertEqual(bugs[0]['title'], 'Bug 1')

    def test_extract_bugs_empty(self):
        mock_data = {
            '_meta': {'count': 0},
            '_embedded': {'elements': []},
            '_links': {}
        }

        with patch.object(self.extractor, 'fetch_work_packages', return_value=mock_data):
            bugs = self.extractor.extract_bugs()
            self.assertEqual(len(bugs), 0)


class TestSaveToCsv(unittest.TestCase):
    def setUp(self):
        import extractor
        self.extractor = extractor

    def test_saves_csv_correctly(self):
        bugs = [
            {'id': 1, 'title': 'Bug 1'},
            {'id': 2, 'title': 'Bug 2'}
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = self.extractor.save_to_csv(bugs, output_dir=Path(tmpdir))
            self.assertTrue(filepath.exists())
            self.assertTrue(str(tmpdir) in str(filepath))

            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
                self.assertIn('1;Bug 1', content)
                self.assertIn('2;Bug 2', content)
                self.assertIn('id;title', content)

    def test_csv_uses_semicolon_separator(self):
        bugs = [{'id': 1, 'title': 'Test Bug'}]

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = self.extractor.save_to_csv(bugs, output_dir=Path(tmpdir))
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.reader(f, delimiter=';')
                rows = list(reader)
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0], ['id', 'title'])
                self.assertEqual(rows[1], ['1', 'Test Bug'])

    def test_filename_format(self):
        bugs = [{'id': 1, 'title': 'Bug 1'}]

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = self.extractor.save_to_csv(bugs, output_dir=Path(tmpdir))
            filename = filepath.name
            self.assertTrue(filename.startswith('res-'))
            self.assertTrue(filename.endswith('.csv'))


if __name__ == '__main__':
    unittest.main()
