"""Windowed entry point; startup failures remain visible without a console."""
try:
    from app import main
    main()
except Exception:
    import os
    import traceback
    from pathlib import Path
    details = traceback.format_exc()
    log = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'ScreenFlow' / 'startup_error.log'
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(details, encoding='utf-8')
        message = f'起動に失敗しました。\nエラー詳細: {log}\n\n{details[-2500:]}'
    except OSError:
        message = details[-3000:]
    if os.name == 'nt':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, 'ScreenFlow — 起動エラー', 0x10)
    else:
        raise
