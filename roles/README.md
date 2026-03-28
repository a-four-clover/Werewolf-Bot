role_distribution_template.json

Format:
- JSON object where each key is the player count (as a string) and the value is an object mapping role_id -> count.
- Example:
  {
    "5": {"werewolf":1, "seer":1, "villager":3}
  }

Notes:
- Ensure the sum of role counts equals the player count for that entry.
- Role ids must match role ids used by the game (e.g. `werewolf`, `seer`, `madman`, `villager`, `medium`, `knight`).
- If a player count is not present in this file, the game will fall back to the built-in heuristic.
- You can customize distributions per table/rule. Keep this file UTF-8 encoded.

Usage:
- Edit `roles/role_distribution_template.json` and add or adjust entries.
- Restart the bot or reload the roles via code if you add a runtime reload command (not implemented by default).
