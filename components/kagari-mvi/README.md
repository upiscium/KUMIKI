# kagari-mvi — Issue 起点の最小 E2E worker（ADR-0007）

ADR-0007 の MVI 経路で動く worker です。承認された Issue を 1 件取り、Claude Code に実装させます。その後、テストを実行し直し、Draft PR を作ります（`run-once`）。Draft PR ができた Issue は、別の Claude セッションが受入条件ごとにレビューします（`review-once`）。

- **構成**：本体は 2 ファイルで、合わせて 800 行以内に保ちます（User 判断 2026-11-11 の 700 行を、2026-11-14 に変更。ADR-0007 D5 の目安「1 モジュール 500 行」から変更）。`kagari_mvi.py` が GitHub とラベルの処理、`kagari_mvi_exec.py` が Claude・テスト・commit の処理を受け持ちます。使うのは、Python の標準ライブラリと、subprocess から呼ぶ `gh api`（REST）・`git`・`sudo`・`claude` だけです。GraphQL は、Issue 本文を最後に編集した時刻を取る 1 か所だけで使います（REST では取れないため。User 判断 2026-11-14）。
- **状態の持ち方**：状態は GitHub Issue のラベルだけで持ちます（ADR-0007 D2）。DB、ref、ローカルの状態ファイルは使いません。既存の dispatch 経路と metadata にも依存しません。
- **実行の単位**：1 回の実行で処理する Issue は最大 1 件です。systemd timer などから定期的に呼ぶ前提です。
- **ユーザーの分担**（D3）：
  - `kagari-runner`（GitHub の資格情報を持つ）が worker を動かします。
  - `kagari-agent`（Claude の資格情報だけを持つ）が、Claude とテストコマンドを動かします。
  - git の commit と push は `kagari-runner` が行います。

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

## `run-once` の動作

1. **Issue を選ぶ**：対象は、open で、`kagari:approved` が付いていて、running / implemented / reviewed / blocked のどれも付いていない Issue です（PR は除きます）。その中で作成日時が最も古い 1 件を選びます。
2. **処理を始める前に確かめる**：次のどれかに当たる場合は、`blocked` を付けて理由をコメントし、そこで終わります。
   - timeline の最後の `labeled` イベントで `kagari:approved` を付けた actor が、許可リストに含まれない。
   - Issue 本文を最後に編集した時刻（GraphQL の `lastEditedAt`）が、その `labeled` イベントより後である。承認した内容と違う本文で動かないためです（#422）。本文を確かめたうえで、`kagari:approved` を外して付け直すと、新しい時刻で比べるので処理が進みます。
   - テストコマンドが見つからない。
3. **ロックを取る**：`kagari:running` を付けます。
4. **worktree を用意する**：`kagari/issue-N` の open な PR が既にあれば、PR を増やさずに `blocked` にします。その PR のテストを確かめ直していないので、`implemented` にはしません。やり直す場合は、PR を close し、ブランチを削除してください。PR が無い場合は、次のように準備します。
   - `<workdir>/repos/O__R` を clone します。既にあれば fetch します。
   - `<workdir>/wt/issue-N` に、`origin/<既定ブランチ>` からブランチ `kagari/issue-N` の worktree を作ります。グループで書き込めるように umask は 002 にします。
