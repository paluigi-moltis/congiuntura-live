"""Tests for update-status tracking and the WebSocket hub (htmx ws extension)."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from congiuntura_live.update_status import KEY_CALENDAR, KEY_PRESSES, UpdateStatusRepository
from congiuntura_live.ws_hub import UpdateHub, render_indicator_fragment


class TestUpdateStatusRepository:
    def test_keys(self):
        assert KEY_PRESSES == "press_releases"
        assert KEY_CALENDAR == "calendar"

    async def test_mark_upserts_by_key(self):
        repo = UpdateStatusRepository.__new__(UpdateStatusRepository)
        coll = MagicMock()
        coll.update_one = AsyncMock()
        repo._coll = coll

        await repo.mark(KEY_PRESSES, status="ok", details="5 new releases")

        coll.update_one.assert_awaited_once()
        args = coll.update_one.await_args
        assert args.args[0] == {"_id": KEY_PRESSES}
        assert args.args[1]["$set"]["status"] == "ok"
        assert args.args[1]["$set"]["details"] == "5 new releases"
        assert args.kwargs.get("upsert") is True

    async def test_get_all_keys_by_id(self):
        repo = UpdateStatusRepository.__new__(UpdateStatusRepository)
        coll = MagicMock()
        coll.find = MagicMock(return_value=MagicMock(
            to_list=AsyncMock(return_value=[
                {"_id": KEY_PRESSES, "last_run": datetime(2026, 8, 16, 7, 0), "status": "ok"},
                {"_id": KEY_CALENDAR, "last_run": datetime(2026, 8, 16, 7, 0), "status": "ok"},
            ])
        ))
        repo._coll = coll

        result = await repo.get_all()
        assert set(result.keys()) == {KEY_PRESSES, KEY_CALENDAR}


class TestUpdateHub:
    async def test_broadcast_reaches_subscribed_clients_only(self):
        hub = UpdateHub()
        ws_press, ws_cal, ws_other = MagicMock(), MagicMock(), MagicMock()
        for ws in (ws_press, ws_cal, ws_other):
            ws.send_text = AsyncMock()
        hub._clients = {
            ws_press: {"press_releases"},
            ws_cal: {"calendar"},
            ws_other: {"calendar"},
        }

        await hub.broadcast("calendar", "<div id='last-update-calendar'>x</div>")

        ws_press.send_text.assert_not_awaited()
        ws_cal.send_text.assert_awaited_once()
        ws_other.send_text.assert_awaited_once()

    async def test_broadcast_drops_dead_clients(self):
        hub = UpdateHub()
        ws_dead = MagicMock()
        ws_dead.send_text = AsyncMock(side_effect=RuntimeError("closed"))
        ws_ok = MagicMock()
        ws_ok.send_text = AsyncMock()
        hub._clients = {ws_dead: {"calendar"}, ws_ok: {"calendar"}}

        await hub.broadcast("calendar", "<div>x</div>")

        assert hub.client_count == 1
        assert ws_ok in hub._clients


class TestIndicatorFragment:
    def test_fragment_renders_ok_status(self):
        html = render_indicator_fragment(
            "press_releases",
            {"last_run": "2026-08-16 16:44 UTC", "status": "ok", "details": "5 new releases"},
        )
        # id matches the element on the page → htmx ws swaps by id
        assert 'id="last-update-press-releases"' in html
        assert 'data-kind="press_releases"' in html
        assert "2026-08-16 16:44 UTC" in html
        assert "5 new releases" in html
        assert "✓" in html

    def test_fragment_renders_never(self):
        html = render_indicator_fragment("calendar", {"last_run": None, "status": "never", "details": ""})
        assert 'id="last-update-calendar"' in html
        assert "never" in html
        assert "⚠" not in html


class TestSchedulerStatusMarking:
    async def test_feed_poller_marks_status(self):
        """FeedPoller.poll_once marks update_status and fires on_update."""
        from congiuntura_live.scheduler import FeedPoller

        reader = MagicMock()
        reader.fetch_agency = AsyncMock(return_value=[])
        repo = MagicMock()
        repo.insert_many_new = AsyncMock(return_value=(0, 0))
        status = MagicMock()
        status.mark = AsyncMock()
        on_update = MagicMock()

        poller = FeedPoller(reader, repo, status_repo=status, on_update=on_update)
        # Empty feeds config → one poll cycle with zero agencies
        poller._feeds_config_path = "/nonexistent/feeds.toml"
        import congiuntura_live.scheduler as sched
        original = sched.load_feeds_config
        sched.load_feeds_config = lambda p: {}
        try:
            await poller.poll_once()
        finally:
            sched.load_feeds_config = original

        status.mark.assert_awaited_once_with("press_releases", status="ok", details="0 new releases")
        on_update.assert_called_once()

    async def test_calendar_poller_marks_status(self, monkeypatch):
        """CalendarPoller.collect_once marks update_status and fires on_update."""
        from congiuntura_live.calendar import scheduler as cal_sched
        from congiuntura_live.calendar.collectors import NSO_COLLECTORS

        async def fake_collect(self):
            return [{"source": self.source_code(), "title": "t", "release_dt": "x", "source_uid": "u"}]

        monkeypatch.setattr(cal_sched.NSO_COLLECTORS[0], "collect", fake_collect)
        monkeypatch.setattr(cal_sched, "NSO_COLLECTORS", NSO_COLLECTORS[:1])

        ff_mock = MagicMock()
        ff_mock.collect_routine = AsyncMock(return_value=[])
        monkeypatch.setattr(cal_sched, "ForexFactoryCollector", lambda: ff_mock)

        repo = MagicMock()
        repo.upsert_nso = AsyncMock(return_value=1)
        repo.upsert_ff = AsyncMock(return_value=0)
        status = MagicMock()
        status.mark = AsyncMock()
        on_update = MagicMock()

        poller = cal_sched.CalendarPoller(repo, status_repo=status, on_update=on_update)
        counts = await poller.collect_once()

        expected_code = NSO_COLLECTORS[0]().source_code()
        assert counts == {expected_code: 1, "forexfactory": 0}
        status.mark.assert_awaited_once_with("calendar", status="ok", details="1 releases")
        on_update.assert_called_once()
