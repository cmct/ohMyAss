"""输出文件命名、ASS 文本读写与多语言同步。

这是「生成端」和「问题修复端」共用的唯一出口：
- 输出文件名（含 HDR / SDR / 单语片源差异）
- ASS 文本读取与原子写入
- 双语文本拆分、字数统计、问题行识别
- 按主字幕同步 / 删除其他语言的字幕条

本模块不导入 subtitle_core，避免循环依赖；繁化转换在函数内部按需导入。
"""


import os
import re
from dataclasses import dataclass, field
from functools import lru_cache

from config_store import atomic_write_text, log_exception


# ===================== 输出类别 =====================

OUT_BILINGUAL = '简英双语'
OUT_CHS = '简体中文'
OUT_CHT = '繁体中文'
OUT_ENG = '英文'

ASS_CACHE_MAX_BYTES = 4 * 1024 * 1024

BILINGUAL_PRIMARY = '1-简英双语-zh.ass'
BILINGUAL_PRIMARY_HDR = '1-简英双语-HDR-zh.ass'
CHINESE_PRIMARY = '1-简体中文-zh.ass'
CHINESE_PRIMARY_HDR = '1-简体中文-HDR-zh.ass'

MAX_ZH_LINE = 28
MAX_EN_LINE = 84


# ===================== 样式 / 特效判断 =====================

match_cjk = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]')
match_latin = re.compile(r'[A-Za-z]')
match_events_override_block = re.compile(r'\{[^}]*\}')
match_events_special_effect = re.compile(
    r'\\move\(|\\pos\(|\\org\(|\\i?clip\(|\\fad\(|\\t\(|'
    r'\\[kK]|\\fsc[xy]?|\\fsp|\\fr[xyz]?|\\be|\\bord|\\shad|'
    r'\\alpha|\\[1-4]a|\\a\d+|\\h(?![A-Za-z])|\\q\d|\\p\d',
    re.I,
)


def has_special_effect(text: str) -> bool:
    for block in match_events_override_block.findall(text):
        if match_events_special_effect.search(block):
            return True
    return False


# ===================== ASS 读取 =====================

def _decode_ass_file(path: str) -> str:
    try:
        with open(path, 'rb') as handle:
            data = handle.read()
    except OSError:
        return ''
    if data[:2] in (b'\xff\xfe', b'\xfe\xff'):
        try:
            return data.decode('utf-16')
        except UnicodeDecodeError:
            pass
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode('cp1252', errors='replace')


@lru_cache(maxsize=8)
def _read_ass_text_cached(path: str, _mtime_ns: int, _size: int) -> str:
    return _decode_ass_file(path)


def read_ass_text(path: str) -> str:
    """按 BOM / UTF-8 / GBK 依次读取 ASS 文本；大文件不进缓存。"""
    try:
        stat = os.stat(path)
    except OSError:
        return ''
    if stat.st_size > ASS_CACHE_MAX_BYTES:
        return _decode_ass_file(os.path.abspath(path))
    return _read_ass_text_cached(
        os.path.abspath(path), stat.st_mtime_ns, stat.st_size)


def _read_ass_text_cached_clear():
    """测试或覆盖写回后清缓存；文件 mtime 变化时会自动失效。"""
    _read_ass_text_cached.cache_clear()


def read_ass_lines(path: str) -> list:
    text = read_ass_text(path)
    if not text:
        return []
    return text.replace('\r\n', '\n').replace('\r', '\n').split('\n')


def write_ass_lines(path: str, lines: list) -> None:
    """原子写入 ASS（UTF-8 BOM + LF）。"""
    atomic_write_text(path, '\n'.join(lines))
    _read_ass_text_cached_clear()


# ===================== 输出布局 =====================

@dataclass(frozen=True)
class OutputLayout:
    """一种片源类型对应的全部输出文件。"""

    is_hdr: bool
    chinese_only: bool
    outputs: dict = field(default_factory=dict)   # 文件名（不含 .ass） -> 类别
    primary_names: tuple = ()                     # 主字幕候选，按优先级
    targets: tuple = ()                           # (文件名, 角色)
    sdr_name: str = ''


