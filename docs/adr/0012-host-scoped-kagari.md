# ADR-0012: KAGARI の導入の単位を、リポジトリからホストに広げる

- **Status:** Proposed（User が PR を merge した時点で Accepted とする）
- **Human decision:** 2026-11-19 に User が次の方針を決めた。KAGARI の初期の思想は「各リポジトリに Orchestrator を置き、リポジトリごとに KAGARI を導入する」だった。これを、「KAGARI は 1 ホストに 1 回導入する」設計に主軸を変える。KAGARI の「簡単に導入でき、簡単に外せる」という考え方（ADR-0002）は、対象をリポジトリからホストに広げて引き継ぐ。あわせて User が決めた：旧方式は凍結する、導入はホスト準備・固定 checkout・timer・対象登録まで（資格情報は含めない）、`uninstall` は導入したものだけ外す、この変更は新しい ADR と Epic に書く。
- **Issue:** #496（Epic）、#497（この ADR）
- **Related:** ADR-0002（K1〜K4）、ADR-0007（D2a、D3、D7）、ADR-0008（D1、D3）、ADR-0009（D1、D2、追補 2026-11-14）、ADR-0010（D2、D5）
- **Amends:** ADR-0002 K1・K2・K4：対象を Project からホストに広げる（D1〜D4）。ADR-0002 K3 は、対象のリポジトリに何も置かない形で満たす（D5）。ADR-0007 D7 の「ホストのセットアップはホストごとに 1 回」の箇条：手作業の手順を、標準の操作に格上げする（D2、D3）

## Context

- ADR-0002 K1〜K4 は、KAGARI を Project に着脱できる部品とし、`install`・`uninstall`・`reinstall`・`repair` を標準の操作にすると定めた（Status は Accepted; implementation pending）。対象は Project（リポジトリ）で、旧方式の導入器（`tools/kagari_bootstrap.py`、リポジトリ内の `.kagari`）がその実装だった。
- ADR-0007 の MVI は、すでにホスト単位で動いている。ホストの準備は 1 回（共有グループ・sudoers・`/srv/kagari`。ADR-0007 D7）で、1 つの KAGARI が複数のリポジトリを扱い、対象のリポジトリにファイルを置かない（kagari-mvi の README「ホストのセットアップ」）。
- ただし、ホストの導入と削除は手作業の手順書のままで、標準の操作・導入したものの記録・状態の確認・修復が無い。NixOS の設定例は実機で試していない。
- 旧方式のうち、`dispatch` 経路（ADR-0006 W1〜W5）は ADR-0007 D2a と ADR-0008 D1・D3 で、MVI を正とするために凍結または処置が決まった。**リポジトリごとの導入器そのものの扱いは、まだ決めていない。**この ADR の D6 で決める。

## Decision

### D1 — 導入の単位はホストである
KAGARI は 1 ホストに 1 回導入する。リポジトリには導入しない。リポジトリは、ホストの KAGARI が扱う「対象」として**登録**するものとする。

登録したリポジトリは、**すべて同じように扱う**。信頼度、公開・非公開、重要度による区別は、KAGARI の中に持たない。**どのリポジトリを KAGARI に管理させるかの責任は、User が負う。**同じホストに登録するリポジトリは、同じ `kagari-agent` と同じホストの上で動く（リポジトリ間の隔離は、KAGARI の保証の範囲に含まれない）。信頼度の違うリポジトリを同じホストに登録するかどうかは、User が判断する。

### D2 — ホストの導入が扱う範囲
`install` が扱う（= KAGARI が所有する）ものを、次に限る。
- 実行ユーザー（`kagari-runner`、`kagari-agent`）、共有グループ、`kagari-agent` として実行するための sudoers の 1 行
- `/srv/kagari` とその下の 3 つのディレクトリ（`wt`・`repos`・`logs`）
- KUMIKI の固定した checkout（導入時に版を記録する。更新の方法は「未決の事項」）
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
- 既定の扱いを、所有物ごとに次のとおりとする。

