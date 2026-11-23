"""样式编辑器与样式预览窗口。"""


import os
import re
import tkinter as tk
from types import SimpleNamespace
from tkinter import messagebox, ttk, colorchooser

try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk
except ImportError:
    Image = ImageDraw = ImageFont = ImageTk = None

import theme_ui
from theme_ui import (
    ToolTip, UI_ACCENT, UI_BG, UI_BODY_SIZE,
    UI_FONT, UI_MUTED, UI_PREVIEW_BG, UI_SMALL_SIZE,
    UI_TEXT, UI_TROUGH, add_combo_arrow, center_window,
    prepare_combobox, px,
)
from fonts import preview_font_path
from subtitle_core import (
    FSVP_EDIT_KINDS, FSVP_PAIRS, HDR_SUB_BRIGHTNESS, STYLE_CATEGORIES,
    STYLE_FIELD_HELP, STYLE_FIELD_LABELS, STYLE_FIELD_NAMES, STYLE_KIND_TO_LABEL,
    STYLE_LABEL_TO_KIND, get_styles, parse_ass_colour, style_line_name,
)

theme_ui.register_theme_namespace(globals())


PREVIEW_STYLE_SAMPLES = {
    'zh_dialogue_bilingual': '这是示例字幕 Sample 001',
    'zh_dialogue_monolingual': '这是示例字幕',
    'en_dialogue_bilingual': 'Sample subtitle 001',
    'en_dialogue_monolingual': 'Sample subtitle',
    'zh_lyrics_bilingual': '歌词示例',
    'zh_lyrics_monolingual': '歌词示例',
    'en_lyrics_bilingual': '♪lyrics♪',
    'en_lyrics_monolingual': '♪lyrics♪',
    'zh_note_single_line': '注解示例 Note',
    'zh_note_double_line': '双行注释示例 Note',
    'Default': '示例字幕 Default',
}
PREVIEW_FSVP = 12.0
# ASS 字号按 GDI 字高解释，实际字形高度约为字号的 0.69
PREVIEW_FONT_SCALE = 0.69
# VSFilterMod 实测：80/52 号双语对白在 \fsvp12 下，中英文可见字形间距约 7px（1080p）
PREVIEW_BILINGUAL_BASE_GAP = 19.0
# VSFilterMod 实测：66 号两行注释在 \fsvp0 时可见字形间距约 19px，按字号等比缩放
PREVIEW_ANNOTATION_GAP_RATIO = 19.0 / 66.0


