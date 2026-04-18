# platform-infra
Platform Engineering portfolio — Infrastructure as Code repository.

## Phase 0: Local Foundation

### このPhaseで解決すること
「自分のマシンでは動く」問題を、ローカル開発環境の完全なコード化によって解消する。
エンジニアは1コマンドで同一のツールセットを再現できる。

### 使い方
```bash
# 全ツールをインストール
make init

# ツールバージョンを確認
make check
```

### miseで管理するツール
| ツール    | バージョン |
|---------|---------|
| kubectl | 1.35.3  |
| helm    | 3.20.1  |
| k3d     | 5.8.3   |
| argocd  | 3.2.9   |

### 前提条件
- WSL2 (Ubuntu 24.04 LTS)
- mise
- direnv
- Docker Engine

## Phase 1: k3d Cluster IaC

### このPhaseで解決すること
クラスタ構成を `cluster.yaml` に宣言することで、手動セットアップを排除する。
エンジニアは1コマンドで同一の開発クラスタを作成・破棄・再作成できる。

### 使い方
\`\`\`bash
# クラスタを作成
make -C k3d cluster-create

# ノードの状態を確認
make -C k3d cluster-status

# クラスタを破棄
make -C k3d cluster-delete
\`\`\`

### クラスタ構成（`k3d/cluster.yaml`）
| 項目 | 値 |
|---|---|
| クラスタ名 | dev |
| コントロールプレーンノード数 | 1 |
| エージェントノード数 | 2 |
| HTTPポート | 80 |
| HTTPSポート | 443 |

### 設計上の決定事項
- ポート80/443はPhase 3（Ingress導入）に備え、ロードバランサーノードにマッピング済み。
- クラスタ作成時にkubeconfigへの自動マージとコンテキストの切り替えを行う。
