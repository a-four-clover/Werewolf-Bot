from __future__ import annotations
from typing import Dict, List, Optional, Set
import random

class WordWolfGame:
    def __init__(self, channel_id: str, owner_id: str):
        self.channel_id = channel_id
        self.owner_id = owner_id
        # participants are discord user ids as strings
        self.players: List[str] = []
        # chooser is an external user id (str) who will pick the words and not participate
        self.chooser_id: Optional[str] = None
        # assigned word per player id
        self.assigned_words: Dict[str, str] = {}
        # minority player ids
        self.minority_ids: Set[str] = set()
        # pending votes: voter_id -> target_id
        self._pending_votes: Dict[str, Optional[str]] = {}
        self.started: bool = False
        # words in use (majority, minority)
        self.major_word: Optional[str] = None
        self.minor_word: Optional[str] = None

    def add_players_from_voice_channel(self, members: List[int]):
        # members: list of member ids (ints) -> store as str
        self.players = [str(m) for m in members if m is not None]

    def pick_minority(self, minority_count: int = 1):
        if not self.players:
            self.minority_ids = set()
            return
        choices = random.sample(self.players, min(minority_count, len(self.players)))
        self.minority_ids = set(choices)

    def assign_words(self):
        if not self.major_word or not self.minor_word:
            return
        self.assigned_words = {}
        for pid in self.players:
            if pid in self.minority_ids:
                self.assigned_words[pid] = self.minor_word
            else:
                self.assigned_words[pid] = self.major_word

    def record_vote(self, voter: str, target: str):
        if voter not in self.players:
            return False
        if target not in self.players:
            return False
        self._pending_votes[str(voter)] = str(target)
        return True

    def tally_votes(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        # Only count votes that target actual players. This ignores markers like
        # '__abstain__' or '__invalid__' which are written by the Cog into
        # _pending_votes for timeout/pre-seeding. Counting non-player targets
        # causes incorrect lynch behavior (e.g. abstain being selected as lynch).
        for v, t in list(self._pending_votes.items()):
            if not t:
                continue
            # only count if t is a current participant id
            try:
                if str(t) not in self.players:
                    continue
            except Exception:
                continue
            counts[t] = counts.get(t, 0) + 1
        return counts

    def clear_votes(self):
        self._pending_votes = {}

    def eliminate(self, target: str) -> List[str]:
        # remove target from players and return list of removed ids
        removed = []
        if target in self.players:
            try:
                self.players.remove(target)
                removed.append(target)
            except Exception:
                pass
        # also remove from minority set if present
        try:
            if target in self.minority_ids:
                self.minority_ids.remove(target)
        except Exception:
            pass
        # cleanup assigned words
        try:
            if target in self.assigned_words:
                del self.assigned_words[target]
        except Exception:
            pass
        return removed

    def check_win(self) -> Optional[str]:
        # simple rule: if no minority remain -> majority win; if minority_count >= majority_count -> minority win
        maj = len([p for p in self.players if p not in self.minority_ids])
        minc = len(self.minority_ids)
        if minc == 0:
            return 'majority'
        if minc >= maj:
            return 'minority'
        return None
