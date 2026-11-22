import io
import json
from unittest.mock import patch

from rainbo_scrape.hosted_client import Apex


def test_python_client_has_scheduled_monitor_operations():
    replies = [
        {"monitor": {"id": "mon_1"}, "signing_secret": "whsec_fixture"},
        {"monitors": [{"id": "mon_1"}]},
        {"monitor": {"id": "mon_1", "state": "paused"}},
        {"monitor": {"id": "mon_1", "state": "active"}},
        {"events": []},
        {"deleted": "mon_1"},
    ]
    calls = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    def open_request(request, timeout):
        calls.append((request.get_method(), request.full_url, json.loads(request.data) if request.data else None))
        return Response(json.dumps(replies.pop(0)).encode())

    client = Apex(api_key="rs_live_fixture", base_url="https://api.example/v1", retries=0)
    with patch("rainbo_scrape.hosted_client.urllib.request.urlopen", open_request):
        assert client.create_monitor("https://example.com", "https://hooks.example.com", 3600, True)["signing_secret"] == "whsec_fixture"
        client.monitors()
        client.pause_monitor("mon_1")
        client.resume_monitor("mon_1")
        client.monitor_events("mon_1", 5)
        client.delete_monitor("mon_1")
    assert [(method, url.removeprefix("https://api.example/v1"), body) for method, url, body in calls] == [
        ("POST", "/monitors/create", {"url": "https://example.com", "webhook_url": "https://hooks.example.com", "interval_seconds": 3600, "confirm_recurring": True}),
        ("GET", "/monitors/list", None),
        ("POST", "/monitors/pause", {"id": "mon_1"}),
        ("POST", "/monitors/resume", {"id": "mon_1"}),
        ("POST", "/monitors/events", {"id": "mon_1", "limit": 5}),
        ("POST", "/monitors/delete", {"id": "mon_1"}),
    ]
