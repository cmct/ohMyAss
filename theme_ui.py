"""主题令牌、DPI、字体、图标与通用 Tk 控件。"""


import json
import os
import sys
import ctypes
import math
import tkinter as tk
from tkinter import ttk

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:
    Image = ImageDraw = ImageFilter = ImageFont = None
try:
    import winreg
except ImportError:
    winreg = None

from app_info import resource_path
from config_store import load_config


# WinUI 主题令牌（浅色 / 深色）
LIGHT_PALETTE = {
    'UI_BG': '#eef1f6',
    'UI_CARD': '#ffffff',
    'UI_BORDER': '#e3e3e3',
    'UI_TEXT': '#202938',
    'UI_MUTED': '#586477',
    'UI_ACCENT': '#2563eb',
    'UI_ACCENT_DARK': '#1d4ed8',
    'UI_SELECT': '#c7ddff',
    'UI_FIELD_BORDER': '#c9cfda',
    'UI_FIELD': '#fbfcfe',
    'UI_FIELD_SOFT': '#fafbfc',
    'UI_TREE_HEADER': '#f4f6fa',
    'UI_TROUGH': '#eceff4',
    'UI_ICON_ACTIVE_BG': '#e4e8ee',
    'UI_ICON_HOVER_BG': '#eceff4',
    'UI_TOOLTIP_BG': '#fffbe6',
    'UI_TOOLTIP_FG': '#243b53',
    'UI_SUCCESS': '#15803d',
    'UI_WARNING': '#b45309',
    'UI_CONFIRM': '#ca8a04',
    'UI_DANGER': '#b91c1c',
    'UI_PREVIEW_BG': '#d6dbe3',
}
DARK_PALETTE = {
    'UI_BG': '#12161c',
    'UI_CARD': '#1b2028',
    'UI_BORDER': '#2c333e',
    'UI_TEXT': '#e7ecf3',
    'UI_MUTED': '#9aa7b8',
    'UI_ACCENT': '#3b82f6',
    'UI_ACCENT_DARK': '#2563eb',
    'UI_SELECT': '#2b4570',
    'UI_FIELD_BORDER': '#3a4452',
    'UI_FIELD': '#151a21',
    'UI_FIELD_SOFT': '#1d232c',
    'UI_TREE_HEADER': '#232a34',
    'UI_TROUGH': '#2a323d',
    'UI_ICON_ACTIVE_BG': '#262e39',
    'UI_ICON_HOVER_BG': '#20262f',
    'UI_TOOLTIP_BG': '#2b333f',
    'UI_TOOLTIP_FG': '#e7ecf3',
    'UI_SUCCESS': '#4ade80',
    'UI_WARNING': '#fbbf24',
    'UI_CONFIRM': '#fde047',
    'UI_DANGER': '#f87171',
    'UI_PREVIEW_BG': '#d6dbe3',
}

UI_BG = LIGHT_PALETTE['UI_BG']
UI_CARD = LIGHT_PALETTE['UI_CARD']
UI_BORDER = LIGHT_PALETTE['UI_BORDER']
UI_TEXT = LIGHT_PALETTE['UI_TEXT']
UI_MUTED = LIGHT_PALETTE['UI_MUTED']
UI_ACCENT = LIGHT_PALETTE['UI_ACCENT']
UI_ACCENT_DARK = LIGHT_PALETTE['UI_ACCENT_DARK']
UI_SELECT = LIGHT_PALETTE['UI_SELECT']
UI_FIELD_BORDER = LIGHT_PALETTE['UI_FIELD_BORDER']
UI_FIELD = LIGHT_PALETTE['UI_FIELD']
UI_FIELD_SOFT = LIGHT_PALETTE['UI_FIELD_SOFT']
UI_TREE_HEADER = LIGHT_PALETTE['UI_TREE_HEADER']
UI_TROUGH = LIGHT_PALETTE['UI_TROUGH']
UI_ICON_ACTIVE_BG = LIGHT_PALETTE['UI_ICON_ACTIVE_BG']
UI_ICON_HOVER_BG = LIGHT_PALETTE['UI_ICON_HOVER_BG']
UI_TOOLTIP_BG = LIGHT_PALETTE['UI_TOOLTIP_BG']
UI_TOOLTIP_FG = LIGHT_PALETTE['UI_TOOLTIP_FG']
UI_SUCCESS = LIGHT_PALETTE['UI_SUCCESS']
UI_WARNING = LIGHT_PALETTE['UI_WARNING']
UI_CONFIRM = LIGHT_PALETTE['UI_CONFIRM']
UI_DANGER = LIGHT_PALETTE['UI_DANGER']
UI_PREVIEW_BG = LIGHT_PALETTE['UI_PREVIEW_BG']

