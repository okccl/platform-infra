#!/usr/bin/env python3
"""
DR リストアのために GitOps リポジトリを直接書き換える。
`make generate-dr-manifests` から呼び出される。

【動作概要】
  platform-gitops の CNPG Cluster YAML → spec.bootstrap を recovery に書き換え
  apps-gitops の values.yaml         → db.recovery フィールドを追加

【スキャン対象】
  - ~/platform-gitops/platform/**/*.yaml  (kind: Cluster + barmanObjectStore があるもの)
  - ~/apps-gitops/apps/*/*/values.yaml     (db.backup.enabled: true があるもの)

【出力】
  - 上記ファイルを直接書き換え（DR 後は git checkout -- <ファイル> で元に戻す）

【DR 手順】
  1. このスクリプトを実行（make generate-dr-manifests）
  2. 各 gitops リポジトリで git diff を確認して commit + push
  3. ArgoCD が差分を検知してクラスターを recovery bootstrap で再作成しようとする
     （CNPG webhook により既存クラスターへの変更は拒否されるが無視してよい）
  4. 対象クラスターを削除（kubectl delete cluster <name> -n <ns>）
  5. 対象 PVC を削除（kubectl delete pvc -n <ns> -l cnpg.io/cluster=<name>）
  6. ArgoCD が recovery bootstrap でクラスターを自動作成する
  7. "Cluster in healthy state" を待機
  8. DR 後: 各 gitops リポジトリで git checkout -- <ファイル> して commit + push
"""
import copy
import glob
import io
import os
import sys

import yaml

PLATFORM_GITOPS = os.path.expanduser("~/platform-gitops")
APPS_GITOPS = os.path.expanduser("~/apps-gitops")

MINIO_ENDPOINT = "http://host.k3d.internal:9000"


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)


