from functionality.twitch_drops.favorites import FavoritesStore


def test_add_and_remove_favorites(tmp_path):
	path = tmp_path / "favorites.json"
	store = FavoritesStore(str(path))

	assert store.get_user_favorites(1, 10) == []

	assert store.add_favorite(1, 10, "apex-legends")
	assert store.add_favorite(1, 10, "valorant")
	# Duplicate inserts should be ignored.
	assert not store.add_favorite(1, 10, "valorant")

	assert store.get_user_favorites(1, 10) == ["apex-legends", "valorant"]

	assert store.remove_favorite(1, 10, "apex-legends")
	assert not store.remove_favorite(1, 10, "apex-legends")
	assert store.get_user_favorites(1, 10) == ["valorant"]


def test_remove_many_and_cleanup(tmp_path):
	path = tmp_path / "favorites.json"
	store = FavoritesStore(str(path))

	for key in ("apex-legends", "valorant", "overwatch"):
		store.add_favorite(5, 20, key)

	removed = store.remove_many(5, 20, ["valorant", "unknown"])
	assert removed == 1
	assert store.get_user_favorites(5, 20) == ["apex-legends", "overwatch"]

	removed = store.remove_many(5, 20, ["apex-legends", "overwatch"])
	assert removed == 2
	assert store.get_user_favorites(5, 20) == []


def test_guild_favorites_snapshot(tmp_path):
	path = tmp_path / "favorites.json"
	store = FavoritesStore(str(path))

	store.add_favorite(99, 1, "apex")
	store.add_favorite(99, 1, "valorant")
	store.add_favorite(99, 2, "apex")

	result = store.get_guild_favorites(99)
	assert result == {1: {"apex", "valorant"}, 2: {"apex"}}


def test_watchers_lookup(tmp_path):
	path = tmp_path / "favorites.json"
	store = FavoritesStore(str(path))

	store.add_favorite(7, 101, "apex")
	store.add_favorite(7, 102, "valorant")
	store.add_favorite(7, 103, "apex")
	store.add_favorite(7, 103, "valorant")

	watchers = store.get_watchers(7, ["valorant"])
	assert watchers == {102: {"valorant"}, 103: {"valorant"}}

	watchers = store.get_watchers(7, ["apex", "valorant"])
	assert watchers == {
		101: {"apex"},
		102: {"valorant"},
		103: {"apex", "valorant"},
	}
