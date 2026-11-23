"""程序入口。"""

import ctypes
import os
import sys
import threading
import tkinter as tk
from tkinter import messagebox

from app_ui import App, create_root
from config_store import (
    ERROR_LOG_PATH, cleanup_stale_temp_folders, log_exception,
)
from theme_ui import UI_SCALE

SINGLE_INSTANCE_HANDLE = None


def acquire_single_instance(name: str = 'Local\\ohMyAssNoto_SingleInstance') -> bool:
    """Windows 命名互斥体，防止两个实例同时写配置。"""
    global SINGLE_INSTANCE_HANDLE
    if os.name != 'nt':
        return True
    try:
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [
            wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            return True
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return False
        SINGLE_INSTANCE_HANDLE = handle
        return True
    except Exception as exc:
        # 单实例检查失败不应阻止程序启动，但要留下记录
        log_exception('单实例检查', exc)
        return True


def _mark_update_success():
    """更新批处理会设置该变量；新版本成功启动后写标记，失败则自动回滚。"""
    marker = os.environ.get('OMA_UPDATE_MARKER')
    if not marker:
        return
    try:
        with open(marker, 'w', encoding='utf-8') as handle:
            handle.write('ok')
    except OSError as exc:
        log_exception('写入更新标记', exc)


def main():
    if not acquire_single_instance():
        messagebox.showinfo('ohMyAss Noto', '软件已经在运行，请使用已打开的窗口。')
        return 0
    # 仅冻结运行时清理临时解压目录，源码运行不碰其他程序的目录
    if getattr(sys, 'frozen', False):
        threading.Thread(target=cleanup_stale_temp_folders, daemon=True).start()
    root = create_root()
    try:
        root.tk.call('tk', 'scaling', UI_SCALE * 96.0 / 72.0)
    except tk.TclError:
        pass
    App(root)
    root.update_idletasks()
    # App 初始化成功后再写更新标记，启动失败时让批处理回滚
    _mark_update_success()
    root.mainloop()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:
        log_exception('启动失败', exc)
        try:
            messagebox.showerror(
                'ohMyAss Noto 启动失败',
                f'启动失败：{exc}\n\n详细信息已写入：\n{ERROR_LOG_PATH}',
            )
        except Exception:
            pass
        sys.exit(1)
