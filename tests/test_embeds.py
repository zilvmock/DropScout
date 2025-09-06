import pytest

import hikari

from functionality.twitch_drops.embeds import build_campaign_embed
from functionality.twitch_drops.models import CampaignRecord, BenefitRecord


def _rec(**kwargs) -> CampaignRecord:
	benefits = [BenefitRecord(id="b1", name="Alpha", image_url=None)]
	return CampaignRecord(
		id=kwargs.get("id", "cid1"),
		name=kwargs.get("name", "Sample Campaign"),
		status="ACTIVE",
		game_name=kwargs.get("game_name", "Once Human"),
		game_slug=kwargs.get("game_slug", "once-human"),
		game_box_art=kwargs.get("game_box_art", "https://static/box.jpg"),
		starts_at=None,
		ends_at=None,
		benefits=benefits,
	)


def test_embed_title_and_link_uses_game_and_slug():
	rec = _rec()
	e = build_campaign_embed(rec, title_prefix="Active")
	assert isinstance(e, hikari.Embed)
	assert e.title == "Once Human"
	assert e.url == "https://www.twitch.tv/directory/category/once-human?filter=drops"
	assert e.color == 0x235876
	# No explicit "Channels" field present
	fields = [(f.name or "", f.value or "") for f in (e.fields or [])]
	assert not any(name.lower() == "channels" for name, _ in fields)

