"""软件自身在线更新。"""


import os
import sys
import re
import json
import shutil
import subprocess
import tempfile
import urllib.request
import urllib.parse

from download_utils import (
    TOOLS_USER_AGENT, download_file, html_latest_tag, tools_request,
    verify_file_digest,
)


APP_UPDATE_REPO = 'cmct/ohMyAss'
APP_UPDATE_ASSET = 'ohMyAss Noto.exe'


def _asset_key(name: str) -> str:
    """把附件名归一化成只含字母数字的键，忽略空格、点号和版本差异。"""
    stem = os.path.splitext(str(name or ''))[0].lower()
    return re.sub(r'[^a-z0-9]', '', stem)


def _app_assets_from_page(repo: str, tag: str):
    page = tools_request(
        f'https://github.com/{repo}/releases/expanded_assets/{urllib.parse.quote(tag)}',
        timeout=60,
    ).read().decode('utf-8', 'replace')
    assets = []
    seen = set()
    for href in re.findall(r'href="(/[^"]+/releases/download/[^"]+)"', page):
        url = 'https://github.com' + href
        name = urllib.parse.unquote(url.rsplit('/', 1)[-1])
        lower = name.lower()
        if lower.endswith('.exe') and 'ohmyass' in lower and url not in seen:
            seen.add(url)
            assets.append((name, url))
    return assets


def fetch_app_update():
    """返回 (最新版本号, 下载地址, 资源文件名, SHA-256 校验值)。"""
    repo = APP_UPDATE_REPO
    tag = ''
    assets = []
    try:
        request = urllib.request.Request(
            f'https://api.github.com/repos/{repo}/releases/latest',
            headers={'User-Agent': TOOLS_USER_AGENT},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode('utf-8'))
        tag = str(data.get('tag_name') or '')
        for asset in data.get('assets') or []:
            name = str(asset.get('name') or '')
            url = str(asset.get('browser_download_url') or '')
            digest = str(asset.get('digest') or '')
            lower = name.lower()
            if lower.endswith('.exe') and 'ohmyass' in lower and url:
                assets.append((name, url, digest))
    except (OSError, ValueError, KeyError, TypeError):
        # 接口不可用时退回发布页解析属于正常降级，不写错误日志
        tag = ''
    if not tag:
        tag = html_latest_tag(repo)
    if not assets:
        assets = [(name, url, '') for name, url in _app_assets_from_page(repo, tag)]
    if not assets:
        name = APP_UPDATE_ASSET
        url = (f'https://github.com/{repo}/releases/latest/download/'
               f'{urllib.parse.quote(name)}')
        assets = [(name, url, '')]
    wanted = _asset_key(APP_UPDATE_ASSET)
    for name, url, digest in assets:
        if _asset_key(name) == wanted:
            return tag, url, name, digest
    # GitHub 会把附件名里的空格规范成点号，按归一化名称再找一次
    for name, url, digest in assets:
        if 'ohmyass' in _asset_key(name):
            return tag, url, name, digest
    name, url, digest = assets[0]
    return tag, url, name, digest


def discard_app_update(path: str):
    """删除下载的更新文件及其临时目录（仅限 oma_update_ 前缀）。"""
    folder = os.path.dirname(os.path.abspath(path))
    if os.path.basename(folder).startswith('oma_update_'):
        shutil.rmtree(folder, ignore_errors=True)


def fetch_sidecar_digest(url: str) -> str:
    """API 不可用时尝试读取发布附件旁的 .sha256 校验文件。"""
    try:
        with tools_request(url + '.sha256', timeout=30) as response:
            text = response.read().decode('utf-8', 'replace')
    except (OSError, ValueError):
        return ''
    match = re.search(r'\b([0-9a-fA-F]{64})\b', text)
    return 'sha256:' + match.group(1).lower() if match else ''


def download_app_update(url: str, log=None, timeout: int = 900, digest: str = ''):
    folder = tempfile.mkdtemp(prefix='oma_update_')
    destination = os.path.join(folder, APP_UPDATE_ASSET)
    try:
        download_file(url, destination, log=log, timeout=timeout)
        expected = digest or fetch_sidecar_digest(url)
        if expected and not verify_file_digest(destination, expected):
            raise RuntimeError('更新文件校验失败，请重新下载')
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return destination


PYINSTALLER_ENV_KEYS = (
    '_PYI_APPLICATION_HOME_DIR',
    '_PYI_ARCHIVE_FILE',
    '_PYI_PARENT_PROCESS_LEVEL',
    '_PYI_SPLASH_IPC',
    '_PYI_ONEFILE_PARENT_PID',
    '_MEIPASS2',
)


def update_environment(base=None) -> dict:
    """复制环境变量并移除 PyInstaller 单文件模式注入的项。

    这些变量指向旧进程的 _MEI 解压目录；若传给新实例，新实例会直接复用
    该目录，旧进程退出删除目录后就会报 Failed to load Python DLL。
    """
    env = dict(os.environ if base is None else base)
    for key in list(env):
        if key in PYINSTALLER_ENV_KEYS or key.startswith('_PYI'):
            env.pop(key, None)
    return env


