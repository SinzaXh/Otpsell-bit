# Telegram OTP Shop Bot

## Overview
This is a Telegram bot that sells OTP (One-Time Password) accounts for Telegram. The bot allows administrators to upload accounts using Telethon for real sign-in and provides an automated shop interface for users to purchase accounts from different countries.

**Status**: ✅ Running successfully in Replit environment

## Recent Changes
- **2024-11-15**: Initial project import and setup
  - ✅ **SECURITY FIX**: Removed all hardcoded credentials from code - BOT_TOKEN, API_ID, and API_HASH are now required via Replit Secrets
  - Fixed dependency conflicts (removed dummy `telegram` package)
  - Set up workflow for bot execution
  - Configured deployment settings for VM deployment
  - Created .gitignore for sessions, database, and sensitive files
  - Bot is now running securely with environment-provided credentials only

## Project Architecture

### Technology Stack
- **Language**: Python 3.11
- **Bot Framework**: python-telegram-bot (v22.5)
- **Telethon**: For real Telegram account sign-in
- **Database**: SQLite with aiosqlite
- **Scheduler**: APScheduler for background tasks
- **Other**: aiohttp for async HTTP requests

### File Structure
```
.
├── main.py                 # Main bot application
├── requirements.txt        # Python dependencies
├── shop.db                 # SQLite database (auto-created)
├── sessions/              # Telethon session files (auto-created)
├── .gitignore             # Git ignore rules
└── replit.md              # This file
```

### Database Schema
- **users**: User accounts with balance tracking
- **bans**: Banned users list
- **accounts**: Available Telegram accounts inventory
- **transactions**: Transaction history
- **settings**: Bot configuration settings

## Configuration

### Environment Variables (Secrets)
The following secrets should be configured in the Replit Secrets pane:

**Required:**
- `BOT_TOKEN`: Your Telegram bot token from @BotFather
- `API_ID`: Telegram API ID from my.telegram.org
- `API_HASH`: Telegram API hash from my.telegram.org

**Optional (with defaults):**
- `TWO_FA_PASSWORD`: 2FA password for accounts if needed
- `ADMIN_IDS`: Comma-separated admin user IDs (default: 8251818467,6936153954)
- `FORCE_JOIN_USERNAME`: Channel username users must join (default: @abouttechyrajput)
- `FORCE_JOIN_CHAT_ID`: Channel chat ID (default: -1002731834108)
- `OWNER_HANDLE`: Owner's Telegram handle (default: choudhary_ji600)
- `DEVELOPER_CREDITS`: Developer credits text
- `RESERVE_MINUTES`: Minutes to reserve account (default: 10)
- `DATABASE_PATH`: Database file path (default: shop.db)
- `SESSION_DIR`: Session files directory (default: sessions)

### Security Notes on Configuration
- ✅ **BOT_TOKEN**, **API_ID**, and **API_HASH** are **REQUIRED** - the bot will not start without them
- The bot will display a clear error message if any required credentials are missing
- All sensitive credentials must be stored in Replit Secrets (never in code)
- Non-sensitive settings have reasonable defaults but can be overridden via environment variables

## Features

### User Features
- 🛒 Buy Telegram accounts from multiple countries
- 💰 Balance checking
- 📱 Automatic OTP forwarding
- 🔒 Forced channel join verification

### Admin Features
- 📥 Upload accounts interactively via Telethon
- 📊 View statistics (users, accounts, revenue)
- 📇 Account management
- 💳 Balance management (add/deduct coins)
- 📣 Broadcast messages to all users
- 🚫 Ban/unban users

### Available Countries & Prices
- 🇺🇸 US - ₹40
- 🇪🇹 Ethiopia - ₹35
- 🇻🇳 Vietnam - ₹35
- 🇮🇳 India - ₹40
- 🇳🇵 Nepal - ₹40
- 🇸🇻 El Salvador - ₹55
- 🇵🇭 Philippines - ₹80

## Running the Bot

### In Development (Replit)
The bot is already configured with a workflow named `telegram-bot` that runs automatically:
```bash
python main.py
```

### Deployment
This bot is configured for **VM deployment** on Replit, which means:
- ✅ Always running (not paused between requests)
- ✅ Maintains in-memory state
- ✅ Suitable for long-running Telegram bots
- ✅ Can handle background tasks (APScheduler)

To deploy, use the Replit "Deploy" button.

## Admin Commands
- `/start` - Start the bot and show main menu
- `/upload` - Upload a new account (interactive)
- `/stats` - View bot statistics
- `/accounts` - View and manage accounts
- `/balance <user> <amount>` - View/set user balance
- `/broadcast <message>` - Send message to all users
- `/ban <user>` - Ban a user
- `/unban <user>` - Unban a user
- `/addcoins <user> <amount>` - Add coins to user
- `/deductcoin <user> <amount>` - Deduct coins from user

## Security Notes
- ✅ **Security Hardened**: All hardcoded API credentials have been removed. BOT_TOKEN, API_ID, and API_HASH are now required via Replit Secrets.
- ✅ **Logging Secured**: Configured httpx and telegram loggers to WARNING level to prevent API tokens from appearing in logs
- 🔒 Session files are stored locally in the `sessions/` directory (gitignored)
- 🗄️ SQLite database contains user data and transactions (gitignored)
- 🚫 All sensitive files (`sessions/`, `shop.db`, `.env`) are excluded from git

**Important**: If you need to rotate credentials:
1. Generate new credentials from @BotFather and my.telegram.org
2. Update them in Replit Secrets
3. Restart the bot workflow

## User Preferences
None specified yet.

## Troubleshooting

### Bot Not Starting
1. Check that `BOT_TOKEN` is set correctly
2. Verify the token is valid using @BotFather
3. Check workflow logs for detailed errors

### Import Errors
- The dummy `telegram` package was removed - only `python-telegram-bot` is needed
- All dependencies should auto-install from `requirements.txt`

### Database Issues
- The database is auto-created on first run
- If corrupted, delete `shop.db` and restart (⚠️ this will delete all data)

## Additional Notes
- The bot uses IST (Asia/Kolkata) timezone by default
- Reservation system expires accounts after 10 minutes if not completed
- APScheduler runs cleanup tasks every minute
- HTTP timeouts are configured for reliability with Telegram API
