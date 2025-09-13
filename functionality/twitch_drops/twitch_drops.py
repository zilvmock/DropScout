"""Android-only Twitch GraphQL client for DropScout.

Provides persisted operation calls, token validation and refresh using the
Android Client-ID, and a convenience function to fetch and merge current
campaigns with details.
"""

import os
from typing import Any, Dict, List, Tuple, Optional

import aiohttp


# Official first-party client ID that works with persisted GQL ops (Android)
ANDROID_CLIENT_ID = "kd1unb4b3q4t58fwlpcbzcbnm76a8fp"   # Android app
ANDROID_UA = (
	"Dalvik/2.1.0 (Linux; U; Android 16; SM-S911B Build/TP1A.220624.014) "
	"tv.twitch.android.app/25.3.0/2503006"
)
# Acceptable client IDs for tokens we allow (Android only)
FIRST_PARTY_CLIENT_IDS = {ANDROID_CLIENT_ID}

def is_first_party_validate(payload: Dict[str, Any] | None) -> bool:
	return isinstance(payload, dict) and str(payload.get("client_id")) in FIRST_PARTY_CLIENT_IDS


class GQLOperation(dict):
	"""Helper to build persisted GraphQL operation payloads."""
	def __init__(self, name: str, sha256: str, variables: Dict[str, Any] | None = None) -> None:
		super().__init__(
			operationName=name,
			extensions={
				"persistedQuery": {
					"version": 1,
					"sha256Hash": sha256,
				}
			},
		)
		if variables is not None:
			self["variables"] = variables

	def with_variables(self, variables: Dict[str, Any]) -> "GQLOperation":
		merged = GQLOperation(
			self["operationName"],
			self["extensions"]["persistedQuery"]["sha256Hash"],
			variables=dict(self.get("variables", {})),
		)
		# Shallow merge is good enough for our variables usage
		merged["variables"].update(variables)
		return merged


GQL_OPERATIONS = {
	"Inventory": GQLOperation(
		"Inventory",
		"d86775d0ef16a63a33ad52e80eaff963b2d5b72fada7c991504a57496e1d8e4b",
		variables={"fetchRewardCampaigns": False},
	),
	"Campaigns": GQLOperation(
		"ViewerDropsDashboard",
		"5a4da2ab3d5b47c9f9ce864e727b2cb346af1e3ea8b897fe8f704a97ff017619",
		variables={"fetchRewardCampaigns": False},
	),
	"CampaignDetails": GQLOperation(
		"DropCampaignDetails",
		"039277bf98f3130929262cc7c6efd9c141ca3749cb6dca442fc8ead9a53f77c1",
		variables={
			"channelLogin": "",  # not used here; leave blank per TDM behavior
			"dropID": "",        # to be set per-campaign
		},
	),
}


async def gql_request(session: aiohttp.ClientSession, token: str, ops: Any) -> Any:
	"""POST a GQL request using Android first-party client only.

	Attempts both Authorization schemes with the Android Client-Id.
	"""

	def build_headers(scheme: str) -> Dict[str, str]:
		ua = os.getenv("TWITCH_USER_AGENT") or ANDROID_UA
		return {
			"Accept": "*/*",
			"Client-Id": ANDROID_CLIENT_ID,
			"Authorization": f"{scheme} {token}",
			"User-Agent": ua,
			"Content-Type": "application/json",
			"Origin": "https://www.twitch.tv",
			"Referer": "https://www.twitch.tv/",
			"Accept-Language": "en-US",
		}

	url = "https://gql.twitch.tv/gql"
	attempts: List[str] = ["OAuth", "Bearer"]

	last_error: Exception | None = None
	def is_persisted_nf(obj: Any) -> bool:
		def has_pqnf(d: Dict[str, Any]) -> bool:
			return any(
				(e.get("message") == "PersistedQueryNotFound") or
				(e.get("message") == "service error")
				for e in d.get("errors", [])
			)
		if isinstance(obj, list):
			return any(has_pqnf(x) for x in obj if isinstance(x, dict))
		if isinstance(obj, dict):
			return has_pqnf(obj)
		return False

	for scheme in attempts:
		headers = build_headers(scheme)
		try:
			async with session.post(url, json=ops, headers=headers) as resp:
				text = await resp.text()
				if resp.status in (401, 403):
					# try next combination
					last_error = aiohttp.ClientResponseError(
						request_info=resp.request_info, history=resp.history,
						status=resp.status, message=text or "Unauthorized", headers=resp.headers
					)
					continue
				if resp.status >= 400:
					# Try to parse JSON for GQL errors
					data = None
					try:
						data = await resp.json()
					except Exception:
						pass
					if data is not None and is_persisted_nf(data):
						# Let next combination try
						continue
					raise aiohttp.ClientResponseError(
						request_info=resp.request_info, history=resp.history,
						status=resp.status, message=text, headers=resp.headers
					)
				# OK path
				try:
					data = await resp.json()
				except Exception:
					# Not JSON? Raise with raw text
					raise aiohttp.ClientResponseError(
						request_info=resp.request_info, history=resp.history,
						status=resp.status, message=text, headers=resp.headers
					)
				# Handle persisted query errors by retrying with another scheme
				if is_persisted_nf(data):
					continue
				# Heuristic: if server returned a successful status but no user context,
				# treat as unauthorized for ops that rely on currentUser
				def lacks_user_context(obj: Any) -> bool:
					if isinstance(obj, dict) and isinstance(obj.get("data"), dict):
						d = obj["data"]
						if "currentUser" in d and d["currentUser"] is None:
							return True
					return False
				if lacks_user_context(data):
					# try next combination
					last_error = aiohttp.ClientResponseError(
						request_info=resp.request_info, history=resp.history,
						status=401, message="Missing user context", headers=resp.headers
					)
					continue
				return data
		except aiohttp.ClientResponseError as e:
			# for non-401/403, bubble up immediately
			if e.status not in (401, 403):
				raise
			last_error = e
			continue
	# If we get here, all attempts failed
	if last_error:
		raise last_error
	raise RuntimeError("Failed to perform GQL request: no attempts made")