def build_app_update_script() -> str:
    """生成更新批处理内容（纯 ASCII，路径全部通过环境变量传入）。

    替换前备份旧 EXE；新版本启动后写入 MARKER 标记，超时未写入则自动回滚。
    """
    lines = [
        '@echo off',
        'setlocal EnableExtensions',
        'set "_PYI_APPLICATION_HOME_DIR="',
        'set "_PYI_ARCHIVE_FILE="',
        'set "_PYI_PARENT_PROCESS_LEVEL="',
        'set "_PYI_SPLASH_IPC="',
        'set "_MEIPASS2="',
        'echo [%date% %time%] update start>>"%LOG%"',
        'echo pid %PID%>>"%LOG%"',
        'echo target "%TARGET%">>"%LOG%"',
        'echo source "%SOURCE%">>"%LOG%"',
        'if not exist "%SOURCE%" goto missing',
        'copy /Y "%TARGET%" "%TARGET%.old" >nul 2>&1',
        'if errorlevel 1 echo backup failed, continue>>"%LOG%"',
        'set /a tries=0',
        ':wait',
        'tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul',
        'if errorlevel 1 goto copy',
        'ping -n 2 127.0.0.1 >nul',
        'set /a tries+=1',
        'if %tries% GEQ 60 goto copy',
        'goto wait',
        ':copy',
        'attrib -R "%TARGET%" >nul 2>&1',
        'set /a copies=0',
        ':copytry',
        'copy /Y "%SOURCE%" "%TARGET%" >nul 2>&1',
        'if not errorlevel 1 goto copied',
        'ping -n 2 127.0.0.1 >nul',
        'set /a copies+=1',
        'if %copies% GEQ 90 goto failed',
        'goto copytry',
        ':copied',
        'echo [%date% %time%] copied>>"%LOG%"',
        'del /F /Q "%MARKER%" >nul 2>&1',
        'cd /d "%TARGETDIR%"',
        'start "" "%TARGET%"',
        'set /a waits=0',
        ':waitmarker',
        'if exist "%MARKER%" goto started',
        'ping -n 2 127.0.0.1 >nul',
        'set /a waits+=1',
        'if %waits% GEQ 15 goto rollback',
        'goto waitmarker',
        ':started',
        'echo [%date% %time%] started>>"%LOG%"',
        'if exist "%TARGET%.old" del /F /Q "%TARGET%.old" >nul 2>&1',
        'rd /s /q "%SOURCEDIR%" >nul 2>&1',
        'goto done',
        ':rollback',
        'echo [%date% %time%] new version did not start, rollback>>"%LOG%"',
        'if not exist "%TARGET%.old" goto failed',
        'copy /Y "%TARGET%.old" "%TARGET%" >nul 2>&1',
        'del /F /Q "%TARGET%.old" >nul 2>&1',
        'start "" "%TARGET%"',
        'goto done',
        ':missing',
        'echo [%date% %time%] source missing>>"%LOG%"',
        'goto done',
        ':failed',
        'echo [%date% %time%] copy failed>>"%LOG%"',
        ':done',
        'del /f /q "%~f0" >nul 2>&1',
    ]
    return '\r\n'.join(lines) + '\r\n'


def folder_writable(folder: str) -> bool:
    """真正尝试写一个临时文件，比 os.access 可靠。"""
    probe = os.path.join(folder, f'.oma_write_{os.getpid()}.tmp')
    try:
        with open(probe, 'wb') as handle:
            handle.write(b'1')
        os.remove(probe)
        return True
    except OSError:
        try:
            os.remove(probe)
        except OSError:
            pass
        return False


def apply_app_update(new_exe: str):
    """用批处理等待当前进程退出后替换 EXE 并重启。"""
    if not getattr(sys, 'frozen', False):
        return False, '源码运行模式无法自动替换'
    current = os.path.abspath(sys.executable)
    folder = os.path.dirname(current)
    if not folder_writable(folder):
        return False, '程序目录没有写入权限，请手动下载替换'
    if not os.path.isfile(new_exe):
        return False, '更新文件不存在，请重新下载'
    try:
        if os.path.getsize(new_exe) < 1024 * 1024:
            return False, '更新文件不完整，请重新下载'
    except OSError:
        return False, '无法读取更新文件'
    log_path = os.path.join(folder, 'ohMyAssNoto_update.log')
    script = os.path.join(tempfile.gettempdir(), f'oma_update_{os.getpid()}.bat')
    content = build_app_update_script()
    env = update_environment()
    marker = os.path.join(
        tempfile.gettempdir(), f'oma_update_ok_{os.getpid()}.flag')
    env.update({
        'PID': str(os.getpid()),
        'TARGET': current,
        'TARGETDIR': folder,
        'SOURCE': new_exe,
        'SOURCEDIR': os.path.dirname(new_exe),
        'MARKER': marker,
        'LOG': log_path,
    })
    try:
        with open(script, 'wb') as handle:
            handle.write(content.encode('ascii'))
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        # 路径通过环境变量传递，脚本内容保持纯 ASCII；
        # 双引号包裹可兼容带空格的临时目录。
        subprocess.Popen(
            f'cmd /c ""{script}""', env=env, creationflags=flags, close_fds=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return False, str(exc)
    return True, ''
