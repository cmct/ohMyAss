"""主窗口：首页、文本字幕、设置与面板组装。"""


import os
import sys
import re
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from tkinterdnd2 import TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    TkinterDnD = None
    DND_AVAILABLE = False
try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:
    Image = ImageDraw = ImageTk = None

from app_info import APP_NAME, APP_VERSION, resource_path, version_tuple
from config_store import (
    CONFIG_PATH, ERROR_LOG_PATH, get_output_dir, last_directory, load_config,
    log_exception, normalize_display_path, remember_directory, save_config,
)

import theme_ui
from theme_ui import (
    ICON_FIX, ICON_GLOSSARY, ICON_HOME, ICON_PENCIL,
    ICON_RENDERERS, ICON_SETTINGS, ICON_TV, ICON_VIDEO, RoundedFrame,
    ToolTip, UI_ACCENT, UI_ACCENT_DARK, UI_BG,
    UI_BODY_SIZE, UI_BORDER, UI_BUTTON_SIZE, UI_CARD,
    UI_DISPLAY_FONT, UI_FIELD, UI_FIELD_BORDER, UI_FIELD_SOFT,
    UI_FONT, UI_ICON_ACTIVE_BG, UI_ICON_CARD_SIZE, UI_ICON_FONT,
    UI_ICON_HOVER_BG, UI_ICON_NAV_SIZE, UI_MODULE_TITLE_SIZE, UI_MUTED,
    UI_SCALE, UI_SELECT, UI_SMALL_SIZE, UI_SUCCESS, UI_TEXT,
    UI_TREE_HEADER, UI_TROUGH, UI_WARNING, add_combo_arrow,
    apply_palette, center_window, create_card, create_hero,
    hex_to_rgb, monitor_dpi_for_window, pick_ui_fonts, prepare_combobox, px,
    recolor_widget_tree, register_bundled_fonts, render_camera_icon, render_gear_icon,
    render_pencil_icon, render_tv_icon, render_wrench_icon, rounded_rect, system_uses_dark_theme,
    theme_fg_map, theme_is_dark, theme_mode_from_config, theme_switch_map,
)
from reports import NOTICES_DATA_NAME, NOTICES_REPORT_NAME, TAIWAN_REPORT_NAME
from fonts import (
    ensure_source_han_font, installed_font_records, source_han_medium_installed,
)
from subtitle_core import (
    FSVP_EDIT_KINDS, FSVP_PAIRS, HDR_SUB_BRIGHTNESS, ProcessCancelled,
    STYLE_CATEGORIES, STYLE_KIND_TO_LABEL, STYLE_LABEL_TO_KIND,
    apply_updated_fontsize, apply_updated_margin_v, default_style_line, linked_fsvp_kinds,
    process_file, style_line_name,
)
from app_update import (
    apply_app_update, discard_app_update, download_app_update, fetch_app_update,
)
from task_runner import TaskRunner
from bdn_sup import (
    BDN_TOOL_ROOT, bdn_tool_version_text, bdn_tools_status_text,
    install_all_bdn_tools,
)
from ui_widgets import PlaceholderEntry, enable_file_drop, split_dropped_paths
from ui_blackbar import BlackBarDetectorWindow
from ui_style import StyleEditor, StylePreviewWindow
from ui_fixer import GlossaryPanel, IssueFixerPanel
from ui_bdn import BdnSupPanel
from ui_atv import AtvSubsPanel

theme_ui.register_theme_namespace(globals())


def create_root():
    """创建主窗口；优先使用带拖放支持的 TkinterDnD。"""
    global DND_AVAILABLE
    if DND_AVAILABLE and TkinterDnD is not None:
        try:
            return TkinterDnD.Tk()
        except tk.TclError:
            DND_AVAILABLE = False
    return tk.Tk()


def build_success_message(html_path, output_dir, chinese_only=False, is_HDR=False):
    html_name = os.path.basename(html_path) if html_path else NOTICES_REPORT_NAME
    occupied_note = ''
    if html_name != NOTICES_REPORT_NAME:
        occupied_note += f"\n注意：{NOTICES_REPORT_NAME} 正在被占用，本次已写入 {html_name}"

    if chinese_only:
        if is_HDR:
            count = 3
            file_lines = [
                "1-简体中文-HDR-zh.ass（简体中文）",
                "2-简体中文-SDR-zh.ass（简体中文 SDR）",
                "3-繁体中文-HDR-zh.ass（繁体中文）",
            ]
        else:
            count = 2
            file_lines = [
                "1-简体中文-zh.ass（简体中文）",
                "2-繁体中文-zh.ass（繁体中文）",
            ]
    elif is_HDR:
        count = 5
        file_lines = [
            "1-简英双语-HDR-zh.ass（简英双语）",
            "2-简英双语-SDR-zh.ass（简英双语 SDR）",
            "3-简体中文-HDR-zh.ass（简体中文）",
            "4-繁体中文-HDR-zh.ass（繁体中文）",
            "5-英文-HDR-en.ass（英文）",
        ]
    else:
        count = 4
        file_lines = [
            "1-简英双语-zh.ass（简英双语）",
            "2-简体中文-zh.ass（简体中文）",
            "3-繁体中文-zh.ass（繁体中文）",
            "4-英文-en.ass（英文）",
        ]

    lines = [f"已生成 {count} 个字幕文件到文件夹：", output_dir, ""] + file_lines + [
        f"{html_name}（可视化报告）",
    ]
    if output_dir and os.path.exists(os.path.join(output_dir, TAIWAN_REPORT_NAME)):
        lines.append(f"{TAIWAN_REPORT_NAME}（繁化姬台湾化对比）")
    return '\n'.join(lines) + occupied_note


def show_error_dialog(title: str, message: str, log_path: str = '', parent=None):
    """带「打开日志」按钮的错误对话框；没有日志时退回普通提示。"""
    if not log_path or not os.path.isfile(log_path):
        messagebox.showerror(title, message, parent=parent)
        return
    dialog = tk.Toplevel(parent) if parent is not None else tk.Toplevel()
    dialog.title(title)
    dialog.configure(bg=UI_BG)
    dialog.resizable(False, False)
    if parent is not None:
        dialog.transient(parent)

    body = tk.Frame(dialog, bg=UI_BG, padx=20, pady=16)
    body.pack(fill='both', expand=True)
    tk.Label(
        body, text=message, bg=UI_BG, fg=UI_TEXT, justify='left',
        wraplength=560, font=(UI_FONT, UI_BODY_SIZE),
    ).pack(anchor='w')

    buttons = tk.Frame(body, bg=UI_BG)
    buttons.pack(fill='x', pady=(14, 0))

    def open_log():
        try:
            os.startfile(log_path)
        except OSError as exc:
            messagebox.showerror('无法打开日志', str(exc), parent=dialog)

    ttk.Button(buttons, text='关闭', command=dialog.destroy,
               style='Secondary.TButton').pack(side='right')
    ttk.Button(buttons, text='打开日志', command=open_log,
               style='Accent.TButton').pack(side='right', padx=(0, 8))
    dialog.protocol('WM_DELETE_WINDOW', dialog.destroy)
    dialog.grab_set()
    center_window(dialog, parent)
    dialog.wait_window()


