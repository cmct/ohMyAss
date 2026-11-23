"""图形字幕面板、任务队列与高级参数对话框。"""


import os
import re
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from config_store import (
    last_directory, load_config, log_exception, normalize_display_path,
    remember_directory, save_config,
)

import theme_ui
from theme_ui import (
    CURRENT_DARK_THEME, ToolTip, UI_ACCENT, UI_BG,
    UI_BODY_SIZE, UI_BORDER, UI_CARD, UI_FIELD,
    UI_FIELD_BORDER, UI_FONT, UI_MUTED, UI_SELECT,
    UI_SMALL_SIZE, UI_TEXT, add_combo_arrow, center_window,
    create_card, create_hero, hex_to_rgb, prepare_combobox,
    px,
)
from bdn_sup import (
    BDN_FORMATS, BDN_FORMAT_FPS, BDN_FPS, BDN_PARAM_DEFAULTS,
    BDN_PARAM_HELP, BDN_QUANTIZERS, RenderCancelled, cleanup_bdn_outputs,
    ass_vertical_font_names, generate_bdn_sup, missing_ass_fonts,
    sup_to_pes, validate_bdn_format, validate_bdn_sup,
    vsfilter_available,
)
from ui_widgets import attach_entry_placeholder, enable_file_drop, split_dropped_paths

theme_ui.register_theme_namespace(globals())


class BdnAdvancedDialog(tk.Toplevel):
    """SUPer 高级参数设置窗口，所有参数均带悬停说明。"""

    LOG_OPTIONS = (
        ('关闭', 'off'),
        ('精简 (-10)', '-10'),
        ('标准 (20)', '20'),
        ('极简 (25)', '25'),
        ('仅警告/错误 (30)', '30'),
        ('调试 (10)', '10'),
        ('最大调试 (5)', '5'),
    )

    def __init__(self, master, params: dict):
        super().__init__(master)
        self.title('高级参数（SUPer）')
        self.resizable(False, False)
        self.result = None
        self.vars = {}
        self.transient(master.winfo_toplevel())
        self.configure(bg=UI_CARD)
        self.geometry(f'{px(640)}x{px(730)}')

        tk.Label(
            self, text='鼠标移到参数名称或输入框上可查看说明',
            bg=UI_CARD, fg=UI_MUTED, font=(UI_FONT, UI_SMALL_SIZE),
        ).pack(anchor='w', padx=18, pady=(14, 0))

        body = tk.Frame(self, bg=UI_CARD, padx=16, pady=12)
        body.pack(fill='both', expand=True)
        body.columnconfigure(1, weight=1)

        row = 0
        self.add_entry(body, row, '压缩率 [0-100]', 'compression', params)
        row += 1
        self.add_entry(body, row, '采集率 [0-100]', 'acqrate', params)
        row += 1
        self.add_combo(body, row, '色彩空间', 'color', ('bt709', 'bt601', 'bt2020'), params,
                       display={'bt709': 'bt709（高清）', 'bt601': 'bt601（标清）', 'bt2020': 'bt2020（UHD/HDR）'})
        row += 1
        self.add_combo(body, row, '量化器', 'quantizer', [item[1] for item in BDN_QUANTIZERS], params,
                       display={item[1]: item[0] for item in BDN_QUANTIZERS})
        row += 1
        self.add_combo(body, row, '线程数', 'threads', ['auto', '1', '2', '3', '4', '5', '6', '7', '8'], params)
        row += 1
        for key, label in (
            ('allow_normal', '允许普通情况对象重定义'),
            ('prefer_normal', '优先普通情况对象重定义'),
            ('buffer', '允许调色板更新缓冲'),
            ('full_palette', '写入完整调色板'),
        ):
            self.add_check(body, row, label, key, params)
            row += 1
        self.add_entry(body, row, '调色板更新后插入采集 [0 关闭]', 'extra_acq', params)
        row += 1
        self.add_entry(body, row, '锚点间隔（秒）[0 关闭]', 'redraw_period', params)
        row += 1
        self.add_entry(body, row, 'SSIM 容差偏移 [-100~100]', 'ssim_tol', params)
        row += 1
        self.add_entry(body, row, '最大码率测试（Kbps，留空关闭）', 'max_kbps', params)
        row += 1
        self.add_combo(body, row, '日志级别', 'log_level',
                       [item[1] for item in self.LOG_OPTIONS], params,
                       display={item[1]: item[0] for item in self.LOG_OPTIONS})

        buttons = ttk.Frame(body, style='Card.TFrame')
        buttons.grid(row=row + 1, column=0, columnspan=2, sticky='e', pady=(16, 0))
        ttk.Button(buttons, text='恢复默认', command=self.reset_defaults, style='Secondary.TButton').pack(side='left', padx=(0, 8))
        ttk.Button(buttons, text='取消', command=self.destroy, style='Secondary.TButton').pack(side='left', padx=(0, 8))
        ttk.Button(buttons, text='确定', command=self.apply, style='Accent.TButton').pack(side='left')

        self.bind('<Escape>', lambda event: self.destroy())
        self.update_idletasks()
        self.grab_set()
        center_window(self, master)

    def add_label(self, parent, row, text, key):
        label = ttk.Label(parent, text=text, style='Card.TLabel')
        label.grid(row=row, column=0, sticky='w', padx=(0, 12), pady=5)
        ToolTip(label, BDN_PARAM_HELP.get(key, ''))
        return label

    def add_entry(self, parent, row, text, key, params):
        self.add_label(parent, row, text, key)
        var = tk.StringVar(value=str(params.get(key, '')))
        entry = ttk.Entry(parent, textvariable=var, width=14, justify='center')
        entry.grid(row=row, column=1, sticky='w', pady=5)
        ToolTip(entry, BDN_PARAM_HELP.get(key, ''))
        self.vars[key] = (var, 'entry')

    def add_combo(self, parent, row, text, key, values, params, display=None):
        self.add_label(parent, row, text, key)
        raw = str(params.get(key, values[0]))
        shown = (display or {}).get(raw, raw)
        var = tk.StringVar(value=shown)
        combo = add_combo_arrow(prepare_combobox(ttk.Combobox(parent, textvariable=var, values=[(display or {}).get(v, v) for v in values],
                             state='readonly', width=28)))
        combo.grid(row=row, column=1, sticky='w', pady=5)
        ToolTip(combo, BDN_PARAM_HELP.get(key, ''))
        self.vars[key] = (var, 'combo', values, display or {})

    def add_check(self, parent, row, text, key, params):
        var = tk.BooleanVar(value=bool(params.get(key)))
        check = tk.Checkbutton(
            parent, text=text, variable=var, bg=UI_FIELD, fg=UI_TEXT,
            activebackground=UI_FIELD, activeforeground=UI_TEXT,
            selectcolor=UI_FIELD,
            font=(UI_FONT, UI_BODY_SIZE), highlightthickness=0, bd=0, padx=0, cursor='hand2',
        )
        check.grid(row=row, column=0, columnspan=2, sticky='w', pady=4)
        ToolTip(check, BDN_PARAM_HELP.get(key, ''))
        self.vars[key] = (var, 'check')

    def reset_defaults(self):
        for key, item in self.vars.items():
            kind = item[1]
            default = BDN_PARAM_DEFAULTS[key]
            if kind == 'check':
                item[0].set(bool(default))
            elif kind == 'combo':
                display = item[3]
                item[0].set(display.get(str(default), str(default)))
            else:
                item[0].set(str(default))

    def collect(self) -> dict:
        result = {}
        for key, item in self.vars.items():
            kind = item[1]
            if kind == 'check':
                result[key] = bool(item[0].get())
            elif kind == 'combo':
                shown = item[0].get()
                reverse = {v: k for k, v in item[3].items()}
                result[key] = reverse.get(shown, shown)
            else:
                result[key] = item[0].get().strip()

        def as_int(key, minimum, maximum, label):
            value = result[key]
            try:
                number = int(value)
            except ValueError:
                raise ValueError(f'{label} 需要填写整数')
            if not minimum <= number <= maximum:
                raise ValueError(f'{label} 超出范围（{minimum}-{maximum}）')
            result[key] = str(number)

        as_int('compression', 0, 100, '压缩率')
        as_int('acqrate', 0, 100, '采集率')
        as_int('extra_acq', 0, 99, '调色板更新后插入采集')
        as_int('ssim_tol', -100, 100, 'SSIM 容差偏移')

        try:
            redraw = float(result['redraw_period'] or 0)
        except ValueError:
            raise ValueError('锚点间隔需要填写数字')
        if redraw != 0 and not 1.0 <= redraw <= 3600:
            raise ValueError('锚点间隔需为 0，或 1-3600 秒')
        result['redraw_period'] = f'{redraw:g}'

        max_kbps = result['max_kbps']
        if max_kbps:
            as_int('max_kbps', 0, 48000, '最大码率测试')
        return result

    def apply(self):
        try:
            self.result = self.collect()
        except ValueError as exc:
            messagebox.showinfo('参数有误', str(exc), parent=self)
            return
        self.destroy()


