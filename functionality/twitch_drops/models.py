from __future__ import annotations

"""Data models used by the Twitch Drops functionality.

Provides simple dataclasses for condensed campaign and benefit representations
used across fetch, diff, and notifications.
"""

from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timezone


def _to_epoch_seconds(dt_str: str | None) -> Optional[int]:
	"""Convert an ISO 8601 string to a UTC epoch seconds integer.

	Returns None if parsing fails or the input is empty.
	"""
	if not dt_str:
		return None
	s = dt_str.replace("Z", "+00:00")
	try:
		dt = datetime.fromisoformat(s)
		if dt.tzinfo is None:
			dt = dt.replace(tzinfo=timezone.utc)
		return int(dt.timestamp())
	except Exception:
		return None


@dataclass
class BenefitRecord:
	"""Condensed representation of a drop benefit (reward)."""
	id: str
	name: str
	image_url: Optional[str]


@dataclass
class CampaignRecord:
	"""Condensed representation of a Twitch Drops campaign.

	Includes only fields required by bot commands and notifications.
	"""
	id: str
	name: str
	status: str  # ACTIVE | EXPIRED
	game_name: Optional[str]
	game_slug: Optional[str]
	game_box_art: Optional[str]
	starts_at: Optional[str]
	ends_at: Optional[str]
	benefits: list[BenefitRecord]

	@property
	def starts_ts(self) -> Optional[int]:
		"""Campaign start time (epoch seconds) or None."""
		return _to_epoch_seconds(self.starts_at)

	@property
	def ends_ts(self) -> Optional[int]:
		"""Campaign end time (epoch seconds) or None."""
		return _to_epoch_seconds(self.ends_at)
