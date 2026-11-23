"""流媒体（Apple TV / iTunes）全地区字幕下载页面。"""

import argparse
import asyncio
import contextlib
import io
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from config_store import (
    last_directory, load_config, normalize_display_path, remember_directory,
    save_config,
)
from theme_ui import (
    ToolTip, UI_BG, UI_BODY_SIZE, UI_CARD, UI_FIELD, UI_FONT,
    UI_MUTED, UI_SMALL_SIZE, UI_TEXT, create_card, create_hero, px,
)
from ui_widgets import PlaceholderEntry, attach_entry_placeholder


def default_output_dir() -> str:
    downloads = os.path.join(os.path.expanduser('~'), 'Downloads')
    preferred = os.path.join(downloads, 'AppleTV Subs')
    if os.path.isdir(preferred):
        return preferred
    videos = os.path.join(os.path.expanduser('~'), 'Videos')
    if os.path.isdir(videos):
        return os.path.join(videos, 'AppleTV Subs')
    return preferred


class _QueueWriter(io.TextIOBase):
    def __init__(self, sink):
        self.sink = sink

    def write(self, text):
        if text:
            self.sink(('log', text))
        return len(text)

    def flush(self):
        pass


class AtvSubsPanel(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, style='App.TFrame')
        self.queue = queue.Queue()
        self.running = False
        self._loop = None
        self._task = None
        self._module = None
        self.build_ui()
        self._restore_options()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def build_ui(self):
        create_hero(
            self, '流媒体字幕',
            '按 IMDB 编号、片名或 Apple TV / iTunes 链接，抓取全部地区的字幕轨道。',
        ).pack(fill='x', pady=(0, px(10)))

        query_outer, query_body = create_card(self, '影片')
        query_outer.pack(fill='x', pady=(0, px(10)))
        query_body.columnconfigure(1, weight=1)

        self.query_var = tk.StringVar()
        tk.Label(
            query_body, text='影片', bg=UI_CARD, fg=UI_TEXT,
            font=(UI_FONT, UI_BODY_SIZE),
        ).grid(row=0, column=0, sticky='w', padx=(0, px(10)), pady=px(4))
        query_entry = ttk.Entry(query_body, textvariable=self.query_var)
        query_entry.grid(row=0, column=1, columnspan=2, sticky='ew', pady=px(4))
        attach_entry_placeholder(
            query_entry, self.query_var,
            'IMDB 编号（推荐）/ 影片名 / Apple TV 或 iTunes 链接')

        self.output_var = tk.StringVar(value=default_output_dir())
        tk.Label(
            query_body, text='输出目录', bg=UI_CARD, fg=UI_TEXT,
            font=(UI_FONT, UI_BODY_SIZE),
        ).grid(row=1, column=0, sticky='w', padx=(0, px(10)), pady=px(4))
        self.output_entry = ttk.Entry(query_body, textvariable=self.output_var)
        self.output_entry.grid(row=1, column=1, sticky='ew', pady=px(4))
        attach_entry_placeholder(
            self.output_entry, self.output_var, '下载目录')
        self.btn_choose_output = ttk.Button(
            query_body, text='选择', command=self.choose_output,
            style='Secondary.TButton')
        self.btn_choose_output.grid(
            row=1, column=2, sticky='e', padx=(px(8), 0), pady=px(4))

        option_outer, option_body = create_card(self, '筛选')
        option_outer.pack(fill='x', pady=(0, px(10)))
        for column in range(4):
            option_body.columnconfigure(column, weight=1)

        def label(text, row, column):
            tk.Label(
                option_body, text=text, bg=UI_CARD, fg=UI_TEXT,
                font=(UI_FONT, UI_BODY_SIZE),
            ).grid(row=row, column=column, sticky='w',
                   padx=(0, px(10)), pady=(0, px(3)))

        label('语言', 0, 0)
        label('地区', 0, 1)
        label('代理', 0, 2)
        label('DSID', 0, 3)

        self.lang_entry = PlaceholderEntry(
            option_body, placeholder='全部语言，例如 en,zh,fr')
        self.lang_entry.grid(row=1, column=0, sticky='ew',
                             padx=(0, px(10)), pady=(0, px(8)))
        self.countries_entry = PlaceholderEntry(
            option_body, placeholder='全部地区，例如 US,GB,JP')
        self.countries_entry.grid(row=1, column=1, sticky='ew',
                                  padx=(0, px(10)), pady=(0, px(8)))
        self.proxy_entry = PlaceholderEntry(
            option_body, placeholder='例如 http://127.0.0.1:7890')
        self.proxy_entry.grid(row=1, column=2, sticky='ew',
                              padx=(0, px(10)), pady=(0, px(8)))
        self.dsid_entry = PlaceholderEntry(
            option_body, placeholder='购买/租借内容才需要')
        self.dsid_entry.grid(row=1, column=3, sticky='ew',
                             pady=(0, px(8)))

        label('探测并发', 2, 0)
        label('下载并发', 2, 1)
        label('超时（秒）', 2, 2)

        self.probe_jobs_var = tk.StringVar(value='8')
        self.download_jobs_var = tk.StringVar(value='3')
        self.timeout_var = tk.StringVar(value='30')
        ttk.Spinbox(
            option_body, from_=1, to=32, textvariable=self.probe_jobs_var,
            width=8,
        ).grid(row=3, column=0, sticky='w', padx=(0, px(10)))
        ttk.Spinbox(
            option_body, from_=1, to=16, textvariable=self.download_jobs_var,
            width=8,
        ).grid(row=3, column=1, sticky='w', padx=(0, px(10)))
        ttk.Spinbox(
            option_body, from_=5, to=300, textvariable=self.timeout_var,
            width=8,
        ).grid(row=3, column=2, sticky='w')

        toggle_outer, toggle_body = create_card(self, '选项')
        toggle_outer.pack(fill='x', pady=(0, px(10)))
        toggle = tk.Frame(toggle_body, bg=UI_CARD)
        toggle.pack(fill='x')
        self.list_var = tk.BooleanVar(value=False)
        self.services_var = tk.BooleanVar(value=False)
        self.jw_countries_var = tk.BooleanVar(value=False)
        self.exact_dedup_var = tk.BooleanVar(value=False)
        self.no_forced_var = tk.BooleanVar(value=False)
        self.no_sdh_var = tk.BooleanVar(value=False)
        self.debug_var = tk.BooleanVar(value=False)

        def check(text, variable, tooltip):
            widget = tk.Checkbutton(
                toggle, text=text, variable=variable, bg=UI_CARD, fg=UI_TEXT,
                activebackground=UI_CARD, activeforeground=UI_TEXT,
                selectcolor=UI_CARD, font=(UI_FONT, UI_BODY_SIZE),
                highlightthickness=0, bd=0, padx=0, cursor='hand2',
            )
            widget.pack(side='left', padx=(0, px(16)))
            ToolTip(widget, tooltip)
            return widget

        check('只列出字幕', self.list_var, '只探测并列出字幕轨，不下载文件。')
        check('只查上架', self.services_var, '只查询 JustWatch 流媒体上架情况，不下载。')
        check('仅 JustWatch 地区', self.jw_countries_var,
              '只探测 JustWatch 记录的地区，更快，但可能漏掉冷门地区。')
        check('精确去重', self.exact_dedup_var,
              '只合并字节完全相同的字幕，保留 <i> 换行等排版差异。')
        check('不要 Forced', self.no_forced_var, '不下载 Forced 字幕。')
        check('不要 SDH', self.no_sdh_var, '不下载 SDH / CC 字幕。')
        check('调试日志', self.debug_var, '输出 isubrip 调试日志。')

        action = tk.Frame(self, bg=UI_BG)
        action.pack(fill='x', pady=(0, px(8)))
        self.btn_start = ttk.Button(
            action, text='开始下载', command=self.start, style='Accent.TButton')
        self.btn_start.pack(side='left')
        self.btn_cancel = ttk.Button(
            action, text='取消', command=self.cancel,
            style='Secondary.TButton', state='disabled')
        self.btn_cancel.pack(side='left', padx=(px(8), 0))
        self.btn_open = ttk.Button(
            action, text='打开输出目录', command=self.open_output,
            style='Secondary.TButton')
        self.btn_open.pack(side='left', padx=(px(8), 0))
        self.status_var = tk.StringVar(value='')
        tk.Label(
            action, textvariable=self.status_var, bg=UI_BG, fg=UI_MUTED,
            font=(UI_FONT, UI_SMALL_SIZE),
        ).pack(side='left', padx=(px(12), 0))
        self.progress = ttk.Progressbar(
            action, style='Blue.Horizontal.TProgressbar',
            orient='horizontal', mode='indeterminate', length=px(180))
        self.progress.pack(side='right')

        log_outer, log_body = create_card(self, '运行日志')
        log_outer.pack(fill='both', expand=True)
        log_body.rowconfigure(0, weight=1)
        log_body.columnconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_body, height=10, wrap='word', state='disabled',
            bg=UI_FIELD, fg=UI_TEXT, insertbackground=UI_TEXT,
            relief='flat', bd=0, font=(UI_FONT, UI_SMALL_SIZE),
            padx=px(8), pady=px(6),
        )
        self.log_text.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(log_body, orient='vertical',
                               command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.log_text.configure(yscrollcommand=scroll.set)

    # ------------------------------------------------------------------
    # Options
    # ------------------------------------------------------------------
    def _restore_options(self):
        config = load_config()
        self.query_var.set(str(config.get('atv_query') or ''))
        output = str(config.get('atv_output') or '').strip()
        if output:
            self.output_var.set(normalize_display_path(output))
        self.lang_entry.set_value(str(config.get('atv_lang') or ''))
        self.countries_entry.set_value(str(config.get('atv_countries') or ''))
        self.proxy_entry.set_value(str(config.get('atv_proxy') or ''))
        self.probe_jobs_var.set(str(config.get('atv_probe_jobs') or '8'))
        self.download_jobs_var.set(str(config.get('atv_download_jobs') or '3'))
        self.timeout_var.set(str(config.get('atv_timeout') or '30'))
        for key, variable in (
                ('atv_list', self.list_var),
                ('atv_services', self.services_var),
                ('atv_jw_countries', self.jw_countries_var),
                ('atv_exact_dedup', self.exact_dedup_var),
                ('atv_no_forced', self.no_forced_var),
                ('atv_no_sdh', self.no_sdh_var),
                ('atv_debug', self.debug_var)):
            variable.set(bool(config.get(key, False)))

    def _save_options(self):
        config = load_config()
        config.update({
            'atv_query': self.query_var.get().strip(),
            'atv_output': self.output_var.get().strip(),
            'atv_lang': self.lang_entry.get_value(),
            'atv_countries': self.countries_entry.get_value(),
            'atv_proxy': self.proxy_entry.get_value(),
            'atv_probe_jobs': self.probe_jobs_var.get(),
            'atv_download_jobs': self.download_jobs_var.get(),
            'atv_timeout': self.timeout_var.get(),
            'atv_list': bool(self.list_var.get()),
            'atv_services': bool(self.services_var.get()),
            'atv_jw_countries': bool(self.jw_countries_var.get()),
            'atv_exact_dedup': bool(self.exact_dedup_var.get()),
            'atv_no_forced': bool(self.no_forced_var.get()),
            'atv_no_sdh': bool(self.no_sdh_var.get()),
            'atv_debug': bool(self.debug_var.get()),
        })
        save_config(config)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def choose_output(self):
        path = filedialog.askdirectory(
            title='选择流媒体字幕输出目录', parent=self.winfo_toplevel(),
            initialdir=last_directory('atv_output', self.output_var.get()))
        if path:
            remember_directory('atv_output', path)
            self.output_var.set(normalize_display_path(path))

    def open_output(self):
        path = self.output_var.get().strip()
        if not path:
            path = default_output_dir()
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass
        if os.path.isdir(path):
            try:
                os.startfile(path)
                return
            except OSError as exc:
                messagebox.showerror('无法打开', str(exc), parent=self.winfo_toplevel())
                return
        messagebox.showinfo('提示', f'输出目录不存在：\n{path}',
                            parent=self.winfo_toplevel())

    def _parse_int(self, variable, default, minimum, maximum):
        try:
            value = int(variable.get())
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    def _make_namespace(self):
        return argparse.Namespace(
            query=self.query_var.get().strip(),
            output=self.output_var.get().strip() or default_output_dir(),
            lang=self.lang_entry.get_value() or None,
            countries=self.countries_entry.get_value() or None,
            list=bool(self.list_var.get()),
            services_only=bool(self.services_var.get()),
            justwatch_countries=bool(self.jw_countries_var.get()),
            probe_jobs=self._parse_int(self.probe_jobs_var, 8, 1, 32),
            download_jobs=self._parse_int(self.download_jobs_var, 3, 1, 16),
            timeout=float(self._parse_int(self.timeout_var, 30, 5, 300)),
            proxy=self.proxy_entry.get_value() or None,
            dsid=self.dsid_entry.get_value() or None,
            exact_dedup=bool(self.exact_dedup_var.get()),
            no_forced=bool(self.no_forced_var.get()),
            no_sdh=bool(self.no_sdh_var.get()),
            debug=bool(self.debug_var.get()),
            version=False,
        )

    def start(self):
        if self.running:
            return
        query = self.query_var.get().strip()
        if not query:
            messagebox.showinfo('提示', '请输入 IMDB 编号、影片名或 Apple TV / iTunes 链接。',
                                parent=self.winfo_toplevel())
            return
        output = self.output_var.get().strip() or default_output_dir()
        self.output_var.set(normalize_display_path(output))
        try:
            os.makedirs(output, exist_ok=True)
        except OSError as exc:
            messagebox.showerror('输出目录不可用', str(exc),
                                 parent=self.winfo_toplevel())
            return
        ns = self._make_namespace()
        self._save_options()
        self._set_running(True)
        self._clear_log()
        self._append_log(
            f'输出目录：{output}\n'
            f'输入：{query}\n'
            + ('只列出字幕\n' if ns.list else '')
            + ('只查 JustWatch 上架\n' if ns.services_only else '')
            + '\n')
        threading.Thread(target=self._worker, args=(ns,), daemon=True).start()
        self.after(100, self._poll)

    def cancel(self):
        if not self.running:
            return
        self.status_var.set('正在取消…')
        self.btn_cancel.config(state='disabled')
        loop = self._loop
        task = self._task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass

    def _worker(self, ns):
        rc = 1
        try:
            try:
                if self._module is None:
                    import atv_subs
                    self._module = atv_subs
                module = self._module
            except BaseException as exc:
                self.queue.put(('log', f'加载 atv_subs 失败：{exc}\n'))
                self.queue.put(('done', 1))
                return

            writer = _QueueWriter(self.queue.put)
            loop = asyncio.new_event_loop()
            self._loop = loop
            try:
                asyncio.set_event_loop(loop)
                # isubrip 的 Scraper 是进程级单例，内部 AsyncClient 会绑定
                # 创建时的事件循环。OMA 可以在同一进程里多次运行，必须先
                # 清掉旧单例，否则第二次会报 “client has been closed”。
                try:
                    from isubrip.scrapers.scraper import SingletonMeta
                    from isubrip.scrapers.appletv_scraper import AppleTVScraper
                    from isubrip.scrapers.itunes_scraper import ItunesScraper
                    for scraper_cls in (AppleTVScraper, ItunesScraper):
                        SingletonMeta._instances.pop(scraper_cls, None)
                except Exception:
                    pass
                with contextlib.redirect_stdout(writer), \
                        contextlib.redirect_stderr(writer):
                    self._task = loop.create_task(module.run(ns))
                    rc = loop.run_until_complete(self._task)
            except asyncio.CancelledError:
                rc = 130
                self.queue.put(('log', '\n已取消。\n'))
            except BaseException as exc:
                rc = 1
                self.queue.put(('log', f'\n运行失败：{exc}\n'))
            finally:
                self._task = None
                self._loop = None
                try:
                    loop.close()
                except Exception:
                    pass
        finally:
            self.queue.put(('done', rc))

    def _poll(self):
        done = False
        rc = 0
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            kind = item[0]
            if kind == 'log':
                self._append_log(item[1])
            elif kind == 'done':
                done = True
                rc = item[1]
        if done:
            self._set_running(False)
            if rc == 0:
                self.status_var.set('完成')
            elif rc == 130:
                self.status_var.set('已取消')
            else:
                self.status_var.set(f'失败（退出码 {rc}）')
            self._append_log('\n')
        if self.running or not done:
            self.after(100, self._poll)

    # ------------------------------------------------------------------
    # Widget state
    # ------------------------------------------------------------------
    def _set_running(self, running):
        self.running = running
        state = 'disabled' if running else 'normal'
        self.btn_start.config(state=state)
        self.btn_cancel.config(state='normal' if running else 'disabled')
        for widget in (
                self.output_entry, self.btn_choose_output,
                self.lang_entry, self.countries_entry, self.proxy_entry,
                self.dsid_entry):
            try:
                widget.config(state=state)
            except tk.TclError:
                pass
        if running:
            self.progress.start(12)
            self.status_var.set('运行中…')
        else:
            self.progress.stop()

    def _clear_log(self):
        self.log_text.configure(state='normal')
        self.log_text.delete('1.0', 'end')
        self.log_text.configure(state='disabled')

    def _append_log(self, text):
        if not text:
            return
        self.log_text.configure(state='normal')
        self.log_text.insert('end', text)
        self.log_text.see('end')
        self.log_text.configure(state='disabled')
