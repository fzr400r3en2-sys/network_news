# Network Tech Watch 使用手順

この手順は、Windows PowerShell で `C:\Users\淳一\projects\network_news` を使う前提です。README ではなく、実際に操作するときの順番だけを書いています。

## 1. 作業フォルダへ移動する

```powershell
cd C:\Users\淳一\projects\network_news
```

## 2. 初回だけ仮想環境を準備する

Python 3.12 が使える環境では次を実行します。

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Dashboard も使う場合だけ、追加で実行します。

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-app.txt
```

`py` や `python` が WindowsApps のエイリアスで失敗する場合は、`.venv\Scripts\python.exe` を直接指定してください。

## 3. 動作確認をする

```powershell
.venv\Scripts\python.exe -m pytest tests -v
.venv\Scripts\python.exe scripts\watch_network_news.py --help
.venv\Scripts\python.exe scripts\browse_network_archive.py --help
.venv\Scripts\python.exe scripts\build_network_archive_index.py --help
```

すべて成功すれば準備完了です。

## 4. ニュース収集を実行する

通常の収集はこれだけです。

```powershell
.venv\Scripts\python.exe scripts\watch_network_news.py
```

表示件数をカテゴリごとに 3 件へ絞る場合:

```powershell
.venv\Scripts\python.exe scripts\watch_network_news.py --limit 3
```

実行すると、主に次のファイルが更新されます。

- `data/latest.json`: 既知記事 ID
- `data/archive/articles-YYYY-MM.jsonl`: 新着記事 archive
- `reports/latest.md`: 詳細 report
- `reports/digest.md`: 短い digest
- `reports/digest_meta.json`: dashboard 用メタデータ

## 5. 収集結果を確認する

まず digest を見ます。

```powershell
Get-Content reports\digest.md
```

詳細を見る場合:

```powershell
Get-Content reports\latest.md
```

確認するポイントは次です。

- `New items`: 新着記事数
- `Priority hits`: watch rule に一致した記事数
- `Sources failed`: 取得失敗 source 数
- `HTML fallback`: RSS 失敗後に HTML fallback で拾えたか

`New items` が 0 でも `Sources failed` が 1 以上なら、単なる新着なしではなく部分失敗です。

## 6. archive index を更新する

収集後、archive の一覧と HTML viewer を更新します。

```powershell
.venv\Scripts\python.exe scripts\build_network_archive_index.py
```

生成されるファイル:

- `reports/archive_index.md`
- `reports/archive_summary.json`
- `reports/archive_viewer.html`

ブラウザで viewer を開く場合:

```powershell
Start-Process reports\archive_viewer.html
```

## 7. archive をコマンドで検索する

直近の archive を 10 件見る:

```powershell
.venv\Scripts\python.exe scripts\browse_network_archive.py --limit 10
```

A カテゴリだけ見る:

```powershell
.venv\Scripts\python.exe scripts\browse_network_archive.py --category A --limit 20
```

BGP 関連を探す:

```powershell
.venv\Scripts\python.exe scripts\browse_network_archive.py --query BGP --limit 20
```

Priority hit だけ見る:

```powershell
.venv\Scripts\python.exe scripts\browse_network_archive.py --priority-only --limit 20
```

特定 watch rule の記事だけ見る:

```powershell
.venv\Scripts\python.exe scripts\browse_network_archive.py --watch-rule "Critical routing and internet stability" --limit 20
```

JSON で出す:

```powershell
.venv\Scripts\python.exe scripts\browse_network_archive.py --query DNS --json
```

## 8. Dashboard を起動する

Dashboard 依存を入れていない場合は先に入れます。

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-app.txt
```

起動します。

```powershell
.venv\Scripts\streamlit run app\network_watch_dashboard.py
```

ブラウザで `http://localhost:8501` を開きます。

Dashboard では次を確認できます。

- 最新 digest の状態
- archive 累計
- morning picks
- recent articles
- priority hits
- 手動の `Run Collection`

Dashboard の `Run Collection` はローカル実行です。GitHub への commit / push や外部通知は行いません。

## 9. source を追加・変更する

編集するファイル:

```powershell
notepad config\sources.yml
```

追加後に確認します。

```powershell
.venv\Scripts\python.exe scripts\watch_network_news.py --help
.venv\Scripts\python.exe -m pytest tests -v
```

実取得して、失敗 source がないか確認します。

```powershell
.venv\Scripts\python.exe scripts\watch_network_news.py --limit 5
Get-Content reports\latest.md
```

ノイズが多い場合は、まず `max_items` と `recent_days` を小さくします。RSS が取れない場合だけ `site_url` や `html_fallback_selector_groups` を調整します。

## 10. watch rule を追加・変更する

編集する場所は `config/sources.yml` の `watch_rules` です。

変更後に収集を実行します。

```powershell
.venv\Scripts\python.exe scripts\watch_network_news.py --limit 5
.venv\Scripts\python.exe scripts\build_network_archive_index.py
```

確認するファイル:

```powershell
Get-Content reports\digest.md
Get-Content reports\archive_index.md
```

Priority hit が増えすぎる場合は、watch rule に `categories`、`sources`、`exclude_keywords` を追加して絞ります。

## 11. 既知記事 state をリセットしたい場合

通常は触りません。リセットすると、次回実行時に現在 feed で見えている記事が新着扱いになります。

```powershell
@'
{
  "updated_at": null,
  "seen_ids": []
}
'@ | Set-Content -Encoding UTF8 data\latest.json
```

その後、通常どおり実行します。

```powershell
.venv\Scripts\python.exe scripts\watch_network_news.py
```

## 12. GitHub に反映する

変更内容を確認します。

```powershell
git status --short
```

必要なファイルだけを commit します。

```powershell
git add config\sources.yml reports\digest.md reports\digest_meta.json reports\latest.md data\latest.json data\archive reports\archive_index.md reports\archive_summary.json reports\archive_viewer.html
git commit -m "chore: update network watch outputs"
git push
```

設定やコードを変更した場合は、該当ファイルも `git add` に含めます。

## 13. よく使う一連の流れ

日常の最短手順:

```powershell
cd C:\Users\淳一\projects\network_news
.venv\Scripts\python.exe scripts\watch_network_news.py --limit 5
.venv\Scripts\python.exe scripts\build_network_archive_index.py
Get-Content reports\digest.md
```

調査用:

```powershell
.venv\Scripts\python.exe scripts\browse_network_archive.py --priority-only --since-days 30 --limit 30
.venv\Scripts\python.exe scripts\browse_network_archive.py --query "RPKI" --since-days 30 --limit 30
```

検証用:

```powershell
.venv\Scripts\python.exe -m pytest tests -v
.venv\Scripts\python.exe scripts\watch_network_news.py --help
.venv\Scripts\python.exe scripts\browse_network_archive.py --help
.venv\Scripts\python.exe scripts\build_network_archive_index.py --help
```
