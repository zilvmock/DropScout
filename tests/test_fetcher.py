import pytest
import asyncio

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