def dump_yaml(data):
    """dict を YAML 文字列にシリアライズ（キー順序保持）"""
    stream = io.StringIO()
    yaml.dump(data, stream, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return stream.getvalue()


def make_platform_recovery_doc(doc):
    """
    platform-gitops の CNPG Cluster doc を recovery bootstrap に書き換えた dict を返す。
    externalClusters を追加し、spec.backup（WAL アーカイブ設定）は保持する。
    """
    new_doc = copy.deepcopy(doc)
    bmos = doc["spec"]["backup"]["barmanObjectStore"]
    cluster_name = doc["metadata"]["name"]

    # spec.bootstrap を recovery に変更
    new_doc["spec"]["bootstrap"] = {
        "recovery": {"source": "minio-backup"}
    }

    # spec.externalClusters を追加
    new_doc["spec"]["externalClusters"] = [{
        "name": "minio-backup",
        "barmanObjectStore": {
            "endpointURL": MINIO_ENDPOINT,
            "destinationPath": bmos["destinationPath"],
            "serverName": cluster_name,
            "s3Credentials": copy.deepcopy(bmos["s3Credentials"]),
            "wal": {"compression": "gzip"},
            "data": {"compression": "gzip"},
        }
    }]

    return new_doc


def insert_recovery_into_values_file(values_path, vals):
    """
    apps-gitops の values.yaml に db.recovery ブロックをテキスト挿入する。
    YAML の再シリアライズはせず元のフォーマット・コメントを保持する。
    db.recovery 以外のフィールドは変更しない。
    """
    with open(values_path) as f:
        original = f.read()

    db = vals.get("db", {})
    backup = db.get("backup", {})
    endpoint_url = backup.get("endpointURL", MINIO_ENDPOINT)
    bucket_name = backup.get("bucketName", "cnpg-backup")
    secret_name = backup.get("secretName", "minio-backup-secret")

    recovery_block = (
        "  recovery:\n"
        "    enabled: true\n"
        "    source: minio-backup\n"
        f"    endpointURL: \"{endpoint_url}\"\n"
        f"    bucketName: \"{bucket_name}\"\n"
        f"    secretName: \"{secret_name}\"\n"
    )

    # db: セクションの直後（次の top-level キーの行）に挿入
    lines = original.splitlines(keepends=True)
    db_section_started = False
    insert_at = None

    for i, line in enumerate(lines):
        if line.startswith("db:"):
            db_section_started = True
            continue
        if db_section_started and line.strip() and not line[0].isspace() and not line.startswith("#"):
            # 次の top-level キー（例: env:）の直前が挿入ポイント
            insert_at = i
            break

    if insert_at is None and db_section_started:
        # db: がファイル最後のセクション → 末尾に追加
        insert_at = len(lines)

    if insert_at is None:
        result = original + "\n" + recovery_block
    else:
        result = "".join(lines[:insert_at]) + recovery_block + "".join(lines[insert_at:])

    with open(values_path, "w") as f:
        f.write(result)


# ── 収集フェーズ ───────────────────────────────────────────────────────

platform_clusters = []  # {"name", "source_file", "original_doc", ...}
apps_clusters = []      # {"name", "values_file", "original_vals", ...}

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

    platform_clusters.append({
        "name": doc["metadata"]["name"],
        "source_file": path,
        "original_doc": doc,
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

    apps_clusters.append({
        "name": db["name"],
        "values_file": values_path,
        "original_vals": vals,
    })

if not platform_clusters and not apps_clusters:
    print("ERROR: DR 対象クラスターが見つかりませんでした。")
    print(f"  platform-gitops: {PLATFORM_GITOPS}")
    print(f"  apps-gitops:     {APPS_GITOPS}")
    sys.exit(1)

# ── 書き換えフェーズ ───────────────────────────────────────────────────

changed_files = []

print(f"\n【platform-gitops】{len(platform_clusters)} クラスターを recovery に書き換えます...")
for cl in platform_clusters:
    new_doc = make_platform_recovery_doc(cl["original_doc"])
    header = (
        f"# DR マニフェスト: {cl['name']}\n"
        f"# generate-dr-manifests で生成。DR 完了後は以下で元に戻すこと:\n"
        f"#   cd ~/platform-gitops && git checkout -- {os.path.relpath(cl['source_file'], PLATFORM_GITOPS)}\n"
        f"---\n"
    )
    content = header + dump_yaml(new_doc)

    with open(cl["source_file"], "w") as f:
        f.write(content)
    changed_files.append(("platform-gitops", cl["source_file"]))

    print(f"  ✓ {cl['source_file']}")

print(f"\n【apps-gitops】{len(apps_clusters)} クラスターの values.yaml を書き換えます...")
for cl in apps_clusters:
    insert_recovery_into_values_file(cl["values_file"], cl["original_vals"])
    changed_files.append(("apps-gitops", cl["values_file"]))

    print(f"  ✓ {cl['values_file']}")

# ── 次の手順を表示 ────────────────────────────────────────────────────

print("""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【次の手順】
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. 変更内容を確認する:
     cd ~/platform-gitops && git diff
     cd ~/apps-gitops && git diff

2. 各 gitops リポジトリで commit + push する:
     cd ~/platform-gitops
     git add -A && git commit -m "dr: activate recovery bootstrap" && git push

     cd ~/apps-gitops
     git add -A && git commit -m "dr: activate recovery bootstrap" && git push

3. 対象クラスターを削除する（ArgoCD が recovery bootstrap で自動再作成する）:
     kubectl delete cluster <cluster-name> -n <namespace>
     kubectl delete pvc -n <namespace> -l cnpg.io/cluster=<cluster-name>

4. "Cluster in healthy state" を待機する:
     kubectl get cluster <cluster-name> -n <namespace> -w

5. DR 完了後、gitops を元の initdb に戻す:
     cd ~/platform-gitops && git checkout -- . && git add -A && git commit -m "dr: restore initdb bootstrap" && git push
     cd ~/apps-gitops && git checkout -- . && git add -A && git commit -m "dr: restore initdb bootstrap" && git push

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
注意: ArgoCD は ignoreDifferences で spec.bootstrap の差分を無視するため、
      Step 5 後も running クラスターの bootstrap は変わらない（自然に保たれる）。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")
