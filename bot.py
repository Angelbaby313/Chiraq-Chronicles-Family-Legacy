import os
import io
import time
import asyncio
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
GUILD_ID = int(os.environ["GUILD_ID"])
TOKEN = os.environ["DISCORD_TOKEN"]

ACCENT = 0xB71C1C

MANAGEMENT = ["Family Management"]
COUNCIL = ["Family Council"]
MEDIATORS = ["Family Mediators"]
HEADS = ["Head of Family"]
STAFF = MANAGEMENT + COUNCIL
SUPPORT_STAFF = STAFF + MEDIATORS
BASE_ROLES = MANAGEMENT + COUNCIL + MEDIATORS + HEADS + ["Family Member"]

LAYOUT = [
    ("📌 FAMILY INFORMATION", None,
     ["family-rules", "family-announcements", "family-directory", "family-tree"], []),

    ("🏙️ FAMILY COMMUNITY", None,
     ["family-chat", "family-gallery", "family-recruitment", "family-leaderboard"],
     ["Family Lounge"]),

    ("🎫 FAMILY SERVICES", None,
     ["register-your-family", "family-support", "family-mediation"], []),

    ("💎 HEAD OF FAMILY HUB", HEADS + STAFF,
     ["head-of-family-chat", "roster-requests", "family-updates"], []),

    ("🤝 MEDIATION CENTER", SUPPORT_STAFF,
     ["mediation-review", "mediation-records"], []),

    ("🔒 FAMILY MANAGEMENT", STAFF,
     ["registration-review", "family-leaders", "family-strikes",
      "family-records", "ticket-transcripts", "bot-logs"], []),

    ("📁 PRIVATE TICKETS", SUPPORT_STAFF, [], []),
]

READONLY = {
    "family-rules", "family-announcements", "family-directory", "family-tree",
    "family-leaderboard", "register-your-family", "family-support",
    "family-mediation", "registration-review", "family-leaders",
    "family-strikes", "family-records", "mediation-review",
    "mediation-records", "ticket-transcripts", "bot-logs"
}

ICONS = {
    "family-rules": "📜", "family-announcements": "📢",
    "family-directory": "📖", "family-tree": "🌳",
    "family-chat": "💬", "family-gallery": "📸",
    "family-recruitment": "🤝", "family-leaderboard": "🏆",
    "register-your-family": "📝", "family-support": "🎫",
    "family-mediation": "⚖️", "head-of-family-chat": "💎",
    "roster-requests": "👥", "family-updates": "📝",
    "mediation-review": "🤝", "mediation-records": "🗃️",
    "registration-review": "📋", "family-leaders": "👑",
    "family-strikes": "⚠️", "family-records": "🗂️",
    "ticket-transcripts": "📄", "bot-logs": "🤖",
}

locks = {}


def base_name(name):
    return name.split("・", 1)[-1].strip()


def role(guild, name):
    return next((r for r in guild.roles if base_name(r.name) == name), None)


def channel(guild, name):
    return next((c for c in guild.text_channels if base_name(c.name) == name), None)


def pretty_channel(name):
    return f'{ICONS.get(name, "🔊")}・{name}'


def permissions(guild, names=None):
    p = {
        guild.default_role: discord.PermissionOverwrite(view_channel=names is None),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_channels=True,
            manage_roles=True, read_message_history=True, attach_files=True
        ),
    }
    if names is not None:
        for n in set(names + MANAGEMENT):
            r = role(guild, n)
            if r:
                p[r] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True,
                    read_message_history=True, connect=True, attach_files=True
                )
    return p


