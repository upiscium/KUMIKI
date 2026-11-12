# ADR-0008: MVI の外側の流れを正とし、D8 経路の部品は必要が示されたときだけ取り込む

- **Status:** Proposed（2026-11-12）
- **Human decision:** 案C＋統合を採用（2026-11-12）。ADR-0007 D2「正とする経路」と D2a「W1〜W5 の凍結」の扱いを、ここで決める。
- **Related:** ADR-0006（D1〜D8、W1〜W5）、ADR-0007（D2、D2a、D5、D7）
- **Issue:** #416
- **Amends:** ADR-0006 D8 の実施経路、ADR-0007 D1（設計者の役割）。ADR-0007 D2・D2a の未決事項を解消する

## Context

- E2E-MVI-01 は、`kagari-mvi` を使い、実モデルで一周した（ADR-0007 の実施状況）。D8 経路はまだ一周したことがない。ここで言う D8 経路は、既存の `RepositoryMain.dispatch`、v1 metadata、allocator、flock と、ADR-0006 W1〜W5 を合わせたものを指す。
- E2E で見つかった課題は、権限窓口のような実行基盤の機能ではなかった。題材の仕様の抜けや、既定ブランチの分かりにくさといった、手前の問題だった。
- 一方で、KUMIKI 自身を対象にする段階では、Contract と照合する権限窓口（ADR-0006 W2、Draft PR #402）が必要になる見込みが高い。

## Decision

### D1 — 正とする経路は `kagari-mvi`
- Issue → 実装 → runner によるテストの再実行 → Draft PR → 独立レビュー → User の判断、という外側の流れは `kagari-mvi` を正とする。
- 既存の dispatch 経路には、新しい機能を足さない。削除するかどうかは、KUMIKI 自身を対象にする前に、別の Issue で決める。それまでは二本を並行して育てない。
- ADR-0006 D8 のリリース定義（v1 dispatch 経路を使う）は保留する。次のリリースの定義は、別の ADR で決める。

### D2 — D8 経路の部品の取り込み規則
- 取り込むのは、必要が示されたときだけとする。ここでの「必要」は、次のどちらかを指す。
  - E2E や実運用で、問題が実際に起きた。
  - 対象を KUMIKI 自身に広げるための前提条件である（D4）。
- 取り込むときは、その部品が何の誤操作を防ぐのかを 1 行で書く（ADR-0007 D5）。
- 取り込んだ部品も、MVI と同じく単体テストと LXC での実機確認で通す。
- 行数の上限（2 ファイル・700 行）は、取り込みのたびに見直す。ファイルを増やすときは、README と ADR に理由を書く。

### D3 — W1〜W5 の処置（ADR-0007 D2a の解消）

| 項目 | 処置 | 理由 |
| --- | --- | --- |
| W1 #393 / Draft PR #399（headless 起動） | close（ブランチは残す） | 実機確認で得た知見は、ADR-0007 D7 の起動設定に取り込み済み |
| W2 #394 / Draft PR #402（Contract 権限窓口） | 統合する。取り込み用の Issue を新しく起票した時点で、#394 と #402 を close する | KUMIKI 自身を対象にする前提条件（D4）になる |
| W3 #395（OpenCode + TSUMGI のレビュー窓口） | close する。2 周目のレビュー（ADR-0007 D6）として、`review-once` の選択肢という形で起票し直す | 外側の流れは `kagari-mvi` で、レビューの実体だけを差し替えればよい |
| W4 #396（v1 dispatch の OpenCode 依存を Claude 起動に置き換え） | close | D1 により、dispatch 経路は育てない |
| W5 #397（Claude 経路の E2E-01） | close | E2E-MVI-01 で代替した |

### D4 — KUMIKI 自身を対象にする前の前提条件
- Contract 権限窓口を取り込む（W2）。
- GitHub Actions の secrets への対策を決める（ADR-0007 の Known limitations）。
- レビュアーの起動を乗っ取られる件への対策を決める。候補は、レビュアーを別のユーザーで動かすことか、ログインシェルを使わずに起動することである。
- 既存の dispatch 経路を削除するかどうかを決める（D1）。

### D5 — ADR-0007 D1 の改訂（設計者の役割）
- ADR-0007 D1 の手順 1・2・6（要求を聞く、設計して Issue を起票する、PR とレビューを承認依頼の 4 点にまとめる）は、claude.ai の設計者 Claude ではなく、実装と同じ Claude Code のセッションが担う（User 判断 2026-11-12）。
- 承認ラベル `kagari:approved` を付けるのは、引き続き User だけとする。Issue の起票、commit、push には User の承認が要る。merge は User が行う。

## Consequences
- W1〜W5 の Issue と Draft PR を閉じる作業は、この ADR が merge された後に、User の承認を得て行う。
- ADR-0006 の Status に「D8 は ADR-0008 で保留」と追記する。

本 ADR は、Merge、Release、本番適用、既存 dispatch 経路の削除を承認するものではない。
