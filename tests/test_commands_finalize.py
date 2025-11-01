import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, cast

import pytest

import functionality.twitch_drops.commands as commands_mod
import functionality.twitch_drops.fetcher as fetcher_mod
import functionality.twitch_drops.images as images_mod
import lightbulb
from functionality.twitch_drops.models import CampaignRecord, BenefitRecord


class FakeClient:
    """Minimal stand-in for Lightbulb client to capture registered classes."""

    def __init__(self) -> None:
        self.registered: list[type] = []

    def register(self, cls):  # decorator-style usage: @client.register
        self.registered.append(cls)
        return cls


class FakeCtx:
    """Minimal context with defer/respond and response cleanup hooks."""

    def __init__(self) -> None:
        self.deferred = False
        self.responses: list[dict] = []
        self.deleted_initial = False
        self.deleted_last = False
        self.edited_initial = False
        self.edited_last = False
        self.last_edit_content: Optional[str] = None

    async def defer(self, *_, **__):
        self.deferred = True

    async def respond(self, *_, **kwargs):
        # record all respond calls (embeds or content)
        self.responses.append(kwargs)

    # Methods used by _finalize_interaction
    async def delete_last_response(self):
        self.deleted_last = True

    async def delete_initial_response(self):
        self.deleted_initial = True

    async def edit_last_response(self, *, content: Optional[str] = None, **kwargs):
        self.edited_last = True
        self.last_edit_content = content

    async def edit_initial_response(self, *, content: Optional[str] = None, **kwargs):
        self.edited_initial = True
        self.last_edit_content = content


def _active_week_campaign() -> CampaignRecord:
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=1)
    return CampaignRecord(
        id="c1",
        name="Camp One",
        status="ACTIVE",
        game_name="Game A",
        game_slug="game-a",
        game_box_art=None,
        starts_at=now.isoformat(),
        ends_at=end.isoformat(),
        benefits=[BenefitRecord(id="b1", name="Reward", image_url="https://img/1.png")],
    )


@pytest.mark.asyncio
async def test_drops_this_week_clears_deferred_placeholder(monkeypatch):
    """Invoking drops_this_week should finalize the deferred interaction."""

    # Patch fetching to return one active campaign ending within a week
    class _FakeFetcher:
        async def fetch_condensed(self):
            return [_active_week_campaign()]

    monkeypatch.setattr(fetcher_mod, "DropsFetcher", _FakeFetcher)

    # Do not attempt collages (avoid network/IO). Force fallback path.
    async def _no_collage(*args, **kwargs):
        return None, None

    monkeypatch.setattr(images_mod, "build_benefits_collage", _no_collage)

    # Register commands using a fake client to capture the command classes
    fake = FakeClient()
    # Cast to satisfy static type checkers; runtime only needs .register
    commands_mod.register_commands(cast(lightbulb.Client, fake))  # populates fake.registered

    # Find the DropsThisWeek command class by its class name
    target_cls = next(cls for cls in fake.registered if cls.__name__ == "DropsThisWeek")

    # Create a minimal instance without running heavy base initializers
    cmd_instance = object.__new__(target_cls)
    ctx = FakeCtx()

    # Invoke and ensure it completes and clears the placeholder
    # Access via descriptor to obtain a bound coroutine function
    bound_invoke = target_cls.invoke.__get__(cmd_instance, target_cls)
    await bound_invoke(ctx)

    # We expect at least one response (the embeds chunk)
    assert ctx.responses, "Command did not produce any response output"

    # And the deferred placeholder should be finalized (deleted or edited).
    assert (
        ctx.deleted_last or ctx.deleted_initial or ctx.edited_last or ctx.edited_initial
    ), "Deferred placeholder was not finalized"
    if ctx.edited_last or ctx.edited_initial:
        assert ctx.last_edit_content == "Done."
