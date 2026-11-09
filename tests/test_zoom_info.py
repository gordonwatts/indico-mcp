import pytest
import httpx
from icalendar import Calendar, Event

from indico_mcp import server
from indico_mcp.client import IndicoClient, IndicoError
from indico_mcp.config import InstanceConfig
from indico_mcp.models import _zoom_links, calendar_passcodes, public_zoom_meetings


def test_links_and_explicit_passcodes_are_extracted_without_host_spoofing():
    description = '<a href="https://example.zoom.us/j/123?pwd=synthetic&amp;x=1">Join</a> Passcode: example'
    assert _zoom_links(description) == ["https://example.zoom.us/j/123?pwd=synthetic&x=1"]
    assert calendar_passcodes(description) == ["example"]
    assert not _zoom_links("https://zoom.us.evil.example/j/1 https://[bad")
    assert not calendar_passcodes("https://zoom.us/j/1?pwd=synthetic")


def make_calendar(include_event=True):
    calendar = Calendar()
    event = Event()
    event.add("url", "https://indico.example/event/1/" if include_event else "https://indico.example/event/2/")
    event.add("description", "Zoom: https://example.zoom.us/j/123?pwd=synthetic\nPasscode: example")
    calendar.add_component(event)
    return calendar.to_ical()


def test_public_page_retains_scope_and_only_visible_passcodes():
    html = '''<div class="vc-room-list">
      <div class="event-service-association-title">Synthetic event<span class="event-service-association-subtitle">Event</span></div>
      <ind-vc-room-segment><div class="vc-room-name">Main</div><ind-vc-zoom-join-button></ind-vc-zoom-join-button>
        <div class="item"><div class="header">Passcode</div>alpha</div>
        <input name="vc-room-url-1" value="https://example.zoom.us/j/1?pwd=synthetic"></ind-vc-room-segment>
      <div class="event-service-association-title">Synthetic session<span class="event-service-association-subtitle">Session</span></div>
      <ind-vc-room-segment><div class="vc-room-name">Session room</div><ind-vc-zoom-join-button></ind-vc-zoom-join-button>
        <input name="vc-room-url-2" value="https://example.zoom.us/j/2"></ind-vc-room-segment>
      <ind-vc-room-segment><input name="vc-room-url-3" value="https://example.zoom.us/j/3"></ind-vc-room-segment>
    </div>'''
    meetings = public_zoom_meetings(html)
    assert len(meetings) == 2
    assert meetings[0]["scope"] == "event"
    assert meetings[0]["passcode"] == "alpha"
    assert meetings[0]["join_url"].endswith("?pwd=synthetic")
    assert meetings[1]["scope"] == "session"
    assert meetings[1]["linked_title"] == "Synthetic session"
    assert "passcode" not in meetings[1]


@pytest.mark.asyncio
async def test_calendar_tool_matches_only_requested_event_and_requests_utc(monkeypatch):
    class Client:
        async def export(self, resource, **params):
            assert resource == "event/1"
            assert params["tz"] == "UTC"
            return {"results": [{"id": "1", "url": "https://indico.example/event/1/",
                    "categoryId": 10, "timezone": "Europe/Zurich",
                    "startDate": {"date": "2026-10-25", "time": "01:30:00", "tz": "UTC"},
                    "endDate": {"date": "2026-10-25", "time": "02:30:00", "tz": "UTC"}}]}

        async def category_calendar(self, category_id, **params):
            assert category_id == 10
            assert params["limit"] == 1000
            return make_calendar()

        async def public_videoconference_page(self, event_id):
            raise IndicoError("unavailable")

    monkeypatch.setattr(server, "_client", lambda instance: Client())
    result = await server.get_event_zoom_info(1)
    assert result["calendar_export_status"] == "matched"
    assert result["zoom_links"] == ["https://example.zoom.us/j/123?pwd=synthetic"]
    assert result["passcodes"] == ["example"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "denied"])
async def test_unavailable_calendar_does_not_leak_other_events(monkeypatch, failure):
    class Client:
        async def export(self, resource, **params):
            return {"results": [{"url": "https://indico.example/event/1/", "categoryId": 10,
                    "startDate": {"date": "2026-01-01"}, "endDate": {"date": "2026-01-01"}}]}

        async def category_calendar(self, category_id, **params):
            if failure == "denied":
                raise IndicoError("denied", 403)
            return make_calendar(include_event=False)

        async def public_videoconference_page(self, event_id):
            return "<html>login</html>"

    monkeypatch.setattr(server, "_client", lambda instance: Client())
    result = await server.get_event_zoom_info(1)
    assert result["zoom_links"] == []
    assert result["passcodes"] == []
    assert result["calendar_export_status"] == ("unavailable" if failure == "denied" else "not_found_within_scan_limit")


@pytest.mark.asyncio
async def test_calendar_scan_finds_match_after_first_page(monkeypatch):
    class Client:
        async def export(self, resource, **params):
            return {"results": [{"url": "https://indico.example/event/1/", "categoryId": 10,
                    "startDate": {"date": "2026-01-01"}, "endDate": {"date": "2026-01-01"}}]}

        async def category_calendar(self, category_id, **params):
            return make_calendar(include_event=params["offset"] == 1000)

        async def public_videoconference_page(self, event_id):
            return "<html></html>"

    monkeypatch.setattr(server, "_client", lambda instance: Client())
    assert (await server.get_event_zoom_info(1))["calendar_export_status"] == "matched"


@pytest.mark.asyncio
async def test_calendar_export_uses_configured_auth_and_rejects_html():
    def handler(request):
        assert request.headers["authorization"] == "Bearer synthetic-test-token"
        assert request.url.path == "/export/categ/10.ics"
        return httpx.Response(200, headers={"content-type": "text/html"}, text="login")

    client = IndicoClient(InstanceConfig("test", "https://indico.example", "synthetic-test-token"))
    headers = client._http.headers
    await client._http.aclose()
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                    headers=headers)
    try:
        with pytest.raises(IndicoError, match="unavailable or access denied"):
            await client.category_calendar(10)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_public_page_request_never_forwards_configured_token(monkeypatch):
    client = IndicoClient(InstanceConfig("test", "https://indico.example", "synthetic-test-token"))
    await client.aclose()

    class AnonymousClient:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
            assert "authorization" not in {k.lower() for k in kwargs["headers"]}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url):
            assert url == "https://indico.example/event/1/videoconference/"
            return httpx.Response(200, headers={"content-type": "text/html"}, text="<html></html>")

    monkeypatch.setattr("indico_mcp.client.httpx.AsyncClient", AnonymousClient)
    assert await client.public_videoconference_page(1) == "<html></html>"
