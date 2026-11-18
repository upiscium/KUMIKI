# kagari-mvi — Issue 起点の最小 E2E worker（ADR-0007）

ADR-0007 の MVI 経路で動く worker です。承認された Issue を 1 件取り、Claude Code に実装させます。その後、テストを実行し直し、Draft PR を作ります（`run-once`）。Draft PR ができた Issue は、別の Claude セッションが受入条件ごとにレビューします（`review-once`）。

- **構成**：1 ファイルあたり、本体は 500 行以内、テスト（`test_*.py`）は 800 行以内に保ちます。`just mvi::test` の `LineLimitTest` がこの上限を確かめます。超える場合は責任の単位でファイルを分け、理由を PR に書きます（ADR-0010 追補 2026-11-16。それまでの、本体 2 ファイルの合計に対する上限から変更）。`kagari_mvi.py` が GitHub とラベルの処理、`kagari_mvi_exec.py` が Claude・テスト・commit の処理、`kagari_mvi_cleanup.py` が merge 後の片付け（`cleanup-once`）を受け持ちます。テストは、`test_kagari_mvi.py`（Issue の選択・承認・本文と申告の読み取り・TSUMGI・行数の上限）、`test_kagari_mvi_run.py`（`run-once`）、`test_kagari_mvi_review.py`（`review-once`）、`test_kagari_mvi_cleanup.py`（Ready 化と `cleanup-once`）に分け、共通の偽の runner と Issue の見本は `kagari_mvi_testkit.py` に置きます。`just mvi::test` と `python3 components/kagari-mvi/test_kagari_mvi.py` は、どちらもすべてのテストのファイルを実行します。使うのは、Python の標準ライブラリと、subprocess から呼ぶ `gh api`（REST）・`git`・`sudo`・`claude` だけです。GraphQL は、Issue 本文を最後に編集した時刻を取る所（User 判断 2026-11-14）と、PR を Draft から Ready にする所（#432）の 2 か所だけで使います。どちらも REST に同じ機能が無いためです。
- **TSUMGI の直接呼び出し**（#434、ADR-0010 D1）：編集を伴わない Leaf（今はレビュー）が使う TSUMGI の呼び出しは、`kagari_mvi_tsumgi.py` に分けています。分けたときは、本体 2 ファイルが当時の合計の上限に達していました（ADR-0010 Consequences）。
- **状態の持ち方**：状態は GitHub Issue のラベルだけで持ちます（ADR-0007 D2）。DB、ref、ローカルの状態ファイルは使いません。既存の dispatch 経路と metadata にも依存しません。
- **実行の単位**：1 回の実行で処理する Issue は最大 1 件です。systemd timer などから定期的に呼ぶ前提です。
- **ユーザーの分担**（D3）：
  - `kagari-runner`（GitHub の資格情報を持つ）が worker を動かします。
  - `kagari-agent`（Claude の資格情報だけを持つ）が、Claude とテストコマンドを動かします。
  - git の commit と push は `kagari-runner` が行います。
- **1 つのホストに 1 つの KAGARI**：複数のリポジトリを、同じホストの 1 つの KAGARI で扱います。worktree・レビュー用の checkout・記録は `<workdir>/wt/<owner>__<repo>/`・`<workdir>/logs/<owner>__<repo>/` のようにリポジトリごとに分けるので、別のリポジトリに同じ番号の Issue があってもぶつかりません。ただし、`run-once`・`review-once`・`cleanup-once` は、同じホストで同時に動かさないでください。テストの前に `kagari-agent` のプロセスをすべて止めるので、もう一方の実行を止めてしまいます（リポジトリを順に回す 1 つの timer で動かす）。
  - **古い置き場所からの移行**：この変更より前に作った `<workdir>/wt/issue-N` の worktree は、clone に登録されたまま残ります。残っていると、同じ番号の Issue を処理するときに新しい場所で worktree を作れず、止まります。移行するときに、リポジトリごとに一度だけ消してください（`kagari-runner` として、`for w in /srv/kagari/wt/issue-*; do git -C /srv/kagari/repos/O__R worktree remove --force "$w"; done; git -C /srv/kagari/repos/O__R worktree prune`。消えずに残ったディレクトリは `rm -rf` してから `prune` する）。
- **実行環境の前提**：同じホスト上の OS のユーザー分離（上の 2 ユーザー）だけです。2 つのユーザーは KAGARI 専用にしてください。LXC は MVI の実機確認に使った環境で、必須ではありません（ADR-0009 D2）。

## KAGARI が責任を持つこと（ADR-0009 D1）

KAGARI の目的は、簡単かつ安全に Agent 開発をすることです。攻撃への防御は目的にしません。

- **責任を持つこと**：
  - 人間の判断の関門。承認ラベルを付けるのも、merge するのも人間です。
  - GitHub の資格情報の分離。Claude は GitHub の資格情報を持たず、commit・push・PR・ラベルは runner が扱います。
  - 誤操作の防止。指示ファイル（`CLAUDE.md`、`CLAUDE.local.md`、`AGENTS.md`）、`.claude/`、`.github/workflows/` の変更は、Issue で許していなければ push せずに止めます。
  - 検証。runner がテストを実行し直し、別のセッションがレビューします。
- **責任を持たないこと**：
  - 記録やレビューを欺く行為、Claude の資格情報の流出、root やホストの侵害への対策。
  - 対象リポジトリの設定による危険（GitHub Actions の secrets など）。下の「既知の限界」に注意を書きます。
  - プロンプトインジェクションや、悪意ある入力・agent への防御。**Issue の中身が安全かどうかは、承認する人が判断してください。** 外部の人が書いた Issue は、本文を読んでから承認してください。下の「既知の限界」も、この前提で書いています。

## ラベル

| ラベル | 付ける人 | 意味 |
| --- | --- | --- |
| `kagari:approved` | **User だけ** | 実装してよい |
| `kagari:running` | worker | 実装中。二重に取得しないためのロック |
| `kagari:implemented` | worker | Draft PR を作り、テストが通った |
| `kagari:reviewed` | worker | レビューコメントを投稿した（I-3 で使う） |
| `kagari:blocked` | worker | 失敗した、または判断を待っている。理由を Issue にコメントする |

## Issue の書き方

worker は、Issue 本文の `### テストコマンド` 見出し（`### Test command` でも可）のすぐ下にあるコードブロックを探し、その 1 行目をテストコマンドとして使います。この見出しが無い Issue は、Claude を起動する前に `blocked` にします。

