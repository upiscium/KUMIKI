# ADR-0007: Issue起点の最小E2E経路（MVI walking skeleton）をD8より先に通す

- **Status:** Proposed（下書き 2026-11-11）
- **Human decision:** 案B（KUMIKI内に最小E2E経路を新設し、ADR-0006 D8より先に通す）を採用（2026-11-11 JST）。承認ラベルの付与をagentに許さないADR-0006の制約は維持する。
- **Issues:** #403（I-0 LXC）、#404（I-1 骨格）、#405（I-2 Claude実装）、#406（I-3 レビュー）
- **Related:** ADR-0001、ADR-0005、ADR-0006（D1〜D8）、#291、#340
- **Amends:** ADR-0006 D8の実施順序。D8のリリース定義そのものは廃止しない

## Context

- KUMIKI/KAGARIは開発を始めてから長いが、実モデルでIssueからPRまでを一周させたことがない。ADR-0006 D8にも、v1のdispatch経路は「fakeのOpenCodeとfixtureでしかテストしていない」とある。
- D8の残作業は重い。具体的には、Claudeの起動（AC-05）、Contractと照合して権限Askを判定するhost窓口（MCP）、レビュー用Leafの窓口がある。どれも、一周した実績が無い経路の上に積み上げる作業である。
- 速度を決めていたのは、実装の難しさよりも手続きの量だった。段階ごとの文書、細分化したphase、どの段階にも同じ重さでかかる関門がそれにあたる。
- 開発方式はアジャイル＋スパイラルとする。まずMVIで欲しい機能が最小構成で動くことを確かめ、その後に機能を足すか、同じ機能を作り直して洗練させる。

## Decision

### D1 — MVIの定義（E2E-MVI-01）

次の経路が、実モデルで一周すること。

1. Userがclaude.aiの設計者Claudeに要求を話す
2. 設計者Claudeが設計し、Issueとして起票する（4欄テンプレート。ADR-0006 D5）
3. Userが承認ラベル`kagari:approved`を付ける
4. 専用LXCのworkerがIssueを選び、Claude Codeが実装し、テストを通し、Draft PRを作る
5. 別のセッションが独立レビューを行い、受入条件ごとのPASS/FAILをPRにコメントする
6. 設計者ClaudeがPRとレビューを読み、承認依頼の4点（ADR-0006 D5）にまとめる
7. UserがOK（merge）またはNG（コメントしてやり直し）を出す

### D2 — 新しい経路を既存のdispatch経路から分離する

- 実装は`components/kagari-mvi/`に置く（名前は仮）。既存の`RepositoryMain.dispatch`、v1/v2 metadata、allocator、flockには依存しない。
- **状態はGitHub Issueのラベルだけで持つ。** データベース、ref、ローカルの状態ファイルは使わない。

| ラベル | 付与者 | 意味 |
| --- | --- | --- |
| `kagari:approved` | **Userのみ** | 実装してよい |
| `kagari:running` | worker | 実装中（二重に取得しないためのロック） |
| `kagari:implemented` | worker | Draft PR作成済み |
| `kagari:reviewed` | worker | レビューコメント投稿済み |
| `kagari:blocked` | worker | 失敗・判断待ち。理由をIssueにコメントする |

- workerは、外から接続を受けずにGitHubを見に行く方式（systemd timerによるポーリング）で動く。同時実行数は1とする（ADR-0006 D6と同じ）。
- 正とする経路：**E2E-MVI-01が通るまでは`kagari-mvi`を正とする。** 既存のdispatch経路には手を入れない。MVIが通った後、次の周回で「`kagari-mvi`を育てる」か「D8経路へ部品を移す」かを、別のADRで決める。決めずに二本を並行して保守することはしない。

### D2a — ADR-0006 W1〜W5 の凍結（Human 承認 2026-11-11）

- #393〜#397（W1〜W5）は凍結する。Draft PR #399（W1）と #402（W2）は、close せずに Draft のまま残す。
- 凍結は、E2E-MVI-01 が通った後に再開・統合・close のどれにするかを決めるまで続く（D2 の「正とする経路」の判断と同時に行う）。
- #399 にある headless 起動の実装と実機確認の結果は、I-2 を実装するときの参考資料にしてよい。ただし、取り込む量は D5 の行数の目安に収める。

