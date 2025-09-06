from __future__ import annotations

"""Discord embed helpers for presenting Twitch Drops campaigns."""

import hikari
import re
from urllib.parse import quote

from .models import CampaignRecord


def build_campaign_embed(c: CampaignRecord, *, title_prefix: str) -> hikari.Embed:
	"""Build a consistent embed for a single campaign.

	Title is the game name; description is the campaign name. Start/end
	timestamps and drop names are included as fields. Game box art is shown
	as the thumbnail. The title_prefix (e.g., "Active Campaign") is used as
	the embed author to keep status context without changing the title.
	"""
	title = (c.game_name or c.name or "Twitch Drops").strip()
	# Fixed brand color
	color = 0x235876
	e = hikari.Embed(title=title, color=color)
	if title_prefix:
		e.set_author(name=title_prefix)
	# Campaign name as subtitle/description
	if c.name:
		e.description = c.name
	if c.starts_ts:
		e.add_field(name="Starts", value=f"<t:{c.starts_ts}:F> (<t:{c.starts_ts}:R>)", inline=True)
	if c.ends_ts:
		e.add_field(name="Ends", value=f"<t:{c.ends_ts}:F> (<t:{c.ends_ts}:R>)", inline=True)
	if c.benefits:
		first = c.benefits[:6]
		benefits_text = "\n".join(f"• {b.name}" for b in first)
		e.add_field(name="Drops", value=benefits_text or "N/A", inline=False)
	if c.game_box_art:
		e.set_thumbnail(c.game_box_art)

	# Add link to browse participating channels for this game's Drops
	if c.game_name:
		def _slugify(name: str) -> str:
			s = name.lower()
			s = re.sub(r"'", "", s)
			s = re.sub(r"\W+", "-", s)
			s = re.sub(r"-{2,}", "-", s).strip("-")
			return s or quote(name)

		slug = c.game_slug or _slugify(c.game_name)
		url = f"https://www.twitch.tv/directory/category/{slug}?filter=drops"
		# Make only the title (game name) clickable to the Drops directory
		e.url = url
	return e
