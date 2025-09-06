from __future__ import annotations

"""Per-guild configuration store for DropScout.

Currently stores the notifications channel id for each guild.
"""

import os
import json
from typing import Any, Optional
from threading import Lock

# Module-level lock to synchronize across multiple store instances in-process
_GUILD_CFG_LOCK = Lock()


class GuildConfigStore:
	"""JSON-backed store for guild-specific settings."""

	def __init__(self, path: str = "data/guild_config.json") -> None:
		"""Initialize the store with a filesystem path."""
		self.path = path

	def load(self) -> dict[str, dict[str, Any]]:
		"""Load all guild configs, returning an empty dict if missing."""
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
		# os.replace is atomic on POSIX/Windows
		os.replace(tmp, self.path)

	def save(self, data: dict[str, dict[str, Any]]) -> None:
		"""Write the provided guild configs to disk (atomic, process-synchronized)."""
		with _GUILD_CFG_LOCK:
			payload = json.dumps(data, indent=2, ensure_ascii=False)
			self._atomic_write(payload)

	def get_channel_id(self, guild_id: int) -> Optional[int]:
		"""Return the configured channel id for a guild, if any."""
		data = self.load()
		g = data.get(str(guild_id))
		cid = g.get("channel_id") if isinstance(g, dict) else None
		return int(cid) if isinstance(cid, int) else None

	def set_channel_id(self, guild_id: int, channel_id: int) -> None:
		"""Set the notifications channel id for a guild."""
		with _GUILD_CFG_LOCK:
			data = self.load()
			g = data.get(str(guild_id)) or {}
			g["channel_id"] = int(channel_id)
			data[str(guild_id)] = g
			payload = json.dumps(data, indent=2, ensure_ascii=False)
			self._atomic_write(payload)