````markdown
### テストコマンド
```sh
python3 -m unittest -v
```
````

Issue 本文には、置き場所を判断するための次の 3 つの見出しも書きます（ADR-0011 の追補）。どれかの見出しが無いか、中身が空（箇条書きの記号だけの場合も含む）の Issue は、Claude を起動する前に `blocked` にし、欠けている見出しをコメントします。英語の見出しでも同じに扱います。中身の良し悪しは runner は判定しません。User が承認ラベルを付けるときに判断します。
- `### 全体ロジック`（`### Overall logic`）：変更が入る処理の流れ全体。入口から出口まで、どのコンポーネントを通るか。
- `### 置く理由`（`### Why here`）：このロジックをこの場所に入れる理由。
- `### 却下した置き場所`（`### Rejected placements`）：検討した他の候補と、却下した理由を 1 行ずつ。候補が無い場合は「無し」と、その理由を 1 行書きます。

````markdown
### 全体ロジック
`run-once`：Issue を選ぶ → 本文を読む → worktree → Claude → commit → テスト → PR。今回は「本文を読む」段を変える。

### 置く理由
Claude を起動する前に止められる。

### 却下した置き場所
- `review-once` で確かめる：実装の後なので間に合わない。
````

保護するファイル（下の「`run-once` の動作」の 6）を変える必要がある Issue では、`### 変更する保護ファイル`（`### Protected files to change` でも可）の見出しの下に、変えてよいパスを箇条書きで書きます。1 行に 1 つのパスを、リポジトリの直下から書きます（backtick で囲んでもよく、後ろに説明を添えてもかまいません）。末尾が `/` ならその下すべてを指します。承認することで、この変更も許したことになります。

````markdown
### 変更する保護ファイル
- .github/workflows/ci.yml
- .claude/
````

ワークフローのファイルを push するには、`kagari-runner` の GitHub トークンに、ワークフローを書き換える権限（classic token の `workflow`、fine-grained token の Workflows: Read and write）が要ります。権限が無いと、push の段階で `blocked` になります。

Claude に渡すのは、Issue のタイトルと本文だけです。コメントは取得しません（D4）。

`review-once` は、`### 受入条件`（`### Acceptance criteria` でも可）の見出しの下にある箇条書きを、受入条件として 1 つずつ判定します。次の見出しまでが対象です。この見出しが無い Issue は、レビューせずに `blocked` にします。受入条件は、次の決まりで書いてください。
- `- [ ] ...` または `- ...` で始まる 1 行を、1 つの受入条件として読みます。
- 続きの行は読みません。入れ子の箇条書きは、別の受入条件として数えます。
- 番号付きの一覧（`1.`）は読みません。

## ホストのセットアップ（ホストごとに 1 回）

この作業はホストごとに 1 回だけです。リポジトリを追加しても、追加の作業はありません。`kagari-runner` と `kagari-agent` の 2 ユーザーは、I-0（#403）で作成済みとします。

| パス | 所有者 | モード | 用途 |
| --- | --- | --- | --- |
| `/srv/kagari` | `kagari-runner:kagari` | `0750` | 作業ディレクトリの親 |
| `/srv/kagari/wt` | `kagari-runner:kagari` | `3770` | 2 ユーザーが共有する worktree。setgid と sticky を付けます |
| `/srv/kagari/repos` | `kagari-runner:kagari` | `0700` | runner だけが使う clone と git の管理ディレクトリ |
| `/srv/kagari/logs` | `kagari-runner:kagari` | `0700` | runner だけが使う Claude の stream-json の記録 |

- `kagari-agent` は git の管理ディレクトリに書き込めません。そのため、git の設定やフックを仕込んで runner の権限でコードを動かすことはできません。
- `wt` に sticky を付けるのは、`kagari-agent` が worktree のディレクトリそのものを差し替えられないようにするためです。

```sh
sudo groupadd kagari
sudo usermod -aG kagari kagari-runner
sudo usermod -aG kagari kagari-agent
sudo install -d -o kagari-runner -g kagari -m 0750 /srv/kagari
sudo install -d -o kagari-runner -g kagari -m 3770 /srv/kagari/wt
sudo install -d -o kagari-runner -g kagari -m 0700 /srv/kagari/repos /srv/kagari/logs
t=$(mktemp) && echo 'kagari-runner ALL=(kagari-agent) NOPASSWD: ALL' > "$t"
sudo visudo -cf "$t" && sudo install -m 0440 "$t" /etc/sudoers.d/kagari-mvi; rm -f "$t"
```

セットアップの後、`kagari-runner` で次のコマンドを実行して確かめます。`claude` のパスとバージョンが表示されれば完了です。

```sh
sudo -n -u kagari-agent -H -- sh -lc 'command -v claude && claude --version'
```

- `sudo` の権限は、`kagari-agent` として実行することに限ります。`kagari-agent` は `kagari-runner` より弱いユーザーです。
- テストを実行する前に、worker は `kagari-agent` のプロセスをすべて止めます。前回の実行で残ったプロセスが、テストの結果を書き換えないようにするためです。そのため、`kagari-agent` で対話的な作業（`/login` など）をしているときは、`run-once` と `review-once` を動かさないでください。`run-once` と `review-once` は、どの組み合わせでも同時に動かさず、1 つの timer から順に呼んでください。
- テストは、ログインシェルを使わずに、sudo の `secure_path` で実行します。テストで使うコマンドは、`secure_path`（たとえば `/usr/bin`）に置くか、絶対パスで書いてください。
- `claude` が見つからない場合は、`kagari-agent` の `~/.profile` で PATH を通すか、`run-once` に `--claude /絶対パス` を渡してください。
- I-1 のときに作った `/srv/kagari` が残っている場合は、`sudo rm -rf /srv/kagari` で消してから作り直してください。中にあるのはテストの状態だけです。

### 削除

```sh
sudo rm -f /etc/sudoers.d/kagari-mvi
sudo rm -rf /srv/kagari
sudo groupdel kagari
# ユーザーも消す場合（I-0 で作ったもの）
sudo userdel -r kagari-agent; sudo userdel -r kagari-runner
```

### NixOS の設定例（未確認：実機では試していません）

