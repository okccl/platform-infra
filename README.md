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
| エージェントノード数 | 3 |
| HTTPポート | 80 |
| HTTPSポート | 443 |

### 設計上の決定事項
- ポート80/443はPhase 3（Ingress導入）に備え、ロードバランサーノードにマッピング済み。
- クラスタ作成時にkubeconfigへの自動マージとコンテキストの切り替えを行う。
- Traefik を無効化（`--disable=traefik`）。ポート80/443を ingress-nginx に明け渡すため。

---

## Phase 3: Connectivity

### このPhaseで解決すること
Ingress-nginx と cert-manager を導入し、`*.localhost` で即座にサービスを公開できる基盤を構築する。
自己署名CA証明書によりHTTPS通信を実現する。

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
クラスタを完全に破壊した状態から `make bootstrap` の1コマンドで完全自動復旧できることを実証する。
RTOを実測し、バックアップ/リストアまで含めたDR手順を確立する。

### DR手順（クラスタ全損からの復旧）

```bash
# 1. Ageキーが存在することを確認（別マシンの場合はコピーが必要）
ls ~/.config/sops/age/keys.txt

# 2. 外部MinIOが起動していることを確認
docker ps | grep minio-external

# 3. bootstrap実行（これだけで完全復旧）
cd k3d
make bootstrap
```

### RTO計測結果

| 指標 | 時間 |
|---|---|
| RTO① `make bootstrap` 完了 | **7分37秒** |
| RTO② 全App Synced/Healthy | **15分24秒** |
| 手動作業 | 新規マシンの場合のAgeキーコピーのみ |

### 外部MinIOによるDR基盤

クラスタ内にMinIOを立てていたが、クラスタ全損の想定ではバックアップとして機能しないため、
WSL上のDockerコンテナとして外部MinIOを構築し移行した。
なお、今後のクラウド展開のPhaseでクラウドへのバックアップも検討する。

```bash
# 外部MinIO起動（--restart unless-stopped でWSL再起動後も自動復旧）
docker run -d \
  --name minio-external \
  --restart unless-stopped \
  -p 9000:9000 -p 9001:9001 \
  -v ~/minio-data:/data \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=xxx \
  quay.io/minio/minio:latest \
  server /data --console-address ":9001"
```

### DBリストア手順（CNPGのrecovery）

```bash
# ArgoCD自動syncを止めてからrecoveryクラスタを作成
kubectl scale statefulset argocd-application-controller -n argocd --replicas=0

kubectl apply -f - <<EOF
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: sample-backend-db
  namespace: sample-app
spec:
  instances: 2
  imageName: ghcr.io/cloudnative-pg/postgresql:17
  bootstrap:
    recovery:
      source: sample-backend-db
  externalClusters:
    - name: sample-backend-db
      barmanObjectStore:
        endpointURL: "http://host.k3d.internal:9000"
        destinationPath: "s3://cnpg-backup/sample-backend-db"
        s3Credentials:
          accessKeyId:
            name: minio-backup-secret
            key: ACCESS_KEY_ID
          secretAccessKey:
            name: minio-backup-secret
            key: ACCESS_SECRET_KEY
        wal:
          compression: gzip
        data:
          compression: gzip
  storage:
    size: 1Gi
  resources:
    requests:
      cpu: 100m
      memory: 256Mi
    limits:
      cpu: 500m
      memory: 512Mi
EOF

# 復旧確認後にArgoCD再起動
kubectl scale statefulset argocd-application-controller -n argocd --replicas=1
```

### 設計上の決定事項
- SecretsはSOPS × Age で暗号化管理。復旧時に必要なのはAgeキー1ファイルのみ。
- クラスタ内MinIOを廃止し、WSL上の外部Dockerコンテナに移行。クラスタ消去の影響を受けない。
- ArgoCD sync-waveを導入し、CRD依存の順序制御を構造的に実装。bootstrap完全自動化を実現。
- KyvernoのPodポリシー（resources.limits必須）がCNPG recoveryをブロックする。recoveryマニフェストにresourcesを明示する必要がある。

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
- DBバックアップ先をAWS S3に移行することで、真のクラウドDRを実現予定。
