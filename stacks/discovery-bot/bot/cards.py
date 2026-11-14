"""Discord card: embed plus persistent Approve/Reject buttons. Thin; logic is in actions.py."""
import logging
from collections import namedtuple
from datetime import datetime, timezone

import discord

from . import actions

log = logging.getLogger(__name__)

Deps = namedtuple("Deps", "store aurral lidarr")
COVER = "https://coverartarchive.org/release-group/{}/front-250"


def build_embed(draft) -> discord.Embed:
    rec, pick = draft.rec, draft.pick
    year = f" ({pick.year})" if pick.year else ""
    e = discord.Embed(title=f"{rec.get('name', '')} — {pick.title}{year}")
    seeds = ", ".join((rec.get("sourceArtists") or [])[:4])
    tags = ", ".join((rec.get("matchedTags") or [])[:4])
    if seeds:
        e.add_field(name="Because you listen to", value=seeds, inline=False)
    if tags:
        e.add_field(name="Tags", value=tags, inline=False)
    if draft.note:
        e.add_field(name="Note", value=draft.note, inline=False)
    e.set_image(url=COVER.format(pick.mbid))
    return e


class CardView(discord.ui.View):
    def __init__(self, sug_id, deps=None):
        super().__init__(timeout=None)
        self.sug_id, self.deps = sug_id, deps
        for label, kind, style in (("Approve", "approve", discord.ButtonStyle.success),
                                   ("Reject", "reject", discord.ButtonStyle.danger)):
            b = discord.ui.Button(label=label, style=style, custom_id=f"disc:{kind}:{sug_id}")
            b.callback = self._make_callback(kind)
            self.add_item(b)

    def _make_callback(self, kind):
        async def callback(interaction: discord.Interaction):
            await interaction.response.defer()
            now = datetime.now(timezone.utc)
            d = self.deps
            if kind == "approve":
                ok, msg = await actions.approve(self.sug_id, d.store, d.aurral, d.lidarr, now)
            else:
                ok, msg = await actions.reject(self.sug_id, d.store, d.aurral, now)
            status = d.store.get(self.sug_id).status
            if status == "pending":  # failed approve: keep buttons, ephemeral error
                await interaction.followup.send(msg, ephemeral=True)
                return
            embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed()
            embed.set_footer(text=status.capitalize() + ("" if ok else " (no action)"))
            await interaction.edit_original_response(embed=embed, view=None)
            if kind == "approve" and ok and actions.needs_alert(msg):
                await interaction.followup.send(  # visible to the channel
                    f"{embed.title or 'Suggestion'}: {msg}")
            elif not ok or "Already" in msg or msg.startswith("Approved"):
                await interaction.followup.send(msg, ephemeral=True)
        return callback


def register_persistent_views(client, store, aurral, lidarr):
    deps = Deps(store, aurral, lidarr)
    for sug_id in store.pending_ids():
        client.add_view(CardView(sug_id, deps))
