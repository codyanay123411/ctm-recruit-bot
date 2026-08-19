# CTM Recruit Bot

A small Discord bot for the **Chief of Training & Membership (CTM)** office.

The goal is deliberately simple: keep recruit information in one place, make report cards instantly retrievable, and put the report card directly beside the membership vote.

## Version 1 commands

- `/recruit create @member` — create a recruit record.
- `/recruit update @member ...` — update activity, completed training, strengths, weaknesses, and warnings/issues.
- `/recruit score @member ...` — score Conduct, Communication, Teamwork, Knowledge, and Performance from 1–5 (25 points total).
- `/recruit view @member` — instantly pull up a recruit report card.
- `/recruit finalize @member` — record CTM's final recommendation.
- `/recruit vote @member` — post the report card with **Accept / Deny / Abstain** buttons.
- `/recruit closevote @member` — close the active vote and display the final tally.

A member may change their vote by clicking a different button. The recruit cannot vote on their own membership.

## Setup

### 1. Create the Discord app

In the Discord Developer Portal, create an application and add a bot user. Keep the bot token secret.

When installing the app in the server, include the `bot` and `applications.commands` scopes. The bot needs permission to send messages, embed links, read message history, and use application commands in the channel where CTM operates.

### 2. Install Python

Use Python 3.11 or newer.

```bash
python -m venv .venv
```

Windows:

```bash
.venv\Scripts\activate
```

Then install dependencies:

```bash
pip install -r requirements.txt
```

### 3. Configure the bot

Copy `.env.example` to `.env` and fill in the values.

```env
DISCORD_TOKEN=your_secret_bot_token
DISCORD_GUILD_ID=your_server_id
CTM_USER_ID=your_discord_user_id
CTM_ROLE_ID=
DATABASE_PATH=ctm_recruits.db
```

`DISCORD_GUILD_ID` is recommended while developing because commands sync to that server immediately.

For CTM write access, set either `CTM_USER_ID`, `CTM_ROLE_ID`, or both. Members with Discord's **Manage Server** permission are also permitted to manage CTM records.

### 4. Run it

```bash
python bot.py
```

The SQLite database is created automatically. It is intentionally ignored by Git so recruit records and bot secrets are not committed to the repository.

## What is stored

Each recruit record stores:

- Discord member identity
- Active days and activity hours
- Training completed
- Conduct / Communication / Teamwork / Knowledge / Performance scores
- Strengths
- Weaknesses
- Warnings / issues
- CTM recommendation and notes
- Membership vote ballots and tally

## Security

**Never commit the Discord bot token.** `.env` and the SQLite database are ignored by `.gitignore`.

This first version intentionally avoids dashboards, websites, Roblox integration, automatic attendance, or other infrastructure. It is meant to remove Discord-post digging without becoming another project to manage.