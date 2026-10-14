"""Telegram handlers and polling/webhook entry point."""

import asyncio
import logging
import os
import time
from collections import defaultdict
from contextlib import suppress

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from game import DUEL_ROUNDS, EMOJI, LABELS, POINTS, DuelState, GameState

logger = logging.getLogger(__name__)
dp = Dispatcher()
games = {}
duels = {}
updated = {}
locks = defaultdict(asyncio.Lock)
SESSION_TTL = 3600
DUEL_DELAY_SECONDS = 5


async def answer(callback, text=None):
    try:
        await callback.answer(text)
    except TelegramBadRequest as error:
        if "query is too old" not in error.message.lower() and "query id is invalid" not in error.message.lower():
            raise


def keyboard(board, reveal=False, round_number=None):
    rows = []
    for row in range(3):
        buttons = []
        for index in range(row * 3, row * 3 + 3):
            data = f"open:{index}" if round_number is None else f"duel_open:{round_number}:{index}"
            buttons.append(InlineKeyboardButton(
                text=EMOJI[board[index]] if reveal else "📦",
                callback_data="noop" if reveal else data,
            ))
        rows.append(buttons)
    if round_number is None and not reveal:
        rows.append([InlineKeyboardButton(text="⛔️ Окончить игру", callback_data="end")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ready_keyboard(count):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"⚔️ Готов ({count}/2)", callback_data="duel_ready")
    ]])


def scoreboard(duel):
    return "  |  ".join(f"{name}: {duel.scores[uid]}" for uid, name in duel.players.items())


def game_text(game):
    # Retain recent results to stay within Telegram's message length limit.
    lines = []
    length = 10
    for line in reversed(game.log):
        line_length = len(line.encode("utf-16-le")) // 2
        if length + line_length + 1 > 3900:
            break
        lines.append(line)
        length += line_length + 1
    return "найди 🍆" + ("\n\n" + "\n".join(reversed(lines)) if lines else "")


def forget(key):
    games.pop(key, None)
    duels.pop(key, None)
    updated.pop(key, None)


async def cleanup_sessions():
    while True:
        await asyncio.sleep(60)
        for key in list(updated):
            if not locks[key].locked() and time.monotonic() - updated[key] > SESSION_TTL:
                forget(key)
        for key in list(locks):
            if key not in updated and not locks[key].locked():
                locks.pop(key, None)


@dp.message(Command("start", "help"))
async def start_handler(message: Message):
    await message.answer(
        "Привет! Я игровой бот 🍆\nДобавь меня в группу или играй здесь.\n\n"
        "/game — каждый выбирает одну коробку; поле раскрывается в конце\n"
        "/duel — дуэль на 3 раунда: 🍆 = 1, 🍌 = 5, 💨 = 0 очков"
    )


@dp.message(F.new_chat_members)
async def bot_added_to_group(message: Message, bot: Bot):
    if any(member.id == bot.id for member in message.new_chat_members):
        await message.answer("🍆 Спасибо, что добавили меня!\n/game — игра\n/duel — дуэль 1 на 1")


@dp.message(Command("game"))
async def game_handler(message: Message):
    game = GameState()
    msg = await message.answer("найди 🍆", reply_markup=keyboard(game.board))
    key = (msg.chat.id, msg.message_id)
    games[key] = game
    updated[key] = time.monotonic()


@dp.message(Command("duel"))
async def duel_handler(message: Message):
    msg = await message.answer("Готовы сразиться в дуэли?", reply_markup=ready_keyboard(0))
    key = (msg.chat.id, msg.message_id)
    duels[key] = DuelState()
    updated[key] = time.monotonic()


@dp.callback_query(F.data != "noop")
async def on_callback(callback: CallbackQuery, bot: Bot):
    if not isinstance(callback.message, Message):
        await answer(callback, "Сообщение недоступно")
        return
    key = (callback.message.chat.id, callback.message.message_id)
    # Serialize updates per message, including the duel reveal delay.
    async with locks[key]:
        try:
            await process_callback(callback, bot, key)
        except ValueError as error:
            await answer(callback, str(error))
        except TelegramBadRequest:
            logger.exception("Could not update game message")
            forget(key)
            await answer(callback, "Не удалось обновить игру. Запусти /game или /duel")


