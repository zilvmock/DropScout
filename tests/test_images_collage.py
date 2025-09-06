import asyncio
import io
from PIL import Image

import pytest

from functionality.twitch_drops.images import build_benefits_collage
from functionality.twitch_drops.models import CampaignRecord, BenefitRecord


def _rec_with_icons(n: int) -> CampaignRecord:
	benefits = [BenefitRecord(id=f"b{i}", name=f"B{i}", image_url=f"http://img/{i}.png") for i in range(n)]
	return CampaignRecord(
		id="c1",
		name="Camp",
		status="ACTIVE",
		game_name="Game",
		game_slug="game",
		game_box_art=None,
		starts_at=None,
		ends_at=None,
		benefits=benefits,
	)


def _png_bytes(size=(10, 10)):
	buf = io.BytesIO()
	Image.new("RGBA", size, (255, 0, 0, 255)).save(buf, format="PNG")
	return buf.getvalue()


@pytest.mark.asyncio
async def test_build_benefits_collage(monkeypatch):
	rec = _rec_with_icons(4)

	async def fake_fetch(url, session):
		return _png_bytes()

	import functionality.twitch_drops.images as images
	monkeypatch.setattr(images, "_fetch_bytes", fake_fetch)

	png, fname = await build_benefits_collage(rec, limit=4, icon_size=(16, 16), columns=2)
	assert png and fname
	assert fname.endswith(".png")
	# PNG header check
	assert png[:8] == b"\211PNG\r\n\032\n"

