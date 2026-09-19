# Titanium White command reference

The bot uses grouped slash commands. After restarting and syncing, Discord exposes:

- `/help`, `/ping`, `/uptime`
- `/setgreetvoice`
- `/config panel`, `/config toggle`, `/config leavelogging`, `/config setup_leave`, `/config whitelist`
- `/mod warn`, `/mod timeout`, `/mod kick`, `/mod ban`
- `/level rank`
- `/ticket`, `/rank`, `/serverinfo`, `/userinfo`
- `/manage <category> <action> [member] [reason]`

`/manage` contains 20 categories with 16 actions each (320 nested moderation/administration actions), including security, channels, messages, members, roles, automod, raid, nuke, logging, invites, voice, tickets, giveaways, utility, welcome, leveling, music, config, reports, and staff.

Examples:

```text
/manage security warn @User reason
/manage security timeout @User reason
/manage channels lock
/manage messages clear
/manage automod enable
/manage raid lockdown
/manage tickets create
/manage giveaways reroll
/manage music queue
/manage utility poll
/manage welcome test
/manage leveling leaderboard
```

The generated actions intentionally share a simple interface so they are easy to discover and extend. Destructive actions still require the relevant Discord permissions and bot role hierarchy.