```nix
{
  users.groups.kagari = { };
  users.users.kagari-runner = { isNormalUser = true; extraGroups = [ "kagari" ]; };
  users.users.kagari-agent = { isNormalUser = true; extraGroups = [ "kagari" ]; };
  security.sudo.extraRules = [{
    users = [ "kagari-runner" ];
    runAs = "kagari-agent";
    commands = [{ command = "ALL"; options = [ "NOPASSWD" ]; }];
  }];
  systemd.tmpfiles.rules = [
    "d /srv/kagari 0750 kagari-runner kagari -"
    "d /srv/kagari/wt 3770 kagari-runner kagari -"
    "d /srv/kagari/repos 0700 kagari-runner kagari -"
    "d /srv/kagari/logs 0700 kagari-runner kagari -"
  ];
}
```

この設定を外して `nixos-rebuild switch` を実行すれば、元に戻ります。ただし、`/srv/kagari` の中身は残るので、手で消してください。

## 使い方

```sh
just mvi::ensure-labels OWNER/REPO   # 5 つのラベルを作る（既にあれば何もしない）
just mvi::run-once OWNER/REPO        # 承認された Issue を最大 1 件処理する
just mvi::run-once OWNER/REPO --agent-config-dir DIR   # 追加の引数は kagari_mvi.py に渡る
just mvi::review-once OWNER/REPO     # implemented の Issue の PR を最大 1 件レビューする
just mvi::review-once OWNER/REPO --reviewer tsumgi   # レビューを TSUMGI（別系統のモデル）で行う
just mvi::cleanup-once OWNER/REPO    # merge 済みの PR のブランチと作業ディレクトリを消す
just mvi::test                       # 単体テスト（gh・git・sudo は偽物に差し替える）
```

`just mvi::run-once` の既定値は、次の環境変数で上書きできます。

| 環境変数 | 既定値 | 意味 |
| --- | --- | --- |
| `KAGARI_MVI_WORKDIR` | `/srv/kagari` | 作業ディレクトリ |
| `KAGARI_MVI_ALLOW_ACTOR` | `upiscium` | 承認ラベルを付けてよいユーザー |

`kagari_mvi.py run-once` は、次の引数も受け付けます。

| 引数 | 既定値 | 意味 |
| --- | --- | --- |
| `--agent-user` | `kagari-agent` | Claude とテストを実行するユーザー |
| `--claude` | `claude` | `claude` コマンドの名前またはパス |
| `--agent-config-dir` | なし | `CLAUDE_CONFIG_DIR`。テスト専用 |

終了コードの意味は次のとおりです。

| 終了コード | 意味 |
| --- | --- |
| 0 | 対象が無かった、処理が完了した、または処理を始める前に blocked にした |
| 1 | 処理を始めた後に blocked にした、または gh / git が失敗した |
| 2 | 引数が不正だった |
| 3 | Claude の利用上限に当たったので、Issue を `blocked` にせず元に戻した（`run-once`・`review-once`、#481） |

終了コード 3 は失敗ではありません。上限が戻れば次の実行が進みます。`run-once`・`review-once`・`cleanup-once` を 1 つの timer から順に呼ぶときは、3 で後ろの呼び出しを止めないでください（systemd なら `SuccessExitStatus=3`、シェルなら `|| [ $? -eq 3 ]`）。特に `cleanup-once` は、利用上限と関係なく動かせます。

## `run-once` の動作

1. **Issue を選ぶ**：対象は、open で、`kagari:approved` が付いていて、running / implemented / reviewed / blocked のどれも付いていない Issue です（PR は除きます）。その中で作成日時が最も古い 1 件を選びます。
2. **処理を始める前に確かめる**：次のどれかに当たる場合は、`blocked` を付けて理由をコメントし、そこで終わります。
   - timeline の最後の `labeled` イベントで `kagari:approved` を付けた actor が、許可リストに含まれない。
   - Issue 本文を最後に編集した時刻（GraphQL の `lastEditedAt`）が、その `labeled` イベントより後である。承認した内容と違う本文で動かないためです（#422）。本文を確かめたうえで、`kagari:approved` を外して付け直すと、新しい時刻で比べるので処理が進みます。Web の画面で付け直すときは、ラベルを外したらメニューをいったん閉じてから、もう一度付けてください。メニューを開いたまま外して付け直すと変更が記録されず、承認の時刻が新しくなりません（sandbox で確認）。
   - テストコマンドが見つからない。
   - `### 全体ロジック`・`### 置く理由`・`### 却下した置き場所` のどれかが無いか、中身が空である。コメントに、欠けている見出しを書きます（ADR-0011 の追補、#452）。
3. **ロックを取る**：`kagari:running` を付けます。
4. **worktree を用意する**：`kagari/issue-N` の open な PR が既にあれば、PR を増やさずに `blocked` にします。その PR のテストを確かめ直していないので、`implemented` にはしません。やり直す場合は、PR を close し、ブランチを削除してください。PR が無い場合は、次のように準備します。
   - `<workdir>/repos/O__R` を clone します。既にあれば fetch します。
   - `<workdir>/wt/<owner>__<repo>/issue-N` に、`origin/<既定ブランチ>` からブランチ `kagari/issue-N` の worktree を作ります。グループで書き込めるように umask は 002 にします。
