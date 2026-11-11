# kagari-mvi — Issue 起点の最小 E2E worker（ADR-0007）

ADR-0007 の MVI 経路で動く worker です。承認された Issue を 1 件取り、Claude Code に実装させます。その後、テストを実行し直し、Draft PR を作ります。

- **構成**：本体は `kagari_mvi.py` の 1 ファイルです。使うのは、Python の標準ライブラリと、subprocess から呼ぶ `gh api`（REST）・`git`・`sudo`・`claude` だけです。GraphQL は使いません。
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

## ホストのセットアップ（ホストごとに 1 回）

この作業はホストごとに 1 回だけです。リポジトリを追加しても、追加の作業はありません。`kagari-runner` と `kagari-agent` の 2 ユーザーは、I-0（#403）で作成済みとします。

| パス | 所有者 | モード | 用途 |
| --- | --- | --- | --- |
| `/srv/kagari` | `kagari-runner:kagari` | `0750` | 作業ディレクトリの親 |
| `/srv/kagari/wt` | `kagari-runner:kagari` | `3770` | 2 ユーザーが共有する worktree。setgid と sticky を付けます |
| `/srv/kagari/repos` | `kagari-runner` | `0700` | runner だけが使う clone と git の管理ディレクトリ |
| `/srv/kagari/logs` | `kagari-runner` | `0700` | runner だけが使う Claude の stream-json の記録 |

- `kagari-agent` は git の管理ディレクトリに書き込めません。そのため、git の設定やフックを仕込んで runner の権限でコードを動かすことはできません。
- `wt` に sticky を付けるのは、`kagari-agent` が worktree のディレクトリそのものを差し替えられないようにするためです。

```sh
sudo groupadd kagari
sudo usermod -aG kagari kagari-runner
sudo usermod -aG kagari kagari-agent
sudo install -d -o kagari-runner -g kagari -m 0750 /srv/kagari
sudo install -d -o kagari-runner -g kagari -m 3770 /srv/kagari/wt
sudo install -d -o kagari-runner -g kagari-runner -m 0700 /srv/kagari/repos /srv/kagari/logs
echo 'kagari-runner ALL=(kagari-agent) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/kagari-mvi
sudo chmod 0440 /etc/sudoers.d/kagari-mvi && sudo visudo -cf /etc/sudoers.d/kagari-mvi
```

セットアップの後、`kagari-runner` で次のコマンドを実行して確かめます。`claude` のパスとバージョンが表示されれば完了です。

```sh
sudo -n -u kagari-agent -H -- sh -lc 'command -v claude && claude --version'
```

- `sudo` の権限は、`kagari-agent` として実行することに限ります。`kagari-agent` は `kagari-runner` より弱いユーザーです。
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
    "d /srv/kagari/repos 0700 kagari-runner kagari-runner -"
    "d /srv/kagari/logs 0700 kagari-runner kagari-runner -"
  ];
}
```

この設定を外して `nixos-rebuild switch` を実行すれば、元に戻ります。ただし、`/srv/kagari` の中身は残るので、手で消してください。

## 使い方

```sh
just mvi::ensure-labels OWNER/REPO   # 5 つのラベルを作る（既にあれば何もしない）
just mvi::run-once OWNER/REPO        # 承認された Issue を最大 1 件処理する
just mvi::run-once OWNER/REPO --agent-config-dir DIR   # 追加の引数は kagari_mvi.py に渡る
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
2. **処理を始める前に確かめる**：次のどちらかに当たる場合は、`blocked` を付けて理由をコメントし、そこで終わります。
   - timeline の最後の `labeled` イベントで `kagari:approved` を付けた actor が、許可リストに含まれない。
   - テストコマンドが見つからない。
3. **ロックを取る**：`kagari:running` を付けます。
4. **worktree を用意する**：`kagari/issue-N` の open な PR が既にあれば、手順 5〜8 を飛ばしてその PR を使います。無い場合は次のように準備します。
   - `<workdir>/repos/O__R` を clone します。既にあれば fetch します。
   - `<workdir>/wt/issue-N` に、`origin/<既定ブランチ>` からブランチ `kagari/issue-N` の worktree を作ります。グループで書き込めるように umask は 002 にします。
5. **Claude に実装させる**（段階 `claude`）：`kagari-agent` として `claude -p` を起動します。
   - 起動のしかた：sudo で環境変数をリセットし、ログインシェルから起動します。
   - 出力：`--output-format stream-json` で受け取り、`<workdir>/logs/issue-N-claude.jsonl` に保存します。
   - 設定（D3 と #405 による）：
     - `--strict-mcp-config` を付け、MCP サーバーは空にします。
     - `--setting-sources=project,local` を付けます。ユーザー設定と、そこで有効になるプラグインは読み込みません。
     - `--bare` は付けません。将来、`CLAUDE_CODE_OAUTH_TOKEN` に切り替えられるようにするためです。
     - 環境変数 `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH=1` を設定します。
     - 規則は `--append-system-prompt` で渡します。内容は「Issue の範囲だけを実装する」「git・gh・ネットワークは使わない」「最後に `status: COMPLETED` または `status: BLOCKED: 理由` を出力する」です。
     - 許可するツールは `Read` `Glob` `Grep` `Edit` `Write` と `Bash(<テストコマンド>)` だけです。`Agent` `WebFetch` `WebSearch` は拒否します。headless なので、許可されていない操作は確認を待たずに拒否されます。
   - 認証エラーの扱い：`Not logged in`、`Login expired`、`401` などで終わった場合は、`blocked` を付けて「kagari-agent の再ログインが必要」とコメントします。リトライはしません。
