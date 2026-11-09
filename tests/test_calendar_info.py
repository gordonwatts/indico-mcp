import pytest

from indico_mcp import server
from indico_mcp.models import normalize_event


def test_timestamp_zone_is_not_confused_with_event_zone():
    event = normalize_event({
        "timezone": "Europe/Zurich",
        "startDate": {"date": "2026-11-10", "time": "15:00:00", "tz": "UTC"},
        "endDate": {"date": "2026-11-10", "time": "16:00:00", "tz": "UTC"},
    })
    assert event["timezone"] == "Europe/Zurich"
    assert event["start_datetime"] == {"date_time": "2026-11-10T15:00:00", "timezone": "UTC"}
    assert event["end_datetime"]["timezone"] == "UTC"
    assert "timezone" not in normalize_event({"startDate": "2026-11-10"})


@pytest.mark.asyncio
async def test_calendar_tool_requests_utc_and_keeps_indico_link(monkeypatch):
    class Client:
        async def export(self, resource, **params):
            assert resource == "event/1"
            assert params == {"detail": "events", "tz": "UTC"}
            return {"results": [{"id": "1", "title": "Meeting", "url": "https://indico.example/event/1/",
                    "timezone": "Europe/Zurich",
                    "startDate": {"date": "2026-10-25", "time": "01:30:00", "tz": "UTC"},
                    "endDate": {"date": "2026-10-25", "time": "02:30:00", "tz": "UTC"}}]}

    monkeypatch.setattr(server, "_client", lambda instance: Client())
    result = await server.get_event_calendar_info(1)
    assert result["url"] == "https://indico.example/event/1/"
    assert result["start_datetime"] == {"date_time": "2026-10-25T01:30:00", "timezone": "UTC"}
    assert result["end_datetime"] == {"date_time": "2026-10-25T02:30:00", "timezone": "UTC"}


@pytest.mark.asyncio
async def test_calendar_tool_does_not_invent_missing_zone(monkeypatch):
    class Client:
        async def export(self, resource, **params):
            return {"results": [{"startDate": {"date": "2026-01-01", "time": "10:00:00"}}]}

    monkeypatch.setattr(server, "_client", lambda instance: Client())
    result = await server.get_event_calendar_info(1)
    assert result["start_datetime"]["timezone"] is None
