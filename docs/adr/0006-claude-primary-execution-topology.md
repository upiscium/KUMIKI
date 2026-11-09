# ADR-0006: Claude主体の実行構成、host所有のMain、Ask二分類、v3廃止

- **Status:** Accepted — PR #392で2026-11-09にMerge。同日の追補（D4aのプラグイン方針、Follow-upの追記）を含む
- **Human decision:** 案Y（Claude主体）を採用。未確定4論点＋同時実行数について推奨案をすべて承認。D8のリリース定義、#291 ACの扱い、v1 metadata経路の採用を承認（いずれも2026-11-09 JST）
- **Related:** ADR-0001/0004/0005、#291、#340、#292、#339、#288、#359、#317、#364、#337、#256、#194、#197、#142
- **Supersedes:** 引き継ぎ文書の「OpenCodeがdefault、Claudeは任意executor」という前提、#340の「optional executor」という位置付け、#288の「N/C上限を強制しない」のうちClaudeセッション部分

## Context

v0.1.0 bounded GA以後、MVI 8項目（#269、2026-10-23）を目標に、Main（#291）、Control Server（#339）、Claude executor（#340）、Metadata v2（#337）、model binding（#359）が並行して進んだ。その結果、次の状態になっている。

- OpenCode前提の設計（`opencode serve`のPermission/Question API、`opencode.json`のglob deny、OpenCode export Evidence）と、Claude対応が二重に進んでいる。
- 設計・調整役のArca（ChatGPT + Codex）が廃止予定になり、外部設計者（MVI目標8）の担い手が空白になる。
- Agent Core v3は完全廃止の方針だが、移行先の実行構成が未定のため、移行作業の中身を決められない。
- 「Leafから直接Ask」を許すと、不変条件11・12（Leafは非対話、Depth-1が再評価する）と衝突する。

## Decision

### D1 — 実行構成（案Y）

```
User ──承認ラベル──▶ Issue（要件・受入条件・非目標・決定済み事項）
                        │
                KAGARI Main（host コード）
                        │  Issue 1件 = worktree 1つ
                Task Orchestrator（Claude Code）
                 ├─ 調査 Leaf：Claude 標準 subagent（読み取り専用・入れ子禁止）
                 ├─ 編集 Leaf：host Leaf窓口 → Claude または OpenCode
                 └─ レビュー Leaf：host Leaf窓口 → OpenCode + TSUMGI（ローカルLLM）
```

- Task Orchestratorの実行基盤はClaude Code（公式CLI、利用者本人の認証）とする。
- OpenCodeは廃止しない。TSUMGI経由のローカルLLMを使うLeaf実行基盤として残す。
- Arcaは廃止する。`codex-repo-state`形式のIssueコメント記録は新規に作らない。

### D2 — KAGARI Mainはhostコードとする

- Mainは決定的に動くhostの常駐プロセスとし、LLMセッションにはしない（#291のMainSupervisorと一致）。
- 対象Issueの選択は**ルールで決める**: 承認ラベルがあり、immutable Contractが確定し、D5のテンプレート要件を満たすIssueだけを対象とする。
- Mainの判断にLLMを使う拡張（Issue間の依存推定など）は、必要性が実証されてから別のADRまたはIssueで決める。

### D3 — Leafの実行方式

1. **Claude標準subagentは読み取り専用の調査に限る。** `tools`から編集系と`Agent`を外す。加えて`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1`で入れ子を禁止する。Claude Codeは既定で本体の下に3層までsubagentを起動できる（subagentからさらに起動できるのは2段まで。実機で確認したのは2層目まで）。この環境変数は親プロセスから継承されるため、hostは継承に頼らず毎回明示的に設定する。
   - 組み込みの汎用subagentは無効にする。読み取り専用の制限を迂回されないためである。
   - 背景実行のsubagent（`background` / `run_in_background`）は禁止する。背景subagentがあるとstream-jsonの`result`イベントが複数回出て、完了判定を誤らせるためである。
