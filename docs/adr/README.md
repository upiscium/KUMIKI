# Architecture Decision Records

KUMIKI（組木）/ KAGARI（篝）の合意済み設計判断を記録します。採択された**設計方針**と、現在利用できる**実装・機能**は区別してください。KAGARIは従来のAgentCoreに対応する**製品の表示名**であり、永続化・API・認可の内部識別子はそのまま維持します。対応関係と互換性は[フレームワークの名称・移行方針](../kagari-framework-identity.md)を参照してください。

| ADR | Status | 対象 |
| --- | --- | --- |
| [0001 — Trusted AgentTeam と Ask 境界](0001-trusted-agentteam-and-human-decisions.md) | Accepted; implementation partial | 目的・安全性・自律操作・人間の判断 |
| [0002 — 着脱可能な KAGARI と Project 所有権](0002-detachable-kagari-and-project-ownership.md) | Accepted; implementation pending | Install/Uninstall/Reinstall、Nix/Just/OpenCode、Repair |
| [0003 — 自己更新可能な source 開発と統一復旧](0003-self-hosted-development-and-recovery.md) | Accepted principles; implementation choices pending | Source Guard、自律更新、既知・未知の障害への対処 |
| [0004 — KAGARIとAGENTS.mdの共存](0004-kagari-agents-coexistence.md) | Accepted design; publication pending | 既存の設定と衝突時Ask |
| [0005 — v0.1.0優先と耐障害性拡張の凍結](0005-v010-delivery-and-resilience-freeze.md) | Accepted; v0.1.0 bounded GA released | 最小実モデルTask・基本着脱・公開順序 |
| [0006 — Claude主体の実行構成とv3廃止](0006-claude-primary-execution-topology.md) | Accepted (PR #392, 2026-11-09); addendum 2026-11-09 | 実行構成・host所有のMain・Ask二分類・Leaf実行・承認ラベル・v3廃止・次期リリース条件 |
| [0007 — Issue起点の最小E2E経路（MVI walking skeleton）](0007-mvi-walking-skeleton.md) | Accepted (2026-11-11); I-1〜I-3 実装済み、E2E-MVI-01 実施済み、D7 追補 (2026-11-12)、D2/D2a は ADR-0008 で決定 | ADR-0006 D8より先に通す最小経路・ラベル状態機械・LXC/ユーザー分離・W1〜W5凍結（ADR-0008 で処置）・実装で確定した起動設定と権限 |
| [0008 — MVI を正とし、D8 経路の部品は必要時に取り込む](0008-mvi-mainline-and-d8-parts.md) | Accepted (2026-11-12) | 正とする経路・部品の取り込み規則・W1〜W5 の処置・KUMIKI を対象にする前提条件・設計者の役割 |

**Decision tracking:** [Issue #254](https://github.com/upiscium/KUMIKI/issues/254)。既存の v4 [Issue #189](https://github.com/upiscium/KUMIKI/issues/189)、KUMIKI/KAGARI 改名 [#242](https://github.com/upiscium/KUMIKI/issues/242)、#221 / Draft PR #224、#250 と整合させる。

これらのADRは、各時点の設計判断・実装範囲を示す歴史的記録です。[KAGARI / KUMIKI v0.1.0限定GA](https://github.com/upiscium/KUMIKI/releases/tag/v0.1.0)は公開済みですが、現行の生成先AgentCoreは **VERSION 3** のままです。外部KAGARI導入は旧設定との共存を標準とし、内部 `components/agent-core-v4/` の存在だけで任意のProjectや既存環境をアップグレード・本番適用する権限は生じません。

古い Issue・設計文書・テストは履歴証拠として保持します。矛盾がある場合は決定の前後関係・適用範囲を記録し、無断で既存の Guard や権限を変更しません。
