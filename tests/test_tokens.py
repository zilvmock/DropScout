import os
import pytest

from functionality.twitch_drops import twitch_drops as td


pytestmark = pytest.mark.asyncio


async def test_ensure_env_access_token_refresh(monkeypatch):
	# Simulate invalid current token, successful refresh
	os.environ["TWITCH_ACCESS_TOKEN"] = "old"
	os.environ["TWITCH_REFRESH_TOKEN"] = "refresh_token"

	async def fake_validate(session, token):
		return (False, None) if token == "old" else (True, {"client_id": td.ANDROID_CLIENT_ID})

	async def fake_refresh(session, client_id, refresh_token, client_secret=None):
		assert client_id == td.ANDROID_CLIENT_ID
		assert refresh_token == "refresh_token"
		return {"access_token": "newtok", "refresh_token": "newref"}

	monkeypatch.setattr(td, "_validate_token", fake_validate)
	monkeypatch.setattr(td, "_refresh_token", fake_refresh)

	import aiohttp
	async with aiohttp.ClientSession() as s:
		res = await td.ensure_env_access_token(s)
		assert res == "newtok"
		assert os.environ["TWITCH_ACCESS_TOKEN"] == "newtok"
		assert os.environ["TWITCH_REFRESH_TOKEN"] == "newref"

