# ADR-0005: KAGARI / KUMIKI v0.1.0を先行し、耐障害性の追加拡張を凍結する

- **Status:** Accepted (2026-10-15); release implementation pending
- **Human decision:** Human Reviewの優先順位変更を承認。KAGARI v0.1.0 / KUMIKI v0.1.0を優先。
- **Delivery tracker:** [#271](https://github.com/upiscium/Templates/issues/271)
- **Related:** #269, #256, #159, #142, #146, #189, #242; ADR-0001/0002/0003

## Context

v4のProject所有権、外部OpenCode設定、限定された製品編集、Contract/Evidence、
プロセス復旧とGit競合の実験は進んだ。一方、実モデルを用いたTaskの一巡、利用可能な
配布物と基本的な着脱はまだ確認できていない。テスト条件を増やし続けるより、検証済みの
境界を利用する最小の製品を完成させることを優先する。

## Decision

### D1 — 次の公開目標は両製品の0.1.0

KAGARIの公開目標を **0.1.0**、KUMIKIの公開目標も **0.1.0** とする。
KAGARIはProjectとは別に導入する開発支援部品、KUMIKIはそのソース・テンプレート・
配布側である。最初の目標番号は同じだが、将来の全バージョンを同期する約束はしない。

**v4は内部設計の世代**であり、公開SemVerのmajorを意味しない。#159の旧計画
AgentCore 1.0.0 / Templates 4.0.0は、直近のリリース目標ではなくなる。
既存タグ・配布履歴・Contract/Record/Evidenceのschema・凍結済みN1の
`target_version=1.0.0`証拠は書き換えない。新しい配布物のversion出力、package
metadata、READMEが0.1.0と一致することをM2で確認する。古い実験用Bundleに
名前を付け替えただけでは、利用可能な0.1.0の完成とはしない。

### D2 — 耐障害性の「追加拡張」を凍結

次の作業は0.1.0の必須経路から外す: 電源断・再起動の障害注入、Git内部書き換えの
網羅的停止試験、残存ロックの自動回収、ref/index不整合の自動修復、任意のGit/FS
更新者との完全排他、製品/index/metadata全体の原子性、分散復旧サービス、追加の
耐障害性マトリクス。

**既存の保全機能を削除・無効化する決定ではない。** Project設定・製品/Git/Task/
Evidenceの所有権、既存staged変更の保持、Contractと変更範囲の再検証、異常時の
明確な停止、部分成功の区別、一時資源のCleanupは残す。0.1.0の最小経路を妨げる
具体的な不具合やデータ損失リスクは、理由と差分を記録して限定修正できる。
それを理由に耐障害性プロジェクト全体を再開しない。

### D3 — 実装・試験・凍結実験を分離

既存34ファイルをそのまま一括のランタイムとして出荷しない。

| 既存成果 | 0.1.0への扱い |
| --- | --- |
| N0のProject識別・所有権診断、N1の既存v4 native modules | 必要部分を抽出して実行入口へ接続。診断結果を実行許可と混同しない |
| 外部設定・namespace・限定製品編集の正の実行経路 | M1の単一実モデルTaskへ統合する候補 |
| Task Record / Contract / Evidence / Task View | 既存v4 APIを再利用し、ユーザーに見える一巡を完成させる |
| synthetic Fixture、fake OpenCode、固定Python writer、設定組合せマトリクス | テスト専用。実モデルの実績や配布物に偽装しない |
| process recovery、commit transaction、recovery gateの追加拡張 | 凍結した履歴証拠。必要な既存safe-stopだけを損なわない |

最新のC 24ファイルは#269のguarded recovery checkpointと照合し、他のN0/A/B/Dを
合わせた34ファイルをローカルの検証済みアーカイブに保存した。これはGitHub上への
ソース公開ではない。凍結証拠を後から書き換える代わりに、製品統合の差分を区別する。

### D4 — M1: 最小の実モデルTaskを次の主要成果にする

使い捨てGit Projectで、主エージェント1体がProjectの設定とAGENTS.mdを引き継ぎ、
限定した製品変更を行い、合意したテストを通し、Contract/変更範囲を再検証して
コミットする。Task Record、製品コミットに結びついたEvidence、Task Viewを保存して
読み戻す。セッション終了・再開で以前のturn-local Work Unitsを必須にしない。
Draft PR/checkpoint経路も、許可された使い捨てremoteで確認する。

モデルはOpenCode側の設定を使い、強制上書きしない。2026-10-15の追加Human指示で
AIサブエージェント禁止を解除した。開発・レビューの並列委譲は利用可能だが、M1の
最小受入試験は帰属を明確にするため主モデル1体から開始する。委譲先の結果は
独立レビューの有無・対象・実行テストとの区別を記録する。通常のテスト・プロセス
並列も利用できる。耐障害性拡張の凍結や公開承認の境界は変更しない。`debug config`や固定writerの
PASSは実モデルTaskの証拠として数えない。

### D5 — M2/M3: 基本的な着脱と配布を閉じる

最初の検証範囲は現在検証可能なLinux/Nix/OpenCodeの明示した構成に絞る。
その構成でInstall → M1の実Task → Uninstall → Reinstallを一巡し、既知の
KAGARI-owned部分欠落は同じ入口のRepairで対処する。Project、Git履歴・index、
Task/Evidenceを保存し、未知の所有権や安全に扱えない破損状態は理由付きで停止する。
全環境・全破損組合せの自動修復は0.1.0の必須条件にしない。

通常Task、Adminのinstall/repair/uninstall、KUMIKI source publicationは別責務。
#146のAdmin Launcherをすべての通常Taskに必要な巨大なトランザクション機構にはしない。
レビュー、対象回帰、GitHub CI、Quickstart、既知の制限を確認してから、必要な
Publication/Merge/Release承認を経て配布する。旧レビュー結果を新しい差分に流用しない。

## Milestone order and present truth

1. **M0:** 成果を保全・分類し、本ADRと#271でスコープを確定する。
2. **M1:** 実モデルの最小Task一巡を確認する。最優先の実装経路。
3. **M2:** 0.1.0の実行入口、配布version、基本的なInstall/Uninstall/Reinstall/Repairを確認する。
4. **M3:** 実際に出荷する差分のレビュー・CI・文書・公開承認を完了する。

このADRは設計承認を記録する文書で、**M1/M2/M3の完了・0.1.0リリース済み・
本番適用済みという宣言ではない**。#269/#256のより広い未達条件も、0.1.0の
範囲を狭めたことだけで完了扱いにしない。

## Authority and working hygiene

今回の承認は優先順位変更とローカル実装の承認であり、全候補ファイルの一括公開、
Source Guard更新、タグ/Release作成、本番導入、独立レビュー免除の包括承認ではない。
既存の作業領域を再利用し、永続tmuxやcloneを増やさず、一時環境は作業終了時に片づける。
凍結中の耐障害性マトリクスを「進捗」として追加実行し続けない。

## Subsequent accepted M1 acceptance amendment (2026-10-15)

The authoritative later #271 Human acceptance requires one primary agent to
actually delegate at least two bounded genuinely concurrent native children
for v0.1.0. Earlier language allowing a single-agent starting demonstration
is historical, not sufficient for the final multi-agent gate. Model-created
TaskSpec-bound Planning is a target. Host-only tests, revalidation, scoped
Git commit, Evidence and Task View remain separate from leaf responsibilities.
The #271 M2 local acceptance proves this only under disposable TEST_ONLY
conditions; it is not production qualification or publication authority.

## Explicit later Human scope decision: Session Export limitation (#276)

On 2026-10-17 the Human classified the missing **write-time 3 MiB** Export
disk bound as **LOW priority, acceptable for KAGARI v0.1.0 when documented**.
The current 35-second timeout and post-write 3 MiB Evidence rejection
remain unchanged. Temporary write-time disk usage may exceed 3 MiB; this
unproven resource-consumption risk is disclosed under
[Known Limitations](../kagari-v010-known-limitations.md) and tracked for a
later release in [Issue #276](https://github.com/upiscium/Templates/issues/276).

This is a targeted deferral, not a change to Task integrity, a production
approval, an exemption from independent code review, or a reopening of the
frozen general resilience matrix. Publication/Merge/Release approval and
other security/qualification gates remain separate.