2. **ファイルを編集するLeafと独立レビューは、すべてhostのLeaf窓口（MCPツール）を経由する。**
   - 入力：TaskSpecと許可範囲
   - 出力：`COMPLETED` / `BLOCKED` / `NEEDS_APPROVAL` / `NEEDS_DECISION`
   - 裏側でClaude（`claude -p`）かOpenCode（`opencode run`）を別プロセスとして起動する。
   - Leafの完了後、hostが変更範囲・Contract・テスト結果を検証する。
3. **独立レビューはOpenCode + TSUMGI上のローカルLLMで行う。** 作り手（Claude）と検証役のモデル系統を分けるためである。レビュー用の環境は読み取り専用のsnapshotを使い、編集・Web・Bash・委譲は許可しない（#317 B2の方針を継承）。

### D4 — Askの二分類とhost窓口への一本化

| 種類 | 発生源 | 経路 | 人間に届く条件 |
| --- | --- | --- | --- |
| 権限Ask | ツール実行の許可確認（Claudeの`--permission-prompt-tool`、OpenCodeのPermission API） | 設定のallow / denyルールで決まらなかった確認だけがhost窓口に届く | hostがContractと照合し、範囲内なら自動許可、範囲外なら自動拒否してagentに返す。ADR-0001が定めるAsk対象の操作だけを人間に回す |
| 判断Ask | `NEEDS_DECISION` / `NEEDS_APPROVAL` | Leaf → Task Orchestrator（範囲・権限・代替案を再評価）→ host窓口 | Task Orchestratorが自分で解決できないもの |

- host窓口だけがAskの受付・照合・回答の正本を持つ。MUSUBIは表示と回答入力のclientであり、Askを管理しない（#292）。
- 拒否された操作を、言い換えて人間へのAskに変換してはならない（不変条件13を維持）。
- 不変条件3・11・12・13は変更しない。
- **Task セッションのallowルールは読み取り系に限る。** allowやdenyのルールに一致した操作は窓口に届かず、Contractとの照合が効かないためである（実機確認A6・A7）。それ以外の操作はすべて窓口の判定に回す。
- 窓口に届く要求には、本体とsubagentを区別する情報が含まれない（`tool_name`・`input`・`tool_use_id`のみ）。D3.1によりsubagentは読み取り系のツールしか持たないため、窓口は届いた要求をTask Orchestratorのものとして、同じContractで判定する。発生元の区別が必要になった場合は、stream-jsonの`parent_tool_use_id`との突き合わせで行う。
- 窓口が応答しない、異常終了する、不正な応答を返す場合、操作は拒否されなければならない。不正な応答で拒否されることは確認済み（A10）。異常終了とタイムアウトは未確認であり、実装時の受入条件とする。

### D4a — Task 規則の渡し方と起動環境

- **Task の規則は、起動時の追加システムプロンプトでClaudeに渡す。** KAGARIはProjectの`AGENTS.md`・`CLAUDE.md`・`CLAUDE.local.md`に書き込まない。`CLAUDE.md`または`CLAUDE.local.md`が存在すると`AGENTS.md`が読まれなくなるため、KAGARIがこれらのファイルを置くとProjectの規則を消してしまう（実機確認M2・M4）。
- hostは起動前に、worktreeでどの指示ファイルが有効になるか（`AGENTS.md`のみ、`CLAUDE.md`のみ、`@AGENTS.md`の取り込みあり、など）を診断し、Evidenceに記録する。ADR-0004（AGENTS.mdとの共存）は、Claudeでの読み込み規則を前提に追補する。
- **Task セッションはクリーンな環境変数で起動する。** 親セッションの`CLAUDECODE`・`CLAUDE_CODE_SESSION_ID`などは除去する。MCPは`--strict-mcp-config`でKAGARIが指定したものだけを使う。
- **Task セッションのプラグインは、KAGARIの設定に明示した許可リストで決める（2026-11-09追補）。** 許可リストに無いプラグイン（確認時の`arca-issue-handoff`など）は読み込ませない。許可リストの初期値は空とする。
  - プラグインを一律に禁止はしない。ただし許可リストに加える前に、そのプラグインが持つhooks・MCPサーバ・skillsを確認し、(1) 権限窓口（D4）の判定やdenyルールを迂回しないこと、(2) skillが自動起動してIssueやPRへの書き込み、worktreeの削除など、Task Orchestratorの役割（承認済みIssueの実装）を超える操作を始めないこと、を確かめる。
  - 起動記録には、実際に有効だったプラグインの名前とバージョンを残す。個人profileのプラグインは更新で中身が変わり、同じIssueでも実行日によって振る舞いが変わり得るためである。
  - 設計・承認側の作業（User、claude.ai Project、Adamの設計用Claude Codeセッション）でプラグインを使うことは、この制限の対象外とする（D5）。