5. **Claude に実装させる**（段階 `claude`）：`kagari-agent` として `claude -p` を起動します。
   - 起動のしかた：sudo で環境変数をリセットし、ログインシェルから起動します。
   - 出力：`--output-format stream-json` で受け取り、`<workdir>/logs/issue-N-claude.jsonl` に保存します。
   - 設定（D3 と #405 による）：
     - `--strict-mcp-config` を付け、MCP サーバーは空にします。
     - `--tools Bash,Read,Edit,Write,Glob,Grep` で、セッションで使えるツールをこの 6 つに限ります。Cron、RemoteTrigger、Skill などのツールは存在しません（LXC の記録で、指定しないと 20 個ほどのツールが使えることを確かめました）。
     - `--restricted` を付けます。ユーザー・project・local の設定ファイル（`.claude/settings*.json`）を読み込まず、ファイルを扱うツールを worktree の中に限り、設定・git・ツール設定のファイルへの書き込みを承認なしでは許しません。そのため、target リポジトリで有効にしたプラグイン・hooks・許可ルールは効きません（CLI の help による。実機では未確認）。
     - 念のため、target リポジトリが `.claude/settings.local.json` を commit している場合は、Claude を起動せずに `blocked` にします。
     - `--permission-prompts none` を付け、承認が必要な操作は自動で拒否します。
     - `--bare` は付けません。将来、`CLAUDE_CODE_OAUTH_TOKEN` に切り替えられるようにするためです。
     - 環境変数 `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1` を設定します。
     - 規則は `--append-system-prompt` で渡します。内容は「Issue の範囲だけを実装する」「commit はしない」「最後に `status: COMPLETED` または `status: BLOCKED: 理由` を出力する」です。
     - Bash は原則として許可します（User 判断 2026-11-11）。ビルド、lint、テストなどを自由に実行できます。Bash は `kagari-agent` として動き、GitHub の資格情報を持たないので、安全の境界は OS のユーザー分離に置きます。
     - 拒否する操作：ネットワークや資格情報に関わるコマンド（`curl` `wget` `gh` `git push` `git remote` `ssh` `scp` `sudo` `su` `nc` `ncat` `socat` `telnet`）と、`Agent` `WebFetch` `WebSearch` `NotebookEdit` です。これは誤操作を防ぐためのもので、境界ではありません。
     - `Edit` と `Write` は worktree の中に限ります。headless なので、許可されていない操作は確認を待たずに拒否されます。
   - 認証エラーの扱い：`Not logged in`、`Login expired`、`401` などで終わった場合は、`blocked` を付けて「kagari-agent の再ログインが必要」とコメントします。リトライはしません。
6. **commit する**（段階 `commit`、`kagari-runner` が実行）
   - worktree の中にある `.git` ファイルは `kagari-agent` が書き換えられるので、使いません。git の管理ディレクトリを `--git-dir` で明示して指定します。
   - `core.hooksPath=/dev/null` と `core.fsmonitor=false` を指定し、`--no-verify` を付けて、すべての変更を commit します。ただし、テストで生成されるファイル（`__pycache__`、`*.pyc`、`.pytest_cache`、`node_modules`、`.mypy_cache`）は commit しません。
   - 変更が無い場合は、PR を作らずに `blocked` にします。
7. **テストを実行し直す**（段階 `test`）：合否を決めるのは、この結果だけです。Claude の自己申告では決めません。
   - まず、`kagari-agent` のプロセスをすべて止めます。
   - 次に、commit した内容だけを `<workdir>/wt/issue-N.verify` に checkout します。
   - そのうえで、runner が `kagari-agent` としてテストコマンドを実行し、終了コードと出力を受け取ります。実行するときは、ログインシェルを使わず、HOME を使い捨てのディレクトリにします。
   - こうすることで、commit に含まれないファイルや、Claude が `~/.profile` などに残した変更は、結果に影響しません。
8. **PR を作る**：push して Draft PR を作ります。
   - PR の本文には、Issue 本文の全文と `Closes #N` を入れます。
   - runner が実行したテストの結果を、PR にコメントします。コメントに入れるのは、テストコマンド、終了コード、Claude の status、権限の拒否の件数、出力の末尾です。
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
3. **PR の先頭を checkout する**：`kagari-agent` のプロセスを止めます。そのうえで、PR の先頭の commit を `<workdir>/wt/issue-N.review` に detached worktree として checkout します。
4. **レビューする**：`kagari-agent` として、新しい `claude -p` を起動します。
   - 使えるツールは `--tools Read,Grep,Glob` だけで、Bash・Edit・Write・Web は存在しません。設定でも拒否します。
   - `--restricted` で、読めるのは checkout の中だけになります。
   - そのほかの起動設定は、実装と同じです（`--permission-prompts none`、MCP は空、subagent の深さは 1）。
   - 規則：受入条件ごとに `AC<n>: PASS|FAIL|UNKNOWN — 根拠（file:line）` を 1 行ずつ出力し、根拠が足りなければ UNKNOWN にします。
   - 記録は `<workdir>/logs/issue-N-review.jsonl` に残します。
