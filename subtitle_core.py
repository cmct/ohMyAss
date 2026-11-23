"""ASS/SRT 解析、文本规则、HDR、术语与字幕生成流程。"""


import os
import re
import json
import html
import time
import threading
import zhconv
from enum import Enum
from collections import Counter
from functools import lru_cache
import urllib.request
import urllib.parse

try:
    from PIL import Image, ImageChops
except ImportError:
    Image = ImageChops = None

from config_store import (
    atomic_write_text, get_output_dir, log_exception, write_text_with_fallback,
)
from reports import (
    NOTICES_DATA_NAME, NOTICES_REPORT_NAME, TAIWAN_REPORT_NAME, build_notices_html,
    build_taiwan_compare_html,
)
from subtitle_outputs import (
    OUT_BILINGUAL, OUT_CHS, OUT_CHT, OUT_ENG,
    has_special_effect, match_cjk, match_latin, output_layout,
)


class ProcessCancelled(Exception):
    """用户在生成过程中取消。"""


match_paragraph_script_info = re.compile(r'\[Script Info\]', re.I)
match_paragraph_styles = re.compile(r'\[V4\+ Styles\]', re.I)
match_paragraph_events = re.compile(r'\[Events\]', re.I)
match_paragraph_aegisub = re.compile(r'\[Aegisub Project Garbage\]', re.I)
match_script_info_play_x = re.compile(r'PlayResX: *(\d+)', re.I)
match_script_info_play_y = re.compile(r'PlayResY: *(\d+)', re.I)

match_events_dialogue = re.compile(r'^\s*Dialogue:', re.I)
match_events_newline = re.compile(r'\\N')
match_events_fs = re.compile(r'\\fs(\d+)', re.I)

match_override_split = re.compile(r'(\{[^}]*\})')
match_events_move = re.compile(r'\\move\(([\d.]+),([\d.]+),([\d.]+),([\d.]+)', re.I)
match_events_pos = re.compile(r'\\pos\(([\d.]+),([\d.]+)', re.I)
match_events_org = re.compile(r'\\org\(([\d.]+),([\d.]+)', re.I)
match_events_iclip = re.compile(r'\\iclip\(([\d.]+),([\d.]+),([\d.]+),([\d.]+)', re.I)
match_events_clip = re.compile(r'\\clip\(([\d.]+),([\d.]+),([\d.]+),([\d.]+)', re.I)

match_override_tag = re.compile(r'{.*?}')
match_font_tag = re.compile(r'\\fn[^\\}]*')
match_reset_tag = re.compile(r'\\r[^\\}]*')
match_font_size_tag = re.compile(r'\\fs\d+')
match_colour_tag = re.compile(r'\\[1-4]?[ac]&H[0-9A-Fa-f]+&?')
match_blur3_tag = re.compile(r'\\blur3(?![0-9])')
match_blur_tag = re.compile(r'\\blur\d*')
match_fsvp_tag = re.compile(r'\\fsvp\d+')
match_bold_tag = re.compile(r'\\b(?:[1-9]\d*|0)?(?![A-Za-z])', re.I)
match_italic_tag = re.compile(r'\\i[01]?(?![A-Za-z])', re.I)
match_underline_tag = re.compile(r'\\u[01]?(?![A-Za-z])', re.I)
match_strike_tag = re.compile(r'\\s[01]?(?![A-Za-z])', re.I)
match_encoding_tag = re.compile(r'\\fe[^\\}]*', re.I)
match_empty_tag = re.compile(r'\{\s*\}')

MIN_EVENT_DURATION_MS = 500
MIN_EVENT_GAP_MS = 200

# 繁化姬（https://zhconvert.org）台湾化接口
ZHCONVERT_API = 'https://api.zhconvert.org/convert'
ZHCONVERT_CONVERTER = 'Taiwan'
ZHCONVERT_MAX_CHARS = 4000
ZHCONVERT_TIMEOUT = 30
EDIT_SOURCE_PREFIX = '字幕修改稿'

# ASS 样式行字段位置
STYLE_COLOUR_FIELDS = (3, 4, 5, 6)
STYLE_FONT_SIZE_FIELD = 2
STYLE_MARGIN_V_FIELD = 21

# ssaHdrify（https://github.com/gky99/ssaHdrify）色彩映射参数
HDR_SUB_BRIGHTNESS = 100.0
HDR_SRGB_TO_XYZ = (
    (0.4124, 0.3576, 0.1805),
    (0.2126, 0.7152, 0.0722),
    (0.0193, 0.1192, 0.9505),
)
HDR_XYZ_TO_BT2020 = (
    (1.71665119, -0.35567078, -0.25336628),
    (-0.66668435, 1.61648124, 0.01576855),
    (0.01763986, -0.04277061, 0.94210312),
)
HDR_PQ_M1 = 2610 / 16384
HDR_PQ_M2 = 2523 / 4096 * 128
HDR_PQ_C1 = 3424 / 4096
HDR_PQ_C2 = 2413 / 4096 * 32
HDR_PQ_C3 = 2392 / 4096 * 32

match_inline_colour = re.compile(r'(\\[1-4]?c&H)([0-9A-Fa-f]{2,})(?=[&})\\])')


