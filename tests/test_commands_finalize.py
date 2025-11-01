import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, cast

import pytest

import functionality.twitch_drops.commands as commands_mod
import functionality.twitch_drops.fetcher as fetcher_mod
import functionality.twitch_drops.images as images_mod
import lightbulb
from functionality.twitch_drops.models import CampaignRecord, BenefitRecord

pytestmark = pytest.mark.skip(reason="Legacy Drop commands are benched and not active")


class FakeClient:
    """Minimal stand-in for Lightbulb client to capture registered classes."""

    def __init__(self) -> None:
        self.registered: list[type] = []

    def register(self, cls):  # decorator-style usage: @client.register
        self.registered.append(cls)
        return cls


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
    """Legacy regression test retained for reference."""

    class _FakeFetcher:
        async def fetch_condensed(self):
            return [_active_week_campaign()]

    monkeypatch.setattr(fetcher_mod, "DropsFetcher", _FakeFetcher)

    async def _no_collage(*args, **kwargs):
        return None, None

    monkeypatch.setattr(images_mod, "build_benefits_collage", _no_collage)

    fake = FakeClient()
    commands_mod.register_commands(cast(lightbulb.Client, fake))

    target_cls = next(cls for cls in fake.registered if cls.__name__ == "DropsThisWeek")

    cmd_instance = object.__new__(target_cls)

    class FakeCtx:
        def __init__(self) -> None:
            self.deferred = False
            self.responses = []
            self.deleted_initial = False
            self.deleted_last = False

        async def defer(self, *_, **__):
            self.deferred = True

        async def respond(self, *_, **kwargs):
            self.responses.append(kwargs)

        async def delete_last_response(self):
            self.deleted_last = True

        async def delete_initial_response(self):
            self.deleted_initial = True

    ctx = FakeCtx()

    bound_invoke = target_cls.invoke.__get__(cmd_instance, target_cls)
    await bound_invoke(ctx)

    assert ctx.responses
    assert ctx.deleted_last or ctx.deleted_initial
