import argparse
import csv
import time
from datetime import datetime
from pathlib import Path

import pywintypes
import win32com.client

RPC_E_CALL_REJECTED = -2147418111


def find_workbook(excel):
    for wb in excel.Workbooks:
        try:
            wb.Worksheets("FOP_DATA")
            wb.Worksheets("TICK_DATA")
            return wb
        except Exception:
            pass
    return None


def read_retry(ws, address, retries=20, retry_sleep=0.05):
    last = None
    for n in range(retries + 1):
        try:
            return ws.Range(address).Value, n
        except pywintypes.com_error as e:
            last = e
            hresult = e.args[0] if e.args else None
            if hresult != RPC_E_CALL_REJECTED or n >= retries:
                raise
            time.sleep(retry_sleep)
    raise last


def freeze(v):
    if isinstance(v, tuple):
        return tuple(freeze(x) for x in v)
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def locate_tick_rows(rows):
    """
    RssTickListの出力形が
      状態行 → ヘッダー → データ
    または
      ヘッダー → データ
    のどちらでも、時刻/出来高/約定値ヘッダーを探す。
    """
    header = None
    for i, row in enumerate(rows):
        vals = [str(x).strip() if x is not None else "" for x in row]
        if len(vals) >= 3 and vals[0] == "時刻" and vals[1] == "出来高" and vals[2] == "約定値":
            header = i
            break

    if header is None:
        return []

    out = []
    for row in rows[header + 1:]:
        t, vol, price = row[:3]
        if t is None and vol is None and price is None:
            break
        if str(t).strip().startswith("-"):
            break
        out.append((freeze(t), freeze(vol), freeze(price)))
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--seconds", type=float, default=120)
    p.add_argument("--interval", type=float, default=0.20)
    p.add_argument("--tick-depth", type=int, default=20)
    args = p.parse_args()

    excel = win32com.client.GetActiveObject("Excel.Application")
    wb = find_workbook(excel)
    if wb is None:
        raise RuntimeError("FOP_DATA と TICK_DATA のあるブックが見つかりません")

    fop = wb.Worksheets("FOP_DATA")
    tick = wb.Worksheets("TICK_DATA")
    symbol = fop.Range("B2").Value

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    board_path = Path.cwd() / f"board_snapshots_{stamp}.csv"
    tick_path = Path.cwd() / f"tick_snapshots_{stamp}.csv"

    board_header = ["pc_time", "rss_trade_time", "last", "night_volume"]
    for i in range(1, 11):
        board_header += [f"ask{i}", f"ask_qty{i}", f"bid{i}", f"bid_qty{i}"]

    tick_header = [
        "snapshot_id", "pc_time", "rank",
        "tick_time", "volume", "price",
    ]

    print("Workbook:", wb.Name)
    print("Symbol:", symbol)
    print(f"{args.seconds:g}秒 / polling={args.interval:g}秒 / tick top={args.tick_depth}")
    print("board:", board_path)
    print("ticks:", tick_path)
    print()

    prev_board = None
    prev_ticks = None
    board_changes = 0
    tick_changes = 0
    retry_total = 0
    snapshot_id = 0

    start = time.perf_counter()
    end = start + args.seconds

    with board_path.open("w", newline="", encoding="utf-8-sig") as bf, \
         tick_path.open("w", newline="", encoding="utf-8-sig") as tf:

        bw = csv.writer(bf)
        tw = csv.writer(tf)
        bw.writerow(board_header)
        tw.writerow(tick_header)

        while time.perf_counter() < end:
            loop_start = time.perf_counter()
            now = datetime.now()
            pc_time = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

            # 板・基本情報
            board_raw, r1 = read_retry(fop, "B5:E32")
            retry_total += r1

            basic = [board_raw[i][0] for i in range(16)]
            last = freeze(basic[4])
            rss_trade_time = basic[5]
            night_volume = freeze(basic[14])

            book_rows = board_raw[18:28]
            levels = []
            for row in book_rows:
                ask, ask_qty, bid, bid_qty = [freeze(v) for v in row]
                levels.extend([ask, ask_qty, bid, bid_qty])

            board_state = (rss_trade_time, last, night_volume, *levels)

            if board_state != prev_board:
                bw.writerow([pc_time, rss_trade_time, last, night_volume, *levels])
                bf.flush()
                board_changes += 1
                prev_board = board_state

            # 歩み値：最大300本 + 余白を含む
            tick_raw, r2 = read_retry(tick, "A6:C310")
            retry_total += r2
            tick_rows = locate_tick_rows(tick_raw)

            # 上位N本だけを比較・保存
            top = tuple(tick_rows[:args.tick_depth])

            if top and top != prev_ticks:
                snapshot_id += 1
                tick_changes += 1
                for rank, row in enumerate(top, start=1):
                    t, vol, price = row
                    tw.writerow([snapshot_id, pc_time, rank, t, vol, price])
                tf.flush()
                prev_ticks = top

                top1 = top[0]
                print(
                    f"{now.strftime('%H:%M:%S.%f')[:-3]} "
                    f"Last={last} "
                    f"Tick={top1[0]} price={top1[2]} vol={top1[1]} "
                    f"board#{board_changes} tick#{tick_changes}"
                )

            elapsed = time.perf_counter() - loop_start
            sleep_for = args.interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

    print()
    print("=== 結果 ===")
    print("板変化回数:", board_changes)
    print("歩み値リスト変化回数:", tick_changes)
    print("COM再試行回数:", retry_total)
    print("保存:", board_path)
    print("保存:", tick_path)


if __name__ == "__main__":
    main()