def _decode_srgb(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _pq_encode(nits: float) -> float:
    luma = min(max(nits / 10000.0, 0.0), 1.0) ** HDR_PQ_M1
    return ((HDR_PQ_C1 + HDR_PQ_C2 * luma) / (1 + HDR_PQ_C3 * luma)) ** HDR_PQ_M2


def srgb_to_hdr(rgb, brightness: float = HDR_SUB_BRIGHTNESS):
    """按 ssaHdrify 的流程把 sRGB 颜色映射到 BT.2020 + PQ。"""
    red, green, blue = (_decode_srgb(value / 255.0) for value in rgb)
    x = HDR_SRGB_TO_XYZ[0][0] * red + HDR_SRGB_TO_XYZ[0][1] * green + HDR_SRGB_TO_XYZ[0][2] * blue
    y = HDR_SRGB_TO_XYZ[1][0] * red + HDR_SRGB_TO_XYZ[1][1] * green + HDR_SRGB_TO_XYZ[1][2] * blue
    z = HDR_SRGB_TO_XYZ[2][0] * red + HDR_SRGB_TO_XYZ[2][1] * green + HDR_SRGB_TO_XYZ[2][2] * blue
    x *= brightness
    y *= brightness
    z *= brightness
    linear = (
        HDR_XYZ_TO_BT2020[0][0] * x + HDR_XYZ_TO_BT2020[0][1] * y + HDR_XYZ_TO_BT2020[0][2] * z,
        HDR_XYZ_TO_BT2020[1][0] * x + HDR_XYZ_TO_BT2020[1][1] * y + HDR_XYZ_TO_BT2020[1][2] * z,
        HDR_XYZ_TO_BT2020[2][0] * x + HDR_XYZ_TO_BT2020[2][1] * y + HDR_XYZ_TO_BT2020[2][2] * z,
    )
    return tuple(int(round(_pq_encode(value) * 255)) for value in linear)


def parse_ass_colour(digits: str):
    """解析 BBGGRR / AABBGGRR，返回 (alpha, (r, g, b))，格式不符时返回 None。"""
    if len(digits) == 8:
        alpha, body = digits[:2], digits[2:]
    elif len(digits) == 6:
        alpha, body = '', digits
    else:
        return None
    try:
        blue, green, red = int(body[0:2], 16), int(body[2:4], 16), int(body[4:6], 16)
    except ValueError:
        return None
    return alpha, (red, green, blue)


def format_ass_colour(alpha: str, rgb) -> str:
    red, green, blue = rgb
    return f'&H{alpha}{blue:02X}{green:02X}{red:02X}'


def hdrify_ass_colour(value: str, brightness: float = HDR_SUB_BRIGHTNESS) -> str:
    """把 ASS 的 &HAABBGGRR 颜色转换成 HDR 映射结果。"""
    text = value.strip()
    if not text.upper().startswith('&H'):
        return value
    parsed = parse_ass_colour(text[2:].rstrip('&'))
    if not parsed:
        return value
    alpha, rgb = parsed
    return format_ass_colour(alpha, srgb_to_hdr(rgb, brightness))


def hdrify_style_line(line: str, brightness: float = HDR_SUB_BRIGHTNESS) -> str:
    data = line.split(',')
    for index in STYLE_COLOUR_FIELDS:
        data[index] = hdrify_ass_colour(data[index], brightness)
    return ','.join(data)


def hdrify_event_colours(text: str, brightness: float = HDR_SUB_BRIGHTNESS) -> str:
    """转换行内的 \\c&HBBGGRR& 等颜色标签。"""

    def replacer(match):
        prefix, digits = match.group(1), match.group(2)
        parsed = parse_ass_colour(digits)
        if not parsed:
            return match.group(0)
        alpha, rgb = parsed
        red, green, blue = srgb_to_hdr(rgb, brightness)
        return f'{prefix}{alpha}{blue:02X}{green:02X}{red:02X}'

    return match_inline_colour.sub(replacer, text)


SDR_STYLES = [
    'Style: zh_dialogue_bilingual,思源黑体 Medium,80,&H00EAEAEA,&HF0000000,&H000D0500,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,0,0,14,1',
    'Style: zh_dialogue_monolingual,思源黑体 Medium,80,&H00EAEAEA,&HF0000000,&H000D0500,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,0,0,40,1',
    'Style: en_dialogue_bilingual,思源黑体 Medium,52,&H003CA8DC,&HF0000000,&H000D0500,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,0,0,14,1',
    'Style: en_dialogue_monolingual,思源黑体 Medium,52,&H00EAEAEA,&HF0000000,&H000D0500,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,0,0,40,1',
    'Style: zh_lyrics_bilingual,思源黑体 Medium,80,&H00EAEAEA,&HF0000000,&H000D0500,&H00000000,0,0,0,0,100,100,0,0,1,2,0,7,66,0,14,1',
    'Style: zh_lyrics_monolingual,思源黑体 Medium,80,&H00EAEAEA,&HF0000000,&H000D0500,&H00000000,0,0,0,0,100,100,0,0,1,2,0,7,66,0,40,1',
    'Style: en_lyrics_bilingual,思源黑体 Medium,52,&H003CA8DC,&HF0000000,&H000D0500,&H00000000,0,0,0,0,100,100,0,0,1,2,0,7,66,0,14,1',
    'Style: en_lyrics_monolingual,思源黑体 Medium,52,&H00EAEAEA,&HF0000000,&H000D0500,&H00000000,0,0,0,0,100,100,0,0,1,2,0,7,66,0,40,1',
    'Style: zh_note_single_line,思源黑体 Medium,66,&H00EAEAEA,&HF0000000,&H000D0500,&H00000000,0,0,0,0,100,100,0,0,1,2,0,8,0,0,40,1',
    'Style: zh_note_double_line,思源黑体 Medium,66,&H00EAEAEA,&HF0000000,&H000D0500,&H00000000,0,0,0,0,100,100,0,0,1,2,0,8,0,0,14,1',
    'Style: Default,思源黑体 Medium,80,&H00EAEAEA,&HF0000000,&H000D0500,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,0,0,66,1',
]

# 双语样式成对共用行间距：修改任一类别，另一类别同步
FSVP_PAIRS = (
    ('zh_dialogue_bilingual', 'en_dialogue_bilingual'),
    ('zh_lyrics_bilingual', 'en_lyrics_bilingual'),
)
FSVP_EDIT_KINDS = {
    'zh_dialogue_bilingual', 'en_dialogue_bilingual',
    'zh_lyrics_bilingual', 'en_lyrics_bilingual',
    'zh_note_double_line',
}


def linked_fsvp_kinds(kind: str) -> tuple:
    for primary, secondary in FSVP_PAIRS:
        if kind in (primary, secondary):
            return primary, secondary
    return (kind,)


def normalize_glossary(data) -> list:
    """把配置里的术语表统一成 {source, target, case_sensitive, note}。"""
    result = []
    if not isinstance(data, list):
        return result
    seen = {}
    for item in data:
        if isinstance(item, dict):
            source = str(item.get('source', '')).strip()
            target = str(item.get('target', '')).strip()
            case_sensitive = bool(item.get('case_sensitive'))
            note = str(item.get('note', '')).strip()
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            source = str(item[0]).strip()
            target = str(item[1]).strip()
            case_sensitive = bool(item[2]) if len(item) > 2 else False
            note = str(item[3]).strip() if len(item) > 3 else ''
        else:
            continue
        if not source or not target:
            continue
        entry = {
            'source': source,
            'target': target,
            'case_sensitive': case_sensitive,
            'note': note,
        }
        if source in seen:
            result[seen[source]] = entry
        else:
            seen[source] = len(result)
            result.append(entry)
    return result


def term_in_text(text: str, term: str, case_sensitive: bool = False) -> bool:
    """术语匹配：英文按单词边界，中文按子串；可选区分大小写。"""
    if not text or not term:
        return False
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .&'\-]*", term):
        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = r'(?<![A-Za-z0-9])' + re.escape(term) + r'(?![A-Za-z0-9])'
        return re.search(pattern, text, flags) is not None
    if case_sensitive:
        return term in text
    return term.casefold() in text.casefold()

STYLE_CATEGORIES = (
    ('zh_dialogue_bilingual', '中文对白（双语）'),
    ('zh_dialogue_monolingual', '中文对白（单语）'),
    ('en_dialogue_bilingual', '英文对白（双语）'),
    ('en_dialogue_monolingual', '英文对白（单语）'),
    ('zh_lyrics_bilingual', '中文歌词（双语）'),
    ('zh_lyrics_monolingual', '中文歌词（单语）'),
    ('en_lyrics_bilingual', '英文歌词（双语）'),
    ('en_lyrics_monolingual', '英文歌词（单语）'),
    ('zh_note_single_line', '中文注释（单行）'),
    ('zh_note_double_line', '中文注释（双行）'),
    ('Default', 'Default'),
)
STYLE_LABEL_TO_KIND = {label: kind for kind, label in STYLE_CATEGORIES}
STYLE_KIND_TO_LABEL = {kind: label for kind, label in STYLE_CATEGORIES}
STYLE_FIELD_NAMES = (
    'Name', 'Fontname', 'Fontsize', 'PrimaryColour', 'SecondaryColour',
    'OutlineColour', 'BackColour', 'Bold', 'Italic', 'Underline',
    'StrikeOut', 'ScaleX', 'ScaleY', 'Spacing', 'Angle', 'BorderStyle',
    'Outline', 'Shadow', 'Alignment', 'MarginL', 'MarginR', 'MarginV',
    'Encoding',
)
STYLE_FIELD_LABELS = {
    1: '字体',
    2: '字号',
    3: '主色',
    4: '次色',
    5: '描边色',
    6: '背景色',
    7: '粗体',
    8: '斜体',
    9: '下划线',
    10: '删除线',
    11: '横向缩放',
    12: '纵向缩放',
    13: '字间距',
    14: '旋转角度',
    15: '边框样式',
    16: '描边宽度',
    17: '阴影距离',
    18: '对齐方式',
    19: '左边距',
    20: '右边距',
    21: '垂直边距',
}
STYLE_FIELD_HELP = {
    1: 'Fontname：字体名称，需与系统已安装字体一致',
    2: 'Fontsize：字号，1080p 下约为字形像素高度',
    3: 'PrimaryColour：主色，格式 &HAABBGGRR，也支持 #RRGGBB',
    4: 'SecondaryColour：次色，用于卡拉 OK 等次要颜色',
    5: 'OutlineColour：描边颜色',
    6: 'BackColour：背景/阴影颜色，AA 为透明度',
    7: 'Bold：是否粗体，0 否 / 1 是',
    8: 'Italic：是否斜体，0 否 / 1 是',
    9: 'Underline：是否下划线，0 否 / 1 是',
    10: 'StrikeOut：是否删除线，0 否 / 1 是',
    11: 'ScaleX：横向缩放百分比，100 为原始宽度',
    12: 'ScaleY：纵向缩放百分比，100 为原始高度',
    13: 'Spacing：字符间距，单位像素，正数加宽',
    14: 'Angle：旋转角度，逆时针为正',
    15: 'BorderStyle：1 描边+阴影，3 不透明背景框',
    16: 'Outline：描边宽度，单位像素',
    17: 'Shadow：阴影距离，单位像素',
    18: 'Alignment：小键盘对齐，1–9；2 底部居中、7 左上、8 顶部居中',
    19: 'MarginL：左边距，单位像素',
    20: 'MarginR：右边距，单位像素',
    21: 'MarginV：垂直边距，距顶部或底部，取决于对齐方式',
}


def default_style_line(kind: str) -> str:
    prefix = f'Style: {kind},'
    for line in SDR_STYLES:
        if line.startswith(prefix):
            return line
    # 未知类别统一退回 Default，避免误用第一个样式
    for line in SDR_STYLES:
        if line.startswith('Style: Default,'):
            return line
    return SDR_STYLES[0]

def style_line_name(line: str) -> str:
    """读取样式行里的样式名（第一个字段冒号后的部分）。"""
    return line.split(',', 1)[0].split(':', 1)[-1].strip()

def rename_style_line(line: str, kind: str) -> str:
    """把样式行的样式名替换为新的固定名称，其余字段保持不变。"""
    parts = line.split(',')
    parts[0] = f'Style: {kind}'
    return ','.join(parts)

def apply_updated_margin_v(line: str, kind: str, margin: int) -> str:
    """仅当旧样式行与当前内置默认仅差 MarginV 时，同步新的默认垂直边距。"""
    default = default_style_line(kind)
    parts = line.split(',')
    base = default.split(',')
    field = STYLE_MARGIN_V_FIELD
    if len(parts) != len(base):
        return line
    if parts[field] == str(margin):
        return line
    if parts[:field] == base[:field] and parts[field + 1:] == base[field + 1:]:
        parts[field] = str(margin)
        return ','.join(parts)
    return line

def apply_updated_fontsize(line: str, kind: str, size: int) -> str:
    """仅当旧样式行与当前内置默认仅差字号时，同步新的默认字号。"""
    default = default_style_line(kind)
    parts = line.split(',')
    base = default.split(',')
    field = STYLE_FONT_SIZE_FIELD
    if len(parts) != len(base):
        return line
    if parts[field] == str(size):
        return line
    if parts[:field] == base[:field] and parts[field + 1:] == base[field + 1:]:
        parts[field] = str(size)
        return ','.join(parts)
    return line

def default_margin_v(kind: str) -> int:
    """取内置样式的垂直边距。"""
    parts = default_style_line(kind).split(',')
    if len(parts) > STYLE_MARGIN_V_FIELD:
        return int(parts[STYLE_MARGIN_V_FIELD])
    return 0

# 中黑边视频需要把字幕整体上移，固定在内置边距基础上增加 44px
MID_BLACK_MARGIN_OFFSET = 44
MID_BLACK_MARGINS = {
    kind: default_margin_v(kind) + MID_BLACK_MARGIN_OFFSET
    for kind, _label in STYLE_CATEGORIES if kind != 'Default'
}

def srt_time_to_ass(value: str):
    match = re.match(r'^\s*(\d+):(\d{1,2}):(\d{1,2})(?:[,.](\d{1,3}))?', value)
    if not match:
        return None
    hours, minutes, seconds, fraction = match.groups()
    fraction = fraction or '0'
    milliseconds = int(fraction.ljust(3, '0')[:3])
    return f'{int(hours)}:{int(minutes):02d}:{int(seconds):02d}.{milliseconds // 10:02d}'

def srt_to_ass(text: str) -> str:
    blocks = re.split(r'\n\s*\n', text.strip())
    dialogues = []
    for block in blocks:
        lines = [line.rstrip() for line in block.splitlines()]
        time_index = None
        start = end = None
        for index, line in enumerate(lines):
            if '-->' in line:
                parts = line.split('-->')
                if len(parts) >= 2:
                    start = srt_time_to_ass(parts[0])
                    end = srt_time_to_ass(parts[1].strip().split()[0])
                    time_index = index
                break
        if time_index is None or start is None or end is None:
            continue
        text_lines = []
        for line in lines[time_index + 1:]:
            line = line.strip()
            if not line:
                continue
            line = re.sub(r'<br\s*/?>', r'\\N', line, flags=re.I)
            line = re.sub(r'<[^>]+>', '', line)
            line = html.unescape(line).strip()
            if line:
                text_lines.append(line)
        if not text_lines:
            continue
        dialogue_text = r'\N'.join(text_lines)
        dialogues.append(f'Dialogue: 0,{start},{end},Default,,0,0,0,,{dialogue_text}\n')

    header = (
        '[Script Info]\n'
        'ScriptType: v4.00+\n'
        'PlayResX: 1920\n'
        'PlayResY: 1080\n'
        '\n'
        '[V4+ Styles]\n'
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
        f'{default_style_line("Default")}\n'
        '\n'
        '[Events]\n'
        'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
    )
    return header + ''.join(dialogues)

class Section(Enum):
    SCRIPT_INFO = 1
    STYLES = 2
    EVENTS = 3
    AEGISUB = 10
    OTHERS = 0

class Aegisub():
    def __init__(self, filename: str, is_HDR: bool = False, is_mid_black: bool = False):
        self.filename = filename
        self.raw_contents = []
        self.mode = Section.OTHERS
        self.xRatio = 1
        self.yRatio = 1
        self.is_HDR = is_HDR
        self.is_mid_black = is_mid_black
        self.hdr_brightness = HDR_SUB_BRIGHTNESS
        self.style_lines = None
        self.source_style_lines = {}
        self.effect_style_names = set()
        self.extra_style_lines = {}
        self.contents = dict(output_layout(is_HDR, False).outputs)
        self.notices = []
        self.notice_index = 0
        self.timeline_events = []
        self.style_alignments = {}
        self.source_is_bilingual = False
        self.source_is_chinese_only = False
        self.source_order = 'zh_first'
        self.cht_indexes = []
        self.fsvp_en = 12.0
        self.fsvp_en_lyrics = 12.0
        self.fsvp_note_double = 12.0
        self.glossary = []

    def add_notice(self, text: str, index: int = None):
        if index is None:
            index = self.notice_index
        self.notices.append(f'{index},{text}\n')

    def detect_source_type(self):
        total = 0
        cjk_only = 0
        latin_only = 0
        bilingual = 0
        zh_first = 0
        en_first = 0
        for line in self.raw_contents:
            if not match_events_dialogue.search(line):
                continue
            data = line.split(',', 9)
            if len(data) < 10:
                continue
            text = data[9]
            if has_special_effect(text):
                continue
            plain = match_override_tag.sub('', text)
            segments = [segment for segment in match_events_newline.split(plain) if segment.strip()]
            has_cjk = any(match_cjk.search(segment) for segment in segments)
            has_latin = any(match_latin.search(segment) for segment in segments)
            has_latin_only = any(
                match_latin.search(segment) and not match_cjk.search(segment)
                for segment in segments
            )
            if has_cjk and has_latin_only:
                bilingual += 1
            elif has_cjk:
                cjk_only += 1
            elif has_latin:
                latin_only += 1

            cjk_positions = [i for i, segment in enumerate(segments) if match_cjk.search(segment)]
            latin_only_positions = [
                i for i, segment in enumerate(segments)
                if match_latin.search(segment) and not match_cjk.search(segment)
            ]
            if cjk_positions and latin_only_positions:
                if min(cjk_positions) < min(latin_only_positions):
                    zh_first += 1
                else:
                    en_first += 1

            total += 1
        # 超过一半的对话事件中，中英文分属不同字幕行时，才按双语片源处理
        self.source_is_bilingual = total > 0 and bilingual * 2 >= total
        chinese_events = cjk_only + bilingual
        self.source_is_chinese_only = (
            not self.source_is_bilingual
            and chinese_events > latin_only
            and chinese_events > 0
        )
        self.source_order = 'zh_first' if zh_first >= en_first else 'en_first'

    def get_event_alignment(self, text: str, style_name: str, is_annotation: bool) -> int:
        matches = re.findall(r'\\an(\d)', text)
        if matches:
            return int(matches[-1])
        if is_annotation:
            return 8
        return self.style_alignments.get(style_name.lower(), 2)

    @staticmethod
    def timeline_pair_exempt(first: int, second: int) -> bool:
        # 底部对白与顶部注释/顶部字幕互相重叠或间隔不足时，不提示
        return ((first == 2 and second in (7, 8)) or
                (second == 2 and first in (7, 8)))

    def check_timeline_overlaps(self):
        events = sorted(self.timeline_events, key=lambda event: (event['start'], event['end']))
        active = []
        for event in events:
            active = [item for item in active if item['end'] > event['start']]
            for previous in active:
                if previous.get('special') or event.get('special'):
                    continue
                if not self.timeline_pair_exempt(previous['align'], event['align']):
                    self.notices.append(
                        f"{event['index']},与第{previous['index']}条字幕时间轴重叠,请处理\n"
                    )
                    break
            active.append(event)

    def check_timeline_gaps(self, min_gap_ms: int = MIN_EVENT_GAP_MS):
        events = sorted(self.timeline_events, key=lambda event: (event['start'], event['end']))
        previous = None
        previous_end = None
        for event in events:
            if (previous is not None
                    and not previous.get('special')
                    and not event.get('special')):
                gap = event['start'] - previous_end
                if (0 < gap < min_gap_ms
                        and not self.timeline_pair_exempt(
                            previous['align'], event['align'])):
                    self.notices.append(
                        f"{previous['index']},与下一条({event['index']})字幕"
                        f"间隔不足{min_gap_ms}ms,请处理\n"
                    )
            if previous_end is None or event['end'] > previous_end:
                previous_end = event['end']
                previous = event

    def set_xRatio(self, xRatio: float):
        self.xRatio = xRatio

    def set_yRatio(self, yRatio: float):
        self.yRatio = yRatio

    def read_file(self):
        with open(self.filename, 'rb') as f:
            raw = f.read()

        # 自动识别常见字幕编码：UTF-8 / UTF-16 / GB18030(GBK)
        if raw.startswith(b'\xef\xbb\xbf'):
            text = raw.decode('utf-8-sig')
        elif raw.startswith(b'\xff\xfe') or raw.startswith(b'\xfe\xff'):
            text = raw.decode('utf-16')
        elif b'\x00' in raw[:4]:
            # 无 BOM 的 UTF-16 LE / BE
            text = raw.decode('utf-16-le') if raw[0] != 0 else raw.decode('utf-16-be')
        else:
            try:
                text = raw.decode('utf-8')
            except UnicodeDecodeError:
                text = raw.decode('gb18030')

        text = text.replace('\r\n', '\n').replace('\r', '\n')
        is_srt = self.filename.lower().endswith('.srt')
        if is_srt and '[Script Info]' in text and '[Events]' in text:
            is_srt = False
        if not is_srt and '[Script Info]' not in text and '[Events]' not in text and '-->' in text:
            is_srt = True
        if is_srt:
            text = srt_to_ass(text)
        self.raw_contents = text.splitlines(keepends=True)

    def match_function_move(self, match) -> str:
        if match:
            return rf'\move({float(match.group(1))*self.xRatio:.3f},{float(match.group(2))*self.yRatio:.3f},{float(match.group(3))*self.xRatio:.3f},{float(match.group(4))*self.yRatio:.3f}'

    def match_function_pos(self, match) -> str:
        if match:
            return rf'\pos({float(match.group(1))*self.xRatio:.3f},{float(match.group(2))*self.yRatio:.3f}'

    def match_function_org(self, match) -> str:
        if match:
            return rf'\org({float(match.group(1))*self.xRatio:.3f},{float(match.group(2))*self.yRatio:.3f}'

    def match_function_iclip(self, match) -> str:
        if match:
            return rf'\iclip({float(match.group(1))*self.xRatio:.3f},{float(match.group(2))*self.yRatio:.3f},{float(match.group(3))*self.xRatio:.3f},{float(match.group(4))*self.yRatio:.3f}'

    def match_function_clip(self, match) -> str:
        if match:
            return rf'\clip({float(match.group(1))*self.xRatio:.3f},{float(match.group(2))*self.yRatio:.3f},{float(match.group(3))*self.xRatio:.3f},{float(match.group(4))*self.yRatio:.3f}'

    def match_function_fs(self, match) -> str:
        if match:
            return rf'\fs{round(int(match.group(1)) * self.yRatio)}'

def get_style_name(sub: Aegisub, kind: str) -> str:
    return kind

def get_styles(sub: Aegisub, style_lines=None) -> list:
    source = list(style_lines) if style_lines else (
        getattr(sub, 'style_lines', None) or SDR_STYLES)
    if sub.is_HDR:
        brightness = getattr(sub, 'hdr_brightness', HDR_SUB_BRIGHTNESS)
        styles = [hdrify_style_line(line, brightness) for line in source]
    else:
        styles = list(source)
    if not sub.is_mid_black:
        return styles.copy()

    result = []
    for line in styles:
        data = line.split(',')
        name = data[0].split(':', 1)[1].strip()
        if name in MID_BLACK_MARGINS:
            data[STYLE_MARGIN_V_FIELD] = str(MID_BLACK_MARGINS[name])
        result.append(','.join(data))

    return result

def output_style_lines(sub: Aegisub, style_lines=None) -> list:
    """输出用样式行：固定样式 + 特效字幕引用的原始样式。"""
    base = list(style_lines) if style_lines else (
        getattr(sub, 'style_lines', None) or SDR_STYLES)
    result = list(base)
    existing = {style_line_name(line).lower() for line in result}
    for name, line in getattr(sub, 'extra_style_lines', {}).items():
        if name in existing:
            continue
        result.append(line)
        existing.add(name)
    return result


def build_sdr_bilingual_lines(sub: Aegisub, lines: list) -> list:
    """把 HDR 双语内容转成 SDR 版：换回 SDR 样式；SDR/HDR 样式名一致。"""
    original = sub.is_HDR
    sub.is_HDR = False
    try:
        sdr_styles = get_styles(sub, output_style_lines(sub))
    finally:
        sub.is_HDR = original
    result = []
    styles_inserted = False
    for line in lines:
        if line.startswith('Style: '):
            if not styles_inserted:
                result.extend(f'{style}\n' for style in sdr_styles)
                styles_inserted = True
            continue
        result.append(line)
    return result

def get_script_info(meta: dict = None) -> str:
    meta = meta or {}

    def value(key: str) -> str:
        return str(meta.get(key, '') or '').strip()

    return (
        "[Script Info]\n"
        f"Title: {value('title')}\n"
        f"Original Script: {value('original_script')}\n"
        f"Original Translation: {value('original_translation')}\n"
        f"Original Timing: {value('original_timing')}\n"
        f"Original Editing: {value('original_editing')}\n"
        f"Script Updated By: {value('script_updated_by')}\n"
        "Update Details: \n"
        "ScriptType: v4.00+\n"
        "Collisions: Normal\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n"
        "Timer: 100.0000\n"
        "Synch Point: \n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: no\n"
        "YCbCr Matrix: TV.709\n"
        "\n"
    )

def parse_ass_time(value: str):
    """把 ASS 时间 H:MM:SS.cc 转为毫秒。"""
    match = re.match(r'^(\d+):(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?$', value.strip())
    if not match:
        return None
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    fraction = match.group(4) or ''
    if len(fraction) == 3:
        milliseconds = int(fraction)
    elif len(fraction) == 2:
        milliseconds = int(fraction) * 10
    elif len(fraction) == 1:
        milliseconds = int(fraction) * 100
    else:
        milliseconds = 0
    return ((hours * 3600 + minutes * 60 + seconds) * 1000) + milliseconds

LYRIC_MARKERS = re.compile(r'[∮♩♪♫♬*#＊＃]')
DIALOGUE_PREFIX_TAGS = re.compile(r'^((?:\{[^}]*\})*)')
HYPHEN_PLACEHOLDER = '\u0000'

# 英文行中的全角 ASCII 字符统一转半角
FULLWIDTH_ASCII_MAP = str.maketrans({
    '！': '!', '＂': '"', '＃': '#', '＄': '$', '％': '%', '＆': '&',
    '＇': "'", '（': '(', '）': ')', '＊': '*', '＋': '+', '，': ',',
    '－': '-', '．': '.', '／': '/', '：': ':', '；': ';', '＜': '<',
    '＝': '=', '＞': '>', '？': '?', '＠': '@', '［': '[', '＼': '\\',
    '］': ']', '＾': '^', '＿': '_', '｀': '`', '｛': '{', '｜': '|',
    '｝': '}', '～': '~',
    '０': '0', '１': '1', '２': '2', '３': '3', '４': '4',
    '５': '5', '６': '6', '７': '7', '８': '8', '９': '9',
    'Ａ': 'A', 'Ｂ': 'B', 'Ｃ': 'C', 'Ｄ': 'D', 'Ｅ': 'E', 'Ｆ': 'F',
    'Ｇ': 'G', 'Ｈ': 'H', 'Ｉ': 'I', 'Ｊ': 'J', 'Ｋ': 'K', 'Ｌ': 'L',
    'Ｍ': 'M', 'Ｎ': 'N', 'Ｏ': 'O', 'Ｐ': 'P', 'Ｑ': 'Q', 'Ｒ': 'R',
    'Ｓ': 'S', 'Ｔ': 'T', 'Ｕ': 'U', 'Ｖ': 'V', 'Ｗ': 'W', 'Ｘ': 'X',
    'Ｙ': 'Y', 'Ｚ': 'Z',
    'ａ': 'a', 'ｂ': 'b', 'ｃ': 'c', 'ｄ': 'd', 'ｅ': 'e', 'ｆ': 'f',
    'ｇ': 'g', 'ｈ': 'h', 'ｉ': 'i', 'ｊ': 'j', 'ｋ': 'k', 'ｌ': 'l',
    'ｍ': 'm', 'ｎ': 'n', 'ｏ': 'o', 'ｐ': 'p', 'ｑ': 'q', 'ｒ': 'r',
    'ｓ': 's', 'ｔ': 't', 'ｕ': 'u', 'ｖ': 'v', 'ｗ': 'w', 'ｘ': 'x',
    'ｙ': 'y', 'ｚ': 'z',
})

# 中文行只把全角数字、字母转半角，保留全角括号等规定符号
FULLWIDTH_ALNUM_MAP = str.maketrans(dict(
    [(chr(0xFF10 + i), chr(0x30 + i)) for i in range(10)] +
    [(chr(0xFF21 + i), chr(0x41 + i)) for i in range(26)] +
    [(chr(0xFF41 + i), chr(0x61 + i)) for i in range(26)]
))

def apply_outside_tags(text: str, func) -> str:
    """只处理花括号外的文字，保持 ASS 标签不变。"""
    parts = match_override_split.split(text)
    for index in range(0, len(parts), 2):
        parts[index] = func(parts[index])
    return ''.join(parts)

@lru_cache(maxsize=20000)
def convert_zh(text: str, target: str) -> str:
    """转换简繁；繁体输出把成对引号换成直角引号「」。"""
    text = zhconv.convert(text, target)
    if target == 'zh-hant':
        text = corner_brackets(text)
    return text

def convert_outside_tags(text: str, target: str) -> str:
    return apply_outside_tags(text, lambda part: convert_zh(part, target))

def is_word_char(char: str) -> bool:
    return bool(char) and (char.isalnum() or bool(match_cjk.match(char)))

def complete_quotes(text: str) -> str:
    """单条字幕中只有一个引号时，尝试补齐成对引号。"""
    positions = [index for index, char in enumerate(text) if char == '"']
    if len(positions) % 2 == 1:
        last = positions[-1]
        after = match_override_tag.sub('', text[last + 1:]).strip()
        if not after:
            # 引号在句尾，补一个开引号
            match = re.match(r'^((?:\{[^}]*\})*(?:\s*-\s*)?)', text)
            prefix = match.group(1) if match else ''
            text = prefix + '"' + text[len(prefix):]
        else:
            # 引号在句首或行中，补一个闭引号
            text = text.rstrip() + '"'
    return text

def format_quotes(text: str) -> str:
    """引号外侧加空格，引号内侧不留空格。"""
    text = complete_quotes(text)
    result = []
    inside = False
    index = 0
    length = len(text)

    while index < length:
        char = text[index]
        if char == '"':
            if not inside:
                if result and result[-1] != ' ' and is_word_char(result[-1]):
                    result.append(' ')
                result.append('"')
                inside = True
                index += 1
                while index < length and text[index] == ' ':
                    index += 1
                continue
            else:
                while result and result[-1] == ' ':
                    result.pop()
                result.append('"')
                inside = False
                if index + 1 < length and text[index + 1] != ' ' and is_word_char(text[index + 1]):
                    result.append(' ')
        else:
            result.append(char)
        index += 1

    return ''.join(result)

def corner_brackets(text: str) -> str:
    """把成对的半角引号换成直角引号，跳过样式标签内的内容。"""
    result = []
    opening = True
    in_tag = False
    for char in text:
        if char == '{':
            in_tag = True
        elif char == '}':
            in_tag = False
        if char == '"' and not in_tag:
            result.append('「' if opening else '」')
            opening = not opening
        else:
            result.append(char)
    return ''.join(result)

def format_dialogue_dashes(text: str) -> str:
    """区分对话符和英文复合词连字符，并统一对话符前后空格。"""
    # 先把各种横线统一为半角连字符
    text = text.replace('–', '-').replace('—', '-').replace('－', '-')
    # 行内连字符：free- will -> free-will
    text = re.sub(r'(?<=\S)-\s+', '-', text)
    # 前后都不是空格的连字符视为复合词/编号连字符，例如 twenty-three、6188-祖鲁
    text = re.sub(r'(?<=\S)-(?=\S|$)', HYPHEN_PLACEHOLDER, text)
    text = re.sub(r'\s*-\s*', ' - ', text)
    text = re.sub(r'^\s*-\s*', '- ', text)
    text = re.sub(r'^-\s+$', '-', text)
    return text.replace(HYPHEN_PLACEHOLDER, '-')

def normalize_zh_ellipsis(text: str) -> str:
    """中文省略号：只保留文字后的全角省略号。"""
    text = text.replace('……', '…')
    text = text.replace('···', '…')
    text = re.sub(r'\.{2,}', '…', text)
    text = re.sub(r'…{2,}', '…', text)
    text = text.replace('——', '…')
    text = re.sub(r'--+', '…', text)
    # 句首省略号
    text = re.sub(r'^(\s*-\s*)…+', r'\1', text)
    text = re.sub(r'^\s*…+', '', text)
    # 对话符后的省略号
    text = re.sub(r'(\s-\s*)…+', r'\1', text)
    # 句首引号、括号后的省略号
    text = re.sub(r'([「『"]\s*)…+', r'\1', text)
    text = re.sub(r'([（(]\s*)…+', r'\1', text)
    # 文字后的省略号，后面仍有文字时补一个空格
    text = re.sub(r'…(?=[^\s…])', '… ', text)
    return text

def process_zh(text: str, is_music: bool = False) -> str:
    def _process(part: str) -> str:
        # 参考 SrtEdit 中文标点替换表
        part = part.translate(FULLWIDTH_ALNUM_MAP)
        part = part.replace('﹟', ' ')
        part = part.replace('〝', '"').replace('〞', '"')
        part = part.replace('‥', '…')
        part = part.replace('－', '-')
        part = part.replace('\u200e', ' ')
        part = part.replace('\u200b', '').replace('\u200c', '')
        part = part.replace('\u200d', '').replace('\u2060', '').replace('\ufeff', '')

        if is_music:
            part = re.sub(r'[∮♩♪♫♬*#＊＃]', ' ', part)
        else:
            part = re.sub(r'[∮♩♪♫♬]', ' ', part)

        # 引号先统一成半角
        part = part.replace('“', '"').replace('”', '"')
        part = part.replace('「', '"').replace('」', '"')
        part = part.replace('『', '"').replace('』', '"').replace('＂', '"')
        part = part.replace('‘', "'").replace('’', "'").replace('＇', "'")
        part = part.replace('［', '[').replace('］', ']')
        part = part.replace('【', '[').replace('】', ']')
        part = part.replace('[', '（').replace(']', '）')

        part = normalize_zh_ellipsis(part)

        # 中文标点替换为一个空格（ASCII 句点保留，避免破坏数字小数）
        part = re.sub(r'[，。！？、：；．,!?:;<>＜＞]', ' ', part)
        part = part.replace('~', '…')
        part = re.sub(r'…{2,}', '…', part)

        # 省略号紧贴前面的文字（文字 … -> 文字…）
        part = re.sub(r'\s+…+', '…', part)

        # 间隔号用于人名，前后不加空格
        part = part.replace('•', '·').replace('・', '·').replace('･', '·')
        part = re.sub(r'\s*·\s*', '·', part)

        # 书名号、括号内侧不加空格
        part = re.sub(r'《\s*', '《', part)
        part = re.sub(r'\s*》', '》', part)
        part = re.sub(r'（\s*', '（', part)
        part = re.sub(r'\s*）', '）', part)

        part = format_quotes(part)
        part = format_dialogue_dashes(part)
        part = re.sub(r'\s+', ' ', part)
        return part.strip()

    return apply_outside_tags(text, _process)

def process_en_commas(text: str) -> str:
    result = []
    for index, char in enumerate(text):
        if index == 0 and char == ',':
            continue
        result.append(char)
        if char == ',' and index < len(text) - 1:
            next_char = text[index + 1]
            prev_char = text[index - 1]
            if next_char not in ' "':
                if prev_char.isdigit() and next_char.isdigit():
                    continue
                result.append(' ')
    return ''.join(result)

def wrap_lyrics(text: str) -> str:
    match = DIALOGUE_PREFIX_TAGS.match(text)
    prefix = match.group(1) if match else ''
    body = text[len(prefix):].strip()
    if not body:
        return text
    return prefix + '♪ ' + body + ' ♪'

def process_en(text: str, is_music: bool = False) -> str:
    def _process(part: str) -> str:
        # 先统一全角 ASCII，避免全角歌词符号漏处理
        part = part.translate(FULLWIDTH_ASCII_MAP)
        part = part.replace('>>', '')

        if is_music:
            part = re.sub(r'[∮♩♪♫♬*#]', ' ', part)
        else:
            part = re.sub(r'[∮♩♪♫♬]', ' ', part)

        # 全角句号转半角（其余全角 ASCII 已由上面的映射处理）
        part = part.replace('。', '.')
        part = part.replace('？', '?').replace('：', ':').replace('；', ';')
        part = part.replace('（', '(').replace('）', ')')
        part = part.replace('、', ',')
        part = part.replace('“', '"').replace('”', '"')
        part = part.replace('「', '"').replace('」', '"')
        part = part.replace('‘', "'").replace('’', "'")
        part = part.replace('《', '"').replace('》', '"')
        part = part.replace('【', '[').replace('】', ']')
        part = part.replace('–', '-').replace('—', '-')
        part = part.replace('～', '~')

        # 省略号统一为英文三点
        part = part.replace('……', '...').replace('…', '...')
        part = re.sub(r'\.{4,}', '...', part)
        part = re.sub(r'(?<!\.)\.\.(?!\.)', '...', part)
        part = part.replace('. . .', '...')
        part = part.replace('... ...', '...')
        part = part.replace('--', '...')
        part = part.replace('- -', '...')

        # 单词后的省略号紧贴单词，如 and ... -> and...
        part = re.sub(r'(?<=[A-Za-z0-9])\s+\.{3}', '...', part)

        # 删除句首省略号及对话符后的省略号
        part = re.sub(r'^(\s*-\s*)\.{3,}\s*', r'\1', part)
        part = re.sub(r'^\s*\.{3,}\s*', '', part)
        part = re.sub(r'(\s-\s*)\.{3,}\s*', r'\1', part)

        # 问号/感叹号后接省略号时保留空格
        part = re.sub(r'([?!])(\.{3})', r'\1 \2', part)

        # 问号、感叹号后补空格
        part = re.sub(r'([?!])(?=[^\s"\'\)\]\},.!?;:])', r'\1 ', part)

        # 句号后接大写字母时补空格
        part = re.sub(r'(?<=[a-z])\.(?=[A-Z])', '. ', part)

        part = process_en_commas(part)

        # 两个单引号视作双引号
        part = part.replace("''", '"')

        part = format_quotes(part)
        part = format_dialogue_dashes(part)

        # 单独的小写 i 修正为大写 I
        part = re.sub(r'\bi\b', 'I', part)

        part = re.sub(r'\s+', ' ', part)
        return part.strip()

    result = apply_outside_tags(text, _process)
    if is_music:
        result = wrap_lyrics(result)
    return result

def clean_segment(text: str) -> str:
    text = match_font_tag.sub('', text)
    text = match_reset_tag.sub('', text)
    text = match_font_size_tag.sub('', text)
    text = match_colour_tag.sub('', text)
    text = match_blur_tag.sub('', text)
    text = match_fsvp_tag.sub('', text)
    text = match_bold_tag.sub('', text)
    text = match_italic_tag.sub('', text)
    text = match_underline_tag.sub('', text)
    text = match_strike_tag.sub('', text)
    text = match_encoding_tag.sub('', text)
    text = match_empty_tag.sub('', text)
    return text.strip()

def count_dialogue_symbols(text: str) -> int:
    plain = plain_text(text)
    return len(re.findall(r'(^|\s)-(?=\s|$)', plain))

def starts_with_dialogue(text: str) -> bool:
    plain = plain_text(text)
    return plain.lstrip().startswith('- ')

def add_dialogue_prefix(text: str) -> str:
    match = DIALOGUE_PREFIX_TAGS.match(text)
    prefix = match.group(1) if match else ''
    rest = text[len(prefix):]
    if rest.startswith('- ') or rest == '-':
        return text
    return prefix + '- ' + rest

def fix_dialogue_pair(sub: Aegisub, zh: str, en: str):
    """检查中英文行对话符；单行出现对话符时至少应有 2 个。"""
    zh_count = count_dialogue_symbols(zh)
    en_count = count_dialogue_symbols(en)
    zh_start = starts_with_dialogue(zh)
    en_start = starts_with_dialogue(en)
    fixed = False

    # 先自动补齐句首对话符
    if zh_count == 1 and en_count == 1 and not zh_start and not en_start:
        zh = add_dialogue_prefix(zh)
        en = add_dialogue_prefix(en)
        fixed = True
    elif zh_count >= 2 and en_count == zh_count - 1 and en_count > 0 and not en_start:
        # 中文对话符齐全，英文只在句首少一个
        en = add_dialogue_prefix(en)
        fixed = True
    elif en_count >= 2 and zh_count == en_count - 1 and zh_count > 0 and not zh_start:
        # 英文对话符齐全，中文只在句首少一个
        zh = add_dialogue_prefix(zh)
        fixed = True

    if fixed:
        sub.add_notice('对话符缺失已修复,请确认')
    else:
        # 单行中只要有对话符，就必须至少 2 个（2 人及以上对话）
        if 0 < zh_count < 2 or 0 < en_count < 2:
            sub.add_notice('对话符缺失,请处理')
        elif zh_count == 0 and en_count > 0:
            sub.add_notice('对话符缺失,请处理')
        elif en_count == 0 and zh_count > 0:
            sub.add_notice('对话符缺失,请处理')
        elif zh_count != en_count:
            sub.add_notice('对话符数量不匹配,请处理')

    return zh, en

def plain_text(text: str) -> str:
    return match_override_tag.sub('', text).strip()

def build_dialogue(data: list, style: str, text: str, zero_margin: bool = True) -> str:
    result = data.copy()
    result[3] = style
    if zero_margin:
        result[7] = '0'
    result[9] = text
    return ','.join(result) + '\n'

def format_segments(segments: list, convert_to: str = None, fsvp: float = None) -> str:
    result = []
    for index, segment in enumerate(segments):
        text = segment['text']
        if convert_to:
            text = convert_zh(text, convert_to)
        # 句首不重复定义，换行后的行才设置行间距；每行都补回 \blur2
        if index == 0:
            text = '{\\blur2}' + text
        elif fsvp is not None and int(round(fsvp)) != 0:
            text = '{' + f'\\fsvp{int(round(fsvp))}' + '\\blur2}' + text
        else:
            text = '{\\blur2}' + text
        result.append(text)
    return '\\N'.join(result)

def format_bilingual(sub: Aegisub, segments: list, convert_zh_to: str = None,
                     fsvp: float = None) -> str:
    result = []
    current_style = None
    for index, segment in enumerate(segments):
        kind = 'zh_dialogue_bilingual' if segment['zh'] else 'en_dialogue_bilingual'
        style = get_style_name(sub, kind)
        text = segment['text']
        if convert_zh_to and segment['zh']:
            text = convert_zh(text, convert_zh_to)
        if index == 0:
            text = '{\\blur2}' + text
        else:
            tags = []
            # 换行后需要行间距；语言切换时同时切换样式
            if style != current_style:
                tags.append('\\r' + style)
            if fsvp is not None and int(round(fsvp)) != 0:
                tags.append(f'\\fsvp{int(round(fsvp))}')
            tags.append('\\blur2')
            text = '{' + ''.join(tags) + '}' + text
        result.append(text)
        current_style = style
    return '\\N'.join(result)

def format_lyrics_bilingual(sub: Aegisub, segments: list, convert_zh_to: str = None,
                            fsvp: float = None) -> str:
    result = []
    current_style = None
    for index, segment in enumerate(segments):
        kind = 'zh_lyrics_bilingual' if segment['zh'] else 'en_lyrics_bilingual'
        style = get_style_name(sub, kind)
        text = segment['text']
        if convert_zh_to and segment['zh']:
            text = convert_zh(text, convert_zh_to)
        if index == 0:
            text = '{\\blur2}' + text
        else:
            tags = []
            # 歌词双语：中文行用中文歌词样式，英文行用英文歌词样式
            if style != current_style:
                tags.append('\\r' + style)
            if fsvp is not None and int(round(fsvp)) != 0:
                tags.append(f'\\fsvp{int(round(fsvp))}')
            tags.append('\\blur2')
            text = '{' + ''.join(tags) + '}' + text
        result.append(text)
        current_style = style
    return '\\N'.join(result)

def get_special_effect(sub: Aegisub, data: str) -> str:
    # 处理move
    data = match_events_move.sub(sub.match_function_move, data)

    # 处理pos
    data = match_events_pos.sub(sub.match_function_pos, data)

    # 处理org
    data = match_events_org.sub(sub.match_function_org, data)

    # 处理iclip/clip
    data = match_events_iclip.sub(sub.match_function_iclip, data)
    data = match_events_clip.sub(sub.match_function_clip, data)

    return data

def fill_line(contents: dict, text: str):
    for key in contents:
        contents[key].append(text)


def fill_line_except_eng(contents: dict, text: str):
    """写入除英文以外的输出（注释行不进入英文文件）。"""
    for key in contents:
        if key != OUT_ENG:
            contents[key].append(text)


def check_glossary_consistency(sub, zh_segments: list, en_segments: list):
    """按术语表检查当前事件：原文出现时必须使用指定译文。"""
    if not sub.glossary or not en_segments:
        return
    zh_text = ' '.join(plain_text(segment['text']) for segment in zh_segments)
    en_text = ' '.join(plain_text(segment['text']) for segment in en_segments)
    zh_text_hans = convert_zh(zh_text, 'zh-hans') if zh_text else ''
    seen = set()
    for entry in sub.glossary:
        source = entry['source']
        target = entry['target']
        if not term_in_text(en_text, source, entry['case_sensitive']):
            continue
        if not zh_text:
            notice = '译文缺失,请处理'
        elif not (term_in_text(zh_text, target, True)
                  or term_in_text(zh_text_hans, target, True)):
            notice = '译文与术语表不符,请处理'
        else:
            continue
        if notice not in seen:
            seen.add(notice)
            sub.add_notice(notice)

def fill_dialogue(sub: Aegisub, contents: dict, line: str):
    data = line.split(',', 9)
    if len(data) < 10:
        for key in contents:
            contents[key].append(line)
        return
    sub.notice_index += 1

    start_ms = parse_ass_time(data[1])
    end_ms = parse_ass_time(data[2])

    if not data[9]:
        return
    if data[9].endswith('\n'):
        data[9] = data[9][:-1]
    text = data[9]
    special_effect = has_special_effect(text)
    if (not special_effect and start_ms is not None and end_ms is not None
            and end_ms - start_ms < MIN_EVENT_DURATION_MS):
        sub.add_notice(
            f'时间轴长度低于{MIN_EVENT_DURATION_MS}ms,请处理')
    style_name = data[3].strip()
    align_matches = re.findall(r'\\an(\d)', text)
    if align_matches:
        base_alignment = int(align_matches[-1])
    else:
        base_alignment = sub.style_alignments.get(style_name.lower(), 2)

    # 特效字幕不做规则套用，统一使用 Default 兜底样式
    if special_effect:
        if start_ms is not None and end_ms is not None:
            # 特效字幕不参与闪轴/重叠提示，但保留在时间轴上作为间隔分隔
            sub.timeline_events.append({
                'index': sub.notice_index,
                'start': start_ms,
                'end': end_ms,
                'align': base_alignment,
                'special': True,
            })
        sub.add_notice('疑似特效字幕,请确认')
        text = match_events_fs.sub(sub.match_function_fs, text)
        text = get_special_effect(sub, text)
        text = match_blur3_tag.sub('', text)
        text = match_empty_tag.sub('', text)
        # 特效字幕保留原样式：把源样式复制进输出样式表
        original_style = data[3].strip() or 'Default'
        source_style = sub.source_style_lines.get(original_style.lower())
        if source_style:
            sub.extra_style_lines[original_style.lower()] = source_style
        special_style = original_style
        result_chs = build_dialogue(
            data, special_style, convert_outside_tags(text, 'zh-hans'), zero_margin=False)
        result_cht = build_dialogue(
            data, special_style, convert_outside_tags(text, 'zh-hans'), zero_margin=False)
        contents[OUT_BILINGUAL].append(result_chs)
        contents[OUT_CHS].append(result_chs)
        sub.cht_indexes.append(sub.notice_index)
        contents[OUT_CHT].append(result_cht)
        return

    is_annotation = base_alignment == 8
    is_lyrics = base_alignment == 7
    raw_lines = match_events_newline.split(text)
    # 双语字幕条中，中英文任意一行含 ♪ 等歌词符号即按歌词检查
    has_lyric_marker = any(
        LYRIC_MARKERS.search(plain_text(raw)) for raw in raw_lines)
    is_music = has_lyric_marker
    segments = []
    for raw in raw_lines:
        segment = clean_segment(raw)
        if not segment:
            continue
        plain = plain_text(segment)
        if not plain:
            continue
        is_chinese = bool(match_cjk.search(plain))
        is_english = (not is_chinese) and bool(match_latin.search(plain))
        if is_chinese:
            segment = process_zh(segment, is_music)
        elif is_english:
            segment = process_en(segment, is_music)
        else:
            segment = process_zh(segment, is_music)
        plain = plain_text(segment)
        if not plain:
            continue
        segments.append({'text': segment, 'zh': not is_english, 'en': is_english})

    if not segments:
        return

    # 双语片源中，两行字幕固定为“中文行 + 英文行”。
    # 即使中文行未翻译、只剩英文/数字，也保持中文行的样式槽位。
    if sub.source_is_bilingual and len(segments) == 2:
        english_index = 1 if sub.source_order == 'zh_first' else 0
        if not match_cjk.search(plain_text(segments[english_index]['text'])):
            for index, segment in enumerate(segments):
                is_zh_slot = (index == 0) if sub.source_order == 'zh_first' else (index == 1)
                segment['zh'] = is_zh_slot
                segment['en'] = not is_zh_slot

    zh_segments = [segment for segment in segments if segment['zh']]
    en_segments = [segment for segment in segments if segment['en']]

    # 中文字幕行没有中文，只有数字/英文/连字符
    for segment in zh_segments:
        plain = plain_text(segment['text'])
        if not match_cjk.search(plain) and re.fullmatch(r'[0-9A-Za-z\-\s]+', plain):
            sub.add_notice('中文行无中文,请确认')
            break

    if len(segments) > 2:
        if base_alignment == 2:
            sub.add_notice('对白行数大于2,请处理')
        else:
            sub.add_notice('注释行数大于2,请处理')

    if sub.glossary and not is_annotation:
        check_glossary_consistency(sub, zh_segments, en_segments)

    if is_music:
        if zh_segments and en_segments and has_lyric_marker:
            sub.add_notice('疑似双语歌词,请检查')
        elif en_segments:
            sub.add_notice('疑似单英语歌词,请检查')
        else:
            sub.add_notice('疑似歌词,请检查')

    # 无 \an8 的双行纯中文字幕不再按注释处理，按中文单语对白输出并提示处理
    if (not is_annotation and not align_matches and base_alignment == 2
            and len(zh_segments) == 2 and not en_segments
            and all(match_cjk.search(plain_text(segment['text']))
                    for segment in zh_segments)):
        sub.add_notice('中文对白行数大于1,请处理')

    if start_ms is not None and end_ms is not None:
        alignment = sub.get_event_alignment(text, style_name, is_annotation)
        sub.timeline_events.append({
            'index': sub.notice_index,
            'start': start_ms,
            'end': end_ms,
            'align': alignment,
        })

    if is_annotation:
        sub.add_notice('疑似注释,请检查')
        is_double_note = len(segments) == 2
        note_kind = ('zh_note_double_line' if is_double_note
                     else 'zh_note_single_line')
        style = get_style_name(sub, note_kind)
        note_fsvp = sub.fsvp_note_double if is_double_note else None
        text_chs = format_segments(
            segments, convert_to='zh-hans', fsvp=note_fsvp)
        contents[OUT_BILINGUAL].append(build_dialogue(data, style, text_chs))
        contents[OUT_CHS].append(build_dialogue(data, style, text_chs))
        text_cht = format_segments(
            segments, convert_to='zh-hans', fsvp=note_fsvp)
        sub.cht_indexes.append(sub.notice_index)
        contents[OUT_CHT].append(build_dialogue(data, style, text_cht))
        return

    # 双语字幕中出现的单语行，提示人工检查
    if sub.source_is_bilingual and not is_music:
        if len(en_segments) > 1 and not zh_segments:
            sub.add_notice('疑似翻译缺失且英文行数大于1,请处理')
        elif len(en_segments) == 1 and not zh_segments:
            sub.add_notice('疑似翻译缺失,请处理')
        elif len(zh_segments) == 1 and not en_segments:
            sub.add_notice('疑似英文缺失,请处理')

    # 对话符检查：单行出现对话符时至少应有 2 个
    if not is_music:
        if len(zh_segments) == 1 and len(en_segments) == 1:
            zh_text, en_text = fix_dialogue_pair(sub, zh_segments[0]['text'], en_segments[0]['text'])
            for segment in segments:
                segment['text'] = zh_text if segment['zh'] else en_text
            zh_segments = [segment for segment in segments if segment['zh']]
            en_segments = [segment for segment in segments if segment['en']]
        else:
            if any(0 < count_dialogue_symbols(segment['text']) < 2 for segment in segments):
                sub.add_notice('对话符缺失,请处理')
            elif zh_segments and en_segments:
                zh_total = sum(count_dialogue_symbols(segment['text']) for segment in zh_segments)
                en_total = sum(count_dialogue_symbols(segment['text']) for segment in en_segments)
                if zh_total == 0 and en_total > 0:
                    sub.add_notice('对话符缺失,请处理')
                elif en_total == 0 and zh_total > 0:
                    sub.add_notice('对话符缺失,请处理')
                elif zh_total != en_total:
                    sub.add_notice('对话符数量不匹配,请处理')

    # 单行长度与中文行阿拉伯数字检查
    zh_len = max((len(plain_text(segment['text'])) for segment in zh_segments), default=0)
    en_len = max((len(plain_text(segment['text'])) for segment in en_segments), default=0)
    if zh_len > 28 or en_len > 84:
        if zh_len and en_len:
            sub.add_notice(f'单行过长，英文：{en_len}，中文：{zh_len},请处理')
        elif zh_len:
            sub.add_notice(f'单行过长，中文：{zh_len},请处理')
        else:
            sub.add_notice(f'单行过长，英文：{en_len},请处理')
    if any(re.search(r'\d', plain_text(segment['text'])) for segment in zh_segments):
        sub.add_notice('存在阿拉伯数字,请检查')

    if zh_segments and en_segments:
        # 双语：按原顺序输出，逐行切换中/英样式
        if is_lyrics:
            first_kind = ('zh_lyrics_bilingual' if segments[0]['zh']
                          else 'en_lyrics_bilingual')
            bilingual_style = get_style_name(sub, first_kind)
            bilingual_text = format_lyrics_bilingual(
                sub, segments, convert_zh_to='zh-hans',
                fsvp=sub.fsvp_en_lyrics)
        else:
            first_kind = ('zh_dialogue_bilingual' if segments[0]['zh']
                          else 'en_dialogue_bilingual')
            bilingual_style = get_style_name(sub, first_kind)
            bilingual_text = format_bilingual(
                sub, segments, convert_zh_to='zh-hans', fsvp=sub.fsvp_en)
        contents[OUT_BILINGUAL].append(build_dialogue(
            data, bilingual_style, bilingual_text))

        if is_lyrics:
            chinese_style = get_style_name(sub, 'zh_lyrics_monolingual')
            english_style = get_style_name(sub, 'en_lyrics_monolingual')
        else:
            chinese_style = get_style_name(sub, 'zh_dialogue_monolingual')
            english_style = get_style_name(sub, 'en_dialogue_monolingual')
        contents[OUT_CHS].append(build_dialogue(
            data, chinese_style, format_segments(zh_segments, convert_to='zh-hans')))
        sub.cht_indexes.append(sub.notice_index)
        contents[OUT_CHT].append(build_dialogue(
            data, chinese_style, format_segments(zh_segments, convert_to='zh-hans')))
        contents[OUT_ENG].append(build_dialogue(
            data, english_style, format_segments(en_segments)))
    elif zh_segments:
        style = get_style_name(
            sub,
            'zh_lyrics_monolingual' if is_lyrics
            else 'zh_dialogue_monolingual')
        text_chs = format_segments(zh_segments, convert_to='zh-hans')
        contents[OUT_BILINGUAL].append(build_dialogue(data, style, text_chs))
        contents[OUT_CHS].append(build_dialogue(data, style, text_chs))
        text_cht = format_segments(zh_segments, convert_to='zh-hans')
        sub.cht_indexes.append(sub.notice_index)
        contents[OUT_CHT].append(build_dialogue(data, style, text_cht))
    else:
        style = get_style_name(
            sub,
            'en_lyrics_monolingual' if is_lyrics
            else 'en_dialogue_monolingual')
        text_eng = format_segments(en_segments)
        contents[OUT_BILINGUAL].append(build_dialogue(data, style, text_eng))
        contents[OUT_ENG].append(build_dialogue(data, style, text_eng))


_ZHCONVERT_CACHE = {}
_ZHCONVERT_CACHE_LIMIT = 20000
_ZHCONVERT_CACHE_LOCK = threading.Lock()


def _cache_put(cache, key, value):
    if not key:
        return
    with _ZHCONVERT_CACHE_LOCK:
        cache[key] = value
        while len(cache) > _ZHCONVERT_CACHE_LIMIT:
            try:
                cache.pop(next(iter(cache)))
            except (StopIteration, KeyError):
                break


def zhconvert_api(texts, converter=ZHCONVERT_CONVERTER, cache=None,
                  progress=None, cancel_event=None):
    """批量调用繁化姬，返回与 texts 等长的转换结果；失败时抛出异常。

    cache 为「原文 -> 译文」字典，命中时不再发起请求；不传则使用会话级缓存。
    这样重复保存同一批字幕时不必重新转换。
    """
    if cache is None:
        cache = _ZHCONVERT_CACHE
    results = [None] * len(texts)
    pending = []
    for position, text in enumerate(texts):
        with _ZHCONVERT_CACHE_LOCK:
            cached = cache.get(text)
        if cached is None:
            pending.append(position)
        else:
            results[position] = cached

    total = len(pending)
    done = 0
    index = 0
    while index < total:
        if cancel_event is not None and cancel_event.is_set():
            raise ProcessCancelled('已取消繁化姬转换')
        group_positions = []
        group = []
        size = 0
        while index + len(group) < total:
            position = pending[index + len(group)]
            item = texts[position]
            if group and size + len(item) + 1 > ZHCONVERT_MAX_CHARS:
                break
            group.append(item)
            group_positions.append(position)
            size += len(item) + 1
        body = urllib.parse.urlencode({
            'converter': converter,
            'text': '\n'.join(group),
        }).encode('utf-8')
        request = urllib.request.Request(
            ZHCONVERT_API,
            data=body,
            headers={
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'User-Agent': 'ohMyAssNoto/1.0',
            },
        )
        last_error = None
        result = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=ZHCONVERT_TIMEOUT) as response:
                    result = json.loads(response.read().decode('utf-8'))
                break
            except (OSError, ValueError, KeyError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1.5)
        if result is None:
            raise last_error
        if not isinstance(result, dict):
            raise RuntimeError('繁化姬返回内容格式异常')
        if result.get('code') != 0:
            raise RuntimeError(result.get('msg') or '繁化姬返回错误')
        pieces = (result.get('data') or {}).get('text', '').split('\n')
        if len(pieces) != len(group):
            raise RuntimeError('繁化姬返回行数与提交行数不一致')
        for position, piece in zip(group_positions, pieces):
            results[position] = piece
            _cache_put(cache, texts[position], piece)
        index += len(group)
        done += len(group)
        if progress is not None and total:
            progress(int(done * 100 / total))
    return results