def database():
    db = sqlite3.connect("family_bot.db")
    db.execute("""CREATE TABLE IF NOT EXISTS families(
        guild INTEGER,
        name TEXT,
        leader INTEGER,
        coleader TEXT,
        color TEXT,
        motto TEXT,
        recruiting TEXT,
        status TEXT,
        role_id INTEGER,
        created REAL,
        PRIMARY KEY(guild,name)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS family_members(
        guild INTEGER,
        family TEXT,
        member INTEGER,
        PRIMARY KEY(guild,family,member)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS family_points(
        guild INTEGER,
        family TEXT,
        points INTEGER DEFAULT 0,
        PRIMARY KEY(guild,family)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS family_strikes(
        guild INTEGER,
        family TEXT,
        reason TEXT,
        actor INTEGER,
        created REAL
    )""")
    return db


def is_management(member):
    return member.guild_permissions.administrator or any(
        base_name(r.name) in MANAGEMENT for r in member.roles
    )


def is_council(member):
    return is_management(member) or any(
        base_name(r.name) in COUNCIL for r in member.roles
    )


def is_mediator(member):
    return is_council(member) or any(
        base_name(r.name) in MEDIATORS for r in member.roles
    )


async def refresh_directory(guild):
    ch = channel(guild, "family-directory")
    if not ch:
        return
    with database() as db:
        rows = db.execute(
            "SELECT name,leader,coleader,color,motto,recruiting FROM families "
            "WHERE guild=? AND status='approved' ORDER BY name",
            (guild.id,),
        ).fetchall()

    if not rows:
        body = "No families have been officially approved yet."
    else:
        chunks = []
        for name, leader, coleader, color, motto, recruiting in rows:
            chunks.append(
                f"### 👨‍👩‍👧‍👦 {name}\n"
                f"💎 Head of Family: <@{leader}>\n"
                f"👥 Co-Head/Second: {coleader or 'Not listed'}\n"
                f"🎨 Color: {color or 'Not listed'}\n"
                f"💬 Motto: {motto or 'Not listed'}\n"
                f"📣 Recruiting: {recruiting or 'Ask the Head of Family'}"
            )
        body = "\n\n".join(chunks)

    marker = "CC Family Directory v1"
    existing = [
        m async for m in ch.history(limit=50)
        if m.author == bot.user and m.embeds and m.embeds[0].footer.text == marker
    ]
    e = discord.Embed(
        title="📖 CHIRAQ CHRONICLES FAMILY DIRECTORY",
        description=body[:4000],
        color=ACCENT,
    )
    e.set_footer(text=marker)
    if existing:
        await existing[0].edit(embed=e)
    else:
        await ch.send(embed=e)


async def refresh_leaderboard(guild):
    ch = channel(guild, "family-leaderboard")
    if not ch:
        return
    with database() as db:
        rows = db.execute(
            """SELECT f.name, COALESCE(p.points,0)
               FROM families f
               LEFT JOIN family_points p
               ON p.guild=f.guild AND p.family=f.name
               WHERE f.guild=? AND f.status='approved'
               ORDER BY COALESCE(p.points,0) DESC, f.name""",
            (guild.id,),
        ).fetchall()

    lines = [
        f"**{n}. {name}** — {points} points"
        for n, (name, points) in enumerate(rows, 1)
    ] or ["No approved families are on the board yet."]

    marker = "CC Family Leaderboard v1"
    existing = [
        m async for m in ch.history(limit=50)
        if m.author == bot.user and m.embeds and m.embeds[0].footer.text == marker
    ]
    e = discord.Embed(
        title="🏆 FAMILY LEADERBOARD",
        description="\n".join(lines)[:4000],
        color=ACCENT,
    )
    e.set_footer(text=marker)
    if existing:
        await existing[0].edit(embed=e)
    else:
        await ch.send(embed=e)


class FamilyBot(commands.Bot):
    async def setup_hook(self):
        self.add_view(RegisterPanel())
        self.add_view(SupportPanel())
        self.add_view(MediationPanel())
        self.add_view(CloseTicket())
        target = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=target)
        await self.tree.sync(guild=target)


intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = FamilyBot(
    command_prefix=commands.when_mentioned,
    intents=intents,
    allowed_mentions=discord.AllowedMentions.none(),
)