class QueueList(tk.Frame):
    """自绘队列：每条字幕占一行，整行按进度从左到右渐变填充。"""

    ROW_HEIGHT = px(24)
    VISIBLE_ROWS = 6
    FILL_END = (147, 197, 253)

    def __init__(self, master, **kwargs):
        super().__init__(master, bg=UI_CARD, **kwargs)
        self.items = []
        self.selected = set()
        self.offset = 0
        self.running = False
        self._progress_redraw_pending = False
        self._gradient_cache = None

        body = tk.Frame(self, bg=UI_CARD)
        body.pack(fill='both', expand=True)
        body.columnconfigure(0, weight=1)
        self.header_canvas = tk.Canvas(
            body, height=px(22), bg=UI_CARD, highlightthickness=0, bd=0)
        self.header_canvas.grid(row=0, column=0, sticky='ew')
        self.canvas = tk.Canvas(
            body, height=self.ROW_HEIGHT * self.VISIBLE_ROWS, bg=UI_FIELD,
            highlightthickness=px(1), highlightbackground=UI_FIELD_BORDER,
            highlightcolor=UI_ACCENT, takefocus=1, bd=0)
        self.canvas.grid(row=1, column=0, sticky='nsew')
        self.scrollbar = ttk.Scrollbar(
            body, orient='vertical', command=self._on_scroll)
        self.scrollbar.grid(row=1, column=1, sticky='ns')
        self.header_canvas.bind('<Configure>', lambda event: self._draw_header())
        self.canvas.bind('<Configure>', lambda event: self.redraw())
        self.canvas.bind('<Button-1>', self._on_click)
        self.canvas.bind(
            '<Button-1>', lambda event: self.canvas.focus_set(), add='+')
        self.canvas.bind('<MouseWheel>', self._on_wheel)
        self.canvas.bind('<Delete>', lambda event: self.remove_selected())
        self.canvas.bind('<Control-a>', lambda event: self.select_all())
        self.queue_menu = tk.Menu(self.canvas, tearoff=0)
        self.queue_menu.add_command(
            label='从队列移除', command=self.remove_selected)
        self.canvas.bind('<Button-3>', self._show_queue_menu)
        self.redraw()
        self._draw_header()

    def _draw_header(self):
        canvas = self.header_canvas
        canvas.delete('all')
        width = max(1, canvas.winfo_width())
        middle = px(11)
        canvas.create_text(
            px(8), middle, text='ASS 队列（添加或拖入 .ass）', anchor='w',
            fill=UI_MUTED, font=(UI_FONT, UI_SMALL_SIZE))
        canvas.create_text(
            width - px(78), middle, text='状态', anchor='e',
            fill=UI_MUTED, font=(UI_FONT, UI_SMALL_SIZE))
        canvas.create_text(
            width - px(8), middle, text='进度', anchor='e',
            fill=UI_MUTED, font=(UI_FONT, UI_SMALL_SIZE))

    def count(self):
        return len(self.items)

    def set_running(self, running):
        self.running = bool(running)
        self.redraw()

    def paths(self):
        return [item['path'] for item in self.items]

    def append(self, paths):
        added = 0
        existing = {os.path.normcase(item['path']) for item in self.items}
        for raw in paths:
            path = os.path.abspath(str(raw))
            key = os.path.normcase(path)
            if not path.lower().endswith('.ass') or key in existing:
                continue
            self.items.append({
                'path': path,
                'name': os.path.basename(path),
                'status': '等待',
                'progress': 0,
            })
            existing.add(key)
            added += 1
        if added:
            self.offset = max(0, min(self.offset, max(0, len(self.items) - self.VISIBLE_ROWS)))
            self.redraw()
        return added

    def set_status(self, index, text):
        if 0 <= index < len(self.items):
            if self.items[index]['status'] == text:
                return
            self.items[index]['status'] = text
            self._ensure_visible(index)
            self.redraw()

    def _schedule_progress_redraw(self):
        if self._progress_redraw_pending:
            return
        self._progress_redraw_pending = True
        self.after(60, self._flush_progress_redraw)

    def _flush_progress_redraw(self):
        self._progress_redraw_pending = False
        try:
            self.redraw()
        except tk.TclError:
            pass

    def set_progress(self, index, value):
        if not (0 <= index < len(self.items)):
            return
        try:
            progress = max(0, min(100, int(float(value)))) if value is not None else 0
        except (TypeError, ValueError):
            progress = 0
        if self.items[index]['progress'] == progress:
            return
        self.items[index]['progress'] = progress
        # 渲染进度回调频率较高，合并到一次重绘，避免队列闪烁
        self._schedule_progress_redraw()

    def remove_selected(self):
        if self.running or not self.selected:
            return 0
        indexes = sorted(self.selected, reverse=True)
        for index in indexes:
            if 0 <= index < len(self.items):
                del self.items[index]
        self.selected.clear()
        self.offset = max(0, min(self.offset, max(0, len(self.items) - self.VISIBLE_ROWS)))
        self.redraw()
        return len(indexes)

    def clear(self):
        self.items = []
        self.selected.clear()
        self.offset = 0
        self.redraw()

    def select_all(self):
        self.selected = set(range(len(self.items)))
        self.redraw()
        return 'break'

    def _ensure_visible(self, index):
        if index < self.offset:
            self.offset = index
        elif index >= self.offset + self.VISIBLE_ROWS:
            self.offset = index - self.VISIBLE_ROWS + 1
        self.offset = max(0, min(self.offset, max(0, len(self.items) - self.VISIBLE_ROWS)))

    def _on_click(self, event):
        index = self.offset + int(event.y // self.ROW_HEIGHT)
        if not (0 <= index < len(self.items)):
            self.selected.clear()
            self.redraw()
            return
        if event.state & 0x0004:  # Control
            if index in self.selected:
                self.selected.remove(index)
            else:
                self.selected.add(index)
        else:
            self.selected = {index}
        self.redraw()

    def _on_wheel(self, event):
        delta = -1 if event.delta > 0 else 1
        max_offset = max(0, len(self.items) - self.VISIBLE_ROWS)
        self.offset = max(0, min(max_offset, self.offset + delta))
        self.redraw()

    def _show_queue_menu(self, event):
        index = self.offset + int(event.y // self.ROW_HEIGHT)
        if not (0 <= index < len(self.items)):
            return
        if index not in self.selected:
            self.selected = {index}
            self.redraw()
        self.queue_menu.entryconfigure(
            0, state='disabled' if self.running else 'normal')
        try:
            self.queue_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.queue_menu.grab_release()

    def _on_scroll(self, action, *args):
        max_offset = max(0, len(self.items) - self.VISIBLE_ROWS)
        if action == 'moveto' and args:
            self.offset = int(float(args[0]) * max_offset)
        elif action == 'scroll' and args:
            step = int(args[1])
            self.offset += step
        self.offset = max(0, min(max_offset, self.offset))
        self.redraw()

    def _sync_scroll(self):
        total = max(1, len(self.items))
        first = self.offset / total
        last = min(1.0, (self.offset + self.VISIBLE_ROWS) / total)
        self.scrollbar.set(first, last)

    def _gradient_bounds(self):
        key = bool(CURRENT_DARK_THEME)
        if self._gradient_cache is None or self._gradient_cache[0] != key:
            if key:
                start = hex_to_rgb(UI_FIELD)
                end = hex_to_rgb(UI_ACCENT)
            else:
                start = (255, 255, 255)
                end = self.FILL_END
            self._gradient_cache = (key, start, end)
        return self._gradient_cache[1], self._gradient_cache[2]

    def _gradient(self, ratio):
        ratio = max(0.0, min(1.0, ratio))
        start, end = self._gradient_bounds()
        red = int(start[0] + (end[0] - start[0]) * ratio)
        green = int(start[1] + (end[1] - start[1]) * ratio)
        blue = int(start[2] + (end[2] - start[2]) * ratio)
        return f'#{red:02x}{green:02x}{blue:02x}'

    def redraw(self):
        self.canvas.delete('all')
        width = max(1, self.canvas.winfo_width())
        for row in range(self.VISIBLE_ROWS):
            index = self.offset + row
            top = row * self.ROW_HEIGHT
            bottom = top + self.ROW_HEIGHT
            self.canvas.create_rectangle(
                0, top, width, bottom, fill=UI_FIELD, outline='')
            if index >= len(self.items):
                continue
            item = self.items[index]
            progress = item.get('progress') or 0
            if progress > 0:
                filled = int(width * progress / 100.0)
                steps = 24
                for step in range(steps):
                    x1 = int(filled * step / steps)
                    x2 = int(filled * (step + 1) / steps)
                    if x2 <= x1:
                        continue
                    ratio = (x2 / width) if width else 0
                    self.canvas.create_rectangle(
                        x1, top, x2, bottom, fill=self._gradient(ratio), outline='')
            if index in self.selected and not self.running:
                self.canvas.create_rectangle(
                    0, top, width, bottom, fill=UI_SELECT, outline='')
            self.canvas.create_line(
                0, bottom, width, bottom, fill=UI_BORDER)
            name = item.get('name') or os.path.basename(item['path'])
            available = max(px(80), width - px(170))
            limit = max(8, int(available / max(1, px(8))))
            if len(name) > limit:
                name = name[:limit - 1] + '…'
            self.canvas.create_text(
                px(8), (top + bottom) / 2, text=name, anchor='w',
                fill=UI_TEXT, font=(UI_FONT, UI_SMALL_SIZE))
            self.canvas.create_text(
                width - px(78), (top + bottom) / 2, text=item.get('status', ''),
                anchor='e', fill=UI_TEXT, font=(UI_FONT, UI_SMALL_SIZE))
            percent = f'{int(progress)}%' if progress > 0 else ''
            self.canvas.create_text(
                width - px(8), (top + bottom) / 2, text=percent,
                anchor='e', fill=UI_TEXT, font=(UI_FONT, UI_SMALL_SIZE))
        self._sync_scroll()


def build_output_dirs(paths, out_root=''):
    """为每个 ASS 计算输出文件夹；不同目录下的同名文件自动加序号。"""
    jobs = []
    used = set()
    for path in paths:
        base = out_root or os.path.dirname(os.path.abspath(path))
        stem = os.path.splitext(os.path.basename(path))[0]
        out_dir = os.path.join(base, stem + '_BDN')
        counter = 2
        while out_dir.casefold() in used:
            out_dir = os.path.join(base, f'{stem}_{counter}_BDN')
            counter += 1
        used.add(out_dir.casefold())
        jobs.append((path, out_dir))
    return jobs


class BdnSupPanel(ttk.Frame):
    """SUP 生成页：调用 ass2bdnxml + SUPer，工具在线更新。"""

    def __init__(self, master):
        super().__init__(master, style='App.TFrame')
        self.queue = queue.Queue()
        self.running = False
        self.cancel_event = threading.Event()
        self.started_at = None
        self.sup_out_auto = True
        self.sup_jobs = []
        self.sup_job_index = 0
        self.sup_batch = False
        self.sup_batch_success = 0
        self.sup_batch_failures = []
        self.current_sup_job = None
        config = load_config()
        self.params = dict(BDN_PARAM_DEFAULTS)
        saved_params = config.get('sup_params')
        if isinstance(saved_params, dict):
            self.params.update(
                {key: value for key, value in saved_params.items()
                 if key in BDN_PARAM_DEFAULTS})
        self.build_ui()
        self._restore_options(config)

    def build_ui(self):
        create_hero(
            self, '图形字幕',
            '由 ASS 生成 BDN XML + PNG，并编码 SUP、PES + MUI；也可把已有 SUP 转为 PES。',
        ).pack(fill='x', pady=(0, 12))

        file_outer, file_body = create_card(self, '字幕与输出')
        file_outer.pack(fill='x', pady=(0, 10))
        file_body.columnconfigure(1, weight=1)

        self.out_var = tk.StringVar(value='')

        def file_label(text, row):
            tk.Label(
                file_body, text=text, bg=UI_CARD, fg=UI_TEXT, font=(UI_FONT, UI_BODY_SIZE),
            ).grid(row=row, column=0, sticky='w', padx=(0, 10), pady=4)

        file_label('字幕文件', 0)
        self.ass_queue_list = QueueList(file_body)
        self.ass_queue_list.grid(row=0, column=1, sticky='ew', pady=4)
        enable_file_drop(self.ass_queue_list.canvas, self._on_drop_ass)
        queue_buttons = tk.Frame(file_body, bg=UI_CARD)
        queue_buttons.grid(row=0, column=2, sticky='new', padx=(10, 0), pady=(26, 4))
        self.btn_choose_ass = ttk.Button(
            queue_buttons, text='添加', command=self.choose_ass,
            style='Secondary.TButton')
        self.btn_choose_ass.grid(row=0, column=0, sticky='ew')
        self.btn_ass_queue_remove = ttk.Button(
            queue_buttons, text='移除', command=self.remove_selected_ass_queue,
            style='Secondary.TButton')
        self.btn_ass_queue_remove.grid(row=1, column=0, sticky='ew', pady=(4, 0))
        self.btn_ass_queue_clear = ttk.Button(
            queue_buttons, text='清空', command=self.clear_ass_queue,
            style='Secondary.TButton')
        self.btn_ass_queue_clear.grid(row=2, column=0, sticky='ew', pady=(4, 0))

        file_label('输出文件夹', 1)
        self.out_entry = ttk.Entry(file_body, textvariable=self.out_var)
        self.out_entry.grid(row=1, column=1, sticky='ew', pady=4)
        self.out_entry.bind(
            '<Key>', lambda event: setattr(self, 'sup_out_auto', False), add='+')
        self.out_entry.bind(
            '<FocusOut>',
            lambda event: self.out_var.set(
                normalize_display_path(self.out_var.get())), add='+')
        attach_entry_placeholder(
            self.out_entry, self.out_var,
            '生成到 <输出文件夹>/<字幕名>_BDN；默认 <输出文件夹> 为字幕所在文件夹')
        self.btn_choose_out = ttk.Button(
            file_body, text='选择', command=self.choose_out, style='Secondary.TButton')
        self.btn_choose_out.grid(
            row=1, column=2, sticky='ew', padx=(10, 0), pady=4)

        option_outer, option_card = create_card(self, '生成选项')
        option_outer.pack(fill='x', pady=(0, 10))
        for column in range(3):
            option_card.columnconfigure(column, weight=1)

        self.fmt_var = tk.StringVar(value='1080p')
        self.fps_var = tk.StringVar(value='23.976')
        self.lang_var = tk.StringVar(value='chi')
        self.make_sup_var = tk.BooleanVar(value=True)
        self.make_pes_var = tk.BooleanVar(value=False)
        self.cleanup_bdn_var = tk.BooleanVar(value=False)

        def option_label(text, row, column, padx=(0, 0)):
            tk.Label(
                option_card, text=text, bg=UI_CARD, fg=UI_TEXT, font=(UI_FONT, UI_BODY_SIZE),
            ).grid(row=row, column=column, sticky='w', padx=padx, pady=(0, 3))

        option_label('视频格式', 0, 0)
        self.fmt_combo = add_combo_arrow(prepare_combobox(ttk.Combobox(option_card, textvariable=self.fmt_var, values=BDN_FORMATS, state='readonly', width=12)))
        self.fmt_combo.grid(row=1, column=0, sticky='w', pady=(0, 8))
        ToolTip(self.fmt_combo, 'BDN 视频格式：1080p / 1080i / 720p / 576i / 480i。\n'
                           '普通蓝光电影选 1080p；720p 片源选 720p；标清 DVD 源选 576i/480i。')
        option_label('帧率', 0, 1, padx=(14, 0))
        self.fps_combo = add_combo_arrow(prepare_combobox(ttk.Combobox(option_card, textvariable=self.fps_var, values=BDN_FPS, state='readonly', width=10)))
        self.fps_combo.grid(row=1, column=1, sticky='w', padx=(14, 0), pady=(0, 8))
        self.fmt_var.trace_add('write', self._sync_fps_choices)
        self.fps_var.trace_add('write', self._sync_fps_choices)
        self._sync_fps_choices()
        ToolTip(self.fps_combo, 'BDN 帧率，必须与视频一致：23.976 / 24 / 25 / 29.97 / 50 / 59.94。\n'
                           '23.976 最常见；25 用于欧洲片源；59.94 仅 UHD 可用。')
        option_label('语言代码', 0, 2, padx=(14, 0))
        lang_entry = ttk.Entry(option_card, textvariable=self.lang_var, width=10)
        lang_entry.grid(row=1, column=2, sticky='w', padx=(14, 0), pady=(0, 8))
        ToolTip(lang_entry, 'BDN 元数据语言代码（ISO 639-2）：chi/zho 中文、eng 英文、jpn 日文。\n'
                            '只写入 bdn.xml 的 <Language Code>，不影响 SUP/PES 码流；\n'
                            '封装 MKV 或 Authoring 蓝光时需在对应软件里再设置轨道语言。')

        toggle_frame = tk.Frame(option_card, bg=UI_CARD)
        toggle_frame.grid(row=2, column=0, columnspan=3, sticky='w', pady=(8, 0))
        tk.Label(
            toggle_frame, text='渲染链：SUPer（BDN + SUP）', bg=UI_CARD, fg=UI_MUTED,
            font=(UI_FONT, UI_BODY_SIZE),
        ).pack(side='left', padx=(0, px(18)))
        self.make_sup_check = tk.Checkbutton(
            toggle_frame, text='同时生成 SUP', variable=self.make_sup_var,
            bg=UI_CARD, fg=UI_TEXT, activebackground=UI_CARD, activeforeground=UI_TEXT,
            selectcolor=UI_CARD, font=(UI_FONT, UI_BODY_SIZE),
            highlightthickness=0, bd=0, padx=0, cursor='hand2',
        )
        self.make_sup_check.pack(side='left')
        ToolTip(self.make_sup_check,
                '勾选后用 SUPer 把 BDN 编码为 <字幕名>.sup。\n'
                '取消则只生成 bdn.xml 和 PNG。')
        self.make_pes_check = tk.Checkbutton(
            toggle_frame, text='同时生成 PES', variable=self.make_pes_var,
            bg=UI_CARD, fg=UI_TEXT, activebackground=UI_CARD, activeforeground=UI_TEXT,
            selectcolor=UI_CARD, font=(UI_FONT, UI_BODY_SIZE),
            highlightthickness=0, bd=0, padx=0, cursor='hand2',
        )
        self.make_pes_check.pack(side='left', padx=(18, 0))
        ToolTip(self.make_pes_check, '勾选后除 SUP 外同时生成 PES + MUI 文件。\n'
                                     'PES/MUI 由 SUPer 链生成。')
        self.cleanup_bdn_check = tk.Checkbutton(
            toggle_frame, text='仅保留 SUP / PES', variable=self.cleanup_bdn_var,
            bg=UI_CARD, fg=UI_TEXT, activebackground=UI_CARD, activeforeground=UI_TEXT,
            selectcolor=UI_CARD, font=(UI_FONT, UI_BODY_SIZE),
            highlightthickness=0, bd=0, padx=0, cursor='hand2',
        )
        self.cleanup_bdn_check.pack(side='left', padx=(18, 0))
        ToolTip(self.cleanup_bdn_check,
                '生成成功后删除中间的 bdn.xml 和 PNG，只保留 SUP / PES 文件。\n'
                '需要保留画面帧用于检查时不要勾选。')
        ttk.Button(toggle_frame, text='高级参数', command=self.open_advanced, style='Secondary.TButton').pack(
            side='left', padx=(18, 0))

        action = tk.Frame(self, bg=UI_BG)
        action.pack(side='bottom', fill='x', pady=(8, 8))
        self.btn_start = ttk.Button(action, text='开始生成', command=self.start, style='Accent.TButton')
        self.btn_start.pack(side='left')
        self.btn_cancel = ttk.Button(
            action, text='取消', command=self.cancel, style='Secondary.TButton', state='disabled')
        self.btn_cancel.pack(side='left', padx=(10, 0))
        self.btn_open = ttk.Button(action, text='打开输出文件夹', command=self.open_output, style='Secondary.TButton')
        self.btn_open.pack(side='left', padx=(10, 0))
        self.btn_sup_to_pes = ttk.Button(
            action, text='SUP 转 PES', command=self.convert_sup_to_pes,
            style='Secondary.TButton')
        self.btn_sup_to_pes.pack(side='left', padx=(10, 0))
        ToolTip(self.btn_sup_to_pes,
                '把已有 SUP 直接封装为 PES + MUI，不重新渲染、不重新编码。\n'
                '输出到每个 SUP 所在目录，文件名与 SUP 相同。')
        self.status_var = tk.StringVar(value='')
        tk.Label(
            action, textvariable=self.status_var, bg=UI_BG, fg=UI_MUTED, font=(UI_FONT, UI_SMALL_SIZE),
        ).pack(side='left', padx=(14, 0))
        self.elapsed_var = tk.StringVar(value='')
        tk.Label(
            action, textvariable=self.elapsed_var, bg=UI_BG, fg=UI_MUTED,
            font=(UI_FONT, UI_SMALL_SIZE),
        ).pack(side='right')

    def open_advanced(self):
        dialog = BdnAdvancedDialog(self, self.params)
        self.wait_window(dialog)
        if dialog.result:
            self.params = dialog.result
            self.status_var.set('高级参数已更新')

    def ingest_log(self, text: str):
        self.queue.put(('log', text))
        match = re.search(r'下载中 (\d+)%', text)
        if match:
            self.queue.put(('progress', int(match.group(1)) * 0.9))

    def choose_ass(self):
        paths = filedialog.askopenfilenames(
            title='选择 ASS 字幕（可多选）', parent=self.winfo_toplevel(),
            initialdir=last_directory('ass_file', self.out_var.get()),
            filetypes=[('ASS 字幕', '*.ass'), ('所有文件', '*.*')],
        )
        if paths:
            remember_directory('ass_file', paths[0])
        if paths and not self.append_ass_queue(paths):
            self.status_var.set('选择的文件已经在队列里')

    def append_ass_queue(self, paths):
        if self.running:
            return 0
        empty_before = self.ass_queue_list.count() == 0
        added = self.ass_queue_list.append(paths)
        if added and empty_before and (self.sup_out_auto or not self.out_var.get().strip()):
            first = self.ass_queue_list.paths()[0]
            self.out_var.set(os.path.dirname(os.path.abspath(first)))
            self.sup_out_auto = True
        if self.ass_queue_list.count():
            count = self.ass_queue_list.count()
            if count > 1:
                self.status_var.set(f'队列共 {count} 个 ASS，批量输出到各自子文件夹')
            else:
                self.status_var.set('已载入 ASS 文件')
        return added

    def remove_selected_ass_queue(self):
        removed = self.ass_queue_list.remove_selected()
        if removed:
            left = self.ass_queue_list.count()
            if left == 0 and self.sup_out_auto:
                self.out_var.set('')
            self.status_var.set(
                f'队列剩余 {left} 个 ASS' if left else '队列已清空')

    def clear_ass_queue(self):
        if self.running:
            return
        self.ass_queue_list.clear()
        if self.sup_out_auto:
            self.out_var.set('')
        self.status_var.set('队列已清空')

    def _set_ass_queue_status(self, index, text):
        self.ass_queue_list.set_status(index, text)

    def _set_ass_queue_progress(self, index, value):
        self.ass_queue_list.set_progress(index, value)

    def _on_drop_ass(self, event):
        if self.running:
            self.status_var.set('正在生成，暂不能修改队列')
            return 'break'
        paths = split_dropped_paths(self.winfo_toplevel(), event.data)
        files = [path for path in paths if path.lower().endswith('.ass')]
        if not files:
            self.status_var.set('请拖入 .ass 字幕文件')
            return 'break'
        if not self.append_ass_queue(files):
            self.status_var.set('拖入的文件已经在队列里')
        return 'break'

    def _sync_fps_choices(self, *args):
        allowed = BDN_FORMAT_FPS.get(self.fmt_var.get(), BDN_FPS)
        try:
            self.fps_combo.config(values=allowed)
        except AttributeError:
            return
        if self.fps_var.get() not in allowed:
            self.fps_var.set(allowed[0])

    def _restore_options(self, config):
        fmt = str(config.get('sup_fmt') or '')
        if fmt in BDN_FORMATS:
            self.fmt_var.set(fmt)
        fps = str(config.get('sup_fps') or '')
        if fps in BDN_FPS:
            self.fps_var.set(fps)
        language = str(config.get('sup_lang') or '').strip()
        if language:
            self.lang_var.set(language)
        self.make_sup_var.set(bool(config.get('sup_make_sup', True)))
        self.make_pes_var.set(bool(config.get('sup_make_pes', False)))
        self.cleanup_bdn_var.set(bool(config.get('sup_cleanup_bdn', False)))
        out_dir = str(config.get('sup_out') or '').strip()
        if out_dir:
            if out_dir.lower().endswith('_bdn'):
                out_dir = os.path.dirname(out_dir)
            self.out_var.set(normalize_display_path(out_dir))
        self.sup_out_auto = bool(
            config.get('sup_out_auto', not bool(out_dir)))
        self._sync_fps_choices()

    def config_values(self):
        return {
            'sup_fmt': self.fmt_var.get(),
            'sup_fps': self.fps_var.get(),
            'sup_lang': self.lang_var.get(),
            'sup_make_sup': bool(self.make_sup_var.get()),
            'sup_make_pes': bool(self.make_pes_var.get()),
            'sup_cleanup_bdn': bool(self.cleanup_bdn_var.get()),
            'sup_out': self.out_var.get(),
            'sup_out_auto': bool(self.sup_out_auto),
            'sup_params': dict(self.params),
        }

    def _save_options(self):
        config = load_config()
        config.pop('sup_font_dir', None)
        config.pop('sup_effect', None)
        config.update(self.config_values())
        save_config(config)

    def choose_out(self):
        if self.running:
            return
        path = filedialog.askdirectory(
            title='选择输出文件夹', parent=self.winfo_toplevel(),
            initialdir=last_directory('ass_output', self.out_var.get()))
        if path:
            remember_directory('ass_output', path)
            self.out_var.set(normalize_display_path(path))
            self.sup_out_auto = False

    def open_output(self):
        path = self.out_var.get().strip()
        if path and os.path.isdir(path):
            os.startfile(path)
        else:
            messagebox.showinfo('提示', '输出文件夹还不存在', parent=self.winfo_toplevel())

    def convert_sup_to_pes(self):
        if self.running:
            return
        paths = filedialog.askopenfilenames(
            title='选择要转换的 SUP（可多选）', parent=self.winfo_toplevel(),
            initialdir=last_directory('sup_file', self.out_var.get()),
            filetypes=[('PGS 字幕', '*.sup'), ('所有文件', '*.*')])
        if not paths:
            return
        remember_directory('sup_file', paths[0])
        targets = []
        for path in paths:
            stem = os.path.splitext(os.path.basename(path))[0]
            folder = os.path.dirname(os.path.abspath(path))
            for suffix in ('.pes', '.pes.mui'):
                target = os.path.join(folder, stem + suffix)
                if os.path.exists(target):
                    targets.append(target)
        if targets:
            listing = '\n'.join('· ' + os.path.basename(item)
                                for item in targets[:8])
            if len(targets) > 8:
                listing += f'\n· … 共 {len(targets)} 个'
            if not messagebox.askyesno(
                    '确认覆盖',
                    '以下 PES/MUI 已存在：\n' + listing +
                    '\n\n继续转换会覆盖这些文件，是否继续？',
                    parent=self.winfo_toplevel()):
                return

        outputs = []
        failures = []
        total = len(paths)
        for index, path in enumerate(paths, 1):
            name = os.path.basename(path)
            last = {'percent': -10}

            def progress(value, name=name, index=index, last=last):
                percent = int(value)
                if percent >= 100 or percent >= last['percent'] + 10:
                    last['percent'] = percent
                    self.status_var.set(
                        f'SUP 转 PES（{index}/{total}）：{name} {percent}%')
                    self.update_idletasks()

            progress(0)
            try:
                ok, message, files = sup_to_pes(path, progress=progress)
            except Exception as exc:
                ok, message, files = False, str(exc), []
            if ok:
                outputs.extend(files)
            else:
                failures.append((name, message))

        if failures:
            detail = '\n'.join(f'· {name}：{message}'
                               for name, message in failures[:8])
            if len(failures) > 8:
                detail += f'\n· … 共 {len(failures)} 个失败'
            messagebox.showwarning(
                'SUP 转 PES',
                f'成功 {total - len(failures)}/{total}，失败 {len(failures)}。\n\n'
                + detail,
                parent=self.winfo_toplevel())
        else:
            listing = '\n'.join('· ' + os.path.basename(item)
                                for item in outputs[:12])
            if len(outputs) > 12:
                listing += f'\n· … 共 {len(outputs)} 个文件'
            messagebox.showinfo(
                'SUP 转 PES',
                f'已转换 {total} 个 SUP：\n\n' + listing,
                parent=self.winfo_toplevel())
        self.status_var.set(
            f'SUP 转 PES 完成：成功 {total - len(failures)}/{total}')

    def _set_running(self, running: bool):
        self.running = running
        state = 'disabled' if running else 'normal'
        self.btn_start.config(state=state, text='生成中…' if running else '开始生成')
        self.btn_cancel.config(state='normal' if running else 'disabled')
        for name in ('btn_choose_ass', 'btn_ass_queue_remove', 'btn_ass_queue_clear',
                     'make_sup_check', 'make_pes_check', 'btn_sup_to_pes',
                     'cleanup_bdn_check'):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.config(state=state)
        self.ass_queue_list.set_running(running)
        self.out_entry.config(state=state)
        self.btn_choose_out.config(state=state)
        if running:
            self.started_at = time.time()
            self.elapsed_var.set('已用 00:00')
            self.status_var.set('正在处理，请稍候…')
        else:
            self.started_at = None
            self.elapsed_var.set('')
            self.status_var.set('')
        try:
            self.winfo_toplevel().focus_set()
        except tk.TclError:
            pass

    def cancel(self):
        if not self.running:
            return
        self.cancel_event.set()
        self.status_var.set('正在取消…')
        self.btn_cancel.config(state='disabled')

    def _cleanup_cancelled(self, out_dir: str, ass_path: str):
        cleanup_bdn_outputs(out_dir)
        stem = os.path.splitext(os.path.basename(ass_path))[0]
        for suffix in ('.sup', '.pes', '.pes.mui', '.mui'):
            path = os.path.join(out_dir, stem + suffix)
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass

    def _expected_output_names(self, ass_path: str) -> list:
        stem = os.path.splitext(os.path.basename(ass_path))[0]
        names = ['bdn.xml']
        if self.make_sup_var.get() or self.make_pes_var.get():
            names.append(stem + '.sup')
        if self.make_pes_var.get():
            names += [stem + '.pes', stem + '.pes.mui', stem + '.mui']
        return names

    def start(self):
        if self.running:
            return
        paths = self.ass_queue_list.paths()
        if not paths:
            messagebox.showinfo('提示', '请先添加 ASS 字幕文件', parent=self.winfo_toplevel())
            return
        for path in paths:
            if not os.path.isfile(path):
                messagebox.showerror('找不到文件', path, parent=self.winfo_toplevel())
                return
        batch = len(paths) > 1
        out_root = self.out_var.get().strip()
        ok_combo, combo_message = validate_bdn_format(
            self.fmt_var.get(), self.fps_var.get())
        if not ok_combo:
            messagebox.showerror(
                '参数不兼容', combo_message, parent=self.winfo_toplevel())
            return
        if not vsfilter_available():
            messagebox.showerror(
                '缺少渲染组件',
                '未找到 VSFilterMod 渲染组件。\n'
                '请确认 bin64\\avs2bdnxml.exe、AviSynth.dll、VSFilterMod.dll '
                '存在，或保留 bin 作为 32 位回退。',
                parent=self.winfo_toplevel())
            return

        jobs = [
            {
                'path': path,
                'out_dir': out_dir,
                'make_sup': bool(self.make_sup_var.get()),
                'make_pes': bool(self.make_pes_var.get()),
            }
            for path, out_dir in build_output_dirs(paths, out_root)
        ]

        missing = []
        for job in jobs:
            for name in missing_ass_fonts(job['path']):
                if name not in missing:
                    missing.append(name)
        if missing:
            listing = '\n'.join('· ' + name for name in missing[:8])
            if len(missing) > 8:
                listing += f'\n· … 共 {len(missing)} 种'
            if not messagebox.askyesno(
                    '字体缺失',
                    f'{"这些字幕" if batch else "ASS"}使用了以下字体，但系统里没有安装：\n\n'
                    f'{listing}\n\n'
                    '字体名和字重需完全对应（不会把 Medium 当作 Regular 或 Bold）。\n'
                    '生成严格使用 VSFilterMod，它只能调用系统字体，缺失时会被替换，'
                    '导致宽度、位置与预期不一致。\n\n建议先安装字体。仍要继续吗？',
                    parent=self.winfo_toplevel()):
                return

        # 图形字幕只保留 SUPer 链。
        vertical_by_path = {
            job['path']: ass_vertical_font_names(job['path'])
            for job in jobs
        }
        for job in jobs:
            job['vertical_fonts'] = vertical_by_path.get(job['path'], [])

        conflicts = []
        for job in jobs:
            existing = [
                name for name in self._expected_output_names(job['path'])
                if os.path.exists(os.path.join(job['out_dir'], name))
            ]
            if existing:
                conflicts.append((job, existing))
        if conflicts:
            if batch:
                lines = [
                    f'· {os.path.basename(job["path"])}：{len(existing)} 个同名文件'
                    for job, existing in conflicts[:8]
                ]
                if len(conflicts) > 8:
                    lines.append(f'· … 共 {len(conflicts)} 个')
                if not messagebox.askyesno(
                        '确认覆盖',
                        '以下字幕的输出文件夹已存在同名文件：\n' + '\n'.join(lines) +
                        '\n\n继续生成会覆盖这些文件，是否继续？',
                        parent=self.winfo_toplevel()):
                    return
            else:
                job, existing = conflicts[0]
                if not messagebox.askyesno(
                        '确认覆盖',
                        '输出文件夹中已存在以下文件：\n' + '\n'.join(existing) +
                        '\n\n继续生成可能覆盖同名文件，是否继续？',
                        parent=self.winfo_toplevel()):
                    return

        self._save_options()
        self.cancel_event.clear()
        self.sup_jobs = jobs
        self.sup_job_index = 0
        self.sup_batch = batch
        self.sup_batch_success = 0
        self.sup_batch_failures = []
        for index in range(len(jobs)):
            self._set_ass_queue_status(index, '等待')
            self._set_ass_queue_progress(index, None)
        self._set_running(True)
        self._start_next_sup_job()

    def _start_next_sup_job(self):
        if self.sup_job_index >= len(self.sup_jobs):
            self._finish_sup_batch()
            return
        job = self.sup_jobs[self.sup_job_index]
        self.current_sup_job = job
        self._set_ass_queue_status(self.sup_job_index, '渲染中')
        self._set_ass_queue_progress(self.sup_job_index, 0)
        if self.sup_batch:
            self.status_var.set(
                f'({self.sup_job_index + 1}/{len(self.sup_jobs)}) 正在渲染 '
                f'{os.path.basename(job["path"])}')
        else:
            self.status_var.set(f'正在渲染 {os.path.basename(job["path"])}')
        self.started_at = time.time()
        self.elapsed_var.set('已用 00:00')
        params = dict(self.params)
        fmt = self.fmt_var.get()
        fps = self.fps_var.get()
        language = self.lang_var.get().strip() or 'und'
        make_sup = bool(self.make_sup_var.get())
        make_pes = bool(self.make_pes_var.get())
        job_path = job['path']
        job_out = job['out_dir']
        vertical_fonts = job.get('vertical_fonts') or []
        cleanup_after = (
            bool(self.cleanup_bdn_var.get())
            and (make_sup or make_pes))

        def worker():
            try:
                if vertical_fonts:
                    self.ingest_log(
                        f'检测到 @ 竖排字体（{" / ".join(vertical_fonts)}），'
                        '使用 x64 GDI 渲染器')
                self.ingest_log('SUPer 链：x64 渲染 BDN，再交给 SUPer')
                ok, message, _outputs = generate_bdn_sup(
                    job_path, job_out,
                    fmt=fmt,
                    fps=fps,
                    language=language,
                    track_name='',
                    make_sup=make_sup,
                    make_pes=make_pes,
                    params=params,
                    log=self.ingest_log,
                    progress=lambda value: self.queue.put(('progress', value)),
                    cancel_event=self.cancel_event,
                )
                if ok:
                    try:
                        _info, issues = validate_bdn_sup(
                            job_path, job_out, make_sup, make_pes)
                        if issues:
                            log_exception(
                                f'BDN/SUP 校验：{os.path.basename(job_path)}：'
                                + '；'.join(issues))
                    except Exception as exc:
                        log_exception('校验 BDN/SUP', exc)
                if cleanup_after:
                    cleanup_bdn_outputs(job_out)
            except RenderCancelled:
                self._cleanup_cancelled(job_out, job_path)
                self.queue.put(('cancelled',))
                return
            except Exception as exc:
                log_exception('生成 BDN/SUP', exc)
                ok, message = False, str(exc)
            self.queue.put(('done', ok, message))

        threading.Thread(target=worker, daemon=True).start()
        self.after(100, self._poll)

    def _sup_job_finished(self, ok, message):
        job = self.current_sup_job
        if ok:
            self.sup_batch_success += 1
            if job is not None:
                self._set_ass_queue_status(self.sup_job_index, '完成')
                self._set_ass_queue_progress(self.sup_job_index, 100)
            if not self.sup_batch:
                self._set_running(False)
                self.status_var.set('生成完成')
                self._show_generation_result(
                    1, 1, [], job.get('out_dir', '') if job else '')
                return
        else:
            if job is not None:
                self.sup_batch_failures.append((job['path'], message))
                self._set_ass_queue_status(self.sup_job_index, '失败')
            if not self.sup_batch:
                self._set_running(False)
                self.status_var.set('生成失败')
                self._show_generation_result(
                    1, 0, [(job['path'] if job else '', message)])
                return
        self.sup_job_index += 1
        self._start_next_sup_job()

    def _show_generation_result(self, total, success, failures, output_dir=''):
        """统一的生成结果提示：重点只显示成功 / 失败。"""
        failed = len(failures)
        if failed:
            lines = [f'共 {total} 个，成功 {success} 个，失败 {failed} 个。']
            for path, message in failures[:5]:
                name = os.path.basename(path) if path else '字幕'
                reason = str(message).strip().splitlines()
                reason = reason[0][:120] if reason else '未知错误'
                lines.append(f'\n{name}\n{reason}')
            if failed > 5:
                lines.append(f'\n… 共 {failed} 个失败，详见错误日志。')
            messagebox.showwarning(
                '生成失败' if total == 1 else '生成完成（有失败）',
                '\n'.join(lines), parent=self.winfo_toplevel())
            return
        text = '成功 1 个。' if total == 1 else f'成功 {success} 个。'
        if output_dir:
            text += f'\n\n输出文件夹：\n{output_dir}'
        else:
            text += '\n\n可在「打开输出文件夹」中查看结果。'
        messagebox.showinfo(
            '生成成功', text, parent=self.winfo_toplevel())

    def _finish_sup_batch(self):
        self._set_running(False)
        self.sup_batch = False
        total = len(self.sup_jobs)
        self.status_var.set(f'队列完成：成功 {self.sup_batch_success}/{total}')
        self._show_generation_result(
            total, self.sup_batch_success, self.sup_batch_failures)

    def _sup_cancelled(self):
        if self.current_sup_job is not None:
            self._set_ass_queue_status(self.sup_job_index, '已取消')
        remaining = max(0, len(self.sup_jobs) - self.sup_job_index - 1)
        was_batch = self.sup_batch
        self.sup_batch = False
        self._set_running(False)
        self.status_var.set('已取消')
        message = '已停止生成，中途生成的 BDN / PNG 已清理。'
        if was_batch and remaining:
            message += f'\n剩余 {remaining} 个字幕未处理。'
        messagebox.showinfo('已取消', message, parent=self.winfo_toplevel())

    def _poll(self):
        if self.running and self.started_at:
            elapsed = max(0, int(time.time() - self.started_at))
            self.elapsed_var.set(f'已用 {elapsed // 60:02d}:{elapsed % 60:02d}')
        try:
            while True:
                item = self.queue.get_nowait()
                kind = item[0]
                if kind == 'log':
                    self.status_var.set(item[1])
                    if self.current_sup_job is not None:
                        if '渲染' in item[1]:
                            self._set_ass_queue_status(self.sup_job_index, '渲染中')
                        elif '生成 SUP' in item[1]:
                            self._set_ass_queue_status(self.sup_job_index, '生成中')
                elif kind == 'progress':
                    self._set_ass_queue_progress(self.sup_job_index, item[1])
                elif kind == 'cancelled':
                    self._sup_cancelled()
                    return
                elif kind == 'done':
                    self._sup_job_finished(item[1], item[2])
                    return
        except queue.Empty:
            pass
        self.after(100, self._poll)
