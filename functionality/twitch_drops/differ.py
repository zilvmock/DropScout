from __future__ import annotations

"""Diffing logic for condensed Twitch Drops campaigns.

Tracks which campaigns transitioned to ACTIVE. Upcoming handling removed.
"""

from dataclasses import dataclass
from typing import Any

from .models import CampaignRecord


@dataclass
class DropsDiff:
	"""Represents changes between two condensed campaign snapshots."""

	activated: list[CampaignRecord]


class DropsDiffer:
	"""Compares previous and current campaign lists to produce a diff."""

	def diff(
		self,
		prev: dict[str, dict[str, Any]],
		curr: list[CampaignRecord],
	) -> DropsDiff:
		"""Return which campaigns have newly transitioned to ACTIVE."""
		prev_status: dict[str, str] = {cid: str(c.get("status", "")) for cid, c in prev.items()}
		activated: list[CampaignRecord] = []
		for c in curr:
			ps = prev_status.get(c.id)
			if c.status == "ACTIVE" and ps and ps != "ACTIVE":
				activated.append(c)
		return DropsDiff(activated=activated)
