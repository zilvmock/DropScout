import pytest

from functionality.twitch_drops import fetcher as fetcher_mod
from functionality.twitch_drops.models import CampaignRecord


pytestmark = pytest.mark.asyncio


async def _fake_fetch_active_campaigns_single():
	# One active campaign only; Twitch doesn't provide true future campaigns in practice
	return {
		"campaigns": [
			{
				"id": "c_active",
				"name": "Active Camp",
				"status": "ACTIVE",
				"game": {"displayName": "Alpha", "slug": "alpha", "boxArtURL": "https://box"},
				"startAt": "2000-01-01T00:00:00Z",
				"endAt": "2099-02-01T00:00:00Z",
				"timeBasedDrops": [],
			},
		]
	}


async def test_fetcher_prefers_display_name_and_slug(monkeypatch):
	monkeypatch.setattr(fetcher_mod, "fetch_active_campaigns", _fake_fetch_active_campaigns_single)
	f = fetcher_mod.DropsFetcher()
	recs = await f.fetch_condensed()
	ids = [r.id for r in recs]
	assert ids == ["c_active"]
	alpha = next(r for r in recs if r.id == "c_active")
	assert alpha.game_name == "Alpha"
	assert alpha.game_slug == "alpha"


async def _fake_fetch_active_campaigns_mixed():
	return {
		"campaigns": [
			{
				"id": "c_active",
				"name": "Primary",
				"status": "ACTIVE",
				"game": {"displayName": "Game One", "slug": "game-one"},
				"timeBasedDrops": [
					{
						"benefitEdges": [
							{"benefit": {"id": "b1", "name": "Reward A", "imageAssetURL": "https://img/a.png"}},
							{"benefit": {"id": "b1", "name": "Reward A (dup)", "imageAssetURL": "https://img/a2.png"}},
						]
					},
					{
						"benefitEdges": [
							{"benefit": {"id": "b2", "name": "Reward B", "imageAssetURL": "https://img/b.png"}},
						]
					},
				],
			},
			{
				"id": "c_future",
				"name": "Future Campaign",
				"status": "UPCOMING",
				"game": {"displayName": "Future Game"},
			},
			{
				"id": "c_invalid",
				"name": "Invalid",
				"status": "ACTIVE",
				"game": None,
				"timeBasedDrops": [
					{},
					{
						"benefitEdges": [
							{"benefit": None},
							{"benefit": {}},
						]
					},
				],
			},
			"not-a-dict",
			{
				"id": "c_missing_status",
			},
		]
	}


class DummyCatalog:
	def __init__(self, should_raise: bool = False) -> None:
		self.records: list[CampaignRecord] | None = None
		self.should_raise = should_raise

	def merge_from_campaign_records(self, recs: list[CampaignRecord]) -> bool:
		self.records = recs
		if self.should_raise:
			raise RuntimeError("boom")
		return True


async def test_fetcher_filters_invalid_and_deduplicates(monkeypatch):
	monkeypatch.setattr(fetcher_mod, "fetch_active_campaigns", _fake_fetch_active_campaigns_mixed)
	catalog = DummyCatalog()
	monkeypatch.setattr(fetcher_mod, "get_game_catalog", lambda: catalog)

	f = fetcher_mod.DropsFetcher()
	recs = await f.fetch_condensed()

	assert len(recs) == 2  # c_active + c_invalid (even with minimal data)
	active = next(r for r in recs if r.id == "c_active")
	assert active.game_name == "Game One"
	assert active.game_slug == "game-one"
	# Benefit IDs should be unique even across multiple drops
	assert [b.id for b in active.benefits] == ["b1", "b2"]
	assert catalog.records is recs


async def test_fetcher_swallows_catalog_errors(monkeypatch):
	async def fake_fetch():
		return {"campaigns": [{"id": "c1", "name": "Ok", "status": "ACTIVE", "timeBasedDrops": []}]}

	monkeypatch.setattr(fetcher_mod, "fetch_active_campaigns", fake_fetch)
	catalog = DummyCatalog(should_raise=True)
	monkeypatch.setattr(fetcher_mod, "get_game_catalog", lambda: catalog)

	f = fetcher_mod.DropsFetcher()
	recs = await f.fetch_condensed()
	assert len(recs) == 1
	assert recs[0].id == "c1"