def split_dialogue_text(text: str):
    """按 \\N 和覆盖标签拆分文本，返回 (排版, 可转换的文字片段)。"""
    layout = []
    parts = []
    for piece in text.split('\\N'):
        chunks = match_override_split.split(piece)
        layout.append(chunks)
        parts.extend(chunks[0::2])
    return layout, parts


def join_dialogue_text(layout, values):
    iterator = iter(values)
    pieces = []
    for chunks in layout:
        rebuilt = list(chunks)
        for position in range(0, len(rebuilt), 2):
            rebuilt[position] = next(iterator)
        pieces.append(''.join(rebuilt))
    return '\\N'.join(pieces)


def convert_cht_contents(lines, indexes=None, use_api=True, cache=None,
                         progress=None, cancel_event=None):
    """把繁体输出转成台湾繁体。

    返回 (新行, 对比记录, 错误信息, 台湾化后超长行)。
    对比记录以普通繁体（本地简繁转换）为基准，与台湾化结果比较。
    cache 用于复用繁化姬结果，重复保存时避免重复请求。
    """
    entries = []
    all_parts = []
    for line_index, line in enumerate(lines):
        if not line.startswith('Dialogue:'):
            continue
        data = line.split(',', 9)
        if len(data) < 10:
            continue
        suffix = '\n' if data[9].endswith('\n') else ''
        text = data[9][:-1] if suffix else data[9]
        layout, parts = split_dialogue_text(text)
        entries.append({
            'index': line_index,
            'data': data,
            'layout': layout,
            'parts': parts,
            'suffix': suffix,
            'text': text,
        })
        all_parts.extend(parts)

    if cancel_event is not None and cancel_event.is_set():
        raise ProcessCancelled('已取消台湾化')
    # 普通繁体：本地简繁转换，作为对比基准
    local_parts = [zhconv.convert(part, 'zh-hant') for part in all_parts]
    taiwan_parts = local_parts
    error = ''
    if use_api and all_parts:
        try:
            taiwan_parts = zhconvert_api(
                all_parts, cache=cache, progress=progress,
                cancel_event=cancel_event)
        except ProcessCancelled:
            raise
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            error = f'{type(exc).__name__}: {exc}'
            log_exception('繁体台湾化', exc)

    result = list(lines)
    changes = []
    overlong = []
    cursor = 0
    for number, entry in enumerate(entries, 1):
        count = len(entry['parts'])
        taiwan_values = taiwan_parts[cursor:cursor + count]
        local_values = local_parts[cursor:cursor + count]
        cursor += count

        new_text = corner_brackets(join_dialogue_text(entry['layout'], taiwan_values))
        local_text = corner_brackets(join_dialogue_text(entry['layout'], local_values))
        data = list(entry['data'])
        data[9] = new_text + entry['suffix']
        result[entry['index']] = ','.join(data)

        before = plain_text(local_text).replace('\\N', ' / ')
        after = plain_text(new_text).replace('\\N', ' / ')
        if before != after:
            changes.append({
                'index': number,
                'time': entry['data'][1].strip(),
                'before': before,
                'after': after,
            })

        # 台湾化后重新做 28 字符检查；简体原文已超长的不重复提示
        simplified_pieces = entry['text'].split('\\N')
        for position, piece in enumerate(new_text.split('\\N')):
            source_piece = simplified_pieces[position] if position < len(simplified_pieces) else ''
            length = len(plain_text(piece))
            if length > 28 and len(plain_text(source_piece)) <= 28:
                event_index = None
                if indexes and number - 1 < len(indexes):
                    event_index = indexes[number - 1]
                overlong.append({
                    'index': event_index,
                    'time': entry['data'][1].strip(),
                    'length': length,
                })
    return result, changes, error, overlong
