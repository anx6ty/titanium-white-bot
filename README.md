# Titanium White Bot

A production-ready, modular Discord bot built with discord.py 2.x, MongoDB-ready storage, Lavalink support, and a dashboard starter.

## Overview

This project is a clean migration from the original lightweight greeting bot into a scalable architecture for:
- moderation and security automation
- ticketing, leveling, giveaways, automod
- temporary VC and music playback
- dashboard OAuth2 + configuration UI
- MongoDB document storage

## Setup

1. Copy `.env.example` to `.env` and fill in your real values.
2. Install dependencies:
   `python -m pip install -r requirements.txt`
3. Start Lavalink (see `lavalink/application.yml`), then run:
   `python bot.py`

## Environment

See `.env.example` for required settings.

## Project layout

- `app/` core bot architecture
- `dashboard/` FastAPI dashboard starter
- `lavalink/` Lavalink config example
- `scripts/` migration and maintenance helpers

## Notes

- This project deliberately avoids registering 800 top-level slash commands; it uses command groups and a dynamic action registry to remain within Discord limits while supporting many actions in modular form.
- The bot uses discord.py 2.x and MongoDB-style models while emphasizing a clean, maintainable structure.