### D5 — 外部設計者と承認の作法

- 設計と議論は、Userとclaude.ai Project「KUMIKI / KAGARI」で行う。Issueの草案作成はClaude Codeの設計用skillで補助してよい。
- Issueのテンプレートには**要件・受入条件・非目標・決定済み事項**の4欄を必須とする。欠けたIssueはMainの対象外にする。
- **承認とは、Userが承認ラベル（名称は実装時に決める。例：`kagari:approved`）を付けることである。** Mainはこのラベルが付いたIssueだけを対象にする。
- 承認依頼は、5行以内の要約・差分行数・人間が読むべきファイル・却下した場合の影響、の4点を必ず含める。欠けている依頼は中身を見ずに差し戻す。

### D6 — 同時実行数

- Claude Task Orchestratorの同時実行数は**1**から始める。**2**に上げるのは、Claude経路でE2E-01が通り、かつmetadataをv2（#337）へ移行した後とする。v1は全Issueが1本の共有refに書き込むため、並行実行では書き込みが衝突するからである（D8「実行経路とmetadata」）。それ以上は利用実績を見て別途決める。
- OpenCode Leafの同時実行数はTSUMGIのGPU容量で決める。
- これは#288の「N/C上限を強制しない」を、Claudeセッションに限って改める。

### D7 — Agent Core v3の廃止手順

1. v3は保守凍結とする。新機能は入れない。実ユーザーを止める不具合だけを、例外として明示したうえで最小限修正する。
2. 移行対象はRITSU、MUSUBI、Terreate、blog-upiscium-devの4件。KASANEは停止中のため移行しない。
3. 移行の開始条件は、D8のリリースと#256の実Projectへの導入が完了していること（日付では決めない）。
4. v3からの移行ツール（#142）はplan（読み取り専用）とapplyに分け、ADR-0004の所有権ルールに従う。
5. RITSUで試験移行し、問題を直してから残りを一つずつ移す。全件が完了したら#197でv3を削除する。

### D8 — 次のリリースの条件

**リリース定義（Human承認 2026-11-09）:**

> 承認ラベルの付いたIssue 1件を、Main（host）が選んでworktreeを作り、ClaudeのTask Orchestratorが実装し、OpenCode + TSUMGIのレビューを通し、Draft PRまで作る。続けて2件目のIssueを同じMainで順番に処理する。途中で中断しても二重にdispatchしない。NEEDS_*が出たらBLOCKEDになり、CLIに理由を表示する。

**#291 AC-MAIN-01〜08の扱い:**

| AC | 扱い | 補足 |
| --- | --- | --- |
| 01 単一Main | 含める | 既存のflock・AttachmentLeaseを使う |
| 02 対象Issue選択 | 含める | 明示した番号リストに、D5の承認ラベル判定を加える |
| 03 Issue正本・Contract | 含める | 既存の#191 Contract固定とdrift検知を使う |
| 04 二重割り当て防止 | 含める | 既存の#286 allocatorを使う |
| 05 Taskセッション起動 | 含める（主要作業） | `opencode serve`前提（#163）を、worktree内でheadlessのClaudeを起動する方式に置き換える。現在のMainはClaudeを`CLAUDE_EXECUTOR_UNQUALIFIED`として拒否している |
| 06 進捗とNEEDS_*の上申 | 縮小して含める | 権限AskはhostがContractで自動判定する（D4）。NEEDS_*はTaskをBLOCKEDにしてCLIに表示するだけとし、HumanはIssueを直して新しいContractで再実行する。MUSUBIからの回答と再開は次の版に回す |
| 07 中断からの復旧 | 縮小して含める | 安全に停止すること、再起動しても二重にdispatchしないこと、曖昧な状態ではBLOCKすることに限る。中断した操作をちょうど1回だけ再開する完全な復旧は次の版に回す |
| 08 実Projectでの一連の流れ | 含める | 「configured OpenCode model」をClaudeに読み替える。2件目のIssueは1件目の後に順番に処理し、並行実行は検証しない（D6） |