5. **Claude に実装させる**（段階 `claude`）：`kagari-agent` として `claude -p` を起動します。
   - 起動のしかた：sudo で環境変数をリセットし、ログインシェルから起動します。
   - 出力：`--output-format stream-json` で受け取り、`<workdir>/logs/<owner>__<repo>/issue-N-claude.jsonl` に保存します。
   - 設定（D3 と #405 による）：
     - `--strict-mcp-config` を付け、MCP サーバーは空にします。
     - `--tools Bash,Read,Edit,Write,Glob,Grep` で、セッションで使えるツールをこの 6 つに限ります。Cron、RemoteTrigger、Skill などのツールは存在しません（LXC の記録で、指定しないと 20 個ほどのツールが使えることを確かめました）。
     - `--restricted` を付けます。ユーザー・project・local の設定ファイル（`.claude/settings*.json`）を読み込まず、ファイルを扱うツールを worktree の中に限り、設定・git・ツール設定のファイルへの書き込みを承認なしでは許しません。そのため、target リポジトリで有効にしたプラグイン・hooks・許可ルールは効きません（CLI の help による。実機では未確認）。
     - 念のため、target リポジトリが `.claude/settings.local.json` を commit している場合は、Claude を起動せずに `blocked` にします。
     - `--permission-prompts none` を付け、承認が必要な操作は自動で拒否します。
     - `--bare` は付けません。将来、`CLAUDE_CODE_OAUTH_TOKEN` に切り替えられるようにするためです。
     - 環境変数 `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1` を設定します。
     - 規則は `--append-system-prompt` で渡します。内容は「Issue の範囲だけを実装する」「commit はしない」「`status:` の行の直前に、テストごとの目的を申告する」「最後に `status: COMPLETED` または `status: BLOCKED: 理由` を出力する」です。申告の形は、`tests:` の下に 1 テスト 1 行で `- <テストの名前>: <何を保証する目的か> (AC<n>)`、続けて `untested: <テストで確かめていないこと、無ければ none>` です（#453）。テストで確かめられない受入条件（変わったファイルの範囲など）は、`untested:` に `AC<n>: <テストでは確かめようがない理由>` と書き（複数なら箇条書き）、レビュアーが差分で確かめます。テストで確かめられる受入条件には、テストが要ります（#478）。
     - Bash は原則として許可します（User 判断 2026-11-11）。ビルド、lint、テストなどを自由に実行できます。Bash は `kagari-agent` として動き、GitHub の資格情報を持たないので、安全の境界は OS のユーザー分離に置きます。
     - 拒否する操作：ネットワークや資格情報に関わるコマンド（`curl` `wget` `gh` `git push` `git remote` `ssh` `scp` `sudo` `su` `nc` `ncat` `socat` `telnet`）と、`Agent` `WebFetch` `WebSearch` `NotebookEdit` です。これは誤操作を防ぐためのもので、境界ではありません。
     - `Edit` と `Write` は worktree の中に限ります。headless なので、許可されていない操作は確認を待たずに拒否されます。
     - 保護するファイル（指示ファイル、`.claude/`、`.github/workflows/`）への `Edit` と `Write` は拒否します。うっかりの書き換えを減らすためのもので、Bash で書いた場合は次の commit の段階で止めます。Issue の `### 変更する保護ファイル` に項目が 1 つ以上ある場合は、この拒否ルールを付けず、commit の段階の確認だけで判定します。
   - 認証エラーの扱い：`Not logged in`、`Login expired`、`401` などで終わった場合は、`blocked` を付けて「kagari-agent の再ログインが必要」とコメントします。リトライはしません。
   - 利用上限の扱い（#481）：Claude が利用上限のエラー（`usage_limit_reached`、HTTP 429）で終わった場合は、`blocked` にしません。実装の失敗ではなく、時間がたてば直るためです。`kagari:running` を外して Issue を元の状態に戻し、上限が戻る時刻を Issue にコメントして（同じ時刻のコメントは重ねません）、終了コード 3 で終わります。上限が戻った後の次の実行が、その Issue をもう一度取ります。認証エラーの判定より先に見分けます。
6. **commit する**（段階 `commit`、`kagari-runner` が実行）
   - worktree の中にある `.git` ファイルは `kagari-agent` が書き換えられるので、使いません。git の管理ディレクトリを `--git-dir` で明示して指定します。
   - `core.hooksPath=/dev/null` と `core.fsmonitor=false` を指定し、`--no-verify` を付けて、すべての変更を commit します。ただし、テストやビルドで生成されるファイルと、パッケージのキャッシュ（`__pycache__`、`*.pyc`、`.pytest_cache`、`node_modules`、`.mypy_cache`、`.npm-cache`、`.npm`、`.cache`、`.yarn/cache`、`.pnpm-store`。どの階層にあっても）は commit しません。これらの場所にある変更は、リポジトリで管理しているファイルでも commit しません。npm のキャッシュが worktree の中に作られ、PR に入ったことがあるため（upiscium/MUSUBI#40）です。外した変更は通知しません。リポジトリで管理している `.cache/` などの中身を変える Issue では、その変更が commit に入らず、runner によるテストの再実行で失敗として表れることがあります。
   - 変更が無い場合は、PR を作らずに `blocked` にします。
   - 保護するファイルが変わっていて、Issue の `### 変更する保護ファイル` に書かれていない場合は、commit を worktree に残したまま、push も PR の作成もせずに `blocked` にします。コメントには、そのファイルを列挙します。保護するのは、どの階層にある `CLAUDE.md`・`CLAUDE.local.md`・`AGENTS.md`・`.claude/` と、リポジトリ直下の `.github/workflows/` です。
   - Issue で許した保護ファイルが変わった場合は、push し、PR のテスト結果のコメントにそのファイルを列挙します。merge するときに確かめてください。
7. **テストを実行し直す**（段階 `test`）：合否を決めるのは、この結果だけです。Claude の自己申告では決めません。
   - まず、`kagari-agent` のプロセスをすべて止めます。
   - 次に、commit した内容だけを `<workdir>/wt/<owner>__<repo>/issue-N.verify` に checkout します。
   - そのうえで、runner が `kagari-agent` としてテストコマンドを実行し、終了コードと出力を受け取ります。実行するときは、ログインシェルを使わず、HOME を使い捨てのディレクトリにします。
   - こうすることで、commit に含まれないファイルや、Claude が `~/.profile` などに残した変更は、結果に影響しません。
8. **PR を作る**：push して Draft PR を作ります。
   - PR の本文には、Issue 本文の全文と `Closes #N` を入れます。
   - runner が実行したテストの結果を、PR にコメントします。コメントに入れるのは、テストコマンド、終了コード、Claude の status、権限の拒否の件数、出力の末尾です。
   - 同じコメントの末尾に、「テストの目的（Claude の申告。合否には使わない）」として、Claude の最後のメッセージから取り出した `tests:` と `untested:` を載せます。取り出せなかった場合は「申告なし」と書きます。`untested:` が無い場合は一覧だけを載せ、`untested: (not declared)` と書きます。申告は信頼できない入力なので、40 行・3,000 バイトを超える分は切り、`(truncated by the runner)` と書き足します。合否は手順 7 の結果だけで決め、申告では変わりません。
9. **ラベルを付ける**：
   - テストが通り、status が `COMPLETED` の場合：`kagari:implemented` を付けてから `running` を外します。
   - それ以外の場合：Draft PR は残したまま `blocked` にし、Issue に理由をコメントします。

手順 4〜9 で失敗した場合は、`blocked` を付けてから `running` を外します。そのうえで、失敗した段階の名前とエラーの末尾を Issue にコメントします。

## `review-once` の動作（I-3）

