from __future__ import annotations

import lightbulb

from .common import SharedContext


def register(client: lightbulb.Client, shared: SharedContext) -> str:
    @client.register
    class Hello(
        lightbulb.SlashCommand,
        name="hello",
        description="Say hello",
    ):
        @lightbulb.invoke
        async def invoke(self, ctx: lightbulb.Context) -> None:
            await ctx.respond("Hello👋")

    return "hello"