# 主题相关但未纳入 UI_* 的固定色，切换时按对映射
THEME_LITERAL_PAIRS = (
    ('#f4f6fa', DARK_PALETTE['UI_TREE_HEADER']),
    ('#fafbfc', DARK_PALETTE['UI_FIELD_SOFT']),
    ('#f8fafc', DARK_PALETTE['UI_FIELD_SOFT']),
    ('#eceff4', DARK_PALETTE['UI_TROUGH']),
    ('#e5e9f0', DARK_PALETTE['UI_BORDER']),
    ('#e4e8ee', DARK_PALETTE['UI_ICON_ACTIVE_BG']),
    ('#98a2b3', DARK_PALETTE['UI_MUTED']),
    ('#243b53', DARK_PALETTE['UI_TEXT']),
    ('#d9dfe9', DARK_PALETTE['UI_TROUGH']),
)


# 拆分模块后，主题色与字体仍需同步到各 UI 模块的 globals()
_THEME_SYNC_NAMES = tuple(LIGHT_PALETTE) + (
    'CURRENT_DARK_THEME', 'UI_FONT', 'UI_DISPLAY_FONT',
    'UI_ICON_FONT', 'UI_TITLE_FONT',
)
_theme_namespaces = []


def _sync_theme_namespaces():
    for namespace in _theme_namespaces:
        for name in _THEME_SYNC_NAMES:
            if name in globals():
                namespace[name] = globals()[name]


def register_theme_namespace(namespace: dict):
    """注册需要跟随主题/字体变化的模块 globals()。"""
    if namespace not in _theme_namespaces:
        _theme_namespaces.append(namespace)
    _sync_theme_namespaces()


def apply_palette(dark: bool):
    global CURRENT_DARK_THEME
    CURRENT_DARK_THEME = bool(dark)
    palette = DARK_PALETTE if dark else LIGHT_PALETTE
    for name, value in palette.items():
        globals()[name] = value
    _sync_theme_namespaces()


def system_uses_dark_theme() -> bool:
    if winreg is None:
        return False
    key = None
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize')
        value, _ = winreg.QueryValueEx(key, 'AppsUseLightTheme')
        return int(value) == 0
    except (OSError, ValueError, TypeError):
        return False
    finally:
        if key is not None:
            try:
                winreg.CloseKey(key)
            except OSError:
                pass


def theme_mode_from_config(mode=None) -> str:
    if mode is None:
        mode = load_config().get('theme_mode', 'system')
    mode = str(mode or 'system').lower()
    return mode if mode in ('system', 'light', 'dark') else 'system'


def theme_is_dark(mode=None) -> bool:
    mode = theme_mode_from_config(mode)
    if mode == 'dark':
        return True
    if mode == 'light':
        return False
    return system_uses_dark_theme()


def theme_switch_map(old_dark: bool, new_dark: bool) -> dict:
    """返回旧主题颜色到新主题颜色的映射，用于已有控件重着色。"""
    old_palette = DARK_PALETTE if old_dark else LIGHT_PALETTE
    new_palette = DARK_PALETTE if new_dark else LIGHT_PALETTE
    mapping = {}
    for key, old_value in old_palette.items():
        new_value = new_palette[key]
        if old_value.lower() != new_value.lower():
            mapping[old_value.lower()] = new_value
    for light, dark in THEME_LITERAL_PAIRS:
        old_value = dark if old_dark else light
        new_value = dark if new_dark else light
        if old_value.lower() != new_value.lower():
            mapping[old_value.lower()] = new_value
    return mapping


def theme_fg_map(old_dark: bool, new_dark: bool) -> dict:
    old_palette = DARK_PALETTE if old_dark else LIGHT_PALETTE
    new_palette = DARK_PALETTE if new_dark else LIGHT_PALETTE
    keys = ('UI_TEXT', 'UI_MUTED', 'UI_ACCENT', 'UI_ACCENT_DARK',
            'UI_TOOLTIP_FG', 'UI_SUCCESS', 'UI_WARNING', 'UI_CONFIRM',
            'UI_DANGER')
    mapping = {}
    for key in keys:
        old_value = old_palette[key]
        new_value = new_palette[key]
        if old_value.lower() != new_value.lower():
            mapping[old_value.lower()] = new_value
    for literal, key in (('#243b53', 'UI_TEXT'), ('#98a2b3', 'UI_MUTED')):
        new_value = new_palette[key]
        if literal.lower() != new_value.lower():
            mapping[literal.lower()] = new_value
    return mapping


BG_OPTIONS = ('bg', 'background', 'activebackground', 'selectbackground', 'highlightcolor',
              'troughcolor', 'selectcolor', 'highlightbackground',
              'readonlybackground', 'disabledbackground')
FG_OPTIONS = ('fg', 'foreground', 'activeforeground', 'selectforeground',
              'insertbackground')


