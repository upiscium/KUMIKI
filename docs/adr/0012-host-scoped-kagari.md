# ADR-0012: KAGARI の導入の単位を、リポジトリからホストに広げる

- **Status:** Proposed（User が PR を merge した時点で Accepted とする）
- **Human decision:** 2026-11-19 に User が次の方針を決めた。KAGARI の初期の思想は「各リポジトリに Orchestrator を置き、リポジトリごとに KAGARI を導入する」だった。これを、「KAGARI は 1 ホストに 1 回導入する」設計に主軸を変える。KAGARI の「簡単に導入でき、簡単に外せる」という考え方（ADR-0002）は、対象をリポジトリからホストに広げて引き継ぐ。
- **Issue:** #496（Epic）、#497（この ADR）
- **Related:** ADR-0002（K1〜K4）、ADR-0007（D3）、ADR-0008（D2a の凍結）、ADR-0009（D1）、ADR-0010（D1）
- **Amends:** ADR-0002 K1〜K4：対象を Project からホストに読み替える（D1〜D4）。ADR-0007 D3：手作業のホスト準備を、標準の操作に格上げする（D2、D3）

## Context

- ADR-0002 K1〜K4 は、KAGARI を Project に着脱できる部品とし、`install`・`uninstall`・`reinstall`・`repair` を標準の操作にすると定めた（Accepted、実装は未着手）。対象は Project（リポジトリ）で、旧方式の導入器（`tools/kagari_bootstrap.py`、リポジトリ内の `.kagari`）がその実装だった。
- ADR-0007 の MVI は、すでにホスト単位で動いている。ホストの準備は 1 回（共有グループ・sudoers・`/srv/kagari`）で、1 つの KAGARI が複数のリポジトリを扱い、対象のリポジトリにファイルを置かない（kagari-mvi の README「ホストのセットアップ」）。
- ただし、ホストの導入と削除は手作業の手順書のままで、標準の操作・導入したものの記録・状態の確認・修復が無い。NixOS の設定例は実機で試していない。
- 旧方式（リポジトリごとの Orchestrator）は、ADR-0007 D2a と ADR-0008 で正の経路から外れ、凍結されている。

## Decision

### D1 — 導入の単位はホストである
KAGARI は 1 ホストに 1 回導入する。リポジトリには導入しない。リポジトリは、ホストの KAGARI が扱う「対象」として**登録**するものとする。

### D2 — ホストの導入が扱う範囲
`install` が扱う（= KAGARI が所有する）ものを、次に限る。
- 実行ユーザー（`kagari-runner`、`kagari-agent`）、共有グループ、`kagari-agent` として実行するための sudoers の 1 行
- `/srv/kagari` とその下の 3 つのディレクトリ（`wt`・`repos`・`logs`）
- KUMIKI の固定した checkout（導入した版を記録する）
- systemd の service と timer（`run-once` → `review-once` → `cleanup-once` を順に呼ぶ）
- 対象リポジトリの登録（一覧と、許可する actor）

次は扱わない。**GitHub の資格情報と Claude のログイン**は、人間の手順のまま残す（資格情報を分離する方針と、OAuth のログインが人間の作業である点に合わせる）。`install` は、これらが未設定であることを診断として示すだけにする。

### D3 — 標準の操作
ホストの操作として、次を提供する。いずれも冪等とする。
- `install` / `uninstall` / `reinstall`
- `status`（導入したものの有無と、資格情報の期限などの診断）
- `repair`（部分欠落・一部破損から、`uninstall` を先に行わずに戻す。ADR-0002 K4 の引き継ぎ）

リポジトリの操作として、`register` / `unregister` を提供する。

入口は、対象のリポジトリにも、既存の KAGARI の実行環境にも依存しない（ADR-0002 K2 の引き継ぎ）。

### D4 — `uninstall` は導入したものだけを外す
- `install` は、導入したものの記録（受領書）を作る。`uninstall` は、記録にあり、変更されていないものだけを外す。人が変更したもの、記録に無いものは外さず、診断として示す（ADR-0002 K4・K3 の引き継ぎ）。
- 既定では、ログ、作業ディレクトリ、clone、リポジトリ上のラベルと PR を**残す**。完全に消すには、別の明示的な操作（`--purge` など）を要する。
- `unregister` は、ホストの登録からリポジトリを外すだけで、リポジトリ上のラベル・Issue・PR には触れない。

### D5 — 対象のリポジトリには、何も置かない
`register` が対象のリポジトリに行うのは、ラベルの作成だけとする。ファイルを置かず、リポジトリの設定ファイルを読まない（ADR-0009 D1、kagari-mvi README「既知の限界」）。ADR-0002 K3（Project-first ownership）は、これにより最も強い形で満たされる。

### D6 — 旧方式は凍結する
リポジトリごとの導入器（`tools/kagari_bootstrap.py`、`docs/kagari-external-bootstrap.md`、`docs/agent-core-admin.md`、リポジトリ内の `.kagari`）は、凍結する。新しい作業は行わず、コードと文書は残す。削除は、別の判断と Issue で行う。凍結は ADR-0007 D2a と同じ扱いで、いつでも戻せる。ADR-0002 K1〜K4 のうち、Project を対象とする記述は、旧方式の契約として残る。

## Consequences

- 複数のリポジトリが、1 つの `kagari-agent` と 1 つの KAGARI を共有する。リポジトリ間の隔離は、KAGARI の目的ではない（誤操作の防止・人間の関門・資格情報の分離だけを責任とする。ADR-0009 D1）。worktree はリポジトリごとに分かれ、テストは使い捨ての HOME で動く。
- 「1 つのホストに 1 つの KAGARI」（同時に動かさない）は、`install` が作る timer と service で守る。
- 対象リポジトリを増やす作業は `register` だけになる。導入の作業はホストに 1 回で済む。

## 未決の事項

- 実装の形：NixOS では導入が宣言的な設定（module）になり、命令的な `install` と二重になる。LXC（Ubuntu）は命令的な導入が自然である。どちらを正にするか、両方を持つかは、実装の最初の Issue で User に諮る。
- 対象リポジトリの一覧の置き場所（ホストの設定ファイル、または timer の `ExecStart=` 行）。現在は、README の `ExecStart=` を並べる方式（#489）。
- 複数のホストにまたがる運用は、この ADR の範囲外とする。
