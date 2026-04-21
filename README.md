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
| ツール | バージョン |
|---------|---------|
| kubectl | 1.35.3 |
| helm | 3.20.1 |
| k3d | 5.8.3 |
| argocd | 3.2.9 |

### 前提条件
- WSL2 (Ubuntu 24.04 LTS)
- mise
- direnv
- Docker Engine

---

## Phase 1: k3d Cluster IaC

### このPhaseで解決すること
クラスタ構成を `cluster.yaml` に宣言することで、手動セットアップを排除する。
エンジニアは1コマンドで同一の開発クラスタを作成・破棄・再作成できる。

### 使い方
```bash
# クラスタを作成
make -C k3d cluster-create

# ノードの状態を確認
make -C k3d cluster-status

# クラスタを破棄
make -C k3d cluster-delete
```

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

### 設計上の決定事項（追記）
- Traefik を無効化（`--disable=traefik`）。ポート80/443を ingress-nginx に明け渡すため。

---

## Phase 3: Connectivity

### このPhaseで解決すること
Ingress-nginx と cert-manager を導入し、`*.localhost` で即座にサービスを公開できる基盤を構築する。
自己署名CA証明書によりHTTPS通信を実現する。

### 使い方
```bash
# クラスタ再作成後の復旧手順
helm repo add argocd https://argoproj.github.io/argo-helm
helm repo update
helm install argocd argocd/argo-cd \
  -n argocd --create-namespace \
  -f ~/platform-gitops/platform/argocd/values.yaml \
  --wait

kubectl apply -f ~/platform-gitops/bootstrap/root.yaml
kubectl apply -f ~/platform-gitops/bootstrap/apps-root.yaml

argocd login localhost:8080 \
  --username admin \
  --password $(argocd admin initial-password -n argocd | head -1) \
  --insecure

argocd repo add git@github.com:okccl/platform-gitops.git \
  --ssh-private-key-path ~/.ssh/id_ed25519

argocd app sync root --server-side --async
argocd app sync ingress-nginx --async
argocd app sync external-secrets --server-side --async
argocd app sync root --server-side --async
```

---

## Phase 9: Resilience & Chaos Engineering

### このPhaseで解決すること
ノード障害時にサービスが継続できることを、実際に障害を起こして検証する。
`cluster.yaml` の変更だけで3ノード構成に移行し、AntiAffinity が機能することを確認する。

### クラスタ構成の変更（`k3d/cluster.yaml`）
エージェントノード数を 2 → 3 に変更。同一Podが同一ノードに集中しないよう
`podAntiAffinity` を `platform-gitops` 側の values で設定する。

```bash
# 3ノードクラスタで再作成
make -C k3d cluster-delete
make -C k3d cluster-create
```

### Chaos Engineering の手順

```bash
# 対象ノードを退去させてノード障害をシミュレート
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data

# DB Primary の切替確認（CNPG の自動フェイルオーバー）
kubectl get cluster -n <namespace> -w

# ノードを復旧
kubectl uncordon <node-name>
```

### 設計上の決定事項
- VPA（Vertical Pod Autoscaler）を導入し、リソース上限の最適値を Grafana で観察した。
- Chaos Engineering の結果として、DB Primaryの切替（Switchover）が RTO に与える影響を計測した。

---

## Phase 10: Disaster Recovery (DR)

### このPhaseで解決すること
クラスタを完全に破壊した状態から `make init` の1コマンドで復旧できることを示す。
RTO（目標復旧時間）を計測し、README に記録する。

### DR手順（クラスタ全損からの復旧）

```bash
# 1. クラスタ再作成
make -C k3d cluster-create

# 2. ArgoCD 起動 & GitOps 再接続（Phase 3 の手順に従う）
#    → platform-gitops の全コンポーネントが自動復旧

# 3. MinIO からの DB リストア（バックアップ有効時）
#    → CNPG の Cluster マニフェストに bootstrap.recovery を設定して apply
```

### 設計上の決定事項
- クラスタ状態はすべて Git（platform-gitops）に記録されており、再 apply で復元できる。
- Secrets は SOPS × Age で暗号化管理（Phase 10）。復旧時に平文の Secret をローカルに持つ必要がない。
- DB バックアップは MinIO（Phase 8）に S3互換形式で保存。CNPG の Point-in-Time Recovery に対応。

---

## Phase 11: Cloud Expansion（作業中）

### このPhaseで解決すること
ローカル（k3d）で構築した基盤をそのままクラウドへ展開し、
`terraform/` でコード化した EKS クラスタに同じ GitOps フローを適用する。

### ディレクトリ構成

```
platform-infra/
└── terraform/
    └── eks/        # EKS クラスタ定義（作業中）
```

### 使い方（予定）
```bash
cd terraform/eks
terraform init
terraform apply
```

### 設計上の決定事項
- ローカルの ingress-nginx から Gateway API（Envoy Gateway）へ移行予定。
- EKS 上でも同じ `platform-gitops` の bootstrap 手順で全コンポーネントを展開できることを目標とする。