def recolor_widget_tree(widget, bg_map: dict, fg_map: dict):
    """把已有控件的浅色/深色主题色替换到目标主题。"""
    if isinstance(widget, RoundedFrame):
        widget.fill = bg_map.get(str(widget.fill).lower(), widget.fill)
        widget.border = bg_map.get(str(widget.border).lower(), widget.border)
    if isinstance(widget, ttk.Treeview):
        try:
            widget.tag_configure('process', foreground=UI_DANGER)
            widget.tag_configure('verify', foreground=UI_CONFIRM)
        except tk.TclError:
            pass
    try:
        for option in BG_OPTIONS:
            try:
                value = str(widget.cget(option))
            except tk.TclError:
                continue
            new_value = bg_map.get(value.lower())
            if new_value:
                widget.configure(**{option: new_value})
        for option in FG_OPTIONS:
            try:
                value = str(widget.cget(option))
            except tk.TclError:
                continue
            new_value = fg_map.get(value.lower())
            if new_value:
                widget.configure(**{option: new_value})
    except tk.TclError:
        pass
    if isinstance(widget, tk.Canvas):
        try:
            items = widget.find_all()
        except tk.TclError:
            items = []
        for item in items:
            try:
                kind = widget.type(item)
            except tk.TclError:
                continue
            for option in ('fill', 'outline'):
                try:
                    value = str(widget.itemcget(item, option))
                except tk.TclError:
                    continue
                mapping = fg_map if (kind == 'text' and option == 'fill') else bg_map
                new_value = mapping.get(value.lower())
                if new_value:
                    widget.itemconfigure(item, **{option: new_value})
    try:
        children = widget.winfo_children()
    except tk.TclError:
        children = []
    for child in children:
        recolor_widget_tree(child, bg_map, fg_map)
UI_FONT = 'Microsoft YaHei UI'
UI_DISPLAY_FONT = 'Microsoft YaHei UI'
UI_ICON_FONT = 'Segoe MDL2 Assets'
UI_TITLE_SIZE = 20
UI_BODY_SIZE = 10
UI_SMALL_SIZE = 9
UI_BUTTON_SIZE = 10
UI_MODULE_TITLE_SIZE = 12
UI_ICON_NAV_SIZE = 16
UI_ICON_CARD_SIZE = 20
UI_TITLE_FONT = (UI_DISPLAY_FONT, UI_TITLE_SIZE, 'bold')

UI_SCALE = 1.0
CURRENT_DARK_THEME = False


class _POINT(ctypes.Structure if sys.platform == 'win32' else object):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]