def _report_progress(progress, value, message=''):
    if progress is None:
        return
    value = max(0, min(100, int(value)))
    try:
        progress(value, message)
    except TypeError:
        progress(value)


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise ProcessCancelled('已取消生成')


def _apply_subtitle_options(sub, meta):
    """应用样式、术语、HDR 亮度与双语行距等生成选项。"""
    custom_styles = meta.get('style_lines')
    if isinstance(custom_styles, list):
        cleaned = []
        for line in custom_styles:
            if not (isinstance(line, str) and line.startswith('Style: ')
                    and line.count(',') >= 21):
                continue
            cleaned.append(line)
        if cleaned:
            unique_styles = {}
            for line in cleaned:
                unique_styles[style_line_name(line)] = line
            cleaned = list(unique_styles.values())
            existing_names = set(unique_styles)
            for default_line in SDR_STYLES:
                if style_line_name(default_line) not in existing_names:
                    cleaned.append(default_line)
            sub.style_lines = cleaned
    sub.glossary = normalize_glossary(meta.get('glossary'))
    try:
        sub.hdr_brightness = float(meta.get('hdr_brightness', HDR_SUB_BRIGHTNESS))
    except (TypeError, ValueError):
        sub.hdr_brightness = HDR_SUB_BRIGHTNESS
    fsvp_values = meta.get('style_fsvp') or {}
    try:
        sub.fsvp_en = float(fsvp_values.get(
            'en_dialogue_bilingual',
            fsvp_values.get('zh_dialogue_bilingual', 12.0)))
    except (TypeError, ValueError, AttributeError):
        sub.fsvp_en = 12.0
    try:
        sub.fsvp_en_lyrics = float(fsvp_values.get(
            'en_lyrics_bilingual',
            fsvp_values.get(
                'zh_lyrics_bilingual',
                fsvp_values.get('en_dialogue_bilingual', 12.0))))
    except (TypeError, ValueError, AttributeError):
        sub.fsvp_en_lyrics = 12.0
    try:
        sub.fsvp_note_double = float(fsvp_values.get(
            'zh_note_double_line',
            fsvp_values.get('zh_note_single_line', 12.0)))
    except (TypeError, ValueError, AttributeError):
        sub.fsvp_note_double = 12.0


