"""Discord transport for BH-DiC.

Authorization is checked before any text is sent to the intent router.
"""

from bh_dic.discord.bot import BHDiCBot

__all__ = ["BHDiCBot"]
