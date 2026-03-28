"""Simple i18n module for the werewolf bot.

This module exposes a flat mapping MESSAGES: Dict[str, str] and a helper
function msg(key, **kwargs) which returns formatted messages. Tests and the
bot code depend on the flat mapping and the msg API, so keys must be preserved.

For readability the messages are grouped below by comment headers (lobby/start,
night, day, roles, wordwolf, ui, etc.).
"""

from typing import Dict


MESSAGES: Dict[str, str] = {
    # -----------------
    # Lobby / Creation
    # -----------------
    'create_success_auto_join': '{owner} がロビーを作成し、ゲーム参加者の募集を始めました。オーナーは自動で参加しました。最大で {max_players} 人参加できます。',
    'create_success_auto_join_list': '{owner} がロビーを作成し、ゲーム参加者の募集を始めました。オーナーは自動で参加しました。最大で {max_players} 人参加できます。{names}は既に参加しています。',
    'create_success_no_auto': '{owner} がロビーを作成し、ゲーム参加者の募集を始めました。オーナーは自動参加しませんでした。最大で {max_players} 人参加できます。',
    'create_failed_vc_required': 'ロビーの作成に失敗しました: ボイスチャンネルに参加しているか、コマンドでボイスチャンネルを指定してください。',
    'create_failed_vc_determine': 'ロビーの作成に失敗しました: ボイスチャンネルが確認できません。明示的にボイスチャンネルを指定してください。',
    'create_already_active': 'ロビーが既に作成されているか、ゲームが始まっています。募集し直すには現在のゲームを終了してください。',
    'join_no_lobby': 'このチャンネルにロビーはありません。',
    'join_confirm': '参加しました: {ok}',
    'lobby_reached_min': 'ロビーは最低人数に達しました（{count}人）。オーナーは /ww_start でゲームを開始できます。',
    'leave_owner_cannot': 'オーナーはロビーから離脱できません。ロビーを閉じるには /ww_close を使用してください。',
    'leave_after_start': '離脱できません。ゲームは既に開始済みか終了しています。',
    'left_lobby': '{name} がロビーから離れました。',
    'left_lobby_vc_removed': '{name} がボイスチャンネルから離れたためロビーから除外されました。',
    'vc_left_request_rejoin': 'さんがボイスチャンネルから離れました。ゲーム続行のため再参加してください。',

    # -----------------
    # Game Start / DM / VC reminders
    # -----------------
    'start_no_lobby': 'このチャンネルにロビーはありません。',
    'start_only_owner': 'ゲームを開始できるのはオーナーのみです。',
    'start_failed': '開始に失敗しました。参加者が不足している可能性があります。',
    'role_dm': 'あなたの役職: {role}',
    'vc_reminder_designated': 'このゲームは {vc_name} を使用しています。今すぐ参加してください。',
    'vc_reminder_generic': 'このゲームはボイスチャンネルを使用します。今すぐボイスチャンネルに参加してください。',
    'joined_via_voice': 'がボイスチャンネルに参加しました。',
    'dm_failed_notice': '{names} への DM の配信に失敗しました。DM を受け取れる設定か確認してください。',
    'game_started_embed_title': '人狼ゲームを開始しました',
    'embed_footer': '役職は可能な限り DM で通知しました。個人の情報は DM で届きます。',
    'start_embed_fields': ['参加者', '役職一覧'],
    # labels for start options shown in the start embed
    'start_options_label': 'オプション',
    'start_option_lovers': '恋人',
    'start_option_abstain': '棄権',
    # generic option labels
    'option_on': 'あり',
    'option_off': 'なし',

    # -----------------
    # Night phase
    # -----------------
    'night_prompt': '夜のターンです。ターゲットを選択してください（役職: {role}）',
    # Sage-specific night prompt (no target selection). include remaining shields via {shields}
    'sage_night_prompt': '夜のターンです。残り {shields} 回の結界があります。結界を使用するか選択してください（役職: {role}）',
    'seer_result': '占い結果: {target} は {result} です。',
    'seer_result_followup': '占い結果: {target} は {result} です。',
    'seer_no_white': '占い: 白と判定される候補がいませんでした。',
    'medium_result': '霊媒結果: {victim} は {result} でした。',
    'night_choice_registered': '夜の行動を登録しました: {target}',
    'night_choice_confirmed': '夜の行動を確定しました。',
    'night_choice_executed': '夜の行動を実行しました: {target}',
    'night_select_target': '対象を選択',
    'night_reminder': '夜の行動を終えてください。夜のターンはまもなく強制終了します。',
    # Sage (賢者)
    'sage_shield_label': '結界を張る',
    'sage_shield_used': 'あなたは結界を張りました。襲撃を受けた場合、生存している人狼のうちランダムで1名が死亡します。',
    'sage_shield_none_dm': 'あなたの賢者の結界は残っていません。',
    'sage_shield_use_button': '結界を張る',
    'sage_shield_skip_button': '使わない',
    'sage_shield_confirmed': '結界を使用しました。',
    'sage_shield_skipped': '結界を使用しませんでした。',
    # Evil Busker (イビルバスカー)
    'busker_night_prompt': '夜のターンです。残り {uses} 回の偽装死があります。偽装死を使用するか選択してください（役職: {role}）',
    'busker_night_prompt_with_attack': '夜のターンです。襲撃対象を選択し、偽装死を使用するか選択してください。残り {uses} 回の偽装死があります（役職: {role}）',
    'busker_fake_label': '偽装死を使う',
    'busker_fake_use_button': '偽装死を使う',
    'busker_fake_skip_button': 'スキップ',
    'busker_fake_confirmed': '偽装死を使用しました。夜明けに死亡判定されますが、直後の昼終了時にゲームが終了しなければ復活し、追加襲撃を行います。',
    'busker_fake_skipped': '偽装死を使用しませんでした。',
    'busker_fake_none_dm': 'あなたの偽装死は残っていません。',
    'busker_fake_active_dm': 'あなたは既に偽装死状態です。',
    'evil_busker_used': 'あなたは相手の夜行動を妨害しました。対象: {target}',
    'busker_fake_used': 'あなたは偽装死を使用しました。（残り: {remaining}回）',
    'busker_revive_dm_header': 'あなたは復活しました。追加で1人を襲撃できます。ターゲットを選択してください。',
    'busker_revive_select_placeholder': '襲撃対象を選択...',
    'busker_revive_confirm': '追加襲撃が成功しました。対象: {target}',
    'busker_revive_no_target': '追加襲撃の対象が見つかりませんでした。',
    'confirm_end_night': '夜の行動が未完了の人がいますが、本当に夜ターンを強制終了しますか？',
    'night_forced_dm': '夜ターンが運営によって強制終了されました。あなたの夜の行動は処理されませんでした。',
    'oiled': 'あなたは侵されました。',
    # Knight note: prevent protecting same target twice
    'knight_exclude_prev_note': '注: 前の夜に守った相手は連続して守ることはできません。',

    # -----------------
    # Day / Voting phase
    # -----------------
    'day_vote_started': '昼の投票を開始します。{seconds} 秒以内に投票先を選択してください。',
    'day_vote_started_no_seconds': '昼の投票を開始します。投票先を選択してください。',
    'vote_recorded': '投票が記録されました。タイマーが終了するまでは変更が可能です。',
    'vote_recorded_target': '{target}に投票しました。制限時間まで変更できます。',
    'vote_recorded_abstain': '棄権に投票しました。制限時間まで変更できます。',
    'vote_auto_abstain_dm': '時間切れのため投票が棄権として記録されました。',
    'vote_abstain_label': '棄権',
    'vote_invalid_dm': '時間切れのため、あなたの投票は無効票として扱われました（集計に含まれません）。',
    'vote_ending_soon_dm': 'まもなく投票が終了します。速やかに投票してください。',
    'confirm_buttons': ['はい', 'いいえ'],
    # confirmation for force-ending a vote
    'confirm_end_vote': '現在の投票を強制的に終了して集計を開始しますか？',
    'vote_placeholders': ['投票先を選択してください...', '投票先を選択してください（ページ {page}）...'],
    'tally_embed': ['投票結果', '有効投票数'],
    'dead_dm': 'あなたは死亡しました。これ以降は投票などに参加できません。',
    'dead_players_public': '昨夜の犠牲者は {names} でした。',
    'lynched_public': '投票の結果、{names} が追放されました。',
    'could_not_deliver_private': '{names} への個別メッセージの配信に失敗しました。',
    'day_revote_prompt': '投票が同数で決着がつきませんでした。決選投票を行います。{seconds} 秒以内に投票先を選択してください。',
    'day_revote_prompt_no_seconds': '投票が同数で決着がつきませんでした。決選投票を行います。投票先を選択してください。',
    # Announced when a revote remains tied and a random lynch is performed
    'random_lynch_public': '決選でも決着がつかなかったため、ランダムで処刑されました: {name}',
    'day_vote_30s_public': '投票終了まで残り 30 秒です。最終の投票先を選択してください。',
    'day_vote_30s_dm': '投票終了まで残り 30 秒です。チャンネルで最終投票を行ってください。',
    'day_vote_no_votes': '誰も投票しませんでした。処刑は行われません。夜のターンに移行します。',

    # UI interaction messages
    'vote_invalidated': 'この投票は無効になりました',
    'vote_select_placeholder': '投票先を選択してください',
    'vote_abstain_display': '棄権',
    'vote_confirmation': '{target}に投票しました。制限時間まで変更できます。',
    'game_not_found': 'ゲームが見つかりません',
    'system_error': 'システムエラー',
    'vote_processing_error': '投票処理でエラーが発生しました',
    'end_vote_confirmed': '投票を強制終了しました。集計を開始します。',

    # -----------------
    # Misc / UI / Commands
    # -----------------
    'no_lobby_in_channel': 'このチャンネルにロビーはありません。',
    'left_lobby_result': 'ロビーを離れました: {result}',
    'game_ended_winner': 'ゲーム終了: {winner}の勝利',
    'game_ended_embed_title': 'ゲーム終了',
    'game_ended_fields': ['勝者', '敗者'],
    'only_owner_close': 'ロビーを閉じることができるのはオーナーのみです。',
    'lobby_closed_removed': 'ロビーを閉鎖し、ゲームを削除しました。',
    'lobby_closed_cleared': 'ロビーを閉鎖し、ゲームを終了しました。',
    'failed_close_lobby': 'ロビーの閉鎖に失敗しました。',
    'internal_error_short': '内部エラーが発生しました。管理者に連絡してください。 ({error})',

    # -----------------
    # Werewolf-specific
    # -----------------
    'wolf_chat_created': '人狼専用チャットを作成しました: #{channel_name}。そちらで相談してください。',
    'wolf_group_started': '人狼グループチャットが作成されました。この DM にメッセージを送ると他の人狼に届きます。',
    'wolf_vote_recorded': '人狼投票を記録しました: {target}',
    'wolf_unanimous_achieved': '全員合意が得られました: {target} を襲撃します。',
    'wolf_vote_fallback_plurality': '合意が得られなかったため、個別の票を集計して決定します。',
    'wolf_revote_timeout_public': '人狼の決選が時間内に解決しなかったため、今夜の襲撃は行われませんでした。',
    'wolf_revote_timeout_dm': '決選が時間内に解決しなかったため、今夜は襲撃しません。',
    'wolf_night_30s_dm': '残り30秒です。人狼は急いで選択してください。',
    'wolf_chat_relay': '[人狼チャット] {name}: {content}',
    'wolf_teammates': 'あなたの人狼仲間: {names}',

    # -----------------
    # WordWolf
    # -----------------
    'ww_create_lobby_created': 'ワードウルフのロビーを作成しました。既に VC にいる人は参加済です。開始は /ww_word_start で行います。',
    'ww_create_failed': 'ロビーの作成に失敗しました。',
    'ww_no_lobby_in_channel': 'このチャンネルに作成済みのロビーがありません。/ww_word_create でロビーを作成してください。',
    'ww_start_game_dm_sent': 'ゲームを開始しました。{count} 人に単語を DM しました。（DM失敗: {failed}）',
    'ww_start_game_failed': 'ゲーム開始時にエラーが発生しました。',
    'ww_chooser_prompt': 'あなたはお題出題者です。/ww_word_set <majority_word> <minority_word> で設定してください。',
    'ww_chooser_dm_failed': 'お題出題者に DM を送れませんでした。',
    'ww_chooser_not_found': 'お題出題者の取得に失敗しました。',
    'ww_words_sent': '単語を送信しました（失敗: {failed}）',
    'ww_words_set_ok': '単語を設定しました。/ww_word_start でゲームを開始できます。',
    'ww_words_set_error': '単語送信中にエラーが発生しました。',
    'ww_vote_start_public': 'ワードウルフ: 投票を開始します。',
    'ww_vote_start_public_view_fail': 'ワードウルフ: 投票を開始します。（ビュー送信失敗）',
    'ww_no_votes': '投票がありませんでした。',
    'ww_no_lynch': '同数のため処刑は行われませんでした。',
    'ww_lynched_announce': '{name} が処刑されました。',
    'ww_majority_lynched': '処刑されたのは市民側でした。人狼の勝利です。',
    'ww_reversal_start_public': '逆転チャンスです。',
    'ww_reversal_dm': '逆転チャンス: 逆転チャンスに成功しましたか？',
    'ww_reversal_dm_failed': '<@{id}> に逆転チャンスの通知を送れませんでした。',
    'ww_reversal_timeout_public': '逆転チャンスの時間切れです。市民の勝利とします。',
    'ww_reversal_timeout_dm': '逆転チャンスの時間切れです。',
    'ww_reversal_werewolf_win': '人狼の勝利（逆転成功）',
    'ww_reversal_citizen_win': '市民の勝利（逆転失敗）',
    'ww_start_not_allowed': 'ゲーム開始はお題出題者またはオーナーのみ実行できます。',
    'ww_no_active_vote': '現在進行中の投票はありません。',
    'ww_end_vote_confirmed': '投票を強制終了しました。集計を開始します。',

    # -----------------
    # Command descriptions / args / UI
    # -----------------
    'cmd_guild_only': 'このコマンドはサーバー内のチャンネルで使用してください。',
    'cmd_guess_description': '推測射殺を使います（ナイスゲッサー/イビルゲッサー専用、昼のみ、1回）',
    'guess_not_allowed_phase': '推測できる時間外です。昼の投票が十分に残っているときにのみ実行できます。',
    
    # Admin/reload messages
    'cmd_reload_admin_only': 'このコマンドはサーバー管理者またはボットオーナーのみ実行できます。',
    'reload_success': '設定を再読み込みしました。',
    'reload_failed': '再読み込みに失敗しました: {error}',
    
    # Status panel messages
    'status_embed_title': '人狼ゲーム進行状況',
    'status_current_phase': '{icon} 現在のフェーズ',
    'status_alive_players': '生存者 ({count}人)',
    'status_dead_players': '死亡者 ({count}人)',
    'status_settings': '設定',
    'status_no_players': 'なし',
    'status_others_count': '... 他{count}人',
    'status_max_min_players': '最大: {max}人\n最小: {min}人',
    'status_lovers_enabled': '恋人: あり',
    'status_last_updated': '最終更新: {time}',
    
    # Error messages
    'log_fetch_error': 'ログの取得中にエラーが発生しました: {error}',
    'guess_dead_cannot': '死亡しているため推測できません。',
    'guess_dm_header_alive_list': '生存者一覧（あなたを除く）',
    'guess_dm_header_roles_list': '役職一覧（ゲーム内に含まれる全役職、死亡者に割り当てられた役職も含む）',
    'guess_success_public': '{name} が死にました。',
    'guess_vote_restart': '{name} が死にました。投票を再開します。死亡者を除いた投票先を選択してください。',
    'guess_vote_restart_with_time': '{name} が死にました。投票を再開します（残り時間: {seconds}秒）。死亡者を除いた投票先を選択してください。',
    'guess_command_dm_cancelled': '推測をキャンセルしました。',
    'guess_invalid_old_vote_ui': 'ここには投票できません。',
    'guess_role_dm_hint': '推測射殺を行うには、このボットに DM で /ww_guess と入力してください。昼の会議中、条件を満たす場合に実行できます。（使用可能回数: {limit}回）',
    'guess_already_used': 'あなたは既に推測射殺を実行しています。このゲームでの使用可能回数は {limit} 回です。（残り: {remaining}回）',
    'guess_only_once': '推測は設定された回数（{limit}回）までしか使えません',
    'guess_confirm_prompt': '選択した組み合わせが実際の役職と一致していれば対象を射殺でき、逆に一致していなければあなたが死亡します。推測を実行しますか？',
    'guess_confirm_execute': '選択した組み合わせで推測を実行します。実行しますか？',
    'guess_target_not_alive': '選んだ対象は既に死亡しています。操作を中止します。',

    # -----------------
    # Validation / generic / misc
    # -----------------
    'min_players_at_least': 'min_players は少なくとも 1 である必要があります',
    'max_players_ge_min': 'max_players は min_players 以上でなければなりません',
    'cmd_create_description': '人狼のロビーを作成します。',
    'cmd_show_logs_description': 'ゲームのログと人狼 DM 診断情報を表示します（オーナー専用）。',
    'cmd_start_description': 'ゲームを開始します（オーナーのみ）。',
    'cmd_close_description': 'ロビーを開始せずに閉じます。',
    'cmd_status_description': 'ゲームの状態を表示します。',
    'cmd_end_night_description': '夜ターンを早期に終了します（管理者のみ）。',
    'cmd_pause_description': 'ゲームタイマーを一時停止します（オーナーまたはスタッフのみ）。',
    'cmd_resume_description': '一時停止したゲームタイマーを再開します（オーナーまたはスタッフのみ）。',
    'cmd_end_vote_description': '現在の投票を強制的に終了します（オーナーのみ）。',
    'arg_voice_channel': 'ゲームで使用する任意のボイスチャンネル',
    'arg_night_timeout': '夜の行動待ちタイムアウト（秒）。未指定で無制限',
    'arg_day_vote_timeout': '昼の投票（会議）時間（秒）。未指定で無制限',
    'arg_no_abstain': '（非推奨）棄権選択を無効にします（棄権不可モード）',
    'arg_allow_abstain': '棄権選択を許可します（デフォルト: 許可）',
    'arg_enable_lovers': '恋人モードを有効にします（デフォルト: 無効）',

    # -----------------
    # UI labels
    # -----------------
    'vote_placeholder_single': '投票先を選択してください...',
    'vote_placeholder_paged': '投票先を選択してください（ページ {page}）...',
    'button_yes': 'はい',
    'button_no': 'いいえ',
    'execute_button': '実行',
    'no_selection': '選択がありません。選択肢から対象を選んでください。',
    'confirm_force_night': '強制夜ターンを実行します。',
    'action_cancelled': 'キャンセルされました。',
    'other_label': 'その他',
    'execute_failed': '実行できませんでした。運営にお問い合わせください。',

    # -----------------
    # Lovers / fun / logs
    # -----------------
    'lovers_assigned': 'あなたは {partner} と恋に落ちました。',
    'lovers_partner_killed': 'あなたの恋人 {by} が死亡しました。あなたも同時に死亡しました。',
    'bakery_bread_ready': 'おいしいパンが焼けました。',
    'show_logs_no_channel': 'コマンドはチャンネル内で実行してください。',
    'show_logs_no_game': 'このチャンネルに紐づくゲームが見つかりません。',
    'show_logs_not_owner': 'オーナーのみ実行できます。',
    'show_logs_logs_header': 'Logs (last {count}):',
    'show_logs_wolf_group_members': '_wolf_group_members: {members}',
    'show_logs_wolf_dm_failures': '_wolf_dm_failures: {failures}',
    'show_logs_wolf_dm_errors': '_wolf_dm_errors: {errors}',
    'show_logs_retrieval_error': 'ログの取得中にエラーが発生しました。',
    
    # DM 通知用メッセージ
    'dm_game_victory_title': '勝利',
    'dm_game_victory_message': 'おめでとうございます！あなたの陣営が勝利しました！',
    'dm_game_defeat_title': '敗北',
    'dm_game_defeat_message': 'あなたの陣営は敗北しました。次回はがんばりましょう！',
    'no_public_logs': '（公開可能なログはありません）',
    'dead_players_public_none': '昨夜の犠牲者はいませんでした。',
    'no_lynch_public': '投票の結果、誰も追放されませんでした。',
    
    # -----------------
    # Enhanced UI messages
    # -----------------
    'game_thread_welcome_title': 'ゲームスレッドへようこそ！',
    'game_thread_welcome_description': 'このスレッドは**ゲーム専用**です。\nゲームの進行状況確認と投票をここで行います。',
    'game_thread_features_title': '**このスレッドでできること：**',
    'game_thread_features_list': '• ゲーム進行状況の確認\n• 昼の投票参加\n• ゲーム中の議論\n• 投票結果の確認',
    'game_thread_dm_notice': '**役職通知とプライベートな指示はDMで届きます**',
    'game_thread_tips_title': 'ヒント',
    'game_thread_tips_content': '• ピン留めされた**進行状況パネル**で現在の状況を確認\n• 投票時間になると投票UIがここに表示されます\n• ゲーム中の質問は運営にDMでお願いします',
    'game_thread_footer': 'それでは人狼ゲームをお楽しみください！',
    'thread_creation_title': 'ゲーム専用スレッド作成',
    'thread_creation_description': '人狼ゲーム用のスレッド {thread_mention} を作成しました！\n\n**ゲームの進行や投票はスレッド内で行います**\n役職などのプライベート情報はDMで送信されます',
    'thread_join_instruction': '参加方法',
    'thread_join_description': '上記のスレッド {thread_mention} に参加してください',
    
    # Game start confirmation messages
    'enhanced_game_start_title': '人狼ゲーム開始！',
    'enhanced_game_start_description': '**{total}人**でゲームが始まりました。役職は個別DMで確認してください。',
    'participant_list_title': '参加者一覧',
    'participant_count_suffix': '人',
    'participant_others': '...他{count}人',
    'faction_werewolf': '人狼陣営',
    'faction_village': '市民陣営', 
    'faction_neutral': '第三陣営',
    'faction_other': 'その他',
    'game_settings_title': 'ゲーム設定',
    'setting_lovers': '恋人: {status}',
    'setting_abstain': '棄権: {status}', 
    'setting_night_timeout': '夜制限: {timeout}',
    'setting_vote_timeout': '投票制限: {timeout}',
    'setting_enabled': '有効',
    'setting_disabled': '無効',
    'setting_possible': '可能',
    'setting_impossible': '不可',
    'setting_no_limit': 'なし',
    'setting_seconds': '{seconds}秒',
    'setting_failed': '設定情報の取得に失敗',
    'enhanced_footer_detailed': '役職詳細はDMを確認 | 進行状況はこのスレッドで更新されます',
    'main_channel_start_title': '人狼ゲーム開始',
    'main_channel_start_description': '**{total}人**のゲームが開始されました！\n詳細と進行状況は専用スレッドでご確認ください。',
    'main_channel_thread_field': 'ゲームスレッド',
    
    # Voice chat and start command messages
    'start_existing_game': '既にアクティブなゲームが存在します。現在のゲームを終了してから再度お試しください。',
    'start_no_voice': 'ボイスチャットに参加してからコマンドを実行してください。',
    'start_insufficient_players': 'ボイスチャットの参加者が不足です。最小{min_players}人必要ですが、現在{current_players}人です。',
    'start_too_many_players': 'ボイスチャットの参加者が多すぎます。最大{max_players}人までですが、現在{current_players}人です。',
    'start_voice_error': 'ボイスチャットの参加者を取得できませんでした。',
    'start_creation_failed': 'ゲームの作成・開始に失敗しました。',
    'game_start_failed_insufficient': 'ゲームの開始に失敗しました。参加者が不足している可能性があります。',
    'generic_game_started': 'ゲームが開始されました！',
    
    # Legacy command message
    'create_command_deprecated': 'このコマンドは /ww_start に統合されました。/ww_start を使用してください。',
    
    # Admin command messages
    'command_channel_only': 'コマンドはチャンネル内で実行してください。',
    'no_game_found': 'このチャンネルで動作するゲームが見つかりません。',
    'owner_only': 'オーナーのみ実行できます。',
    'owner_or_admin_only': 'オーナーまたは権限のあるユーザのみ実行できます。',
    'game_paused': 'ゲームを一時停止しました。タイマーは停止します。',
    'game_resumed': 'ゲームを再開しました。タイマーが進行します。',
    'not_vote_phase': '現在投票フェーズではありません。',
    'game_force_ended_cleanup_partial': 'ゲームを強制終了しました。一部のリソースのクリーンアップに失敗した可能性がありますが、ゲームは停止されました。',
    'specified_voice_channel': '指定されたボイスチャンネル',
    'werewolf_game_thread': '人狼ゲーム {identifier}',
    'werewolf_thread_reason': '人狼ゲーム進行用スレッド',
    'night_action_description': '各役職は夜行動を行ってください。DMをご確認ください。',
    'day_meeting_description': '昼の会議を開始してください。',
    'vote_time_comment': '投票時間です',
    'game_ended_title': 'ゲーム終了！',
    'game_ended_description': 'ゲームが終了しました。お疲れさまでした！',
    'phase_change_title': 'フェーズ変更',
    'current_phase': '現在のフェーズ: **{phase}**',
    'survivor': '生存者: {name}',
    'abstain_label': '棄権',
    'abstain_description': '誰にも投票しない',
    'execute_button_label': '実行',
    'execute_button_instruction': '。実行するには「実行」ボタンを押してください。',
    
    # Faction names
    'faction_werewolf': '人狼陣営',
    'faction_village': '市民陣営', 
    'faction_neutral': '第三陣営',
    'faction_other': 'その他',
    
    # Command descriptions
    'cmd_reload_description': '設定を再読み込みします（管理者のみ）',
    'cmd_start_description': 'ボイスチャット参加者で人狼ゲームを開始します',
    'param_max_players_description': '最大参加者数（デフォルト: 15人）',
    'param_min_players_description': '最小参加者数（デフォルト: 4人）',
    
    # Statistics recording
    'stats_record_button': '記録する',
    'stats_skip_button': '記録しない',
    'stats_recorded': '統計を記録しました。',
    'stats_not_recorded': '統計は記録しませんでした。',
    'stats_timeout': '確認がタイムアウトしました。統計は記録されません。',
    'stats_confirm_question': '{owner} この試合の統計を記録しますか？\n（参加者 {players}名、勝利者 {winners}名）',
    
    # Statistics DM
    'stats_dm_title': 'あなたの統計',
    'stats_dm_total_games': '総試合数: **{total}**回',
    'stats_dm_total_wins': '勝利数: **{wins}**回',
    'stats_dm_win_rate': '勝率: **{rate:.1f}%**',
    'stats_dm_footer': 'お疲れさまでした！',
}


def msg(key: str, **kwargs) -> str:
    """Retrieve and format a message from MESSAGES. Falls back to the key if missing."""
    template = MESSAGES.get(key, key)
    try:
        return template.format(**kwargs)
    except Exception:
        # If formatting fails for any reason, return the unformatted template
        # (this mirrors the previous safe behavior).
        return template
