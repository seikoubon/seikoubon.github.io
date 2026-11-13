import argparse
import csv
import statistics
import time
from datetime import datetime
from pathlib import Path

import pywintypes
import win32com.client


RPC_E_CALL_REJECTED = -2147418111


def find_workbook_with_sheet(excel, sheet_name):
    for wb in excel.Workbooks:
        try:
            wb.Worksheets(sheet_name)
            return wb
        except Exception:
            pass
    return None


def normalize(v):
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def fmt(v):
    if isinstance(v, datetime):
        return v.strftime("%H:%M:%S")
    return str(v)


def percentile(values, p):
    if not values:
        return None
    vals = sorted(values)
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    frac = pos - lo
    return vals[lo] * (1 - frac) + vals[hi] * frac


def read_snapshot(ws, retries=30, retry_sleep=0.1):
    """
    ExcelがRSS更新・再計算・UI処理中に RPC_E_CALL_REJECTED を返すことがある。
    その場合だけ短時間待って再試行する。
    """
    last_error = None

    for attempt in range(retries):
        try:
            return ws.Range("B5:E32").Value, attempt
        except pywintypes.com_error as e:
            last_error = e
            hresult = e.args[0] if e.args else None

            if hresult != RPC_E_CALL_REJECTED:
                raise

            time.sleep(retry_sleep)

    raise last_error


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=300.0)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--warmup", type=float, default=10.0)
    parser.add_argument("--retries", type=int, default=30)
    parser.add_argument("--retry-sleep", type=float, default=0.1)
    args = parser.parse_args()

    excel = win32com.client.GetActiveObject("Excel.Application")
    wb = find_workbook_with_sheet(excel, "FOP_DATA")
    if wb is None:
        raise RuntimeError("FOP_DATA シートのあるExcelブックが見つかりません")

    ws = wb.Worksheets("FOP_DATA")
    symbol = ws.Range("B2").Value

    out = Path.cwd() / f"fop_log_v3_{datetime.now():%Y%m%d_%H%M%S}.csv"

    headers = [
        "pc_time",
        "rss_trade_time",
        "last",
        "night_volume",
        "change_type",
        "retry_count",
    ]
    for i in range(1, 11):
        headers += [f"ask{i}", f"ask_qty{i}", f"bid{i}", f"bid_qty{i}"]

    print("Workbook:", wb.Name)
    print("Symbol:", symbol)
    print(
        f"{args.seconds:g}秒監視 / polling={args.interval:g}秒 / "
        f"warmup={args.warmup:g}秒"
    )
    print("CSV:", out)
    print()

    start_perf = time.perf_counter()
    end_perf = start_perf + args.seconds

    previous = None
    previous_trade = None
    previous_book = None

    measured_change_times = []
    all_change_times = []

    counts = {
        "trade_only": 0,
        "book_only": 0,
        "both": 0,
        "other": 0,
    }

    polls = 0
    rejected_calls = 0
    retry_events = 0

    with out.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(headers)

        try:
            while time.perf_counter() < end_perf:
                loop_start = time.perf_counter()

                try:
                    snapshot, retries_used = read_snapshot(
                        ws,
                        retries=args.retries,
                        retry_sleep=args.retry_sleep,
                    )
                except pywintypes.com_error as e:
                    print()
                    print("COM読み取りを再試行しても復旧できませんでした:", e)
                    break

                if retries_used:
                    rejected_calls += retries_used
                    retry_events += 1
                    print(
                        f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} "
                        f"Excel busy -> retry {retries_used}回で復旧"
                    )

                basic = [snapshot[i][0] for i in range(16)]

                last = normalize(basic[4])          # B9 現在値
                rss_trade_time = basic[5]           # B10 現在値詳細時刻
                night_volume = normalize(basic[14]) # B19 夕場出来高

                book_rows = snapshot[18:28]

                levels = []
                for row in book_rows:
                    ask, ask_qty, bid, bid_qty = [normalize(v) for v in row]
                    levels.extend([ask, ask_qty, bid, bid_qty])

                trade_state = (rss_trade_time, last, night_volume)
                book_state = tuple(levels)
                state = trade_state + book_state

                polls += 1

                if previous is None or state != previous:
                    now_perf = time.perf_counter()
                    now = datetime.now()
                    elapsed = now_perf - start_perf

                    if previous is None:
                        change_type = "initial"
                    else:
                        trade_changed = trade_state != previous_trade
                        book_changed = book_state != previous_book

                        if trade_changed and book_changed:
                            change_type = "both"
                        elif trade_changed:
                            change_type = "trade_only"
                        elif book_changed:
                            change_type = "book_only"
                        else:
                            change_type = "other"

                    writer.writerow([
                        now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                        fmt(rss_trade_time),
                        last,
                        night_volume,
                        change_type,
                        retries_used,
                        *levels,
                    ])
                    f.flush()

                    if previous is not None:
                        delta_ms = (now_perf - all_change_times[-1]) * 1000
                    else:
                        delta_ms = None

                    all_change_times.append(now_perf)

                    if elapsed >= args.warmup and previous is not None:
                        measured_change_times.append(now_perf)
                        if change_type in counts:
                            counts[change_type] += 1

                    ask1, ask_qty1, bid1, bid_qty1 = levels[:4]

                    warm_mark = " [warmup]" if elapsed < args.warmup else ""
                    delta_text = "" if delta_ms is None else f" Δ={delta_ms:.0f}ms"

                    print(
                        f"{now.strftime('%H:%M:%S.%f')[:-3]} "
                        f"RSS={fmt(rss_trade_time)} "
                        f"Last={last} "
                        f"Ask={ask1}({ask_qty1}) "
                        f"Bid={bid1}({bid_qty1}) "
                        f"Vol={night_volume} "
                        f"{change_type}"
                        f"{delta_text}"
                        f"{warm_mark}"
                    )

                    previous = state
                    previous_trade = trade_state
                    previous_book = book_state

                sleep_for = args.interval - (time.perf_counter() - loop_start)
                if sleep_for > 0:
                    time.sleep(sleep_for)

        finally:
            intervals_ms = [
                (b - a) * 1000
                for a, b in zip(measured_change_times, measured_change_times[1:])
            ]

            print()
            print("=== 集計（warmup除外） ===")
            print("poll回数:", polls)
            print("更新回数:", len(measured_change_times))
            print(
                "内訳:",
                f"both={counts['both']}",
                f"trade_only={counts['trade_only']}",
                f"book_only={counts['book_only']}",
                f"other={counts['other']}",
            )
            print(
                "Excel COM拒否:",
                f"retryイベント={retry_events}",
                f"拒否回数={rejected_calls}",
            )

            if intervals_ms:
                print(
                    "変化間隔 ms:",
                    f"min={min(intervals_ms):.0f}",
                    f"median={statistics.median(intervals_ms):.0f}",
                    f"avg={statistics.mean(intervals_ms):.0f}",
                    f"p95={percentile(intervals_ms, 0.95):.0f}",
                    f"max={max(intervals_ms):.0f}",
                )
            else:
                print("変化間隔 ms: 集計できる更新数がありません")

            print("保存:", out)


if __name__ == "__main__":
    main()
