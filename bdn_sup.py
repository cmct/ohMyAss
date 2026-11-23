"""BDN XML / SUP / PES 生成、外部工具与 VSFilterMod 渲染。"""


import os
import re
import json
import math
import mmap
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
import urllib.parse

from app_info import resource_path
from config_store import log_exception
from download_utils import (
    download_file, html_latest_tag, tools_request, verify_file_digest,
)
from fonts import font_name_installed, installed_font_records
from subtitle_core import parse_ass_time
from subtitle_outputs import read_ass_text


# ===================== BDN XML / SUP 生成（外部工具，在线更新） =====================

BDN_TOOL_ROOT = os.path.join(
    os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'),
    'ohMyAssNoto', 'tools',
)
SEVEN_ZIP_URL = 'https://www.7-zip.org/a/7zr.exe'
GITHUB_RELEASES_API = 'https://api.github.com/repos/{repo}/releases?per_page=20'
BDN_TOOL_SPECS = {
    'ass2bdnxml': {
        'label': 'ass2bdnxml',
        'repo': 'cubicibo/ass2bdnxml',
        'exe': 'ass2bdnxml.exe',
        'asset_keywords': ('winx86_64', 'win_x64', 'win64', 'win', 'ass2bdnxml'),
        'required_keywords': (),
        'probe_args': [],
        'probe_expect': ('usage', 'ass2bdnxml'),
    },
    'super': {
        'label': 'SUPer',
        'repo': 'cubicibo/SUPer',
        'exe': 'SUPer_CLI.exe',
        'asset_keywords': ('win_x64', 'win64', 'win'),
        'required_keywords': ('cli',),
        'probe_args': ['-v'],
        'probe_expect': ('cubicibo', 'v0.', 'super'),
    },
}
BDN_FORMATS = ('1080p', '1080i', '720p', '576i', '480i')
BDN_FPS = ('23.976', '24', '25', '29.97', '50', '59.94')
BDN_FORMAT_FPS = {
    '1080p': ('23.976', '24', '25', '29.97'),
    '1080i': ('25', '29.97'),
    '720p': ('23.976', '24', '25', '29.97', '50', '59.94'),
    '576i': ('25',),
    '480i': ('29.97',),
}


def validate_bdn_format(fmt: str, fps: str):
    allowed = BDN_FORMAT_FPS.get(str(fmt))
    if not allowed:
        return False, f'未知视频格式：{fmt}'
    if str(fps) not in allowed:
        return False, (
            f'{fmt} 不支持 {fps} fps。\n可用帧率：' + ' / '.join(allowed))
    return True, ''
BDN_QUANTIZERS = (
    ('libimagequant（质量最好）', '3'),
    ('Qtzr（质量高，体积大）', '0'),
    ('HexTree（均衡）', '2'),
    ('Pillow（最快）', '1'),
)


def _no_window_flag():
    return getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0


class RenderCancelled(Exception):
    """用户中止长时间渲染时抛出。"""


def _kill_process_tree(process, drain: bool = True):
    """终止进程及其子进程，避免管道被孙进程占用导致取消卡住。"""
    if os.name == 'nt':
        try:
            subprocess.run(
                ['taskkill', '/F', '/T', '/PID', str(process.pid)],
                capture_output=True, creationflags=_no_window_flag())
        except OSError:
            pass
    try:
        process.kill()
    except OSError:
        pass
    if drain:
        try:
            process.communicate(timeout=2)
        except (subprocess.TimeoutExpired, ValueError):
            pass


def run_process(args, cwd=None, timeout: int = 3600, cancel_event=None):
    """运行外部进程；cancel_event 置位时终止进程并抛 RenderCancelled。"""
    process = subprocess.Popen(
        args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', errors='replace',
        creationflags=_no_window_flag(),
    )
    started = time.time()
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.25)
            return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            if cancel_event is not None and cancel_event.is_set():
                _kill_process_tree(process)
                raise RenderCancelled('已取消')
            if timeout and time.time() - started > timeout:
                _kill_process_tree(process)
                raise subprocess.TimeoutExpired(args, timeout)


_BDN_ARTIFACT_RE = re.compile(
    r'(?:bdn(?:_\d+)*|\d{8}(?:_\d+)?)\.png$', re.IGNORECASE)


def is_bdn_artifact(name: str) -> bool:
    """判断是否为渲染链生成的 BDN/PNG 中间文件。"""
    return name == 'bdn.xml' or _BDN_ARTIFACT_RE.fullmatch(name) is not None


def cleanup_bdn_outputs(out_dir: str):
    """删除 BDN 渲染产生的中途文件（取消或失败后调用）。"""
    try:
        names = os.listdir(out_dir)
    except OSError:
        return
    for name in names:
        if is_bdn_artifact(name):
            try:
                os.remove(os.path.join(out_dir, name))
            except OSError:
                pass


def bdn_tool_dir(name: str) -> str:
    return os.path.join(BDN_TOOL_ROOT, name)


def load_bdn_tool_state(name: str) -> dict:
    try:
        with open(os.path.join(bdn_tool_dir(name), 'state.json'), 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError) as exc:
        log_exception(f'读取工具状态：{name}', exc)
        return {}


def save_bdn_tool_state(name: str, data: dict):
    folder = bdn_tool_dir(name)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, 'state.json')
    temp_path = path + '.tmp'
    try:
        with open(temp_path, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, path)
    except OSError as exc:
        log_exception(f'保存工具状态：{name}', exc)
        try:
            os.remove(temp_path)
        except OSError:
            pass


def bdn_tool_exe(name: str) -> str:
    state = load_bdn_tool_state(name)
    relative = state.get('exe') or BDN_TOOL_SPECS[name]['exe']
    return os.path.join(bdn_tool_dir(name), 'app', relative)


def bdn_tool_version_text() -> str:
    parts = []
    names = ['super'] if vsfilter_uses_x64() else ['ass2bdnxml', 'super']
    for name in names:
        state = load_bdn_tool_state(name)
        parts.append(f'{BDN_TOOL_SPECS[name]["label"]} {state.get("tag") or "未安装"}')
    parts.append(vsfilter_backend_name())
    return ' · '.join(parts)


def bdn_tool_installed(name: str) -> bool:
    """工具状态文件存在且可执行文件确实存在。"""
    state = load_bdn_tool_state(name)
    return bool(state.get('exe')) and os.path.isfile(bdn_tool_exe(name))


def bdn_tools_status_text() -> str:
    """返回已安装 / 部分安装 / 未安装，用于设置页状态标签。"""
    # 64 位后端自带 avs2bdnxml，只需要 SUPer；ass2bdnxml 仅在回退 32 位时需要。
    names = ['super'] if vsfilter_uses_x64() else list(BDN_TOOL_SPECS)
    count = sum(1 for name in names if bdn_tool_installed(name))
    if count == len(names):
        return '已安装'
    if count == 0:
        return '未安装'
    return f'部分安装（{count}/{len(names)}）'