5. **PR にコメントする**：受入条件ごとの判定と根拠を表にして、PR にコメントします。コメントには、レビューした commit と、PASS 以外の件数も入れます。判定の行が無い受入条件は UNKNOWN と表示します。
6. **ラベルを付ける**：判定の結果にかかわらず、`kagari:reviewed` を付けてから `running` を外します。merge するかどうかは User が決めます。

途中で失敗した場合や、認証エラーの場合は、`run-once` と同じく `blocked` にします。レビュアーが終了コード 0 で終わったのに、結果がエラーだった場合や、結果が無かった場合は、`blocked` にはしません。コメントに「reviewer session: ended with an error」と記録し、判定が無い受入条件を UNKNOWN にしたうえで、`reviewed` を付けます。

### 既知の限界（MVI の範囲外）

- **止まった Issue**：`running` が付いたまま止まった Issue は、自動では復旧しません。
- **承認と編集がほぼ同時の場合**：本文の編集と承認が同じ秒に起きた場合は、編集が先とみなして処理を進めます。比べるのは本文だけで、タイトルとコメントの変更は見ません（コメントは Claude に渡しません）。
- **残ったブランチ**：PR を作る前に失敗した後で再実行すると、remote に `kagari/issue-N` が残っている場合があります。そのときは push が non-fast-forward で拒否され、`blocked` になります。force push はしません。再実行する前に、そのブランチを削除してください。
- **target リポジトリの設定**：`--restricted` により、target リポジトリの設定ファイルは読み込みません。`.claude/settings.local.json` を commit しているリポジトリは対象にできません。target リポジトリの `CLAUDE.md` が読み込まれるかどうかは、未確認です。
- **Bash の広さ**：Bash は原則として許可しているので、拒否リストは回り道で越えられます。たとえば `python3 -c` でネットワークに接続できます。`env curl`、`/usr/bin/curl`、`bash -c "curl …"` のように書き方を変えた場合に拒否されるかは、Claude Code の照合の実装しだいで、未確認です。Claude は `kagari-agent` の Claude 資格情報（`~/.claude/.credentials.json`）を読めるので、Issue 本文で誘導されると、その資格情報を外部へ送られるおそれがあります。外部の人が作った Issue を承認するときは、本文を確認してください。
- **読み取り専用のコマンド**：Claude Code は、`uname` のような読み取り専用のコマンドを、許可リストに無くても承認なしで実行します（LXC の #15 で確認）。実行するのは `kagari-agent` なので、GitHub の資格情報には届きません。
- **Claude の読み取り**：Read・Edit・Write などのファイルツールは、`--restricted` によって worktree の中に限られます。ただし、Bash とテストのコードは `kagari-agent` として何でも読めます。そのため、`kagari-agent` が持つ Claude の資格情報（`~/.claude/.credentials.json`）を worktree にコピーされると、commit されて push されるおそれがあります。public リポジトリでは、merge する前に PR の差分を確認してください。
- **設定ファイルと git の関連ファイル**：`--restricted` の下では、設定・git・ツール設定のファイルを Edit や Write で書くには承認が必要です。`--permission-prompts none` なので、その操作は自動で拒否されます。そのため、`.gitignore`、`.github/`、`.claude/` などを変える Issue では、Claude が Bash で書くか、`BLOCKED` で終わります。どのファイルが対象になるかは、未確認です。`--restricted` は local の設定ファイルも読まないので、Claude が `.claude/settings.local.json` を書いても権限は広がらないと推測しています（CLI の help の記述による。実機では未確認）。書かれたファイルは PR の差分に入ります。
- **残るプロセス**：Claude とテストは、sudo の内側で `timeout -k` によって時間を区切ります。テストの前には `kill -1` でプロセスを止めます。ただし、cron・at・`systemd-run --user` で後から起動するプロセスは止められません。そうしたプロセスがテストの最中に `.verify` を書き換えることは、防げていません。
- **GitHub Actions**：runner が push すると、target リポジトリの `push` / `pull_request` のワークフローが動きます。そのワークフローは、Claude が書いたコードを、GITHUB_TOKEN と secrets を使える状態で実行できます。Actions の secrets があるリポジトリを対象にする前に、対策を決めてください。対策の候補は、Workflows 権限の無い fine-grained token を使うこと、`.github/` を変更した差分は push しないことです。
- **worktree の先取り**：`kagari-agent` は `wt/` に書き込めるので、ほかの Issue の worktree の場所に、先にディレクトリを作れます。その Issue は処理するときに `blocked` になります。レビュー用の `issue-N.review` も同じです。これは処理を止めるだけで、権限の昇格にはつながりません。
- **レビューの独立性**：レビュアーは実装者と同じモデル系統です（ADR-0007 D6）。受入条件と差分は Issue と PR から取るので、その文面でレビュアーを誘導することもできます。レビューの結果は参考として扱い、merge は User が判断してください。
- **レビュアーの起動の乗っ取り**：レビュアーは実装者と同じ `kagari-agent` で、ログインシェルから起動します。そのため、実装者が Bash で `~/.profile` の PATH や、`kagari-agent` の home に置いた `claude` を書き換えると、レビュアーの起動を乗っ取れるおそれがあります（未確認）。`claude` を root が所有する場所に入れ、`--claude` に絶対パスを渡せば、指定したパスの本体がすり替わることは防げます。ただし、`~/.profile` はシェルスクリプトなので、その内容（環境変数の設定や、`exec` による別のプログラムへの置き換え、PATH から探す `timeout` や `node` の差し替えなど）による介入は残ります。レビューをログインシェルなしで起動すれば防げると考えていますが、未確認です。実装者と別のユーザーでレビューする方法は、2 周目で扱います。
- **レビュー後の変更**：`reviewed` を付けた後に PR へ commit を足しても、もう一度はレビューしません。やり直す場合は、`kagari:reviewed` を外してください。
- **一覧への反映の遅れ**：承認した直後の実行では、Issue が選ばれないことがあります。ラベルで絞り込む一覧 API への反映が遅れるためと推測しています。次の実行で拾われます。

