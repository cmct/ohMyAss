"""本地配置文件、错误日志、路径与文本写入。"""

import json
import os
import shutil
import sys
import tempfile
import time
import traceback

INVALID_FOLDER_CHARS = {
    '\\': '＼', '/': '／', ':': '：', '*': '＊', '?': '？',
    '"': '＂', '<': '＜', '>': '＞', '|': '｜',
}
WINDOWS_RESERVED_NAMES = {
    'CON', 'PRN', 'AUX', 'NUL',
    'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
    'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9',
}
MAX_FOLDER_NAME_LENGTH = 120

LOCAL_APP_DATA = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
APP_DATA_DIR = os.path.join(LOCAL_APP_DATA, 'ohMyAssNoto')
CONFIG_PATH = os.path.join(os.path.expanduser('~'), 'ohMyAssNoto.json')
ERROR_LOG_PATH = os.path.join(APP_DATA_DIR, 'ohMyAssNoto_error.log')
MAX_LOG_BYTES = 1024 * 1024


def log_exception(context: str, exc=None):
    """把被忽略的异常追加到用户数据目录日志，超过 1MB 时轮转一次。"""
    try:
        os.makedirs(os.path.dirname(ERROR_LOG_PATH) or '.', exist_ok=True)
    except OSError:
        pass
    try:
        if os.path.isfile(ERROR_LOG_PATH) and os.path.getsize(ERROR_LOG_PATH) > MAX_LOG_BYTES:
            os.replace(ERROR_LOG_PATH, ERROR_LOG_PATH + '.1')
    except OSError:
        pass
    try:
        with open(ERROR_LOG_PATH, 'a', encoding='utf-8') as handle:
            handle.write(f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] {context}\n')
            if exc is not None:
                handle.write(f'{type(exc).__name__}: {exc}\n')
                traceback.print_exc(file=handle)
            handle.write('\n')
    except OSError:
        pass


def atomic_write_text(path: str, content: str, encoding: str = 'utf-8-sig') -> None:
    """先写同目录临时文件再替换，避免中途失败留下半截文件。"""
    folder = os.path.dirname(os.path.abspath(path)) or '.'
    os.makedirs(folder, exist_ok=True)
    handle, temp_path = tempfile.mkstemp(
        prefix='.oma_write_', suffix='.tmp', dir=folder)
    try:
        with os.fdopen(handle, 'w', encoding=encoding, newline='') as stream:
            stream.write(content)
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.remove(temp_path)
        except OSError:
            pass
        raise


def normalize_display_path(value: str) -> str:
    """地址栏统一显示 Windows 反斜杠。"""
    text = str(value or '').strip()
    return text.replace('/', '\\') if text else text


def sanitize_folder_name(name: str) -> str:
    name = str(name or '').strip()
    for old, new in INVALID_FOLDER_CHARS.items():
        name = name.replace(old, new)
    # Windows 不允许文件夹名以空格或点结尾
    name = name.rstrip(' .')
    # Windows 保留设备名不能直接作为文件夹名
    if name.upper() in WINDOWS_RESERVED_NAMES:
        name += '_'
    # 过长的文件夹名会超出路径长度限制
    if len(name) > MAX_FOLDER_NAME_LENGTH:
        name = name[:MAX_FOLDER_NAME_LENGTH].rstrip(' .')
    return name


def get_output_dir(source_file: str, title: str, output_root: str = ''):
    folder_name = sanitize_folder_name(title)
    if not folder_name:
        return None
    base = str(output_root or '').strip()
    if not base:
        base = os.path.dirname(os.path.abspath(source_file))
    return os.path.join(base, folder_name)


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError) as exc:
        # 配置损坏时按默认配置启动，同时留下记录
        log_exception('读取配置', exc)
        return {}


def save_config(data: dict):
    """先写临时文件再替换，避免中途失败把配置写坏。"""
    temp_path = CONFIG_PATH + '.tmp'
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, CONFIG_PATH)
    except (OSError, TypeError, ValueError) as exc:
        log_exception('保存配置', exc)
        try:
            os.remove(temp_path)
        except OSError:
            pass


def last_directory(key: str, fallback: str = '') -> str:
    """返回某个文件选择器上次使用的目录。"""
    config = load_config()
    dirs = config.get('last_dirs')
    if isinstance(dirs, dict):
        value = str(dirs.get(key) or '').strip()
        if value and os.path.isdir(value):
            return value
    fallback = str(fallback or '').strip()
    if fallback:
        if os.path.isdir(fallback):
            return fallback
        parent = os.path.dirname(os.path.abspath(fallback))
        if parent and os.path.isdir(parent):
            return parent
    return ''


def remember_directory(key: str, path: str):
    """记住某个文件选择器最后使用的目录。"""
    text = str(path or '').strip()
    if not text:
        return
    folder = text if os.path.isdir(text) else os.path.dirname(os.path.abspath(text))
    if not folder or not os.path.isdir(folder):
        return
    folder = os.path.abspath(folder)
    config = load_config()
    dirs = config.get('last_dirs')
    if not isinstance(dirs, dict):
        dirs = {}
    if dirs.get(key) == folder:
        return
    dirs[key] = folder
    config['last_dirs'] = dirs
    save_config(config)


def cleanup_stale_temp_folders(max_age_seconds: int = 7 * 86400, base: str = None):
    """清理一周前的 PyInstaller 解压目录与更新临时目录。

    先改名再删除：仍在运行的 _MEI 目录通常无法改名，可避免误删。
    """
    root = base or tempfile.gettempdir()
    current = ''
    meipass = getattr(sys, '_MEIPASS', '')
    if meipass:
        current = os.path.abspath(meipass).lower()
    now = time.time()
    try:
        entries = os.listdir(root)
    except OSError:
        return
    for name in entries:
        if not (name.startswith('_MEI') or name.startswith('oma_update_')):
            continue
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        if current and os.path.abspath(path).lower() == current:
            continue
        try:
            if now - os.path.getmtime(path) < max_age_seconds:
                continue
        except OSError:
            continue
        stale = path + '.stale'
        try:
            if os.path.exists(stale):
                shutil.rmtree(stale, ignore_errors=True)
            os.rename(path, stale)
        except OSError:
            # 仍被占用，跳过
            continue
        shutil.rmtree(stale, ignore_errors=True)


def write_text_with_fallback(path: str, content: str) -> str:
    """原子写入文本；若文件被占用，则改用 name_1.ext 等文件名。"""
    base, ext = os.path.splitext(path)
    candidate = path
    counter = 0
    while True:
        try:
            atomic_write_text(candidate, content)
            return candidate
        except OSError:
            counter += 1
            if counter > 99:
                raise
            candidate = f'{base}_{counter}{ext}'
