import time
import statistics
from datetime import datetime

import pywintypes
import win32com.client


RPC_E_CALL_REJECTED = -2147418111

TESTS = [
    ("last_only", "B9"),
    ("best_bid_ask", "B23:E23"),
    ("book_10", "B23:E32"),
    ("full_snapshot", "B5:E32"),
]

SECONDS_PER_TEST = 20
POLL_INTERVAL = 0.05
RETRY_SLEEP = 0.05
MAX_RETRIES = 20


def find_sheet(excel, sheet_name):
    for wb in excel.Workbooks:
        try:
            return wb, wb.Worksheets(sheet_name)
        except Exception:
            pass
    raise RuntimeError(f"{sheet_name} が見つかりません")


def read_with_retry(ws, addr):
    retries = 0
    while True:
        t0 = time.perf_counter()
        try:
            value = ws.Range(addr).Value
            dt = (time.perf_counter() - t0) * 1000
            return value, dt, retries
        except pywintypes.com_error as e:
            hresult = e.args[0] if e.args else None
            if hresult != RPC_E_CALL_REJECTED or retries >= MAX_RETRIES:
                raise
            retries += 1
            time.sleep(RETRY_SLEEP)


def freeze(v):
    if isinstance(v, tuple):
        return tuple(freeze(x) for x in v)
    return v


def summarize(name, addr, read_ms, update_intervals, polls, updates, retry_events, retry_count):
    print()
    print(f"=== {name}  {addr} ===")
    print(f"poll回数: {polls}")
    print(f"更新回数: {updates}")
    print(f"COM retryイベント: {retry_events} / 拒否回数: {retry_count}")
    if read_ms:
        print(
            "COM読取時間 ms:",
            f"median={statistics.median(read_ms):.1f}",
            f"avg={statistics.mean(read_ms):.1f}",
            f"max={max(read_ms):.1f}",
        )
    if update_intervals:
        print(
            "値変化間隔 ms:",
            f"min={min(update_intervals):.0f}",
            f"median={statistics.median(update_intervals):.0f}",
            f"avg={statistics.mean(update_intervals):.0f}",
            f"max={max(update_intervals):.0f}",
        )


def main():
    excel = win32com.client.GetActiveObject("Excel.Application")
    wb, ws = find_sheet(excel, "FOP_DATA")

    print("Workbook:", wb.Name)
    print(
        f"{len(TESTS)}種類 × {SECONDS_PER_TEST}秒 / "
        f"polling={POLL_INTERVAL:.2f}秒"
    )

    for name, addr in TESTS:
        print()
        print(f"開始: {name} {addr}")

        end = time.perf_counter() + SECONDS_PER_TEST
        previous = None
        last_change = None

        read_ms = []
        update_intervals = []

        polls = 0
        updates = 0
        retry_events = 0
        retry_count = 0

        while time.perf_counter() < end:
            loop_start = time.perf_counter()

            try:
                value, dt_ms, retries = read_with_retry(ws, addr)
            except pywintypes.com_error as e:
                print("復旧不能COMエラー:", e)
                break

            polls += 1
            read_ms.append(dt_ms)

            if retries:
                retry_events += 1
                retry_count += retries

            state = freeze(value)

            if state != previous:
                now = time.perf_counter()
                updates += 1

                if last_change is not None:
                    delta = (now - last_change) * 1000
                    update_intervals.append(delta)
                    delta_txt = f" Δ={delta:.0f}ms"
                else:
                    delta_txt = ""

                print(
                    datetime.now().strftime("%H:%M:%S.%f")[:-3],
                    f"change#{updates}",
                    f"read={dt_ms:.1f}ms",
                    f"retry={retries}",
                    delta_txt,
                )

                previous = state
                last_change = now

            elapsed = time.perf_counter() - loop_start
            sleep_for = POLL_INTERVAL - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

        summarize(
            name, addr, read_ms, update_intervals,
            polls, updates, retry_events, retry_count
        )

        time.sleep(1)

    print()
    print("=== 比較完了 ===")


if __name__ == "__main__":
    main()
