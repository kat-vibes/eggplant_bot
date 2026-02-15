import asyncio
import logging
import os
import random
from dataclasses import dataclass, field
from typing import Dict, List, Tuple


from aiohttp import web
from aiogram.exceptions import TelegramBadRequest
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application



# --- Safe callback answer helper ---
async def safe_callback_answer(callback: CallbackQuery, text: str | None = None, show_alert: bool = False):
    """Безопасно отвечаем на callback: Telegram может ругаться, если кнопка слишком старая."""
    try:
        if text is None:
            await callback.answer()
        else:
            await callback.answer(text, show_alert=show_alert)
    except TelegramBadRequest:
        # query is too old / invalid — игнорируем
        pass

# -------------------- Конфиг --------------------
BOX = "📦"

# То, что показываем внутри открытых коробок
EGGPLANT_EMOJI = "🍆"
GOLD_BANANA_EMOJI = "🍌"
AIR_EMOJI = "💨"

# Тексты логов (как ты просила)
TXT_GOLD = "ЗОЛОТОООЙ БАНАН"
TXT_EGGPLANT = "баклажан"
TXT_EMPTY = "открыл пустую коробку"

# Вероятности (можно менять)
# Золотой банан: 0%..5% (динамически для каждой новой игры)
GOLD_BANANA_CHANCE_MIN = 0.00
GOLD_BANANA_CHANCE_MAX = 0.05
# Баклажан: 30%..50% (мы будем случайно выбирать цель в этом диапазоне)
EGGPLANT_RATE_MIN = 0.30
EGGPLANT_RATE_MAX = 0.50

# Ограничение по количеству баклажанов на поле
MIN_EGGPLANTS = 1
MAX_EGGPLANTS = 7


STATIC_HEADER = "найди 🍆"

# -------------------- DUEL MODE CONSTANTS --------------------
DUEL_ROUNDS = 3
POINTS = {
    "empty": 0,
    "eggplant": 1,
    "gold": 5,
}

# -------------------- DUEL HELPERS --------------------
DUEL_DELAY_SECONDS = 5

def cell_to_text(cell: str) -> str:
    if cell == "gold":
        return TXT_GOLD
    if cell == "eggplant":
        return TXT_EGGPLANT
    return "пусто"


# -------------------- Состояние игры --------------------
@dataclass
class GameState:
    chat_id: int
    message_id: int
    board: List[str]              # значения: "eggplant" | "gold" | "empty"
    opened: List[bool]            # 9 штук: какие клетки раскрыты (используем ТОЛЬКО при завершении игры)
    log: List[str] = field(default_factory=list)
    active: bool = True
    picked_by_user: Dict[int, int] = field(default_factory=dict)   # user_id -> idx


# -------------------- DUEL STATE --------------------
@dataclass
class DuelState:
    chat_id: int
    message_id: int
    players: List[int] = field(default_factory=list)
    player_names: Dict[int, str] = field(default_factory=dict)
    round_number: int = 1
    scores: Dict[int, int] = field(default_factory=dict)
    board: List[str] = field(default_factory=list)
    picks: Dict[int, int] = field(default_factory=dict)
    active: bool = True


# Можно запускать несколько игр: ключ = (chat_id, message_id)
games: Dict[Tuple[int, int], GameState] = {}

# -------------------- DUELS STORAGE --------------------
duels: Dict[Tuple[int, int], DuelState] = {}



# -------------------- Генерация поля --------------------
def generate_board() -> List[str]:
    """Генерирует поле 3x3, которое не меняется в ходе игры."""

    # 1) Роллим золотой банан (шанс динамический для каждой игры)
    gold_chance = random.uniform(GOLD_BANANA_CHANCE_MIN, GOLD_BANANA_CHANCE_MAX)
    has_gold = random.random() < gold_chance

    total_cells = 9
    remaining = total_cells - (1 if has_gold else 0)

    # 2) Выбираем целевую долю баклажанов 30..50% и переводим в количество
    target_rate = random.uniform(EGGPLANT_RATE_MIN, EGGPLANT_RATE_MAX)
    egg_count = int(round(target_rate * remaining))

    # 3) Клампим по жёстким ограничениям
    egg_count = max(MIN_EGGPLANTS, min(MAX_EGGPLANTS, egg_count))

    # 4) Делаем так, чтобы баклажанов было (почти всегда) меньше воздуха.
    # Для простоты: гарантируем EGGPLANTS < EMPTY.
    # Если золотой банан есть, то сравниваем внутри оставшихся клеток.
    # remaining = egg + empty
    # Нужно egg < empty => egg < remaining - egg => 2*egg < remaining => egg <= floor((remaining-1)/2)
    max_eggs_for_less_than_empty = (remaining - 1) // 2
    egg_count = min(egg_count, max_eggs_for_less_than_empty)

    # На очень редкий случай, если ограничения сделали 0 (например, remaining=1 — у нас такого не бывает),
    # всё равно держим минимум 1
    egg_count = max(1, egg_count)

    empty_count = remaining - egg_count

    items: List[str] = []
    if has_gold:
        items.append("gold")
    items += ["eggplant"] * egg_count
    items += ["empty"] * empty_count

    random.shuffle(items)

    # Проверка длины
    if len(items) != 9:
        # На всякий случай, чтобы игра никогда не падала
        items = (items + ["empty"] * 9)[:9]

    return items


