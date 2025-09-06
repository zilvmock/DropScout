from __future__ import annotations

"""Image utilities for DropScout.

Builds simple collage images from drop benefit icons to include in embeds.
Falls back gracefully if Pillow is unavailable or image fetch fails.
"""

from typing import Optional, Sequence, Tuple
import io
import asyncio

import aiohttp

from .models import CampaignRecord, BenefitRecord


async def _fetch_bytes(url: str, session: aiohttp.ClientSession) -> Optional[bytes]:
	"""Fetch the raw bytes for a URL or return None on failure."""
	try:
		async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
			if resp.status == 200:
				return await resp.read()
	except Exception:
		return None
	return None


async def build_benefits_collage(
	campaign: CampaignRecord,
	*,
	limit: int | None = 6,
	icon_size: Tuple[int, int] = (96, 96),
	columns: int = 3,
) -> tuple[Optional[bytes], Optional[str]]:
	"""Return (png_bytes, filename) for a simple grid collage of benefit icons.

	- limit: max icons to include; use 0 or None for all.
	- columns: number of columns; if <= 0, computed automatically.
	- icon_size: target width/height for each icon.

	If Pillow is not available or no images can be fetched, returns (None, None).
	"""
	try:
		from PIL import Image  # type: ignore
	except Exception:
		return None, None

	icons: list[bytes] = []
	benefits_all = [b for b in campaign.benefits if b.image_url]
	if not benefits_all:
		return None, None
	if limit and limit > 0:
		benefits = benefits_all[:limit]
	else:
		benefits = benefits_all
	if not benefits:
		return None, None

	async with aiohttp.ClientSession() as session:
		tasks = [
			_fetch_bytes(b.image_url, session)  # type: ignore[arg-type]
			for b in benefits
		]
		results = await asyncio.gather(*tasks, return_exceptions=True)
	for r in results:
		if isinstance(r, bytes):
			icons.append(r)
	if not icons:
		return None, None

	# Compose grid
	cols = max(1, min(columns if columns and columns > 0 else len(icons), 10))
	w, h = icon_size
	rows = (len(icons) + cols - 1) // cols
	canvas = Image.new("RGBA", (cols * w, rows * h), (255, 255, 255, 0))
	i = 0
	for data in icons:
		try:
			img = Image.open(io.BytesIO(data)).convert("RGBA")
			img = img.resize((w, h))
			r, c = divmod(i, cols)
			canvas.paste(img, (c * w, r * h))
			i += 1
		except Exception:
			continue
	if i == 0:
		return None, None
	buf = io.BytesIO()
	canvas.save(buf, format="PNG")
	png = buf.getvalue()
	filename = f"drops_{campaign.id}.png"
	return png, filename