def _collect_source_styles(sub):
    """先收集源样式与特效字幕使用的样式，保证写入样式表时就能包含它们。"""
    for raw in sub.raw_contents:
        stripped = raw.strip()
        if stripped.lower().startswith('style:'):
            style_parts = stripped.split(',')
            if len(style_parts) > 18:
                style_name = style_parts[0].split(':', 1)[1].strip()
                if style_name:
                    sub.source_style_lines[style_name.lower()] = stripped
        elif stripped.lower().startswith('dialogue:'):
            parts = stripped.split(',', 9)
            if len(parts) >= 10 and has_special_effect(parts[9]):
                style_name = parts[3].strip()
                if style_name:
                    sub.effect_style_names.add(style_name.lower())
    for style_name in sub.effect_style_names:
        source_line = sub.source_style_lines.get(style_name)
        if source_line:
            sub.extra_style_lines[style_name] = source_line


def _build_output_contents(sub, contents, meta, progress=None, cancel_event=None):
    """按 ASS 段落把源字幕转换成各语言的输出行。"""
    mode = Section.OTHERS
    script_info_written = False
    styles_written = False
    total = max(1, len(sub.raw_contents))

    for position, line in enumerate(sub.raw_contents):
        if position % 25 == 0:
            _check_cancel(cancel_event)
            _report_progress(
                progress, 8 + 47 * position / total,
                '正在转换字幕文本…')
        if match_paragraph_script_info.search(line):
            mode = Section.SCRIPT_INFO
            if not script_info_written:
                fill_line(contents, get_script_info(meta))
                script_info_written = True
        elif match_paragraph_styles.search(line):
            mode = Section.STYLES
            fill_line(contents, line)
        elif match_paragraph_events.search(line):
            if mode == Section.STYLES and not styles_written:
                for style_line in get_styles(sub, output_style_lines(sub)):
                    fill_line(contents, f'{style_line}\n')
                styles_written = True
            mode = Section.EVENTS
            fill_line(contents, line)
        elif match_paragraph_aegisub.search(line):
            mode = Section.AEGISUB
        else:
            if mode == Section.SCRIPT_INFO:
                # 只读取原始分辨率用于坐标缩放，不输出原始 Script Info
                match = match_script_info_play_x.search(line)
                if match:
                    sub.set_xRatio(1920 / int(match.group(1)))
                else:
                    match = match_script_info_play_y.search(line)
                    if match:
                        sub.set_yRatio(1080 / int(match.group(1)))
            elif mode == Section.STYLES:
                if line.lstrip().lower().startswith('style:'):
                    style_data = line.split(',')
                    if len(style_data) > 18:
                        style_name = style_data[0].split(':', 1)[1].strip()
                        try:
                            sub.style_alignments[style_name.lower()] = int(style_data[18])
                        except ValueError:
                            pass
                    continue
                if line == '\n':
                    if not styles_written:
                        for style_line in get_styles(sub, output_style_lines(sub)):
                            fill_line(contents, f'{style_line}\n')
                        styles_written = True
                    fill_line(contents, line)
                else:
                    fill_line(contents, line)
            elif mode == Section.EVENTS:
                stripped = line.lstrip()
                if stripped.lower().startswith('comment:'):
                    # 注释行（通常是中文说明）不进入英文文件
                    fill_line_except_eng(contents, line)
                elif match_events_dialogue.search(line):
                    fill_dialogue(sub, contents, line)
                else:
                    fill_line(contents, line)
            elif mode == Section.AEGISUB:
                continue
            else:
                fill_line(contents, line)

    if not script_info_written:
        for key in contents:
            contents[key].insert(0, get_script_info(meta))
    _report_progress(progress, 55, '文本转换完成')