# -------------------- UI --------------------
def cell_to_emoji(cell: str) -> str:
    if cell == "gold":
        return GOLD_BANANA_EMOJI
    if cell == "eggplant":
        return EGGPLANT_EMOJI
    return AIR_EMOJI


def render_keyboard(game: GameState, reveal_all: bool = False, ended: bool = False) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []

    for r in range(3):
        row: List[InlineKeyboardButton] = []
        for c in range(3):
            idx = r * 3 + c
            opened = reveal_all
            text = cell_to_emoji(game.board[idx]) if opened else BOX

            cb = "noop" if (ended or opened) else f"open:{idx}"
            row.append(InlineKeyboardButton(text=text, callback_data=cb))
        rows.append(row)

    if not ended:
        rows.append([InlineKeyboardButton(text="⛔️ Окончить игру", callback_data="end")])
    else:
        rows.append([InlineKeyboardButton(text="✅ Игра окончена", callback_data="noop")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def render_text(game: GameState, ended: bool = False) -> str:
    # Заголовок всегда статичный, лог добавляем ниже
    text = STATIC_HEADER
    if game.log:
        text += "\n\n" + "\n".join(game.log)
    return text


def count_game_stats(game: GameState) -> Dict[str, int]:
    """Считает статистику по уже сделанным кликам в игре."""
    counts = {"eggplant": 0, "gold": 0, "empty": 0}
    for idx in game.picked_by_user.values():
        cell = game.board[idx]
        if cell in counts:
            counts[cell] += 1
        else:
            counts["empty"] += 1
    return counts


# -------------------- Бот --------------------
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Не задан BOT_TOKEN. Запусти так: BOT_TOKEN=... python bot.py")

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
if not WEBHOOK_URL:
    raise RuntimeError(
        "Не задан WEBHOOK_URL. Добавь его в Render Environment: https://eggplant-bot.onrender.com/webhook"
    )

bot = Bot(token=TOKEN)
dp = Dispatcher()

# --- HTTP server for Render + Webhook endpoint ---
async def start_web_server() -> web.AppRunner:
    """Aiohttp-сервер для Render + endpoint для Telegram webhook."""

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    app = web.Application()

    # --- Webhook endpoint for Telegram ---
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path="/webhook")
    setup_application(app, dp, bot=bot)

    # Health routes
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()

    logging.info("Starting HTTP server for Render...")
    port = int(os.getenv("PORT", "10000"))
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()

    logging.info(f"HTTP server started on 0.0.0.0:{port}")
    return runner


@dp.message(Command("start"))
async def start_handler(message: Message):
    await message.answer("Привет! Я игровой бот для вашего чата \n Добавь меня в группу и посмотри на что я способен 🍆 \n Команды: /game, /duel \n Прошу обратить внимание, что я нахожусь в БЕТА-версии и могу немного тупить \n По всем вопросам прошу обращаться к @frozensolid ")




@dp.message(Command("game"))
async def game_handler(message: Message):
    board = generate_board()

    # Сначала отправим сообщение, получим message_id — он будет id игры
    msg = await message.answer(STATIC_HEADER)

    game = GameState(
        chat_id=message.chat.id,
        message_id=msg.message_id,
        board=board,
        opened=[False] * 9,
    )

    games[(game.chat_id, game.message_id)] = game

    await bot.edit_message_text(
        chat_id=game.chat_id,
        message_id=game.message_id,
        text=render_text(game),
        reply_markup=render_keyboard(game),
    )


@dp.callback_query(F.data.startswith("open:"))
async def on_open(callback: CallbackQuery):
    if not callback.message:
        return

    chat_id = callback.message.chat.id
    message_id = callback.message.message_id
    key = (chat_id, message_id)

    game = games.get(key)
    if not game or not game.active:
        await safe_callback_answer(callback, "Игра не найдена или уже завершена.")
        return

    try:
        idx = int(callback.data.split(":", 1)[1])
    except Exception:
        await safe_callback_answer(callback, "Некорректный ход.")
        return

    if idx < 0 or idx >= 9:
        await safe_callback_answer(callback, "Некорректный ход.")
        return

    user_id = callback.from_user.id
    user_name = callback.from_user.full_name

    if user_id in game.picked_by_user:
        await safe_callback_answer(callback, "Ты уже выбрал коробку в этой игре.")
        return

    game.picked_by_user[user_id] = idx
    cell = game.board[idx]

    # Лог в общий чат
    if cell == "gold":
        game.log.append(f"{user_name} нашёл {TXT_GOLD} {GOLD_BANANA_EMOJI}")
    elif cell == "eggplant":
        game.log.append(f"{user_name} нашёл {TXT_EGGPLANT} {EGGPLANT_EMOJI}")
    else:
        game.log.append(f"{user_name} {TXT_EMPTY} {AIR_EMOJI}")

    # Обновляем только текст (лог). Коробки остаются закрытыми до окончания игры.
    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=render_text(game),
            reply_markup=render_keyboard(game),
        )
    except Exception:
        logging.exception("Failed to edit game message on open")
        await safe_callback_answer(callback, "Ошибка при обновлении игры 😢", show_alert=True)
        return

    await safe_callback_answer(callback, "Принято")