class FamilyRegistration(discord.ui.Modal, title="Register Your Family"):
    family_name = discord.ui.TextInput(label="Family name", max_length=50)
    head_name = discord.ui.TextInput(label="Head of Family / character name", max_length=80)
    color = discord.ui.TextInput(label="Family color", max_length=40, required=False)
    motto = discord.ui.TextInput(label="Family motto", max_length=150, required=False)
    details = discord.ui.TextInput(
        label="Co-head, recruiting status & family details",
        style=discord.TextStyle.paragraph,
        max_length=900,
    )

    async def on_submit(self, i):
        await i.response.defer(ephemeral=True)
        async with locks.setdefault(("registration", i.user.id), asyncio.Lock()):
            existing = next(
                (
                    c for c in i.guild.text_channels
                    if c.topic and c.topic.startswith(
                        f"cc-family:{i.user.id}:open:registration"
                    )
                ),
                None,
            )
            if existing:
                return await i.followup.send(
                    f"You already have an open registration: {existing.mention}",
                    ephemeral=True,
                )

            cat = discord.utils.get(i.guild.categories, name="📁 PRIVATE TICKETS")
            if not cat:
                return await i.followup.send("Run /setup_family first.", ephemeral=True)

            p = permissions(i.guild, STAFF)
            p[i.user] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True,
                read_message_history=True, attach_files=True
            )

            safe = "".join(
                x for x in str(self.family_name).lower().replace(" ", "-")
                if x.isalnum() or x == "-"
            )[:35]

            c = await i.guild.create_text_channel(
                f"family-{safe or i.user.id}",
                category=cat,
                overwrites=p,
                topic=f"cc-family:{i.user.id}:open:registration",
            )

            e = discord.Embed(
                title="👨‍👩‍👧‍👦 FAMILY REGISTRATION",
                description="Chiraq Chronicles • Family Management Review",
                color=ACCENT,
            )
            e.add_field(name="Family Name", value=str(self.family_name), inline=False)
            e.add_field(
                name="Head of Family",
                value=f"{self.head_name}\nDiscord: {i.user.mention}",
                inline=False,
            )
            e.add_field(name="Family Color", value=str(self.color) or "Not provided", inline=False)
            e.add_field(name="Family Motto", value=str(self.motto) or "Not provided", inline=False)
            e.add_field(name="Details", value=str(self.details), inline=False)
            await c.send(content=i.user.mention, embed=e, view=CloseTicket())

            review = channel(i.guild, "registration-review")
            if review:
                await review.send(
                    f"📝 New family registration: **{self.family_name}** "
                    f"from {i.user.mention} → {c.mention}"
                )
            await i.followup.send(
                f"Your registration ticket is ready: {c.mention}", ephemeral=True
            )


class SupportForm(discord.ui.Modal, title="Family Support"):
    subject = discord.ui.TextInput(label="What do you need help with?", max_length=100)
    details = discord.ui.TextInput(
        label="Details", style=discord.TextStyle.paragraph, max_length=1000
    )

    async def on_submit(self, i):
        await create_simple_ticket(
            i, "support", "🎫 FAMILY SUPPORT",
            [("Subject", str(self.subject)), ("Details", str(self.details))],
            SUPPORT_STAFF
        )


class MediationForm(discord.ui.Modal, title="Request Family Mediation"):
    families = discord.ui.TextInput(
        label="Family/families involved", max_length=150
    )
    issue = discord.ui.TextInput(
        label="What happened?", style=discord.TextStyle.paragraph, max_length=1200
    )
    requested = discord.ui.TextInput(
        label="What resolution are you looking for?",
        style=discord.TextStyle.paragraph, max_length=700
    )

    async def on_submit(self, i):
        await create_simple_ticket(
            i, "mediation", "🤝 FAMILY MEDIATION REQUEST",
            [
                ("Family/Families", str(self.families)),
                ("Issue", str(self.issue)),
                ("Requested Resolution", str(self.requested)),
            ],
            SUPPORT_STAFF,
        )
        review = channel(i.guild, "mediation-review")
        if review:
            await review.send(f"🤝 New mediation request submitted by {i.user.mention}.")