def _merge_data(primary_data: Dict[str, Any], secondary_data: Dict[str, Any]) -> Dict[str, Any]:
	"""Merge two nested dicts with preference for primary values."""
	merged: Dict[str, Any] = {}
	for key in set(primary_data) | set(secondary_data):
		if key in primary_data and key in secondary_data:
			if isinstance(primary_data[key], dict) and isinstance(secondary_data[key], dict):
				merged[key] = _merge_data(primary_data[key], secondary_data[key])
			else:
				merged[key] = primary_data[key]
		elif key in primary_data:
			merged[key] = primary_data[key]
		else:
			merged[key] = secondary_data[key]
	return merged


async def _validate_token(session: aiohttp.ClientSession, token: str) -> Tuple[bool, Dict[str, Any] | None]:
	"""Validate a token via id.twitch.tv using OAuth/Bearer schemes."""
	url = "https://id.twitch.tv/oauth2/validate"
	for scheme in ("OAuth", "Bearer"):
		async with session.get(url, headers={"Authorization": f"{scheme} {token}"}) as r:
			if r.status == 200:
				return True, await r.json()
	return False, None


async def _refresh_token(
	session: aiohttp.ClientSession,
	client_id: str,
	refresh_token: str,
	client_secret: Optional[str] = None,
) -> Dict[str, Any]:
    """Attempt to refresh via id.twitch.tv first, then passport.twitch.tv.

    Twitch first‑party (Android) flows sometimes use the Passport domain for
    token refresh. If the standard id.twitch.tv endpoint rejects the request,
    fall back to passport with the same payload.
    """
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if client_secret:
        payload["client_secret"] = client_secret

    errors: list[str] = []
    for endpoint in ("https://id.twitch.tv/oauth2/token", "https://passport.twitch.tv/oauth2/token"):
        try:
            async with session.post(endpoint, data=payload) as r:
                txt = await r.text()
                if r.status >= 400:
                    # Collect details and try next endpoint
                    try:
                        data = await r.json()
                    except Exception:
                        data = None
                    errors.append(f"{endpoint} -> {r.status} {txt or data}")
                    continue
                return await r.json()
        except Exception as e:
            errors.append(f"{endpoint} -> {e}")
            continue
    # If neither endpoint succeeded, raise a consolidated error
    raise RuntimeError("refresh failed: " + "; ".join(errors))
async def ensure_env_access_token(session: aiohttp.ClientSession) -> str:
	"""Return a valid access token from the environment, refreshing if needed.

	Env vars used:
	- TWITCH_ACCESS_TOKEN: current token (Android first‑party)
	- TWITCH_REFRESH_TOKEN: refresh token minted via Android device flow
	"""
	access = os.getenv("TWITCH_ACCESS_TOKEN", "") or ""
	refresh = os.getenv("TWITCH_REFRESH_TOKEN", "") or ""
	ok, val = await _validate_token(session, access) if access else (False, None)
	if ok and is_first_party_validate(val):
		return access
	# Attempt first‑party refresh using Android client (no secret). Try both
	# id.twitch.tv and passport.twitch.tv endpoints internally.
	errors: list[str] = []
	if refresh:
		try:
			r = await _refresh_token(session, ANDROID_CLIENT_ID, refresh)
			a2 = r.get("access_token")
			rf2 = r.get("refresh_token", refresh)
			if a2:
				ok, val = await _validate_token(session, a2)
				if ok and is_first_party_validate(val):
					os.environ["TWITCH_ACCESS_TOKEN"] = a2
					os.environ["TWITCH_REFRESH_TOKEN"] = rf2
					return a2
		except Exception as e:
			errors.append(f"android refresh failed: {e}")

	# Optional: if provided, allow a one-time regeneration using a different
	# refresh token source (e.g., TWITCH_REFRESH_TOKEN_ANDROID). This helps
	# recover when the default refresh token belongs to a different client.
	alt_refresh = os.getenv("TWITCH_REFRESH_TOKEN_ANDROID", "") or ""
	if alt_refresh and alt_refresh != refresh:
		try:
			r = await _refresh_token(session, ANDROID_CLIENT_ID, alt_refresh)
			a2 = r.get("access_token")
			rf2 = r.get("refresh_token", alt_refresh)
			if a2:
				ok, val = await _validate_token(session, a2)
				if ok and is_first_party_validate(val):
					os.environ["TWITCH_ACCESS_TOKEN"] = a2
					os.environ["TWITCH_REFRESH_TOKEN"] = rf2
					return a2
		except Exception as e:
			errors.append(f"android alt refresh failed: {e}")
	# All attempts failed; surface a clear error with collected attempts
	raise RuntimeError(
		"Service token invalid and refresh failed using Android client. "
		"Ensure the refresh token belongs to an Android device authorization. "
		"Tried id.twitch.tv and passport.twitch.tv refresh endpoints. Details: "
		+ "; ".join(errors)
	)


