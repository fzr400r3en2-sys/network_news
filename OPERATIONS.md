# Network Tech Watch Operations

## 日常運用

1. `scripts/watch_network_news.py` を実行します。
2. `reports/digest.md` で新着件数、priority hits、source failure を確認します。
3. 詳細が必要なときは `reports/latest.md` を見ます。
4. archive を見返すときは `scripts/browse_network_archive.py` か `scripts/build_network_archive_index.py` を使います。

```powershell
.venv\Scripts\python.exe scripts\watch_network_news.py --limit 5
Get-Content reports\digest.md
Get-Content reports\latest.md
```

## 失敗時の見方

- `reports/latest.md` の `Source Breakdown` で `status` と `reason` を見ます。
- `ok` は RSS 取得成功です。
- `ok_html_fallback` は RSS 失敗後に HTML fallback で記事を拾えた状態です。
- `failed` は RSS と fallback の両方で記事を取得できなかった状態です。
- `New items: 0` でも `Sources failed` が 1 以上なら、静かな日ではなく部分失敗です。

## source 追加

1. まず `config/sources.yml` に 1 から 3 件だけ追加します。
2. `url`、`site_url`、`max_items`、`recent_days`、`category_hints` を設定します。
3. `python scripts\watch_network_news.py --help` と `pytest` を通します。
4. 実取得を 1 回だけ行い、`reports/latest.md` の source breakdown を確認します。

ノイズが多い source は、先に `max_items` と `recent_days` を絞ります。RSS が不安定な source だけ `site_url` や `html_fallback_selector_groups` を調整します。

## watch rule 調整

`watch_rules` は priority の強いものから少数に保つと見やすくなります。

- `priority_level: 3`: routing / DNS / DDoS / outage など即確認したいもの
- `priority_level: 2`: cloud networking / edge / CDN など重要更新
- `priority_level: 1`: automation / telemetry / Cilium など継続観測

watch rule を変えたら archive index を再生成します。

```powershell
.venv\Scripts\python.exe scripts\build_network_archive_index.py
```

## state と archive

- `data/latest.json` は既知記事 ID の state です。消すと次回は現在見えている記事が新着扱いになります。
- `data/archive/articles-YYYY-MM.jsonl` は新着記事だけを月別に追記します。
- 初期状態の `data/` と `reports/` は最小 placeholder だけです。

## Dashboard

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-app.txt
.venv\Scripts\streamlit run app\network_watch_dashboard.py
```

Dashboard の `Run Collection` はローカルで単発収集を実行します。GitHub への commit や外部通知は行いません。