class StyleEditor(tk.Toplevel):
    """单个样式类别的完整 V4+ 字段编辑器。"""

    COLOR_FIELDS = {3, 4, 5, 6}

    def __init__(self, master, label, line, fsvp=12.0,
                 default_line=None, default_fsvp=12.0, show_fsvp=True):
        super().__init__(master)
        self.window = self
        self.title(f'修改样式：{label}')
        self.configure(bg=UI_BG)
        self.resizable(False, False)
        self.result = None
        self.default_line = default_line or line
        self.default_fsvp = default_fsvp
        self.show_fsvp = bool(show_fsvp)
        self._fsvp = float(fsvp)
        parts = line.split(',')
        while len(parts) < 23:
            parts.append('')
        self._style_name = parts[0].strip()
        self._encoding = parts[22].strip() or '1'

        body = tk.Frame(self, bg=UI_BG, padx=16, pady=14)
        body.pack(fill='both', expand=True)
        paste_row = tk.Frame(body, bg=UI_BG)
        paste_row.grid(row=0, column=0, columnspan=3, sticky='ew',
                       pady=(0, 10))
        paste_label = tk.Label(
            paste_row, text='粘贴样式', bg=UI_BG, fg=UI_MUTED,
            font=(UI_FONT, UI_SMALL_SIZE))
        paste_label.pack(side='left')
        paste_tip = ('把完整的 Style: 行粘贴到这里，点「识别」填入字段。\n'
                     '样式名保持当前类别不变，Encoding 固定为 1，行间距缩进保持不变。\n'
                     '示例：Style: Default,Calibri,18,&H00FFFFFF,...')
        ToolTip(paste_label, paste_tip)
        ttk.Button(
            paste_row, text='识别', command=self.apply_pasted_style,
            style='Secondary.TButton').pack(side='right')
        self.paste_var = tk.StringVar()
        self.paste_entry = ttk.Entry(
            paste_row, textvariable=self.paste_var)
        self.paste_entry.pack(side='left', fill='x', expand=True, padx=(8, 8))
        self.paste_entry.bind('<Return>', self.apply_pasted_style)
        ToolTip(self.paste_entry, paste_tip)

        tk.Label(
            body, text=f'样式类别：{label}', bg=UI_BG, fg=UI_TEXT,
            font=(UI_FONT, UI_BODY_SIZE, 'bold'),
        ).grid(row=1, column=0, columnspan=3, sticky='w', pady=(0, 8))

        self.fields = {}
        entries = list(range(1, 22))
        for position, index in enumerate(entries):
            row = 2 + position // 3
            column = position % 3
            holder = tk.Frame(body, bg=UI_BG)
            holder.grid(row=row, column=column, sticky='w', padx=(0, 16), pady=3)
            label_text = STYLE_FIELD_LABELS.get(
                index, STYLE_FIELD_NAMES[index])
            label_widget = tk.Label(
                holder, text=label_text, bg=UI_BG, fg=UI_MUTED,
                width=10, anchor='w', font=(UI_FONT, UI_SMALL_SIZE),
            )
            label_widget.pack(side='left')
            tooltip = STYLE_FIELD_HELP.get(index, STYLE_FIELD_NAMES[index])
            ToolTip(label_widget, tooltip)
            variable = tk.StringVar(value=parts[index].strip())
            self.fields[index] = variable
            if index in self.COLOR_FIELDS:
                entry = ttk.Entry(holder, textvariable=variable, width=11)
                entry.pack(side='left')
                swatch = tk.Button(
                    holder, width=2, bd=0, relief='flat', cursor='hand2',
                    command=lambda idx=index: self.pick_colour(idx))
                swatch.pack(side='left', padx=(4, 0))
                ToolTip(swatch, '点击打开色板')
                self._update_swatch(index, swatch)
                variable.trace_add(
                    'write', lambda *args, idx=index, btn=swatch:
                    self._update_swatch(idx, btn))
            else:
                entry = ttk.Entry(
                    holder, textvariable=variable, width=15,
                    justify='center' if index not in (1,) else 'left')
                entry.pack(side='left')
            ToolTip(entry, tooltip)

        base_row = 2 + (len(entries) + 2) // 3
        if self.show_fsvp:
            fsvp_row = tk.Frame(body, bg=UI_BG)
            fsvp_row.grid(row=base_row, column=0,
                          columnspan=3, sticky='w', pady=(10, 0))
            fsvp_label = tk.Label(
                fsvp_row, text='行间距缩进', bg=UI_BG, fg=UI_MUTED, width=10,
                anchor='w', font=(UI_FONT, UI_SMALL_SIZE))
            fsvp_label.pack(side='left')
            self.fsvp_var = tk.StringVar(value=f'{self._fsvp:g}')
            fsvp_entry = ttk.Entry(
                fsvp_row, textvariable=self.fsvp_var, width=8, justify='center')
            fsvp_entry.pack(side='left')
            tip = ('\\fsvp（行间距缩进）控制该类别字幕的行间距缩进，属于 VSFilterMod 扩展标签。\\n'
                   '单位约为 1 像素（1080p）：正数让该行上移，负数值下移，0 表示不调整。\\n'
                   '例如 12 会让英文行向中文靠拢 12px。\\n'
                   '双语对白/歌词的中英文样式共用同一个值；本项同时影响样式预览和生成。')
            ToolTip(fsvp_label, tip)
            ToolTip(fsvp_entry, tip)

        buttons = tk.Frame(body, bg=UI_BG)
        buttons.grid(row=base_row + (1 if self.show_fsvp else 0),
                     column=0, columnspan=3, sticky='ew', pady=(12, 0))
        ttk.Button(
            buttons, text='恢复默认', command=self.load_defaults,
            style='Secondary.TButton').pack(side='left')
        ttk.Button(
            buttons, text='取消', command=self.destroy,
            style='Secondary.TButton').pack(side='right')
        ttk.Button(
            buttons, text='保存', command=self.save,
            style='Accent.TButton').pack(side='right', padx=(0, 8))

        self.bind('<Escape>', lambda event: self.destroy())
        center_window(self, master)
        self.grab_set()

    def apply_pasted_style(self, event=None):
        """把粘贴的完整 Style: 行解析到各字段（样式名和 Encoding 保持不变）。"""
        raw = self.paste_var.get().strip()
        if not raw:
            try:
                raw = self.clipboard_get().strip()
            except tk.TclError:
                raw = ''
        if not raw:
            messagebox.showerror('样式无效', '请先粘贴样式文本', parent=self)
            return
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        candidates = [
            line for line in lines if line.lower().startswith('style:')]
        if not candidates and ',' in raw:
            candidates = [raw]
        if not candidates:
            messagebox.showerror(
                '样式无效',
                '未识别到 Style: 开头的样式行，请复制完整样式文本。',
                parent=self)
            return
        current_name = style_line_name(self._style_name)
        chosen = candidates[0]
        for line in candidates:
            if style_line_name(line).lower() == current_name.lower():
                chosen = line
                break
        parts = chosen.split(',')
        if len(parts) < 23:
            messagebox.showerror(
                '样式无效',
                f'样式字段不足：需要 Name + 22 个 V4+ 字段，当前只有 {len(parts)} 段。',
                parent=self)
            return
        for index in range(1, 22):
            self.fields[index].set(parts[index].strip())
        self._encoding = '1'
        self.paste_var.set('')
        messagebox.showinfo(
            '已识别',
            '样式字段已填入，行间距缩进保持不变；确认无误后点「保存」生效。',
            parent=self)

    def _parse_colour(self, value):
        text = str(value or '').strip()
        if text.upper().startswith('&H'):
            text = text[2:]
        text = text.rstrip('&')
        if len(text) == 6:
            alpha, body = '00', text
        elif len(text) == 8:
            alpha, body = text[:2], text[2:]
        else:
            return None
        try:
            blue = int(body[0:2], 16)
            green = int(body[2:4], 16)
            red = int(body[4:6], 16)
        except ValueError:
            return None
        return alpha, (red, green, blue)

    def _update_swatch(self, index, button):
        parsed = self._parse_colour(self.fields[index].get())
        if parsed:
            red, green, blue = parsed[1]
            button.configure(bg=f'#{red:02x}{green:02x}{blue:02x}')

    def pick_colour(self, index):
        parsed = self._parse_colour(self.fields[index].get())
        alpha = parsed[0] if parsed else '00'
        colour = parsed[1] if parsed else (255, 255, 255)
        _rgb, hex_colour = colorchooser.askcolor(
            color=f'#{colour[0]:02x}{colour[1]:02x}{colour[2]:02x}',
            parent=self, title='选择颜色')
        if not hex_colour:
            return
        red = int(hex_colour[1:3], 16)
        green = int(hex_colour[3:5], 16)
        blue = int(hex_colour[5:7], 16)
        self.fields[index].set(f'&H{alpha}{blue:02X}{green:02X}{red:02X}')

    def load_defaults(self):
        parts = self.default_line.split(',')
        for index in range(1, 22):
            if index in self.fields:
                value = parts[index].strip() if index < len(parts) else ''
                self.fields[index].set(value)
        self._encoding = '1'
        if self.show_fsvp:
            self.fsvp_var.set(f'{float(self.default_fsvp):g}')
            self._fsvp = float(self.default_fsvp)

    def save(self):
        parts = [''] * 23
        parts[0] = self._style_name

        def value(index):
            return self.fields[index].get().strip()

        if not value(1):
            messagebox.showerror('样式无效', 'Fontname 不能为空', parent=self)
            return
        for index in self.COLOR_FIELDS:
            text = value(index)
            if text.startswith('#'):
                if not re.match(r'^#[0-9A-Fa-f]{6}$', text):
                    messagebox.showerror(
                        '样式无效',
                        f'{STYLE_FIELD_NAMES[index]} 格式应为 #RRGGBB 或 &HBBGGRR',
                        parent=self)
                    return
                red = int(text[1:3], 16)
                green = int(text[3:5], 16)
                blue = int(text[5:7], 16)
                self.fields[index].set(
                    f'&H00{blue:02X}{green:02X}{red:02X}')
            if not self._parse_colour(value(index)):
                messagebox.showerror(
                    '样式无效',
                    f'{STYLE_FIELD_NAMES[index]} 格式应为 &HBBGGRR 或 &HAABBGGRR',
                    parent=self)
                return
        numeric = {
            2: (1, 300), 7: (0, 1), 8: (0, 1), 9: (0, 1), 10: (0, 1),
            11: (1, 1000), 12: (1, 1000), 13: (-100, 100), 14: (-360, 360),
            15: (1, 3), 16: (0, 100), 17: (0, 100), 18: (1, 9),
            19: (0, 10000), 20: (0, 10000), 21: (0, 10000),
        }
        float_fields = {2, 11, 12, 13, 14, 16, 17}
        for index, (low, high) in numeric.items():
            try:
                number = float(value(index))
            except ValueError:
                messagebox.showerror(
                    '样式无效', f'{STYLE_FIELD_NAMES[index]} 必须是数字', parent=self)
                return
            if not low <= number <= high:
                messagebox.showerror(
                    '样式无效',
                    f'{STYLE_FIELD_NAMES[index]} 超出范围（{low}–{high}）',
                    parent=self)
                return
            parts[index] = f'{number:g}' if index in float_fields else str(int(number))
        parts[1] = value(1)
        for index in self.COLOR_FIELDS:
            parts[index] = value(index)
        parts[22] = self._encoding
        if self.show_fsvp:
            try:
                fsvp = float(self.fsvp_var.get().strip() or 0)
            except ValueError:
                messagebox.showerror(
                    '样式无效', '\\fsvp 必须是数字', parent=self)
                return
            if not -200 <= fsvp <= 200:
                messagebox.showerror(
                    '样式无效', '\\fsvp 超出范围（-200–200）', parent=self)
                return
            fsvp = int(round(fsvp))
        else:
            fsvp = self._fsvp
        self.result = {'line': ','.join(parts), 'fsvp': fsvp}
        self.destroy()