### D3 — 安全の境界はLXCとUnixユーザーの分離に置く

- 実行場所はArc上の専用LXCとする。Adamでは動かさない。
- LXCの中で2つのユーザーを分ける。
  - `kagari-runner`：GitHubの資格情報（gh / git push）を持つ。workerのhostコードを動かす。
  - `kagari-agent`：Claude CodeのOAuth資格情報だけを持つ。Claude Codeのセッションを動かす。**GitHubの資格情報は持たせない。**
- このため、Claudeのセッションはラベルを付けられず、pushもPR操作もできない。ADR-0006の既知の限界「承認ラベルの真正性」も、この分離で担保する。
- Contractと照合する権限窓口（`--permission-prompt-tool`のMCP）は、MVIでは**作らない**。headless実行では、allowlistに無い操作は承認されずに拒否される。この性質とLXCという境界で代替する。ただし、この拒否動作は実機で確認する（受入条件）。
- ADR-0006の実機確認で得た起動設定は流用する。
  - クリーンな環境変数で起動する
  - 規則は`--append-system-prompt`で渡し、Projectの`AGENTS.md`・`CLAUDE.md`は書き換えない
  - `--strict-mcp-config`を指定し、MCPは空にする
  - プラグインの許可リストは空にする
  - `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1`を設定する

### D4 — Issueの入力を固定する

- KUMIKIはpublicリポジトリなので、第三者がIssueにコメントを書ける。workerがClaudeに渡すのは、**取得時点のIssue本文とタイトルだけ**とする。コメントは渡さない。
- 承認ラベルを付けたのがUserであることを、timeline APIの`labeled`イベントで確かめる。actorが許可リスト（`upiscium`）に含まれない場合は`kagari:blocked`にする。
- 取得したIssue本文はPRの説明欄に全文を転記する。承認後に本文が変わったかどうかは、MVIでは検知しない（後回し）。

### D5 — 関門の重さはスパイラルの段階に合わせる

| 段階 | 関門 |
| --- | --- |
| MVI（今回） | テストの実行結果、レビューコメント、Userによるmerge判断。この3つだけ |
| 機能追加・再設計の周回 | 必要に応じて、Evidence、Contract固定、独立系統のレビューなどを足す。足すたびに、何の誤操作を防ぐのかを1行で書く |

- MVI経路のコードは、**1モジュール（本体500行以内を目安）とREADME 1本**とする。phaseごとの文書は作らない。上限を超えそうになったら、実装を続けずに設計を見直す。

### D6 — 独立レビュー

- 1周目は、別プロセスの`claude -p`（新しいコンテキスト、読み取り専用ツールのみ）で行う。作り手と同じモデル系統になる点は、既知の限界として扱う。
- 2周目で、ADR-0006 D3.3のOpenCode + TSUMGIに切り替える。前提として、LXCからTSUMGIに到達できる必要がある。

## Acceptance evidence

- 使い捨てのリポジトリを対象に、E2E-MVI-01を実モデルで一周させること。KUMIKI自身を対象にするのは、一周できた後とする。
- `kagari-agent`のセッションから、`gh`とgit pushが失敗すること（資格情報が無いこと）。
- headless実行で、allowlistに無いBashコマンドが拒否され、セッションが止まらずに続くか、BLOCKEDとして終わること。
- workerを途中で止めて再起動しても、`kagari:running`のIssueを二重に取得しないこと。取得しない代わりに`blocked`へ倒すことは許容する。
- `upiscium`以外が付けた`kagari:approved`は処理されないこと。

## Known limitations / unverified

- OAuth（サブスクリプション）をheadlessのLXCで長期間使い続ける認証の持たせ方は未確認である。利用枠はUserの対話と同じ枠を消費する（ADR-0006 Follow-up）。
- 1周目のレビューは同じモデル系統で行うため、独立性は限定的である。
- 承認後にIssue本文が変わっても検知しない。

本ADRは、Merge、Release、本番適用、既存dispatch経路の変更を承認するものではない。