def _apply_taiwan_conversion(sub, contents, meta, output_dir, filename, title,
                             progress=None, cancel_event=None):
    """繁体输出统一做台湾化：优先走繁化姬，失败时退回本地转换。"""
    use_taiwan = bool(meta.get('taiwan', True))
    taiwan_error = ''
    if not contents[OUT_CHT]:
        return taiwan_error
    _check_cancel(cancel_event)
    _report_progress(progress, 58, '正在做繁体台湾化…')
    contents[OUT_CHT], taiwan_changes, taiwan_error, taiwan_overlong = convert_cht_contents(
        contents[OUT_CHT], sub.cht_indexes, use_api=use_taiwan,
        progress=lambda value: _report_progress(progress, 58 + 17 * value / 100),
        cancel_event=cancel_event,
        cache=meta.get('cht_cache'),
    )
    for item in taiwan_overlong:
        if use_taiwan:
            sub.add_notice(f'台湾化后单行过长，中文：{item["length"]},请处理', index=item['index'])
    report_path = os.path.join(output_dir, TAIWAN_REPORT_NAME)
    if taiwan_error:
        sub.add_notice('繁化姬台湾化失败,请处理', index=0)
    # 对比报告属于分析产物，即使只重新分析（不写字幕）也要刷新
    if use_taiwan and (taiwan_changes or taiwan_error):
        write_text_with_fallback(
            report_path,
            build_taiwan_compare_html(title, filename, taiwan_changes, taiwan_error),
        )
    elif os.path.exists(report_path):
        try:
            os.remove(report_path)
        except OSError:
            pass
    _report_progress(progress, 76, '台湾化完成')
    return taiwan_error


