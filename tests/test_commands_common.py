import hikari
from hikari.files import Bytes
import pytest

from functionality.twitch_drops.commands.common import SharedContext
from functionality.twitch_drops.config import GuildConfigStore
from functionality.twitch_drops.favorites import FavoritesStore
from functionality.twitch_drops.models import BenefitRecord, CampaignRecord


class StubCatalog:
	def __init__(self) -> None:
		self.merged: list[list[CampaignRecord]] = []

	def merge_from_campaign_records(self, recs):
		self.merged.append(recs)
		return True


@pytest.fixture()
def shared(tmp_path):
	return SharedContext(
		guild_store=GuildConfigStore(str(tmp_path / "guild.json")),
		ICON_LIMIT=6,
		ICON_SIZE=96,
		ICON_COLUMNS=3,
		MAX_ATTACH_PER_CMD=0,
		SEND_DELAY_MS=0,
		FETCH_TTL=60,
		game_catalog=StubCatalog(),
		favorites_store=FavoritesStore(str(tmp_path / "favorites.json")),
	)


class FakeFetcher:
	call_count = 0

	async def fetch_condensed(self):
		FakeFetcher.call_count += 1
		return [
			CampaignRecord(
				id=f"c{FakeFetcher.call_count}",
				name="Campaign",
				status="ACTIVE",
				game_name="Game",
				game_slug="game",
				game_box_art=None,
				starts_at=None,
				ends_at=None,
				benefits=[BenefitRecord(id="b", name="Reward", image_url=None)],
			)
		]


@pytest.mark.asyncio
async def test_shared_context_caches_and_expires(monkeypatch, shared):
	monkeypatch.setattr("functionality.twitch_drops.fetcher.DropsFetcher", FakeFetcher)
	first = await shared.get_campaigns_cached()
	assert FakeFetcher.call_count == 1
	second = await shared.get_campaigns_cached()
	assert FakeFetcher.call_count == 1  # cache hit
	assert shared.game_catalog.merged and shared.game_catalog.merged[-1] is first

	# Force expiration and ensure refetch occurs
	shared._cache_exp = 0
	third = await shared.get_campaigns_cached()
	assert FakeFetcher.call_count == 2
	assert third[0].id == "c2"


class FinalizeCtx:
	def __init__(self, *, deferred: bool) -> None:
		self._dropscout_deferred = deferred
		self.calls: list[tuple[str, str | None]] = []
		self.responded: list[tuple[str, dict]] = []

	async def edit_last_response(self, *, content=None):
		self.calls.append(("edit_last", content))
		return

	async def edit_initial_response(self, *, content=None):
		self.calls.append(("edit_initial", content))
		raise RuntimeError("should not reach")

	async def delete_last_response(self):
		self.calls.append(("delete_last", None))
		return

	async def delete_initial_response(self):
		self.calls.append(("delete_initial", None))
		return

	async def respond(self, content, ephemeral=False):
		self.responded.append((content, {"ephemeral": ephemeral}))


@pytest.mark.asyncio
async def test_finalize_interaction_prefers_edits(shared):
	ctx = FinalizeCtx(deferred=True)
	await shared.finalize_interaction(ctx)
	assert ctx.calls[0] == ("edit_last", "Done.")
	assert not ctx.responded


class FinalizeCtxNoops:
	def __init__(self) -> None:
		self.responded: list[tuple[str, dict]] = []

	async def edit_last_response(self, *, content=None):
		raise RuntimeError("fail")

	async def edit_initial_response(self, *, content=None):
		raise RuntimeError("fail")

	async def delete_last_response(self):
		raise RuntimeError("fail")

	async def delete_initial_response(self):
		raise RuntimeError("fail")

	async def respond(self, content, ephemeral=False):
		self.responded.append((content, {"ephemeral": ephemeral}))


@pytest.mark.asyncio
async def test_finalize_interaction_falls_back_to_ephemeral(shared):
	ctx = FinalizeCtxNoops()
	await shared.finalize_interaction(ctx, message="All done")
	assert ctx.responded == [("All done", {"ephemeral": True})]


class SendCtx:
	def __init__(self, *, channel_id=1):
		self.channel_id = channel_id
		self.respond_calls: list[dict] = []
		self.sent: list[tuple[int, list[hikari.Embed]]] = []

		class _Rest:
			def __init__(self, outer):
				self.outer = outer

			async def create_message(self, channel_id, *, embeds=None, **kwargs):
				self.outer.sent.append((int(channel_id), list(embeds or [])))

		class _App:
			def __init__(self, outer):
				self.rest = SendCtx._Rest(outer)  # type: ignore[attr-defined]

		class _Client:
			def __init__(self, outer):
				self.app = SendCtx._App(outer)  # type: ignore[attr-defined]

		# Attach helper classes with closures
		SendCtx._Rest = _Rest  # type: ignore[attr-defined]
		SendCtx._App = _App  # type: ignore[attr-defined]
		SendCtx._Client = _Client  # type: ignore[attr-defined]

		self.client = SendCtx._Client(self)

	async def respond(self, **payload):
		self.respond_calls.append(payload)


@pytest.mark.asyncio
async def test_send_embeds_with_attachments(shared):
	ctx = SendCtx()
	embed = hikari.Embed(title="Hello")
	attachment = Bytes(b"123", "a.png")
	await shared.send_embeds(ctx, [embed], attachments_aligned=[attachment])
	assert ctx.sent and ctx.sent[0][0] == ctx.channel_id
	assert ctx.sent[0][1][0].title == "Hello"
	assert not ctx.respond_calls


@pytest.mark.asyncio
async def test_send_embeds_chunks_without_attachments(shared):
	ctx = SendCtx()
	embeds = [hikari.Embed(title=f"E{i}") for i in range(11)]
	await shared.send_embeds(ctx, embeds, attachments_aligned=None)
	assert len(ctx.respond_calls) == 2  # 10 + 1 embeds
	assert len(ctx.respond_calls[0]["embeds"]) == 10
	assert len(ctx.respond_calls[1]["embeds"]) == 1