**リリース範囲外とするもの:**

- MUSUBI経由の生のAsk回答と再開（#292のClaude対応）
- Metadata v2（#337）全体。次期リリースは既存のv1 metadata（`refs/agentcore/metadata`）を使う（下記「実行経路とmetadata」）
- 完全なexactly-once復旧（#332〜#334）
- 複数Taskの並行実行

**実行経路とmetadata（Human承認 2026-11-09）:**

- 次期リリースは、既存のv1 dispatch経路（`RepositoryMain.dispatch`）を土台にする。この経路は、worktreeの確保（#286）、初期Draft PR（#194）、Issueの変更検知、初回プロンプトの二重送信防止までを1本の流れとして持つ、main上で唯一の経路である。
- OpenCodeに依存する3か所（`opencode serve`によるセッション起動、HTTPによるプロンプト送信、OpenCodeのセッションAPIを使った再送時の照合）だけをClaudeの起動に置き換える。
- metadataはv1（`refs/agentcore/metadata`）を使う。v1とv2はTask Record・Contract・Evidence・Task Viewのデータ形式が同じで、違いは保存場所の構造である。v1の弱点である共有refへの並行書き込みの衝突は、同時実行1件（D6）の次期リリースでは表に出ない。
- main上のv2系の部品（v2 readonly / worktree / session / PR-first / product PRの各経路）は互いに排他で、dispatchの流れに統合されていない。v2での実行は`V2_MAIN_EXECUTION_NOT_QUALIFIED`で拒否される。これらの統合はリリース後に行う。
- **v1の dispatch経路のテストはfakeのOpenCodeとfixtureで行われており、実モデルで一連の流れを通した記録は確認できていない。** 次期リリースのE2Eが、この経路の初めての実地検証になる。

**残る主な作業:** AC-05のClaude起動、権限Askの自動判定を行うhost窓口（`--permission-prompt-tool`のMCP）、レビュー用Leafのhost窓口（OpenCode + TSUMGI）。作業量の見積もりは、実機確認(a)〜(d)の結果を受けて行う。

## Consequences

### Issueの扱い

| Issue | 扱い |
| --- | --- |
| #340 | 任意executorから中核に変える。Task Orchestratorと編集Leafの実行経路になる |
| #291 | 継続。D2（hostコード、ルールで選択）を前提にする |
| #292 | Askの受付をhost窓口に一本化し、Claude側（MCPの`--permission-prompt-tool`）とOpenCode側の両方の入口をつなぐ |
| #339 | MUSUBIからの入口としては残す。「OpenCode workerの並列実行」部分は見直す |
| #359 | OpenCode Leafでは引き続き必要だが、優先度は下げる |
| #288 | D3・D6に合わせて改める |
| #317 | 実機確認(d)で、引用符内の`\|`・`;`による誤検知はClaude経路では構文解析で解消されることを確認した。v3の実害は手動での回避と移行で解消する。v4向けのA2（型付き操作）・B2（隔離レビュー）は次期リリースの前提にしない |
| #364 | Git読み取り時の外部コード実行は、Claude経路でも窓口のContract判定で扱う。PreToolUse hookによる対処は未確認 |
| #332〜#336 | 引き続き保留 |
| #337 Metadata v2 | 次期リリースはv1を使うため、リリースまで新しい作業を止める（P3Cの#391を含む）。リリース後、同時実行数を2に上げる前にv2へ移行する |

### 既知の限界