async def fetch_active_campaigns() -> Dict[str, Any]:
	"""Fetch ACTIVE campaigns and merge overview + details.

	Twitch's public GQL often does not expose truly future campaigns. To be
	conservative, we also filter by startAt <= now.
	"""
	async with aiohttp.ClientSession() as session:
		token_to_use = await ensure_env_access_token(session)

		# Prefer login for CampaignDetails; fetch from token validation payload
		ok, val = await _validate_token(session, token_to_use)
		if not ok:
			raise RuntimeError("Failed to validate token after ensuring env token")
		user_login = str((val or {}).get("login", ""))

		# In-progress (inventory)
		inventory_resp = await gql_request(session, token_to_use, GQL_OPERATIONS["Inventory"])
		inv_root = inventory_resp.get("data") if isinstance(inventory_resp, dict) else None
		if not isinstance(inv_root, dict) or inv_root.get("currentUser") in (None, False):
			raise RuntimeError("Missing user context in Inventory response; token not accepted by GQL")
		inv = inv_root["currentUser"]["inventory"]
		ongoing = inv.get("dropCampaignsInProgress") or []
		claimed_benefits = {b["id"]: b.get("lastAwardedAt") for b in inv.get("gameEventDrops", [])}
		inventory_map: Dict[str, Any] = {c["id"]: c for c in ongoing}

		# Available campaigns overview
		campaigns_resp = await gql_request(session, token_to_use, GQL_OPERATIONS["Campaigns"])
		camp_root = campaigns_resp.get("data") if isinstance(campaigns_resp, dict) else None
		if not isinstance(camp_root, dict) or camp_root.get("currentUser") in (None, False):
			raise RuntimeError("Missing user context in Campaigns response; token not accepted by GQL")
		all_campaigns = camp_root["currentUser"].get("dropCampaigns") or []
		target_status = {"ACTIVE"}
		available_map: Dict[str, Any] = {c["id"]: c for c in all_campaigns if c.get("status") in target_status}

		# Fetch details in small batches
		ids = list(available_map.keys())
		full_details: Dict[str, Any] = {}
		batch_size = 20
		for i in range(0, len(ids), batch_size):
			batch = ids[i : i + batch_size]
			ops = [
				GQL_OPERATIONS["CampaignDetails"].with_variables(
					{"dropID": cid, "channelLogin": user_login}
				)
				for cid in batch
			]
			resp_list: List[Any] = await gql_request(session, token_to_use, ops)
			for r in resp_list:
				if not isinstance(r, dict):
					continue
				d = r.get("data")
				if not isinstance(d, dict):
					continue
				user = d.get("user")
				if not isinstance(user, dict):
					# If no user object, skip this item (token/client mismatch or invalid login)
					continue
				data = user.get("dropCampaign")
				if isinstance(data, dict) and "id" in data:
					full_details[data["id"]] = data

		# Merge: inventory + details (taking inventory first), falling back to available overview
		merged_list: List[Dict[str, Any]] = []
		now_iso = None  # not needed; we filter by start time when condensing
		for cid in ids:
			primary = inventory_map.get(cid, available_map[cid])
			detail = full_details.get(cid, {})
			merged = _merge_data(primary, detail) if detail else dict(primary)
			merged_list.append(merged)

		return {
			"campaigns": merged_list,
			"claimed_benefits": claimed_benefits,
		}


async def fetch_and_save_campaigns_json(filepath: str) -> Tuple[int, str]:
	"""Fetch campaigns and save to a JSON file.

	Returns a tuple of (campaign_count, filepath).
	"""
	# Ensure a valid token and fetch (reads from env, refreshes if possible)
	data = await fetch_active_campaigns()
	os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
	import json
	with open(filepath, "w", encoding="utf-8") as f:
		json.dump(data, f, indent=2, ensure_ascii=False)
	return len(data.get("campaigns", [])), filepath
