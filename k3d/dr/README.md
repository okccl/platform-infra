# k3d/dr/

DR（障害復旧）用の CNPG リカバリーマニフェスト出力ディレクトリ。

## 使い方

```bash
cd ~/platform-infra
make generate-dr-manifests
```

`platform-gitops` / `apps-gitops` の GitOps ソースをスキャンし、各 CNPG クラスターの `bootstrap.recovery` マニフェストをここに生成する。

生成後、DR 手順に従って `kubectl apply` する（Runbook 参照）。

## 注意

- 生成ファイル（`*.yaml`）は `.gitignore` 済み。Git 管理しない。
- 静的ファイルを置かずに都度生成する設計はドリフト防止のため（クラスター設定変更時に自動で最新を反映する）。
- 生成スクリプト: `k3d/scripts/generate-dr-manifests.py`
