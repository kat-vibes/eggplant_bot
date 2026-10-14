"""Game rules independent of Telegram and deployment settings."""

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

DUEL_ROUNDS = 3
POINTS = {"empty": 0, "eggplant": 1, "gold": 5}
EMOJI = {"empty": "💨", "eggplant": "🍆", "gold": "🍌"}
LABELS = {"empty": "пусто", "eggplant": "баклажан", "gold": "ЗОЛОТОООЙ БАНАН"}


def generate_board(rng=None) -> List[str]:
    """More empty cells than eggplants; average gold probability is 2.5%."""
    rng = rng or random
    chance = rng.uniform(0, 0.05)
    has_gold = rng.random() < chance
    remaining = 9 - int(has_gold)
    eggs = min(round(rng.uniform(0.30, 0.50) * remaining), (remaining - 1) // 2)
    board = ["gold"] * int(has_gold) + ["eggplant"] * eggs
    board += ["empty"] * (remaining - eggs)
    rng.shuffle(board)
    return board


@dataclass
class GameState:
    board: List[str] = field(default_factory=generate_board)
    picks: Dict[int, int] = field(default_factory=dict)
    log: List[str] = field(default_factory=list)

    def pick(self, user_id: int, index: int) -> str:
        if not 0 <= index < 9:
            raise ValueError("Некорректный ход")
        if user_id in self.picks:
            raise ValueError("Ты уже выбрал коробку в этой игре")
        self.picks[user_id] = index
        return self.board[index]


@dataclass
class DuelState:
    players: Dict[int, str] = field(default_factory=dict)
    scores: Dict[int, int] = field(default_factory=dict)
    board: List[str] = field(default_factory=generate_board)
    picks: Dict[int, int] = field(default_factory=dict)
    round_number: int = 1
    resolving: bool = False

    def join(self, user_id: int, name: str) -> None:
        if user_id in self.players:
            raise ValueError("Ты уже готов")
        if len(self.players) == 2:
            raise ValueError("Уже набрано 2 игрока")
        self.players[user_id] = name
        self.scores[user_id] = 0

    def pick(self, user_id: int, index: int, round_number: int) -> bool:
        if len(self.players) != 2 or user_id not in self.players:
            raise ValueError("Ты не участвуешь в дуэли")
        if self.resolving or round_number != self.round_number:
            raise ValueError("Этот раунд уже завершён")
        if not 0 <= index < 9:
            raise ValueError("Некорректный ход")
        if user_id in self.picks:
            raise ValueError("Ты уже сделал выбор")
        self.picks[user_id] = index
        if len(self.picks) != 2:
            return False
        self.resolving = True
        for player, chosen in self.picks.items():
            self.scores[player] += POINTS[self.board[chosen]]
        return True

    def next_round(self) -> bool:
        if not self.resolving:
            raise ValueError("Both players must pick before the next round")
        if self.round_number == DUEL_ROUNDS:
            return False
        self.round_number += 1
        self.board = generate_board()
        self.picks.clear()
        self.resolving = False
        return True

    def winner(self) -> Optional[int]:
        if len(self.players) != 2:
            raise ValueError("A duel requires two players")
        first, second = self.players
        if self.scores[first] == self.scores[second]:
            return None
        return max(self.scores, key=self.scores.get)
