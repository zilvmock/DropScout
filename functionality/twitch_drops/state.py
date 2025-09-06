from __future__ import annotations

"""Persistent state management for Twitch Drops monitoring.

Stores and loads the last known condensed campaigns snapshot to detect changes
between polling intervals.
"""

import os
import json
from typing import Any
from threading import Lock

_STATE_LOCK = Lock()

from .models import CampaignRecord


class DropsStateStore:
	"""Simple JSON-backed store for condensed campaign state."""

	def __init__(self, path: str = "data/campaigns_state.json") -> None:
		"""Initialize the store with a filesystem path."""
		self.path = path

	def load(self) -> dict[str, dict[str, Any]]:
		"""Load and return the previously saved state or an empty dict."""
		try:
			with open(self.path, "r", encoding="utf-8") as f:
				data = json.load(f)
			if isinstance(data, dict):
				return data  # type: ignore[return-value]
		except FileNotFoundError:
			pass
		except Exception:
			pass
		return {}


	def _atomic_write(self, payload: str) -> None:
		"""Atomically write JSON payload to the configured path."""
		dirname = os.path.dirname(self.path) or "."
		os.makedirs(dirname, exist_ok=True)
		tmp = f"{self.path}.tmp"
		with open(tmp, "w", encoding="utf-8") as f:
			f.write(payload)
		os.replace(tmp, self.path)

	def save(self, campaigns: list[CampaignRecord]) -> None:
		"""Write the current campaigns to disk for future diffing (atomic, synchronized)."""
		payload: dict[str, dict[str, Any]] = {
			c.id: {
				"id": c.id,
				"name": c.name,
				"status": c.status,
				"game_name": c.game_name,
				"game_box_art": c.game_box_art,
				"starts_at": c.starts_at,
				"ends_at": c.ends_at,
				"benefits": [
					{"id": b.id, "name": b.name, "image_url": b.image_url} for b in c.benefits
				],
			}
			for c in campaigns
		}
		with _STATE_LOCK:
			self._atomic_write(json.dumps(payload, indent=2, ensure_ascii=False))