@dp.callback_query(F.data == "end")
async def on_end(callback: CallbackQuery):
    if not callback.message:
        return

    chat_id = callback.message.chat.id
    message_id = callback.message.message_id
    key = (chat_id, message_id)

    game = games.get(key)
    if not game or not game.active:
        await safe_callback_answer(callback, "Игра уже завершена.")
        return

    ender_name = callback.from_user.full_name
    game.log.append(f"Игра окончена — {ender_name} окончил игру")

    # Removed stats and personal stat updates as per instructions

    game.active = False
    game.opened = [True] * 9

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=render_text(game, ended=True),
            reply_markup=render_keyboard(game, reveal_all=True, ended=True),
        )
    except Exception:
        logging.exception("Failed to edit game message on end")
        await safe_callback_answer(callback, "Ошибка при завершении игры 😢", show_alert=True)
        return

    await safe_callback_answer(callback, "Игра завершена")


@dp.callback_query(F.data == "noop")
async def on_noop(callback: CallbackQuery):
    # Ничего не делаем, просто снимаем «часики»
    await safe_callback_answer(callback)




@dp.message(Command("duel"))
async def duel_handler(message: Message):
    msg = await message.answer(
        "Готовы сразиться в дуэли?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⚔️ Готов (0/2)", callback_data="duel_ready")]]
        ),
    )

    duel = DuelState(
        chat_id=message.chat.id,
        message_id=msg.message_id,
    )

    duels[(duel.chat_id, duel.message_id)] = duel


def render_keyboard_for_duel(duel: DuelState, reveal_all: bool = False) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for r in range(3):
        row: List[InlineKeyboardButton] = []
        for c in range(3):
            idx = r * 3 + c
            text = cell_to_emoji(duel.board[idx]) if reveal_all else BOX
            cb = "noop" if reveal_all else f"duel_open:{idx}"
            row.append(InlineKeyboardButton(text=text, callback_data=cb))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.callback_query(F.data == "duel_ready")
async def duel_ready(callback: CallbackQuery):
    if not callback.message:
        return

    key = (callback.message.chat.id, callback.message.message_id)
    duel = duels.get(key)
    if not duel or not duel.active:
        await safe_callback_answer(callback, "Дуэль недоступна")
        return

    user_id = callback.from_user.id
    user_name = callback.from_user.full_name

    if user_id in duel.players:
        await safe_callback_answer(callback, "Ты уже готов")
        return

    if len(duel.players) >= 2:
        await safe_callback_answer(callback, "Уже набрано 2 игрока")
        return

    duel.players.append(user_id)
    duel.player_names[user_id] = user_name
    duel.scores[user_id] = 0

    # Обновляем кнопку с количеством готовых игроков
    ready_count = len(duel.players)
    await bot.edit_message_reply_markup(
        chat_id=duel.chat_id,
        message_id=duel.message_id,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=f"⚔️ Готов ({ready_count}/2)", callback_data="duel_ready")]]
        ),
    )

    if len(duel.players) < 2:
        await safe_callback_answer(callback, "Ждём второго игрока")
        return

    # старт первого раунда
    duel.round_number = 1
    duel.board = generate_board()
    duel.picks = {}

    await bot.edit_message_text(
        chat_id=duel.chat_id,
        message_id=duel.message_id,
        text=f"Раунд {duel.round_number}/{DUEL_ROUNDS}\n{duel.player_names[duel.players[0]]}: {duel.scores[duel.players[0]]}  |  {duel.player_names[duel.players[1]]}: {duel.scores[duel.players[1]]}",
        reply_markup=render_keyboard_for_duel(duel),
    )


