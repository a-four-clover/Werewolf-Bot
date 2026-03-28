from __future__ import annotations
from typing import Dict, Optional
from src.engine import Game


class UserStats:
    """ユーザーの統計データを保持するクラス"""
    def __init__(self):
        self.total_games: int = 0
        self.total_wins: int = 0
        self.win_rate: float = 0.0
    
    def add_game_result(self, is_winner: bool) -> None:
        """ゲーム結果を追加して統計を更新"""
        self.total_games += 1
        if is_winner:
            self.total_wins += 1
        self.win_rate = self.total_wins / self.total_games if self.total_games > 0 else 0.0


class StorageInterface:
    def save_game(self, game: Game) -> None:
        raise NotImplementedError

    def load_game(self, game_id: str) -> Optional[Game]:
        raise NotImplementedError
    
    def save_user_stats(self, user_id: str, stats: UserStats) -> None:
        raise NotImplementedError
    
    def load_user_stats(self, user_id: str) -> UserStats:
        raise NotImplementedError
    
    def update_game_results(self, all_player_ids: list[str], winner_ids: list[str]) -> None:
        """ゲーム終了時に全プレイヤーの統計を更新"""
        raise NotImplementedError


class InMemoryStorage(StorageInterface):
    def __init__(self):
        self._games: Dict[str, Game] = {}
        self._user_stats: Dict[str, UserStats] = {}

    def save_game(self, game: Game) -> None:
        self._games[game.game_id] = game

    def load_game(self, game_id: str):
        return self._games.get(game_id)
    
    def save_user_stats(self, user_id: str, stats: UserStats) -> None:
        self._user_stats[user_id] = stats
    
    def load_user_stats(self, user_id: str) -> UserStats:
        return self._user_stats.get(user_id, UserStats())
    
    def update_game_results(self, all_player_ids: list[str], winner_ids: list[str]) -> None:
        """ゲーム終了時に全プレイヤーの統計を更新"""
        for player_id in all_player_ids:
            stats = self.load_user_stats(player_id)
            is_winner = player_id in winner_ids
            stats.add_game_result(is_winner)
            self.save_user_stats(player_id, stats)
    
    def get_all_user_stats(self) -> Dict[str, UserStats]:
        """デバッグ用：全ユーザーの統計を取得"""
        return self._user_stats.copy()
    
    def add_test_stats(self, user_id: str, games: int, wins: int) -> None:
        """テスト用：指定したユーザーに統計データを追加"""
        stats = self.load_user_stats(user_id)
        for _ in range(games):
            stats.add_game_result(_ < wins)
        self.save_user_stats(user_id, stats)