def ensure_seven_zip(log=None) -> str:
    path = os.path.join(BDN_TOOL_ROOT, '7zr.exe')
    if os.path.isfile(path) and os.path.getsize(path) > 100000:
        return path
    os.makedirs(BDN_TOOL_ROOT, exist_ok=True)
    if log:
        log('首次使用，正在下载 7zr 解压组件…')
    download_file(SEVEN_ZIP_URL, path, log=log)
    if not os.path.isfile(path):
        raise RuntimeError('7zr 下载失败')
    return path


def fetch_tool_releases(repo: str):
    url = GITHUB_RELEASES_API.format(repo=repo)
    with tools_request(url) as response:
        data = json.loads(response.read().decode('utf-8'))
    if isinstance(data, dict):
        raise RuntimeError(data.get('message') or f'GitHub 接口返回异常（{repo}）')
    if not isinstance(data, list):
        raise RuntimeError(f'GitHub 接口返回异常（{repo}）')
    return data


def _release_cache_path() -> str:
    return os.path.join(BDN_TOOL_ROOT, 'releases_cache.json')


def _load_release_cache(repo: str):
    try:
        with open(_release_cache_path(), 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return []
        items = data.get(repo)
        if not isinstance(items, list):
            return []
        candidates = []
        for item in items:
            if not isinstance(item, (list, tuple)) or len(item) < 3:
                continue
            tag, name, url = item[0], item[1], item[2]
            digest = item[3] if len(item) > 3 else ''
            if tag and name and url:
                candidates.append((tag, {
                    'name': name,
                    'browser_download_url': url,
                    'digest': digest,
                    'size': 0,
                }))
        return candidates
    except FileNotFoundError:
        return []
    except (OSError, ValueError, TypeError) as exc:
        log_exception('读取版本缓存', exc)
        return []


def _save_release_cache(repo: str, candidates: list):
    try:
        path = _release_cache_path()
        data = {}
        if os.path.isfile(path):
            with open(path, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
        data[repo] = [
            [tag, asset.get('name', ''), asset.get('browser_download_url', ''),
             asset.get('digest', '')]
            for tag, asset in candidates
        ]
        os.makedirs(BDN_TOOL_ROOT, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
    except (OSError, ValueError, TypeError) as exc:
        log_exception('保存版本缓存', exc)


def _match_asset(name: str, spec: dict) -> bool:
    lower = name.lower()
    if not lower.endswith('.7z'):
        return False
    if not any(keyword in lower for keyword in spec['asset_keywords']):
        return False
    if spec['required_keywords'] and not all(
            keyword in lower for keyword in spec['required_keywords']):
        return False
    return True


def _html_release_tags(repo: str, limit: int = 8):
    """解析 GitHub Releases 页面上的版本号，不消耗 API 配额。"""
    page = tools_request(f'https://github.com/{repo}/releases', timeout=60).read()
    tags = []
    for tag in re.findall(r'/releases/tag/([^"\'#?<>\s]+)', page.decode('utf-8', 'replace')):
        tag = urllib.parse.unquote(tag)
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:limit]


def _html_tag_candidates(repo: str, tag: str, spec: dict):
    """解析某个版本的资产列表。"""
    page = tools_request(
        f'https://github.com/{repo}/releases/expanded_assets/{urllib.parse.quote(tag)}',
        timeout=60,
    ).read().decode('utf-8', 'replace')
    candidates = []
    seen = set()
    for href in re.findall(r'href="(/[^"]+/releases/download/[^"]+)"', page):
        url = 'https://github.com' + href
        name = urllib.parse.unquote(url.rsplit('/', 1)[-1])
        if url in seen or not _match_asset(name, spec):
            continue
        seen.add(url)
        candidates.append((tag, {
            'name': name,
            'browser_download_url': url,
            'digest': '',
            'size': 0,
        }))
    return candidates


def _fallback_candidates(repo: str, spec: dict, log=None):
    """发布页解析失败时，退回 GitHub API，再退回本地缓存。"""
    errors = []
    try:
        releases = fetch_tool_releases(repo)
        candidates = []
        seen = set()
        for tag, asset in _tool_asset_candidates(releases, spec):
            url = asset.get('browser_download_url', '')
            if url in seen:
                continue
            seen.add(url)
            candidates.append((tag, asset))
        if candidates:
            _save_release_cache(repo, candidates)
            return candidates
        errors.append('GitHub 接口没有可用的安装包')
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        errors.append(f'GitHub 接口失败：{exc}')
        log_exception(f'获取工具发布：{repo}', exc)

    cached = _load_release_cache(repo)
    if cached:
        if log:
            log('在线获取失败，改用上次缓存的版本信息')
        return cached
    raise RuntimeError('；'.join(errors))


def _tool_asset_candidates(releases: list, spec: dict):
    seen = set()
    for release in releases:
        tag = release.get('tag_name') or ''
        for asset in release.get('assets') or []:
            name = asset.get('name') or ''
            if not _match_asset(name, spec):
                continue
            url = asset.get('browser_download_url')
            if not url or url in seen:
                continue
            seen.add(url)
            yield tag, asset


def _extract_archive(seven_zip: str, archive: str, dest: str):
    os.makedirs(dest, exist_ok=True)
    result = subprocess.run(
        [seven_zip, 'x', archive, f'-o{dest}', '-y'],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=900, creationflags=_no_window_flag(),
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or '解压失败').strip().splitlines()
        raise RuntimeError(message[-1] if message else '解压失败')


def _find_file(root: str, filename: str) -> str:
    for base, _dirs, files in os.walk(root):
        if filename in files:
            return os.path.join(base, filename)
    return ''


def _probe_tool(exe_path: str, args, expect) -> bool:
    try:
        result = subprocess.run(
            [exe_path] + list(args),
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=120, cwd=os.path.dirname(exe_path),
            creationflags=_no_window_flag(),
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    text = ((result.stdout or '') + (result.stderr or '')).lower()
    return any(word in text for word in expect)


def install_bdn_tool(name: str, log=None, force: bool = False):
    """从 GitHub Releases 下载并安装工具；失败返回 (False, 原因)。"""
    spec = BDN_TOOL_SPECS[name]
    state = load_bdn_tool_state(name)
    installed = bool(state.get('exe')) and os.path.isfile(bdn_tool_exe(name))
    if installed and not force:
        return True, state.get('tag', '')

    tags = []
    html_error = ''
    try:
        latest_tag = html_latest_tag(spec['repo'])
    except (OSError, ValueError, RuntimeError) as exc:
        latest_tag = ''
        html_error = str(exc)
        log_exception(f'解析发布页：{spec["repo"]}', exc)

    fallback = []
    if not latest_tag:
        fallback = _fallback_candidates(spec['repo'], spec, log)
        latest_tag = fallback[0][0] if fallback else ''

    # 已安装且上次检查过同一个最新版本时，不再重复下载
    if installed and state.get('checked') == latest_tag and latest_tag:
        if log:
            if state.get('tag') == latest_tag:
                log(f'{spec["label"]} {latest_tag} 已是最新版本')
            else:
                log(f'{spec["label"]} 当前 {state.get("tag", "")}，已检查过最新版本 {latest_tag}')
        return True, state.get('tag', '')

    def candidate_stream():
        if latest_tag and not fallback:
            for candidate in _html_tag_candidates(spec['repo'], latest_tag, spec):
                yield candidate
            try:
                other_tags = _html_release_tags(spec['repo'])
            except (OSError, ValueError, RuntimeError) as exc:
                log_exception(f'读取发布标签：{spec["repo"]}', exc)
                return
            for tag in other_tags:
                if tag == latest_tag:
                    continue
                try:
                    for candidate in _html_tag_candidates(spec['repo'], tag, spec):
                        yield candidate
                except (OSError, ValueError, RuntimeError) as exc:
                    log_exception(f'读取发布资源：{spec["repo"]} {tag}', exc)
                    continue
        else:
            for pair in fallback:
                yield pair

    seven = ensure_seven_zip(log)
    errors = []
    if html_error and not tags:
        errors.append(f'发布页解析失败：{html_error}')
    for tag, asset in candidate_stream():
        work = tempfile.mkdtemp(prefix=f'ohmyass_{name}_')
        try:
            size = int(asset.get('size') or 0) // 1048576
            if log:
                suffix = f'（{size} MB）' if size else ''
                log(f'下载 {spec["label"]} {tag}{suffix}…')
            archive = os.path.join(work, 'tool.7z')
            download_file(asset['browser_download_url'], archive)
            if not verify_file_digest(archive, asset.get('digest', '')):
                errors.append(f'{tag}：文件校验失败')
                continue
            extract_dir = os.path.join(work, 'extract')
            _extract_archive(seven, archive, extract_dir)
            exe_path = _find_file(extract_dir, spec['exe'])
            if not exe_path:
                errors.append(f'{tag}：压缩包内没有 {spec["exe"]}')
                continue
            if not _probe_tool(exe_path, spec['probe_args'], spec['probe_expect']):
                errors.append(f'{tag}：程序无法启动（上游包可能缺少依赖）')
                continue
            target = os.path.join(bdn_tool_dir(name), 'app')
            shutil.rmtree(target, ignore_errors=True)
            os.makedirs(target, exist_ok=True)
            payload = os.path.dirname(exe_path)
            for item in os.listdir(payload):
                shutil.move(os.path.join(payload, item), os.path.join(target, item))
            save_bdn_tool_state(name, {
                'tag': tag,
                'checked': latest_tag or tag,
                'asset': asset.get('name', ''),
                'exe': os.path.relpath(exe_path, payload).replace('\\', '/'),
                'url': asset.get('browser_download_url', ''),
                'installed': time.strftime('%Y-%m-%d %H:%M:%S'),
            })
            if log:
                log(f'{spec["label"]} {tag} 安装完成')
            return True, tag
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            errors.append(f'{tag}：{exc}')
            log_exception(f'安装工具：{name} {tag}', exc)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    detail = '；'.join(errors[-4:]) if errors else '没有找到可用的发布包'
    return False, detail


def install_all_bdn_tools(log=None, force: bool = False, names=None):
    for name in (names or ('ass2bdnxml', 'super')):
        ok, info = install_bdn_tool(name, log, force=force)
        if not ok:
            return False, f'{BDN_TOOL_SPECS[name]["label"]} 安装失败：{info}'
    return True, ''


def ensure_bdn_tools(log=None, allow_x64: bool = True):
    # 64 位后端自带渲染器，不再依赖 32 位 ass2bdnxml；只有回退到旧链时才需要。
    required = ['super']
    if not (allow_x64 and vsfilter_uses_x64()):
        required.insert(0, 'ass2bdnxml')
    missing = [
        name for name in required
        if not os.path.isfile(bdn_tool_exe(name))
    ]
    if not missing:
        return True, ''
    if log:
        log('首次使用需要下载 ' + ' 与 '.join(
            BDN_TOOL_SPECS[name]['label'] for name in required) + '…')
    return install_all_bdn_tools(log, names=tuple(required))


def _normalize_bdn_xml(path: str):
    """ass2bdnxml 在中文系统上会用 GBK 写 XML，这里统一转成 UTF-8。"""
    try:
        with open(path, 'rb') as handle:
            data = handle.read()
    except OSError:
        return
    try:
        data.decode('utf-8')
        return
    except UnicodeDecodeError:
        pass
    for encoding in ('gb18030', 'big5', 'cp1252'):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        try:
            with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
                handle.write(text)
        except OSError:
            pass
        return


def build_super_args(params: dict, sup_path: str, make_pes: bool = False):
    """按高级参数拼出 SUPer 命令行。"""
    params = {**BDN_PARAM_DEFAULTS, **(params or {})}
    args = [
        bdn_tool_exe('super'), '-i', 'bdn.xml',
        '-q', str(params.get('quantizer', '3')),
        '-c', str(params.get('compression', '80')),
        '-a', str(params.get('acqrate', '100')),
        '-b', str(params.get('color', 'bt709')).replace('bt', ''),
        '-e', str(params.get('extra_acq', '2')),
        '--ssim-tol', str(params.get('ssim_tol', '0')),
        '-y', sup_path,
    ]
    threads = str(params.get('threads', 'auto'))
    if threads and threads.lower() != 'auto':
        args += ['-t', threads]
    if params.get('allow_normal'):
        args.append('-n')
    if params.get('prefer_normal'):
        args.append('-k')
    if params.get('buffer'):
        args.append('-d')
    if params.get('full_palette'):
        args.append('-p')
    try:
        redraw = float(params.get('redraw_period') or 0)
    except ValueError:
        redraw = 0.0
    if redraw > 0:
        args += ['--redraw-period', f'{redraw:g}']
    max_kbps = str(params.get('max_kbps') or '').strip()
    if max_kbps and max_kbps != '0':
        args += ['-m', max_kbps]
    log_level = str(params.get('log_level') or 'off')
    if log_level != 'off':
        args += ['-l', log_level]
    if make_pes:
        args.append('-w')
    return args


VS_BUNDLE_FILES = ('avs2bdnxml.exe', 'VSFilterMod.dll', 'AviSynth.dll', 'DevIL.dll')
# 优先使用 64 位 avs2bdnxml + AviSynth+ + VSFilterMod（GDI）；
# 缺失时回退到旧的 32 位 avs2bdnxml + AviSynth + VSFilterMod 组合。
VS_RENDER_BUNDLES = (
    {
        'arch': 'x64',
        'label': 'avs2bdnxml',
        'mode': 'avs2bdnxml',
        'source_dir': 'bin64',
        'target_dir': 'vsfilter64',
        'files': ('avs2bdnxml.exe', 'AviSynth.dll', 'VSFilterMod.dll'),
        'required': ('avs2bdnxml.exe', 'AviSynth.dll', 'VSFilterMod.dll'),
    },
    {
        'arch': 'x86',
        'label': 'avs2bdnxml',
        'mode': 'avs2bdnxml',
        'source_dir': 'bin',
        'target_dir': 'vsfilter',
        'files': VS_BUNDLE_FILES,
        'required': ('avs2bdnxml.exe', 'VSFilterMod.dll', 'AviSynth.dll'),
    },
)
VS_RENDER_FPS = ('23.976', '24', '25', '29.97', '50', '59.94')
VS_VIDEO_SIZE = {
    '1080p': (1920, 1080), '1080i': (1920, 1080),
    '720p': (1280, 720), '576i': (720, 576), '480i': (720, 480),
}
VS_FPS_EXPR = {
    '23.976': '24000.0/1001.0',
    '29.97': '30000.0/1001.0',
    '59.94': '60000.0/1001.0',
}


def _vsfilter_source(name: str, source_dir: str) -> str:
    """在打包资源根目录或源码目录的 bin/bin64 子目录里查找渲染组件。"""
    for candidate in (
            resource_path(os.path.join(source_dir, name)),
            resource_path(name)):
        if os.path.isfile(candidate):
            return candidate
    return ''


def _prepare_vsfilter_bundle(spec: dict) -> str:
    """把随包附带的渲染组件释放到独立工具目录，返回目录（不可用则空串）。"""
    # 必须以随包/源码目录中的组件为准，不能只靠上次释放到 tools 的缓存；
    # 否则把 bin64 改名后仍会继续走 x64，无法测试回退。
    if not any(_vsfilter_source(name, spec['source_dir']) for name in spec['required']):
        return ''
    target = os.path.join(BDN_TOOL_ROOT, spec['target_dir'])
    for name in spec['files']:
        src = _vsfilter_source(name, spec['source_dir'])
        dst = os.path.join(target, name)
        try:
            if not src:
                continue
            if os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(src):
                continue
            os.makedirs(target, exist_ok=True)
            shutil.copy2(src, dst)
        except OSError:
            pass
    if spec['arch'] == 'x64':
        # 清理上一版 vsfmrender 分支残留，避免误用。
        try:
            os.remove(os.path.join(target, 'vsfmrender.exe'))
        except OSError:
            pass
    if all(os.path.isfile(os.path.join(target, name)) for name in spec['required']):
        return target
    return ''


def _vsfilter_runtime(allow_x64: bool = True):
    """返回 (backend_spec, runtime_dir)；优先 x64，失败回退 x86。"""
    for spec in VS_RENDER_BUNDLES:
        if not allow_x64 and spec['arch'] == 'x64':
            continue
        target = _prepare_vsfilter_bundle(spec)
        if target:
            return spec, target
    return None, ''


def vsfilter_render_dir(allow_x64: bool = True):
    """返回当前可用的 VSFilterMod 渲染目录（不可用则空串）。"""
    _spec, target = _vsfilter_runtime(allow_x64)
    return target


def vsfilter_uses_x64(allow_x64: bool = True) -> bool:
    """当前是否会走 64 位渲染后端。"""
    spec, _target = _vsfilter_runtime(allow_x64)
    return bool(spec and spec['arch'] == 'x64')


def vsfilter_backend_name(allow_x64: bool = True) -> str:
    """返回当前渲染后端名称，用于界面状态和日志。"""
    spec, _target = _vsfilter_runtime(allow_x64)
    if not spec:
        return '未找到'
    return f"VSFilterMod {spec['arch']}（{spec['label']}）"


def vsfilter_available(allow_x64: bool = True) -> bool:
    return bool(vsfilter_render_dir(allow_x64))



def ass_font_names(path: str) -> list:
    """提取 ASS 引用的字体名（样式 + 行内 \\fn，去重并保持出现顺序）。"""
    names = []
    section = ''
    fields = None
    text = read_ass_text(path)
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('['):
            section = line.lower()
            fields = None
            continue
        if section not in ('[v4+ styles]', '[v4 styles]'):
            continue
        if line.lower().startswith('format:'):
            fields = [item.strip().lower() for item in line.split(':', 1)[1].split(',')]
            continue
        if not line.lower().startswith('style:') or not fields or 'fontname' not in fields:
            continue
        values = line.split(':', 1)[1].split(',')
        index = fields.index('fontname')
        if index >= len(values):
            continue
        name = values[index].strip().lstrip('@')
        if name and name not in names:
            names.append(name)
    # 行内 \fn 覆盖使用的字体同样需要检查
    inline_pattern = re.compile(r'\\fn([^\\}]*)', re.I)
    for raw in text.splitlines():
        if not raw.lower().lstrip().startswith('dialogue:'):
            continue
        parts = raw.split(',', 9)
        if len(parts) < 10:
            continue
        for match in inline_pattern.finditer(parts[9]):
            name = match.group(1).strip().lstrip('@')
            if name and name not in names:
                names.append(name)
    return names


def ass_vertical_font_names(path: str) -> list:
    """提取 ASS 里显式使用 @ 竖排字体的名称（样式 + 行内 \\fn）。"""
    names = []
    section = ''
    fields = None
    text = read_ass_text(path)
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith('['):
            section = line.lower()
            fields = None
            continue
        if section in ('[v4+ styles]', '[v4 styles]'):
            if line.lower().startswith('format:'):
                fields = [item.strip().lower()
                          for item in line.split(':', 1)[1].split(',')]
                continue
            if (not line.lower().startswith('style:') or not fields
                    or 'fontname' not in fields):
                continue
            values = line.split(':', 1)[1].split(',')
            index = fields.index('fontname')
            if index >= len(values):
                continue
            name = values[index].strip()
            if name.startswith('@'):
                if name not in names:
                    names.append(name)
            continue
        if section != '[events]':
            continue
        if not line.lower().startswith('dialogue:'):
            continue
        parts = line.split(',', 9)
        if len(parts) < 10:
            continue
        for match in re.finditer(r'\\fn\s*(@[^\\}]*)', parts[9], re.I):
            name = match.group(1).strip()
            if name and name not in names:
                names.append(name)
    return names


def missing_ass_fonts(path: str) -> list:
    """返回 ASS 引用但系统未安装的字体名。"""
    names = ass_font_names(path)
    if not names:
        return []
    records = installed_font_records()
    return [name for name in names if not font_name_installed(name, records)]


def _ass_last_time_ms(ass_path: str) -> int:
    """取 ASS 里最后一条对白的结束时间（毫秒）。"""
    last = 0
    text = read_ass_text(ass_path)
    for line in text.splitlines():
        if not line.startswith('Dialogue:'):
            continue
        parts = line.split(',')
        if len(parts) < 3:
            continue
        end = parse_ass_time(parts[2])
        if end and end > last:
            last = end
    return last


def _ass_intervals_ms(ass_path: str):
    """读取 ASS 对白的起止时间（毫秒）。"""
    intervals = []
    for line in read_ass_text(ass_path).splitlines():
        if not line.startswith('Dialogue:'):
            continue
        parts = line.split(',')
        if len(parts) < 3:
            continue
        start = parse_ass_time(parts[1])
        end = parse_ass_time(parts[2])
        if start is not None and end is not None and end > start:
            intervals.append((start, end))
    return intervals


def _merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


_ANIMATED_TAG_RE = re.compile(
    r'\\(?:t|move|k|kf|ko|kt|fad|fade|rnd)\b', re.IGNORECASE)


def _ass_render_ranges(ass_path: str, fps_value: float, frames: int):
    """返回需要交给 VSFilterMod 的帧区间。

    静态事件只保留开始/结束边界；带 \t、\move、\k、\fad、\rnd 等
    时间动画的事件保留完整区间。
    """
    ranges = []
    for line in read_ass_text(ass_path).splitlines():
        if not line.startswith('Dialogue:'):
            continue
        parts = line.split(',', 9)
        if len(parts) < 10:
            continue
        start_ms = parse_ass_time(parts[1])
        end_ms = parse_ass_time(parts[2])
        if start_ms is None or end_ms is None or end_ms <= start_ms:
            continue
        start = max(0, int(math.ceil(start_ms / 1000.0 * fps_value)))
        end = min(frames, max(start + 1,
                              int(math.ceil(end_ms / 1000.0 * fps_value))))
        if _ANIMATED_TAG_RE.search(parts[9]):
            if end > start:
                ranges.append((max(0, start - 1), min(frames, end + 2)))
        else:
            ranges.append((max(0, start - 1), min(frames, start + 2)))
            if end < frames:
                ranges.append((max(0, end - 1), min(frames, end + 2)))
            elif frames > 0:
                ranges.append((max(0, frames - 2), frames))
    if not ranges:
        return []
    return _merge_intervals(ranges)


def _write_active_ranges(work: str, ass_path: str,
                         fps_value: float, frames: int) -> str:
    """把需要渲染的帧区间写成 avs2bdnxml 的 active-ranges 文件。"""
    ranges = _ass_render_ranges(ass_path, fps_value, frames)
    if not ranges:
        return ''
    path = os.path.join(work, 'active.txt')
    try:
        with open(path, 'w', encoding='ascii', newline='\n') as handle:
            for start, end in ranges:
                handle.write(f'{start} {end}\n')
    except OSError:
        return ''
    return 'active.txt'


def _vsfilter_segments(intervals, fps_value, first_frame, last_frame, parts, min_frames):
    """按对白空档把 [first_frame, last_frame) 切成若干段，避免切断事件。"""
    total = last_frame - first_frame
    if parts <= 1 or total < min_frames * 2:
        return [(first_frame, total)]
    merged = _merge_intervals(intervals)
    gaps = [(merged[i][1], merged[i + 1][0]) for i in range(len(merged) - 1)]
    gaps = [(start, end) for start, end in gaps if end - start >= 120]
    if not gaps:
        return [(first_frame, total)]
    span_ms = total / fps_value * 1000.0
    base_ms = first_frame / fps_value * 1000.0
    targets = [base_ms + span_ms * i / parts for i in range(1, parts)]
    cuts = []
    for target in targets:
        best = None
        for gap_start, gap_end in gaps:
            mid = (gap_start + gap_end) / 2.0
            if mid <= 0:
                continue
            distance = abs(mid - target)
            if best is None or distance < best[0]:
                best = (distance, mid)
        if best is None:
            continue
        frame = int(round(best[1] / 1000.0 * fps_value))
        if frame - first_frame < min_frames or last_frame - frame < min_frames:
            continue
        if any(abs(frame - cut) < min_frames for cut in cuts):
            continue
        cuts.append(frame)
    cuts = sorted(set(cuts))
    points = [first_frame] + cuts + [last_frame]
    return [(points[i], points[i + 1] - points[i]) for i in range(len(points) - 1)]


def _merge_bdn_parts(xml_paths, out_path):
    """合并多段 avs2bdnxml 输出（PNG 名按绝对帧号，天然不冲突）。"""
    blocks = []
    head = {}
    for path in xml_paths:
        try:
            with open(path, 'rb') as handle:
                data = handle.read()
        except OSError:
            return False
        if not head:
            for key, pattern in (
                ('name', rb'<Name\b[^>]*?/>'),
                ('language', rb'<Language\b[^>]*?/>'),
                ('format', rb'<Format\b[^>]*?/>'),
            ):
                match = re.search(pattern, data, re.S)
                if match:
                    head[key] = match.group(0)
        blocks.extend(re.findall(rb'<Event\b.*?</Event>', data, re.S))
    if not blocks:
        return False
    first = re.search(rb'InTC="([^"]+)"', blocks[0])
    last = re.search(rb'OutTC="([^"]+)"', blocks[-1])
    first_tc = first.group(1).decode('ascii') if first else '00:00:00:00'
    last_tc = last.group(1).decode('ascii') if last else first_tc
    header = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<BDN Version="0.93" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n'
        b'xsi:noNamespaceSchemaLocation="BD-03-006-0093b BDN File Format.xsd">\n'
        b'<Description>\n'
        + head.get('name', b'<Name Title="Undefined" Content=""/>') + b'\n'
        + head.get('language', b'<Language Code="und"/>') + b'\n'
        + head.get('format', b'<Format VideoFormat="1080p" FrameRate="23.976" DropFrame="false"/>') + b'\n'
        + (
            f'<Events LastEventOutTC="{last_tc}" FirstEventInTC="{first_tc}"\n'
            f'ContentInTC="{first_tc}" ContentOutTC="{last_tc}" '
            f'NumberofEvents="{len(blocks)}" Type="Graphic"/>\n'
        ).encode('ascii')
        + b'</Description>\n<Events>\n'
    )
    payload = header + b'\n'.join(blocks) + b'\n</Events>\n</BDN>\n'
    with open(out_path, 'wb') as handle:
        handle.write(payload)
    return True


def render_bdn_with_vsfilter(ass_path: str, out_dir: str, fmt: str, fps: str,
                             language: str, track_name: str,
                             log=None, progress=None, cancel_event=None,
                             allow_x64: bool = True):
    """用 VSFilterMod 渲染 BDN XML + PNG（优先 x64，支持 \\fsvp 等 Mod 标签）。"""
    def report(value):
        if progress:
            progress(value)

    spec, runtime = _vsfilter_runtime(allow_x64)
    if not runtime:
        return False, (
            '未找到 VSFilterMod 渲染组件（bin64\\avs2bdnxml.exe / '
            'AviSynth.dll / VSFilterMod.dll，或 32 位 bin 回退）')
    if cancel_event is not None and cancel_event.is_set():
        raise RenderCancelled('已取消')
    width, height = VS_VIDEO_SIZE.get(fmt, (1920, 1080))
    try:
        fps_value = float(fps)
    except ValueError:
        return False, '帧率无效：' + str(fps)

    end_ms = _ass_last_time_ms(ass_path)
    if end_ms <= 0:
        return False, '字幕里没有可渲染的对白'
    frames = int(math.ceil(end_ms / 1000.0 * fps_value)) + 2
    frames = max(1, min(frames, 500000))

    intervals = _ass_intervals_ms(ass_path)
    first_ms = min((start for start, _ in intervals), default=0)
    first_frame = max(0, int((first_ms - 1000) / 1000.0 * fps_value))

    cpu = os.cpu_count() or 4
    duration_s = (frames - first_frame) / fps_value
    parts = 1
    if duration_s >= 120 and cpu >= 4:
        parts = min(4, max(2, cpu // 2), max(1, int(duration_s // 45)))
    segments = _vsfilter_segments(
        intervals, fps_value, first_frame, frames, parts,
        min_frames=int(30 * fps_value),
    )

    work = os.path.join(runtime, 'work')
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    expected_images = max(1, len(intervals))
    monitor_stop = threading.Event()

    def monitor():
        while not monitor_stop.wait(0.7):
            try:
                count = sum(
                    1 for name in os.listdir(work)
                    if name.lower().endswith('.png'))
            except OSError:
                continue
            report(10 + 85 * min(1.0, count / expected_images))

    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()
    try:
        try:
            text = read_ass_text(ass_path)
            if not text:
                raise OSError('文件为空或无法读取')
            with open(os.path.join(work, 'sub.ass'), 'w', encoding='utf-16', newline='') as handle:
                handle.write(text)
        except OSError as exc:
            return False, '读取 ASS 失败：' + str(exc)

        title = track_name.strip() or 'Undefined'
        fps_expr = VS_FPS_EXPR.get(str(fps), str(fps))
        script = (
            'LoadPlugin("..\\VSFilterMod.dll")\n'
            f'MaskSubMod("sub.ass", {width}, {height}, {fps_expr}, {frames})\n'
        )
        try:
            payload = script.encode('mbcs')
        except (UnicodeEncodeError, LookupError):
            payload = script.encode('utf-8')
        try:
            with open(os.path.join(work, 'sub.avs'), 'wb') as handle:
                handle.write(payload)
        except OSError as exc:
            return False, '写入渲染脚本失败：' + str(exc)

        exe = os.path.join(runtime, 'avs2bdnxml.exe')
        active_args = []
        if spec['arch'] == 'x64':
            active_name = _write_active_ranges(work, ass_path, fps_value, frames)
            if active_name:
                active_args = ['-R', active_name]
        base_args = [
            exe, '-o', 'bdn.xml',
            '-v', fmt, '-f', str(fps), '-l', language or 'und',
            '-t', title,
            '-a', '1', '-p', '0', '-e', '0',
        ] + active_args

        def segment_args(index, start, count, single=False):
            args = base_args[:]
            if not single:
                args[2] = f'bdn_{index}.xml'
            args += ['-j', str(start), '-c', str(count), 'sub.avs']
            return args

        def run_args(args):
            try:
                return run_process(
                    args, cwd=work, timeout=10800, cancel_event=cancel_event)
            except RenderCancelled:
                raise
            except (OSError, subprocess.SubprocessError) as exc:
                return exc

        def clean_work():
            for name in os.listdir(work):
                if name.endswith('.png') or name.endswith('.xml'):
                    try:
                        os.remove(os.path.join(work, name))
                    except OSError:
                        pass

        def render_single():
            if log:
                log(f"正在用 VSFilterMod {spec['arch']} 渲染字幕帧…")
            result = run_args(segment_args(0, first_frame, frames - first_frame, single=True))
            if isinstance(result, Exception):
                return False, 'VSFilterMod 渲染失败：' + str(result)
            if result.returncode != 0 or not os.path.isfile(os.path.join(work, 'bdn.xml')):
                tail = (result.stderr or result.stdout or '').strip().splitlines()[-4:]
                return False, 'VSFilterMod 渲染失败：' + (' / '.join(tail) or '未知错误')
            return True, ''

        rendered = False
        if len(segments) > 1:
            if log:
                log(f"正在用 VSFilterMod {spec['arch']} 并行渲染字幕帧（{len(segments)} 段）…")
            lock = threading.Lock()
            errors = []
            cancelled = threading.Event()
            done = 0

            def worker(index, start, count):
                nonlocal done
                try:
                    result = run_args(segment_args(index, start, count))
                except RenderCancelled:
                    cancelled.set()
                    return
                with lock:
                    if isinstance(result, Exception):
                        errors.append(str(result))
                    elif result.returncode != 0 or not os.path.isfile(
                            os.path.join(work, f'bdn_{index}.xml')):
                        tail = (result.stderr or result.stdout or '').strip().splitlines()[-3:]
                        errors.append(' / '.join(tail) or '未知错误')
                    done += 1

            threads = [
                threading.Thread(target=worker, args=(index, start, count), daemon=True)
                for index, (start, count) in enumerate(segments)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            if cancelled.is_set():
                clean_work()
                raise RenderCancelled('已取消')
            if errors:
                if log:
                    log('并行渲染失败，改用单进程渲染…')
                clean_work()
            else:
                xml_paths = [os.path.join(work, f'bdn_{index}.xml') for index in range(len(segments))]
                if _merge_bdn_parts(xml_paths, os.path.join(work, 'bdn.xml')):
                    rendered = True
                    for path in xml_paths:
                        try:
                            os.remove(path)
                        except OSError:
                            pass

        if not rendered:
            ok, message = render_single()
            if not ok:
                return False, message

        monitor_stop.set()
        os.makedirs(out_dir, exist_ok=True)
        for name in os.listdir(work):
            if is_bdn_artifact(name):
                src = os.path.join(work, name)
                dst = os.path.join(out_dir, name)
                if os.path.exists(dst):
                    try:
                        os.remove(dst)
                    except OSError:
                        pass
                shutil.move(src, dst)
        report(95)
        return True, ''
    finally:
        monitor_stop.set()
        monitor_thread.join(timeout=2)
        shutil.rmtree(work, ignore_errors=True)


PGS_SEGMENT_TYPES = (0x14, 0x15, 0x16, 0x17, 0x80)
PES_MUX_OFFSET = 54000000
_PGS_W32 = 0xFFFFFFFF
_PGS_W33 = 0x1FFFFFFFF
_PGS_W39 = 0x7FFFFFFFFF
_PGS_WRAP_MARGIN = 900000


def _sup_timestamp(value: int, index: int) -> int:
    """把 SUP 里靠近 32 位回绕点的负 DTS/PTS 还原成负值。"""
    if index < 10 and value > _PGS_W32 - _PGS_WRAP_MARGIN:
        return value - 0x100000000
    return value


def sup_to_pes(sup_path: str, out_dir: str = '', log=None, progress=None,
               cancel_event=None):
    """把已有 SUP/PGS 直接封装为 PES + MUI，不重新渲染或编码。"""
    source = os.path.abspath(sup_path)
    if not os.path.isfile(source):
        return False, '找不到 SUP 文件', []
    target_dir = os.path.abspath(out_dir) if out_dir else os.path.dirname(source)
    try:
        os.makedirs(target_dir, exist_ok=True)
    except OSError as exc:
        return False, f'无法创建输出目录：{exc}', []
    stem = os.path.splitext(os.path.basename(source))[0]
    pes_path = os.path.join(target_dir, stem + '.pes')
    mui_path = os.path.join(target_dir, stem + '.pes.mui')
    tmp_pes = pes_path + '.tmp'
    tmp_mui = mui_path + '.tmp'
    total = os.path.getsize(source)
    segment_count = 0
    has_pcs = False
    try:
        with open(source, 'rb') as src, \
                open(tmp_pes, 'wb') as pes, open(tmp_mui, 'wb') as mui:
            mui.write(b'\x00\x00\x00\x03')
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise RenderCancelled('已取消')
                header = src.read(13)
                if not header:
                    break
                if len(header) < 13:
                    raise ValueError('SUP 文件结尾不完整')
                if header[:2] != b'PG':
                    raise ValueError(
                        f'SUP 段头无效（偏移 {src.tell() - len(header)}）')
                pts, dts, seg_type, length = struct.unpack('>IIBH', header[2:])
                if seg_type not in PGS_SEGMENT_TYPES:
                    raise ValueError(f'未知 PGS 段类型：0x{seg_type:02X}')
                payload = src.read(length)
                if len(payload) != length:
                    raise ValueError('SUP 段数据不完整')
                pts = _sup_timestamp(pts, segment_count)
                dts = _sup_timestamp(dts, segment_count)
                mux_pts = (PES_MUX_OFFSET + pts) & _PGS_W33
                mux_dts = (PES_MUX_OFFSET + dts) & _PGS_W33
                mui.write(struct.pack('>BI', seg_type, length + 3))
                meta = bytearray(9)
                meta[:4] = struct.pack('>I', (mux_dts >> 1) & _PGS_W32)
                meta[4:9] = struct.pack('>Q', (mux_pts << 6) & _PGS_W39)[3:]
                meta[4] |= ((mux_dts & 1) << 7)
                mui.write(meta)
                pes.write(struct.pack('>BH', seg_type, length))
                pes.write(payload)
                segment_count += 1
                if seg_type == 0x16:
                    has_pcs = True
                if progress and total:
                    progress(min(99.0, src.tell() * 100.0 / total))
            mui.write(b'\xFF' + bytes(13))
        if not segment_count or not has_pcs:
            raise ValueError('SUP 中没有有效的 PGS 显示集')
        os.replace(tmp_pes, pes_path)
        os.replace(tmp_mui, mui_path)
    except RenderCancelled:
        for path in (tmp_pes, tmp_mui):
            try:
                os.remove(path)
            except OSError:
                pass
        raise
    except Exception as exc:
        for path in (tmp_pes, tmp_mui):
            try:
                os.remove(path)
            except OSError:
                pass
        return False, str(exc), []
    if progress:
        progress(100)
    if log:
        log(f'SUP 转 PES：{os.path.basename(source)} -> '
            f'{os.path.basename(pes_path)} / {os.path.basename(mui_path)}')
    return True, '', [pes_path, mui_path]


def generate_bdn_sup(ass_path: str, out_dir: str, fmt: str = '1080p', fps: str = '23.976',
                     language: str = 'chi', track_name: str = '',
                     make_sup: bool = True, make_pes: bool = False,
                     params: dict = None, log=None, progress=None, cancel_event=None,
                     allow_x64: bool = True):
    """SUPer 链：渲染 BDN XML + PNG，再交给 SUPer 生成 SUP / PES。"""
    def report(value):
        if progress:
            progress(value)

    report(3)
    ok_combo, combo_message = validate_bdn_format(fmt, fps)
    if not ok_combo:
        return False, combo_message, []
    if cancel_event is not None and cancel_event.is_set():
        raise RenderCancelled('已取消')
    if not os.path.isfile(ass_path):
        return False, '找不到 ASS 字幕文件', []
    if str(fps) not in VS_RENDER_FPS:
        return False, f'VSFilterMod 不支持该帧率：{fps}', []

    os.makedirs(out_dir, exist_ok=True)
    report(10)
    for name in os.listdir(out_dir):
        if is_bdn_artifact(name):
            try:
                os.remove(os.path.join(out_dir, name))
            except OSError:
                pass

    if not vsfilter_available(allow_x64=allow_x64):
        return False, (
            '未找到 VSFilterMod 渲染组件（bin64\\avs2bdnxml.exe / '
            'AviSynth.dll / VSFilterMod.dll，或 32 位 bin 回退）。\n'
            '请检查程序目录是否完整。'), []

    if make_sup or make_pes:
        ok, info = ensure_bdn_tools(log, allow_x64=allow_x64)
        if not ok:
            return False, info, []

    bdn_path = os.path.join(out_dir, 'bdn.xml')
    ok, message = render_bdn_with_vsfilter(
        ass_path, out_dir, fmt, str(fps), language, track_name,
        log=log, progress=progress, cancel_event=cancel_event,
        allow_x64=allow_x64)
    if not ok:
        return False, message, []
    _normalize_bdn_xml(bdn_path)
    outputs = [bdn_path]
    report(95)

    if make_sup or make_pes:
        stem = os.path.splitext(os.path.basename(ass_path))[0]
        sup_path = os.path.join(out_dir, stem + '.sup')
        if log:
            log('正在生成 SUP' + (' 与 PES+MUI' if make_pes else '') + '，复杂特效可能需要几分钟…')
        report(97)
        if cancel_event is not None and cancel_event.is_set():
            raise RenderCancelled('已取消')
        sup_args = build_super_args(params, sup_path, make_pes=make_pes)
        sup_result = run_process(
            sup_args, cwd=out_dir, timeout=7200, cancel_event=cancel_event)
        expected = [sup_path]
        if make_pes:
            expected += [
                os.path.join(out_dir, stem + '.pes'),
                os.path.join(out_dir, stem + '.pes.mui'),
                os.path.join(out_dir, stem + '.mui'),
            ]
        produced = [item for item in expected if os.path.isfile(item)]
        if sup_result.returncode != 0 or not produced:
            tail = (sup_result.stderr or sup_result.stdout or '').strip().splitlines()[-6:]
            return False, 'SUPer 失败：' + (' / '.join(tail) or '未知错误'), outputs + produced
        outputs.extend(produced)
    report(100)
    return True, '', outputs


def _bdn_tc_to_ms(value: str, fps_value: float = 23.976):
    parts = str(value or '').split(':')
    if len(parts) != 4:
        return None
    try:
        hours, minutes, seconds, frames = (int(part) for part in parts)
    except ValueError:
        return None
    base = (hours * 3600 + minutes * 60 + seconds) * 1000
    if fps_value > 0:
        base += int(round(frames / fps_value * 1000))
    return base


def validate_bdn_sup(ass_path: str, out_dir: str, make_sup: bool = True,
                     make_pes: bool = False):
    """生成后检查 BDN 事件、图片引用、时间轴和 SUP 显示段。"""
    info = {'events': 0, 'pcs': 0, 'last_tc': '', 'last_ms': None}
    issues = []
    bdn_path = os.path.join(out_dir, 'bdn.xml')
    if not os.path.isfile(bdn_path):
        return info, ['缺少 bdn.xml']
    try:
        root = ET.parse(bdn_path).getroot()
    except (ET.ParseError, OSError) as exc:
        return info, [f'bdn.xml 解析失败：{exc}']

    events = root.findall('.//Event')
    info['events'] = len(events)
    description = root.find('./Description/Events')
    fps_value = 23.976
    format_node = root.find('./Description/Format')
    if format_node is not None:
        try:
            fps_value = float(format_node.get('FrameRate'))
        except (TypeError, ValueError):
            pass
    if description is not None:
        info['last_tc'] = description.get('LastEventOutTC') or ''
        try:
            declared = int(description.get('NumberofEvents') or 0)
        except ValueError:
            declared = 0
        if declared and declared != len(events):
            issues.append(f'BDN 事件数不一致：声明 {declared}，实际 {len(events)}')
    if not events:
        issues.append('BDN 中没有可显示事件')

    for graphic in root.findall('.//Graphic'):
        name = (graphic.text or '').strip()
        if name and not os.path.isfile(os.path.join(out_dir, name)):
            issues.append(f'BDN 引用的图片不存在：{name}')
            break

    if info['last_tc']:
        raw_last = _bdn_tc_to_ms(info['last_tc'], fps_value)
        if raw_last is not None:
            # avs2bdnxml 对 NTSC 帧率按整数帧率写时码，SUPer 会再换算回来。
            integer_fps = int(round(fps_value))
            factor = fps_value / integer_fps if integer_fps else 1.0
            info['last_ms'] = raw_last / factor if factor else raw_last
            ass_end = _ass_last_time_ms(ass_path)
            if ass_end and info['last_ms'] + 500 < ass_end:
                issues.append('BDN 末事件早于字幕结束时间，可能漏渲')

    if make_sup or make_pes:
        stem = os.path.splitext(os.path.basename(ass_path))[0]
        sup_path = os.path.join(out_dir, stem + '.sup')
        if not os.path.isfile(sup_path):
            issues.append('缺少 SUP 文件')
        else:
            try:
                pcs = []
                file_size = os.path.getsize(sup_path)
                if file_size:
                    with open(sup_path, 'rb') as handle, mmap.mmap(
                            handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
                        index = 0
                        while index + 13 <= file_size:
                            if data[index:index + 2] == b'PG':
                                pts = int.from_bytes(
                                    data[index + 2:index + 6], 'big') & 0x3FFFFFFF
                                segment_type = data[index + 10]
                                size = int.from_bytes(data[index + 11:index + 13], 'big')
                                if segment_type == 0x14:
                                    pcs.append(pts / 90.0)
                                index += 13 + size
                            else:
                                index += 1
                info['pcs'] = len(pcs)
                if not pcs:
                    issues.append('SUP 中没有显示段（PCS=0）')
                else:
                    intervals = _ass_intervals_ms(ass_path)
                    starts = [item[0] for item in intervals]
                    if starts:
                        first_delta = abs(pcs[0] - starts[0])
                        last_delta = abs(pcs[-1] - starts[-1])
                        if first_delta > 500 or last_delta > 500:
                            issues.append(
                                f'SUP 时间轴与字幕不一致：首段差 {first_delta:.0f}ms，'
                                f'末段差 {last_delta:.0f}ms')
                        elif len(pcs) == len(starts):
                            max_delta = max(
                                abs(pcs[i] - starts[i]) for i in range(len(starts)))
                            if max_delta > 500:
                                issues.append(
                                    f'SUP 时间轴与字幕最大相差 {max_delta:.0f}ms')
            except OSError as exc:
                issues.append(f'SUP 读取失败：{exc}')

    if make_pes:
        stem = os.path.splitext(os.path.basename(ass_path))[0]
        for suffix in ('.pes', '.pes.mui'):
            path = os.path.join(out_dir, stem + suffix)
            if not os.path.isfile(path) or os.path.getsize(path) == 0:
                issues.append(f'缺少 PES 输出：{stem}{suffix}')
    return info, issues


BDN_PARAM_DEFAULTS = {
    'compression': '80',
    'acqrate': '100',
    'color': 'bt709',
    'quantizer': '3',
    'threads': 'auto',
    'allow_normal': False,
    'prefer_normal': False,
    'buffer': False,
    'full_palette': False,
    'extra_acq': '2',
    'redraw_period': '0',
    'ssim_tol': '0',
    'max_kbps': '',
    'log_level': 'off',
}

BDN_PARAM_HELP = {
    'compression': '压缩率 0-100。两次事件之间至少要间隔多少时间才做一次刷新（acquisition）。\n'
                   '数值越高越稳定、事件保留越多，码率也越高；越低越省码率，但可能丢事件或出现残留。\n'
                   '建议：普通对白 80-85，复杂特效 90-100。',
    'acqrate': '采集率 0-100。影响压缩因子的衰减速度，进而影响刷新次数。\n'
               '100 表示不再额外压缩；数值越低越省码率但画质下降。\n'
               '建议保持 100，码率超标时再降到 80-90。',
    'color': '目标色彩矩阵。\n'
             'bt709：1080p / 高清常用；bt601：576i/480i 标清；bt2020：UHD / HDR。\n'
             '选错会导致字幕颜色偏色。',
    'quantizer': '调色板量化器，决定每张图的颜色数量与选色方式。\n'
                 'libimagequant：质量最好；Qtzr：质量高但体积大；HexTree：均衡；Pillow：最快但质量最低。',
    'threads': '并行编码线程数。auto 自动选择（最多 8）。\n'
               '机器性能好可设 4-6 提速，过高收益递减。',
    'allow_normal': '允许“普通情况对象重定义”：解码时间不够时只更新两个图形中的一个，减少丢事件。\n'
                    '特效复杂、发现事件缺失时开启。',
    'prefer_normal': '优先“普通情况对象重定义”：即使时间足够也只更新一个对象，可明显降低码率，\n'
                     '但两个图形不再共用调色板，画质可能略降。',
    'buffer': '允许调色板更新缓冲（提前解码显示集），减少丢事件。\n'
              '复杂动画、事件频繁时建议开启。',
    'full_palette': '每次都写入完整调色板，兼容性更好但码率更高。\n'
                    'Authoring 软件或播放器出现调色板异常时开启；普通 SUP 不必。',
    'extra_acq': '调色板更新 N 次后插入一次采集（刷新）。0 关闭，建议 2-5。\n'
                 '可消除调色板动画之后的残影，代价是码率略增。',
    'redraw_period': '锚点间隔（秒）：给长时间不变的字幕定期插入刷新，方便解码器追赶。\n'
                     '0 关闭；最小 1 秒；数值越小码率越高。',
    'ssim_tol': '图像相似度容差偏移 -100~100。\n'
                '正值更容易把两张图判定为相同（省码率，可能丢细节）；负值更保守（更清晰，码率更高）。\n'
                '一般保持 0，闪烁/残影时试 +10~20。',
    'max_kbps': '最大码率检测（Kbps）。只用于检查输出是否超标，不会限制实际码流。\n'
                '0 或留空表示关闭。按目标码率填写，例如 1600。',
    'log_level': '日志写入文件。默认关闭；排查丢事件、码率、兼容性问题时开启。\n'
                 '精简(-10) 只记录关键决策；标准(20)/极简(25)/仅警告(30)/调试(10)。',
}