- **承認ラベルの真正性：** agentとUserは同じGitHub CLI認証を共有している（2026-11-07の決定）。そのためGitHub上の操作者からは、ラベルを付けたのが人間かagentかを区別できない。悪意あるagentは脅威モデルの範囲外（ADR-0001）なので、agentがラベルを付けられないよう**権限ルールとhookで禁止する**ことで担保する。これは暗号的な証明ではない。
- **個人ログインとmod：** managed settingsが無く個人ログインで動かす場合、インストールしたmodがdenyルールを覆して承認できる。host側のeffective policy検証に、mod・pluginの有無を含める。許可リスト外のプラグインを読み込ませない具体的な指定方法は未確認。
- **`python3`のallowルール：** `python3 -c '…'`や`python3 ok.py`は、exactなallowルールでも承認要求になった。原因は未確認。窓口経由では実行できる（A8）ため、テスト実行は窓口の判定に任せる。
- **利用枠と規約：** 個人サブスクリプションの利用上限と、自動実行の規約上の扱いは未確認である。
- **モデル系統の同一性：** Claudeの標準subagentによる調査は作り手と同じ系統になる。独立性が必要な検証には使わない。

## Acceptance evidence required before calling this implemented

実機確認（合成テストではなく実CLIで行う）:

- (a) worktree内でheadlessのClaude Task Orchestratorを動かし、subagentを含めた権限確認が`--permission-prompt-tool`経由でhost窓口に届くこと。
- (b) Claudeから、MCP経由でOpenCode（TSUMGI）のLeafを起動し、構造化された結果が返り、hostが変更範囲を検証できること。
- (c) subagentの入れ子禁止が効き、AGENTS.mdの読み込みとsubagentへの指示の伝わり方が想定どおりであること。
- (d) 引用符内の`|`・`;`が部分コマンドの区切りとして扱われないこと（#317の再現条件）。
- 以上を満たしたうえで、D8のE2E-01を実Project（使い捨てのclone）で通すこと。

**実施状況（2026-11-09）：** (a)〜(d)はClaude CLI 2.1.293、OpenCode 1.18.35、`tsumgi/qwen3.8-27b`で実施し、すべてPASS。詳細と証拠の場所はPR #392のコメントを参照。結果を受けてD3.1、D4、D4aを修正した。

**未確認（実装時の受入条件とする）:**

- 権限Askの窓口が異常終了したとき、およびタイムアウトしたときに、操作が拒否されること
- 入れ子の上限（3層目）の実際の挙動
- 許可リストどおりにプラグインを読み込ませた起動で、deny規則とContract判定が想定どおりに効くこと
- レビュー用Leafが読み取り専用のsnapshotで動くこと

## Follow-up (not decided here)

- 承認ラベルの名称と、agentによる付与を禁止する具体的な設定
- host Leaf窓口（MCPツール）のインターフェース
- 同時実行数の上限をどこで強制するか（Mainかhost窓口か）
- **MUSUBIからの承認（2026-11-09追記）：** MainがAPIを公開し、MUSUBIから承認ラベルを付けられるようにする。承認APIは、agentから届かない資格情報、Taskセッションからの到達拒否、承認経路のEvidenceへの記録、Issueの4欄が揃わなければ承認不可、の4条件で守る。D8の後に別のADRで決める。
- **利用枠（2026-11-09追記）：** サブスクリプション認証の`claude -p`は、Userの対話と同じ5時間枠・週枠を消費する。使用率が設定した割合を超えたら新しいdispatchを止める仕組みと、上限到達時に`BLOCKED(RATE_LIMITED)`にしてリセット後に次のIssueを新しくdispatchする仕組みを、D8の後に検討する。途中からの再開は行わない。headlessで使用率と上限到達を機械的に取れるかは未確認（実機確認(e)）。
- **認証経路（2026-11-09追記）：** Max以上のプランの月次APIクレジットを、APIキー経由の`claude -p`に使えるかは、公式文書の間で記述が食い違っており未確認。使えなくても本ADRの構成は変えない。切り替える場合は、hostがdispatchごとに環境変数で認証経路を選び、Evidenceに記録する。

本ADRは、Source Publication、Merge、Release、本番適用、またはv3コンシューマの変更を承認するものではない。