1. **Issue を選ぶ**：対象は、open で、`kagari:approved` と `kagari:implemented` が付いていて、running / reviewed / blocked のどれも付いていない Issue です。その中で最も古い 1 件を選び、`kagari:running` を付けます。
2. **入力を集める**：レビュアーに渡すのは、次の 3 つと、PR の先頭の checkout（手順 3）です。実装セッションの会話は渡しません。
   - checkout を渡すのは、#406 の文面（入力は 3 つだけ）から外れます。根拠をファイルと行で示せるようにするためで、この判断は実装時に決めました。
   - Issue の受入条件（`### 受入条件` の箇条書き）
   - `kagari/issue-N` の open な PR の差分（REST の diff）。200,000 文字を超える部分は切り詰め、残りはファイルを読むよう伝えます。
   - runner が投稿したテスト結果のコメント。認証済みの runner 自身が投稿し、「kagari-mvi: test result recorded by the runner」で始まるものだけを使います。他の人が同じ書き出しで投稿したコメントは使いません。
   - テスト結果には、テストした commit が入っています。その commit がレビューする PR の先頭と違う場合、またはテスト結果が無い場合は、「根拠にしない」という警告を付けて渡します。レビューのコメントにも、そのことを記録します。
3. **PR の先頭を checkout する**：`kagari-agent` のプロセスを止めます。そのうえで、PR の先頭の commit を `<workdir>/wt/<owner>__<repo>/issue-N.review` に detached worktree として checkout します。
4. **レビューする**：`kagari-agent` として、新しい `claude -p` を起動します。
   - 使えるツールは `--tools Read,Grep,Glob` だけで、Bash・Edit・Write・Web は存在しません。設定でも拒否します。
   - `--restricted` で、読めるのは checkout の中だけになります。
   - そのほかの起動設定は、実装と同じです（`--permission-prompts none`、MCP は空、subagent の深さは 1）。
   - 規則：受入条件ごとに `AC<n>: PASS|FAIL|UNKNOWN — 根拠（file:line）` を 1 行ずつ出力し、根拠が足りなければ UNKNOWN にします。その後に、テスト結果のコメントにある実装役の申告について、`TESTS: PASS|FAIL|UNKNOWN — 根拠` を 1 行出力します。各テストの申告した目的が実際のテストの中身と合っていて、どの受入条件にもテストがあるか、テストが無い受入条件は実装役が `untested:` に理由とともに挙げていて、テストでは確かめようがない（変わったファイルの範囲など）とレビュアーが認め、差分とファイルから PASS と判定していれば PASS です（#478）。目的が合わない、テストで確かめられるのにテストが無い、テストも `untested:` の申告も無い、のどれかの受入条件があれば FAIL、申告が無いか判断できなければ UNKNOWN です（#453）。
   - 記録は `<workdir>/logs/<owner>__<repo>/issue-N-review.jsonl` に残します。
   - **`--reviewer tsumgi` の場合**：Claude は起動せず、runner が TSUMGI（`https://tsumgi.arc.upiscium.dev/v1`、モデル `qwen3.6-35b-a3b`）の chat completions API に 1 回だけ問い合わせます（`curl`）。実装者（Claude）と別系統のモデルでレビューするためです（ADR-0007 D6、ADR-0009 D4 の条件 2）。
     - 渡すのは、上の 3 つの入力（受入条件、差分、テスト結果）だけです。TSUMGI はファイルを読めないので、根拠は差分とテスト結果の中から示します。差分の外にあるファイルを根拠にする受入条件は、UNKNOWN になりやすくなります。
     - TSUMGI の入力は 10,240 トークンまでです。送る前に、入力（規則と、受入条件・差分・テスト結果）のトークン数を UTF-8 のバイト数から見積もります（既定は 2.5 バイトで 1 トークン、上限の 95% まで。約 24,300 バイトまで送る。2026-11-15 に、日本語を含む差分で実際のトークン数より多めに見積もることを確認）。超える PR は送らず、すべての受入条件を UNKNOWN にして、コメントに「too large for TSUMGI」と書きます。差分は切り詰めません。見積もりの方法と余裕は、環境変数 `KAGARI_MVI_TSUMGI_BYTES_PER_TOKEN` と `KAGARI_MVI_TSUMGI_MARGIN` で変えられます。
     - 呼び出すたびに、見積もったトークン数、TSUMGI が返した入力と出力のトークン数、送ったかどうか、エラーを、`<workdir>/logs/<owner>__<repo>/issue-N-tsumgi.jsonl` に 1 行ずつ追記します（答えの本文は残しません）。レビューのコメントの 1 行目にも、入力と出力のトークン数を書きます。
     - TSUMGI が応答しない、エラーを返す、出力の上限（2,048 トークン）で途中で切れた、推論（`<think>`）が閉じていない、などの場合も、すべての受入条件を UNKNOWN にし、「reviewer session: ended with an error」と記録して `reviewed` を付けます。
     - レビュー用の checkout（手順 3）は、TSUMGI では使いませんが、Claude のときと同じく作ります。
     - OpenCode は使いません。OpenCode が付け足す指示文だけで約 9,000 トークンになり、TSUMGI の上限を超えるためです（2026-11-14 に確認）。
5. **PR にコメントする**：受入条件ごとの判定と根拠を表にして、PR にコメントします。コメントには、レビューした commit と、PASS 以外の件数も入れます。判定の行が無い受入条件は UNKNOWN と表示します。表の最後の行は `TESTS` の判定です。`TESTS:` の行が無いか、食い違う行が複数あれば UNKNOWN と表示します。「PASS 以外の件数」は受入条件だけを数えます。
6. **Ready にする**（段階 `ready`、ADR-0010 D3、#432）：次のすべてを満たすときだけ、GraphQL の `markPullRequestReadyForReview` で PR を Draft から Ready にします。1 つでも欠ければ Draft のままにし、手順 5 のコメントの末尾に、その理由を書きます（例：`the PR stays Draft (TESTS is UNKNOWN)`）。Ready にしたときも、そう書きます。
   - すべての受入条件が PASS である。
   - `TESTS` の判定が PASS である（#453）。
   - レビュアーのセッションがエラーで終わっていない。
   - runner のテスト結果が、レビューした head に対するもので、終了コードが 0 である。どちらも、コメントの最初のコードブロックより前の、runner が書いた行だけから読みます。テストの出力と Claude の申告はコードブロックの中にあり、`- exit code: 0` のような行を含められるためです。
   Ready にできなかった（GitHub が Draft のままを返した、または GraphQL が失敗した）場合は、判定の表をコメントしてから `blocked` にします。merge は人間だけが行います。
7. **ラベルを付ける**：判定の結果にかかわらず、`kagari:reviewed` を付けてから `running` を外します。merge するかどうかは User が決めます。

すべての変更に独立レビューを行うのが既定のルールです（ADR-0010 D3）。`review-once` を飛ばして人間が直接 Ready にする運用はしません。

