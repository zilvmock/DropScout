import types

import hikari
import lightbulb
import pytest

from functionality.twitch_drops.commands import search_game as search_mod
from functionality.twitch_drops.commands.common import SharedContext
from functionality.twitch_drops.config import GuildConfigStore
from functionality.twitch_drops.favorites import FavoritesStore
from functionality.twitch_drops.game_catalog import GameCatalog, GameEntry
from functionality.twitch_drops.models import BenefitRecord, CampaignRecord


class StubClient:
	def __init__(self) -> None:
		self.registered = []

	def register(self, item):
		self.registered.append(item)
		return item


@pytest.fixture()
def shared(tmp_path):
	catalog = GameCatalog(str(tmp_path / "catalog.json"))
	catalog.merge_games(
		[
			GameEntry(
				key="valorant",
				name="Valorant",
				weight=500,
				aliases=["valorant"],
				sources=["seed"],
			)
		]
	)
	catalog.set_ready(True)
	return SharedContext(
		guild_store=GuildConfigStore(str(tmp_path / "guild.json")),
		ICON_LIMIT=6,
		ICON_SIZE=96,
		ICON_COLUMNS=3,
		MAX_ATTACH_PER_CMD=0,
		SEND_DELAY_MS=0,
		FETCH_TTL=30,
		game_catalog=catalog,
		favorites_store=FavoritesStore(str(tmp_path / "favorites.json")),
	)


class FakeCtx:
	def __init__(self):
		self.responses: list[tuple[tuple, dict]] = []
		self.deferred = False
		self.channel_id = 555
		self.client = type("Client", (), {"app": type("App", (), {"rest": object()})()})()

	async def respond(self, *args, **kwargs):
		self.responses.append((args, kwargs))

	async def defer(self, *args, **kwargs):
		self.deferred = True


@pytest.fixture()
def command(shared):
	client = StubClient()
	search_mod.register(client, shared)
	cmd_cls = next(cls for cls in client.registered if cls.__name__ == "DropsSearchGame")
	return cmd_cls, shared


@pytest.mark.asyncio
async def test_search_game_requires_selection(command):
	cmd_cls, shared = command
	ctx = FakeCtx()
	instance = object.__new__(cmd_cls)
	instance.game = ""
	await cmd_cls.invoke.__get__(instance, cmd_cls)(ctx)
	assert ctx.responses
	args, kwargs = ctx.responses[0]
	assert args and "Select a game" in args[0]
	assert kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_search_game_matches_and_finalizes(monkeypatch, command):
	cmd_cls, shared = command
	ctx = FakeCtx()
	instance = object.__new__(cmd_cls)
	instance.game = "valorant"

	campaign = CampaignRecord(
		id="c1",
		name="Valorant Drops",
		status="ACTIVE",
		game_name="Valorant",
		game_slug="valorant",
		game_box_art=None,
		starts_at=None,
		ends_at=None,
		benefits=[BenefitRecord(id="b1", name="Reward", image_url=None)],
	)

	async def fake_cached(self):
		return [campaign]

	async def fake_finalize(self, ctx_obj):
		ctx_obj.finalized = True

	shared.get_campaigns_cached = types.MethodType(fake_cached, shared)
	shared.finalize_interaction = types.MethodType(fake_finalize, shared)

	async def fake_collage(campaign, **kwargs):
		return None, None

	monkeypatch.setattr("functionality.twitch_drops.commands.search_game.build_benefits_collage", fake_collage)

	await cmd_cls.invoke.__get__(instance, cmd_cls)(ctx)

	assert ctx.deferred is True
	assert ctx.responses
	args, kwargs = ctx.responses[0]
	assert "embeds" in kwargs
	assert kwargs["embeds"][0].title == "Valorant"
	assert getattr(ctx, "finalized", False) is True
