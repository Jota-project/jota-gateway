# OpenClaw — Channels Reference

## Available Channels

| Channel | Auth | Notes |
|---------|------|-------|
| **WebChat** | None | Built-in at `:18789/`, no setup needed |
| **Telegram** | Bot token | @BotFather |
| **WhatsApp** | QR scan | One session per host |
| **Discord** | Bot token | Create bot, invite to server |
| **Signal** | Device link | Requires signal-cli |
| **iMessage** | macOS only | Via BlueBubbles or legacy method |

## Quick Setup

### Telegram
```jsonc
{
  "channels": {
    "telegram": {
      "botToken": "<from @BotFather>",
      "allowFrom": ["@your_username"]
    }
  }
}
```

### WhatsApp
```bash
openclaw onboard   # follow QR scan
```
One WhatsApp session per host — no duplicate processes.

### Discord
```jsonc
{
  "channels": {
    "discord": {
      "token": "<bot token>",
      "allowFrom": ["<user_id>"],
      "guildId": "<optional: restrict to guild>"
    }
  }
}
```

### Group chats
```jsonc
{
  "messages": {
    "groupChat": {
      "mentionPatterns": ["@openclaw", "@jota"]
    }
  }
}
```

## Security: allowFrom

Always configure `allowFrom` to restrict who can message your agent.
Without it, anyone who can reach the bot can interact with it.

```jsonc
"whatsapp": {
  "allowFrom": ["+34600000000", "+34600000001"]
}
```
