#!/usr/bin/env python3
"""
DR マニフェストを GitOps ソースから生成する。
`make generate-dr-manifests` から呼び出される。

スキャン対象:
  - ~/platform-gitops/platform/**/*.yaml  (kind: Cluster + barmanObjectStore があるもの)
  - ~/apps-gitops/apps/*/*/values.yaml     (db.backup.enabled: true があるもの)

出力先: k3d/dr/<cluster-name>-recovery.yaml
"""
import yaml
import os
import glob
import sys

PLATFORM_GITOPS = os.path.expanduser("~/platform-gitops")
APPS_GITOPS = os.path.expanduser("~/apps-gitops")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "..", "k3d", "dr")

TEMPLATE = """\
# DR マニフェスト: {name}
# 生成元: make generate-dr-manifests（GitOps ソースから自動生成）
#
# クラスター全損（k3d cluster delete）→ MinIO からリストア:
#   endpointURL: "http://host.k3d.internal:9000"  ← デフォルト
#
# WSL 全損 → GCS からリストア:
#   endpointURL を GCS エンドポイントに変更してから apply（Runbook 参照）
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: {name}
  namespace: {namespace}
spec:
  instances: {instances}
  imageName: {image}
  storage:
    size: {storage_size}
    storageClass: {storage_class}
  bootstrap:
    recovery:
      source: minio-backup
  externalClusters:
    - name: minio-backup
      barmanObjectStore:
        endpointURL: "http://host.k3d.internal:9000"
        destinationPath: "{destination_path}"
        serverName: "{name}"
        s3Credentials:
          accessKeyId:
            name: {secret_name}
            key: ACCESS_KEY_ID
          secretAccessKey:
            name: {secret_name}
            key: ACCESS_SECRET_KEY
        wal:
          compression: gzip
        data:
          compression: gzip
  resources:
    requests:
      memory: "256Mi"
      cpu: "100m"
    limits:
      memory: "512Mi"
      cpu: "500m"
  monitoring:
    enablePodMonitor: true
"""


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)


clusters = []

# platform-gitops: kind=Cluster + barmanObjectStore を持つ YAML を自動検出
for path in sorted(glob.glob(f"{PLATFORM_GITOPS}/platform/**/*.yaml", recursive=True)):
    try:
        doc = load_yaml(path)
    except Exception:
        continue
    if not isinstance(doc, dict):
        continue
    if doc.get("apiVersion") != "postgresql.cnpg.io/v1" or doc.get("kind") != "Cluster":
        continue
    bmos = doc.get("spec", {}).get("backup", {}).get("barmanObjectStore")
    if not bmos:
        continue
    spec = doc["spec"]
    clusters.append({
        "name": doc["metadata"]["name"],
        "namespace": doc["metadata"]["namespace"],
        "instances": spec["instances"],
        "image": spec["imageName"],
        "storage_size": spec["storage"]["size"],
        "storage_class": spec["storage"].get("storageClass", "local-path-retain"),
        "destination_path": bmos["destinationPath"],
        "secret_name": bmos["s3Credentials"]["accessKeyId"]["name"],
    })

# apps-gitops: db.backup.enabled=true の values.yaml を自動検出
for values_path in sorted(glob.glob(f"{APPS_GITOPS}/apps/*/*/values.yaml")):
    try:
        vals = load_yaml(values_path)
    except Exception:
        continue
    db = vals.get("db", {})
    if not db.get("enabled", True):
        continue
    if not db.get("backup", {}).get("enabled"):
        continue

    # namespace を同階層の application.yaml から取得
    app_yaml = os.path.join(os.path.dirname(values_path), "application.yaml")
    namespace = "default"
    if os.path.exists(app_yaml):
        try:
            app = load_yaml(app_yaml)
            namespace = app["spec"]["destination"]["namespace"]
        except Exception:
            pass

    clusters.append({
        "name": db["name"],
        "namespace": namespace,
        "instances": db.get("instances", 1),
        "image": f"ghcr.io/cloudnative-pg/postgresql:{db.get('postgresVersion', 17)}",
        "storage_size": db.get("storageSize", "1Gi"),
        "storage_class": db.get("storageClassName", "local-path-retain"),
        "destination_path": f"s3://{db['backup']['bucketName']}/{db['name']}",
        "secret_name": db["backup"]["secretName"],
    })

if not clusters:
    print("ERROR: DR マニフェストの生成対象クラスターが見つかりませんでした。")
    print(f"  platform-gitops: {PLATFORM_GITOPS}")
    print(f"  apps-gitops:     {APPS_GITOPS}")
    sys.exit(1)

os.makedirs(OUTPUT_DIR, exist_ok=True)
print(f"{len(clusters)} クラスターの DR マニフェストを生成します...")
for cl in clusters:
    fname = os.path.join(OUTPUT_DIR, f"{cl['name']}-recovery.yaml")
    with open(fname, "w") as f:
        f.write(TEMPLATE.format(**cl))
    print(f"  生成: {fname}")
print("完了。内容を確認してから kubectl apply してください。")