async def process_callback(callback, bot, key):
    data = callback.data or ""
    uid = callback.from_user.id
    name = callback.from_user.full_name[:128]
    chat_id, message_id = key
    if data.startswith("open:") or data == "end":
        game = games.get(key)
        if game is None:
            raise ValueError("Игра завершена или бот перезапущен. Запусти /game")
        if data == "end":
            game.log.append(f"Игра окончена — {name}")
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                text=game_text(game), reply_markup=keyboard(game.board, reveal=True))
            forget(key)
            await answer(callback, "Игра завершена")
            return
        try:
            index = int(data.split(":")[1])
        except (ValueError, IndexError):
            raise ValueError("Некорректный ход")
        cell = game.pick(uid, index)
        if cell == "empty":
            game.log.append(f"{name} открыл пустую коробку {EMOJI[cell]}")
        else:
            game.log.append(f"{name} нашёл {LABELS[cell]} {EMOJI[cell]}")
        updated[key] = time.monotonic()
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
            text=game_text(game), reply_markup=keyboard(game.board))
        await answer(callback, "Принято")
    elif data == "duel_ready" or data.startswith("duel_open:"):
        duel = duels.get(key)
        if duel is None:
            raise ValueError("Дуэль завершена или бот перезапущен. Запусти /duel")
        if data == "duel_ready":
            duel.join(uid, name)
            updated[key] = time.monotonic()
            if len(duel.players) == 1:
                await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id,
                    reply_markup=ready_keyboard(1))
                await answer(callback, "Ждём второго игрока")
                return
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                text=f"Раунд 1/{DUEL_ROUNDS}\n{scoreboard(duel)}",
                reply_markup=keyboard(duel.board, round_number=1))
            await answer(callback, "Дуэль началась")
            return
        try:
            _, round_text, index_text = data.split(":")
            round_number, index = int(round_text), int(index_text)
        except ValueError:
            raise ValueError("Некорректный ход")
        complete = duel.pick(uid, index, round_number)
        updated[key] = time.monotonic()
        await answer(callback, "Выбор принят")
        if not complete:
            return
        lines = [f"Раунд {duel.round_number}/{DUEL_ROUNDS}"]
        for player, player_name in duel.players.items():
            cell = duel.board[duel.picks[player]]
            if cell == "empty":
                lines.append(f"{player_name} открыл пустую коробку {EMOJI[cell]} (+0)")
            else:
                lines.append(
                    f"{player_name} нашёл {LABELS[cell]} {EMOJI[cell]} (+{POINTS[cell]})"
                )
        lines.append(scoreboard(duel))
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
            text="\n".join(lines), reply_markup=keyboard(duel.board, True, duel.round_number))
        await asyncio.sleep(DUEL_DELAY_SECONDS)
        if duel.next_round():
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                text=f"Раунд {duel.round_number}/{DUEL_ROUNDS}\n{scoreboard(duel)}",
                reply_markup=keyboard(duel.board, round_number=duel.round_number))
        else:
            winner = duel.winner()
            result = "Ничья" if winner is None else f"Победил {duel.players[winner]}"
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                text=f"Дуэль завершена!\n{scoreboard(duel)}\n{result}", reply_markup=None)
            forget(key)
    else:
        await answer(callback, "Кнопка не распознана")


@dp.callback_query(F.data == "noop")
async def on_noop(callback: CallbackQuery):
    await answer(callback)


async def main():
    logging.basicConfig(level=logging.INFO)
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise RuntimeError("Set BOT_TOKEN in the environment before starting the bot")
    webhook_url = os.environ.get("WEBHOOK_URL")
    runner = None
    cleanup = asyncio.create_task(cleanup_sessions())
    async with Bot(token=token) as bot:
        try:
            if not webhook_url:
                await bot.delete_webhook(drop_pending_updates=False)
                await dp.start_polling(bot, close_bot_session=False)
                return
            if not webhook_url.startswith("https://") or not webhook_url.endswith("/webhook"):
                raise RuntimeError("WEBHOOK_URL must be an HTTPS URL ending with /webhook")
            secret = os.environ.get("WEBHOOK_SECRET")
            if not secret:
                raise RuntimeError("Set WEBHOOK_SECRET when using webhook mode")
            app = web.Application()
            SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=secret).register(app, path="/webhook")
            setup_application(app, dp, bot=bot)

            async def health(request):
                return web.json_response({"status": "ok"})

            app.router.add_get("/", health)
            app.router.add_get("/health", health)
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", "10000"))).start()
            await bot.set_webhook(webhook_url, secret_token=secret,
                allowed_updates=dp.resolve_used_update_types())
            logger.info("Webhook server started")
            await asyncio.Event().wait()
        finally:
            cleanup.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup
            if runner is not None:
                await runner.cleanup()


if __name__ == "__main__":
    with suppress(KeyboardInterrupt):
        asyncio.run(main())