@dp.callback_query(F.data.startswith("duel_open:"))
async def duel_open(callback: CallbackQuery):
    if not callback.message:
        return

    key = (callback.message.chat.id, callback.message.message_id)
    duel = duels.get(key)
    if not duel or not duel.active:
        return

    user_id = callback.from_user.id

    if user_id not in duel.players:
        await safe_callback_answer(callback, "Ты не участвуешь в дуэли")
        return

    if user_id in duel.picks:
        await safe_callback_answer(callback, "Ты уже сделал выбор")
        return

    try:
        idx = int(callback.data.split(":")[1])
    except Exception:
        await safe_callback_answer(callback, "Некорректный ход")
        return

    duel.picks[user_id] = idx
    await safe_callback_answer(callback, "Выбор принят")

    if len(duel.picks) < 2:
        return

    # оба сделали выбор — раскрываем
    log_lines = [f"Раунд {duel.round_number}/{DUEL_ROUNDS}"]

    for uid in duel.players:
        chosen_idx = duel.picks[uid]
        cell = duel.board[chosen_idx]

        # начисляем очки
        duel.scores[uid] += POINTS[cell]

        name = duel.player_names.get(uid, str(uid))
        log_lines.append(f"{name} нашёл {cell_to_text(cell)} {cell_to_emoji(cell)} (+{POINTS[cell]})")

    log_lines.append("")
    log_lines.append(f"Счёт: {duel.player_names[duel.players[0]]}: {duel.scores[duel.players[0]]}  |  {duel.player_names[duel.players[1]]}: {duel.scores[duel.players[1]]}")

    await bot.edit_message_text(
        chat_id=duel.chat_id,
        message_id=duel.message_id,
        text="\n".join(log_lines),
        reply_markup=render_keyboard_for_duel(duel, reveal_all=True),
    )

    await asyncio.sleep(DUEL_DELAY_SECONDS)

    duel.round_number += 1

    if duel.round_number > DUEL_ROUNDS:
        duel.active = False

        p1, p2 = duel.players
        s1, s2 = duel.scores[p1], duel.scores[p2]

        if s1 > s2:
            winner_text = f"Победил {duel.player_names[p1]}"
        elif s2 > s1:
            winner_text = f"Победил {duel.player_names[p2]}"
        else:
            winner_text = "Ничья"

        await bot.edit_message_text(
            chat_id=duel.chat_id,
            message_id=duel.message_id,
            text=(
                "Дуэль завершена!\n"
                f"Счёт: {duel.player_names[p1]} {s1} — {s2} {duel.player_names[p2]}\n"
                f"{winner_text}"
            ),
            reply_markup=None,
        )
        return

    # новый раунд
    duel.board = generate_board()
    duel.picks = {}

    await bot.edit_message_text(
        chat_id=duel.chat_id,
        message_id=duel.message_id,
        text=f"Раунд {duel.round_number}/{DUEL_ROUNDS}\n{duel.player_names[duel.players[0]]}: {duel.scores[duel.players[0]]}  |  {duel.player_names[duel.players[1]]}: {duel.scores[duel.players[1]]}",
        reply_markup=render_keyboard_for_duel(duel),
    )


@dp.callback_query()
async def on_any_callback(callback: CallbackQuery):
    # Диагностика: если какая-то callback не матчит наши хэндлеры, хотя бы увидим её в логах.
    logging.info(f"Unhandled callback data: {callback.data}")
    await safe_callback_answer(callback, "(кнопка не распознана)")

async def main():
    logging.basicConfig(level=logging.INFO, force=True)

    runner: web.AppRunner | None = None
    try:
        # Tell Telegram where to send updates
        await bot.set_webhook(WEBHOOK_URL)
        logging.info(f"Webhook set to: {WEBHOOK_URL}")

        # Start aiohttp server (Render port)
        runner = await start_web_server()

        # Keep process alive forever
        await asyncio.Event().wait()

    finally:
        try:
            await bot.delete_webhook(drop_pending_updates=False)
        except Exception:
            pass
        if runner is not None:
            await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped.")