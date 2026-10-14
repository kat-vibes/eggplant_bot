import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from game import DuelState, GameState


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        bot.games.clear()
        bot.duels.clear()
        bot.updated.clear()
        bot.locks.clear()
        self.key = (123, 456)
        self.api = SimpleNamespace(
            edit_message_text=AsyncMock(), edit_message_reply_markup=AsyncMock()
        )

    def callback(self, data, user=1):
        return SimpleNamespace(
            data=data, from_user=SimpleNamespace(id=user, full_name="Player"),
            answer=AsyncMock(),
        )

    async def test_normal_game_finishes_and_reveals_board(self):
        bot.games[self.key] = GameState()
        await bot.process_callback(self.callback("open:0"), self.api, self.key)
        await bot.process_callback(self.callback("end"), self.api, self.key)
        self.assertNotIn(self.key, bot.games)
        buttons = self.api.edit_message_text.call_args.kwargs["reply_markup"].inline_keyboard
        self.assertTrue(all(b.callback_data == "noop" for row in buttons for b in row))

    async def test_duel_completes_and_cleans_up(self):
        bot.duels[self.key] = DuelState()
        await bot.process_callback(self.callback("duel_ready", 1), self.api, self.key)
        await bot.process_callback(self.callback("duel_ready", 2), self.api, self.key)
        with patch.object(bot, "DUEL_DELAY_SECONDS", 0):
            for round_number in range(1, 4):
                await bot.process_callback(self.callback(f"duel_open:{round_number}:0", 1), self.api, self.key)
                await bot.process_callback(self.callback(f"duel_open:{round_number}:1", 2), self.api, self.key)
        self.assertNotIn(self.key, bot.duels)
        self.assertIsNone(self.api.edit_message_text.call_args.kwargs["reply_markup"])

    async def test_missing_session_is_reported(self):
        with self.assertRaisesRegex(ValueError, "перезапущен"):
            await bot.process_callback(self.callback("open:0"), self.api, self.key)

    async def test_old_duel_buttons_cannot_pick_in_new_round(self):
        duel = DuelState(round_number=2)
        duel.join(1, "Alice")
        duel.join(2, "Bob")
        bot.duels[self.key] = duel
        with self.assertRaises(ValueError):
            await bot.process_callback(self.callback("duel_open:1:0"), self.api, self.key)
        self.assertEqual(duel.picks, {})
        self.api.edit_message_text.assert_not_awaited()

    def test_duel_keyboard_includes_round_number(self):
        keyboard = bot.keyboard(["empty"] * 9, round_number=2)
        self.assertEqual(keyboard.inline_keyboard[0][0].callback_data, "duel_open:2:0")

    def test_long_game_log_is_truncated(self):
        game = GameState(log=["🍆" * 150] * 100)
        text = bot.game_text(game)
        self.assertLessEqual(len(text.encode("utf-16-le")) // 2, 4096)


if __name__ == "__main__":
    unittest.main()
