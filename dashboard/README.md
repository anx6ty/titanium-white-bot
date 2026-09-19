# Titanium White Dashboard

A clean, white, responsive Next.js 14 dashboard starter for the all-in-one Discord bot.

## Run locally

```bash
cd dashboard
cp .env.example .env.local
npm install
npm run dev
```

Open http://localhost:3000.

## Next integrations

- Add Discord OAuth2 login and guild selection.
- Connect the dashboard to the bot API using `NEXT_PUBLIC_API_URL`.
- Replace the sample overview values with authenticated server data.
- Add protected settings routes for security, leveling, audio, tickets, and logging.

The current page is intentionally API-independent so the visual shell can be reviewed before authentication and database services are connected.
