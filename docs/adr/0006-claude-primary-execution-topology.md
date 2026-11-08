# ADR-0006: Claude主体の実行構成、host所有のMain、Ask二分類、v3廃止

- **Status:** Proposed — Humanの設計選択は2026-11-09に会話上で確定。本ファイルのレビュー・Mergeは未了
- **Human decision:** 案Y（Claude主体）を採用。未確定4論点＋同時実行数について推奨案をすべて承認（2026-11-09 JST）
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

1. **Claude標準subagentは読み取り専用の調査に限る。** `tools`から編集系と`Agent`を外す。加えて`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`で入れ子を禁止する。Claude Codeは既定で、subagentがさらに3段までsubagentを起動できるためである。
2. **ファイルを編集するLeafと独立レビューは、すべてhostのLeaf窓口（MCPツール）を経由する。**
   - 入力：TaskSpecと許可範囲
   - 出力：`COMPLETED` / `BLOCKED` / `NEEDS_APPROVAL` / `NEEDS_DECISION`
   - 裏側でClaude（`claude -p`）かOpenCode（`opencode run`）を別プロセスとして起動する。
   - Leafの完了後、hostが変更範囲・Contract・テスト結果を検証する。
3. **独立レビューはOpenCode + TSUMGI上のローカルLLMで行う。** 作り手（Claude）と検証役のモデル系統を分けるためである。レビュー用の環境は読み取り専用のsnapshotを使い、編集・Web・Bash・委譲は許可しない（#317 B2の方針を継承）。

### D4 — Askの二分類とhost窓口への一本化

| 種類 | 発生源 | 経路 | 人間に届く条件 |
| --- | --- | --- | --- |
| 権限Ask | ツール実行の許可確認（Claudeの`--permission-prompt-tool`、OpenCodeのPermission API） | host窓口へ直接届く | hostがContractと照合し、範囲内なら自動許可、範囲外なら自動拒否してagentに返す。ADR-0001が定めるAsk対象の操作だけを人間に回す |
| 判断Ask | `NEEDS_DECISION` / `NEEDS_APPROVAL` | Leaf → Task Orchestrator（範囲・権限・代替案を再評価）→ host窓口 | Task Orchestratorが自分で解決できないもの |

- host窓口だけがAskの受付・照合・回答の正本を持つ。MUSUBIは表示と回答入力のclientであり、Askを管理しない（#292）。
- 拒否された操作を、言い換えて人間へのAskに変換してはならない（不変条件13を維持）。
- 不変条件3・11・12・13は変更しない。

### D5 — 外部設計者と承認の作法

- 設計と議論は、Userとclaude.ai Project「KUMIKI / KAGARI」で行う。Issueの草案作成はClaude Codeの設計用skillで補助してよい。
- Issueのテンプレートには**要件・受入条件・非目標・決定済み事項**の4欄を必須とする。欠けたIssueはMainの対象外にする。
- **承認とは、Userが承認ラベル（名称は実装時に決める。例：`kagari:approved`）を付けることである。** Mainはこのラベルが付いたIssueだけを対象にする。
- 承認依頼は、5行以内の要約・差分行数・人間が読むべきファイル・却下した場合の影響、の4点を必ず含める。欠けている依頼は中身を見ずに差し戻す。

### D6 — 同時実行数

- Claude Task Orchestratorの同時実行数は**1**から始め、Claude経路でE2E-01が通ったら**2**に上げる。それ以上は利用実績を見て別途決める。
- OpenCode Leafの同時実行数はTSUMGIのGPU容量で決める。
- これは#288の「N/C上限を強制しない」を、Claudeセッションに限って改める。

### D7 — Agent Core v3の廃止手順

1. v3は保守凍結とする。新機能は入れない。実ユーザーを止める不具合だけを、例外として明示したうえで最小限修正する。
2. 移行対象はRITSU、MUSUBI、Terreate、blog-upiscium-devの4件。KASANEは停止中のため移行しない。
3. 移行の開始条件は、D8のリリースと#256の実Projectへの導入が完了していること（日付では決めない）。
4. v3からの移行ツール（#142）はplan（読み取り専用）とapplyに分け、ADR-0004の所有権ルールに従う。
5. RITSUで試験移行し、問題を直してから残りを一つずつ移す。全件が完了したら#197でv3を削除する。

### D8 — 次のリリースの条件

- 次のリリースは**Claude経路でのE2E-01**（Issue 1件 → worktree → Task Orchestrator → Leaf → テスト・レビュー → Draft PR）を必須とする。
- 具体的な受入範囲は#291のAC-MAIN-01〜08から選んで本ADRの追補に記録する（**未決定**）。

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
| #317 / #364 | ClaudeのPreToolUse hookとコマンド構文の解析で、根本的に解決できるかを先に確かめる |
| #332〜#336 | 引き続き保留 |

### 既知の限界

- **承認ラベルの真正性：** agentとUserは同じGitHub CLI認証を共有している（2026-11-07の決定）。そのためGitHub上の操作者からは、ラベルを付けたのが人間かagentかを区別できない。悪意あるagentは脅威モデルの範囲外（ADR-0001）なので、agentがラベルを付けられないよう**権限ルールとhookで禁止する**ことで担保する。これは暗号的な証明ではない。
- **個人ログインとmod：** managed settingsが無く個人ログインで動かす場合、インストールしたmodがdenyルールを覆して承認できる。host側のeffective policy検証に、mod・pluginの有無を含める。
- **利用枠と規約：** 個人サブスクリプションの利用上限と、自動実行の規約上の扱いは未確認である。
- **モデル系統の同一性：** Claudeの標準subagentによる調査は作り手と同じ系統になる。独立性が必要な検証には使わない。

## Acceptance evidence required before calling this implemented

実機確認（合成テストではなく実CLIで行う）:

- (a) worktree内でheadlessのClaude Task Orchestratorを動かし、subagentを含めた権限確認が`--permission-prompt-tool`経由でhost窓口に届くこと。
- (b) Claudeから、MCP経由でOpenCode（TSUMGI）のLeafを起動し、構造化された結果が返り、hostが変更範囲を検証できること。
- (c) subagentの入れ子禁止が効き、AGENTS.mdの読み込みとsubagentへの指示の伝わり方が想定どおりであること。
- (d) 引用符内の`|`・`;`が部分コマンドの区切りとして扱われないこと（#317の再現条件）。
- 以上を満たしたうえで、D8のE2E-01を実Project（使い捨てのclone）で通すこと。

## Follow-up (not decided here)

- 承認ラベルの名称と、agentによる付与を禁止する具体的な設定
- host Leaf窓口（MCPツール）のインターフェース
- D8に含める#291 ACの範囲
- 同時実行数の上限をどこで強制するか（Mainかhost窓口か）
- 引き継ぎ文書をこのADRに合わせて更新すること

本ADRは、Source Publication、Merge、Release、本番適用、またはv3コンシューマの変更を承認するものではない。
