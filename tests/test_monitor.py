import pytest

from functionality.twitch_drops.models import BenefitRecord, CampaignRecord
from functionality.twitch_drops.monitor import DropsMonitor


class StubCatalog:
	def __init__(self):
		self.merged = []

	def merge_state_snapshot(self, data):
		self.merged.append(data)


class StubStore:
	def __init__(self):
		self._data = {}
		self.saves: list[list[str]] = []

	def load(self):
		return dict(self._data)

	def save(self, campaigns):
		self.saves.append([c.id for c in campaigns])
		self._data = {c.id: {"status": c.status} for c in campaigns}


class StubFetcher:
	def __init__(self, campaigns):
		self.campaigns = campaigns
		self.calls = 0

	async def fetch_condensed(self):
		self.calls += 1
		return self.campaigns


class StubNotifier:
	def __init__(self):
		self.calls = 0
		self.payloads = []

	async def notify(self, diff):
		self.calls += 1
		self.payloads.append(diff)


class StubApp:
	def __init__(self):
		self.rest = object()


def make_campaign(cid: str) -> CampaignRecord:
	return CampaignRecord(
		id=cid,
		name="Camp",
		status="ACTIVE",
		game_name="Game",
		game_slug="game",
		game_box_art=None,
		starts_at=None,
		ends_at=None,
		benefits=[BenefitRecord(id="b1", name="Reward", image_url=None)],
	)


@pytest.mark.asyncio
async def test_monitor_skips_notify_on_first_run(monkeypatch):
	monkeypatch.setattr("functionality.twitch_drops.monitor.get_game_catalog", lambda: StubCatalog())

	app = StubApp()
	monitor = DropsMonitor(app, interval_minutes=1, notify_on_boot=False)
	store = StubStore()
	fetcher = StubFetcher([make_campaign("c1")])
	notifier = StubNotifier()
	monitor.store = store
	monitor.fetcher = fetcher
	monitor.notifier = notifier

	async def stop_sleep(*args, **kwargs):
		raise StopAsyncIteration

	monkeypatch.setattr("functionality.twitch_drops.monitor.asyncio.sleep", stop_sleep)

	with pytest.raises(StopAsyncIteration):
		await monitor._run_loop()

	assert notifier.calls == 0
	assert fetcher.calls == 1
	assert store.saves == [["c1"]]


@pytest.mark.asyncio
async def test_monitor_notifies_when_enabled(monkeypatch):
	monkeypatch.setattr("functionality.twitch_drops.monitor.get_game_catalog", lambda: StubCatalog())

	app = StubApp()
	monitor = DropsMonitor(app, interval_minutes=1, notify_on_boot=True)
	store = StubStore()
	fetcher = StubFetcher([make_campaign("c2")])
	notifier = StubNotifier()
	monitor.store = store
	monitor.fetcher = fetcher
	monitor.notifier = notifier

	async def stop_sleep(*args, **kwargs):
		raise StopAsyncIteration

	monkeypatch.setattr("functionality.twitch_drops.monitor.asyncio.sleep", stop_sleep)

	with pytest.raises(StopAsyncIteration):
		await monitor._run_loop()

	assert notifier.calls == 1
	assert notifier.payloads[0].activated[0].id == "c2"