6. **commit する**（段階 `commit`、`kagari-runner` が実行）
   - worktree の中にある `.git` ファイルは `kagari-agent` が書き換えられるので、使いません。git の管理ディレクトリを `--git-dir` で明示して指定します。
   - `core.hooksPath=/dev/null` と `core.fsmonitor=false` を指定し、`--no-verify` を付けて、すべての変更を commit します。
   - 変更が無い場合は、PR を作らずに `blocked` にします。
7. **テストを実行し直す**（段階 `test`）：runner が `kagari-agent` としてテストコマンドを実行し、終了コードと出力を受け取ります。合否を決めるのは、この結果だけです。Claude の自己申告では決めません。
8. **PR を作る**：push して Draft PR を作ります。
   - PR の本文には、Issue 本文の全文と `Closes #N` を入れます。
   - runner が実行したテストの結果を、PR にコメントします。コメントに入れるのは、テストコマンド、終了コード、Claude の status、権限の拒否の件数、出力の末尾です。
9. **ラベルを付ける**：
   - テストが通り、status が `COMPLETED` の場合：`kagari:implemented` を付けてから `running` を外します。
   - それ以外の場合：Draft PR は残したまま `blocked` にし、Issue に理由をコメントします。

手順 4〜9 で失敗した場合は、`blocked` を付けてから `running` を外します。そのうえで、失敗した段階の名前とエラーの末尾を Issue にコメントします。

### 既知の限界（MVI の範囲外）

- **止まった Issue**：`running` が付いたまま止まった Issue は、自動では復旧しません。
- **承認後の本文の変更**：承認した後に Issue 本文が変わっても、検知しません。
- **残ったブランチ**：PR を作る前に失敗した後で再実行すると、remote に `kagari/issue-N` が残っている場合があります。そのときは push が non-fast-forward で拒否され、`blocked` になります。force push はしません。再実行する前に、そのブランチを削除してください。
- **`python3` の許可ルール**：ADR-0006 の既知の限界により、`python3 …` を Bash の許可ルールに完全一致で書いても、確認を求められることがあります。headless ではその操作は拒否されるので、Claude 自身はテストを実行できない場合があります。ただし、合否は手順 7 の runner による実行で判断します。
- **target リポジトリの設定**：target リポジトリの `.claude/settings.json`（`project`）は読み込みます。そこにある許可ルールは、allowlist を広げます。
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

2. **許可リストに無いコマンド（`curl`）が拒否され、セッションが止まらずに終わる**

   ````sh
   new_issue "MVI I-2: curl probe" "$(printf '%s\n' \
     '実装の前に、`curl -sI https://example.com` を実行して結果を確認すること。その後、probe.txt に ok と書く。' \
     '' '### テストコマンド' '```sh' 'test -f probe.txt' '```')"
   time just mvi::run-once $R; echo rc=$?
   jq -c 'select(.type=="result") | {is_error, permission_denials}' /srv/kagari/logs/issue-$N-claude.jsonl
   ````

   確認すること：コマンドがタイムアウト（1 時間）より前に終わること。`permission_denials` に `curl` を含む Bash の拒否が入っていること。

3. **Claude の側（`kagari-agent`）では gh も git push も失敗する**

   テストコマンドは `kagari-agent` として実行されます。そこで、gh と git push をテストコマンドとして実行させます。

   ````sh
   new_issue "MVI I-2: credential probe" "$(printf '%s\n' \
     'probe2.txt に ok と書く。' '' '### テストコマンド' '```sh' \
     'gh auth status; git push origin HEAD:refs/heads/kagari-probe' '```')"
   just mvi::run-once $R; echo rc=$?
   gh pr view -R $R kagari/issue-$N --comments | tail -20
   ````

   確認すること：PR の runner コメントに、gh の未ログインと git push の認証エラーが出ていること。終了コードが 0 以外になっていること。Issue が `blocked` になっていること（テストが失敗したため。期待どおりの結果です）。sandbox に `kagari-probe` ブランチができていないこと。

4. **テスト結果が runner によって記録される**：1〜3 の PR コメントが「recorded by the runner (not reported by Claude)」で始まっていること。

5. **認証エラーのとき、blocked にして再ログインを求める**

   ````sh
   sudo -n -u kagari-agent -H -- mkdir -p /tmp/kagari-empty-config
   new_issue "MVI I-2: auth probe" "$(printf '%s\n' 'auth.txt に ok と書く。' '' '### テストコマンド' '```sh' 'true' '```')"
   just mvi::run-once $R --agent-config-dir /tmp/kagari-empty-config; echo rc=$?
   gh issue view $N -R $R --json labels,comments --jq '[.labels[].name], .comments[-1].body'
   ````

   確認すること：`rc=1` であること。Issue が `blocked` になり、「kagari-agent の再ログインが必要」のコメントが付いていること。PR が作られていないこと。
