# Eggplant Hunt

A small multiplayer Telegram game built with Python, aiogram and aiohttp.
Players choose hidden boxes on a 3×3 board. The bot supports a shared group
game and a three-round duel with automatic scoring.

The user interface is in Russian. Documentation is in English.

## Demo

[Try the bot on Telegram](https://t.me/FindEggplant_bot)

![Eggplant Hunt gameplay](game-demo.png)

## How to play

- `/start` or `/help`: show instructions.
- `/game`: create a board. Each participant gets one pick. Different players
  may choose the same box. Picks are reported in the chat; boxes stay hidden
  until a participant presses the end-game button. Any participant can end it.
- `/duel`: two players join and each picks one box per round. The board is
  revealed for five seconds before the next round starts. An eggplant earns
  1 point, a golden banana 5, and an empty box 0. Highest total after three
  rounds wins; equal scores produce a draw.

Boards contain nine cells, at most one golden banana, and more empty cells
than eggplants. Gold probability is sampled uniformly between 0% and 5%
per board, giving an average probability of 2.5%.

## Local setup

Requires Python 3.10 or newer and a Telegram bot token.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Fill in `BOT_TOKEN` in your local `.env`, leave `WEBHOOK_URL` empty, then run:

```sh
set -a
source .env
set +a
python bot.py
```

The application reads environment variables; it does not automatically load
`.env`. Keep `.env` local. Polling requires no public web server. Run only one
bot instance per token. Starting polling removes the existing webhook.

## Webhook deployment

For a service such as Render:

- Build command: `pip install -r requirements.txt`
- Start command: `python bot.py`
- Set `BOT_TOKEN`, `WEBHOOK_URL=https://YOUR-HOST/webhook` and `WEBHOOK_SECRET`
  in the service environment. Choose a random secret containing letters,
  digits, underscores or hyphens. It must be 1–256 characters long.
- The application binds to `0.0.0.0` using `PORT` (default: `10000`).
- `/` and `/health` return `{"status": "ok"}` while the web server is running.

Telegram requests are authenticated using the webhook secret header. The
health endpoint checks the web server, not Telegram connectivity.

## Code structure

```text
bot.py              Telegram handlers, keyboards, session cleanup, startup
game.py             Board generation, move validation, duel scoring
tests/              Offline game and handler tests
.env.example        Environment variable template without credentials
requirements.txt    Direct application dependencies
```

Game rules do not import aiogram or require a bot token. Handlers serialize
updates per message to avoid overlapping edits and scoring. Duel callbacks
include the round number so delayed clicks cannot become next-round picks.

## Tests

```sh
python -m unittest discover -s tests -v
```

Tests run offline without contacting Telegram. They cover board invariants,
invalid and duplicate moves, stale callbacks, scoring and three-round duels.

## Limitations

- State is held in memory and lost on restart. Run a single process/replica.
- Sessions expire after one hour of inactivity; old buttons then ask players
  to start a new game. Completed sessions are removed immediately.
- There is no persistent leaderboard, database or account system.
- Chat logs display recent results to stay within Telegram message limits.

The original local virtual environment and unused SQLite database are not
application dependencies and must not be included in the published source.
