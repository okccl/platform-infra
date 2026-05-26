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
  - 上記ファイルを直接書き換え（DR 完了後も gitops は recovery のまま維持する）

【DR 手順】
  1. このスクリプトを実行（make generate-dr-manifests）
  2. 各 gitops リポジトリで git diff を確認して commit + push
  3. ArgoCD が差分を検知してクラスターを recovery bootstrap で再作成しようとする
     （CNPG webhook により既存クラスターへの変更は拒否されるが無視してよい）
  4. 対象クラスターを削除（kubectl delete cluster <name> -n <ns>）
  5. 対象 PVC を削除（kubectl delete pvc -n <ns> -l cnpg.io/cluster=<name>）
  6. ArgoCD が recovery bootstrap でクラスターを自動作成する
  7. "Cluster in healthy state" を待機

【DR 完了後の gitops について】
  gitops は recovery bootstrap のまま維持する（git checkout -- は不要）。
  spec.bootstrap は一度きりの初期化イベントであり、running クラスターの動作には影響しない。
  WAL アーカイブは backup.serverName が空の新規パスを指しているため正常に継続される。
"""
import copy
import glob
import io
import os
import sys
from datetime import date

import yaml

DATE_SUFFIX = date.today().strftime("%Y%m%d")

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
    externalClusters を追加し、spec.backup.barmanObjectStore.serverName を新規パスに変更する。

    serverName の扱い:
      - externalClusters.serverName: 旧バックアップ参照先
        現行の backup.serverName（未設定時はクラスター名）を引き継ぐ
      - backup.serverName: 新規 WAL 書き込み先（空パス）
        {cluster_name}-{YYYYMMDD} に変更し、barman-cloud-check-wal-archive を通過させる
    """
    new_doc = copy.deepcopy(doc)
    bmos = doc["spec"]["backup"]["barmanObjectStore"]
    cluster_name = doc["metadata"]["name"]

    # 現行の serverName を読む（未設定時はクラスター名がデフォルト）
    old_server_name = bmos.get("serverName", cluster_name)
    new_server_name = f"{cluster_name}-{DATE_SUFFIX}"

    # spec.bootstrap を recovery に変更
    new_doc["spec"]["bootstrap"] = {
        "recovery": {"source": "minio-backup"}
    }

    # spec.externalClusters を追加（旧バックアップを参照）
    new_doc["spec"]["externalClusters"] = [{
        "name": "minio-backup",
        "barmanObjectStore": {
            "endpointURL": MINIO_ENDPOINT,
            "destinationPath": bmos["destinationPath"],
            "serverName": old_server_name,
            "s3Credentials": copy.deepcopy(bmos["s3Credentials"]),
            "wal": {"compression": "gzip"},
            "data": {"compression": "gzip"},
        }
    }]

    # spec.backup.barmanObjectStore.serverName を新規パス（空）に変更
    new_doc["spec"]["backup"]["barmanObjectStore"]["serverName"] = new_server_name

    return new_doc


def _set_serverName_in_block(text, block_name, server_name):
    """
    db.<block_name> ブロック内の serverName を追加または置換する（テキスト操作）。
    block_name は 'backup' または 'recovery'。インデントは 4 スペース固定。
    """
    lines = text.splitlines(keepends=True)
    in_db = False
    in_block = False

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if not in_db:
            if line.startswith("db:"):
                in_db = True
        elif not in_block:
            if indent == 0 and stripped.strip():
                return "".join(lines)  # db: ブロックを抜けた（block が見つからなかった）
            if indent == 2 and stripped.rstrip() == f"{block_name}:":
                in_block = True
        else:
            # db.<block_name> ブロック内
            if indent == 4 and stripped.startswith("serverName:"):
                # 既存 serverName を置換
                lines[i] = f"    serverName: \"{server_name}\"\n"
                return "".join(lines)
            if indent <= 2 and stripped.strip():
                # ブロック終端（次の peer キーまたは top-level キー）の直前に挿入
                lines.insert(i, f"    serverName: \"{server_name}\"\n")
                return "".join(lines)

    # ファイル末尾までブロック内だった場合（ブロックがファイル最後）
    if in_block:
        lines.append(f"    serverName: \"{server_name}\"\n")
    return "".join(lines)


def insert_recovery_into_values_file(values_path, vals):
    """
    apps-gitops の values.yaml を DR 用に書き換える（テキスト操作）。
    YAML の再シリアライズはせず元のフォーマット・コメントを保持する。

    処理内容:
      - db.recovery が未設定 → recovery ブロックを追加（serverName 含む）
      - db.recovery が設定済み → recovery.serverName を追加/更新
      - db.backup.serverName を新規パスに追加/更新

    serverName の扱い:
      - recovery.serverName: 旧バックアップ参照先
        現行の backup.serverName（未設定時は db.name）を引き継ぐ
      - backup.serverName: 新規 WAL 書き込み先（空パス）
        {db.name}-{YYYYMMDD} に変更し、barman-cloud-check-wal-archive を通過させる
    """
    with open(values_path) as f:
        original = f.read()

    db = vals.get("db", {})
    backup = db.get("backup", {})
    recovery = db.get("recovery", {})
    db_name = db["name"]
    endpoint_url = backup.get("endpointURL", MINIO_ENDPOINT)
    bucket_name = backup.get("bucketName", "cnpg-backup")
    secret_name = backup.get("secretName", "minio-backup-secret")

    # serverName の決定
    old_server_name = backup.get("serverName") or db_name   # 旧バックアップ参照先
    new_server_name = f"{db_name}-{DATE_SUFFIX}"             # 新規書き込み先

    result = original

    if not recovery.get("enabled"):
        # recovery ブロックが未設定 → 追加
        recovery_block = (
            "  recovery:\n"
            "    enabled: true\n"
            "    source: minio-backup\n"
            f"    endpointURL: \"{endpoint_url}\"\n"
            f"    bucketName: \"{bucket_name}\"\n"
            f"    secretName: \"{secret_name}\"\n"
            f"    serverName: \"{old_server_name}\"\n"
        )

        lines = result.splitlines(keepends=True)
        db_section_started = False
        insert_at = None

        for i, line in enumerate(lines):
            if line.startswith("db:"):
                db_section_started = True
                continue
            if db_section_started and line.strip() and not line[0].isspace() and not line.startswith("#"):
                insert_at = i
                break

        if insert_at is None and db_section_started:
            insert_at = len(lines)

        if insert_at is None:
            result = result + "\n" + recovery_block
        else:
            result = "".join(lines[:insert_at]) + recovery_block + "".join(lines[insert_at:])
    else:
        # recovery ブロックが設定済み → serverName を追加/更新
        result = _set_serverName_in_block(result, "recovery", old_server_name)

    # db.backup.serverName を新規パスに追加/更新
    result = _set_serverName_in_block(result, "backup", new_server_name)

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
        f"# generate-dr-manifests で生成（{DATE_SUFFIX}）。gitops は recovery のまま維持する。\n"
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
     kubectl delete pvc -n <namespace> -l cnpg.io/cluster=<cluster-name>  # シナリオ A のみ

4. "Cluster in healthy state" を待機する:
     kubectl get cluster <cluster-name> -n <namespace> -w

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
注意: DR 完了後、gitops は recovery bootstrap のまま維持する（git checkout -- は不要）。
      spec.bootstrap は一度きりの初期化イベントで、running クラスターの動作には影響しない。
      backup.serverName が空の新規パスを指しているため、WAL アーカイブは正常に継続される。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")