async def create_simple_ticket(i, kind, title, fields, access):
    await i.response.defer(ephemeral=True)
    cat = discord.utils.get(i.guild.categories, name="📁 PRIVATE TICKETS")
    if not cat:
        return await i.followup.send("Run /setup_family first.", ephemeral=True)

    p = permissions(i.guild, access)
    p[i.user] = discord.PermissionOverwrite(
        view_channel=True, send_messages=True,
        read_message_history=True, attach_files=True
    )
    c = await i.guild.create_text_channel(
        f"{kind}-{i.user.id}",
        category=cat,
        overwrites=p,
        topic=f"cc-family:{i.user.id}:open:{kind}",
    )
    e = discord.Embed(title=title, color=ACCENT)
    for name, value in fields:
        e.add_field(name=name, value=value, inline=False)
    await c.send(content=i.user.mention, embed=e, view=CloseTicket())
    await i.followup.send(f"Your private ticket is ready: {c.mention}", ephemeral=True)


class RegisterPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Register Family", emoji="👨‍👩‍👧‍👦",
        style=discord.ButtonStyle.danger, custom_id="ccfamily:register"
    )
    async def register(self, i, button):
        await i.response.send_modal(FamilyRegistration())


class SupportPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Open Family Support", emoji="🎫",
        style=discord.ButtonStyle.secondary, custom_id="ccfamily:support"
    )
    async def support(self, i, button):
        await i.response.send_modal(SupportForm())


class MediationPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Request Mediation", emoji="🤝",
        style=discord.ButtonStyle.primary, custom_id="ccfamily:mediation"
    )
    async def mediation(self, i, button):
        await i.response.send_modal(MediationForm())


class CloseTicket(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close & Archive", emoji="🔒",
        style=discord.ButtonStyle.danger, custom_id="ccfamily:close"
    )
    async def close(self, i, button):
        c = i.channel
        parts = (c.topic or "").split(":")
        if len(parts) < 4 or parts[0] != "cc-family":
            return await i.response.send_message(
                "This is not a Family Hub ticket.", ephemeral=True
            )
        if not (is_mediator(i.user) or str(i.user.id) == parts[1]):
            return await i.response.send_message(
                "You cannot close this ticket.", ephemeral=True
            )

        await i.response.defer(ephemeral=True)
        log = channel(i.guild, "ticket-transcripts")
        messages = [m async for m in c.history(limit=None, oldest_first=True)]
        text = (
            f"CHIRAQ CHRONICLES FAMILY TICKET {c.id}\n"
            f"Type: {parts[3]}\nClosed by: {i.user} ({i.user.id})\n\n"
        )
        for m in messages:
            text += f"[{m.created_at.isoformat()}] {m.author}: {m.content}\n"
            for a in m.attachments:
                text += f"Attachment: {a.filename} {a.url}\n"

        if log:
            raw = text.encode("utf-8")
            await log.send(
                content=f"Archived {c.name}",
                file=discord.File(io.BytesIO(raw), filename=f"family-ticket-{c.id}.txt"),
            )
        await i.followup.send("Ticket archived. Removing channel.", ephemeral=True)
        await c.delete(reason="Family ticket archived")


