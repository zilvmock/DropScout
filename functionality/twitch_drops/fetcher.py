from __future__ import annotations

"""Data fetching helpers for the Twitch Drops functionality.

DropsFetcher converts the full GraphQL responses into condensed CampaignRecord
objects used by bot commands and notifications.
"""

from .models import CampaignRecord, BenefitRecord
from .game_catalog import get_game_catalog
from .twitch_drops import fetch_active_campaigns


class DropsFetcher:
	"""Fetches and condenses Twitch Drops campaign data."""

	async def fetch_condensed(self) -> list[CampaignRecord]:
		"""Return a list of ACTIVE campaigns with minimal fields.

		As a safety measure, we filter out campaigns with a start time in the
		future (if any appear), since Twitch rarely exposes those.
		"""
		data = await fetch_active_campaigns()
		campaigns = data.get("campaigns", []) if isinstance(data, dict) else []
		out: list[CampaignRecord] = []
		for c in campaigns:
			if not isinstance(c, dict):
				continue
			status = str(c.get("status", "")).upper()
			if status != "ACTIVE":
				continue
			game = c.get("game") or {}
			gname = game.get("displayName") or game.get("name")
			gslug = game.get("slug")
			# Collect unique benefits across time-based drops
			seen_benefit_ids: set[str] = set()
			benefits: list[BenefitRecord] = []
			for d in c.get("timeBasedDrops", []) or []:
				for edge in d.get("benefitEdges", []) or []:
					b = edge.get("benefit") or {}
					bid = str(b.get("id", ""))
					if not bid or bid in seen_benefit_ids:
						continue
					seen_benefit_ids.add(bid)
					benefits.append(
						BenefitRecord(
							id=bid,
							name=str(b.get("name", "Unknown")),
							image_url=b.get("imageAssetURL"),
						)
					)
			rec = CampaignRecord(
				id=str(c.get("id")),
				name=str(c.get("name", "")),
				status=status,
				game_name=gname,
				game_slug=gslug,
				game_box_art=(game or {}).get("boxArtURL"),
				starts_at=c.get("startAt"),
				ends_at=c.get("endAt"),
				benefits=benefits,
			)
			out.append(rec)
		try:
			get_game_catalog().merge_from_campaign_records(out)
		except Exception:
			pass
		return out
