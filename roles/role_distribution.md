# role_distribution の書き方

このプロジェクトでは、`roles/role_distribution.json` に以下の短縮記法を許容します。

- 各キーはプレイヤー人数（文字列）です。
- 値は配列で、要素は `"role"` または `"role:count"` の形式を使用できます。
  - 例: `["werewolf:2", "seer", "villager:3"]`
- `villager` を省略した場合、残りの人数が自動的に `villager` で埋められます。
- また、従来通り `{"role": count}` の dict 形式もサポートします。

このファイル（README 的なテンプレート）を参照して、`roles/role_distribution.json` を編集してください。
