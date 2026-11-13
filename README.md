# Rakuten MarketSpeed II RSS × Excel × Python

楽天証券 MarketSpeed II RSS を Excel 経由で Python から取得・記録する実験プロジェクトです。

## 現在の到達点

- RssChart による分足・日足等の取得
- 分析用 CSV UTF-8 出力
- 日経225マイクロ先物の現在値・10本板取得
- RssTickList による歩み値取得
- Python COM 経由で板・歩み値を同時監視
- Excel COM 一時拒否時の自動リトライ

実測では、RSS の更新間隔を 0 秒に設定した状態で、10本板は約 4〜5 回/秒、歩み値リストは約 2〜3 回/秒の変化を Python で取得できました。

## 構成

- `Excel/01_chart_selector_normalized.xlsx` — 銘柄・指数、足種、本数を選択して RssChart を取得
- `Excel/02_fop_board_ticks.xlsx` — 日経225マイクロ先物の10本板＋歩み値
- `Python/monitor_board_ticks.py` — 板＋歩み値を同時監視・CSV保存
- `Python/monitor_fop_v3.py` — 板の長時間監視、COM再試行対応
- `Python/benchmark_com_ranges.py` — COM読取範囲別の速度比較
- `VBA/RssChartCsvExport_Normalized_CP932.bas` — RssChart を分析用CSVへ保存

## Python 環境

自宅PCで確認済み:

- Python 3.12.3
- uv 0.13.8
- Git 2.53.0.windows.1
- Excel 16.0 64-bit

セットアップ:

```powershell
uv sync
```

## 実行例

板＋歩み値を2分監視:

```powershell
uv run python Python/monitor_board_ticks.py
```

5分・0.5秒ポーリング:

```powershell
uv run python Python/monitor_board_ticks.py --seconds 300 --interval 0.5
```

## データ管理

実測 CSV (`board_snapshots_*.csv`, `tick_snapshots_*.csv`, `fop_log_*.csv`) は Git 管理対象外です。コード・Excel・VBA・環境設定のみ GitHub で管理します。

## 職場PCへの引き継ぎ

GitHub から取得後、PowerShell で次を実行します。

```powershell
.\SETUP_WORKPLACE.ps1
```

MarketSpeed II にログインし、対象 Excel を開いてから Python 監視スクリプトを実行します。