途中で失敗した場合や、認証エラーの場合は、`run-once` と同じく `blocked` にします。利用上限に当たった場合は、`blocked` にも `reviewed` にもせず、`run-once` と同じく Issue を元に戻して終了コード 3 で終わります（#481）。レビュアーが終了コード 0 で終わったのに、結果がエラーだった場合や、結果が無かった場合は、`blocked` にはしません。コメントに「reviewer session: ended with an error」と記録し、判定が無い受入条件を UNKNOWN にしたうえで、`reviewed` を付けます。

## `cleanup-once` の動作（#432）

人間が PR を merge した後に、その Issue の片付けをします。`run-once`・`review-once` と同じ timer から、順に呼んでください。

1. **対象を数える**：`<workdir>/wt/<owner>__<repo>/` にある `issue-N`・`issue-N.verify`・`issue-N.review` と、clone に残っているローカルのブランチ `kagari/issue-N` から、Issue の番号を集めます。
2. **PR の状態を調べる**：ブランチ `kagari/issue-N` の PR を、open と close の両方から探します。merge 済みの PR があり、open の PR が 1 つも無く、リモートのブランチの先端が merge された PR の head と同じで、ローカルのブランチの先端がその head と同じか祖先である（人間が PR に commit を足してから merge した場合など）場合だけ、次に進みます。祖先かどうかは、PR の head（`pull/N/head`）を fetch してから `git merge-base --is-ancestor` で確かめます。open の PR がある、merge せずに close した、PR が見つからない、merge の後に新しい commit がある（後の実行が push の前に `blocked` で止まり、commit を残した場合など）、のどれかなら、何も消さずに残します。
3. **消す**：リモートのブランチ（GitHub がまだ消していなければ）、作業ディレクトリ（3 つとも）、clone のローカルのブランチ `kagari/issue-N` の順に消します。作業ディレクトリは、`kagari-agent` のプロセスを止めてから、前回の作業ディレクトリと同じ方法で消します（#448、#456）。ローカルのブランチを最後にするのは、途中で失敗しても、次の実行で手順 1 がその番号を見つけられるようにするためです。ラベルは変えません。
4. **結果**：1 件でも失敗したら（GitHub の API の失敗を含む）、残りの Issue も続けて処理したうえで、終了コード 1 で終わります。失敗した Issue は、次の実行でもう一度試します。

### 既知の限界

KAGARI が責任を持たないもの（上の「KAGARI が責任を持つこと」）と、MVI でまだ扱っていないものです。

- **止まった Issue**：`running` が付いたまま止まった Issue は、自動では復旧しません。
- **Claude のログインの期限**（ADR-0009 追補 2026-11-18）：`kagari-agent` の Claude は OAuth（サブスクリプション）でログインしています。期限は `sudo -u kagari-agent -H claude auth status` の `loginExpiresAt` で確かめられます（2026-11-18 の時点で 2026-12-09 05:01 UTC）。認証エラーのときは、`blocked` にして再ログインを求め、リトライしません（空の設定ディレクトリによる疑似的な認証エラーで確かめました）。期限が切れた実際の挙動と、再ログインで期限がどう延びるかは、未確認です。timer で無人運転する間は、期限の前に `kagari-agent` で再ログインしてください。再ログインの作業中は、`run-once`・`review-once` を動かさないでください（上の「1 つのホストに 1 つの KAGARI」）。長期トークンへの切り替えは、v0.2.0 では扱いません。
- **利用上限**：Claude の利用上限（5 時間ごとの上限など）は、User の普段の使い方と同じ枠を共有します。上限に当たったときは、`run-once`・`review-once` とも `blocked` にせず、Issue を元に戻して、上限が戻る時刻を Issue にコメントし、終了コード 3 で終わります（#481）。人間がラベルを外す必要はありません。上限が戻った後の次の実行が、その Issue をもう一度取ります。上限が続く間は、毎回 fetch と作業ディレクトリの作成をやり直します。この動きは、2026-11-18 に実機で記録した上限のエラーの出力の形からテストで確かめたもので、実機の Claude が本当に上限に当たったときの動きは、未確認です。
- **承認と編集がほぼ同時の場合**：本文の編集と承認が同じ秒に起きた場合は、編集が先とみなして処理を進めます。比べるのは本文だけで、タイトルとコメントの変更は見ません（コメントは Claude に渡しません）。
- **残ったブランチ**：PR を作る前に失敗した後で再実行すると、remote に `kagari/issue-N` が残っている場合があります。そのときは push が non-fast-forward で拒否され、`blocked` になります。force push はしません。再実行する前に、そのブランチを削除してください。
- **target リポジトリの設定**：`--restricted` により、target リポジトリの設定ファイルは読み込みません。`.claude/settings.local.json` を commit しているリポジトリは対象にできません。target リポジトリの `CLAUDE.md` が読み込まれるかどうかは、未確認です。
- **Bash の広さ**：Bash は原則として許可しているので、拒否リストは回り道で越えられます。たとえば `python3 -c` でネットワークに接続できます。`env curl`、`/usr/bin/curl`、`bash -c "curl …"` のように書き方を変えた場合に拒否されるかは、Claude Code の照合の実装しだいで、未確認です。Claude は `kagari-agent` の Claude 資格情報（`~/.claude/.credentials.json`）を読めるので、Issue 本文で誘導されると、その資格情報を外部へ送られるおそれがあります。外部の人が作った Issue を承認するときは、本文を確認してください。
- **読み取り専用のコマンド**：Claude Code は、`uname` のような読み取り専用のコマンドを、許可リストに無くても承認なしで実行します（LXC の #15 で確認）。実行するのは `kagari-agent` なので、GitHub の資格情報には届きません。
- **Claude の読み取り**：Read・Edit・Write などのファイルツールは、`--restricted` によって worktree の中に限られます。ただし、Bash とテストのコードは `kagari-agent` として何でも読めます。そのため、`kagari-agent` が持つ Claude の資格情報（`~/.claude/.credentials.json`）を worktree にコピーされると、commit されて push されるおそれがあります。public リポジトリでは、merge する前に PR の差分を確認してください。
- **設定ファイルと git の関連ファイル**：`--restricted` の下では、設定・git・ツール設定のファイルを Edit や Write で書くには承認が必要です。`--permission-prompts none` なので、その操作は自動で拒否されます。そのため、`.gitignore` や `.github/` などを変える Issue では、Claude が Bash で書くか、`BLOCKED` で終わります。保護するファイルを Bash で書いた場合は、commit の段階で止まります。どのファイルが対象になるかは、未確認です。`--restricted` は local の設定ファイルも読まないので、Claude が `.claude/settings.local.json` を書いても権限は広がらないと推測しています（CLI の help の記述による。実機では未確認）。書かれたファイルは PR の差分に入ります。
- **残るプロセス**：Claude とテストは、sudo の内側で `timeout -k` によって時間を区切ります。テストの前には `kill -1` でプロセスを止めます。ただし、cron・at・`systemd-run --user` で後から起動するプロセスは止められません。そうしたプロセスがテストの最中に `.verify` を書き換えることは、防げていません。
- **GitHub Actions**：runner が push すると、target リポジトリの `push` / `pull_request` のワークフローが動きます。そのワークフローは、Claude が書いたコードを、GITHUB_TOKEN と secrets を使える状態で実行できます。KAGARI は、`.github/workflows/` の変更は push せずに止めますが、既存のワークフローが Claude のコードを実行することは防ぎません。これは target リポジトリの設定の問題です。secrets があるリポジトリを対象にする場合は、fork からの PR と同じ扱い（secrets を渡さない、承認してから実行する）になるよう、ワークフローを設定してください。
- **worktree の先取り**：`kagari-agent` は `wt/<owner>__<repo>/` に書き込めるので、ほかの Issue の worktree の場所に、先にディレクトリを作れます。その Issue は処理するときに `blocked` になります。レビュー用の `issue-N.review` も同じです。これは処理を止めるだけで、権限の昇格にはつながりません。
- **レビューの独立性**：既定のレビュアーは、実装者と同じモデル系統です（ADR-0007 D6）。`--reviewer tsumgi` では別系統になりますが、ファイルを読めず、大きな PR はレビューできません。受入条件と差分は Issue と PR から取るので、その文面でレビュアーを誘導することもできます。レビューの結果は参考として扱い、merge は User が判断してください。
- **レビュアーの起動の乗っ取り**：レビュアーは実装者と同じ `kagari-agent` で、ログインシェルから起動します。そのため、実装者が Bash で `~/.profile` の PATH や、`kagari-agent` の home に置いた `claude` を書き換えると、レビュアーの起動を乗っ取れるおそれがあります（未確認）。`claude` を root が所有する場所に入れ、`--claude` に絶対パスを渡せば、指定したパスの本体がすり替わることは防げます。ただし、`~/.profile` はシェルスクリプトなので、その内容（環境変数の設定や、`exec` による別のプログラムへの置き換え、PATH から探す `timeout` や `node` の差し替えなど）による介入は残ります。レビューをログインシェルなしで起動すれば防げると考えていますが、未確認です。実装者と別のユーザーでレビューする方法は、2 周目で扱います。
- **レビュー後の変更**：`reviewed` を付けた後に PR へ commit を足しても、もう一度はレビューしません。やり直す場合は、`kagari:reviewed` を外してください。
- **一覧への反映の遅れ**：承認した直後の実行では、Issue が選ばれないことがあります。ラベルで絞り込む一覧 API への反映が遅れるためと推測しています。次の実行で拾われます。`review-once` でも同じことが起きます。`implemented` を付けた直後に実行すると、「no implemented Issue to review」と出て何もせずに終わります。壊れているわけではありません。どちらの場合も、数十秒ほど待ってから、もう一度実行してください。