def _write_output_files(sub, contents, output_dir, sdr_content, sdr_name,
                        progress=None, cancel_event=None):
    """写出各语言字幕与 SDR 版本，返回被占用后改名的文件列表。"""
    renamed_outputs = []
    for key in sub.contents:
        _check_cancel(cancel_event)
        value = sub.contents[key]
        if value not in contents:
            continue
        file = os.path.join(output_dir, key)
        saved = write_text_with_fallback(file, ''.join(contents[value]))
        if os.path.basename(saved) != os.path.basename(file):
            renamed_outputs.append((os.path.basename(file), os.path.basename(saved)))
    if sdr_content is not None:
        _check_cancel(cancel_event)
        file = os.path.join(output_dir, sdr_name)
        saved = write_text_with_fallback(
            file, ''.join(build_sdr_bilingual_lines(sub, sdr_content)))
        if os.path.basename(saved) != os.path.basename(file):
            renamed_outputs.append((os.path.basename(file), os.path.basename(saved)))
    _report_progress(progress, 92, '字幕文件已写出')
    return renamed_outputs


def _write_notice_files(sub, filename, title, source_label, output_dir, meta,
                        is_HDR, is_mid_black, updated_by):
    """写问题报告 HTML 与问题数据 JSON，返回 HTML 路径。"""
    sub.check_timeline_overlaps()
    sub.check_timeline_gaps()
    sub.notices.sort(key=lambda notice: int(notice.split(',', 1)[0]))

    html_file = write_text_with_fallback(
        os.path.join(output_dir, NOTICES_REPORT_NAME),
        build_notices_html(filename, title, sub.notices, source_label),
    )

    notices_data = []
    for notice in sub.notices:
        parts = notice.strip().split(',', 2)
        if len(parts) != 3:
            continue
        try:
            index = int(parts[0])
        except ValueError:
            continue
        notices_data.append({'index': index, 'problem': parts[1], 'need': parts[2]})
    payload = json.dumps({
        'version': 1,
        'source': os.path.abspath(filename),
        'source_label': source_label,
        'output_dir': os.path.abspath(output_dir),
        'title': title,
        'is_hdr': bool(is_HDR),
        'is_mid_black': bool(is_mid_black),
        'chinese_only': bool(sub.source_is_chinese_only),
        'meta': {
            'title': title,
            'script_updated_by': updated_by,
            'original_script': meta.get('original_script', ''),
            'original_translation': meta.get('original_translation', ''),
            'original_timing': meta.get('original_timing', ''),
            'original_editing': meta.get('original_editing', ''),
            'taiwan': bool(meta.get('taiwan', True)),
            'hdr_brightness': sub.hdr_brightness,
            'source_label': source_label,
            'style_fsvp': meta.get('style_fsvp', {}),
            'style_lines': list(sub.style_lines or SDR_STYLES),
            'glossary': list(sub.glossary),
        },
        'notices': notices_data,
    }, ensure_ascii=False, indent=2)
    try:
        atomic_write_text(
            os.path.join(output_dir, NOTICES_DATA_NAME), payload, encoding='utf-8')
    except OSError as exc:
        # 数据文件写不进去时报告页仍可用，但问题修复页无法自动载入
        log_exception('写入问题数据', exc)
    return html_file


