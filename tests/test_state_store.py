from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from functionality.twitch_drops.state import DropsStateStore
from functionality.twitch_drops.models import CampaignRecord, BenefitRecord


def _rec(i: int) -> CampaignRecord:
	return CampaignRecord(
		id=f"c{i}",
		name=f"Camp {i}",
		status="ACTIVE",
		game_name="Game",
		game_slug="game",
		game_box_art=None,
		starts_at=None,
		ends_at=None,
		benefits=[BenefitRecord(id="b", name="n", image_url=None)],
	)


def test_state_store_atomic(tmp_path: Path):
	path = tmp_path / "state.json"
	store = DropsStateStore(str(path))

	def worker(i: int):
		store.save([_rec(i)])

	with ThreadPoolExecutor(max_workers=8) as ex:
		for i in range(20):
			ex.submit(worker, i)

	# Should be valid JSON and contain 1..20 last write
	data = store.load()
	assert isinstance(data, dict)
	assert len(data) == 1
	# The single key is the last one written by some thread
	key = next(iter(data))
	assert key.startswith("c")

