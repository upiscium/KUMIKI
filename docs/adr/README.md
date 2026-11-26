# Architecture Decision Records

KUMIKI（組木）/ KAGARI（篝）の合意済み設計判断を記録します。採択された**設計方針**と、現在利用できる**実装・機能**は区別してください。KAGARIは従来のAgentCoreに対応する**製品の表示名**であり、永続化・API・認可の内部識別子はそのまま維持します。対応関係と互換性は[フレームワークの名称・移行方針](../kagari-framework-identity.md)を参照してください。

| ADR | Status | 対象 |
| --- | --- | --- |
| [0001 — Trusted AgentTeam と Ask 境界](0001-trusted-agentteam-and-human-decisions.md) | Accepted; implementation partial; #1 の具体化は ADR-0009 | 目的・安全性・自律操作・人間の判断 |
| [0002 — 着脱可能な KAGARI と Project 所有権](0002-detachable-kagari-and-project-ownership.md) | Accepted; implementation pending; 2026-11-20 対象をホストに広げる（ADR-0012） | Install/Uninstall/Reinstall、Nix/Just/OpenCode、Repair |
| [0003 — 自己更新可能な source 開発と統一復旧](0003-self-hosted-development-and-recovery.md) | Accepted principles; implementation choices pending | Source Guard、自律更新、既知・未知の障害への対処 |
| [0004 — KAGARIとAGENTS.mdの共存](0004-kagari-agents-coexistence.md) | Accepted design; publication pending | 既存の設定と衝突時Ask |
| [0005 — v0.1.0優先と耐障害性拡張の凍結](0005-v010-delivery-and-resilience-freeze.md) | Accepted; v0.1.0 bounded GA released | 最小実モデルTask・基本着脱・公開順序 |
| [0006 — Claude主体の実行構成とv3廃止](0006-claude-primary-execution-topology.md) | Accepted (PR #392, 2026-11-09); addendum 2026-11-09; D8 は ADR-0009 で置き換え; D5 の承認依頼は ADR-0011 で置き換え | 実行構成・host所有のMain・Ask二分類・Leaf実行・承認ラベル・v3廃止・次期リリース条件（ADR-0009 で置き換え） |
| [0007 — Issue起点の最小E2E経路（MVI walking skeleton）](0007-mvi-walking-skeleton.md) | Accepted (2026-11-11); I-1〜I-3 実装済み、E2E-MVI-01 実施済み、D7 追補 (2026-11-12)、D2/D2a は ADR-0008 で決定、LXC は実機確認に限る（ADR-0009）、Known limitations の OAuth は ADR-0009 追補 2026-11-18 で「今のまま」と決定 | ADR-0006 D8より先に通す最小経路・ラベル状態機械・LXC/ユーザー分離・W1〜W5凍結（ADR-0008 で処置）・実装で確定した起動設定と権限 |
| [0008 — MVI を正とし、D8 経路の部品は必要時に取り込む](0008-mvi-mainline-and-d8-parts.md) | Accepted (2026-11-12); リリース定義と D4 の前提条件は ADR-0009 | 正とする経路・部品の取り込み規則・W1〜W5 の処置・KUMIKI を対象にする前提条件・設計者の役割 |
| [0009 — KAGARI の責任範囲と v0.2 系](0009-kagari-responsibility-and-v02.md) | Accepted (2026-11-13); 追補 2026-11-14（dispatch 経路は凍結、二重取得は確認済み、OAuth は保留、レビューは TSUMGI、README の手順は条件から外す）; 追補 2026-11-18（OAuth は今のままと決定、#480）; ADR-0010 で一部改定 | 責任を持つ範囲（人間の関門・GitHub 資格情報の分離・誤操作の防止・検証）と持たない範囲・ユーザー分離だけの実行環境・v0.1 資産の扱い・v0.2.0 の条件・#337/#391 の扱い |
| [0010 — 実行役を Claude Code と Pi に分け、許可台帳と運用ルールを足す](0010-executors-permission-ledger-and-rules.md) | Accepted (2026-11-15); 追補 2026-11-16（`kagari-mvi` の行数の上限を 1 ファイルあたり本体 500 行・テスト 800 行に変更）; 追補 2026-11-22（MUSUBI が制御セッションを管理する方針〔D6・D7、MUSUBI#39〕を MUSUBI#43 で取り消し）; 追補 2026-11-26（許可台帳〔#430〕の時期を v0.2.x から v0.3.0 に改める、ADR-0013） | 実行役（Claude Code・Pi・TSUMGI の直接呼び出し、OpenCode の終了）・許可台帳（同じ許可を何度も聞かない、回答を待つ）・Ready 化と運用ルール・秘密情報を要するテスト・並列化・v0.2.0 の範囲 |
| [0011 — 人間のレビューは、ロジックを置く場所の判断に絞る](0011-human-review-placement-judgment.md) | Accepted (2026-11-16, PR #451); 追補 2026-11-17（全体ロジック・置く理由・却下した置き場所は Issue に書き、承認ラベルの時点で判断する。欠けた Issue は起動前に `blocked`）; 追補 2026-11-17 の 2 件目（承認依頼に当初の目的と目的とのずれを必須にし、CI の通過は書かない。項目の順序を改める） | 役割の分担（正しさはテスト、置き場所は人間）・承認依頼の項目（当初の目的・ロジックの概要・目的とのずれ・全体ロジック・置く理由・却下した置き場所・テストの保証範囲・却下した場合の影響） |
| [0012 — KAGARI の導入の単位を、リポジトリからホストに広げる](0012-host-scoped-kagari.md) | Accepted (2026-11-20, PR #498); 追補 2026-11-20（実装の形：命令的なスクリプトを正にする） | KAGARI を 1 ホストに 1 回導入し、リポジトリは「登録」する。登録したリポジトリはすべて同じに扱い、何を管理させるかの責任は User が負う。着脱の標準操作（install/uninstall/status/repair、register/unregister）をホストに広げる（ADR-0002 K1・K2・K4 の対象を広げる、ADR-0007 D7 の手作業の手順を格上げする）。導入が扱うのはホスト準備・固定 checkout・timer・対象登録で、資格情報は含めない。uninstall は導入したものだけを外す。旧方式（リポジトリごとの導入器）は凍結 |
| [0013 — KAGARI v0.3.0 の範囲と、RC から GA までの条件](0013-kagari-v030-scope-and-conditions.md) | Proposed (2026-11-26, #556) | 次のリリースを v0.3.0（rc.1 → GA）とし、入るもの・含めないもの・GA の 8 つの条件（実機の確認の済み・未確認を区別）を定める。タグとリリースは User（ADR-0001）。ADR-0010 D7 の許可台帳の時期を v0.2.x から v0.3.0 に改める |

**Decision tracking:** [Issue #254](https://github.com/upiscium/KUMIKI/issues/254)。既存の v4 [Issue #189](https://github.com/upiscium/KUMIKI/issues/189)、KUMIKI/KAGARI 改名 [#242](https://github.com/upiscium/KUMIKI/issues/242)、#221 / Draft PR #224、#250 と整合させる。

これらのADRは、各時点の設計判断・実装範囲を示す歴史的記録です。[KAGARI / KUMIKI v0.1.0限定GA](https://github.com/upiscium/KUMIKI/releases/tag/v0.1.0)は公開済みですが、現行の生成先AgentCoreは **VERSION 3** のままです。外部KAGARI導入は旧設定との共存を標準とし、内部 `components/agent-core-v4/` の存在だけで任意のProjectや既存環境をアップグレード・本番適用する権限は生じません。

古い Issue・設計文書・テストは履歴証拠として保持します。矛盾がある場合は決定の前後関係・適用範囲を記録し、無断で既存の Guard や権限を変更しません。
