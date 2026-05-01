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
| ツール | バージョン | 用途 |
|---------|---------|------|
| kubectl | 1.35.3 | Kubernetes クラスタ操作 |
| helm | 3.20.1 | Helm Chart 管理 |
| k3d | 5.8.3 | ローカル k3d クラスタ管理 |
| argocd | 3.2.9 | ArgoCD CLI |
| sops | 3.12.2 | Secret 暗号化・復号 |
| age | 1.3.1 | SOPS 暗号化キー管理 |
| kubectl-argo-rollouts | 1.8.3 | Argo Rollouts 操作（Phase 11-3） |
| oha | 1.14.0 | HTTP 負荷テスト（Phase 11-4） |
| gh | 2.87.2 | GitHub CLI（Phase 11-5） |

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
- Traefik を無効化（`--disable=traefik`）。ポート80/443を ingress-nginx（後に Envoy Gateway）に明け渡すため。

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

### 設計上の決定事項
- SecretsはSOPS × Age で暗号化管理。復旧時に必要なのはAgeキー1ファイルのみ。
- クラスタ内MinIOを廃止し、WSL上の外部Dockerコンテナに移行。クラスタ消去の影響を受けない。
- ArgoCD sync-waveを導入し、CRD依存の順序制御を構造的に実装。bootstrap完全自動化を実現。
- KyvernoのPodポリシー（resources.limits必須）がCNPG recoveryをブロックする。recoveryマニフェストにresourcesを明示する必要がある。

---

## Phase 11: Hardening & Exploration

### このPhaseで解決すること
セキュリティ・デプロイ戦略・オートスケール・ネットワーキングの各レイヤーを個別に強化する。

### 完了済みステップ

| ステップ | 内容 | バージョン |
|---|---|---|
| 11-1 | Gateway API（Envoy Gateway）導入・ingress-nginx 廃止 | v1.7.2 |
| 11-2 | Trivy Operator 導入（脆弱性の継続スキャン） | 0.32.1 |
| 11-3 | Argo Rollouts 導入（カナリアデプロイ） | 2.40.9 |
| 11-4 | KEDA 導入（Prometheus メトリクスによるイベント駆動オートスケール） | 2.19.0 |
| 11-5 | sample-backend DB リトライロジック・CoreDNS 修正 | - |

### 残タスク

| ステップ | 内容 |
|---|---|
| 11-6 | Crossplane（provider-helm） |
| 11-7 | Cilium（CNI置き換え） |
| 11-8 | Local→Cloud 移行パス設計・ADR |

### Phase 11-1: Gateway API（Envoy Gateway）

ingress-nginx を廃止し、Envoy Gateway に移行。`helm template` でレンダリングした YAML を Git に格納する **Rendered Manifests Pattern** を採用。

```
Gateway エンドポイント: 172.19.0.2:80（LoadBalancer）
ルーティング方式: HTTPRoute + ReferenceGrant（クロスNamespace）
```

### Phase 11-2: Trivy Operator

全 Namespace のワークロードを継続的にスキャンし、脆弱性レポートを CRD として保存する。

```bash
# 脆弱性レポートの確認
kubectl get vulnerabilityreports --all-namespaces
```

### Phase 11-3: Argo Rollouts

sample-backend の Deployment をカナリア戦略の Rollout に移行。`common-app` Library Chart を v0.2.0 に更新し、`rollout.enabled` フラグで切り替え可能にした。

```bash
# Rollout の状態確認
kubectl argo rollouts get rollout sample-backend -n sample-app --watch

# カナリアの手動 promote
kubectl argo rollouts promote sample-backend -n sample-app
```

### Phase 11-4: KEDA

Prometheus の HTTP リクエスト数を元に sample-backend をスケールする ScaledObject を設定。`oha` による負荷テストで 1 → 5 レプリカへのスケールアウトと、負荷終了後のスケールインを確認した。

```bash
# ScaledObject の状態確認
kubectl get scaledobject -n sample-app

# 負荷テスト
oha -z 60s -c 50 --host sample-backend.localhost http://172.19.0.2/health
```

### 設計上の決定事項
- Rendered Manifests Pattern により、外部レジストリ障害時でも Git から DR 再構築が可能。Phase 10 の DR 方針と整合している。
- `common-app` Service に `name: http` ポート名を付与（v0.3.0）することで、ServiceMonitor による Prometheus scrape が正しく機能するようになった。
- KEDA は HPA を自動生成するため、`common-app` の `hpa.enabled` と併用しない。
- Argo Rollouts と KEDA を組み合わせることで、「段階的デプロイ」と「負荷ベースのオートスケール」を同一ワークロードで実現。

### Phase 11-5: DB リトライロジック・CoreDNS 修正

PostgreSQL フェイルオーバー時の接続断に対する耐性をアプリ側に実装し、あわせてクラスタ内から外部 MinIO への DNS 解決問題を修正した。

#### CoreDNS への host.k3d.internal 登録

k3d クラスタ内から `host.k3d.internal`（外部 MinIO）への名前解決ができない問題を発見。CoreDNS の NodeHosts に k3d ネットワークのゲートウェイ IP を追記することで解決した。クラスタ再作成のたびに手動で修正が必要になるため、`make bootstrap` に `fix-coredns` ターゲットを組み込んだ。

```bash
# CoreDNS を手動修正する場合
make -C k3d fix-coredns

# bootstrap 時は自動実行される
make -C k3d bootstrap
```

#### CNPG フェイルオーバー後の旧 primary 復旧手順

旧 primary が replica に戻る際、pg_wal の残存 WAL を MinIO にアーカイブしようとして `Expected empty archive` エラーが発生し、startup probe が永続的に失敗する問題を確認した。dev 環境での対処は PVC 削除による再クローンが最も確実。

```bash
# 旧 primary の Pod と PVC を削除（CNPG が新インスタンスを自動作成）
kubectl delete pod <旧primary-pod> -n sample-app
kubectl delete pvc <旧primary-pvc> -n sample-app
```

#### 設計上の決定事項
- `fix-coredns` は k3d ネットワークのゲートウェイ IP を動的に取得するため、クラスタを再作成してもIPが変わっても対応できる。
- CoreDNS の NodeHosts は k3s が管理する ConfigMap のため ArgoCD で管理しない。bootstrap スクリプトに組み込む形で冪等性を担保した。
- CNPG 旧 primary の WAL empty チェック失敗は既知の問題。PVC 削除 → 再クローンが dev 環境での標準 Runbook とする（詳細は platform-adr に記録予定）。
