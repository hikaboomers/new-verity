from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


async def setup(bot: "BallsDexBot"):
    # imported here so Django doesn't load the models before it's ready
    from .cog import Rarity

    await bot.add_cog(Rarity(bot))