## 実機での確認手順（I-2 / #405、LXC `kagari-runner`、対象 `upiscium/kagari-mvi-sandbox`）

前提：「ホストのセットアップ」を済ませ、KUMIKI を checkout したディレクトリで実行します。Issue は 1 件ずつ作り、`run-once` で処理し終えてから次の Issue を作ってください。worker は古い Issue から順に選ぶためです。

```sh
R=upiscium/kagari-mvi-sandbox
new_issue() {  # $1 = タイトル、$2 = 本文
  N=$(gh issue create -R $R --title "$1" --body "$2" | grep -o '[0-9]*$')
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
   jq -c 'select(.type=="result") | {is_error, permission_denials}' /srv/kagari/logs/issue-$N-claude.jsonl
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
   L=/srv/kagari/logs/issue-$N-review.jsonl
   jq -c 'select(.type=="system" and .subtype=="init") | {tools}' $L
   jq -c 'select(.type=="assistant") | .message.content[]? | select(.type=="tool_use") | .name' $L | sort | uniq -c
   git -C /srv/kagari/wt/issue-$N.review status --porcelain   # 何も出ない
   ````

   確認すること：`tools` が `Read`・`Grep`・`Glob` の 3 つだけであること。使われたツールもこの 3 つだけであること。checkout に変更が無いこと。