## 実機での確認手順（I-2 / #405、LXC `kagari-runner`、対象 `upiscium/kagari-mvi-sandbox`）

前提：「ホストのセットアップ」を済ませ、KUMIKI を checkout したディレクトリで実行します。Issue は 1 件ずつ作り、`run-once` で処理し終えてから次の Issue を作ってください。worker は古い Issue から順に選ぶためです。

```sh
R=upiscium/kagari-mvi-sandbox; O__R=$(echo $R | sed "s#/#__#")   # 記録と worktree の置き場所に使う
# 確認用の Issue なので、置き場所の 3 つの見出しは new_issue が決まった文で足します。
P3=$(printf '%s\n' '' '### 全体ロジック' '確認用の Issue。' '' '### 置く理由' '確認用の Issue。' '' \
  '### 却下した置き場所' '- 無し：確認用の Issue。')
new_issue() {  # $1 = タイトル、$2 = 本文（置き場所の 3 つの見出しは P3 を足す）
  N=$(gh issue create -R $R --title "$1" --body "$2$P3" | grep -o '[0-9]*$')
  gh issue edit $N -R $R --add-label kagari:approved; sleep 10; echo "#$N"
}
```

1. **小さな機能が実装され、テストが通った Draft PR ができる**

   ````sh
   new_issue "MVI I-2: hello" "$(printf '%s\n' \
     'hello.py に hello() を追加し、"hello" を返すようにする。test_hello.py に unittest のテストを追加する。' \
     '' '### テストコマンド' '```sh' 'python3 -m unittest -v' '```')"
   just mvi::run-once $R; echo rc=$?
   gh pr list -R $R --head kagari/issue-$N --json number,isDraft
   gh pr view -R $R kagari/issue-$N --comments | tail -20   # runner の結果コメントで exit code: 0
   ````

   確認すること：`rc=0` であること。Draft PR が 1 件あること。PR に「recorded by the runner」のコメントがあり、`exit code: 0` になっていること。Issue のラベルが `implemented` になっていること。

2. **許可リストに無いコマンドが拒否され、セッションが止まらずに終わる**

   拒否リストにある `curl` を、Bash ツールで実際に試させます。`uname -a` のような読み取り専用のコマンドは、許可リストと関係なく実行されるため、確認には使えません。

   ````sh
   new_issue "MVI I-2: deny probe" "$(printf '%s\n' \
     '権限の確認のため、実装の前に Bash ツールで `curl -sI https://example.com` の実行を必ず 1 回試みること（拒否されてよい）。' \
     'その後、probe.txt に、curl が成功した場合は reached、拒否された場合は denied と書く。' \
     '' '### テストコマンド' '```sh' 'test -f probe.txt' '```')"
   time just mvi::run-once $R; echo rc=$?
   jq -c 'select(.type=="result") | {is_error, permission_denials}' /srv/kagari/logs/$O__R/issue-$N-claude.jsonl
   ````

   確認すること：コマンドがタイムアウト（1 時間）より前に終わること。`permission_denials` に `curl` の Bash の拒否が入っていること。PR の差分で `probe.txt` が `denied` であること。

