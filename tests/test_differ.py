from functionality.twitch_drops.differ import DropsDiffer
from functionality.twitch_drops.models import CampaignRecord, BenefitRecord


def rec(cid: str, status: str) -> CampaignRecord:
	return CampaignRecord(
		id=cid,
		name="Camp",
		status=status,
		game_name="Game",
		game_slug="game",
		game_box_art=None,
		starts_at=None,
		ends_at=None,
		benefits=[BenefitRecord(id="b", name="n", image_url=None)],
	)


def test_differ_activated():
	prev = {"c1": {"status": "UPCOMING"}}
	curr = [rec("c1", "ACTIVE"), rec("c2", "ACTIVE")]
	d = DropsDiffer().diff(prev, curr)
	ids = [c.id for c in d.activated]
	assert "c1" in ids