def process_file(filename: str, is_HDR: bool, is_mid_black: bool, meta: dict = None,
                 write_outputs: bool = True, progress=None, cancel_event=None):
    meta = meta or {}
    _check_cancel(cancel_event)
    _report_progress(progress, 2, '正在读取字幕…')
    sub = Aegisub(filename, is_HDR, is_mid_black)
    _apply_subtitle_options(sub, meta)
    sub.read_file()
    sub.detect_source_type()
    _check_cancel(cancel_event)

    layout = output_layout(is_HDR, sub.source_is_chinese_only)
    sub.contents = dict(layout.outputs)

    title = str(meta.get('title', '') or '').strip()
    updated_by = str(meta.get('script_updated_by', '') or '').strip()
    source_label = str(meta.get('source_label', '') or '').strip() or os.path.basename(filename)
    if not title:
        raise ValueError('影片名为必填项，请填写后再生成')
    if not updated_by:
        raise ValueError('字幕员为必填项，请填写后再生成')

    output_dir = get_output_dir(filename, title, meta.get('output_root', ''))
    if output_dir is None:
        raise ValueError('影片名不能用于文件夹名，请修改')
    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as exc:
        raise ValueError(f'无法创建输出文件夹：{exc}')

    contents = {
        OUT_BILINGUAL: [],
        OUT_CHS: [],
        OUT_CHT: [],
        OUT_ENG: [],
    }
    _report_progress(progress, 5, '正在解析样式…')
    _collect_source_styles(sub)
    _build_output_contents(sub, contents, meta, progress, cancel_event)
    _check_cancel(cancel_event)

    _apply_taiwan_conversion(
        sub, contents, meta, output_dir, filename, title,
        progress, cancel_event)

    # HDR 时额外另存一份未做 HDR 颜色映射的 SDR 版本
    sdr_content = None
    sdr_name = ''
    if is_HDR:
        if sub.source_is_chinese_only:
            sdr_content = list(contents[OUT_CHS])
        else:
            sdr_content = list(contents[OUT_BILINGUAL])
        sdr_name = layout.sdr_name

    # HDR 时把行内颜色标签按 ssaHdrify 的映射一起转换
    if is_HDR:
        _check_cancel(cancel_event)
        _report_progress(progress, 78, '正在做 HDR 颜色映射…')
        for key in contents:
            contents[key] = [
                hdrify_event_colours(line, sub.hdr_brightness)
                if line.startswith('Dialogue:') else line
                for line in contents[key]
            ]

    if write_outputs:
        _check_cancel(cancel_event)
        _report_progress(progress, 82, '正在写出字幕文件…')
        renamed_outputs = _write_output_files(
            sub, contents, output_dir, sdr_content, sdr_name, progress, cancel_event)
        for expected, saved in renamed_outputs:
            sub.add_notice(
                f'输出文件被占用，{expected} 已另存为 {saved},请检查', index=0)

    _check_cancel(cancel_event)
    _report_progress(progress, 95, '正在生成问题报告…')
    html_file = _write_notice_files(
        sub, filename, title, source_label, output_dir, meta,
        is_HDR, is_mid_black, updated_by)
    _report_progress(progress, 100, '完成')
    return html_file, output_dir, sub.source_is_chinese_only

IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.webp')
BLACK_THRESHOLD = 15
BLACK_SAMPLE_COLUMNS = 960


def get_black_bar_type(scaled_bottom: float) -> str:
    if scaled_bottom <= 0:
        return '无黑边'
    if scaled_bottom <= 50:
        return '小黑边'
    if scaled_bottom <= 108:
        return '中黑边'
    if scaled_bottom < 150:
        return '大黑边'
    return '超大黑边'


def detect_black_bars(image_path: str, progress_callback=None) -> dict:
    """检测上下黑边，并按实际画面比例推算 1080p 等效黑边。

    截图可能已经裁掉黑边（例如 1920×804 的 2.39:1 画面），此时像素扫描
    结果为 0，但仍应按画面比例推算为 1080p 的 138px 黑边。因此黑边类型
    统一使用「实际画面高度 → 16:9 画框」的推算值，而不是只看检测到的
    黑边像素。
    """
    if Image is None:
        raise RuntimeError('黑边检测需要 Pillow')
    with Image.open(image_path) as source:
        width, height = source.size
        if not width or not height:
            raise ValueError('图片尺寸无效')
        red, green, blue = source.convert('RGB').split()
        # 取三分量最大值，等价于原来的 max(pixel)，但在 C 层完成
        bright = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        data = bright.tobytes()

    scale_factor = height / 1080.0
    step = max(1, width // BLACK_SAMPLE_COLUMNS)
    columns = list(range(0, width, step))
    total = len(columns)

    # 用字节表把“亮于阈值”标记成 1，再用 find/rfind 定位上下边缘
    mask = bytes(1 if value > BLACK_THRESHOLD else 0 for value in range(256))
    masked = data.translate(mask)
    last_row_offset = (height - 1) * width

    top_edges = []
    bottom_edges = []
    for index, x in enumerate(columns):
        if masked[x]:
            top = 0
        else:
            top = masked[x::width].find(b'\x01')
            if top < 0:
                top = height
        top_edges.append(top)

        if masked[last_row_offset + x]:
            bottom_edges.append(height - 1)
        else:
            bottom = masked[x::width].rfind(b'\x01')
            bottom_edges.append(0 if bottom < 0 else bottom)

        if progress_callback and (index % 32 == 0 or index == total - 1):
            progress_callback(index + 1, total)

    top_bar = Counter(top_edges).most_common(1)[0][0]
    common_bottom = Counter(bottom_edges).most_common(1)[0][0]
    bottom_bar = height - 1 - common_bottom

    # 边缘校验：只有最外侧一行确实接近纯黑，才把检测结果当成黑边。
    # 这样可以避免把画面顶部/底部的深色树林、夜景误认为黑边。
    edge_limit = max(1, int(width * 0.02))
    if top_bar > 0 and masked[0:width].count(1) > edge_limit:
        top_bar = 0
    if bottom_bar > 0 and masked[last_row_offset:last_row_offset + width].count(1) > edge_limit:
        bottom_bar = 0

    # 全黑图片或几乎全黑，按无黑边处理，避免推算失真
    if top_bar == height and common_bottom == 0:
        top_bar = 0
        bottom_bar = 0

    content_height = height - top_bar - bottom_bar
    if content_height < max(1, height // 20):
        top_bar = 0
        bottom_bar = 0
        content_height = height

    content_width = width
    aspect = content_width / content_height if content_height else width / height
    equivalent_height = 1920.0 / aspect if aspect else 0.0
    inferred_bottom = max(0.0, (1080.0 - equivalent_height) / 2.0)
    detected_bottom = bottom_bar / scale_factor if scale_factor else float(bottom_bar)

    return {
        'width': width,
        'height': height,
        'top': top_bar,
        'bottom': bottom_bar,
        'scale': scale_factor,
        'scaled_bottom': inferred_bottom,
        'detected_scaled_bottom': detected_bottom,
        'content_height': content_height,
        'aspect': aspect,
        'cropped': top_bar == 0 and bottom_bar == 0 and inferred_bottom > 1.0,
        'type': get_black_bar_type(inferred_bottom),
    }


def format_ass_time(ms: int) -> str:
    ms = max(0, int(round(ms)))
    hours = ms // 3600000
    minutes = ms % 3600000 // 60000
    seconds = ms % 60000 // 1000
    centis = (ms % 1000) // 10
    return f'{hours}:{minutes:02d}:{seconds:02d}.{centis:02d}'
