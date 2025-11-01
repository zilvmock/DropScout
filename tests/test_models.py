from datetime import datetime, timezone

from functionality.twitch_drops.models import BenefitRecord, CampaignRecord


def test_campaign_record_timestamp_properties():
	rec = CampaignRecord(
		id="camp",
		name="Test",
		status="ACTIVE",
		game_name="Game",
		game_slug="game",
		game_box_art=None,
		starts_at="2024-04-01T12:00:00Z",
		ends_at="2024-04-03T05:30:00+02:00",
		benefits=[BenefitRecord(id="b", name="Reward", image_url=None)],
	)
	assert rec.starts_ts == int(datetime(2024, 4, 1, 12, tzinfo=timezone.utc).timestamp())
	expected_end = datetime(2024, 4, 3, 3, 30, tzinfo=timezone.utc)  # converted from +02:00
	assert rec.ends_ts == int(expected_end.timestamp())


def test_campaign_record_handles_invalid_timestamps():
	rec = CampaignRecord(
		id="camp",
		name="Invalid Times",
		status="ACTIVE",
		game_name=None,
		game_slug=None,
		game_box_art=None,
		starts_at="not-a-date",
		ends_at=None,
		benefits=[],
	)
	assert rec.starts_ts is None
	assert rec.ends_ts is None
