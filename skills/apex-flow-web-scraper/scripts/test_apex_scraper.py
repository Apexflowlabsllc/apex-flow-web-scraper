import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apex_scraper


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _size=-1):
        return self.payload


class HostedCliTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"APEX_SCRAPER_API_KEY": "secret-test-value"}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    @patch("apex_scraper.urlopen", return_value=Response({"result": {"markdown": "hello"}}))
    def test_scrape_uses_auth_header_and_requested_fields(self, call):
        args = apex_scraper.make_parser().parse_args(["scrape", "https://example.com", "--render", "never", "--max-tokens", "900"])
        result = apex_scraper.execute(args)
        request = call.call_args.args[0]
        self.assertEqual(result["result"]["markdown"], "hello")
        self.assertEqual(request.full_url, "https://webscraper.apexflowlabs.com/v1/scrape")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-test-value")
        self.assertEqual(json.loads(request.data), {"url": "https://example.com", "render": "never", "max_tokens": 900})

    @patch("apex_scraper.urlopen", return_value=Response({"results": []}))
    def test_saved_search_uses_account_scoped_route(self, call):
        args = apex_scraper.make_parser().parse_args(["saved-search", "project docs"])
        apex_scraper.execute(args)
        request = call.call_args.args[0]
        self.assertEqual(request.full_url, "https://webscraper.apexflowlabs.com/v1/search")
        self.assertEqual(json.loads(request.data), {"query": "project docs", "limit": 10})

    @patch("apex_scraper.urlopen", return_value=Response({"results": []}))
    def test_changes_requests_changes_only_output(self, call):
        args = apex_scraper.make_parser().parse_args(["changes", "--url", "https://example.com"])
        apex_scraper.execute(args)
        request = call.call_args.args[0]
        self.assertEqual(request.full_url, "https://webscraper.apexflowlabs.com/v1/batch")
        self.assertEqual(json.loads(request.data), {"urls": ["https://example.com"], "render": "never", "outputs": ["diff"]})

    @patch("apex_scraper.urlopen")
    def test_missing_key_fails_before_network_request(self, call):
        os.environ.pop("APEX_SCRAPER_API_KEY")
        with self.assertRaisesRegex(apex_scraper.ApiError, "APEX_SCRAPER_API_KEY"):
            apex_scraper.request_json("GET", "/v1/usage")
        call.assert_not_called()

    @patch("apex_scraper.urlopen")
    def test_refuses_to_send_api_key_to_non_loopback_http(self, call):
        os.environ["APEX_SCRAPER_API_BASE"] = "http://attacker.example"
        with self.assertRaisesRegex(apex_scraper.ApiError, "must use HTTPS"):
            apex_scraper.request_json("GET", "/v1/usage")
        call.assert_not_called()

    @patch("apex_scraper.urlopen")
    def test_batch_rejects_more_than_ten_before_network_request(self, call):
        values = ["batch", *sum((["--url", f"https://example.com/{i}"] for i in range(11)), [])]
        args = apex_scraper.make_parser().parse_args(values)
        with self.assertRaisesRegex(apex_scraper.ApiError, "at most ten"):
            apex_scraper.execute(args)
        call.assert_not_called()

    @patch("apex_scraper.urlopen", side_effect=HTTPError("https://example.com", 402, "payment", {}, None))
    def test_http_errors_include_status_without_echoing_api_key(self, _call):
        with self.assertRaises(apex_scraper.ApiError) as error:
            apex_scraper.request_json("GET", "/v1/usage")
        self.assertIn('"http_status": 402', str(error.exception))
        self.assertNotIn("secret-test-value", str(error.exception))


if __name__ == "__main__":
    unittest.main()