3. **Claude の側（`kagari-agent`）では gh も git push も失敗する**

   テストコマンドは `kagari-agent` として実行されます。そこで、gh と git push をテストコマンドとして実行させます。git push は、使い捨てのリポジトリから sandbox へ push を試します。

   ````sh
   new_issue "MVI I-2: credential probe" "$(printf '%s\n' \
     'probe2.txt に ok と書く。' '' '### テストコマンド' '```sh' \
     'gh auth status; d=$(mktemp -d) && cd $d && git init -q && git -c user.name=p -c user.email=p@p commit -q --allow-empty -m p && git push https://github.com/upiscium/kagari-mvi-sandbox.git HEAD:refs/heads/kagari-probe' '```')"
   just mvi::run-once $R; echo rc=$?
   gh pr view -R $R kagari/issue-$N --comments | tail -20
   ````

   確認すること：PR の runner コメントに、gh の未ログインと git push の認証エラーが出ていること。終了コードが 0 以外になっていること。Issue が `blocked` になっていること（テストが失敗したため。期待どおりの結果です）。sandbox に `kagari-probe` ブランチができていないこと。

   テストは使い捨ての HOME で実行されます。そのため上の確認だけでは、`kagari-agent` の本来の HOME に資格情報が無いことまでは確かめられません。Claude と同じ条件（本来の HOME とログインシェル）でも、次のコマンドで確かめてください。

   ```sh
   sudo -n -u kagari-agent -H -- env GIT_TERMINAL_PROMPT=0 sh -lc 'gh auth status; echo gh=$?; cd "$(mktemp -d)" && git init -q && git -c user.name=p -c user.email=p@p commit -q --allow-empty -m p && git push https://github.com/upiscium/kagari-mvi-sandbox.git HEAD:refs/heads/kagari-probe; echo push=$?'
   ```

   確認すること：`gh=` と `push=` がどちらも 0 以外になること。`push=` の直前に、`could not read Username`（資格情報が無い）のような認証のエラーが出ていること。init や commit で失敗して `push=` が 0 以外になった場合は、資格情報の確認になりません。このコマンドで確かめているのは HTTPS での push だけです。

   もう一つ、`kagari-agent` が SSH の鍵を持っていないことも確かめてください：`sudo -n -u kagari-agent -H -- sh -lc 'ls -la ~/.ssh'`。ディレクトリが無いか、中に鍵が無ければ確認できます。

4. **テスト結果が runner によって記録される**：1〜3 の PR コメントが「recorded by the runner (not reported by Claude)」で始まっていること。

5. **認証エラーのとき、blocked にして再ログインを求める**

   ````sh
   sudo -n -u kagari-agent -H -- mkdir -p /tmp/kagari-empty-config
   new_issue "MVI I-2: auth probe" "$(printf '%s\n' 'auth.txt に ok と書く。' '' '### テストコマンド' '```sh' 'true' '```')"
   just mvi::run-once $R --agent-config-dir /tmp/kagari-empty-config; echo rc=$?
   gh issue view $N -R $R --json labels,comments --jq '[.labels[].name], .comments[-1].body'
   ````

   確認すること：`rc=1` であること。Issue が `blocked` になり、「kagari-agent の再ログインが必要」のコメントが付いていること。PR が作られていないこと。

## 実機での確認手順（I-3 / #406）

前提と `new_issue` 関数は、I-2 の手順と同じです。

1. **I-2 の Draft PR に、受入条件ごとの判定コメントが付く**

   ````sh
   new_issue "MVI I-3: greet2" "$(printf '%s\n' \
     'greet2.py に greet2(name) を追加し、"Hi, <name>." を返すようにする。test_greet2.py に unittest のテストを追加する。' '' \
     '### 受入条件' '- [ ] greet2("A") が "Hi, A." を返す' '- [ ] test_greet2.py にテストがある' '' \
     '### テストコマンド' '```sh' 'python3 -m unittest -v' '```')"
   just mvi::run-once $R; echo rc=$?          # implemented になる
   just mvi::review-once $R; echo rc=$?       # reviewed になる
   P=$(gh api "repos/$R/pulls?head=upiscium:kagari/issue-$N" --jq '.[0].number')
   gh api repos/$R/issues/$P/comments --jq '.[-1].body'
   gh issue view $N -R $R --json labels --jq '[.labels[].name]'
   ````

   確認すること：PR の最後のコメントが「independent review」で始まり、AC1 と AC2 の判定と根拠が表になっていること。Issue のラベルに `kagari:reviewed` が付いていること。

2. **わざと満たしていない受入条件が FAIL か UNKNOWN になる**

   実装させた後、レビューの前に、`kagari-runner` で PR からテストファイルを消す commit を push します。

   ````sh
   new_issue "MVI I-3: greet3" "$(printf '%s\n' \
     'greet3.py に greet3() を追加し、"Yo" を返すようにする。test_greet3.py に unittest のテストを追加する。' '' \
     '### 受入条件' '- [ ] greet3() が "Yo" を返す' '- [ ] test_greet3.py にテストがある' '' \
     '### テストコマンド' '```sh' 'python3 -m unittest -v' '```')"
   just mvi::run-once $R; echo rc=$?
   d=$(mktemp -d) && git clone -q -b kagari/issue-$N https://github.com/$R.git $d && cd $d \
     && git rm -q test_greet3.py && git -c user.name=u -c user.email=u@u commit -qm "drop test" && git push -q && cd ~/KUMIKI
   just mvi::review-once $R; echo rc=$?
   P=$(gh api "repos/$R/pulls?head=upiscium:kagari/issue-$N" --jq '.[0].number')
   gh api repos/$R/issues/$P/comments --jq '.[-1].body'
   ````

   確認すること：AC2（テストがある）が FAIL か UNKNOWN になっていること。テストした commit が PR の先頭と違うので、レビューのコメントに「runner test result: missing or for another commit」と出ていること。

3. **レビューのセッションからファイルを編集できない**

   ````sh
   L=/srv/kagari/logs/$O__R/issue-$N-review.jsonl
   jq -c 'select(.type=="system" and .subtype=="init") | {tools}' $L
   jq -c 'select(.type=="assistant") | .message.content[]? | select(.type=="tool_use") | .name' $L | sort | uniq -c
   git -C /srv/kagari/wt/$O__R/issue-$N.review status --porcelain   # 何も出ない
   ````

   確認すること：`tools` が `Read`・`Grep`・`Glob` の 3 つだけであること。使われたツールもこの 3 つだけであること。checkout に変更が無いこと。