def output_layout(is_hdr: bool, chinese_only: bool) -> OutputLayout:
    """返回输出布局；文件名均已包含 .ass。"""
    if chinese_only:
        if is_hdr:
            outputs = {
                '1-简体中文-HDR-zh.ass': OUT_CHS,
                '3-繁体中文-HDR-zh.ass': OUT_CHT,
            }
            primary = (CHINESE_PRIMARY_HDR,)
            targets = (
                ('3-繁体中文-HDR-zh.ass', 'cht'),
                ('2-简体中文-SDR-zh.ass', 'sdr'),
            )
            sdr = '2-简体中文-SDR-zh.ass'
        else:
            outputs = {
                '1-简体中文-zh.ass': OUT_CHS,
                '2-繁体中文-zh.ass': OUT_CHT,
            }
            primary = (CHINESE_PRIMARY,)
            targets = (('2-繁体中文-zh.ass', 'cht'),)
            sdr = ''
        return OutputLayout(
            is_hdr=is_hdr, chinese_only=True, outputs=outputs,
            primary_names=primary, targets=targets, sdr_name=sdr)

    if is_hdr:
        outputs = {
            '1-简英双语-HDR-zh.ass': OUT_BILINGUAL,
            '3-简体中文-HDR-zh.ass': OUT_CHS,
            '4-繁体中文-HDR-zh.ass': OUT_CHT,
            '5-英文-HDR-en.ass': OUT_ENG,
        }
        primary = (BILINGUAL_PRIMARY_HDR,)
        targets = (
            ('3-简体中文-HDR-zh.ass', 'chs'),
            ('4-繁体中文-HDR-zh.ass', 'cht'),
            ('5-英文-HDR-en.ass', 'eng'),
            ('2-简英双语-SDR-zh.ass', 'sdr'),
        )
        sdr = '2-简英双语-SDR-zh.ass'
    else:
        outputs = {
            '1-简英双语-zh.ass': OUT_BILINGUAL,
            '2-简体中文-zh.ass': OUT_CHS,
            '3-繁体中文-zh.ass': OUT_CHT,
            '4-英文-en.ass': OUT_ENG,
        }
        primary = (BILINGUAL_PRIMARY,)
        targets = (
            ('2-简体中文-zh.ass', 'chs'),
            ('3-繁体中文-zh.ass', 'cht'),
            ('4-英文-en.ass', 'eng'),
        )
        sdr = ''
    return OutputLayout(
        is_hdr=is_hdr, chinese_only=False, outputs=outputs,
        primary_names=primary, targets=targets, sdr_name=sdr)


def primary_output_path(data: dict) -> str:
    """从问题数据里找生成后的主字幕。

    优先按 JSON 里的 chinese_only 判断，避免同一文件夹残留的旧语言版本
    被误当成当前主字幕；旧数据没有该字段时按双语优先。
    """
    output_dir = str((data or {}).get('output_dir') or '')
    if not output_dir:
        return ''
    chinese_only = bool(data.get('chinese_only'))
    if chinese_only:
        names = ([CHINESE_PRIMARY_HDR, BILINGUAL_PRIMARY_HDR]
                 if data.get('is_hdr') else [])
        names += [CHINESE_PRIMARY, BILINGUAL_PRIMARY]
    else:
        names = ([BILINGUAL_PRIMARY_HDR, CHINESE_PRIMARY_HDR]
                 if data.get('is_hdr') else [])
        names += [BILINGUAL_PRIMARY, CHINESE_PRIMARY]
    for name in names:
        path = os.path.join(output_dir, name)
        if os.path.isfile(path):
            return path
    return ''


def layout_for_primary(primary_path: str) -> OutputLayout:
    """按主字幕文件名推断布局（兼容用户手动改名前后的路径）。"""
    name = os.path.basename(primary_path or '')
    return output_layout(
        is_hdr='-HDR-' in name,
        chinese_only='简体中文' in name and '简英双语' not in name,
    )


# ===================== 行解析 =====================

def dialogue_records(lines: list) -> list:
    records = []
    for index, line in enumerate(lines):
        if not line.lstrip().lower().startswith('dialogue:'):
            continue
        fields = line.split(',', 9)
        if len(fields) < 10:
            continue
        records.append((index, fields, has_special_effect(fields[9])))
    return records


def visible_text(text: str) -> str:
    return re.sub(r'\{[^}]*\}', '', text)


def leading_tags(text: str) -> str:
    match = re.match(r'^((?:\{[^}]*\})+)?', text)
    return match.group(1) if match and match.group(1) else ''


def strip_leading_tags(text: str) -> str:
    return re.sub(r'^(?:\{[^}]*\})+', '', text)


def _segment_is_chinese(segment: str) -> bool:
    visible = visible_text(segment)
    return bool(match_cjk.search(visible)) or not match_latin.search(visible)


def split_bilingual_text(text: str) -> tuple:
    """把双语文本按可见文字拆成 (中文部分, 英文部分)。"""
    chinese = []
    english = []
    for part in text.split('\\N'):
        if _segment_is_chinese(part):
            chinese.append(part)
        else:
            english.append(part)
    return '\\N'.join(chinese), '\\N'.join(english)


def measure_text(text: str) -> dict:
    """统计最长中文行 / 英文行，用于编辑时实时提示。"""
    zh_len = 0
    en_len = 0
    for part in text.split('\\N'):
        length = len(visible_text(part))
        if _segment_is_chinese(part):
            zh_len = max(zh_len, length)
        else:
            en_len = max(en_len, length)
    return {
        'zh': zh_len,
        'en': en_len,
        'zh_limit': MAX_ZH_LINE,
        'en_limit': MAX_EN_LINE,
        'over': zh_len > MAX_ZH_LINE or en_len > MAX_EN_LINE,
    }


# ===================== 同步 / 删除 =====================

@dataclass
class SyncResult:
    synced: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    error: str = ''
    unchanged: list = field(default_factory=list)
    cht_cache: dict = field(default_factory=dict)


@dataclass
class DeleteResult:
    deleted: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    error: str = ''


