import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import client


class TestGetApiAuth(unittest.TestCase):
    def test_returns_basic_auth_tuple(self):
        client.API_TOKEN = 'test_token_123'
        self.assertEqual(client.get_api_auth(), ('apikey', 'test_token_123'))

    def test_raises_without_token(self):
        client.API_TOKEN = None
        with self.assertRaises(ValueError):
            client.get_api_auth()


class TestGetApiHeaders(unittest.TestCase):
    def test_no_auth_header(self):
        # Auth is sent via requests' `auth=` kwarg, not via headers.
        headers = client.get_api_headers()
        self.assertNotIn('Authorization', headers)
        self.assertEqual(headers['Accept'], 'application/json')


class TestGetBugTypeId(unittest.TestCase):
    def setUp(self):
        client.API_TOKEN = 'tok'

    def _mock_types_response(self, types):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {'_embedded': {'elements': types}}
        return resp

    def test_resolves_name_to_id(self):
        with patch.object(client.requests, 'get',
                          return_value=self._mock_types_response(
                              [{'id': 1, 'name': 'Task'}, {'id': 7, 'name': 'Bug'}])):
            self.assertEqual(client.get_bug_type_id(), '7')

    def test_raises_when_name_missing(self):
        with patch.object(client.requests, 'get',
                          return_value=self._mock_types_response(
                              [{'id': 1, 'name': 'Task'}])):
            with self.assertRaises(ValueError):
                client.get_bug_type_id()


class TestFetchWorkPackages(unittest.TestCase):
    def setUp(self):
        client.API_TOKEN = 'tok'

    def test_sends_basic_auth_and_filter_by_type_id(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {'total': 0, '_embedded': {'elements': []}}

        with patch.object(client.requests, 'get', return_value=resp) as mock_get:
            client.fetch_work_packages(page=3, per_page=50, type_id='7')

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
        with patch.object(client.requests, 'get', return_value=resp):
            with self.assertRaises(_requests.exceptions.HTTPError):
                client.fetch_work_packages(type_id='7')


class TestExtractBugs(unittest.TestCase):
    def test_yields_full_work_packages_across_pages(self):
        wp1 = {'id': 1, 'subject': 'A', '_links': {'status': {'title': 'New'}}}
        wp2 = {'id': 2, 'subject': 'B', '_links': {'status': {'title': 'Done'}}}
        wp3 = {'id': 3, 'subject': 'C', '_links': {'status': {'title': 'New'}}}
        pages = [
            {'total': 3, '_embedded': {'elements': [wp1, wp2]}},
            {'total': 3, '_embedded': {'elements': [wp3]}},
        ]
        with patch.object(client, 'get_bug_type_id', return_value='7'), \
             patch.object(client, 'fetch_work_packages', side_effect=pages) as mock_fetch:
            bugs = client.extract_bugs()

        # Returns the raw work-package dicts unchanged — DB layer needs _links etc.
        self.assertEqual(bugs, [wp1, wp2, wp3])
        self.assertEqual(mock_fetch.call_count, 2)
        self.assertEqual(mock_fetch.call_args_list[0].kwargs['page'], 1)
        self.assertEqual(mock_fetch.call_args_list[1].kwargs['page'], 2)

    def test_empty_result(self):
        empty = {'total': 0, '_embedded': {'elements': []}}
        with patch.object(client, 'get_bug_type_id', return_value='7'), \
             patch.object(client, 'fetch_work_packages', return_value=empty):
            self.assertEqual(client.extract_bugs(), [])


if __name__ == '__main__':
    unittest.main()