class StylePreviewWindow:
    """样式预览：按当前片源选项画出 1080p 画面、黑边和各行落点。"""

    def __init__(self, master, is_hdr=False, is_mid_black=False,
                 brightness=HDR_SUB_BRIGHTNESS, bar_px=None, style_lines=None,
                 fsvp_values=None, selected_kind='zh_dialogue_bilingual'):
        self.window = tk.Toplevel(master)
        self.window.title('样式预览')
        self.window.configure(bg=UI_BG)
        self.window.transient(master)
        self.window.resizable(False, False)

        self.style_lines = list(style_lines) if style_lines else None
        self.fsvp_values = dict(fsvp_values or {})
        if selected_kind not in STYLE_KIND_TO_LABEL:
            selected_kind = 'zh_dialogue_bilingual'

        # 横向与主软件窗口一致，画面保持 16:9，纵向随宽度等比放大
        try:
            parent_width = int(master.winfo_width())
        except (AttributeError, tk.TclError):
            parent_width = 1000
        if parent_width < 800:
            parent_width = 1000
        parent_width = min(parent_width, self.window.winfo_screenwidth() - 60)
        self._canvas_width = max(560, parent_width - 34)
        self._canvas_height = max(320, int(round(self._canvas_width * 9 / 16)))

        if bar_px is not None:
            initial_bar = max(0.0, min(200.0, float(bar_px)))
        else:
            initial_bar = 58.0 if is_mid_black else 0.0
        initial_bar = float(int(round(initial_bar)))
        self.brightness = brightness
        self.bar_var = tk.StringVar(value=f'{initial_bar:.0f}')
        self.bar_scale_var = tk.DoubleVar(value=initial_bar)
        self.is_mid_black = bool(is_mid_black)
        self.hdr_var = tk.BooleanVar(value=bool(is_hdr))
        self.style_var = tk.StringVar(
            value=STYLE_KIND_TO_LABEL[selected_kind])
        self._font_cache = {}
        self._photo = None

        self.build_ui()
        self.redraw()
        center_window(self.window, master)

    def build_ui(self):
        body = tk.Frame(self.window, bg=UI_BG, padx=16, pady=14)
        body.pack(fill='both', expand=True)

        tk.Label(
            body, text='1920×1080 画面示意（上下黑边按同一数值模拟）：琥珀色虚线为黑边边缘，红色虚线为字幕顶边/底边',
            bg=UI_BG, fg=UI_MUTED, font=(UI_FONT, UI_SMALL_SIZE),
        ).pack(anchor='w', pady=(0, 8))

        self.canvas = tk.Canvas(
            body, width=self._canvas_width, height=self._canvas_height,
            bg=UI_PREVIEW_BG,
            highlightthickness=px(1), highlightbackground='#313848', bd=0)
        self.canvas.pack()

        controls = tk.Frame(body, bg=UI_BG)
        controls.pack(fill='x', pady=(10, 0))
        tk.Label(
            controls, text='上下黑边', bg=UI_BG, fg=UI_TEXT,
            font=(UI_FONT, UI_BODY_SIZE),
        ).pack(side='left')
        self.bar_entry = ttk.Entry(
            controls, textvariable=self.bar_var, width=6, justify='center')
        self.bar_entry.pack(side='left', padx=(6, 4))
        self.bar_entry.bind('<Return>', self._apply_bar_entry)
        self.bar_entry.bind('<FocusOut>', self._apply_bar_entry)
        tk.Label(
            controls, text='px（1080p）', bg=UI_BG, fg=UI_MUTED,
            font=(UI_FONT, UI_SMALL_SIZE),
        ).pack(side='left')
        self.bar_scale = tk.Scale(
            controls, from_=0, to=200, orient='horizontal', length=150,
            variable=self.bar_scale_var, command=self._on_scale_change,
            bg=UI_BG, fg=UI_TEXT, highlightthickness=0, troughcolor=UI_TROUGH,
            activebackground=UI_ACCENT, showvalue=False, width=12,
        )
        self.bar_scale.pack(side='left', padx=(10, 4))

        for text, variable, command in (
                ('HDR 映射', self.hdr_var, self.redraw),):
            tk.Checkbutton(
                controls, text=text, variable=variable, command=command,
                bg=UI_BG, fg=UI_TEXT, activebackground=UI_BG, activeforeground=UI_TEXT,
                selectcolor=UI_BG, font=(UI_FONT, UI_BODY_SIZE),
                highlightthickness=0, bd=0, padx=0, cursor='hand2',
            ).pack(side='left', padx=(12, 0))

        self.clearance_var = tk.StringVar(value='')
        tk.Label(
            body, textvariable=self.clearance_var, bg=UI_BG, fg=UI_MUTED,
            font=(UI_FONT, UI_SMALL_SIZE), anchor='w', justify='left',
            wraplength=self._canvas_width,
        ).pack(fill='x', pady=(8, 0))

        choose = tk.Frame(body, bg=UI_BG)
        choose.pack(fill='x', pady=(8, 0))
        tk.Label(
            choose, text='样式类别', bg=UI_BG, fg=UI_TEXT,
            font=(UI_FONT, UI_BODY_SIZE),
        ).pack(side='left')
        self.style_combo = add_combo_arrow(prepare_combobox(ttk.Combobox(
            choose, textvariable=self.style_var, state='readonly', width=18,
            values=[label for _kind, label in STYLE_CATEGORIES])))
        self.style_combo.pack(side='left', padx=(8, 0))
        self.style_combo.bind('<<ComboboxSelected>>', lambda event: self.redraw())
        self.style_info_var = tk.StringVar(value='')
        self.style_info_label = tk.Label(
            choose, textvariable=self.style_info_var, bg=UI_BG, fg=UI_MUTED,
            font=(UI_FONT, UI_SMALL_SIZE), anchor='w', justify='left',
        )
        self.style_info_label.pack(side='left', padx=(12, 0))
        ToolTip(
            self.style_info_label,
            '「实际顶边/底边」按预览中的红色虚线位置计算：\n'
            '包含字体行高与行间距缩进；L/R/V 仍是样式里的原始边距。')

        self.window.bind('<Button-1>', self._on_window_click, add='+')

    def _on_window_click(self, event):
        if event.widget is self.bar_entry:
            return
        self._apply_bar_entry()

    def _styles(self):
        dummy = SimpleNamespace(
            is_HDR=self.hdr_var.get(),
            is_mid_black=self.is_mid_black,
            hdr_brightness=self.brightness,
        )
        return [line.split(',') for line in get_styles(
            dummy, self.style_lines)]

    def _fsvp(self, kind):
        try:
            return float(self.fsvp_values.get(kind, PREVIEW_FSVP))
        except (TypeError, ValueError):
            return PREVIEW_FSVP

    def _set_bar(self, value):
        value = float(int(round(max(0.0, min(200.0, float(value))))))
        self.bar_var.set(f'{value:.0f}')
        try:
            self.bar_scale_var.set(value)
        except tk.TclError:
            pass

    def _on_scale_change(self, value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return
        self.bar_var.set(f'{number:.0f}')
        self.redraw()

    def _style_map(self):
        styles = {}
        for fields in self._styles():
            if len(fields) < 22:
                continue
            kind = fields[0].replace('Style: ', '').strip()
            parsed = parse_ass_colour(fields[3].lstrip('&').lstrip('Hh').rstrip('&'))
            colour = '#%02x%02x%02x' % parsed[1] if parsed else '#ffffff'
            styles[kind] = {
                'font': fields[1].strip(),
                'size': float(fields[2] or 0) or 80.0,
                'colour': colour,
                'outline': float(fields[16] or 0),
                'align': int(float(fields[18] or 2)),
                'ml': float(fields[19] or 0),
                'mr': float(fields[20] or 0),
                'mv': float(fields[21] or 0),
            }
        return styles

    def _font(self, name, size):
        key = (name, int(size))
        if key in self._font_cache:
            return self._font_cache[key]
        font = None
        path = preview_font_path(name)
        if path:
            try:
                font = ImageFont.truetype(path, int(size))
            except OSError:
                font = None
        if font is None:
            fallback = os.path.join(
                os.environ.get('WINDIR', r'C:\Windows'), 'Fonts', 'msyh.ttc')
            try:
                font = ImageFont.truetype(fallback, int(size))
            except OSError:
                font = ImageFont.load_default()
        self._font_cache[key] = font
        return font

    def _font_metrics(self, style):
        size = max(6, int(round(style['size'] * PREVIEW_FONT_SCALE * self._s)))
        font = self._font(style['font'], size)
        ascent, descent = font.getmetrics()
        return font, ascent, descent

    def _draw_block(self, draw, text, style, center=None, left=None,
                    top=None, bottom=None):
        size = max(6, int(round(style['size'] * PREVIEW_FONT_SCALE * self._s)))
        font = self._font(style['font'], size)
        stroke = max(0, int(round(style['outline'] * self._s)))
        bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        if center is not None:
            x = center - text_width / 2 - bbox[0]
        else:
            x = (left or 0) - bbox[0]
        if bottom is not None:
            y = bottom - text_height - bbox[1]
        else:
            y = (top or 0) - bbox[1]
        draw.text(
            (x, y), text, font=font, fill=style['colour'],
            stroke_width=stroke, stroke_fill='#000000')
        return {'top': y + bbox[1], 'bottom': y + bbox[1] + text_height}

    def _draw_baseline(self, draw, text, style, baseline, center=None, left=None):
        size = max(6, int(round(style['size'] * PREVIEW_FONT_SCALE * self._s)))
        font = self._font(style['font'], size)
        stroke = max(0, int(round(style['outline'] * self._s)))
        text_width = draw.textlength(text, font=font)
        x = (center - text_width / 2) if center is not None else (left or 0)
        draw.text(
            (x, baseline), text, font=font, fill=style['colour'],
            stroke_width=stroke, stroke_fill='#000000', anchor='ls')
        bbox = draw.textbbox(
            (x, baseline), text, font=font, anchor='ls', stroke_width=stroke)
        return {'top': bbox[1], 'bottom': bbox[3], 'left': bbox[0], 'right': bbox[2]}

    def _apply_bar_entry(self, event=None):
        try:
            value = float(self.bar_var.get())
        except (TypeError, ValueError):
            value = 0.0
        self._set_bar(value)
        self.redraw()

    def _dashed(self, draw, y, colour, width=2):
        margin = int(14 * self._ss)
        x = margin
        end = self._W - margin
        dash = int(10 * self._ss)
        gap = int(7 * self._ss)
        while x < end:
            draw.line([(x, y), (min(x + dash, end), y)], fill=colour, width=width)
            x += dash + gap

    def _draw_sample(self, draw, styles, selected):
        """绘制当前选中样式的示例字幕，返回 (顶边, 底边, 方向)。"""
        top_edge = None
        bottom_edge = None
        side = 'bottom'

        if selected in ('zh_dialogue_bilingual', 'en_dialogue_bilingual'):
            zh_style = styles.get('zh_dialogue_bilingual')
            en_style = styles.get('en_dialogue_bilingual')
            block_style = styles.get(selected) or zh_style
            if zh_style and en_style and block_style:
                block_bottom = self._H - block_style['mv'] * self._s
                fsvp = self._fsvp('en_dialogue_bilingual')
                en_bottom = block_bottom - fsvp * self._s
                en_info = self._draw_block(
                    draw, 'This is a sample subtitle', en_style,
                    center=self._W / 2, bottom=en_bottom)
                gap = PREVIEW_BILINGUAL_BASE_GAP - fsvp
                zh_bottom = en_info['top'] - gap * self._s
                zh_info = self._draw_block(
                    draw, '这是示例字幕', zh_style,
                    center=self._W / 2, bottom=zh_bottom)
                top_edge = zh_info['top']
                bottom_edge = en_info['bottom']
        elif selected in ('zh_lyrics_bilingual', 'en_lyrics_bilingual'):
            zh_style = styles.get('zh_lyrics_bilingual')
            en_style = styles.get('en_lyrics_bilingual')
            if zh_style and en_style:
                fsvp = self._fsvp('en_lyrics_bilingual')
                gap = (PREVIEW_BILINGUAL_BASE_GAP - fsvp) * self._s
                left = zh_style['ml'] * self._s
                top = zh_style['mv'] * self._s
                first = self._draw_block(
                    draw,
                    PREVIEW_STYLE_SAMPLES.get(
                        'zh_lyrics_bilingual', '歌词示例'),
                    zh_style, left=left, top=top)
                second = self._draw_block(
                    draw,
                    PREVIEW_STYLE_SAMPLES.get(
                        'en_lyrics_bilingual', '♪lyrics♪'),
                    en_style, left=left, top=first['bottom'] + gap)
                top_edge = first['top']
                bottom_edge = second['bottom']
                side = 'top'
        elif selected == 'zh_note_double_line':
            style = styles.get('zh_note_double_line')
            if style:
                fsvp = self._fsvp('zh_note_double_line')
                base_gap = style['size'] * PREVIEW_ANNOTATION_GAP_RATIO
                gap = (base_gap - fsvp) * self._s
                sample = PREVIEW_STYLE_SAMPLES.get(
                    'zh_note_double_line', '双行注释示例 Note')
                second_sample = '第二行注释 Second line'
                if style['align'] in (7, 8, 9):
                    first = self._draw_block(
                        draw, sample, style,
                        center=self._W / 2, top=style['mv'] * self._s)
                    second = self._draw_block(
                        draw, second_sample, style,
                        center=self._W / 2, top=first['bottom'] + gap)
                    top_edge = first['top']
                    bottom_edge = second['bottom']
                    side = 'top'
                else:
                    second = self._draw_block(
                        draw, second_sample, style,
                        center=self._W / 2,
                        bottom=self._H - style['mv'] * self._s)
                    first = self._draw_block(
                        draw, sample, style,
                        center=self._W / 2,
                        bottom=second['top'] - gap)
                    top_edge = first['top']
                    bottom_edge = second['bottom']
        else:
            style = styles.get(selected)
            if style:
                sample = PREVIEW_STYLE_SAMPLES.get(selected, selected)
                _, ascent, descent = self._font_metrics(style)
                if style['align'] == 2:
                    info = self._draw_baseline(
                        draw, sample, style,
                        self._H - style['mv'] * self._s - descent,
                        center=self._W / 2)
                elif style['align'] == 8:
                    info = self._draw_baseline(
                        draw, sample, style,
                        style['mv'] * self._s + ascent,
                        center=self._W / 2)
                    side = 'top'
                else:
                    info = self._draw_baseline(
                        draw, sample, style,
                        style['mv'] * self._s + ascent,
                        left=style['ml'] * self._s)
                    side = 'top'
                top_edge = info['top']
                bottom_edge = info['bottom']

        return top_edge, bottom_edge, side


    def redraw(self):
        if Image is None or ImageDraw is None or ImageFont is None:
            return
        try:
            bar = max(0.0, min(200.0, float(self.bar_var.get())))
        except (TypeError, ValueError):
            bar = 0.0

        ss = 2
        width, height = self._canvas_width, self._canvas_height
        self._W, self._H = width * ss, height * ss
        self._ss = ss
        self._s = self._W / 1920.0

        image = Image.new('RGB', (self._W, self._H), UI_PREVIEW_BG)
        draw = ImageDraw.Draw(image)
        bar_px = bar * self._s
        if bar_px > 0:
            draw.rectangle([0, 0, self._W, bar_px], fill='#000000')
            draw.rectangle([0, self._H - bar_px, self._W, self._H], fill='#000000')

        styles = self._style_map()
        selected = STYLE_LABEL_TO_KIND.get(
            self.style_var.get(), 'zh_dialogue_bilingual')
        top_edge, bottom_edge, side = self._draw_sample(
            draw, styles, selected)
        label_font = self._font(UI_FONT, int(11 * ss))
        if bar_px > 0:
            self._dashed(draw, bar_px, '#f59e0b')
            self._dashed(draw, self._H - bar_px, '#f59e0b')
            label = f'黑边边缘 {bar:.0f}px'
            label_bbox = draw.textbbox((0, 0), label, font=label_font)
            label_height = label_bbox[3] - label_bbox[1]
            draw.text(
                (int(12 * ss), bar_px + int(4 * ss)), label,
                font=label_font, fill='#f59e0b')
            bottom_label_y = self._H - bar_px + int(4 * ss)
            if bottom_label_y + label_height > self._H - int(2 * ss):
                bottom_label_y = self._H - bar_px - label_height - int(4 * ss)
            bottom_label_y = max(int(2 * ss), bottom_label_y)
            draw.text(
                (int(12 * ss), bottom_label_y), label,
                font=label_font, fill='#f59e0b')

        def draw_subtitle_edge(edge, text, prefer_below):
            if edge is None:
                return
            self._dashed(draw, edge, '#f43f5e')
            text_width = draw.textlength(text, font=label_font)
            bbox = draw.textbbox((0, 0), text, font=label_font)
            label_height = bbox[3] - bbox[1]
            if prefer_below:
                label_y = edge + int(6 * ss)
                if label_y + label_height > self._H - int(2 * ss):
                    label_y = edge - int(22 * ss)
            else:
                label_y = edge - int(22 * ss)
                if label_y < int(6 * ss):
                    label_y = edge + int(6 * ss)
            draw.text(
                (self._W - int(12 * ss) - text_width, label_y),
                text, font=label_font, fill='#f43f5e')

        draw_subtitle_edge(top_edge, '字幕顶边', prefer_below=False)
        draw_subtitle_edge(bottom_edge, '字幕底边', prefer_below=True)
        edge = bottom_edge if side == 'bottom' else top_edge

        image = image.resize((width, height), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(image)
        self.canvas.delete('all')
        self.canvas.create_image(0, 0, anchor='nw', image=self._photo)

        if bar_px <= 0:
            self.clearance_var.set(
                '未绘制黑边：把上下黑边设为大于 0 的数值，才能看到字幕与黑边的相对位置。')
        elif edge is not None:
            gap_px = ((self._H - bar_px) - edge) if side == 'bottom' else (edge - bar_px)
            gap_px /= self._s
            if gap_px >= 0:
                where = '上方' if side == 'bottom' else '下方'
                self.clearance_var.set(
                    f'当前字幕{"底边" if side == "bottom" else "顶边"}在'
                    f'{"底部" if side == "bottom" else "顶部"}黑边{where} '
                    f'{gap_px:.0f}px（画面内）；按 1080p 像素计算。')
            else:
                self.clearance_var.set(
                    f'当前字幕{"底边" if side == "bottom" else "顶边"}压入'
                    f'{"底部" if side == "bottom" else "顶部"}黑边 '
                    f'{-gap_px:.0f}px；按 1080p 像素计算。')

        align_text = {
            1: '左下', 2: '底部', 3: '右下', 4: '中左',
            5: '居中', 6: '中右', 7: '左上', 8: '顶部', 9: '右上',
        }
        info_style = styles.get(selected)
        if info_style:
            margin_v = int(round(info_style['mv']))
            top_anchor = info_style['align'] in (7, 8, 9)
            margin_text = ''
            if top_anchor and top_edge is not None:
                actual_top = top_edge / self._s
                margin_text = (f'实际顶边 {actual_top:.0f}px'
                               f'（y={actual_top:.0f}）')
            elif not top_anchor:
                actual_bottom = margin_v
                if (selected in FSVP_EDIT_KINDS
                        and any(selected in pair for pair in FSVP_PAIRS)):
                    # 双语第二行被行间距缩进上移
                    actual_bottom += self._fsvp(selected)
                margin_text = (
                    f'实际底边 {actual_bottom:g}px'
                    f'（y={int(round(1080 - actual_bottom))}）')
            if not margin_text:
                screen_y = margin_v if top_anchor else 1080 - margin_v
                margin_text = (
                    f'距{"顶部" if top_anchor else "底部"} {margin_v}px'
                    f'（y={screen_y}）')
            parts = [
                f'字号 {int(round(info_style["size"]))}',
                align_text.get(info_style['align'], f'{info_style["align"]}'),
                f'L{int(round(info_style["ml"]))} '
                f'R{int(round(info_style["mr"]))} V{margin_v}',
                margin_text,
            ]
            if selected in FSVP_EDIT_KINDS:
                parts.append(f'行间距缩进 {self._fsvp(selected):g}')
            self.style_info_var.set(' · '.join(parts))
        else:
            self.style_info_var.set('')