@bot.tree.command(
    name="setup_family",
    description="Create the Chiraq Chronicles Family Hub"
)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.checks.has_permissions(administrator=True)
async def setup_family(i):
    await i.response.defer(ephemeral=True, thinking=True)
    g = i.guild

    if g.id != GUILD_ID:
        return await i.followup.send("Wrong server.", ephemeral=True)

    for name in BASE_ROLES:
        if not role(g, name):
            await g.create_role(
                name=name,
                color=discord.Color(ACCENT),
                permissions=discord.Permissions.none(),
                reason="Chiraq Family Hub setup",
            )

    for catname, names, texts, voices in LAYOUT:
        cat = discord.utils.get(g.categories, name=catname)
        if not cat:
            cat = await g.create_category(catname, overwrites=permissions(g, names))

        for name in texts:
            ch = next(
                (x for x in cat.text_channels if base_name(x.name) == name), None
            )
            if not ch:
                p = permissions(g, names)
                if name in READONLY:
                    for who, ow in p.items():
                        if who != g.me:
                            ow.send_messages = False
                await g.create_text_channel(
                    pretty_channel(name), category=cat, overwrites=p
                )

        for name in voices:
            if not next(
                (x for x in cat.voice_channels if base_name(x.name) == name), None
            ):
                await g.create_voice_channel(
                    name, category=cat, overwrites=permissions(g, names)
                )

    reg = channel(g, "register-your-family")
    if reg and not any(
        m.author == bot.user for m in [m async for m in reg.history(limit=20)]
    ):
        e = discord.Embed(
            title="👨‍👩‍👧‍👦 REGISTER YOUR FAMILY",
            description=(
                "Ready to make your family official in **Chiraq Chronicles**?\n\n"
                "Submit your family name, Head of Family, family color, motto, "
                "co-head/second-in-command and recruiting information below.\n\n"
                "Family Management/Council will review the registration. "
                "**Private family categories and channels are created manually after approval.**"
            ),
            color=ACCENT,
        )
        await reg.send(embed=e, view=RegisterPanel())

    sup = channel(g, "family-support")
    if sup and not any(
        m.author == bot.user for m in [m async for m in sup.history(limit=20)]
    ):
        await sup.send(
            embed=discord.Embed(
                title="🎫 FAMILY SUPPORT",
                description=(
                    "Need help with registration, membership, your family role, "
                    "roster, or another family-related matter? Open a private ticket."
                ),
                color=ACCENT,
            ),
            view=SupportPanel(),
        )

    med = channel(g, "family-mediation")
    if med and not any(
        m.author == bot.user for m in [m async for m in med.history(limit=20)]
    ):
        await med.send(
            embed=discord.Embed(
                title="🤝 FAMILY MEDIATION",
                description=(
                    "Having an issue between families or inside your family? "
                    "Request a neutral Family Mediator. Both sides can be heard "
                    "before the issue is escalated to Council or Management."
                ),
                color=ACCENT,
            ),
            view=MediationPanel(),
        )

    rules = channel(g, "family-rules")
    if rules and not any(
        m.author == bot.user for m in [m async for m in rules.history(limit=10)]
    ):
        await rules.send(
            "📜 **CHIRAQ CHRONICLES FAMILY RULES**\n\n"
            "• Follow all Chiraq Chronicles city and community rules.\n"
            "• Family RP should create storylines, not unnecessary OOC conflict.\n"
            "• Keep IC and OOC issues separate; no metagaming through family channels.\n"
            "• Respect Family Management, Council, Mediators, and other families.\n"
            "• The Head of Family is responsible for keeping their roster accurate.\n"
            "• Family Mediators may assist with disputes before staff escalation.\n"
            "• Approved families receive official recognition; their private family "
            "channels are created manually by management.\n"
            "• Serious or repeated issues may result in strikes, suspension, or removal "
            "of official family status."
        )

    await refresh_directory(g)
    await refresh_leaderboard(g)

    await i.followup.send(
        "✅ Chiraq Chronicles Family Hub is ready. "
        "Family Management, Family Council, Family Mediators, Head of Family and "
        "Family Member roles were included. Closed family categories remain manual.",
        ephemeral=True,
    )