| 所有物 | `uninstall` の既定 | `--purge` |
| --- | --- | --- |
| sudoers の 1 行、systemd の service と timer、受領書、KUMIKI の checkout | 外す | 外す |
| 実行ユーザー、共有グループ（ユーザーの home に、人間が行った Claude のログインがある） | 残す | 消す（ログインも消えることを明示する） |
| `/srv/kagari` の `wt`・`repos`・`logs`（作業、clone、記録） | 残す | 消す |
| リポジトリ上のラベル・Issue・PR | 触れない | 触れない（ラベルの削除は、リポジトリごとの別の明示的な操作とする） |

- `unregister` は、ホストの登録からリポジトリを外すだけで、リポジトリ上のラベル・Issue・PR には触れない。

### D5 — 対象のリポジトリには、何も置かない
`register` が対象のリポジトリに行うのは、ラベルの作成だけとする。ファイルを置かない（ADR-0002 K3、kagari-mvi の README「既知の限界」）。Claude の設定は、`--restricted` によって読み込ませない。`.claude/settings.local.json` を commit しているリポジトリは対象にできない。現在は、`run-once` が Claude を起動する前に検出して `blocked` にする（README）。`register` でこの前提を診断するのは、この ADR が新たに決めること。

### D6 — 旧方式は凍結する
リポジトリごとの導入器（`tools/kagari_bootstrap.py`、`docs/kagari-external-bootstrap.md`、`docs/agent-core-admin.md`、リポジトリ内の `.kagari`）は、この ADR で**新たに**凍結する。新しい作業は行わず、コードと文書は残す。削除は、別の判断と Issue で行う。コードを消さないので、いつでも戻せる。ADR-0002 K1〜K4 のうち、Project を対象とする記述は、旧方式の契約として残る。

## Consequences

- 複数のリポジトリが、1 つの `kagari-agent` と 1 つの KAGARI を共有する。worktree はリポジトリごとに分かれ、テストは使い捨ての HOME で動く。ただし、リポジトリ間の隔離は保証しない（D1）。たとえば、public リポジトリの Issue の承認で動いたセッションから、同じホストの private リポジトリの worktree が読める（README の権限設定、`wt` が `3770` で `kagari-agent` が共有グループに属することからの推論。実機では未確認）。どのリポジトリを登録するかの責任は、User が負う。
- 「同時に動かさない」は、1 つの timer から順に呼ぶ構成にすることで守る。強制はしない。手動で `run-once` などを同時に動かすことは防げず、実行が重ならないことは実機では未確認である（kagari-mvi の README）。
- 対象リポジトリを増やす作業は `register` だけになる。導入の作業はホストに 1 回で済む。
- `install` を実装するまでは、README の手作業の「削除」（`/srv/kagari` を `rm -rf` する）が、D4 の既定（残す）と食い違う。実装の Issue で、README を D4 に合わせる。
- この ADR の merge 後に、ADR-0002 と ADR-0007 の Status に「ADR-0012 で改定」という参照を足す。別の PR で行う（Epic #496 に記録する）。

## 未決の事項

- **実装の形**：NixOS では導入が宣言的な設定（module）になり、命令的な `install` と二重になる。LXC（Ubuntu）は命令的な導入が自然である。どちらを正にするか、両方を持つかは、実装の最初の Issue で User に諮る。
- **既存のホストの取り込み**：すでに手作業で組んだホスト（LXC の `kagari-mvi`）には、導入の記録が無い。`install` が既存のものを引き受けるのか、`repair` が人の変更を戻してよいのか（D4 の「人が変更したもの」との関係）、記録を置く場所と、紛失したときの扱いを決める。
- **KUMIKI の checkout の更新**：現在の運用は `main` に合わせている。固定した版（D2）への移行と、更新を `reinstall` で行うか、`upgrade` を設けるかを決める。
- **`register` の前提の診断**：`settings.local.json` が無いこと（D5）に加え、ADR-0008 D4（KUMIKI 自身を対象にする前の前提条件）、対象リポジトリの GitHub Actions の secrets（ADR-0007 の Known limitations）を、`register` または `status` で診断するかを決める。
- **対象リポジトリの一覧の置き場所**：ホストの設定ファイル、または timer の `ExecStart=` 行。現在は、README の `ExecStart=` を並べる方式（#489）。
- 複数のホストにまたがる運用は、この ADR の範囲外とする。
