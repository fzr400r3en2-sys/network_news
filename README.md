# Network Tech Watch

ネットワーク関連技術の RSS / 公式ブログ / 技術ブログを巡回し、キーワードと watch rule で重要記事を拾う小さな MVP です。

参照元の AI News Watch と同じ考え方で、取得、差分判定、A / B / C 分類、priority watch rule、archive、digest、簡易 dashboard を用意しています。AI News 固有の名称や分類は使っていません。

## ファイル

- `config/sources.yml`: 監視 source と `watch_rules`
- `scripts/watch_network_news.py`: RSS 取得、HTML fallback、新着判定、分類、watch rule、report 生成
- `scripts/browse_network_archive.py`: archive 検索 CLI
- `scripts/build_network_archive_index.py`: archive index / summary JSON / HTML viewer 生成 CLI
- `app/network_watch_dashboard.py`: Streamlit dashboard
- `app/dashboard_helpers.py`: dashboard 用の pure helper
- `app/watch_runner.py`: dashboard からの単発実行ラッパー
- `data/latest.json`: 既知記事 ID の state
- `data/archive/articles-YYYY-MM.jsonl`: 新着記事 archive
- `reports/latest.md`: 最新 run の詳細 report
- `reports/digest.md`: 短い digest
- `reports/digest_meta.json`: dashboard が読む機械可読メタデータ

## セットアップ

Python 3.12 前提です。

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Dashboard を使う場合だけ追加します。

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-app.txt
```

## 実行

```powershell
.venv\Scripts\python.exe scripts\watch_network_news.py
.venv\Scripts\python.exe scripts\browse_network_archive.py --limit 10
.venv\Scripts\python.exe scripts\build_network_archive_index.py
```

検証用:

```powershell
.venv\Scripts\python.exe -m pytest tests -v
.venv\Scripts\python.exe scripts\watch_network_news.py --help
.venv\Scripts\python.exe scripts\browse_network_archive.py --help
.venv\Scripts\python.exe scripts\build_network_archive_index.py --help
```

Dashboard:

```powershell
.venv\Scripts\streamlit run app\network_watch_dashboard.py
```

## 分類

- A: 重要な標準、プロトコル、セキュリティ、インターネット安定性、大規模障害、主要クラウド / ネットワーク製品の重要更新
- B: 運用改善、ツール、実装事例、設計、ベストプラクティス
- C: イベント告知、一般的な導入記事、軽めの会社ニュース

分類は `title + summary + source hints` を使う小さなルールベースです。`watch_rules` は分類後の記事に対して評価され、priority hit として report / digest / archive index に表示されます。

## 初期 watch rules

- Critical routing and internet stability
- Cloud and edge networking
- Network automation and observability

`config/sources.yml` で `keywords`、`categories`、`sources`、`exclude_keywords`、`source_overrides` を調整できます。
