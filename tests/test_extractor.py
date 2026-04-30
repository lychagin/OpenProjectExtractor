import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import client as extractor


class TestGetApiAuth(unittest.TestCase):
    def test_returns_basic_auth_tuple(self):
        extractor.API_TOKEN = 'test_token_123'
        self.assertEqual(extractor.get_api_auth(), ('apikey', 'test_token_123'))

    def test_raises_without_token(self):
        extractor.API_TOKEN = None
        with self.assertRaises(ValueError):
            extractor.get_api_auth()


class TestGetApiHeaders(unittest.TestCase):
    def test_no_auth_header(self):
        # Auth is sent via requests' `auth=` kwarg, not via headers.
        headers = extractor.get_api_headers()
        self.assertNotIn('Authorization', headers)
        self.assertEqual(headers['Accept'], 'application/json')


class TestGetBugTypeId(unittest.TestCase):
    def setUp(self):
        extractor.API_TOKEN = 'tok'

    def _mock_types_response(self, types):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {'_embedded': {'elements': types}}
        return resp

    def test_resolves_name_to_id(self):
        with patch.object(extractor.requests, 'get',
                          return_value=self._mock_types_response(
                              [{'id': 1, 'name': 'Task'}, {'id': 7, 'name': 'Bug'}])):
            self.assertEqual(extractor.get_bug_type_id(), '7')

    def test_raises_when_name_missing(self):
        with patch.object(extractor.requests, 'get',
                          return_value=self._mock_types_response(
                              [{'id': 1, 'name': 'Task'}])):
            with self.assertRaises(ValueError):
                extractor.get_bug_type_id()


class TestFetchWorkPackages(unittest.TestCase):
    def setUp(self):
        extractor.API_TOKEN = 'tok'

    def test_sends_basic_auth_and_filter_by_type_id(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {'total': 0, '_embedded': {'elements': []}}

        with patch.object(extractor.requests, 'get', return_value=resp) as mock_get:
            extractor.fetch_work_packages(page=3, per_page=50, type_id='7')

        kwargs = mock_get.call_args.kwargs
        self.assertEqual(kwargs['auth'], ('apikey', 'tok'))
        self.assertEqual(kwargs['params']['offset'], 3)
        self.assertEqual(kwargs['params']['pageSize'], 50)
        self.assertEqual(
            json.loads(kwargs['params']['filters']),
            [{'type': {'operator': '=', 'values': ['7']}}],
        )

    def test_raises_on_http_error(self):
        resp = MagicMock()
        resp.status_code = 401
        import requests as _requests
        resp.raise_for_status.side_effect = _requests.exceptions.HTTPError('401')
        with patch.object(extractor.requests, 'get', return_value=resp):
            with self.assertRaises(_requests.exceptions.HTTPError):
                extractor.fetch_work_packages(type_id='7')


class TestExtractBugs(unittest.TestCase):
    def test_paginates_until_total_reached(self):
        pages = [
            {'total': 3, '_embedded': {'elements': [
                {'id': 1, 'subject': 'A'}, {'id': 2, 'subject': 'B'}]}},
            {'total': 3, '_embedded': {'elements': [
                {'id': 3, 'subject': 'C'}]}},
        ]
        with patch.object(extractor, 'get_bug_type_id', return_value='7'), \
             patch.object(extractor, 'fetch_work_packages', side_effect=pages) as mock_fetch:
            bugs = extractor.extract_bugs()

        self.assertEqual([b['id'] for b in bugs], [1, 2, 3])
        self.assertEqual([b['title'] for b in bugs], ['A', 'B', 'C'])
        self.assertEqual(mock_fetch.call_count, 2)
        # Pages requested via `page=` kwarg, starting from 1.
        self.assertEqual(mock_fetch.call_args_list[0].kwargs['page'], 1)
        self.assertEqual(mock_fetch.call_args_list[1].kwargs['page'], 2)

    def test_empty_result(self):
        empty = {'total': 0, '_embedded': {'elements': []}}
        with patch.object(extractor, 'get_bug_type_id', return_value='7'), \
             patch.object(extractor, 'fetch_work_packages', return_value=empty):
            self.assertEqual(extractor.extract_bugs(), [])


class TestSaveToCsv(unittest.TestCase):
    def test_writes_header_and_rows_with_semicolon(self):
        bugs = [{'id': 1, 'title': 'Bug 1'}, {'id': 2, 'title': 'Bug; with; semis'}]
        with tempfile.TemporaryDirectory() as tmp:
            path = extractor.save_to_csv(bugs, output_dir=Path(tmp))
            self.assertTrue(path.exists())
            with open(path, encoding='utf-8') as f:
                rows = list(csv.reader(f, delimiter=';'))
            self.assertEqual(rows[0], ['id', 'title'])
            self.assertEqual(rows[1], ['1', 'Bug 1'])
            self.assertEqual(rows[2], ['2', 'Bug; with; semis'])

    def test_filename_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = extractor.save_to_csv([{'id': 1, 'title': 'x'}], output_dir=Path(tmp))
            self.assertTrue(path.name.startswith('res-'))
            self.assertTrue(path.name.endswith('.csv'))


if __name__ == '__main__':
    unittest.main()