def _language_entries(records: list, is_bilingual: bool):
    chinese = []
    english = []
    full = []
    for _index, fields, special in records:
        entry = (fields[1], fields[2], fields[9])
        full.append(entry)
        if special:
            chinese.append(entry)
            continue
        if is_bilingual:
            zh_text, en_text = split_bilingual_text(fields[9])
            if zh_text:
                chinese.append((fields[1], fields[2], zh_text))
            if en_text:
                english.append((fields[1], fields[2], en_text))
        else:
            chinese.append(entry)
    return chinese, english, full


def _entries_for_role(role: str, chinese: list, english: list, full: list):
    if role in ('chs', 'cht'):
        return chinese
    if role == 'eng':
        return english
    return full


def sync_outputs(layout: OutputLayout, primary_path: str, lines: list,
                 meta: dict = None, cht_cache: dict = None) -> SyncResult:
    """把主字幕的文本与时间同步到同目录下的其他语言文件。"""
    from subtitle_core import convert_cht_contents

    meta = meta or {}
    result = SyncResult()
    result.cht_cache = cht_cache if cht_cache is not None else {}
    out_dir = os.path.dirname(primary_path)
    is_bilingual = not layout.chinese_only
    records = dialogue_records(lines)
    chinese, english, full = _language_entries(records, is_bilingual)

    for name, role in layout.targets:
        path = os.path.join(out_dir, name)
        if not os.path.isfile(path):
            continue
        old_text = read_ass_text(path)
        if not old_text:
            continue
        target_lines = old_text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        target_records = dialogue_records(target_lines)
        entries = _entries_for_role(role, chinese, english, full)
        if len(entries) != len(target_records):
            result.skipped.append(name)
            log_exception(
                f'同步跳过：{name}（主字幕 {len(entries)} 行，目标 {len(target_records)} 行）')
            continue
        for (target_index, target_fields, _special), (start, end, new_text) in zip(
                target_records, entries):
            if role == 'eng':
                prefix = leading_tags(target_fields[9]) or '{\\blur2}'
                new_text = prefix + strip_leading_tags(new_text)
            target_fields[1] = start
            target_fields[2] = end
            target_fields[9] = new_text
            target_lines[target_index] = ','.join(target_fields)
        if role == 'cht':
            try:
                target_lines, _changes, error, _overlong = convert_cht_contents(
                    target_lines,
                    use_api=bool(meta.get('taiwan', True)),
                    cache=result.cht_cache,
                )
                if error and not result.error:
                    result.error = error
            except Exception as exc:
                # 转换失败时不要把简体直接写进繁体文件；保留原文件并标记跳过
                log_exception('同步繁体字幕', exc)
                result.skipped.append(name)
                if not result.error:
                    result.error = str(exc)
                continue
        new_text = '\n'.join(target_lines)
        if new_text == old_text.replace('\r\n', '\n').replace('\r', '\n'):
            result.unchanged.append(name)
            result.synced.append(name)
            continue
        try:
            write_ass_lines(path, target_lines)
        except OSError as exc:
            log_exception(f'同步写入：{name}', exc)
            result.skipped.append(name)
            if not result.error:
                result.error = str(exc)
            continue
        result.synced.append(name)
    return result


def _language_positions(records: list, is_bilingual: bool):
    chinese = []
    english = []
    for pos, (_index, fields, special) in enumerate(records):
        if special:
            chinese.append(pos)
            continue
        if is_bilingual:
            zh_text, en_text = split_bilingual_text(fields[9])
            if zh_text:
                chinese.append(pos)
            if en_text:
                english.append(pos)
        else:
            chinese.append(pos)
    return chinese, english


def delete_dialogue(layout: OutputLayout, primary_path: str, lines: list,
                    position: int) -> DeleteResult:
    """从其他语言文件删除与主字幕第 position 条对应的行。"""
    result = DeleteResult()
    out_dir = os.path.dirname(primary_path)
    records = dialogue_records(lines)
    if not (0 <= position < len(records)):
        return result
    is_bilingual = not layout.chinese_only
    chinese_positions, english_positions = _language_positions(records, is_bilingual)
    all_positions = list(range(len(records)))

    for name, role in layout.targets:
        path = os.path.join(out_dir, name)
        if not os.path.isfile(path):
            continue
        if role in ('chs', 'cht'):
            sequence = chinese_positions
        elif role == 'eng':
            sequence = english_positions
        else:
            sequence = all_positions
        if position not in sequence:
            continue
        target_lines = read_ass_lines(path)
        if not target_lines:
            continue
        target_records = dialogue_records(target_lines)
        if len(target_records) != len(sequence):
            result.skipped.append(name)
            log_exception(
                f'删除同步跳过：{name}（主字幕 {len(sequence)} 行，目标 {len(target_records)} 行）')
            continue
        ordinal = sequence.index(position)
        line_index = target_records[ordinal][0]
        del target_lines[line_index]
        try:
            write_ass_lines(path, target_lines)
        except OSError as exc:
            result.skipped.append(name)
            log_exception(f'删除同步失败：{name}', exc)
            if not result.error:
                result.error = str(exc)
            continue
        result.deleted.append(name)
    return result
