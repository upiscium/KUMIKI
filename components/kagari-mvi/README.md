# kagari-mvi — Issue 起点の最小 E2E worker（ADR-0007）

ADR-0007 の MVI 経路の worker です。この README は I-1（#404）の骨格を説明します。I-1 では、Claude による実装をまだ行いません。代わりにスタブとして、Issue のタイトルと本文をファイルに書き出して commit します。

- 本体：`kagari_mvi.py`（1 ファイル）。使うのは Python 標準ライブラリと、subprocess 経由の `gh api`（REST）と `git` です。GraphQL は使いません。
- 状態は GitHub Issue のラベルだけで持ちます（ADR-0007 D2）。DB、ref、ローカルの状態ファイルは使いません。既存の dispatch 経路と metadata には依存しません。
- 1 回の実行で処理する Issue は最大 1 件です。systemd timer などから定期的に呼ぶ前提です。

## ラベル

| ラベル | 付与者 | 意味 |
| --- | --- | --- |
| `kagari:approved` | **User のみ** | 実装してよい |
| `kagari:running` | worker | 実装中。二重に取得しないためのロックです |
| `kagari:implemented` | worker | Draft PR を作成済み |
| `kagari:reviewed` | worker | レビューコメントを投稿済み（I-3 で使います） |
| `kagari:blocked` | worker | 失敗した、または判断を待っている。理由は Issue にコメントします |

## 使い方

```sh
python3 components/kagari-mvi/kagari_mvi.py ensure-labels --repo OWNER/REPO
python3 components/kagari-mvi/kagari_mvi.py run-once --repo OWNER/REPO \
    --allow-actor upiscium --workdir /srv/kagari

just mvi::ensure-labels OWNER/REPO
just mvi::run-once OWNER/REPO   # KAGARI_MVI_WORKDIR（既定 /srv/kagari）と
                                # KAGARI_MVI_ALLOW_ACTOR（既定 upiscium）で上書きできます
just mvi::test                  # 単体テスト（gh と git は偽物に差し替えています）
```

`--allow-actor` は繰り返して指定するか、カンマで区切って複数を渡せます。

終了コードの意味は次のとおりです。

| 終了コード | 意味 |
| --- | --- |
| 0 | 対象が無かった、処理が完了した、または actor の判定で blocked にした |
| 1 | gh / git が失敗した。段階 4〜7 での失敗なら、Issue は blocked になっています |
| 2 | 引数が不正だった |

## `run-once` の動作

1. 対象の Issue を選びます。条件は、open であること、`kagari:approved` が付いていること、running / implemented / reviewed / blocked のどれも付いていないこと、PR ではないことです。条件に合うもののうち、作成日時が最も古い 1 件を選びます。該当が無ければ、何もせずに終了します。
2. timeline API（`GET /repos/O/R/issues/N/timeline`）で、`kagari:approved` を付けた最後の `labeled` イベントの `actor.login` を調べます。その actor が許可リストに無い場合は、`blocked` を付けて理由をコメントし、そこで終わります。
3. `kagari:running` を付けます。
4. `kagari/issue-N` の open な PR が既にあれば、手順 5 と 6 を飛ばしてその PR を使います。無ければ、`<workdir>/repos/O__R` を clone します（既にあれば fetch します）。続いて、`origin/<既定ブランチ>` から `<workdir>/wt/issue-N` にブランチ `kagari/issue-N` の worktree を作ります。前回の試行で作った worktree が残っていれば、作り直します。
5. 【スタブ】`.kagari-mvi/issue-N.md` に、Issue のタイトルと本文を書き出して commit します。コメントは取得しません（D4）。
6. `kagari/issue-N` を push し、Draft PR を作ります。タイトルには Issue のタイトルを使い、本文には Issue 本文の全文と `Closes #N` を入れます。
7. `kagari:implemented` を付け、`running` を外します。implemented を先に付けるので、ロックの無い時間はできません。

手順 4〜7 のどこかで失敗した場合は、`running` を外して `blocked` を付けます。そのうえで、失敗した段階の名前（`find-existing-pr` / `worktree` / `stub-commit` / `push` / `draft-pr` / `labels`）と、エラー出力の末尾 20 行を Issue にコメントします。

### 既知の限界（MVI の範囲外）

- `running` が付いたまま止まった Issue は、自動では復旧しません。User がラベルを確認して直してください。
- 承認した後に Issue 本文が変わっても、検知しません。
- PR を close した後に `kagari/issue-N` ブランチが remote に残っていると、push が non-fast-forward で拒否され、`blocked` になります。force push はしません。再実行する前に、そのブランチを削除してください。

## 実機での確認手順（LXC `kagari-runner`、対象 `upiscium/kagari-mvi-sandbox`）

前提は次の 3 つです。

- `kagari-runner` ユーザーで `gh auth status` が通ること
- `gh auth setup-git` を実行済みで、HTTPS の push に gh の資格情報が使われること
- `/srv/kagari` に書き込めること

```sh
R=upiscium/kagari-mvi-sandbox

# 1. ラベルを作る。2 回目の実行では created: (none) になる
just mvi::ensure-labels $R
just mvi::ensure-labels $R

# 2. Issue を作る（タイトルと本文は自由）
gh issue create -R $R --title "MVI smoke 1" --body "stub body"

# 3. 承認する。User 本人（upiscium）が付ける
gh issue edit <N> -R $R --add-label kagari:approved

# 4. 1 件を処理する
just mvi::run-once $R
```

確認すること（ADR-0007 I-1 の受入条件）は次の 4 つです。

- [ ] Draft PR が 1 件できること。PR のタイトルが Issue のタイトルと同じで、本文に Issue 本文の全文と `Closes #N` が入っていること。差分は `.kagari-mvi/issue-N.md` だけであること。Issue のラベルが `approved` + `implemented` になっていること。
- [ ] もう一度 `just mvi::run-once $R` を実行しても、PR が増えないこと。`implemented` が付いているので、Issue は選ばれません。さらに、`implemented` を手で外してから実行すると、既存の PR が再利用されて PR が増えないこと。
- [ ] User 以外が承認したものとして扱われ、`blocked` になること。別アカウントでラベルを付けるか、許可リストを一時的に変えて確かめます。たとえば、新しい Issue を承認してから `KAGARI_MVI_ALLOW_ACTOR=nobody just mvi::run-once $R` を実行します。このとき、Issue が `blocked` になり、理由のコメントが付き、worktree が作られないこと。
- [ ] 本体が 500 行以内であること（`wc -l components/kagari-mvi/kagari_mvi.py`）。
