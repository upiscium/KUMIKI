# ADR-0007: Issue起点の最小E2E経路（MVI walking skeleton）をD8より先に通す

- **Status:** Accepted（2026-11-11）; I-1・I-2 実装済み、I-3 は PR #410（D7 追補 2026-11-12）
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
- Contractと照合する権限窓口（`--permission-prompt-tool`のMCP）は、MVIでは**作らない**。headless実行では、allowlistに無い操作は承認されずに拒否される。この性質とLXCという境界で代替する。ただし、この拒否動作は実機で確認する（受入条件）。（Bash の許可範囲と起動設定は D7 で変更）
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

- MVI経路のコードは、**1モジュール（本体500行以内を目安）とREADME 1本**とする。phaseごとの文書は作らない。上限を超えそうになったら、実装を続けずに設計を見直す。（行数の目安は D7 で変更）

### D6 — 独立レビュー

- 1周目は、別プロセスの`claude -p`（新しいコンテキスト、読み取り専用ツールのみ）で行う。作り手と同じモデル系統になる点は、既知の限界として扱う。
- 2周目で、ADR-0006 D3.3のOpenCode + TSUMGIに切り替える。前提として、LXCからTSUMGIに到達できる必要がある。

### D7 — 実装で確定した事項（追補 2026-11-12、Human 判断を含む）

I-1・I-2（#404・#405、PR #408・#409）の実装と LXC での実機確認を通じて、次のことを確定した。「（I-3）」と記した項目は、I-3（#406、PR #410）の merge と実機確認で確定する。D3・D5・D6 と Acceptance evidence の記述と食い違う点は、この節が優先する。

- **commit は runner が行う（D3 の補足）**：Claude（`kagari-agent`）は worktree のファイルを編集するだけとし、git の管理ディレクトリには書き込めないようにする。`kagari-runner` が commit と push を行う。commit では git の管理ディレクトリを明示し（`--git-dir`）、hooks と fsmonitor を無効にする。
  - 理由：`kagari-agent` が `.git` の設定やフックに書き込めると、runner が git を実行したときに、GitHub の資格情報を持ったままそのコードが動くため。
- **テストは runner が再実行し、`kagari-agent` として動かす（D3 の補足）**：合否は runner の再実行結果だけで決める。再実行では次の 4 つを行う。
  - 事前に `kagari-agent` のプロセスを止める
  - commit した内容だけを別の場所に checkout し、そこでテストを動かす
  - ログインシェルを使わず、HOME は使い捨てにする
  - `timeout -k` で時間を区切る
- **Claude の起動設定（D3 の追加）**：`--tools`（実装は `Bash,Read,Edit,Write,Glob,Grep`、レビューは `Read,Grep,Glob`（I-3））、`--restricted`、`--permission-prompts none` を付ける。`--restricted` が user・project・local の設定ファイルを読まないので、`--setting-sources` は使わない。
  - 理由：実機で確かめたところ、`--tools` を付けないと 20 個ほどのツール（RemoteTrigger、CronCreate など）が使える状態だった。
- **Bash の許可範囲（Human 判断 2026-11-11、D3 の変更）**：Bash は原則として許可する。ネットワークと資格情報に関わるコマンド（`curl`、`gh`、`git push`、`ssh`、`sudo` など）だけを拒否リストで拒否する。
  - 安全の境界は OS のユーザー分離であり、拒否リストは誤操作を防ぐためのものとする。
  - 読み取り専用のコマンド（`uname` など）は、許可リストに無くても承認なしで実行される。これは実機で確認した。
- **ホストのセットアップはホストごとに 1 回（Human 判断）**：内容は、共有グループ 1 つ、sudoers に 1 行、`/srv/kagari` とその下の 3 つのディレクトリである。リポジトリごとの手作業は作らない。手順、削除の手順、NixOS での設定例は `components/kagari-mvi/README.md` に置く。
- **行数の目安（Human 判断 2026-11-11、D5 の変更）**：本体は 2 ファイル（GitHub 側とプロセス側）で、合わせて 700 行以内とする。README は 1 本のままとする。
- **独立レビューの起動（D6 の補足、I-3）**：`review-once` は `run-once` とは別のコマンドとする。FAIL があっても `blocked` にはせず、`kagari:reviewed` を付ける。merge するかどうかは User が判断する。
- **認証エラー（#405 の追加要件）**：認証エラーは、実装の失敗と区別する。`blocked` を付けて再ログインを求め、リトライはしない。`--bare` は使わない。

## Acceptance evidence

- 使い捨てのリポジトリを対象に、E2E-MVI-01を実モデルで一周させること。KUMIKI自身を対象にするのは、一周できた後とする。
- `kagari-agent`のセッションから、`gh`とgit pushが失敗すること（資格情報が無いこと）。
- headless実行で、拒否リストにあるBashコマンドが拒否され、セッションが止まらずに続くか、BLOCKEDとして終わること（D7 で allowlist 方式から変更）。
- workerを途中で止めて再起動しても、`kagari:running`のIssueを二重に取得しないこと。取得しない代わりに`blocked`へ倒すことは許容する。
- `upiscium`以外が付けた`kagari:approved`は処理されないこと。

**実施状況（2026-11-11〜12、専用 LXC の `kagari-runner` ユーザー、対象 `upiscium/kagari-mvi-sandbox`。I-2 以降は Claude CLI 2.1.322）：** 各行の出力は PR #411 のコメントに記録する。

| 項目 | 結果 |
| --- | --- |
| `kagari-agent` から `gh` と git push が失敗する | PASS（本来の HOME とログインシェルで `gh=1`、`push=128`） |
| 拒否するコマンドが拒否され、セッションが止まらない | PASS（`curl` が `permission_denials` に記録され、25 秒で終了） |
| `kagari:running` の Issue を二重に取得しない | PASS（I-1） |
| `upiscium` 以外が付けた承認は処理されない | PASS（I-1、許可リストを変えて確認） |
| I-2：Issue から、テストが通った Draft PR ができる | PASS（sandbox PR #17） |
| 認証エラーで blocked にし、再ログインを求める | PASS（sandbox Issue #20） |
| I-3：受入条件ごとの独立レビュー | 未確認（PR #410 の後に実施） |
| E2E-MVI-01 の一周 | 未確認 |

## Known limitations / unverified

- OAuth（サブスクリプション）をheadlessのLXCで長期間使い続ける認証の持たせ方は未確認である。利用枠はUserの対話と同じ枠を消費する（ADR-0006 Follow-up）。
- 1周目のレビューは同じモデル系統で行うため、独立性は限定的である。
- 承認後にIssue本文が変わっても検知しない。
- Bash を原則として許可しているため、Claude は `kagari-agent` の Claude 資格情報を読める。Issue 本文で誘導されると、その資格情報を外部へ送られうる。
- runner が push すると、target リポジトリの GitHub Actions が、Claude の書いたコードを secrets 付きで実行しうる。secrets を持つリポジトリを対象にする前に、対策を決める。

本ADRは、Merge、Release、本番適用、既存dispatch経路の変更を承認するものではない。