class App:
    def __init__(self, root):
        self.root = root
        self._install_exception_hooks()
        register_bundled_fonts()
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry(f"{px(1000)}x{px(880)}")
        self.root.minsize(px(940), px(840))
        config = load_config()
        self.dark_theme = theme_is_dark(config.get('theme_mode'))
        apply_palette(self.dark_theme)
        self._restore_window_position(config)
        try:
            self.root.iconbitmap(default=resource_path('ohMyAss Noto.ico'))
        except tk.TclError:
            pass
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.detector_window = None
        self.preview_window = None
        self.mid_black_px = None
        self.processing = False
        self.process_cancel = threading.Event()
        self.text_out_auto = True
        self.process_runner = TaskRunner(root, 100)
        self.nav_photo_cache = {}
        self.app_update_checking = False
        self.app_update_url = ''
        self.app_update_digest = ''
        try:
            self.last_update_check = float(config.get('last_update_check') or 0)
        except (TypeError, ValueError):
            self.last_update_check = 0.0
        self.app_update_queue = queue.Queue()
        self.initWidgets()
        self._apply_theme_options()
        if self.dark_theme:
            recolor_widget_tree(
                self.root,
                theme_switch_map(False, True),
                theme_fg_map(False, True))
        self.root.configure(bg=UI_BG)
        self.redraw_nav()
        config = load_config()
        self.title.set_value(config.get('title', ''))
        self.script_updated_by.set_value(config.get('script_updated_by', ''))
        self.use_taiwan.set(bool(config.get('taiwan', True)))
        self.hdr_brightness.set(str(config.get('hdr_brightness', int(HDR_SUB_BRIGHTNESS))))
        output_root = config.get('output_root', '')
        self.output_root_entry.set_value(normalize_display_path(output_root))
        self.text_out_auto = bool(
            config.get('output_root_auto', not bool(str(output_root or '').strip())))
        for key, entry in (
                ('original_script', self.original_script),
                ('original_translation', self.original_translation),
                ('original_timing', self.original_timing),
                ('original_editing', self.original_editing)):
            entry.set_value(config.get(key, ''))
        try:
            self.mid_black_px = float(config.get('mid_black_px'))
        except (TypeError, ValueError):
            self.mid_black_px = None
        self.last_error_log = str(config.get('last_error_log') or '')
        self.update_brightness_state()
        self._dpi_notice_scale = None
        self._dpi_check_job = None
        self.root.bind('<Configure>', self._schedule_dpi_check, add='+')
        self.root.after(200, self._poll_app_update)
        if getattr(sys, 'frozen', False):
            self.root.after(2500, lambda: self.check_app_update(silent=True))

    def _schedule_dpi_check(self, event=None):
        if event is not None and event.widget is not self.root:
            return
        if self._dpi_check_job is not None:
            try:
                self.root.after_cancel(self._dpi_check_job)
            except tk.TclError:
                pass
        self._dpi_check_job = self.root.after(1200, self._check_monitor_dpi)

    def _check_monitor_dpi(self):
        self._dpi_check_job = None
        try:
            if not self.root.winfo_exists():
                return
            hwnd = self.root.winfo_id()
        except tk.TclError:
            return
        dpi = monitor_dpi_for_window(hwnd)
        if not dpi:
            return
        scale = max(1.0, dpi / 96.0)
        if abs(scale - UI_SCALE) < 0.2:
            return
        key = round(scale, 2)
        if key == self._dpi_notice_scale:
            return
        self._dpi_notice_scale = key
        self.root.after(10, lambda: messagebox.showinfo(
            '显示缩放',
            f'当前显示器的缩放为 {int(round(scale * 100))}%，'
            f'软件启动时按 {int(round(UI_SCALE * 100))}% 创建界面。\n\n'
            '如需最佳显示效果，请把窗口移到该显示器后重新启动软件。',
            parent=self.root))

    def _install_exception_hooks(self):
        """把界面回调里未捕获的异常写进日志并提示，避免窗口模式静默失败。"""
        def report_callback_exception(_exc_type, value, _tb):
            log_exception('界面回调异常', value)
            try:
                messagebox.showerror(
                    '程序错误',
                    f'操作出现异常：{value}\n\n详细信息已写入：\n{ERROR_LOG_PATH}',
                    parent=self.root)
            except tk.TclError:
                pass
        self.root.report_callback_exception = report_callback_exception

    def _virtual_screen_bounds(self):
        try:
            import ctypes
            user32 = ctypes.windll.user32
            left = user32.GetSystemMetrics(76)
            top = user32.GetSystemMetrics(77)
            width = user32.GetSystemMetrics(78)
            height = user32.GetSystemMetrics(79)
            if width > 0 and height > 0:
                return left, top, left + width, top + height
        except (OSError, AttributeError, ValueError):
            pass
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _work_area_for(self, x: int, y: int, width: int, height: int):
        try:
            import ctypes
            from ctypes import wintypes

            class RECT(ctypes.Structure):
                _fields_ = [('left', wintypes.LONG), ('top', wintypes.LONG),
                            ('right', wintypes.LONG), ('bottom', wintypes.LONG)]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', RECT),
                            ('rcWork', RECT), ('dwFlags', wintypes.DWORD)]

            user32 = ctypes.windll.user32
            rect = RECT(x, y, x + width, y + height)
            user32.MonitorFromRect.restype = wintypes.HMONITOR
            monitor = user32.MonitorFromRect(ctypes.byref(rect), 2)
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                return (info.rcWork.left, info.rcWork.top,
                        info.rcWork.right, info.rcWork.bottom)
        except (OSError, AttributeError, ValueError):
            pass
        return self._virtual_screen_bounds()

    def _apply_theme_options(self):
        options = {
            '*TCombobox*Listbox.background': UI_FIELD,
            '*TCombobox*Listbox.foreground': UI_TEXT,
            '*TCombobox*Listbox.selectBackground': UI_SELECT,
            '*TCombobox*Listbox.selectForeground': UI_TEXT,
            '*TCombobox*Listbox.borderWidth': 0,
        }
        for key, value in options.items():
            self.root.option_add(key, value)

    def refresh_home_icons(self):
        if ImageTk is None:
            return
        for label, renderer in getattr(self, 'module_icon_specs', []):
            icon = renderer(px(34), hex_to_rgb(UI_TEXT))
            if icon is None:
                continue
            photo = ImageTk.PhotoImage(icon)
            label.configure(image=photo)
            label.image = photo

    def apply_theme(self, dark: bool):
        if dark == self.dark_theme:
            return
        old_dark = self.dark_theme
        apply_palette(dark)
        self.dark_theme = dark
        self.setup_styles()
        self._apply_theme_options()
        recolor_widget_tree(
            self.root,
            theme_switch_map(old_dark, dark),
            theme_fg_map(old_dark, dark))
        self.root.configure(bg=UI_BG)
        self.redraw_nav()
        self.refresh_home_icons()
        if getattr(self, 'sup_panel', None):
            self.sup_panel.ass_queue_list.redraw()
            self.sup_panel.ass_queue_list._draw_header()

    def change_theme_mode(self):
        mode = self.theme_mode_var.get()
        config = load_config()
        config['theme_mode'] = mode
        save_config(config)
        dark = (mode == 'dark' or
                (mode == 'system' and system_uses_dark_theme()))
        self.apply_theme(dark)

    def _restore_window_position(self, config):
        try:
            x = int(config.get('window_x'))
            y = int(config.get('window_y'))
        except (TypeError, ValueError):
            return
        width, height = px(1000), px(880)
        left, top, right, bottom = self._work_area_for(x, y, width, height)
        max_x = max(left, right - width)
        max_y = max(top, bottom - height)
        x = min(max(x, left), max_x)
        y = min(max(y, top), max_y)
        self.root.geometry(f"+{x}+{y}")

    def on_close(self):
        if self.processing:
            if not messagebox.askyesno(
                    '正在生成',
                    '字幕正在生成，确定要取消并退出吗？', parent=self.root):
                return
            self.cancel_process()
            self._close_wait_count = 0
            self._wait_process_cancel_and_close()
            return
        if getattr(self, 'sup_panel', None) and self.sup_panel.running:
            if not messagebox.askyesno('正在生成', 'SUP 正在生成中，确定要退出吗？', parent=self.root):
                return
            self.sup_panel.cancel_event.set()
            self._close_wait_count = 0
            self._wait_sup_cancel_and_close()
            return
        if getattr(self, 'fixer_panel', None) and self.fixer_panel.running:
            messagebox.showinfo(
                '正在生成', '问题修复正在重新生成字幕，请等待完成后再退出。', parent=self.root)
            return
        if getattr(self, 'fixer_panel', None) and self.fixer_panel.dirty:
            if not messagebox.askyesno('未保存', '问题修复的修改尚未生成，确定要退出吗？', parent=self.root):
                return
        self._save_config_and_destroy()

    def _save_config_and_destroy(self):
        try:
            self.process_cancel.set()
            self.process_runner.stop()
        except Exception:
            pass
        fixer = getattr(self, 'fixer_panel', None)
        if fixer is not None:
            try:
                fixer.task_runner.stop()
            except Exception:
                pass
        config = load_config()
        config.pop('sup_font_dir', None)
        config.update(self._current_config_values())
        sup_panel = getattr(self, 'sup_panel', None)
        if sup_panel is not None:
            config.update(sup_panel.config_values())
        x = self.root.winfo_x()
        y = self.root.winfo_y()
        if x > -10000 and y > -10000:
            config['window_x'] = x
            config['window_y'] = y
        save_config(config)
        self.root.destroy()

    def _wait_sup_cancel_and_close(self):
        panel = getattr(self, 'sup_panel', None)
        if (panel is not None and panel.running
                and getattr(self, '_close_wait_count', 0) < 50):
            self._close_wait_count += 1
            self.root.after(100, self._wait_sup_cancel_and_close)
            return
        self._save_config_and_destroy()

    def _wait_process_cancel_and_close(self):
        if (self.processing
                and getattr(self, '_close_wait_count', 0) < 50):
            self._close_wait_count += 1
            self.root.after(100, self._wait_process_cancel_and_close)
            return
        self._save_config_and_destroy()

    def _current_config_values(self):
        values = {
            'title': self.title.get_value(),
            'script_updated_by': self.script_updated_by.get_value(),
            'taiwan': self.use_taiwan.get(),
            'hdr_brightness': self.hdr_brightness.get(),
            'output_root': self.output_root_entry.get_value(),
            'output_root_auto': bool(self.text_out_auto),
            'original_script': self.original_script.get_value(),
            'original_translation': self.original_translation.get_value(),
            'original_timing': self.original_timing.get_value(),
            'original_editing': self.original_editing.get_value(),
        }
        if self.mid_black_px is not None:
            values['mid_black_px'] = round(float(self.mid_black_px), 1)
        if self.last_error_log:
            values['last_error_log'] = self.last_error_log
        if getattr(self, 'theme_mode_var', None) is not None:
            values['theme_mode'] = self.theme_mode_var.get()
        if getattr(self, 'last_update_check', 0):
            values['last_update_check'] = self.last_update_check
        return values

    def setup_styles(self):
        pick_ui_fonts()
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        self.root.configure(bg=UI_BG)
        style.configure('.', font=(UI_FONT, UI_BODY_SIZE), background=UI_BG, foreground=UI_TEXT)
        style.configure('App.TFrame', background=UI_BG)
        style.configure('Card.TFrame', background=UI_CARD)
        style.configure('Card.TLabel', background=UI_CARD, foreground=UI_TEXT)
        style.configure('CardMuted.TLabel', background=UI_CARD, foreground=UI_MUTED, font=(UI_FONT, UI_SMALL_SIZE))
        style.configure('Status.TLabel', background=UI_BG, foreground=UI_MUTED, font=(UI_FONT, UI_SMALL_SIZE))
        style.configure('Hint.TLabel', background=UI_BG, foreground=UI_MUTED, font=(UI_FONT, UI_SMALL_SIZE))
        style.configure(
            'Accent.TButton', background=UI_ACCENT, foreground='#ffffff',
            bordercolor=UI_ACCENT, lightcolor=UI_ACCENT,
            darkcolor=UI_ACCENT, borderwidth=0,
            focusthickness=0, padding=(px(18), px(11)),
            font=(UI_DISPLAY_FONT, UI_BUTTON_SIZE, 'bold'), relief='groove',
        )
        style.map(
            'Accent.TButton',
            background=[('active', UI_ACCENT_DARK), ('disabled', UI_BORDER)],
            foreground=[('disabled', UI_MUTED)],
        )
        style.configure(
            'Secondary.TButton', background=UI_FIELD, foreground=UI_TEXT,
            bordercolor=UI_FIELD_BORDER, lightcolor=UI_FIELD,
            darkcolor=UI_FIELD, borderwidth=1, padding=(px(14), px(9)), relief='groove',
        )
        style.map(
            'Secondary.TButton',
            background=[('active', UI_FIELD_SOFT), ('pressed', UI_TROUGH)],
        )
        style.configure(
            'TEntry', fieldbackground=UI_FIELD, background=UI_FIELD,
            foreground=UI_TEXT,
            bordercolor=UI_FIELD_BORDER, lightcolor=UI_FIELD,
            darkcolor=UI_FIELD, borderwidth=1, padding=px(7),
        )
        style.map(
            'TEntry',
            bordercolor=[('readonly', UI_FIELD_BORDER), ('focus', UI_ACCENT)],
            lightcolor=[('readonly', UI_FIELD), ('focus', UI_ACCENT)],
            darkcolor=[('readonly', UI_FIELD), ('focus', UI_ACCENT)],
            fieldbackground=[('readonly', UI_FIELD)],
        )
        # 多行编辑框使用与 TEntry 相同的主题 field 边框
        field_layout = [
            ('Entry.field', {'sticky': 'nswe', 'border': '1', 'children': [
                ('Entry.padding', {'sticky': 'nswe'}),
            ]}),
        ]
        for field_style in ('Field.TFrame', 'FieldFocus.TFrame'):
            style.layout(field_style, field_layout)
        style.configure(
            'Field.TFrame', fieldbackground=UI_FIELD,
            bordercolor=UI_FIELD_BORDER, lightcolor=UI_FIELD,
            darkcolor=UI_FIELD, padding=0,
        )
        style.configure(
            'FieldFocus.TFrame', fieldbackground=UI_FIELD,
            bordercolor=UI_ACCENT, lightcolor=UI_ACCENT,
            darkcolor=UI_ACCENT, padding=0,
        )
        style.configure(
            'Placeholder.TEntry', fieldbackground=UI_FIELD,
            background=UI_FIELD, foreground=UI_MUTED,
            bordercolor=UI_FIELD_BORDER, lightcolor=UI_FIELD,
            darkcolor=UI_FIELD, borderwidth=1, padding=px(7),
        )
        style.configure(
            'TCombobox', fieldbackground=UI_FIELD, background=UI_FIELD,
            foreground=UI_TEXT, bordercolor=UI_FIELD_BORDER,
            lightcolor=UI_FIELD, darkcolor=UI_FIELD,
            arrowcolor=UI_MUTED, borderwidth=1, padding=px(5),
        )
        style.map(
            'TCombobox',
            fieldbackground=[('readonly', UI_FIELD)],
            foreground=[('readonly', UI_TEXT)],
            selectbackground=[('readonly', UI_FIELD), ('focus', UI_FIELD), ('!focus', UI_FIELD)],
            selectforeground=[('readonly', UI_TEXT), ('focus', UI_TEXT), ('!focus', UI_TEXT)],
            bordercolor=[('focus', UI_ACCENT)],
            lightcolor=[('focus', UI_ACCENT)],
            darkcolor=[('focus', UI_ACCENT)],
        )
        style.layout('TCombobox', [
            ('Combobox.field', {'sticky': 'nswe', 'children': [
                ('Combobox.padding', {'expand': '1', 'sticky': 'nswe', 'children': [
                    ('Combobox.textarea', {'sticky': 'nswe'}),
                ]}),
            ]}),
        ])
        style.configure('TCombobox', padding=(px(7), px(7), px(14), px(7)))
        style.configure(
            'Blue.Horizontal.TProgressbar', troughcolor=UI_TROUGH, background=UI_ACCENT,
            bordercolor=UI_TROUGH, lightcolor=UI_ACCENT, darkcolor=UI_ACCENT,
            borderwidth=0, thickness=px(8),
        )
        style.configure(
            'Editor.Treeview', background=UI_FIELD, fieldbackground=UI_FIELD,
            foreground=UI_TEXT, rowheight=px(23), borderwidth=0,
            font=(UI_FONT, UI_BODY_SIZE),
        )
        style.configure(
            'Editor.Treeview.Heading', background=UI_TREE_HEADER, foreground=UI_MUTED,
            font=(UI_FONT, UI_SMALL_SIZE), relief='flat', borderwidth=0,
        )
        for orient in ('Vertical', 'Horizontal'):
            style.configure(
                f'{orient}.TScrollbar',
                background=UI_FIELD_SOFT, troughcolor=UI_BG,
                bordercolor=UI_BORDER, arrowcolor=UI_MUTED,
                lightcolor=UI_FIELD_SOFT, darkcolor=UI_FIELD_SOFT,
            )
            style.map(
                f'{orient}.TScrollbar',
                background=[('active', UI_TROUGH), ('pressed', UI_TROUGH)],
            )
        style.map(
            'Editor.Treeview',
            background=[('selected', UI_SELECT)],
            foreground=[('selected', UI_TEXT)],
        )
        # 去掉 ttk 按钮的虚线焦点框
        button_layout = [
            ('Button.border', {'sticky': 'nswe', 'border': '1', 'children': [
                ('Button.padding', {'sticky': 'nswe', 'children': [
                    ('Button.label', {'sticky': 'nswe'}),
                ]}),
            ]}),
        ]
        style.layout('Accent.TButton', button_layout)
        style.layout('Secondary.TButton', button_layout)

    def initWidgets(self):
        self.setup_styles()
        shell = tk.Frame(self.root, bg=UI_BG)
        shell.pack(fill='both', expand=True)

        nav = tk.Frame(shell, bg=UI_BG, width=px(64))
        nav.pack(side='left', fill='y', padx=(px(10), px(4)), pady=px(14))
        nav.pack_propagate(False)
        self.nav_icons = {}

        def add_nav(key, label, side='top'):
            canvas = tk.Canvas(nav, width=px(48), height=px(44), bg=UI_BG,
                               highlightthickness=0, bd=0, cursor='hand2')
            canvas.pack(side=side, pady=(0, px(6)))
            canvas.bind('<Button-1>', lambda event, name=key: self.switch_tab(name))
            canvas.bind('<Enter>', lambda event, name=key: self.redraw_nav(hover=name))
            canvas.bind('<Leave>', lambda event: self.redraw_nav())
            ToolTip(canvas, label)
            self.nav_icons[key] = canvas

        for key, label in (('home', '主页'), ('ass', '文本字幕'),
                           ('glossary', '术语表'), ('fix', '问题修复'),
                           ('sup', '图形字幕'), ('atv', '流媒体字幕')):
            add_nav(key, label)
        tk.Frame(nav, bg=UI_BG).pack(fill='y', expand=True)
        add_nav('settings', '设置', side='bottom')

        container = tk.Frame(shell, bg=UI_BG)
        container.pack(side='left', fill='both', expand=True,
                       padx=(px(6), px(24)), pady=px(12))

        self.pages = {
            key: tk.Frame(container, bg=UI_BG)
            for key in ('home', 'ass', 'glossary', 'fix', 'sup', 'atv', 'settings')
        }
        self._built_pages = set()
        self._page_builders = {
            'home': lambda: self.build_home_page(self.pages['home']),
            'ass': lambda: self.build_ass_page(self.pages['ass']),
            'glossary': self._build_glossary_page,
            'fix': self._build_fix_page,
            'sup': self._build_sup_page,
            'atv': self._build_atv_page,
            'settings': lambda: self.build_settings_page(self.pages['settings']),
        }
        # 主页、文本字幕、术语表、设置在启动时就要用到（设置页含更新检查控件，
        # 冻结模式启动 2.5 秒后会自动检查更新），问题修复 / 图形字幕首次进入时再构建
        for key in ('home', 'ass', 'glossary', 'settings'):
            self._ensure_page(key)
        self.active_page = 'home'
        self.switch_tab('home')

    def _build_glossary_page(self):
        self.glossary_panel = GlossaryPanel(self.pages['glossary'])
        self.glossary_panel.pack(fill='both', expand=True)

    def _build_fix_page(self):
        self.fixer_panel = IssueFixerPanel(self.pages['fix'])
        self.fixer_panel.pack(fill='both', expand=True)

    def _build_sup_page(self):
        self.sup_panel = BdnSupPanel(self.pages['sup'])
        self.sup_panel.pack(fill='both', expand=True)

    def _build_atv_page(self):
        self.atv_panel = AtvSubsPanel(self.pages['atv'])
        self.atv_panel.pack(fill='both', expand=True)

    def _ensure_page(self, key):
        if key in self._built_pages:
            return
        builder = self._page_builders.get(key)
        if builder is not None:
            builder()
        self._built_pages.add(key)

    def switch_tab(self, key):
        self._ensure_page(key)
        self.active_page = key
        for name, page in self.pages.items():
            if name == key:
                page.pack(fill='both', expand=True)
            else:
                page.pack_forget()
        if key == 'settings' and hasattr(self, 'tool_state_label'):
            self.refresh_font_status()
            self.refresh_tool_status()
        self.redraw_nav()

    def redraw_nav(self, hover=None):
        glyphs = {'home': ICON_HOME, 'ass': ICON_PENCIL,
                  'glossary': ICON_GLOSSARY, 'fix': ICON_FIX,
                  'sup': ICON_VIDEO, 'atv': ICON_TV,
                  'settings': ICON_SETTINGS}
        icon_kinds = {'home': 'home', 'ass': 'pencil',
                      'glossary': 'glossary', 'fix': 'fix',
                      'sup': 'frame', 'atv': 'tv',
                      'settings': 'settings'}
        for key, canvas in self.nav_icons.items():
            active = key == self.active_page
            canvas.delete('all')
            if active:
                rounded_rect(canvas, px(7), px(4), px(46), px(40), px(8),
                              UI_ICON_ACTIVE_BG)
                canvas.create_rectangle(0, px(12), px(3), px(32),
                                        fill=UI_ACCENT, outline='')
                color = UI_TEXT
            elif key == hover:
                rounded_rect(canvas, px(7), px(4), px(46), px(40), px(8),
                              UI_ICON_HOVER_BG)
                color = UI_TEXT
            else:
                color = UI_MUTED
            photo = self._nav_photo(icon_kinds[key], color)
            if photo is not None:
                canvas.create_image(px(25), px(22), image=photo)
            else:
                canvas.create_text(px(25), px(22), text=glyphs[key],
                                   font=(UI_ICON_FONT, UI_ICON_NAV_SIZE), fill=color)

    def _nav_photo(self, kind: str, color: str):
        if ImageTk is None:
            return None
        cache_key = (kind, color)
        if cache_key in self.nav_photo_cache:
            return self.nav_photo_cache[cache_key]
        renderer = ICON_RENDERERS.get(kind)
        if renderer is None:
            return None
        icon = renderer(px(20), hex_to_rgb(color), weight=1.3)
        if icon is None:
            return None
        photo = ImageTk.PhotoImage(icon)
        self.nav_photo_cache[cache_key] = photo
        return photo

    def build_home_page(self, main):
        self.module_icon_specs = []
        create_hero(main, APP_NAME, APP_VERSION).pack(fill='x', pady=(0, px(12)))
        cards = tk.Frame(main, bg=UI_BG)
        cards.pack(fill='both', expand=True)
        for column in range(2):
            cards.columnconfigure(column, weight=1, uniform='modules')
        self.build_module_card(
            cards, 0, 0, 'pencil', '文本字幕',
            '字幕样式、标点、HDR 与繁体台湾化处理', 'ass')
        self.build_module_card(
            cards, 0, 1, 'glossary', '术语表',
            '维护原文与指定译法，生成时检查术语一致性', 'glossary')
        self.build_module_card(
            cards, 1, 0, 'fix', '问题修复',
            '按字幕问题报告快速修改并重新生成字幕', 'fix')
        self.build_module_card(
            cards, 1, 1, 'frame', '图形字幕',
            '由 ASS 生成 BDN XML + PNG，并编码 SUP、PES + MUI。', 'sup')
        self.build_module_card(
            cards, 2, 0, 'settings', '设置',
            '工具版本、字体安装与在线更新', 'settings')
        self.build_module_card(
            cards, 2, 1, 'tv', '流媒体字幕',
            '按 IMDB / 片名抓取 Apple TV 全地区、全语言字幕。', 'atv')

    def build_module_card(self, parent, row, column, kind, title, description, target):
        card = RoundedFrame(parent, radius=8)
        card.configure(cursor='hand2')
        padx = (0, px(12)) if column == 0 else (px(12), 0)
        pady = (0, px(12)) if row < 2 else (0, 0)
        card.grid(row=row, column=column, sticky='nsew', padx=padx, pady=pady)
        inner = tk.Frame(card.body, bg=UI_CARD, padx=px(20), pady=px(18))
        inner.pack(fill='both', expand=True)

        glyph = {'pencil': ICON_PENCIL, 'frame': ICON_VIDEO,
                 'tv': ICON_TV, 'fix': ICON_FIX,
                 'glossary': ICON_GLOSSARY,
                 'settings': ICON_SETTINGS}.get(kind, ICON_PENCIL)
        renderer = {'pencil': render_pencil_icon, 'fix': render_wrench_icon,
                    'frame': render_camera_icon, 'tv': render_tv_icon,
                    'settings': render_gear_icon}.get(kind)
        photo = None
        if renderer is not None and ImageTk is not None:
            icon = renderer(px(34), hex_to_rgb(UI_TEXT))
            if icon is not None:
                photo = ImageTk.PhotoImage(icon)
        if photo is not None:
            icon_label = tk.Label(inner, image=photo, bg=UI_CARD)
            icon_label.image = photo
            icon_label.pack(anchor='w')
            self.module_icon_specs.append((icon_label, renderer))
        else:
            tk.Label(
                inner, text=glyph, bg=UI_CARD, fg=UI_TEXT,
                font=(UI_ICON_FONT, UI_ICON_CARD_SIZE),
            ).pack(anchor='w')

        tk.Label(inner, text=title, bg=UI_CARD, fg=UI_TEXT,
                 font=(UI_DISPLAY_FONT, UI_MODULE_TITLE_SIZE, 'bold')).pack(
            anchor='w', pady=(px(10), px(6)))
        tk.Label(inner, text=description, bg=UI_CARD, fg=UI_MUTED,
                 font=(UI_FONT, UI_BODY_SIZE), wraplength=px(380),
                 justify='left').pack(anchor='w')

        footer = tk.Frame(inner, bg=UI_CARD)
        footer.pack(fill='x', pady=(px(18), 0))
        tk.Label(footer, text='进入模块', bg=UI_CARD, fg=UI_TEXT,
                 font=(UI_FONT, UI_BODY_SIZE, 'bold')).pack(side='left')
        tk.Label(footer, text='\u2192', bg=UI_CARD, fg=UI_TEXT,
                 font=(UI_FONT, UI_BODY_SIZE, 'bold')).pack(side='right')

        def bind_click(widget):
            widget.bind('<Button-1>', lambda event, name=target: self.switch_tab(name))
            for child in widget.winfo_children():
                bind_click(child)

        bind_click(card)

    def build_ass_page(self, main):
        self._init_style_library()
        create_hero(main, '文本字幕', '字幕样式、标点、HDR 与繁体台湾化处理').pack(fill='x', pady=(0, 8))

        file_outer, file_body = create_card(main, '字幕与输出')
        file_outer.pack(fill='x', pady=(0, 8))
        file_body.columnconfigure(1, weight=1)

        def file_label(text, row):
            tk.Label(
                file_body, text=text, bg=UI_CARD, fg=UI_TEXT,
                font=(UI_FONT, UI_BODY_SIZE),
            ).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=4)

        file_label('字幕文件', 0)
        self.file_entry = PlaceholderEntry(
            file_body, placeholder='支持 .ass / .srt，可直接拖入文件')
        self.file_entry.grid(row=0, column=1, sticky='ew', pady=4)
        enable_file_drop(self.file_entry, self._on_ass_drop)
        self.btn_open = ttk.Button(
            file_body, text='选择', command=self.openFile, style='Secondary.TButton')
        self.btn_open.grid(row=0, column=2, sticky='e', padx=(12, 0), pady=4)

        file_label('输出文件夹', 1)
        self.output_root_entry = PlaceholderEntry(
            file_body, placeholder='生成到 <输出文件夹>/<影片名>；默认 <输出文件夹> 为字幕所在文件夹')
        self.output_root_entry.grid(row=1, column=1, sticky='ew', pady=4)
        self.output_root_entry.bind(
            '<Key>', lambda event: setattr(self, 'text_out_auto', False), add='+')
        self.btn_choose_output = ttk.Button(
            file_body, text='选择', command=self.choose_output_root,
            style='Secondary.TButton')
        self.btn_choose_output.grid(
            row=1, column=2, sticky='e', padx=(12, 0), pady=4)
        self.file_entry.bind('<FocusOut>', self._normalize_text_entries, add='+')
        self.output_root_entry.bind('<FocusOut>', self._normalize_text_entries, add='+')

        options_outer, options_card = create_card(main, '片源选项')
        options_outer.pack(fill='x', pady=(0, 8))
        self.is_HDR = tk.BooleanVar()
        self.option_is_HDR = self.make_option_check(options_card, 'HDR 高动态', self.is_HDR)
        self.option_is_HDR.grid(row=0, column=0, sticky='w')
        tk.Label(
            options_card, text='亮度', bg=UI_CARD, fg=UI_TEXT, font=(UI_FONT, UI_BODY_SIZE),
        ).grid(row=0, column=1, sticky='w', padx=(20, 0))
        self.hdr_brightness = tk.StringVar(value=str(int(HDR_SUB_BRIGHTNESS)))
        self.hdr_brightness_entry = ttk.Entry(
            options_card, textvariable=self.hdr_brightness, width=6, justify='center')
        self.hdr_brightness_entry.grid(row=0, column=2, sticky='w', padx=(6, 0))
        tk.Label(
            options_card, text='nit', bg=UI_CARD, fg=UI_MUTED, font=(UI_FONT, UI_SMALL_SIZE),
        ).grid(row=0, column=3, sticky='w', padx=(4, 0))
        self.is_mid_black = tk.BooleanVar()
        self.option_is_mid_black = self.make_option_check(options_card, '中黑边视频', self.is_mid_black)
        self.option_is_mid_black.grid(row=0, column=5, sticky='w', padx=(20, 0))
        self.use_taiwan = tk.BooleanVar(value=True)
        self.option_use_taiwan = self.make_option_check(options_card, '繁体台湾化（繁化姬）', self.use_taiwan)
        self.option_use_taiwan.grid(row=0, column=4, sticky='w', padx=(20, 0))
        action_buttons = tk.Frame(options_card, bg=UI_CARD)
        action_buttons.grid(row=0, column=6, sticky='e', padx=(20, 0))
        self.btn_black = ttk.Button(
            action_buttons, text='黑边检测', command=self.open_black_detector,
            style='Secondary.TButton')
        self.btn_black.pack(side='left')
        options_card.columnconfigure(6, weight=1)
        self.is_HDR.trace_add('write', self.update_brightness_state)
        self.update_brightness_state()

        info_outer, info_card = create_card(main, '字幕信息')
        info_outer.pack(fill='x', pady=(0, 8))
        info_card.columnconfigure(1, weight=1)
        info_card.columnconfigure(3, weight=1)

        self.title = PlaceholderEntry(info_card, placeholder='必填')
        self.script_updated_by = PlaceholderEntry(info_card, placeholder='必填')
        self.original_script = PlaceholderEntry(info_card, placeholder='选填')
        self.original_translation = PlaceholderEntry(info_card, placeholder='选填')
        self.original_timing = PlaceholderEntry(info_card, placeholder='选填')
        self.original_editing = PlaceholderEntry(info_card, placeholder='选填')

        def field_label(text):
            return tk.Label(info_card, text=text, bg=UI_CARD, fg=UI_TEXT, font=(UI_FONT, UI_BODY_SIZE))

        field_label('影片名 *').grid(row=0, column=0, sticky='w', padx=(0, 8), pady=5)
        self.title.grid(row=0, column=1, sticky='ew', padx=(0, 20), pady=5)
        field_label('字幕员 *').grid(row=0, column=2, sticky='w', padx=(0, 8), pady=5)
        self.script_updated_by.grid(row=0, column=3, sticky='ew', pady=5)

        field_label('原作者').grid(row=1, column=0, sticky='w', padx=(0, 8), pady=5)
        self.original_script.grid(row=1, column=1, sticky='ew', padx=(0, 20), pady=5)
        field_label('翻译').grid(row=1, column=2, sticky='w', padx=(0, 8), pady=5)
        self.original_translation.grid(row=1, column=3, sticky='ew', pady=5)

        field_label('校对').grid(row=2, column=0, sticky='w', padx=(0, 8), pady=5)
        self.original_timing.grid(row=2, column=1, sticky='ew', padx=(0, 20), pady=5)
        field_label('时间轴').grid(row=2, column=2, sticky='w', padx=(0, 8), pady=5)
        self.original_editing.grid(row=2, column=3, sticky='ew', pady=5)

        style_outer, style_card = create_card(main, '样式库')
        style_outer.pack(fill='x', pady=(0, 8))
        style_row = tk.Frame(style_card, bg=UI_CARD)
        style_row.pack(fill='x')
        tk.Label(
            style_row, text='样式类别', bg=UI_CARD, fg=UI_TEXT,
            font=(UI_FONT, UI_BODY_SIZE),
        ).pack(side='left')
        self.style_category_var = tk.StringVar(
            value=STYLE_KIND_TO_LABEL[self.active_style_kind])
        self.style_library_combo = add_combo_arrow(prepare_combobox(
            ttk.Combobox(
                style_row, textvariable=self.style_category_var,
                state='readonly', width=24,
                values=[label for _kind, label in STYLE_CATEGORIES])))
        self.style_library_combo.pack(side='left', padx=(8, 0))
        self.style_library_combo.bind(
            '<<ComboboxSelected>>', lambda event: self._on_style_selected())
        for text, command in (
                ('修改', self.edit_style_category),
                ('样式预览', self.open_style_preview)):
            ttk.Button(
                style_row, text=text, command=command,
                style='Secondary.TButton').pack(side='left', padx=(8, 0))

        action = tk.Frame(main, bg=UI_BG)
        action.pack(side='bottom', fill='x', pady=(8, 8))
        self.btn_process = ttk.Button(action, text='开始生成', command=self.start_process, style='Accent.TButton')
        self.btn_process.pack(side='left')
        self.btn_cancel = ttk.Button(
            action, text='取消', command=self.cancel_process, style='Secondary.TButton')
        self.btn_cancel.pack(side='left', padx=(8, 0))
        self.btn_cancel.config(state='disabled')
        self.btn_output_open = ttk.Button(
            action, text='打开输出文件夹', command=self.open_output_folder, style='Secondary.TButton')
        self.btn_output_open.pack(side='left', padx=(10, 0))
        self.status_var = tk.StringVar(value='')
        ttk.Label(action, textvariable=self.status_var, style='Status.TLabel').pack(side='left', padx=(16, 0))
        self.process_percent = tk.StringVar(value='')
        self.process_progress = ttk.Progressbar(
            action, style='Blue.Horizontal.TProgressbar', orient='horizontal',
            mode='determinate', length=px(160), maximum=100,
        )
        self.process_progress.pack(side='right', padx=(10, 0))
        ttk.Label(
            action, textvariable=self.process_percent, style='Status.TLabel',
        ).pack(side='right')

    def _init_style_library(self):
        config = load_config()
        saved = config.get('style_categories')
        self.style_lines_by_kind = {}
        self.style_fsvp_by_kind = {}
        defaults_updated = False
        for kind, _label in STYLE_CATEGORIES:
            line = default_style_line(kind)
            fsvp = 12.0
            entry = saved.get(kind) if isinstance(saved, dict) else None
            if isinstance(entry, dict):
                candidate = entry.get('line')
                if (isinstance(candidate, str) and candidate.count(',') >= 21
                        and style_line_name(candidate) == kind):
                    line = candidate
                try:
                    fsvp = float(entry.get('fsvp', 12.0))
                except (TypeError, ValueError):
                    fsvp = 12.0
            if kind in ('zh_lyrics_monolingual', 'zh_note_single_line'):
                updated_line = apply_updated_margin_v(line, kind, 40)
                if updated_line != line:
                    line = updated_line
                    defaults_updated = True
            if kind in ('zh_lyrics_bilingual', 'zh_lyrics_monolingual'):
                updated_line = apply_updated_fontsize(line, kind, 80)
                if updated_line != line:
                    line = updated_line
                    defaults_updated = True
            self.style_lines_by_kind[kind] = line
            self.style_fsvp_by_kind[kind] = fsvp
        # 双语样式两两共用行间距，以实际参与生成的英文样式值为准
        fsvp_synced = False
        for primary, secondary in FSVP_PAIRS:
            value = self.style_fsvp_by_kind.get(secondary, 12.0)
            for kind in (primary, secondary):
                if self.style_fsvp_by_kind.get(kind) != value:
                    self.style_fsvp_by_kind[kind] = value
                    fsvp_synced = True
        active = str(config.get('style_category_active')
                     or 'zh_dialogue_bilingual')
        if active not in self.style_lines_by_kind:
            active = 'zh_dialogue_bilingual'
        self.active_style_kind = active
        expected_keys = {kind for kind, _ in STYLE_CATEGORIES}
        saved_keys = set(saved) if isinstance(saved, dict) else set()
        if (defaults_updated or fsvp_synced
                or not expected_keys.issubset(saved_keys)):
            self._save_style_library()

    def _style_lines_list(self):
        return [self.style_lines_by_kind[kind] for kind, _ in STYLE_CATEGORIES]

    def _fsvp_values(self):
        return dict(self.style_fsvp_by_kind)

    def _save_style_library(self):
        config = load_config()
        config['style_categories'] = {
            kind: {
                'line': self.style_lines_by_kind[kind],
                'fsvp': self.style_fsvp_by_kind.get(kind, 12.0),
            }
            for kind, _ in STYLE_CATEGORIES
        }
        config['style_category_active'] = self.active_style_kind
        save_config(config)

    def _select_style_category(self, kind):
        if kind not in self.style_lines_by_kind:
            return
        self.active_style_kind = kind
        self.style_category_var.set(STYLE_KIND_TO_LABEL[kind])
        self._save_style_library()
        self._sync_preview_styles()

    def _sync_preview_styles(self):
        try:
            if self.preview_window and self.preview_window.window.winfo_exists():
                self.preview_window.style_lines = self._style_lines_list()
                self.preview_window.fsvp_values = self._fsvp_values()
                self.preview_window.style_var.set(
                    STYLE_KIND_TO_LABEL[self.active_style_kind])
                self.preview_window.redraw()
        except (AttributeError, tk.TclError):
            pass

    def _on_style_selected(self):
        kind = STYLE_LABEL_TO_KIND.get(self.style_category_var.get())
        if kind:
            self._select_style_category(kind)

    def edit_style_category(self):
        kind = self.active_style_kind
        label = STYLE_KIND_TO_LABEL[kind]
        editor = StyleEditor(
            self.root, label, self.style_lines_by_kind[kind],
            self.style_fsvp_by_kind.get(kind, 12.0),
            default_style_line(kind), 12.0,
            show_fsvp=kind in FSVP_EDIT_KINDS)
        self.root.wait_window(editor.window)
        if editor.result:
            self.style_lines_by_kind[kind] = editor.result['line']
            for linked in linked_fsvp_kinds(kind):
                self.style_fsvp_by_kind[linked] = editor.result['fsvp']
            self._save_style_library()
            self._sync_preview_styles()

    def open_output_folder(self):
        path = getattr(self, 'last_output_dir', None)
        if path and os.path.isdir(path):
            os.startfile(path)
            return
        path = self.output_root_entry.get_value()
        if path and os.path.isdir(path):
            os.startfile(path)
            return
        source = self.file_entry.get_value()
        if source and os.path.isfile(source):
            os.startfile(os.path.dirname(os.path.abspath(source)))
            return
        messagebox.showinfo('提示', '还没有可打开的输出文件夹', parent=self.root)

    def choose_output_root(self):
        path = filedialog.askdirectory(
            title='选择输出文件夹', parent=self.root,
            initialdir=last_directory(
                'text_output', self.output_root_entry.get_value()))
        if path:
            remember_directory('text_output', path)
            self.output_root_entry.set_value(normalize_display_path(path))
            self.text_out_auto = False

    def build_settings_page(self, main):
        outer = tk.Frame(main, bg=UI_BG)
        outer.pack(fill='both', expand=True)
        settings_canvas = tk.Canvas(
            outer, bg=UI_BG, highlightthickness=0, bd=0)
        settings_scroll = ttk.Scrollbar(
            outer, orient='vertical', command=settings_canvas.yview)
        settings_scroll.pack(side='right', fill='y')
        settings_canvas.pack(side='left', fill='both', expand=True)
        content = tk.Frame(settings_canvas, bg=UI_BG)
        settings_window = settings_canvas.create_window(
            (0, 0), window=content, anchor='nw')
        content.bind(
            '<Configure>',
            lambda event: settings_canvas.configure(
                scrollregion=settings_canvas.bbox('all')))
        settings_canvas.bind(
            '<Configure>',
            lambda event: settings_canvas.itemconfigure(
                settings_window, width=event.width))
        main = content

        create_hero(main, '设置', '工具、字体与在线更新').pack(fill='x', pady=(0, 10))

        appearance_outer, appearance = create_card(main, '外观')
        appearance_outer.pack(fill='x', pady=(0, 10))
        appearance.columnconfigure(0, weight=1)

        theme_row = tk.Frame(appearance, bg=UI_CARD)
        theme_row.grid(row=0, column=0, sticky='w')
        tk.Label(
            theme_row, text='主题', bg=UI_CARD, fg=UI_TEXT,
            font=(UI_FONT, UI_BODY_SIZE),
        ).pack(side='left', padx=(0, 12))
        self.theme_mode_var = tk.StringVar(
            value=theme_mode_from_config(load_config().get('theme_mode')))
        for text, value in (
                ('跟随系统', 'system'), ('浅色', 'light'), ('深色', 'dark')):
            tk.Radiobutton(
                theme_row, text=text, value=value,
                variable=self.theme_mode_var,
                command=self.change_theme_mode,
                bg=UI_CARD, fg=UI_TEXT, activebackground=UI_CARD,
                activeforeground=UI_TEXT, selectcolor=UI_CARD,
                highlightthickness=0, bd=0, padx=0,
                font=(UI_FONT, UI_BODY_SIZE), cursor='hand2',
            ).pack(side='left', padx=(0, 14))

        update_outer, update_card = create_card(main, '软件更新')
        update_outer.pack(fill='x', pady=(0, 10))
        update_card.columnconfigure(0, weight=1)

        tk.Label(
            update_card, text=f'当前版本 {APP_VERSION}', bg=UI_CARD, fg=UI_TEXT,
            font=(UI_FONT, UI_BODY_SIZE),
        ).grid(row=0, column=0, sticky='w')
        self.btn_app_update = ttk.Button(
            update_card, text='检查软件更新', command=self.check_app_update,
            style='Secondary.TButton')
        self.btn_app_update.grid(row=0, column=1, sticky='e')

        self.app_update_status = tk.StringVar(value='启动时会自动检查更新')
        tk.Label(
            update_card, textvariable=self.app_update_status, bg=UI_CARD, fg=UI_MUTED,
            font=(UI_FONT, UI_SMALL_SIZE), wraplength=680, justify='left',
        ).grid(row=1, column=0, columnspan=2, sticky='w', pady=(8, 0))

        self.app_update_progress = ttk.Progressbar(
            update_card, style='Blue.Horizontal.TProgressbar', orient='horizontal', mode='determinate')
        self.app_update_progress.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(8, 0))

        card_outer, card = create_card(main, '工具（在线更新）')
        card_outer.pack(fill='x')
        card.columnconfigure(0, weight=1)

        self.tool_label = tk.StringVar(value=bdn_tool_version_text())
        tool_title = tk.Frame(card, bg=UI_CARD)
        tool_title.grid(row=0, column=0, sticky='w')
        tk.Label(
            tool_title, textvariable=self.tool_label, bg=UI_CARD, fg=UI_TEXT,
            font=(UI_FONT, UI_BODY_SIZE),
        ).pack(side='left')
        self.tool_state_label = tk.Label(
            tool_title, text='', bg=UI_CARD, fg=UI_MUTED,
            font=(UI_FONT, UI_BODY_SIZE))
        self.tool_state_label.pack(side='left', padx=(10, 0))
        self.btn_update = ttk.Button(card, text='检查更新', command=self.update_tools, style='Secondary.TButton')
        self.btn_update.grid(row=0, column=1, sticky='e')
        ToolTip(self.btn_update,
                '检查并更新 SUPer 与官方 ass2bdnxml。\n'
                'x64 图形字幕实际使用的 bin64\\avs2bdnxml.exe 是随程序内置的修改版，\n'
                '不会被这个按钮替换。')
        self.refresh_tool_status()

        tk.Label(
            card, text='工具安装位置：' + BDN_TOOL_ROOT,
            bg=UI_CARD, fg=UI_MUTED, font=(UI_FONT, UI_SMALL_SIZE), wraplength=680, justify='left',
        ).grid(row=1, column=0, columnspan=2, sticky='w', pady=(8, 0))

        self.settings_status = tk.StringVar(value='')
        tk.Label(
            card, textvariable=self.settings_status, bg=UI_CARD, fg=UI_MUTED,
            font=(UI_FONT, UI_SMALL_SIZE), wraplength=680, justify='left',
        ).grid(row=2, column=0, columnspan=2, sticky='w', pady=(10, 0))

        self.settings_progress = ttk.Progressbar(
            card, style='Blue.Horizontal.TProgressbar', orient='horizontal', mode='determinate')
        self.settings_progress.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(8, 0))

        info_row = tk.Frame(card, bg=UI_CARD)
        info_row.grid(row=4, column=0, columnspan=2, sticky='w', pady=(10, 0))
        for text, command in (
                ('打开配置目录', self.open_config_dir),
                ('打开工具目录', self.open_tool_dir),
                ('打开日志', self.open_last_log),
                ('许可与源码', self.show_licenses)):
            ttk.Button(
                info_row, text=text, command=command,
                style='Secondary.TButton').pack(side='left', padx=(0, 8))

        font_outer, font_card = create_card(main, '字体')
        font_outer.pack(fill='x', pady=(10, 0))
        font_card.columnconfigure(0, weight=1)

        font_title = tk.Frame(font_card, bg=UI_CARD)
        font_title.grid(row=0, column=0, columnspan=2, sticky='ew')
        tk.Label(
            font_title, text='思源黑体 Medium（Source Han Sans SC Medium）',
            bg=UI_CARD, fg=UI_TEXT, font=(UI_FONT, UI_BODY_SIZE),
        ).pack(side='left')
        self.font_state_label = tk.Label(
            font_title, text='', bg=UI_CARD, fg=UI_MUTED, font=(UI_FONT, UI_BODY_SIZE))
        self.font_state_label.pack(side='left', padx=(10, 0))

        font_actions = tk.Frame(font_card, bg=UI_CARD)
        font_actions.grid(row=0, column=1, sticky='e')
        self.btn_font = ttk.Button(
            font_actions, text='检查并安装', command=self.install_font, style='Secondary.TButton')
        self.btn_font.pack(side='left')
        self.btn_font_refresh = ttk.Button(
            font_actions, text='刷新状态', command=self.refresh_font_status,
            style='Secondary.TButton')
        self.btn_font_refresh.pack(side='left', padx=(6, 0))

        self.font_status = tk.StringVar(value='')
        tk.Label(
            font_card, textvariable=self.font_status, bg=UI_CARD, fg=UI_MUTED,
            font=(UI_FONT, UI_SMALL_SIZE), wraplength=680, justify='left',
        ).grid(row=1, column=0, columnspan=2, sticky='w', pady=(6, 0))

        tk.Label(
            font_card,
            text='字幕渲染使用的默认字体；未安装时自动下载 SourceHanSansSC-Medium.otf 并安装到当前用户。',
            bg=UI_CARD, fg=UI_MUTED, font=(UI_FONT, UI_SMALL_SIZE),
            wraplength=680, justify='left',
        ).grid(row=2, column=0, columnspan=2, sticky='w', pady=(4, 0))

        self.font_progress = ttk.Progressbar(
            font_card, style='Blue.Horizontal.TProgressbar', orient='horizontal', mode='determinate')
        self.font_progress.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(8, 0))

        self.refresh_font_status()

        def on_wheel(event):
            settings_canvas.yview_scroll(int(-event.delta / 120), 'units')

        def bind_wheel(widget):
            widget.bind('<MouseWheel>', on_wheel, add='+')
            for child in widget.winfo_children():
                bind_wheel(child)

        bind_wheel(content)

    def refresh_font_status(self):
        try:
            installed_font_records.cache_clear()
        except AttributeError:
            pass
        installed = source_han_medium_installed()
        self.font_state_label.config(
            text='已安装' if installed else '未安装',
            fg=UI_SUCCESS if installed else UI_WARNING,
        )

    def refresh_tool_status(self):
        status = bdn_tools_status_text()
        self.tool_label.set(bdn_tool_version_text())
        if status == '已安装':
            color = UI_SUCCESS
        else:
            color = UI_WARNING
        self.tool_state_label.config(text=status, fg=color)

    def _open_directory(self, path, label):
        if path and os.path.isdir(path):
            try:
                os.startfile(path)
            except OSError as exc:
                messagebox.showerror('无法打开', str(exc), parent=self.root)
            return
        messagebox.showinfo(
            '提示', f'{label}不存在：\n{path or "（未设置）"}', parent=self.root)

    def open_config_dir(self):
        self._open_directory(os.path.dirname(CONFIG_PATH), '配置目录')

    def open_tool_dir(self):
        try:
            os.makedirs(BDN_TOOL_ROOT, exist_ok=True)
        except OSError:
            pass
        self._open_directory(BDN_TOOL_ROOT, '工具目录')

    def open_last_log(self):
        path = getattr(self, 'last_error_log', '')
        for candidate in (path, ERROR_LOG_PATH):
            if candidate and os.path.isfile(candidate):
                try:
                    os.startfile(candidate)
                    return
                except OSError as exc:
                    messagebox.showerror(
                        '无法打开日志', str(exc), parent=self.root)
                    return
        messagebox.showinfo(
            '提示',
            f'暂时没有错误日志。\n生成失败时会写入：\n{ERROR_LOG_PATH}',
            parent=self.root)

    def show_licenses(self):
        dialog = tk.Toplevel(self.root)
        dialog.title('第三方许可与源码')
        dialog.geometry('900x700')
        dialog.transient(self.root)
        frame = tk.Frame(dialog, bg=UI_BG)
        frame.pack(fill='both', expand=True)
        text = tk.Text(
            frame, wrap='word', bg=UI_CARD, fg=UI_TEXT,
            font=(UI_FONT, UI_BODY_SIZE), padx=12, pady=10,
            relief='flat', borderwidth=0)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        text.pack(side='left', fill='both', expand=True)
        for name in ('LICENSE', 'THIRD_PARTY_NOTICES.md', 'SOURCE.md'):
            text.insert('end', f'\n===== {name} =====\n\n')
            try:
                with open(resource_path(name), 'r', encoding='utf-8') as handle:
                    text.insert('end', handle.read())
            except OSError as exc:
                text.insert('end', f'无法读取：{exc}\n')
        text.config(state='disabled')
        ttk.Button(
            dialog, text='关闭', command=dialog.destroy,
            style='Secondary.TButton').pack(pady=8)

    def install_font(self):
        if getattr(self, 'font_installing', False):
            return
        self.font_installing = True
        self.btn_font.config(state='disabled', text='处理中…')
        self.font_progress['value'] = 0
        self.font_status.set('正在检查字体…')
        self.font_queue = queue.Queue()

        def worker():
            def log(text):
                self.font_queue.put(('log', text))
            try:
                ok, message = ensure_source_han_font(log)
            except Exception as exc:
                log_exception('安装思源黑体', exc)
                ok, message = False, str(exc)
            self.font_queue.put(('done', ok, message))

        threading.Thread(target=worker, daemon=True).start()
        self._poll_font()

    def _poll_font(self):
        try:
            while True:
                item = self.font_queue.get_nowait()
                if item[0] == 'log':
                    self.font_status.set(item[1])
                    match = re.search(r'下载中 (\d+)%', item[1])
                    if match:
                        self.font_progress['value'] = int(match.group(1))
                else:
                    ok, message = item[1], item[2]
                    self.font_installing = False
                    self.btn_font.config(state='normal', text='检查并安装')
                    self.font_progress['value'] = 100 if ok else 0
                    self.font_status.set(message)
                    self.refresh_font_status()
                    if not ok:
                        messagebox.showerror('字体安装失败', message, parent=self.root)
                    return
        except queue.Empty:
            pass
        self.root.after(100, self._poll_font)

    def check_app_update(self, silent=False):
        if getattr(self, 'app_update_checking', False):
            return
        if silent and time.time() - getattr(self, 'last_update_check', 0) < 6 * 3600:
            # 6 小时内已成功检查过，避免频繁请求 GitHub API
            return
        self.app_update_checking = True
        self.btn_app_update.config(state='disabled', text='检查中…')
        self.app_update_status.set('正在检查软件更新…')
        self.app_update_progress['value'] = 0

        def worker():
            try:
                tag, url, name, digest = fetch_app_update()
                self.app_update_queue.put(
                    ('checked', tag, url, name, digest, '', silent))
            except Exception as exc:
                if not silent:
                    log_exception('检查软件更新', exc)
                self.app_update_queue.put(
                    ('checked', '', '', '', '', str(exc), silent))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_app_update(self):
        try:
            if not self.root.winfo_exists():
                return
            while True:
                item = self.app_update_queue.get_nowait()
                kind = item[0]
                if kind == 'checked':
                    self._app_update_checked(*item[1:])
                elif kind == 'progress':
                    self._app_update_progress(item[1])
                elif kind == 'downloaded':
                    self._app_update_downloaded(item[1])
                elif kind == 'failed':
                    self._app_update_failed(item[1])
        except queue.Empty:
            pass
        self.root.after(150, self._poll_app_update)

    def _app_update_progress(self, text):
        self.app_update_status.set(text)
        match = re.search(r'下载中 (\d+)%', text)
        if match:
            self.app_update_progress['value'] = int(match.group(1))

    def _app_update_checked(self, tag, url, name, digest, error, silent):
        self.app_update_checking = False
        self.btn_app_update.config(state='normal', text='检查软件更新')
        if error:
            self.app_update_status.set('检查失败：' + error)
            if not silent:
                messagebox.showerror('检查更新失败', error, parent=self.root)
            return
        self.app_update_url = url
        self.app_update_digest = digest
        self.last_update_check = time.time()
        if version_tuple(tag) <= version_tuple(APP_VERSION):
            self.app_update_status.set(f'已是最新版本（{APP_VERSION}）')
            if not silent:
                messagebox.showinfo('软件更新', f'当前已是最新版本 {APP_VERSION}', parent=self.root)
            return
        self.app_update_status.set(f'发现新版本 {tag}')
        if not getattr(sys, 'frozen', False):
            if not silent:
                messagebox.showinfo(
                    '软件更新',
                    f'发现新版本 {tag}。\n当前为源码运行模式，无法自动替换，请手动下载。',
                    parent=self.root)
            return
        if messagebox.askyesno(
                '发现新版本',
                f'发现新版本 {tag}（当前 {APP_VERSION}），是否下载并更新？',
                parent=self.root):
            self._start_app_update_download()

    def _start_app_update_download(self):
        if not self.app_update_url:
            return
        self.switch_tab('settings')
        self.btn_app_update.config(state='disabled', text='下载中…')
        self.app_update_status.set('正在下载更新…')
        self.app_update_progress['value'] = 0

        def worker():
            try:
                destination = download_app_update(
                    self.app_update_url,
                    log=lambda text: self.app_update_queue.put(('progress', text)),
                    digest=self.app_update_digest)
                self.app_update_queue.put(('downloaded', destination))
            except Exception as exc:
                log_exception('下载软件更新', exc)
                self.app_update_queue.put(('failed', str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _app_update_downloaded(self, path):
        self.btn_app_update.config(state='normal', text='检查软件更新')
        self.app_update_progress['value'] = 100
        self.app_update_status.set('更新已下载完成')
        if not messagebox.askyesno(
                '更新就绪', '新版本已下载，是否立即重启软件并完成更新？', parent=self.root):
            # 用户选择稍后更新：删除临时下载，避免占满 %TEMP%
            discard_app_update(path)
            self.app_update_status.set('更新已下载，可稍后重新检查并更新')
            return
        ok, message = apply_app_update(path)
        if not ok:
            self.app_update_status.set('更新失败：' + message)
            messagebox.showerror('更新失败', message, parent=self.root)
            return
        self._save_config_and_destroy()

    def _app_update_failed(self, message):
        self.btn_app_update.config(state='normal', text='检查软件更新')
        self.app_update_progress['value'] = 0
        self.app_update_status.set('下载失败：' + message)
        messagebox.showerror('下载失败', message, parent=self.root)

    def update_tools(self):
        if getattr(self, 'sup_panel', None) and self.sup_panel.running:
            messagebox.showinfo('提示', '正在生成字幕，请稍候再更新工具', parent=self.root)
            return
        self.btn_update.config(state='disabled', text='检查中…')
        self.settings_progress['value'] = 0
        self.settings_status.set('开始检查更新…')
        self.update_queue = queue.Queue()

        def worker():
            def log(text):
                self.update_queue.put(('log', text))
            try:
                ok, message = install_all_bdn_tools(log, force=True)
            except Exception as exc:
                log_exception('更新图形字幕工具', exc)
                ok, message = False, str(exc)
            self.update_queue.put(('done', ok, message))

        threading.Thread(target=worker, daemon=True).start()
        self._poll_update()

    def _poll_update(self):
        try:
            while True:
                item = self.update_queue.get_nowait()
                if item[0] == 'log':
                    self.settings_status.set(item[1])
                    match = re.search(r'下载中 (\d+)%', item[1])
                    if match:
                        self.settings_progress['value'] = int(match.group(1)) * 0.9
                else:
                    ok, message = item[1], item[2]
                    self.settings_progress['value'] = 100 if ok else 0
                    self.btn_update.config(state='normal', text='检查更新')
                    self.refresh_tool_status()
                    self.settings_status.set('工具已是最新版本' if ok else f'更新失败：{message}')
                    if not ok:
                        messagebox.showerror('更新失败', message, parent=self.root)
                    return
        except queue.Empty:
            pass
        self.root.after(100, self._poll_update)

    def open_black_detector(self):
        if Image is None:
            messagebox.showerror(
                "缺少依赖",
                "黑边检测需要 Pillow，请先运行：pip install pillow",
            )
            return
        try:
            if self.detector_window and self.detector_window.window.winfo_exists():
                self.detector_window.window.lift()
                self.detector_window.window.focus_force()
                return
        except tk.TclError:
            pass
        self.detector_window = BlackBarDetectorWindow(
            self.root, on_mid_black=self.is_mid_black.set,
            on_bar_height=self._on_bar_height)

    def _on_bar_height(self, value):
        try:
            self.mid_black_px = max(0.0, min(200.0, float(value)))
        except (TypeError, ValueError):
            return

    def open_style_preview(self):
        if Image is None or ImageDraw is None or ImageTk is None:
            messagebox.showerror(
                "缺少依赖", "样式预览需要 Pillow，请先运行：pip install pillow")
            return
        try:
            brightness = float(self.hdr_brightness.get().strip())
        except (TypeError, ValueError):
            brightness = HDR_SUB_BRIGHTNESS
        if not 1 <= brightness <= 1000:
            brightness = HDR_SUB_BRIGHTNESS
        try:
            if self.preview_window and self.preview_window.window.winfo_exists():
                self.preview_window.brightness = brightness
                self.preview_window.hdr_var.set(self.is_HDR.get())
                self.preview_window.is_mid_black = bool(self.is_mid_black.get())
                self.preview_window.style_lines = self._style_lines_list()
                self.preview_window.fsvp_values = self._fsvp_values()
                self.preview_window.redraw()
                self.preview_window.window.lift()
                self.preview_window.window.focus_force()
                return
        except (AttributeError, tk.TclError):
            pass
        self.preview_window = StylePreviewWindow(
            self.root, is_hdr=self.is_HDR.get(),
            is_mid_black=self.is_mid_black.get(), brightness=brightness,
            bar_px=self.mid_black_px, style_lines=self._style_lines_list(),
            fsvp_values=self._fsvp_values(),
            selected_kind=self.active_style_kind)
        self.preview_window.window.lift()
        self.preview_window.window.focus_force()

    def _dropped_paths(self, event):
        return split_dropped_paths(self.root, event.data)

    def _on_ass_drop(self, event):
        if self.processing:
            self.status_var.set('正在生成，请稍候')
            return 'break'
        paths = self._dropped_paths(event)
        files = [path for path in paths if path.lower().endswith(('.ass', '.srt'))]
        if not files:
            self.status_var.set('请拖入 .ass 或 .srt 字幕文件')
            return 'break'
        self.file_entry.set_value(normalize_display_path(files[0]))
        if self.text_out_auto or not self.output_root_entry.get_value():
            self.output_root_entry.set_value(
                normalize_display_path(os.path.dirname(os.path.abspath(files[0]))))
            self.text_out_auto = True
        if len(files) > 1:
            self.status_var.set(f'一次生成一个字幕，已载入第一个（共 {len(files)} 个）')
        else:
            self.status_var.set('已载入字幕文件')
        return 'break'

    def make_option_check(self, parent, text, variable):
        return tk.Checkbutton(
            parent, text=text, variable=variable,
            bg=UI_CARD, fg=UI_TEXT, activebackground=UI_CARD, activeforeground=UI_TEXT,
            selectcolor=UI_CARD, font=(UI_FONT, UI_BODY_SIZE),
            highlightthickness=0, bd=0, padx=0, cursor='hand2',
        )

    def update_brightness_state(self, *args):
        enabled = bool(self.is_HDR.get()) and not self.processing
        self.hdr_brightness_entry.config(state='normal' if enabled else 'disabled')

    def _normalize_text_entries(self, event=None):
        for entry in (self.file_entry, self.output_root_entry):
            value = entry.get_value()
            if value:
                normalized = normalize_display_path(value)
                if normalized != value:
                    entry.set_value(normalized)

    def _apply_text_source(self, path):
        path = normalize_display_path(path)
        self.file_entry.set_value(path)
        if self.text_out_auto or not self.output_root_entry.get_value():
            self.output_root_entry.set_value(
                os.path.dirname(os.path.abspath(path)))
            self.text_out_auto = True

    def openFile(self):
        if self.processing:
            return
        file_path = filedialog.askopenfilename(
            initialdir=last_directory('text_file', self.file_entry.get_value()),
            filetypes=[
                ("字幕文件", "*.ass *.srt"),
                ("ASS 字幕", "*.ass"),
                ("SRT 字幕", "*.srt"),
                ("所有文件", "*.*"),
            ])
        if file_path:
            remember_directory('text_file', file_path)
            self._apply_text_source(file_path)
            self.status_var.set('已载入字幕文件')

    def _set_processing_state(self, processing: bool):
        self.processing = processing
        state = 'disabled' if processing else 'normal'
        self.btn_process.config(state=state, text='生成中...' if processing else '开始生成')
        self.btn_cancel.config(state='normal' if processing else 'disabled')
        self.btn_output_open.config(state=state)
        self.btn_black.config(state=state)
        self.btn_open.config(state=state)
        if hasattr(self, 'btn_choose_output'):
            self.btn_choose_output.config(state=state)
        for widget in (
                self.file_entry, self.output_root_entry, self.title,
                self.script_updated_by, self.original_script,
                self.original_translation, self.original_timing,
                self.original_editing):
            widget.config(state=state)
        self.option_is_HDR.config(state=state)
        self.option_is_mid_black.config(state=state)
        self.option_use_taiwan.config(state=state)
        self.update_brightness_state()
        self.process_progress['value'] = 0
        if processing:
            self.process_percent.set('0%')
            self.status_var.set('正在生成字幕，请稍候...')
        else:
            self.process_percent.set('')

    def _process_event(self, kind, *payload):
        if kind == 'progress':
            self._update_process_progress(*payload)
            return True
        if kind == 'error':
            self._process_finished(None, payload[0], False)
            return False
        self._process_finished(payload[0], payload[1], payload[2])
        return False

    def _update_process_progress(self, value, message=''):
        value = max(0, min(100, int(value)))
        self.process_progress['value'] = value
        self.process_percent.set(f'{value}%')
        if message:
            self.status_var.set(message)

    def cancel_process(self):
        if not self.processing:
            return
        self.process_cancel.set()
        self.btn_cancel.config(state='disabled')
        self.status_var.set('正在取消…')

    def _process_finished(self, result, error, is_hdr):
        try:
            if not self.root.winfo_exists():
                return
        except tk.TclError:
            return
        self._set_processing_state(False)
        if error == 'cancelled':
            self.status_var.set('已取消生成')
            messagebox.showinfo(
                "已取消",
                "生成已取消。输出文件夹中可能保留了已经写出的字幕文件，建议重新生成一次。",
                parent=self.root)
            return
        if error:
            show_error_dialog(
                "生成失败",
                f"生成失败：{error}\n\n详细信息已写入日志，可点击「打开日志」查看。",
                getattr(self, 'last_error_log', ''), self.root)
            return
        html_path, output_dir, chinese_only = result
        self.last_output_dir = output_dir
        self._ensure_page('fix')
        auto_loaded = False
        fixer = getattr(self, 'fixer_panel', None)
        json_path = os.path.join(output_dir, NOTICES_DATA_NAME)
        if (fixer and not fixer.running and not fixer.dirty
                and os.path.isfile(json_path)):
            fixer.load(json_path, silent=True)
            auto_loaded = bool(fixer.entries)
        message = build_success_message(html_path, output_dir, chinese_only, is_hdr)
        if auto_loaded:
            message += f'\n\n问题数据已自动载入「问题修复」页面（{len(fixer.entries)} 条）。'
        messagebox.showinfo("生成完成", message, parent=self.root)
        if auto_loaded and fixer.entries:
            if messagebox.askyesno(
                    '问题修复',
                    f'要现在进入「问题修复」页面处理这 {len(fixer.entries)} 条吗？',
                    parent=self.root):
                self.switch_tab('fix')
        self.status_var.set('生成完成')

    def start_process(self):
        if self.processing:
            return
        file_name = normalize_display_path(self.file_entry.get_value())
        if not os.path.isabs(file_name):
            tk.messagebox.showinfo("提示", "未选择文件")
            return

        meta = {
            'title': self.title.get_value(),
            'original_script': self.original_script.get_value(),
            'original_translation': self.original_translation.get_value(),
            'original_timing': self.original_timing.get_value(),
            'original_editing': self.original_editing.get_value(),
            'script_updated_by': self.script_updated_by.get_value(),
            'taiwan': self.use_taiwan.get(),
            'output_root': normalize_display_path(
                self.output_root_entry.get_value()),
            'style_fsvp': self._fsvp_values(),
            'style_lines': self._style_lines_list(),
            'glossary': self.glossary_panel.get_entries(),
        }
        if self.is_HDR.get():
            try:
                brightness = float(self.hdr_brightness.get().strip())
            except ValueError:
                brightness = 0.0
            if not 1 <= brightness <= 1000:
                messagebox.showinfo(
                    "提示", "字幕亮度请填写 1 到 1000 之间的数字（单位 nit）", parent=self.root)
                return
            meta['hdr_brightness'] = brightness
        else:
            # 非 HDR 模式不校验亮度输入
            meta['hdr_brightness'] = HDR_SUB_BRIGHTNESS

        title = meta['title']
        updated_by = meta['script_updated_by']
        if not title:
            messagebox.showinfo("提示", "影片名为必填项，请填写后再生成", parent=self.root)
            return
        if not updated_by:
            messagebox.showinfo("提示", "字幕员为必填项，请填写后再生成", parent=self.root)
            return

        output_dir = get_output_dir(file_name, title, meta['output_root'])
        if output_dir is None:
            messagebox.showinfo("提示", "影片名不能用于文件夹名，请修改", parent=self.root)
            return

        if self.is_HDR.get():
            expected = [
                '1-简英双语-HDR-zh.ass',
                '2-简英双语-SDR-zh.ass',
                '3-简体中文-HDR-zh.ass',
                '4-繁体中文-HDR-zh.ass',
                '5-英文-HDR-en.ass',
                '1-简体中文-HDR-zh.ass',
                '2-简体中文-SDR-zh.ass',
                '3-繁体中文-HDR-zh.ass',
            ]
        else:
            expected = [
                '1-简英双语-zh.ass',
                '2-简体中文-zh.ass',
                '3-繁体中文-zh.ass',
                '4-英文-en.ass',
                '1-简体中文-zh.ass',
                '2-繁体中文-zh.ass',
            ]
        expected += [
            NOTICES_REPORT_NAME,
            TAIWAN_REPORT_NAME,
        ]
        existing = [
            name for name in expected
            if os.path.exists(os.path.join(output_dir, name))
        ]
        if existing:
            preview = '\n'.join(existing)
            if not messagebox.askyesno(
                "确认覆盖",
                f"输出文件夹中已存在以下文件：\n{preview}\n\n"
                f"继续生成可能覆盖同名文件，是否继续？",
                parent=self.root,
            ):
                return

        config = load_config()
        config.update(self._current_config_values())
        save_config(config)

        self._set_processing_state(True)
        self.process_cancel = threading.Event()
        is_hdr = bool(self.is_HDR.get())
        is_mid_black = bool(self.is_mid_black.get())
        cancel_event = self.process_cancel

        def work(emit):
            try:
                result = process_file(
                    file_name, is_hdr, is_mid_black, meta,
                    progress=lambda value, message='': emit(
                        'progress', value, message),
                    cancel_event=cancel_event,
                )
                error = None
            except ProcessCancelled:
                result = None
                error = 'cancelled'
            except Exception as exc:
                log_exception('生成字幕', exc)
                self.last_error_log = ERROR_LOG_PATH
                result = None
                error = str(exc)
            emit('result', result, error, is_hdr)

        self.process_runner.start(work, self._process_event)