@bot.tree.command(
    name="approve_family",
    description="Management/Council: approve a family and create its official role"
)
@app_commands.guild_only()
async def approve_family(
    i,
    family_name: str,
    head: discord.Member,
    color: str = "Not listed",
    motto: str = "Not listed",
    coleader: str = "Not listed",
    recruiting: str = "Ask Head of Family",
):
    if not is_council(i.user):
        return await i.response.send_message(
            "Family Management or Family Council required.", ephemeral=True
        )

    await i.response.defer(ephemeral=True)
    async with locks.setdefault(("family", i.guild.id, family_name.lower()), asyncio.Lock()):
        with database() as db:
            existing = db.execute(
                "SELECT status FROM families WHERE guild=? AND lower(name)=lower(?)",
                (i.guild.id, family_name),
            ).fetchone()
            if existing and existing[0] == "approved":
                return await i.followup.send(
                    "That family is already approved.", ephemeral=True
                )

        family_role = discord.utils.get(i.guild.roles, name=family_name)
        if not family_role:
            family_role = await i.guild.create_role(
                name=family_name,
                color=discord.Color(ACCENT),
                permissions=discord.Permissions.none(),
                reason="Approved Chiraq family",
            )

        hof = role(i.guild, "Head of Family")
        member_role = role(i.guild, "Family Member")
        add = [r for r in (family_role, hof, member_role) if r]
        if add:
            await head.add_roles(*add, reason="Approved Head of Family")

        with database() as db:
            db.execute(
                """INSERT OR REPLACE INTO families
                   (guild,name,leader,coleader,color,motto,recruiting,status,role_id,created)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    i.guild.id, family_name, head.id, coleader, color,
                    motto, recruiting, "approved", family_role.id, time.time()
                ),
            )
            db.execute(
                "INSERT OR IGNORE INTO family_members VALUES (?,?,?)",
                (i.guild.id, family_name, head.id),
            )
            db.execute(
                "INSERT OR IGNORE INTO family_points VALUES (?,?,0)",
                (i.guild.id, family_name),
            )

        await refresh_directory(i.guild)
        await refresh_leaderboard(i.guild)

        leaders = channel(i.guild, "family-leaders")
        if leaders:
            await leaders.send(
                f"✅ **{family_name}** approved.\n"
                f"💎 Head of Family: {head.mention}\n"
                f"👥 Co-Head/Second: {coleader}\n"
                f"🎨 Color: {color}\n"
                f"📣 Recruiting: {recruiting}"
            )

        await i.followup.send(
            f"✅ **{family_name}** approved. The official family role was created "
            f"and {head.mention} received Head of Family + Family Member.\n"
            f"Create their closed family category/channels manually when ready.",
            ephemeral=True,
        )


@bot.tree.command(
    name="add_family_member",
    description="Head/Council/Management: add a member to an approved family"
)
@app_commands.guild_only()
async def add_family_member(i, member: discord.Member, family_name: str):
    with database() as db:
        fam = db.execute(
            "SELECT leader,role_id FROM families WHERE guild=? AND lower(name)=lower(?) AND status='approved'",
            (i.guild.id, family_name),
        ).fetchone()

    if not fam:
        return await i.response.send_message("Approved family not found.", ephemeral=True)

    if not (is_council(i.user) or i.user.id == fam[0]):
        return await i.response.send_message(
            "Only that Head of Family, Council, or Management can add members.",
            ephemeral=True,
        )

    fr = i.guild.get_role(fam[1])
    general = role(i.guild, "Family Member")
    add = [r for r in (fr, general) if r]
    if add:
        await member.add_roles(*add, reason=f"Added to {family_name}")

    with database() as db:
        db.execute(
            "INSERT OR IGNORE INTO family_members VALUES (?,?,?)",
            (i.guild.id, family_name, member.id),
        )

    await i.response.send_message(
        f"✅ {member.mention} added to **{family_name}**.", ephemeral=True
    )


@bot.tree.command(
    name="remove_family_member",
    description="Head/Council/Management: remove a member from an approved family"
)
@app_commands.guild_only()
async def remove_family_member(i, member: discord.Member, family_name: str):
    with database() as db:
        fam = db.execute(
            "SELECT leader,role_id FROM families WHERE guild=? AND lower(name)=lower(?) AND status='approved'",
            (i.guild.id, family_name),
        ).fetchone()

    if not fam:
        return await i.response.send_message("Approved family not found.", ephemeral=True)

    if not (is_council(i.user) or i.user.id == fam[0]):
        return await i.response.send_message(
            "Only that Head of Family, Council, or Management can remove members.",
            ephemeral=True,
        )

    if member.id == fam[0] and not is_council(i.user):
        return await i.response.send_message(
            "A Head of Family cannot remove themselves. Contact Council/Management.",
            ephemeral=True,
        )

    fr = i.guild.get_role(fam[1])
    general = role(i.guild, "Family Member")
    remove = [r for r in (fr, general) if r]
    if remove:
        await member.remove_roles(*remove, reason=f"Removed from {family_name}")

    with database() as db:
        db.execute(
            "DELETE FROM family_members WHERE guild=? AND lower(family)=lower(?) AND member=?",
            (i.guild.id, family_name, member.id),
        )

    await i.response.send_message(
        f"✅ {member.mention} removed from **{family_name}**.", ephemeral=True
    )


@bot.tree.command(
    name="family_roster",
    description="View the registered roster for an approved family"
)
@app_commands.guild_only()
async def family_roster(i, family_name: str):
    with database() as db:
        fam = db.execute(
            "SELECT name,leader FROM families WHERE guild=? AND lower(name)=lower(?) AND status='approved'",
            (i.guild.id, family_name),
        ).fetchone()
        if not fam:
            return await i.response.send_message("Approved family not found.", ephemeral=True)
        members = db.execute(
            "SELECT member FROM family_members WHERE guild=? AND lower(family)=lower(?)",
            (i.guild.id, family_name),
        ).fetchall()

    lines = [f"• <@{uid}>" for (uid,) in members] or ["No members recorded."]
    await i.response.send_message(
        embed=discord.Embed(
            title=f"👥 {fam[0]} ROSTER",
            description=f"💎 Head of Family: <@{fam[1]}>\n\n" + "\n".join(lines),
            color=ACCENT,
        ),
        ephemeral=True,
    )


@bot.tree.command(
    name="family_points",
    description="Council/Management: add or remove family leaderboard points"
)
@app_commands.guild_only()
async def family_points(i, family_name: str, points: int, reason: str):
    if not is_council(i.user):
        return await i.response.send_message(
            "Family Council or Management required.", ephemeral=True
        )
    if abs(points) > 10000:
        return await i.response.send_message(
            "Use a point adjustment between -10000 and 10000.", ephemeral=True
        )

    with database() as db:
        fam = db.execute(
            "SELECT name FROM families WHERE guild=? AND lower(name)=lower(?) AND status='approved'",
            (i.guild.id, family_name),
        ).fetchone()
        if not fam:
            return await i.response.send_message("Approved family not found.", ephemeral=True)

        db.execute(
            "INSERT OR IGNORE INTO family_points VALUES (?,?,0)",
            (i.guild.id, fam[0]),
        )
        db.execute(
            "UPDATE family_points SET points=points+? WHERE guild=? AND family=?",
            (points, i.guild.id, fam[0]),
        )
        total = db.execute(
            "SELECT points FROM family_points WHERE guild=? AND family=?",
            (i.guild.id, fam[0]),
        ).fetchone()[0]

    await refresh_leaderboard(i.guild)
    await i.response.send_message(
        f"🏆 **{fam[0]}** adjusted by {points:+} points. New total: **{total}**.\nReason: {reason}",
        ephemeral=True,
    )


@bot.tree.command(
    name="family_strike",
    description="Management: record an official family strike"
)
@app_commands.guild_only()
async def family_strike(i, family_name: str, reason: str):
    if not is_management(i.user):
        return await i.response.send_message(
            "Only Family Management can issue official strikes.", ephemeral=True
        )

    with database() as db:
        fam = db.execute(
            "SELECT name FROM families WHERE guild=? AND lower(name)=lower(?) AND status='approved'",
            (i.guild.id, family_name),
        ).fetchone()
        if not fam:
            return await i.response.send_message("Approved family not found.", ephemeral=True)
        db.execute(
            "INSERT INTO family_strikes VALUES (?,?,?,?,?)",
            (i.guild.id, fam[0], reason, i.user.id, time.time()),
        )
        count = db.execute(
            "SELECT COUNT(*) FROM family_strikes WHERE guild=? AND family=?",
            (i.guild.id, fam[0]),
        ).fetchone()[0]

    log = channel(i.guild, "family-strikes")
    if log:
        await log.send(
            f"⚠️ **OFFICIAL FAMILY STRIKE**\nFamily: **{fam[0]}**\n"
            f"Strike #: **{count}**\nIssued by: {i.user.mention}\nReason: {reason}"
        )
    await i.response.send_message(
        f"⚠️ Strike #{count} recorded for **{fam[0]}**.", ephemeral=True
    )


@bot.tree.command(
    name="disband_family",
    description="Management: remove official status from a family"
)
@app_commands.guild_only()
async def disband_family(i, family_name: str, reason: str):
    if not is_management(i.user):
        return await i.response.send_message(
            "Only Family Management can disband an official family.", ephemeral=True
        )

    await i.response.defer(ephemeral=True)
    with database() as db:
        fam = db.execute(
            "SELECT name,leader,role_id FROM families WHERE guild=? AND lower(name)=lower(?) AND status='approved'",
            (i.guild.id, family_name),
        ).fetchone()
        if not fam:
            return await i.followup.send("Approved family not found.", ephemeral=True)
        db.execute(
            "UPDATE families SET status='disbanded' WHERE guild=? AND name=?",
            (i.guild.id, fam[0]),
        )
        member_ids = [
            r[0] for r in db.execute(
                "SELECT member FROM family_members WHERE guild=? AND family=?",
                (i.guild.id, fam[0]),
            ).fetchall()
        ]

    fr = i.guild.get_role(fam[2])
    general = role(i.guild, "Family Member")
    hof = role(i.guild, "Head of Family")

    for uid in member_ids:
        member = i.guild.get_member(uid)
        if not member:
            continue
        remove = [r for r in (fr, general) if r and r in member.roles]
        if uid == fam[1] and hof and hof in member.roles:
            remove.append(hof)
        if remove:
            await member.remove_roles(*remove, reason=f"Family disbanded: {reason}")

    await refresh_directory(i.guild)
    await refresh_leaderboard(i.guild)

    records = channel(i.guild, "family-records")
    if records:
        await records.send(
            f"🛑 **{fam[0]}** official family status removed by {i.user.mention}.\n"
            f"Reason: {reason}\n"
            f"The family role was left in the server for manual review/deletion."
        )

    await i.followup.send(
        f"🛑 **{fam[0]}** marked disbanded. Member access roles were removed. "
        f"I left the actual family role/private channels in place so you can review/delete them manually.",
        ephemeral=True,
    )


@bot.event
async def on_ready():
    print(f"Chiraq Chronicles Family Bot online as {bot.user}. Run /setup_family.")


@bot.tree.error
async def on_app_command_error(i, e):
    msg = "Action failed. Check bot permissions and role hierarchy, then try again."
    if isinstance(e, app_commands.MissingPermissions):
        msg = "Only server administrators can use that command."
    if i.response.is_done():
        await i.followup.send(msg, ephemeral=True)
    else:
        await i.response.send_message(msg, ephemeral=True)


if __name__ == "__main__":
    bot.run(TOKEN)
