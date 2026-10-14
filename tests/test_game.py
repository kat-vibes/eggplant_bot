import random
import unittest

from game import DuelState, GameState, generate_board


class BoardTests(unittest.TestCase):
    def test_board_rules_across_seeds(self):
        saw_gold = False
        for seed in range(1000):
            board = generate_board(random.Random(seed))
            self.assertEqual(len(board), 9)
            self.assertLessEqual(board.count("gold"), 1)
            self.assertGreater(board.count("eggplant"), 0)
            self.assertLess(board.count("eggplant"), board.count("empty"))
            self.assertTrue(set(board) <= {"gold", "empty", "eggplant"})
            saw_gold |= "gold" in board
        self.assertTrue(saw_gold)

    def test_one_pick_per_user_same_cell_allowed(self):
        game = GameState(board=["eggplant"] * 9)
        self.assertEqual(game.pick(1, 0), "eggplant")
        with self.assertRaises(ValueError):
            game.pick(1, 1)
        self.assertEqual(game.pick(2, 0), "eggplant")

    def test_invalid_indices_do_not_mutate_state(self):
        game = GameState()
        for index in (-1, 9, 999):
            with self.assertRaises(ValueError):
                game.pick(1, index)
        self.assertEqual(game.picks, {})


class DuelTests(unittest.TestCase):
    def setUp(self):
        self.duel = DuelState()
        self.duel.join(1, "Alice")
        self.duel.join(2, "Bob")

    def test_join_restrictions(self):
        for player in (1, 3):
            with self.assertRaises(ValueError):
                self.duel.join(player, "Other")

    def test_invalid_moves_do_not_mutate_state(self):
        for player, index, round_number in ((3, 0, 1), (1, -1, 1), (1, 9, 1), (1, 0, 2)):
            with self.assertRaises(ValueError):
                self.duel.pick(player, index, round_number)
        self.assertEqual(self.duel.picks, {})
        self.assertEqual(self.duel.scores, {1: 0, 2: 0})

    def test_scoring_once_and_stale_round(self):
        self.duel.board = ["gold", "eggplant"] + ["empty"] * 7
        self.assertFalse(self.duel.pick(1, 0, 1))
        with self.assertRaises(ValueError):
            self.duel.pick(1, 1, 1)
        self.assertTrue(self.duel.pick(2, 1, 1))
        with self.assertRaises(ValueError):
            self.duel.pick(2, 1, 1)
        self.assertEqual(self.duel.scores, {1: 5, 2: 1})
        self.assertTrue(self.duel.next_round())
        with self.assertRaises(ValueError):
            self.duel.pick(1, 0, 1)
        self.assertEqual(self.duel.picks, {})

    def test_three_rounds_and_winner(self):
        for round_number in range(1, 4):
            self.duel.board = ["eggplant"] + ["empty"] * 8
            self.duel.pick(1, 0, round_number)
            self.duel.pick(2, 1, round_number)
            self.assertEqual(self.duel.next_round(), round_number < 3)
        self.assertEqual(self.duel.scores, {1: 3, 2: 0})
        self.assertEqual(self.duel.winner(), 1)

    def test_tie_and_incomplete_round(self):
        self.assertIsNone(self.duel.winner())
        with self.assertRaises(ValueError):
            self.duel.next_round()


if __name__ == "__main__":
    unittest.main()
