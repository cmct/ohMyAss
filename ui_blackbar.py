"""黑边检测窗口。"""


import os
import queue
import statistics
import tempfile
import threading
import tkinter as tk
from collections import Counter
from tkinter import filedialog, messagebox, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    TkinterDnD = None
    DND_AVAILABLE = False
try:
    from PIL import Image, ImageGrab
except ImportError:
    Image = ImageGrab = None

from app_info import resource_path
from config_store import last_directory, log_exception, remember_directory

import theme_ui
from theme_ui import (
    UI_BG, UI_BODY_SIZE, UI_BORDER, UI_CARD,
    UI_FIELD_SOFT, UI_FONT, UI_MUTED, UI_SMALL_SIZE,
    UI_TEXT, center_window, px,
)
from subtitle_core import IMAGE_EXTENSIONS, detect_black_bars

theme_ui.register_theme_namespace(globals())


class BlackBarDetectorWindow:
    def __init__(self, master, on_mid_black=None, on_bar_height=None):
        self.master = master
        self.on_mid_black = on_mid_black
        self.on_bar_height = on_bar_height
        self.window = tk.Toplevel(master)
        self.window.title("ohMyAss Noto 黑边检测")
        self.window.geometry(f"{px(640)}x{px(700)}")
        self.window.minsize(px(540), px(560))
        try:
            self.window.iconbitmap(default=resource_path('ohMyAss Noto.ico'))
        except tk.TclError:
            pass
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.results = []
        self.analysis_queue = queue.Queue()
        self.analysis_stop = threading.Event()
        self.analysis_active = False
        self._last_analysed_name = ''
        self._temp_images = []
        self._display_names = {}
        self.build_ui()
        self.window.bind('<Control-v>', self._paste_event)
        self.window.bind('<Control-V>', self._paste_event)
        center_window(self.window, master)

    def build_ui(self):
        self.window.configure(bg=UI_BG)
        content = tk.Frame(self.window, bg=UI_BG, padx=18, pady=16)
        content.pack(fill='both', expand=True)

        intro = (
            "通过电影截图检测上下黑边大小\n"
            "可直接按 Ctrl+V 粘贴网页图片；右击网页图片选择「复制图片」\n"
            "裁掉黑边的截图会按实际画面比例推算，2160p 自动换算为 1080p\n"
            "检测完成后会自动联动主界面的“中黑边”选项"
        )
        tk.Label(
            content, text=intro, bg=UI_BG, fg=UI_MUTED,
            font=(UI_FONT, UI_SMALL_SIZE), justify='center',
        ).pack(pady=(2, 12))

        drop_text = "将图片拖到这里\n或点击“选择图片” / 按 Ctrl+V 粘贴"
        if not DND_AVAILABLE:
            drop_text = "拖拽功能需要安装 tkinterdnd2\n可点击此处选择图片"
        self.drop_area = tk.Label(
            content,
            text=drop_text,
            relief='flat',
            bd=0,
            height=4,
            justify='center',
            bg=UI_FIELD_SOFT,
            fg=UI_MUTED,
            font=(UI_FONT, UI_BODY_SIZE),
            cursor='hand2',
            highlightthickness=px(1),
            highlightbackground=UI_BORDER,
        )
        self.drop_area.pack(fill='x', pady=(0, 12))
        self.drop_area.bind('<Button-1>', lambda event: self.choose_images())
        if DND_AVAILABLE:
            self.drop_area.drop_target_register(DND_FILES)
            self.drop_area.dnd_bind('<<Drop>>', self.on_drop)

        button_frame = tk.Frame(content, bg=UI_BG)
        button_frame.pack(pady=(0, 8))
        self.btn_select = ttk.Button(button_frame, text="选择图片", command=self.choose_images, style='Secondary.TButton')
        self.btn_select.pack(side='left', padx=6)
        self.btn_paste = ttk.Button(button_frame, text="粘贴图片（Ctrl+V）", command=self.paste_clipboard, style='Secondary.TButton')
        self.btn_paste.pack(side='left', padx=6)
        self.btn_clear = ttk.Button(button_frame, text="清空结果", command=self.clear_results, style='Secondary.TButton')
        self.btn_clear.pack(side='left', padx=6)

        self.status_var = tk.StringVar(value="等待图片...")
        ttk.Label(content, textvariable=self.status_var, style='Status.TLabel').pack(pady=(2, 6))

        progress_row = tk.Frame(content, bg=UI_BG)
        progress_row.pack(fill='x', pady=(0, 8))
        tk.Label(
            progress_row, text='检测进度', bg=UI_BG, fg=UI_MUTED,
            font=(UI_FONT, UI_SMALL_SIZE),
        ).pack(side='left', padx=(0, 8))
        self.progress = ttk.Progressbar(
            progress_row, orient='horizontal', mode='determinate',
            style='Blue.Horizontal.TProgressbar')
        self.progress.pack(side='left', fill='x', expand=True)

        result_frame = tk.Frame(content, bg=UI_CARD, highlightthickness=px(1),
                                highlightbackground=UI_BORDER, highlightcolor=UI_BORDER)
        result_frame.pack(fill='both', expand=True)
        scrollbar = ttk.Scrollbar(result_frame, orient='vertical')
        scrollbar.pack(side='right', fill='y')
        self.result_text = tk.Text(
            result_frame,
            height=12,
            wrap='word',
            yscrollcommand=scrollbar.set,
            state='disabled',
            font=(UI_FONT, UI_SMALL_SIZE),
            bg=UI_CARD,
            fg=UI_TEXT,
            relief='flat',
            padx=10,
            pady=10,
            highlightthickness=0,
            bd=0,
            insertbackground=UI_TEXT,
        )
        self.result_text.pack(side='left', fill='both', expand=True)
        scrollbar.config(command=self.result_text.yview)

    def on_drop(self, event):
        paths = self.window.tk.splitlist(event.data)
        images = [path for path in paths if path.lower().endswith(IMAGE_EXTENSIONS)]
        if not images:
            messagebox.showinfo(
                "提示",
                "请拖入图片文件（jpg/png/bmp/tif/webp）",
                parent=self.window,
            )
            return
        self.analyse_images(images)

    def _paste_event(self, event=None):
        self.paste_clipboard()
        return 'break'

    def paste_clipboard(self):
        """从剪贴板读取网页图片或图片文件路径。"""
        if ImageGrab is None:
            messagebox.showerror(
                '缺少依赖',
                '剪贴板图片需要 Pillow。请先运行：pip install pillow',
                parent=self.window)
            return
        if self.analysis_active:
            return
        try:
            data = ImageGrab.grabclipboard()
        except Exception as exc:
            log_exception('读取剪贴板图片', exc)
            messagebox.showerror(
                '无法读取剪贴板', str(exc), parent=self.window)
            return
        if data is None:
            messagebox.showinfo(
                '剪贴板中没有图片',
                '请先在网页图片上右击选择「复制图片」，再按 Ctrl+V。',
                parent=self.window)
            return
        paths = []
        if isinstance(data, list):
            paths = [
                os.fspath(path) for path in data
                if os.fspath(path).lower().endswith(IMAGE_EXTENSIONS)
            ]
            if not paths:
                messagebox.showinfo(
                    '剪贴板中没有图片',
                    '剪贴板包含的是文件路径，但没有可识别的图片文件。',
                    parent=self.window)
                return
        elif isinstance(data, Image.Image):
            try:
                handle, path = tempfile.mkstemp(
                    prefix='ohMyAssNoto_paste_', suffix='.png')
                os.close(handle)
                data.save(path, 'PNG')
            except Exception as exc:
                log_exception('保存剪贴板图片', exc)
                messagebox.showerror(
                    '无法保存剪贴板图片', str(exc), parent=self.window)
                return
            self._temp_images.append(path)
            self._display_names[path] = '剪贴板图片'
            paths = [path]
        else:
            messagebox.showinfo(
                '剪贴板中没有图片',
                '当前剪贴板内容不是图片。',
                parent=self.window)
            return
        self.analyse_images(paths)

    def choose_images(self):
        paths = filedialog.askopenfilenames(
            title="选择电影截图",
            parent=self.window,
            initialdir=last_directory('blackbar_file'),
            filetypes=[
                ("图片文件", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp"),
                ("所有文件", "*.*"),
            ],
        )
        if paths:
            remember_directory('blackbar_file', paths[0])
            self.analyse_images(list(paths))

    def clear_results(self):
        self.results = []
        self.result_text.config(state='normal')
        self.result_text.delete('1.0', 'end')
        self.result_text.config(state='disabled')
        self.status_var.set("等待图片...")
        self.progress['value'] = 0

    def append_result(self, text):
        self.result_text.config(state='normal')
        self.result_text.insert('end', text + '\n')
        self.result_text.see('end')
        self.result_text.config(state='disabled')

    def analyse_images(self, paths):
        if Image is None:
            messagebox.showerror(
                "缺少依赖",
                "黑边检测需要 Pillow，请先运行：pip install pillow",
                parent=self.window,
            )
            return
        if not paths:
            return
        if self.analysis_active:
            # 上一批还在检测，忽略新的拖放/选择，避免结果串批
            return

        for button in (self.btn_select, self.btn_paste, self.btn_clear):
            button.config(state='disabled')
        self.results = []
        self.result_text.config(state='normal')
        self.result_text.delete('1.0', 'end')
        self.result_text.config(state='disabled')
        self.progress['maximum'] = 100
        self.progress['value'] = 0
        self.analysis_stop.clear()
        self.analysis_active = True
        threading.Thread(
            target=self._analyse_worker, args=(list(paths),), daemon=True).start()
        self.window.after(50, self._poll_analysis)

    def _analyse_worker(self, paths):
        total = len(paths)
        for index, path in enumerate(paths, 1):
            if self.analysis_stop.is_set():
                break
            filename = self._display_names.get(
                path, os.path.basename(path))
            self.analysis_queue.put(('start', index, total, filename))

            def progress_callback(current, maximum):
                self.analysis_queue.put(('progress', current, maximum))

            try:
                result = detect_black_bars(path, progress_callback)
            except Exception as exc:
                log_exception(f'黑边检测：{filename}', exc)
                self.analysis_queue.put(('error', filename, str(exc)))
                continue
            self.analysis_queue.put(('result', result, filename))
        self.analysis_queue.put(('finished',))

    def _poll_analysis(self):
        try:
            while True:
                item = self.analysis_queue.get_nowait()
                kind = item[0]
                if kind == 'start':
                    self._last_analysed_name = item[3]
                    self.status_var.set(
                        f'正在检测 {item[1]}/{item[2]}: {item[3]}')
                    self.progress['value'] = 0
                elif kind == 'progress':
                    self.progress['maximum'] = item[2]
                    self.progress['value'] = item[1]
                elif kind == 'result':
                    self._add_analysis_result(item[1])
                elif kind == 'error':
                    self.append_result(f'【{item[1]}】\n检测失败：{item[2]}\n')
                elif kind == 'finished':
                    self._finish_analysis()
                    return
        except queue.Empty:
            pass
        try:
            self.window.after(50, self._poll_analysis)
        except tk.TclError:
            self.analysis_active = False

    def _add_analysis_result(self, result):
        self.results.append(result)
        mid_hint = '勾选' if result['type'] == '中黑边' else '不勾选'
        lines = [f"【{self._last_analysed_name}】"]
        lines.append(f"分辨率：{result['width']}×{result['height']}")
        if result.get('cropped'):
            lines.append("截图上下黑边：0 / 0 px（截图已裁切）")
        else:
            lines.append(f"上黑边：{result['top']} px")
            lines.append(f"下黑边：{result['bottom']} px")
            lines.append(f"总黑边：{result['top'] + result['bottom']} px")
        lines.append(
            f"实际画面：{result['width']}×{result['content_height']}"
            f"（{result['aspect']:.2f}:1）")
        lines.append(
            f"1080p 等效黑边：{result['scaled_bottom']:.1f} px（按实际画面推算）")
        lines.append(f"黑边类型：{result['type']}")
        lines.append(f"中黑边勾选参考：{mid_hint}")
        self.append_result('\n'.join(lines) + '\n')

    def _finish_analysis(self):
        self.analysis_active = False
        if len(self.results) > 1:
            self.append_summary()
        elif len(self.results) == 1:
            self.status_var.set(f"检测完成：{self._last_analysed_name}")
        else:
            self.status_var.set("未成功检测到图片")

        self.sync_mid_black_option()
        self.progress['value'] = 0
        for button in (self.btn_select, self.btn_paste, self.btn_clear):
            button.config(state='normal')

    def sync_mid_black_option(self):
        if not self.results:
            return
        if len(self.results) > 1:
            common_type = Counter(item['type'] for item in self.results).most_common(1)[0][0]
        else:
            common_type = self.results[0]['type']
        checked = common_type == '中黑边'
        if self.on_mid_black:
            self.on_mid_black(checked)
        scaled = statistics.median(item['scaled_bottom'] for item in self.results)
        if self.on_bar_height:
            self.on_bar_height(scaled)
        self.append_result(f"已联动主界面中黑边选项：{'勾选' if checked else '不勾选'}\n")

    def append_summary(self):
        tops = [item['top'] for item in self.results]
        bottoms = [item['bottom'] for item in self.results]
        aspects = [item['aspect'] for item in self.results]
        scaled = [item['scaled_bottom'] for item in self.results]
        types = Counter(item['type'] for item in self.results)
        common_type = types.most_common(1)[0][0]
        cropped_count = sum(1 for item in self.results if item.get('cropped'))
        mid_hint = '勾选' if common_type == '中黑边' else '不勾选'
        self.append_result(
            "【综合结果】\n"
            f"检测图片：{len(self.results)} 张\n"
            f"截图黑边中位数：上 {statistics.median(tops):.1f} px / "
            f"下 {statistics.median(bottoms):.1f} px\n"
            f"实际画面比例中位数：{statistics.median(aspects):.2f}:1\n"
            f"1080p 等效黑边中位数：{statistics.median(scaled):.1f} px\n"
            f"已裁切截图：{cropped_count} 张\n"
            f"最多类型：{common_type}\n"
            f"中黑边勾选参考：{mid_hint}\n"
        )
        self.status_var.set(f"检测完成：共 {len(self.results)} 张图片")

    def close(self):
        self.analysis_stop.set()
        self.window.destroy()
        if self._temp_images:
            def cleanup():
                for path in self._temp_images:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            timer = threading.Timer(1.0, cleanup)
            timer.daemon = True
            timer.start()
