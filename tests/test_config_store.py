import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from functionality.twitch_drops.config import GuildConfigStore


def test_guild_store_atomic_and_threadsafe(tmp_path: Path):
	path = tmp_path / "guild_config.json"
	store = GuildConfigStore(str(path))

	def worker(i: int):
		store.set_channel_id(123, 1000 + i)

	with ThreadPoolExecutor(max_workers=8) as ex:
		for i in range(20):
			ex.submit(worker, i)

	# File should be valid JSON and contain a channel_id value
	data = store.load()
	assert isinstance(data, dict)
	assert "123" in data
	assert isinstance(data["123"], dict)
	assert "channel_id" in data["123"]
	# Last-writer-wins; channel_id should be an int in our test range
	val = data["123"]["channel_id"]
	assert isinstance(val, int)
	assert 1000 <= val < 1020

