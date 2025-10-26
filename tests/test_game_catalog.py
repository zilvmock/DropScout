from functionality.twitch_drops.game_catalog import GameCatalog, GameEntry
from functionality.twitch_drops.models import CampaignRecord, BenefitRecord


def test_merge_games_prefers_higher_weight(tmp_path):
	catalog_path = tmp_path / "catalog.json"
	catalog = GameCatalog(str(catalog_path))

	first = GameEntry(
		key="game a",
		name="Game A",
		weight=100,
		aliases=["game a"],
		sources=["helix"],
	)
	second = GameEntry(
		key="game a",
		name="Game A Deluxe",
		weight=250,
		aliases=["game a"],
		sources=["campaign"],
	)

	assert catalog.merge_games([first]) is True
	assert catalog.merge_games([second]) is True

	entry = catalog.get("game a")
	assert entry is not None
	assert entry.weight == 250
	assert entry.name == "Game A Deluxe"
	assert "campaign" in entry.sources
	assert "helix" in entry.sources


def test_matches_campaign_handles_slug(tmp_path):
	catalog_path = tmp_path / "catalog.json"
	catalog = GameCatalog(str(catalog_path))

	entry = GameEntry(
		key="game b",
		name="Game B",
		weight=200,
		aliases=["game b"],
		sources=["helix"],
	)
	catalog.merge_games([entry])

	campaign = CampaignRecord(
		id="1",
		name="Summer Drops",
		status="ACTIVE",
		game_name="Game B",
		game_slug="game-b",
		game_box_art=None,
		starts_at=None,
		ends_at=None,
		benefits=[BenefitRecord(id="b1", name="Reward", image_url=None)],
	)

	resolved = catalog.get("game b")
	assert resolved is not None
	assert catalog.matches_campaign(resolved, campaign) is True


def test_search_returns_weighted_results(tmp_path):
	catalog_path = tmp_path / "catalog.json"
	catalog = GameCatalog(str(catalog_path))
	catalog.merge_games(
		[
			GameEntry(
				key="game c",
				name="Game C",
				weight=300,
				aliases=["game c"],
				sources=["helix"],
			),
			GameEntry(
				key="game d",
				name="Adventure D",
				weight=150,
				aliases=["adventure d"],
				sources=["campaign"],
			),
		]
	)

	results = catalog.search("adventure")
	assert results
	assert results[0].name == "Adventure D"
	assert len(results) == 1


def test_search_filters_out_non_matches(tmp_path):
	catalog_path = tmp_path / "catalog.json"
	catalog = GameCatalog(str(catalog_path))
	catalog.merge_games(
		[
			GameEntry(
				key="game e",
				name="Game E",
				weight=400,
				aliases=["game e"],
				sources=["helix"],
			)
		]
	)

	assert catalog.search("zzz") == []