def _saved_window_point():
    """读取上次窗口位置，用于按所在显示器计算启动缩放。"""
    try:
        with open(os.path.join(os.path.expanduser('~'), 'ohMyAssNoto.json'),
                  'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return int(data['window_x']), int(data['window_y'])
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _monitor_dpi_handle(monitor):
    try:
        dpi_x = ctypes.c_uint()
        dpi_y = ctypes.c_uint()
        if ctypes.windll.shcore.GetDpiForMonitor(
                monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0:
            return int(dpi_x.value)
    except (OSError, AttributeError, ValueError):
        pass
    return 0


def monitor_dpi_for_point(x, y):
    if sys.platform != 'win32':
        return 0
    try:
        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.restype = ctypes.c_void_p
        monitor = user32.MonitorFromPoint(_POINT(int(x), int(y)), 2)
        return _monitor_dpi_handle(monitor)
    except (OSError, AttributeError, ValueError, TypeError):
        return 0


def monitor_dpi_for_window(hwnd):
    if sys.platform != 'win32' or not hwnd:
        return 0
    try:
        user32 = ctypes.windll.user32
        user32.MonitorFromWindow.restype = ctypes.c_void_p
        monitor = user32.MonitorFromWindow(int(hwnd), 2)
        return _monitor_dpi_handle(monitor)
    except (OSError, AttributeError, ValueError, TypeError):
        return 0


def enable_dpi_awareness():
    """开启 Windows DPI 感知并计算界面缩放比例。

    启动时优先按上次窗口所在的显示器计算缩放；运行时移动到不同缩放的
    显示器后，Tk 8.6 无法重新排布已创建控件，界面会提示重启。
    """
    global UI_SCALE
    if sys.platform != 'win32':
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (OSError, AttributeError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (OSError, AttributeError):
            pass
    dpi = 0
    point = _saved_window_point()
    if point is not None:
        dpi = monitor_dpi_for_point(*point)
    if not dpi:
        try:
            dpi = int(ctypes.windll.user32.GetDpiForSystem())
        except (OSError, AttributeError, ValueError):
            dpi = 96
    UI_SCALE = max(1.0, dpi / 96.0)
    try:
        screen_w = int(ctypes.windll.user32.GetSystemMetrics(0))
        screen_h = int(ctypes.windll.user32.GetSystemMetrics(1))
        if screen_w > 0 and screen_h > 0:
            UI_SCALE = min(
                UI_SCALE,
                max(1.0, (screen_h - 100) / 880.0),
                max(1.0, (screen_w - 80) / 1000.0))
    except (OSError, AttributeError, ValueError):
        # 拿不到屏幕尺寸时保留 DPI 推算的缩放比例
        pass


def px(value):
    return int(round(float(value) * UI_SCALE))


enable_dpi_awareness()

ICON_HOME = '\uE80F'
ICON_PENCIL = '\uE70F'
ICON_VIDEO = '\uE714'
ICON_TV = '\uE333'
ICON_FIX = '\uE90F'
ICON_SETTINGS = '\uE713'
ICON_GLOSSARY = '\uE8FD'


def register_bundled_fonts():
    """与 v0.2.13 一致：不内置字体，直接使用系统字体。"""
    return


def pick_ui_fonts():
    """与 v0.2.13 一致：使用系统 Microsoft YaHei UI。"""
    global UI_FONT, UI_DISPLAY_FONT, UI_TITLE_FONT, UI_ICON_FONT
    try:
        from tkinter import font as tkfont
        families = set(tkfont.families())
    except (tk.TclError, OSError):
        families = set()

    def first(candidates, fallback):
        for name in candidates:
            if name in families:
                return name
        return fallback

    UI_FONT = first(['Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI'],
                    'Microsoft YaHei UI')
    UI_DISPLAY_FONT = UI_FONT
    UI_ICON_FONT = first(['Segoe Fluent Icons', 'Segoe MDL2 Assets'], 'Segoe MDL2 Assets')
    UI_TITLE_FONT = (UI_DISPLAY_FONT, UI_TITLE_SIZE, 'bold')
    _sync_theme_namespaces()


def center_window(window, parent):
    """把对话框居中到主窗口位置。"""
    try:
        window.update_idletasks()
        parent = parent.winfo_toplevel()
        parent.update_idletasks()
        width = max(window.winfo_width(), window.winfo_reqwidth())
        height = max(window.winfo_height(), window.winfo_reqheight())
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        window.geometry(f'+{max(0, x)}+{max(0, y)}')
    except (tk.TclError, AttributeError):
        pass


class RoundedFrame(tk.Frame):
    """Canvas 绘制的圆角容器，内容放在 .body 中。"""

    def __init__(self, parent, radius=8, bg=None, fill=None,
                 border=None, inset=4):
        # 颜色不能在默认参数里取：那是导入时的浅色值，懒加载页面会用到旧颜色
        bg = UI_BG if bg is None else bg
        fill = UI_CARD if fill is None else fill
        border = UI_BORDER if border is None else border
        super().__init__(parent, bg=bg)
        self.radius = px(radius)
        self.fill = fill
        self.border = border
        self.inset = px(inset)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack(fill='both', expand=True)
        self.body = tk.Frame(self.canvas, bg=fill)
        self._window = self.canvas.create_window(inset, inset, anchor='nw', window=self.body)
        self._resize_pending = False
        self.body.bind('<Configure>', self._on_body_configure)
        self.canvas.bind('<Configure>', self._on_canvas_configure)
        self.after_idle(self._sync_height)

    def _on_body_configure(self, event=None):
        if self._resize_pending:
            return
        self._resize_pending = True
        self.after_idle(self._sync_height)

    def _sync_height(self):
        self._resize_pending = False
        try:
            height = self.body.winfo_reqheight() + self.inset * 2
        except tk.TclError:
            return
        if height > 0 and abs(self.canvas.winfo_reqheight() - height) > 1:
            self.canvas.configure(height=height)

    def _on_canvas_configure(self, event):
        width = max(1, event.width)
        height = max(1, event.height)
        self.canvas.coords(self._window, self.inset, self.inset)
        self.canvas.itemconfigure(
            self._window,
            width=max(1, width - self.inset * 2),
            height=max(1, height - self.inset * 2),
        )
        self._draw(width, height)

    def _draw(self, width, height):
        self.canvas.delete('frame_shape')
        radius = min(self.radius, max(1, width // 2), max(1, height // 2))
        x1, y1 = 0, 0
        x2, y2 = width - 1, height - 1
        fill, border = self.fill, self.border

        self.canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2,
                                     fill=fill, outline='', tags='frame_shape')
        self.canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius,
                                     fill=fill, outline='', tags='frame_shape')
        self.canvas.create_arc(x1, y1, x1 + 2 * radius, y1 + 2 * radius,
                               start=90, extent=90, style='pieslice',
                               fill=fill, outline='', tags='frame_shape')
        self.canvas.create_arc(x2 - 2 * radius, y1, x2, y1 + 2 * radius,
                               start=0, extent=90, style='pieslice',
                               fill=fill, outline='', tags='frame_shape')
        self.canvas.create_arc(x1, y2 - 2 * radius, x1 + 2 * radius, y2,
                               start=180, extent=90, style='pieslice',
                               fill=fill, outline='', tags='frame_shape')
        self.canvas.create_arc(x2 - 2 * radius, y2 - 2 * radius, x2, y2,
                               start=270, extent=90, style='pieslice',
                               fill=fill, outline='', tags='frame_shape')

        self.canvas.create_line(x1 + radius, y1, x2 - radius, y1,
                                fill=border, tags='frame_shape')
        self.canvas.create_line(x2, y1 + radius, x2, y2 - radius,
                                fill=border, tags='frame_shape')
        self.canvas.create_line(x1 + radius, y2, x2 - radius, y2,
                                fill=border, tags='frame_shape')
        self.canvas.create_line(x1, y1 + radius, x1, y2 - radius,
                                fill=border, tags='frame_shape')
        for box, start in (
            ((x1, y1, x1 + 2 * radius, y1 + 2 * radius), 90),
            ((x2 - 2 * radius, y1, x2, y1 + 2 * radius), 0),
            ((x1, y2 - 2 * radius, x1 + 2 * radius, y2), 180),
            ((x2 - 2 * radius, y2 - 2 * radius, x2, y2), 270),
        ):
            self.canvas.create_arc(*box, start=start, extent=90, style='arc',
                                   outline=border, tags='frame_shape')
        self.canvas.tag_lower('frame_shape')


def load_app_icon(size: int = 72):
    try:
        image = tk.PhotoImage(file=resource_path('ohMyAss Noto.png'))
        factor = max(1, image.width() // size)
        if factor > 1:
            image = image.subsample(factor, factor)
        return image
    except (OSError, tk.TclError):
        # 缺少展示图时退回纯文字界面
        return None


def rounded_rect(canvas, x1, y1, x2, y2, radius, color, tag=''):
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1,
        x2, y1 + radius, x2, y2 - radius, x2, y2,
        x2 - radius, y2, x1 + radius, y2, x1, y2,
        x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    canvas.create_polygon(points, smooth=True, splinesteps=24, fill=color, outline='', tags=tag)


def _normalize_icon(image, size: int, margin: float = 0.04, stretch: bool = False):
    """把图标裁到内容边界后居中放入同尺寸正方形画布。"""
    if image is None:
        return None
    bbox = image.getbbox()
    if not bbox:
        return image.resize((size, size), Image.LANCZOS)
    content = image.crop(bbox)
    target = max(1, round(size * (1 - margin * 2)))
    if stretch:
        new_size = (target, target)
    else:
        side = max(content.size)
        scale = target / side
        new_size = (
            max(1, round(content.width * scale)),
            max(1, round(content.height * scale)),
        )
    content = content.resize(new_size, Image.LANCZOS)
    canvas = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    canvas.paste(content, ((size - new_size[0]) // 2, (size - new_size[1]) // 2), content)
    return canvas


def render_camera_icon(size: int = 34, color=(32, 41, 56), weight: float = 1.0):
    """用 PIL 生成抗锯齿的简笔画照相机图标。"""
    if Image is None or ImageDraw is None:
        return None
    scale = 4
    canvas = size * scale
    unit = canvas / 40.0
    image = Image.new('RGBA', (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    line_color = color + (255,)
    width = max(2, round(2.05 * unit * weight))

    def point(x, y):
        return (x * unit, y * unit)

    def arc(cx, cy, radius, start, end, steps=8):
        values = []
        for index in range(steps + 1):
            angle = math.radians(start + (end - start) * index / steps)
            values.append(point(cx + radius * math.cos(angle),
                                cy + radius * math.sin(angle)))
        return values

    radius = 4
    outline = [point(5, 17)]
    outline += [point(5, 29)]
    outline += arc(9, 29, radius, 180, 90)
    outline += [point(31, 33)]
    outline += arc(31, 29, radius, 90, 0)
    outline += [point(35, 17)]
    outline += arc(31, 17, radius, 0, -90)
    outline += [point(25, 13)]
    outline += [point(25, 11)]
    outline += arc(22.5, 11, 2.5, 0, -90)
    outline += [point(17.5, 8.5)]
    outline += arc(17.5, 11, 2.5, -90, -180)
    outline += [point(15, 13)]
    outline += [point(9, 13)]
    outline += arc(9, 17, radius, -90, -180)
    outline.append(outline[0])
    draw.line(outline, fill=line_color, width=width, joint='curve')

    lens = 6.5
    left, top = point(20 - lens, 23.5 - lens)
    right, bottom = point(20 + lens, 23.5 + lens)
    draw.ellipse((left, top, right, bottom), outline=line_color, width=width)

    return _normalize_icon(image, size)


def render_tv_icon(size: int = 34, color=(32, 41, 56), weight: float = 1.0):
    """用 PIL 生成抗锯齿的简笔画小电视图标。"""
    if Image is None or ImageDraw is None:
        return None
    scale = 4
    canvas = size * scale
    unit = canvas / 40.0
    image = Image.new('RGBA', (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    line_color = color + (255,)
    width = max(2, round(2.05 * unit * weight))

    def point(x, y):
        return (x * unit, y * unit)

    # 电视外框
    left, top = point(5, 9)
    right, bottom = point(35, 29)
    draw.rounded_rectangle(
        (left, top, right, bottom), radius=3 * unit,
        outline=line_color, width=width)
    # 屏幕内框，让图标更像电视
    left, top = point(9, 13)
    right, bottom = point(31, 25)
    draw.rounded_rectangle(
        (left, top, right, bottom), radius=1.5 * unit,
        outline=line_color, width=max(1, round(width * 0.7)))
    # 底座
    draw.line([point(20, 29), point(20, 33)],
              fill=line_color, width=width)
    draw.line([point(14, 34), point(26, 34)],
              fill=line_color, width=width, joint='curve')
    return _normalize_icon(image, size)


def render_gear_icon(size: int = 34, color=(32, 41, 56), weight: float = 1.0):
    """用 PIL 生成抗锯齿的简笔画齿轮图标。"""
    if Image is None or ImageDraw is None:
        return None
    scale = 5
    canvas = size * scale
    unit = canvas / 40.0
    image = Image.new('RGBA', (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    line_color = color + (255,)
    width = max(2, round(2.0 * unit * weight))

    center = 20.0
    teeth = 6
    root_radius = 10.4
    tip_radius = 14.6
    step = 360 / teeth
    base = []
    for index in range(teeth):
        angle = -90 + index * step
        for offset, radius in (
            (angle - 19, root_radius),
            (angle - 11, tip_radius),
            (angle + 11, tip_radius),
            (angle + 19, root_radius),
        ):
            radians = math.radians(offset)
            base.append((
                center + radius * math.cos(radians),
                center + radius * math.sin(radians),
            ))

    def round_path(points, radius, steps=6):
        result = []
        count = len(points)
        for index in range(count):
            previous = points[index - 1]
            point = points[index]
            following = points[(index + 1) % count]
            v1 = (previous[0] - point[0], previous[1] - point[1])
            v2 = (following[0] - point[0], following[1] - point[1])
            l1 = math.hypot(*v1) or 1
            l2 = math.hypot(*v2) or 1
            r = min(radius, l1 / 2, l2 / 2)
            start = (point[0] + v1[0] / l1 * r, point[1] + v1[1] / l1 * r)
            end = (point[0] + v2[0] / l2 * r, point[1] + v2[1] / l2 * r)
            for step_index in range(steps + 1):
                t = step_index / steps
                result.append((
                    (1 - t) ** 2 * start[0] + 2 * (1 - t) * t * point[0] + t ** 2 * end[0],
                    (1 - t) ** 2 * start[1] + 2 * (1 - t) * t * point[1] + t ** 2 * end[1],
                ))
        return result

    outline = round_path(base, 0.8)
    outline.append(outline[0])
    scaled = [(x * unit, y * unit) for x, y in outline]
    draw.line(scaled, fill=line_color, width=width, joint='curve')
    cap = width / 2
    for x, y in scaled:
        draw.ellipse((x - cap, y - cap, x + cap, y + cap), fill=line_color)

    inner = 5.0
    draw.ellipse((
        (center - inner) * unit, (center - inner) * unit,
        (center + inner) * unit, (center + inner) * unit,
    ), outline=line_color, width=width)

    return _normalize_icon(image, size)


def render_pencil_icon(size: int = 34, color=(32, 41, 56), weight: float = 1.0):
    """用 PIL 生成抗锯齿的简笔画铅笔图标。"""
    if Image is None or ImageDraw is None:
        return None
    scale = 5
    canvas = size * scale
    unit = canvas / 40.0
    image = Image.new('RGBA', (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    line_color = color + (255,)
    width = max(2, round(1.55 * unit * weight))
    cap = width / 2

    angle = math.radians(-45)
    cos_a, sin_a = math.cos(angle), math.sin(angle)

    def rotate(x, y):
        return (x * cos_a - y * sin_a, x * sin_a + y * cos_a)

    local = [(0, 0), (7, -3.5), (29, -3.5)]
    for step in range(1, 9):
        angle = math.radians(-90 + 180 * step / 8)
        local.append((29 + 3.5 * math.cos(angle), 3.5 * math.sin(angle)))
    local.append((7, 3.5))
    rotated = [rotate(x, y) for x, y in local]
    min_x = min(point[0] for point in rotated)
    max_x = max(point[0] for point in rotated)
    min_y = min(point[1] for point in rotated)
    max_y = max(point[1] for point in rotated)
    offset_x = 20 - (min_x + max_x) / 2
    offset_y = 20 - (min_y + max_y) / 2
    rotated = [(x + offset_x, y + offset_y) for x, y in rotated]

    def round_path(points, radius, steps=6):
        result = []
        count = len(points)
        for index in range(count):
            previous = points[index - 1]
            point = points[index]
            following = points[(index + 1) % count]
            v1 = (previous[0] - point[0], previous[1] - point[1])
            v2 = (following[0] - point[0], following[1] - point[1])
            l1 = math.hypot(*v1) or 1
            l2 = math.hypot(*v2) or 1
            r = min(radius, l1 / 2, l2 / 2)
            start = (point[0] + v1[0] / l1 * r, point[1] + v1[1] / l1 * r)
            end = (point[0] + v2[0] / l2 * r, point[1] + v2[1] / l2 * r)
            for step_index in range(steps + 1):
                t = step_index / steps
                result.append((
                    (1 - t) ** 2 * start[0] + 2 * (1 - t) * t * point[0] + t ** 2 * end[0],
                    (1 - t) ** 2 * start[1] + 2 * (1 - t) * t * point[1] + t ** 2 * end[1],
                ))
        return result

    outline = round_path(rotated, 1.0)
    scaled = [(x * unit, y * unit) for x, y in outline]
    scaled.append(scaled[0])
    draw.line(scaled, fill=line_color, width=width, joint='curve')
    for x, y in scaled:
        draw.ellipse((x - cap, y - cap, x + cap, y + cap), fill=line_color)

    band_a = rotate(26, -3.5)
    band_b = rotate(26, 3.5)
    band_a = ((band_a[0] + offset_x) * unit, (band_a[1] + offset_y) * unit)
    band_b = ((band_b[0] + offset_x) * unit, (band_b[1] + offset_y) * unit)
    draw.line([band_a, band_b], fill=line_color, width=width)
    for x, y in (band_a, band_b):
        draw.ellipse((x - cap, y - cap, x + cap, y + cap), fill=line_color)

    return _normalize_icon(image, size)


def render_house_icon(size: int = 34, color=(32, 41, 56), weight: float = 1.0):
    """用 PIL 生成抗锯齿的简笔画房屋图标。"""
    if Image is None or ImageDraw is None:
        return None
    scale = 5
    canvas = size * scale
    unit = canvas / 40.0
    image = Image.new('RGBA', (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    line_color = color + (255,)
    width = max(2, round(1.7 * unit * weight))
    cap = width / 2

    points = [
        (7, 19), (20, 7), (33, 19), (33, 31),
        (24, 31), (24, 26), (16, 26), (16, 31),
        (7, 31),
    ]

    def round_path(pts, radius, steps=6):
        result = []
        count = len(pts)
        for index in range(count):
            previous = pts[index - 1]
            point = pts[index]
            following = pts[(index + 1) % count]
            v1 = (previous[0] - point[0], previous[1] - point[1])
            v2 = (following[0] - point[0], following[1] - point[1])
            l1 = math.hypot(*v1) or 1
            l2 = math.hypot(*v2) or 1
            r = min(radius, l1 / 2, l2 / 2)
            start = (point[0] + v1[0] / l1 * r, point[1] + v1[1] / l1 * r)
            end = (point[0] + v2[0] / l2 * r, point[1] + v2[1] / l2 * r)
            for step_index in range(steps + 1):
                t = step_index / steps
                result.append((
                    (1 - t) ** 2 * start[0] + 2 * (1 - t) * t * point[0] + t ** 2 * end[0],
                    (1 - t) ** 2 * start[1] + 2 * (1 - t) * t * point[1] + t ** 2 * end[1],
                ))
        return result

    outline = round_path(points, 2.2)
    scaled = [(x * unit, y * unit) for x, y in outline]
    scaled.append(scaled[0])
    draw.line(scaled, fill=line_color, width=width, joint='curve')
    for x, y in scaled:
        draw.ellipse((x - cap, y - cap, x + cap, y + cap), fill=line_color)

    return _normalize_icon(image, size, stretch=True)


def render_wrench_icon(size: int = 34, color=(32, 41, 56), weight: float = 1.0):
    """从 Segoe MDL2 字形生成原版扳手图标（抗锯齿）。"""
    if Image is None or ImageDraw is None or ImageFont is None:
        return None
    font_path = os.path.join(os.environ.get('WINDIR', r'C:\Windows'),
                             'Fonts', 'segmdl2.ttf')
    scale = 4
    canvas = size * scale
    try:
        font = ImageFont.truetype(font_path, round(canvas * 0.9))
    except OSError:
        return None
    image = Image.new('RGBA', (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    glyph = chr(0xE90F)
    left, top, right, bottom = draw.textbbox((0, 0), glyph, font=font)
    width = right - left
    height = bottom - top
    position = ((canvas - width) / 2 - left, (canvas - height) / 2 - top)
    draw.text(position, glyph, font=font, fill=color + (255,))
    if weight > 1.05 and ImageFilter is not None:
        image = image.filter(ImageFilter.MaxFilter(3))
    elif weight < 0.95 and ImageFilter is not None:
        image = image.filter(ImageFilter.MinFilter(3))
    return _normalize_icon(image, size)


def hex_to_rgb(value: str):
    value = value.lstrip('#')
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


ICON_RENDERERS = {
    'home': render_house_icon,
    'pencil': render_pencil_icon,
    'fix': render_wrench_icon,
    'frame': render_camera_icon,
    'tv': render_tv_icon,
    'settings': render_gear_icon,
}


def create_card(parent, title: str = ''):
    card = RoundedFrame(parent, radius=8)
    if title:
        tk.Label(
            card.body, text=title, bg=UI_CARD, fg=UI_TEXT,
            font=(UI_DISPLAY_FONT, UI_BODY_SIZE, 'bold'),
        ).pack(anchor='w', padx=px(16), pady=(px(8), 0))
    body = tk.Frame(card.body, bg=UI_CARD)
    body.pack(fill='both', expand=True, padx=px(16), pady=(px(5), px(8)))
    return card, body


def create_hero(parent, title: str, subtitle: str, icon_size: int = 120):
    card = RoundedFrame(parent, radius=12)
    inner = tk.Frame(card.body, bg=UI_CARD)
    inner.pack(fill='x', padx=px(24), pady=px(7))
    left = tk.Frame(inner, bg=UI_CARD)
    left.pack(side='left', fill='x', expand=True)
    tk.Label(
        left, text=title, bg=UI_CARD, fg=UI_TEXT,
        font=UI_TITLE_FONT,
    ).pack(anchor='w')
    tk.Label(
        left, text=subtitle, bg=UI_CARD, fg=UI_MUTED, font=(UI_FONT, UI_BODY_SIZE),
    ).pack(anchor='w', pady=(px(6), 0))
    icon = load_app_icon(px(icon_size))
    if icon is not None:
        label = tk.Label(inner, image=icon, bg=UI_CARD)
        label.image = icon
        label.pack(side='right', padx=(px(16), 0))
    return card


def add_combo_arrow(combo, arrow_width: int = 13):
    """给 Combobox 覆盖一个单线分隔的自绘下拉箭头（不遮挡原生边框）。"""
    arrow_width = px(arrow_width)
    bar = tk.Frame(combo, bg=UI_FIELD_BORDER, width=1, bd=0, highlightthickness=0)
    right_bar = tk.Frame(combo, bg=UI_FIELD_BORDER, width=1, bd=0, highlightthickness=0)
    arrow = tk.Label(
        combo, text='\u25BE', bg=UI_FIELD, fg=UI_MUTED,
        font=(UI_FONT, 10), cursor='hand2', padx=1,
    )

    def post(event=None):
        if 'disabled' not in combo.state():
            combo.tk.call('ttk::combobox::Post', combo._w)

    for widget in (bar, arrow, right_bar):
        widget.bind('<Button-1>', post)

    def sync(event=None):
        width = combo.winfo_width()
        height = combo.winfo_height()
        if width <= 1 or height <= 1:
            return
        top = 2
        inner_height = max(1, height - 4)
        bar.place(x=max(1, width - arrow_width), y=top, width=1, height=inner_height)
        arrow.place(x=max(2, width - arrow_width + 1), y=top,
                    width=max(1, arrow_width - 2), height=inner_height)
        right_bar.place(x=max(2, width - 1), y=1, width=1, height=max(1, height - 2))

    combo.bind('<Configure>', sync, add='+')
    combo.after_idle(sync)
    return combo


def prepare_combobox(combo):
    """下拉关闭后清除选中状态，避免文字因选中配色显示为空白。"""
    def clear_selection(event=None):
        try:
            combo.selection_clear()
        except tk.TclError:
            pass

    for sequence in ('<FocusIn>', '<FocusOut>', '<<ComboboxSelected>>', '<ButtonRelease-1>'):
        combo.bind(sequence, clear_selection, add='+')
    return combo


class ToolTip:
    """鼠标悬停时显示说明文字。"""
    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind('<Enter>', self.show, add='+')
        widget.bind('<Leave>', self.hide, add='+')
        widget.bind('<ButtonPress>', self.hide, add='+')

    def show(self, event=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 14
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f'+{x}+{y}')
        tk.Label(
            self.tip, text=self.text, justify='left', background=UI_TOOLTIP_BG,
            foreground=UI_TOOLTIP_FG, relief='solid', borderwidth=1, wraplength=380,
            font=(UI_FONT, UI_SMALL_SIZE), padx=9, pady=7,
        ).pack()

    def hide(self, event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None
