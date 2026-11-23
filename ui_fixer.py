"""术语表与问题修复面板。"""


import os
import io
import json
import csv
import shutil
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from config_store import (
    last_directory, load_config, log_exception, remember_directory, save_config,
)

import theme_ui
from theme_ui import (
    ToolTip, UI_ACCENT, UI_BG, UI_BODY_SIZE,
    UI_CARD, UI_CONFIRM, UI_DANGER, UI_FIELD, UI_FIELD_BORDER,
    UI_FONT, UI_MUTED, UI_SELECT, UI_SMALL_SIZE,
    UI_TEXT, add_combo_arrow, create_card,
    create_hero, prepare_combobox, px,
)
from reports import NOTICES_DATA_NAME, parse_issue_entries
from task_runner import TaskRunner
from subtitle_core import (
    EDIT_SOURCE_PREFIX, format_ass_time, normalize_glossary,
    parse_ass_time, process_file, srt_to_ass,
)
from subtitle_outputs import (
    delete_dialogue, layout_for_primary, leading_tags, measure_text,
    primary_output_path, read_ass_text, strip_leading_tags, sync_outputs,
    write_ass_lines,
)

BACKUP_ROOT = os.path.join(
    os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'),
    'ohMyAssNoto', 'backups')

theme_ui.register_theme_namespace(globals())


class GlossaryPanel(ttk.Frame):
    """术语表：维护原文与指定译法，生成字幕时检查术语一致性。"""

    def __init__(self, master):
        super().__init__(master, style='App.TFrame')
        self.entries = []
        self.editing_index = None
        self.build_ui()
        self.load_entries()

    def build_ui(self):
        create_hero(
            self, '术语表',
            '维护原文与指定译法；生成字幕时会检查是否按术语表翻译。',
        ).pack(fill='x', pady=(0, 10))

        outer, card = create_card(self, '术语列表')
        outer.pack(fill='both', expand=True)

        action = tk.Frame(self, bg=UI_BG)
        action.pack(side='bottom', fill='x', pady=(px(8), px(8)))
        hint = tk.Label(
            self,
            text='原文出现时，中文行必须包含指定译文；英文按单词边界匹配，中文按子串匹配。',
            bg=UI_BG, fg=UI_MUTED, font=(UI_FONT, UI_SMALL_SIZE),
            anchor='w', justify='left', wraplength=px(860), padx=0,
        )
        hint.pack(side='bottom', fill='x', pady=(px(8), 0))

        self.save_button = ttk.Button(
            action, text='添加', command=self.add_or_update,
            style='Accent.TButton')
        self.save_button.pack(side='left')
        ttk.Button(
            action, text='取消编辑', command=self.reset_form,
            style='Secondary.TButton').pack(side='left', padx=(px(8), 0))
        ttk.Button(
            action, text='删除选中', command=self.delete_selected,
            style='Secondary.TButton').pack(side='left', padx=(px(8), 0))
        ttk.Button(
            action, text='清空', command=self.clear_all,
            style='Secondary.TButton').pack(side='left', padx=(px(8), 0))
        ttk.Button(
            action, text='导出 CSV', command=self.export_csv,
            style='Secondary.TButton').pack(side='right')
        ttk.Button(
            action, text='导入 CSV', command=self.import_csv,
            style='Secondary.TButton').pack(side='right', padx=(0, px(8)))

        card.columnconfigure(0, weight=1)
        card.rowconfigure(0, weight=1)

        tree_border = tk.Frame(card, bg=UI_FIELD_BORDER)
        tree_border.grid(row=0, column=0, sticky='nsew')
        tree_frame = tk.Frame(tree_border, bg=UI_FIELD)
        tree_frame.pack(fill='both', expand=True, padx=px(1), pady=px(1))
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            tree_frame, columns=('source', 'target', 'case', 'note'),
            show='headings', style='Editor.Treeview', height=8,
            selectmode='browse')
        for column, title, width, anchor in (
                ('source', '原文', px(230), 'w'),
                ('target', '指定译文', px(230), 'w'),
                ('case', '区分大小写', px(90), 'center'),
                ('note', '备注', px(260), 'w')):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, anchor=anchor,
                             stretch=column == 'note')
        self.tree.grid(row=0, column=0, sticky='nsew')
        tree_scroll = ttk.Scrollbar(
            tree_frame, orient='vertical', command=self.tree.yview)
        tree_scroll.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.bind('<<TreeviewSelect>>', self.on_select)
        self.tree.bind(
            '<FocusIn>', lambda event: tree_border.configure(bg=UI_ACCENT))
        self.tree.bind(
            '<FocusOut>', lambda event: tree_border.configure(bg=UI_FIELD_BORDER))

        form = tk.Frame(card, bg=UI_CARD)
        form.grid(row=1, column=0, sticky='ew', pady=(10, 0))
        form.columnconfigure(1, weight=1)
        form.columnconfigure(2, minsize=px(16))
        form.columnconfigure(4, weight=1)

        def field_label(text, row, column):
            tk.Label(
                form, text=text, bg=UI_CARD, fg=UI_TEXT, anchor='w',
                font=(UI_FONT, UI_BODY_SIZE),
            ).grid(row=row, column=column, sticky='w', padx=(0, 4), pady=4)

        self.source_var = tk.StringVar()
        self.target_var = tk.StringVar()
        self.case_var = tk.BooleanVar(value=False)
        self.note_var = tk.StringVar()

        field_label('原文', 0, 0)
        ttk.Entry(form, textvariable=self.source_var).grid(
            row=0, column=1, sticky='ew', pady=4)
        field_label('指定译文', 0, 3)
        ttk.Entry(form, textvariable=self.target_var).grid(
            row=0, column=4, sticky='ew', pady=4)

        tk.Checkbutton(
            form, text='区分大小写', variable=self.case_var,
            bg=UI_CARD, fg=UI_TEXT, activebackground=UI_CARD,
            activeforeground=UI_TEXT, selectcolor=UI_CARD,
            font=(UI_FONT, UI_BODY_SIZE), highlightthickness=0, bd=0,
            padx=0, cursor='hand2',
        ).grid(row=1, column=0, columnspan=2, sticky='w', pady=4)
        field_label('备注', 1, 3)
        ttk.Entry(form, textvariable=self.note_var).grid(
            row=1, column=4, sticky='ew', pady=4)

    def load_entries(self):
        self.entries = normalize_glossary(load_config().get('glossary'))
        self.refresh()

    def save_entries(self):
        config = load_config()
        config['glossary'] = [dict(entry) for entry in self.entries]
        save_config(config)

    def get_entries(self):
        return [dict(entry) for entry in self.entries]

    def refresh(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for index, entry in enumerate(self.entries):
            self.tree.insert(
                '', 'end', iid=str(index),
                values=(
                    entry['source'], entry['target'],
                    '是' if entry['case_sensitive'] else '否',
                    entry.get('note', ''),
                ))

    def on_select(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if not 0 <= index < len(self.entries):
            return
        entry = self.entries[index]
        self.editing_index = index
        self.source_var.set(entry['source'])
        self.target_var.set(entry['target'])
        self.case_var.set(bool(entry['case_sensitive']))
        self.note_var.set(entry.get('note', ''))
        self.save_button.config(text='保存修改')

    def reset_form(self):
        self.editing_index = None
        self.source_var.set('')
        self.target_var.set('')
        self.case_var.set(False)
        self.note_var.set('')
        self.save_button.config(text='添加')
        for iid in self.tree.selection():
            self.tree.selection_remove(iid)

    def add_or_update(self):
        source = self.source_var.get().strip()
        target = self.target_var.get().strip()
        if not source or not target:
            messagebox.showinfo(
                '提示', '原文和指定译文不能为空。',
                parent=self.winfo_toplevel())
            return
        entry = {
            'source': source,
            'target': target,
            'case_sensitive': bool(self.case_var.get()),
            'note': self.note_var.get().strip(),
        }
        if (self.editing_index is not None
                and 0 <= self.editing_index < len(self.entries)):
            self.entries[self.editing_index] = entry
        else:
            for index, old in enumerate(self.entries):
                if old['source'] == source:
                    self.entries[index] = entry
                    break
            else:
                self.entries.append(entry)
        self.save_entries()
        self.reset_form()
        self.refresh()

    def delete_selected(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo(
                '提示', '请先在列表中选择要删除的术语。',
                parent=self.winfo_toplevel())
            return
        index = int(selection[0])
        if 0 <= index < len(self.entries):
            del self.entries[index]
            self.save_entries()
            self.reset_form()
            self.refresh()

    def clear_all(self):
        if not self.entries:
            return
        if not messagebox.askyesno(
                '清空术语表', '确定清空全部术语吗？',
                parent=self.winfo_toplevel()):
            return
        self.entries = []
        self.save_entries()
        self.reset_form()
        self.refresh()

    def import_csv(self):
        path = filedialog.askopenfilename(
            parent=self.winfo_toplevel(),
            title='导入术语表',
            initialdir=last_directory('fixer_import'),
            filetypes=[('CSV / TSV', '*.csv *.tsv *.txt'),
                       ('所有文件', '*.*')])
        if not path:
            return
        remember_directory('fixer_import', path)
        try:
            with open(path, 'rb') as handle:
                raw = handle.read()
        except OSError as exc:
            messagebox.showerror(
                '导入失败', str(exc), parent=self.winfo_toplevel())
            return
        text = None
        for encoding in ('utf-8-sig', 'gb18030'):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            messagebox.showerror(
                '导入失败', '无法识别文件编码（支持 UTF-8 / GBK）',
                parent=self.winfo_toplevel())
            return
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',\t;')
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(io.StringIO(text), dialect))
        imported = []
        for row in rows:
            if len(row) < 2:
                continue
            source = row[0].strip()
            target = row[1].strip()
            if not source or not target:
                continue
            if source.casefold() in ('原文', 'source', 'term'):
                continue
            case_sensitive = False
            if len(row) > 2:
                case_sensitive = row[2].strip().casefold() in (
                    '1', 'true', 'yes', '是', '区分')
            note = row[3].strip() if len(row) > 3 else ''
            imported.append({
                'source': source,
                'target': target,
                'case_sensitive': case_sensitive,
                'note': note,
            })
        if not imported:
            messagebox.showinfo(
                '提示', '没有识别到可导入的术语。',
                parent=self.winfo_toplevel())
            return
        self.entries = normalize_glossary(self.entries + imported)
        self.save_entries()
        self.reset_form()
        self.refresh()
        messagebox.showinfo(
            '导入完成', f'已导入 {len(imported)} 条术语。',
            parent=self.winfo_toplevel())

    def export_csv(self):
        if not self.entries:
            messagebox.showinfo(
                '提示', '术语表为空。', parent=self.winfo_toplevel())
            return
        path = filedialog.asksaveasfilename(
            parent=self.winfo_toplevel(),
            title='导出术语表',
            initialdir=last_directory('fixer_export'),
            defaultextension='.csv',
            initialfile='术语表.csv',
            filetypes=[('CSV', '*.csv'), ('所有文件', '*.*')])
        if not path:
            return
        remember_directory('fixer_export', path)
        try:
            with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
                writer = csv.writer(handle)
                writer.writerow(['原文', '指定译文', '区分大小写', '备注'])
                for entry in self.entries:
                    writer.writerow([
                        entry['source'], entry['target'],
                        '是' if entry['case_sensitive'] else '否',
                        entry.get('note', ''),
                    ])
        except OSError as exc:
            messagebox.showerror(
                '导出失败', str(exc), parent=self.winfo_toplevel())
            return
        messagebox.showinfo(
            '导出完成', f'已导出到：\n{path}',
            parent=self.winfo_toplevel())


class IssueFixerPanel(ttk.Frame):
    """问题修复页：针对报告中的「请处理 / 请确认」逐条修改并重新生成字幕。"""

    def __init__(self, master):
        super().__init__(master, style='App.TFrame')
        self.data = None
        self.output_dir = ''
        self.source_path = ''
        self.primary_output = ''
        self.source_is_srt = False
        self.dialogues = []
        self.source_lines = []
        self.styles = []
        self.entries = {}
        self.edits = {}
        self.backup_dir = ''
        self.undo_stack = []
        self.dirty = False
        self.running = False
        self.task_runner = TaskRunner(self, 150)
        self._cht_cache = {}
        self.filter_text = ''
        self.filter_need = ''
        self._filter_job = None
        self.build_ui()

    def build_ui(self):
        create_hero(
            self, '问题修复',
            '按「请处理 / 请确认」逐条修改；双语片源以简英双语为基础，单语片源以简体中文为基础。',
        ).pack(fill='x', pady=(0, 10))

        file_outer, file_body = create_card(self, '问题数据')
        file_outer.pack(fill='x', pady=(0, 10))
        file_body.columnconfigure(1, weight=1)
        tk.Label(
            file_body, text='数据文件', bg=UI_CARD, fg=UI_TEXT,
            font=(UI_FONT, UI_BODY_SIZE),
        ).grid(row=0, column=0, sticky='w', padx=(0, 12), pady=4)
        self.path_var = tk.StringVar(value='')
        self.path_entry = ttk.Entry(
            file_body, textvariable=self.path_var, style='TEntry')
        self.path_entry.grid(row=0, column=1, sticky='ew', pady=4)

        def block_path_edit(event):
            if (event.state & 0x4) and event.keysym.lower() in (
                    'c', 'a', 'insert'):
                return None
            if event.keysym in (
                    'Left', 'Right', 'Up', 'Down', 'Home', 'End',
                    'Prior', 'Next', 'Shift_L', 'Shift_R',
                    'Control_L', 'Control_R', 'Tab'):
                return None
            return 'break'

        self.path_entry.bind('<KeyPress>', block_path_edit)
        self.path_entry.bind('<<Paste>>', lambda event: 'break')
        self.path_entry.bind('<<Cut>>', lambda event: 'break')
        self.path_entry.bind('<Button-2>', lambda event: 'break')
        choose_button = ttk.Button(
            file_body, text='选择', command=self.choose_file, style='Secondary.TButton',
        )
        self.btn_choose_file = choose_button
        choose_button.grid(row=0, column=2, padx=(10, 0), pady=4)
        ToolTip(
            choose_button,
            '选择输出文件夹里的「字幕问题数据.json」，或直接选择字幕问题报告.html。')

        list_outer, list_body = create_card(self, '待处理字幕')
        list_outer.pack(fill='both', expand=True)
        list_body.columnconfigure(0, weight=1)
        list_body.rowconfigure(1, weight=1)

        filter_row = tk.Frame(list_body, bg=UI_CARD)
        filter_row.grid(row=0, column=0, sticky='ew', pady=(0, 6))
        filter_row.columnconfigure(1, weight=1)
        tk.Label(
            filter_row, text='搜索', bg=UI_CARD, fg=UI_TEXT,
            font=(UI_FONT, UI_BODY_SIZE),
        ).grid(row=0, column=0, sticky='w', padx=(0, 8))
        self.filter_var = tk.StringVar(value='')
        self.filter_entry = ttk.Entry(filter_row, textvariable=self.filter_var)
        self.filter_entry.grid(row=0, column=1, sticky='ew')
        self.filter_need_var = tk.StringVar(value='全部')
        self.filter_need_combo = add_combo_arrow(prepare_combobox(ttk.Combobox(
            filter_row, textvariable=self.filter_need_var, state='readonly',
            values=('全部', '请处理', '请确认'), width=9)))
        self.filter_need_combo.grid(row=0, column=2, sticky='e', padx=(8, 0))
        self.filter_count = tk.Label(
            filter_row, text='', bg=UI_CARD, fg=UI_MUTED,
            font=(UI_FONT, UI_SMALL_SIZE), width=14, anchor='e',
        )
        self.filter_count.grid(row=0, column=3, sticky='e', padx=(10, 0))
        self.filter_var.trace_add('write', lambda *args: self._on_filter_change())
        self.filter_need_combo.bind(
            '<<ComboboxSelected>>', lambda event: self._on_filter_change())

        tree_border = tk.Frame(list_body, bg=UI_FIELD_BORDER)
        tree_border.grid(row=1, column=0, sticky='nsew')
        self.tree_border = tree_border
        border_px = px(1)
        tree_frame = tk.Frame(tree_border, bg=UI_FIELD)
        tree_frame.pack(fill='both', expand=True, padx=border_px, pady=border_px)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            tree_frame, columns=('idx', 'need', 'problem', 'text'), show='headings',
            style='Editor.Treeview', height=5, selectmode='browse',
        )
        for column, title, width, anchor in (
            ('idx', '#', px(46), 'center'),
            ('need', '类型', px(70), 'center'),
            ('problem', '问题', px(170), 'w'),
            ('text', '文本', px(360), 'w'),
        ):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, anchor=anchor, stretch=column == 'text')
        self.tree.grid(row=0, column=0, sticky='nsew')
        tree_scroll = ttk.Scrollbar(tree_frame, orient='vertical', command=self.tree.yview)
        tree_scroll.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.tag_configure('process', foreground=UI_DANGER)
        self.tree.tag_configure('verify', foreground=UI_CONFIRM)
        self.tree.bind('<<TreeviewSelect>>', self.on_select)
        self.tree.bind(
            '<FocusIn>',
            lambda event: self.tree_border.configure(bg=UI_ACCENT))
        self.tree.bind(
            '<FocusOut>',
            lambda event: self.tree_border.configure(bg=UI_FIELD_BORDER))
        self.row_menu = tk.Menu(self.tree, tearoff=0)
        self.row_menu.add_command(
            label='删除字幕条', command=self.delete_selected_row)
        self.tree.bind('<Button-3>', self._show_row_menu)

        bottom = tk.Frame(list_body, bg=UI_CARD)
        bottom.grid(row=2, column=0, sticky='ew', pady=(8, 0))
        bottom.columnconfigure(1, weight=1)

        form = tk.Frame(bottom, bg=UI_CARD)
        form.grid(row=0, column=0, sticky='nw')

        self.start_var = tk.StringVar(value='')
        self.end_var = tk.StringVar(value='')
        self.style_var = tk.StringVar(value='')

        def form_label(parent, text):
            label = tk.Label(
                parent, text=text, bg=UI_CARD, fg=UI_TEXT,
                font=(UI_FONT, UI_BODY_SIZE), width=4, anchor='w')
            label.pack(side='left', padx=(0, 6))
            return label

        style_row = tk.Frame(form, bg=UI_CARD)
        style_row.pack(anchor='w', pady=(0, 4))
        form_label(style_row, '样式')
        self.style_combo = add_combo_arrow(prepare_combobox(ttk.Combobox(
            style_row, textvariable=self.style_var, state='readonly', width=24)))
        self.style_combo.pack(side='left')

        start_row = tk.Frame(form, bg=UI_CARD)
        start_row.pack(anchor='w', pady=(0, 4))
        form_label(start_row, '开始')
        ttk.Entry(start_row, textvariable=self.start_var, width=12).pack(side='left')
        end_row = tk.Frame(form, bg=UI_CARD)
        end_row.pack(anchor='w')
        form_label(end_row, '结束')
        ttk.Entry(end_row, textvariable=self.end_var, width=12).pack(side='left')
        text_frame = tk.Frame(bottom, bg=UI_CARD)
        text_frame.grid(row=0, column=1, sticky='nsew', padx=(16, 0))
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)
        text_border = ttk.Frame(text_frame, style='Field.TFrame')
        text_border.grid(row=0, column=0, sticky='nsew')
        text_border.rowconfigure(0, weight=1)
        text_border.columnconfigure(0, weight=1)
        self.text_edit = tk.Text(
            text_border, height=4, width=24, wrap='word', undo=True,
            font=(UI_FONT, UI_BODY_SIZE), bg=UI_FIELD, fg=UI_TEXT,
            insertbackground=UI_TEXT, selectbackground=UI_SELECT,
            selectforeground=UI_TEXT, relief='flat', bd=0,
            highlightthickness=0, padx=8, pady=6,
        )
        # 内缩 2px：让 Entry.field 的 bordercolor + lightcolor 两层都露出来，
        # 聚焦时与 TEntry 一样显示 2px 强调色边框。
        self.text_edit.grid(row=0, column=0, sticky='nsew', padx=2, pady=2)
        self.text_edit.bind(
            '<FocusIn>',
            lambda event: text_border.configure(style='FieldFocus.TFrame'))
        self.text_edit.bind(
            '<FocusOut>',
            lambda event: text_border.configure(style='Field.TFrame'))
        self.text_count = tk.Label(
            text_frame, text='', bg=UI_CARD, fg=UI_MUTED,
            font=(UI_FONT, UI_SMALL_SIZE), anchor='w',
        )
        self.text_count.grid(row=1, column=0, sticky='ew', pady=(4, 0))
        self.text_edit.bind('<<Modified>>', self._on_text_modified)
        self.save_hint = tk.Label(
            bottom,
            text='载入问题数据后可保存修改；双语片源以简英双语为基础，单语片源以简体中文为基础。',
            bg=UI_CARD, fg=UI_MUTED, font=(UI_FONT, UI_SMALL_SIZE),
        )
        self.save_hint.grid(row=1, column=0, columnspan=2, sticky='w', pady=(6, 0))

        action = tk.Frame(self, bg=UI_BG)
        action.pack(side='bottom', fill='x', pady=(8, 8))
        self.btn_apply = ttk.Button(
            action, text='应用修改', command=self.apply_current,
            style='Secondary.TButton', state='disabled')
        self.btn_apply.pack(side='right', padx=(0, 8))
        self.btn_undo = ttk.Button(
            action, text='撤回', command=self.undo_last,
            style='Secondary.TButton', state='disabled')
        self.btn_undo.pack(side='right', padx=(0, 20))
        ToolTip(self.btn_undo, '撤回上一步「应用修改」；如果上一步是删除字幕条，则从备份恢复并重新分析。')
        self.btn_save = ttk.Button(
            action, text='保存并同步', command=self.save_and_regenerate,
            style='Accent.TButton', state='disabled')
        self.btn_save.pack(side='left')
        self.btn_reload = ttk.Button(
            action, text='重新载入', command=self.reload,
            style='Secondary.TButton', state='disabled')
        self.btn_reload.pack(side='left', padx=(10, 0))

        self.status_var = tk.StringVar(value='请选择问题数据文件')
        self.status_label = ttk.Label(
            action, textvariable=self.status_var, style='Status.TLabel',
            anchor='w')
        self.status_label.pack(
            side='left', fill='x', expand=True, padx=(16, 8))

    def _buttons(self, enabled: bool):
        state = 'normal' if enabled else 'disabled'
        for button in (self.btn_apply, self.btn_save, self.btn_reload,
                       getattr(self, 'btn_choose_file', None)):
            if button is not None:
                button.config(state=state)
        self.btn_undo.config(
            state='normal' if enabled and self.undo_stack else 'disabled')

    def _status(self, text: str):
        self.status_var.set(text)

    def choose_file(self):
        if self.running:
            return
        if self.dirty and not messagebox.askyesno(
                '未保存', '当前修改尚未保存，仍要打开其他文件吗？', parent=self.winfo_toplevel()):
            return
        path = filedialog.askopenfilename(
            title='选择问题数据',
            initialdir=last_directory('fixer_file'),
            filetypes=[
                ('问题数据 / 报告', '*.json *.html'),
                ('问题数据', '*.json'),
                ('问题报告', '*.html'),
                ('所有文件', '*.*'),
            ],
            parent=self.winfo_toplevel(),
        )
        if path:
            remember_directory('fixer_file', path)
            self.load(path)

    def reload(self):
        if self.running or not self.data:
            return
        self.load(os.path.join(self.output_dir, NOTICES_DATA_NAME))

    def _update_save_mode(self):
        if self.primary_output:
            self.btn_save.config(text='保存并同步')
            self.save_hint.config(
                text='保存会写回主字幕，并同步简体 / 繁体 / 英文等其他语言文件，然后重新分析生成问题报告。')
        else:
            self.btn_save.config(text='保存并重新生成')
            self.save_hint.config(
                text='保存会重新生成整个输出文件夹；繁体中文会重新经过繁化姬。原字幕文件不会被修改。')

    def _primary_output_path(self, data):
        """生成后的主字幕：简英双语；单语片源用简体中文。"""
        return primary_output_path(data)

    @staticmethod
    def _leading_tags(text):
        return leading_tags(text)

    @staticmethod
    def _strip_leading_tags(text):
        return strip_leading_tags(text)

    def _start_backup_batch(self):
        stamp = time.strftime('%Y%m%d_%H%M%S')
        folder = os.path.join(BACKUP_ROOT, stamp)
        counter = 1
        while os.path.exists(folder):
            folder = os.path.join(BACKUP_ROOT, f'{stamp}_{counter}')
            counter += 1
        try:
            os.makedirs(folder, exist_ok=True)
            self.backup_dir = folder
            batches = sorted(
                (name for name in os.listdir(BACKUP_ROOT)
                 if os.path.isdir(os.path.join(BACKUP_ROOT, name))),
                reverse=True)
            for old in batches[10:]:
                shutil.rmtree(os.path.join(BACKUP_ROOT, old), ignore_errors=True)
        except OSError:
            self.backup_dir = ''

    def _write_ass_lines(self, path, lines):
        if self.backup_dir:
            try:
                os.makedirs(self.backup_dir, exist_ok=True)
                shutil.copy2(path, os.path.join(self.backup_dir, os.path.basename(path)))
            except OSError as exc:
                log_exception('备份字幕文件', exc)
        else:
            shutil.copy2(path, path + '.bak')
        write_ass_lines(path, lines)

    def _sync_outputs(self, lines):
        """把主字幕的文本同步到其他语言文件。"""
        layout = layout_for_primary(self.primary_output)
        meta = dict(self.data.get('meta') or {})
        result = sync_outputs(
            layout, self.primary_output, lines, meta, self._cht_cache)
        self._cht_cache = result.cht_cache
        return result

    def _delete_from_outputs(self, lines, position):
        """从其他语言文件中删除与主字幕第 position 条对应的行。"""
        layout = layout_for_primary(self.primary_output)
        result = delete_dialogue(layout, self.primary_output, lines, position)
        return result.deleted, result.skipped
    def _show_row_menu(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        self.tree.focus(row)
        self.on_select()
        enabled = bool(self.data) and not self.running and not self.dirty
        self.row_menu.entryconfigure(0, state='normal' if enabled else 'disabled')
        try:
            self.row_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.row_menu.grab_release()

    def delete_selected_row(self):
        if self.running or not self.data:
            return
        if self.dirty or self.edits:
            messagebox.showinfo(
                '请先保存', '请先保存当前修改，再删除字幕条。',
                parent=self.winfo_toplevel())
            return
        selection = self.tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if index not in self.entries:
            return
        _start, _end, _style, _text, preview = self._entry_values(index)
        if not messagebox.askyesno(
                '删除字幕条',
                f'确定删除第 {index} 条字幕吗？\n\n{preview[:160]}\n\n'
                '所有语言字幕中的对应行都会删除，并重新分析问题。',
                parent=self.winfo_toplevel()):
            return
        position = index - 1
        if not (0 <= position < len(self.dialogues)):
            return
        original_lines = list(self.source_lines)
        line_index = self.dialogues[position]
        if not (0 <= line_index < len(original_lines)):
            return
        new_lines = list(original_lines)
        del new_lines[line_index]
        self._start_backup_batch()
        try:
            self._write_ass_lines(self.primary_output, new_lines)
        except OSError as exc:
            messagebox.showerror(
                '删除失败', str(exc), parent=self.winfo_toplevel())
            return
        self.source_lines = new_lines
        self.running = True
        self._buttons(False)
        self._status('正在同步删除并重新分析…')
        def worker(emit):
            deleted = []
            skipped = []
            sync_error = ''
            try:
                deleted, skipped = self._delete_from_outputs(
                    original_lines, position)
            except Exception as exc:
                sync_error = str(exc)
                log_exception('删除同步其他语言字幕', exc)
            analysed = True
            try:
                self._reanalyse_primary()
            except Exception as exc:
                analysed = False
                log_exception('删除后重新分析', exc)
            emit('delete_done', index, line_index, deleted, skipped,
                 sync_error, analysed)

        self.task_runner.start(worker, self._task_event)

    def _cleanup_legacy_baks(self):
        """清理旧版本在输出目录留下的同名 .bak 文件。"""
        if not self.output_dir or not self.primary_output or not self.backup_dir:
            return
        names = set(layout_for_primary(self.primary_output).outputs)
        names.add(os.path.basename(self.primary_output))
        for name in names:
            path = os.path.join(self.output_dir, name + '.bak')
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass

    def _reanalyse_primary(self):
        meta = dict(self.data.get('meta') or {})
        meta['title'] = self.data.get('title', '')
        meta['output_root'] = os.path.dirname(self.output_dir)
        meta['source_label'] = (
            self.data.get('source_label')
            or os.path.basename(self.primary_output))
        if self._cht_cache:
            meta['cht_cache'] = self._cht_cache
        process_file(
            self.primary_output,
            bool(self.data.get('is_hdr')),
            bool(self.data.get('is_mid_black')),
            meta,
            write_outputs=False,
        )

    def load(self, path: str, silent: bool = False, reset_undo: bool = True):
        if self.running:
            return
        if path.lower().endswith('.html'):
            path = os.path.join(os.path.dirname(path), NOTICES_DATA_NAME)
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            if not silent:
                messagebox.showerror(
                    '无法读取',
                    f'{exc}\n\n请先用当前版本重新生成一次字幕，输出文件夹里会生成「{NOTICES_DATA_NAME}」。',
                    parent=self.winfo_toplevel())
            return
        if not isinstance(data, dict):
            if not silent:
                messagebox.showerror(
                    '无法读取',
                    f'问题数据格式不正确。\n\n请先用当前版本重新生成一次字幕，输出文件夹里会生成「{NOTICES_DATA_NAME}」。',
                    parent=self.winfo_toplevel())
            return
        primary_output = self._primary_output_path(data)
        source_path = primary_output or str(data.get('source') or '')
        text = read_ass_text(source_path) if source_path else ''
        if primary_output and not text:
            # 生成文件读不到时退回原始字幕
            primary_output = ''
            source_path = str(data.get('source') or '')
            text = read_ass_text(source_path)
        if not os.path.isfile(source_path):
            if not silent:
                messagebox.showerror(
                    '找不到源字幕', f'源文件不存在：\n{source_path}', parent=self.winfo_toplevel())
            return
        if not text:
            if not silent:
                messagebox.showerror(
                    '无法读取源字幕', '源字幕为空或无法识别编码', parent=self.winfo_toplevel())
            return
        # 只有退回原始 SRT 时才需要转换；生成后的主字幕已经是 ASS
        source_is_srt = (
            not primary_output
            and (source_path.lower().endswith('.srt')
                 or ('[Events]' not in text and '-->' in text)))
        if source_is_srt:
            text = srt_to_ass(text)
        lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        dialogues = []
        in_events = False
        styles = []
        in_styles = False
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('['):
                in_events = stripped.lower() == '[events]'
                in_styles = stripped.lower() in ('[v4+ styles]', '[v4 styles]')
                continue
            if in_styles and line.lower().startswith('style:'):
                name = line.split(':', 1)[1].split(',', 1)[0].strip()
                if name and name not in styles:
                    styles.append(name)
                continue
            if not in_events or not line.lower().startswith('dialogue:'):
                continue
            if len(line.split(',', 9)) >= 10:
                dialogues.append(index)

        entries = {
            index: item
            for index, item in parse_issue_entries(data).items()
            if 1 <= index <= len(dialogues)
        }

        self.data = data
        self.output_dir = str(data.get('output_dir') or os.path.dirname(path))
        self.source_path = source_path
        self.primary_output = primary_output
        self.source_is_srt = source_is_srt
        self.dialogues = dialogues
        self.source_lines = lines
        self.styles = styles
        self.style_combo.configure(values=styles)
        self.entries = entries
        self.edits = {}
        if reset_undo:
            self.undo_stack.clear()
        self.dirty = False
        self._update_save_mode()
        self.path_var.set(path)
        self._buttons(True)
        self.refresh_tree(select=next(iter(sorted(entries)), None))
        process_count = sum(1 for item in entries.values() if item['need'] == '请处理')
        verify_count = len(entries) - process_count
        self._status(f'待处理 {len(entries)} 条（请处理 {process_count} / 请确认 {verify_count}）')

    def _entry_values(self, index):
        edit = self.edits.get(index)
        if edit:
            start, end, style, text = edit['start'], edit['end'], edit['style'], edit['text']
        else:
            fields = self.source_lines[self.dialogues[index - 1]].split(',', 9)
            start = parse_ass_time(fields[1]) or 0
            end = parse_ass_time(fields[2]) or 0
            style = fields[3].strip()
            text = fields[9]
        preview = text.replace('\r', '').replace('\n', '\\N')
        return start, end, style, text, preview

    def _visible_indexes(self):
        text = (self.filter_text or '').casefold()
        need = self.filter_need or ''
        result = []
        for index in sorted(self.entries):
            item = self.entries[index]
            if need and item['need'] != need:
                continue
            if text:
                preview = self._entry_values(index)[4]
                haystack = (
                    f'{index} {item["need"]} '
                    + ' '.join(item['problems']) + ' ' + preview).casefold()
                if text not in haystack:
                    continue
            result.append(index)
        return result

    def _update_filter_count(self, visible=None):
        total = len(self.entries)
        if visible is None:
            visible = self._visible_indexes()
        if not total:
            self.filter_count.config(text='')
        elif len(visible) == total:
            self.filter_count.config(text=f'共 {total} 条')
        else:
            self.filter_count.config(text=f'显示 {len(visible)} / {total} 条')

    def _on_filter_change(self):
        self.filter_text = self.filter_var.get().strip()
        need = self.filter_need_var.get().strip()
        self.filter_need = '' if need in ('', '全部') else need
        if self._filter_job is not None:
            try:
                self.after_cancel(self._filter_job)
            except tk.TclError:
                pass
        self._filter_job = self.after(120, self._apply_filter)

    def _apply_filter(self):
        self._filter_job = None
        if self.entries:
            self.refresh_tree()
        else:
            self._update_filter_count([])

    def _row_values(self, index):
        item = self.entries[index]
        _, _, _, _, preview = self._entry_values(index)
        tag = 'process' if item['need'] == '请处理' else 'verify'
        return (
            index, item['need'], '；'.join(item['problems']), preview), tag

    def refresh_row(self, index):
        """只刷新一行，避免每次应用修改都重建整张表。"""
        iid = str(index)
        if index not in self.entries or index not in self._visible_indexes():
            if self.tree.exists(iid):
                self.tree.delete(iid)
            self._update_filter_count()
            return
        values, tag = self._row_values(index)
        if self.tree.exists(iid):
            self.tree.item(iid, values=values, tags=(tag,))
        else:
            self.tree.insert('', 'end', iid=iid, values=values, tags=(tag,))
        self._update_filter_count()

    def refresh_tree(self, select=None):
        self.tree.delete(*self.tree.get_children())
        visible = self._visible_indexes()
        for index in visible:
            values, tag = self._row_values(index)
            self.tree.insert('', 'end', iid=str(index), values=values, tags=(tag,))
        self._update_filter_count(visible)
        if select is not None and str(select) in self.tree.get_children():
            iid = str(select)
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)
            self.on_select()

    def _next_visible_index(self, index):
        for candidate in self._visible_indexes():
            if candidate > index:
                return candidate
        return None

    def on_select(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if index not in self.entries:
            return
        start, end, style, text, _ = self._entry_values(index)
        self.start_var.set(format_ass_time(start))
        self.end_var.set(format_ass_time(end))
        self.style_var.set(style)
        self.text_edit.delete('1.0', 'end')
        self.text_edit.insert('1.0', text)
        self.update_text_count()

    def _on_text_modified(self, event=None):
        try:
            if not self.text_edit.edit_modified():
                return
            self.text_edit.edit_modified(False)
        except tk.TclError:
            return
        self.update_text_count()

    def update_text_count(self):
        stats = measure_text(self.text_edit.get('1.0', 'end-1c'))
        self.text_count.config(
            text=(
                f'中文 {stats["zh"]}/{stats["zh_limit"]} 字 · '
                f'英文 {stats["en"]}/{stats["en_limit"]} 字符'
            ),
            fg=UI_DANGER if stats['over'] else UI_MUTED,
        )

    def _current_edit(self):
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo('提示', '请先在上方列表选择一条字幕', parent=self.winfo_toplevel())
            return None
        index = int(selection[0])
        start = parse_ass_time(self.start_var.get())
        end = parse_ass_time(self.end_var.get())
        if start is None or end is None:
            messagebox.showinfo('时间格式有误', '请使用 0:00:01.00 这样的时间格式', parent=self.winfo_toplevel())
            return None
        if end <= start:
            messagebox.showinfo('时间有误', '结束时间必须晚于开始时间', parent=self.winfo_toplevel())
            return None
        text = self.text_edit.get('1.0', 'end-1c')
        text = text.replace('\r\n', '\n').replace('\r', '\n').replace('\n', '\\N')
        style = self.style_var.get().strip()
        return index, {'start': start, 'end': end, 'style': style, 'text': text}

    def apply_current(self):
        result = self._current_edit()
        if not result:
            return
        index, edit = result
        previous = self.edits.get(index)
        self.undo_stack.append((
            'edit', index, dict(previous) if previous is not None else None))
        if len(self.undo_stack) > 10:
            self.undo_stack.pop(0)
        self.edits[index] = edit
        self.dirty = True
        self.btn_undo.config(state='normal')
        self.refresh_row(index)
        next_index = self._next_visible_index(index)
        target = next_index if next_index is not None else index
        iid = str(target)
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.tree.see(iid)
            self.on_select()
        self._status(
            f'已修改 {len(self.edits)} 条 · 点「{self.btn_save.cget("text")}」写入字幕')

    def _restore_backup(self, backup_dir):
        if not backup_dir or not os.path.isdir(backup_dir):
            raise OSError(f'备份目录不存在：{backup_dir or "（无）"}')
        if not self.output_dir or not os.path.isdir(self.output_dir):
            raise OSError('输出目录不存在，无法恢复备份')
        restored = 0
        for name in os.listdir(backup_dir):
            if not name.lower().endswith('.ass'):
                continue
            source = os.path.join(backup_dir, name)
            if not os.path.isfile(source):
                continue
            shutil.copy2(source, os.path.join(self.output_dir, name))
            restored += 1
        if not restored:
            raise OSError('备份目录里没有字幕文件')

    def undo_last(self):
        if self.running or not self.undo_stack:
            return
        action = self.undo_stack.pop()
        if action[0] == 'edit':
            _kind, index, previous = action
            if previous is None:
                self.edits.pop(index, None)
            else:
                self.edits[index] = previous
            self.dirty = bool(self.edits)
            self.refresh_row(index)
            try:
                self.tree.selection_set(str(index))
                self.on_select()
            except tk.TclError:
                pass
            self._buttons(True)
            self._status('已撤回上一步修改')
            return
        _kind, backup_dir, label = action
        self.running = True
        self._buttons(False)
        self._status(f'正在撤回：{label}…')
        def worker(emit):
            error = ''
            try:
                self._restore_backup(backup_dir)
                self._reanalyse_primary()
            except Exception as exc:
                error = str(exc)
                log_exception('撤回操作', exc)
            emit('undo_done', label, error)

        self.task_runner.start(worker, self._task_event)

    def save_and_regenerate(self):
        if self.running or not self.data:
            return
        selection = self.tree.selection()
        if selection:
            index = int(selection[0])
            start, end, style, text, _ = self._entry_values(index)
            current = (
                self.start_var.get().strip(), self.end_var.get().strip(),
                self.style_var.get().strip(),
                self.text_edit.get('1.0', 'end-1c'),
            )
            if current != (
                    format_ass_time(start), format_ass_time(end), style, text):
                result = self._current_edit()
                if not result:
                    return
                self.edits[result[0]] = result[1]

        if getattr(self, 'source_lines', None):
            # 与载入时使用同一份转换结果，保证行号一致
            lines = list(self.source_lines)
        else:
            text = read_ass_text(self.source_path)
            if getattr(self, 'source_is_srt', False):
                text = srt_to_ass(text)
            lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        for index, edit in self.edits.items():
            if not 1 <= index <= len(self.dialogues):
                continue
            line_index = self.dialogues[index - 1]
            fields = lines[line_index].split(',', 9)
            if len(fields) < 10:
                continue
            fields[1] = format_ass_time(edit['start'])
            fields[2] = format_ass_time(edit['end'])
            if edit.get('style'):
                fields[3] = edit['style']
            fields[9] = edit['text']
            lines[line_index] = ','.join(fields)

        if self.primary_output:
            self._save_primary_lines(lines)
            return

        output_dir = self.output_dir or str(self.data.get('output_dir') or '')
        if not output_dir:
            messagebox.showerror('缺少输出目录', '问题数据里没有输出目录信息', parent=self.winfo_toplevel())
            return
        if getattr(self, 'source_is_srt', False):
            # SRT 在载入时已转换为 ASS，重新生成也用 ASS
            extension = '.ass'
        else:
            extension = os.path.splitext(self.source_path)[1] or '.ass'
        working = os.path.join(output_dir, f'{EDIT_SOURCE_PREFIX}{extension}')
        if os.path.abspath(self.source_path) == os.path.abspath(working):
            working = self.source_path
        try:
            os.makedirs(output_dir, exist_ok=True)
            write_ass_lines(working, lines)
        except OSError as exc:
            messagebox.showerror('保存失败', str(exc), parent=self.winfo_toplevel())
            return

        meta = dict(self.data.get('meta') or {})
        meta['title'] = self.data.get('title', '')
        meta['output_root'] = os.path.dirname(output_dir)
        meta['source_label'] = self.data.get('source_label') or os.path.basename(self.source_path)

        self.running = True
        self._buttons(False)
        self._status('正在重新生成字幕…')
        is_hdr = bool(self.data.get('is_hdr'))
        is_mid_black = bool(self.data.get('is_mid_black'))

        def worker(emit):
            try:
                _, outdir, _ = process_file(working, is_hdr, is_mid_black, meta)
                emit('done', outdir)
            except Exception as exc:
                log_exception('问题修复重新生成', exc)
                emit('error', str(exc))

        self.task_runner.start(worker, self._task_event)

    def _save_primary_lines(self, lines):
        target = self.primary_output
        if not target or not os.path.isfile(target):
            messagebox.showerror(
                '保存失败', '生成后的字幕文件不存在，请重新生成一次字幕',
                parent=self.winfo_toplevel())
            return
        self._start_backup_batch()
        try:
            self._write_ass_lines(target, lines)
        except OSError as exc:
            messagebox.showerror(
                '保存失败', str(exc), parent=self.winfo_toplevel())
            return
        self.source_lines = list(lines)
        self.running = True
        self._buttons(False)
        self._status('正在同步其他语言字幕…')
        def worker(emit):
            sync = None
            sync_error = ''
            try:
                sync = self._sync_outputs(lines)
            except Exception as exc:
                sync_error = str(exc)
                log_exception('同步其他语言字幕', exc)
            analysed = True
            try:
                self._reanalyse_primary()
            except Exception as exc:
                analysed = False
                log_exception('问题修复重新分析', exc)
            emit('primary_done', sync, sync_error, analysed, target)

        self.task_runner.start(worker, self._task_event)

    def _task_event(self, kind, *payload):
        self.running = False
        self._buttons(True)
        if kind == 'primary_done':
            sync, sync_error, analysed, target = payload
            self.dirty = False
            self.edits = {}
            self.undo_stack.clear()
            if analysed:
                json_path = os.path.join(self.output_dir, NOTICES_DATA_NAME)
                if os.path.isfile(json_path):
                    self.load(json_path, silent=True, reset_undo=False)
            self._buttons(True)
            synced = list(getattr(sync, 'synced', []) or [])
            skipped = list(getattr(sync, 'skipped', []) or [])
            conversion_error = str(getattr(sync, 'error', '') or '')
            summary = '、'.join(synced) if synced else '没有需要同步的其他语言文件'
            self._status(f'已保存并同步：{summary}')
            lines = [
                f'已写回：\n{target}',
                f'已同步：{summary}',
            ]
            if skipped:
                lines.append('未同步（行数不一致）：' + '、'.join(skipped))
            if sync_error:
                lines.append(f'同步失败：{sync_error}')
            if conversion_error:
                lines.append(f'繁体转换提示：{conversion_error}')
            lines.append(
                '问题报告已重新分析。' if analysed
                else '问题报告未能重新分析，详见错误日志。')
            if self.backup_dir:
                lines.append(f'备份：{self.backup_dir}')
            self._cleanup_legacy_baks()
            if sync_error or skipped or conversion_error or not analysed:
                messagebox.showwarning(
                    '保存完成（部分失败）', '\n\n'.join(lines),
                    parent=self.winfo_toplevel())
            else:
                messagebox.showinfo(
                    '保存完成', '\n\n'.join(lines),
                    parent=self.winfo_toplevel())
            return False
        if kind == 'delete_done':
            (index, line_index, deleted, skipped,
             sync_error, analysed) = payload
            self.dirty = False
            if analysed:
                json_path = os.path.join(self.output_dir, NOTICES_DATA_NAME)
                if os.path.isfile(json_path):
                    self.load(json_path, silent=True, reset_undo=False)
            else:
                self.dialogues = [
                    pos if pos < line_index else pos - 1
                    for pos in self.dialogues if pos != line_index]
                self.entries.pop(index, None)
                self.refresh_tree()
            if self.backup_dir:
                self.undo_stack.append(('restore', self.backup_dir, f'删除第 {index} 条'))
                if len(self.undo_stack) > 10:
                    self.undo_stack.pop(0)
            self._buttons(True)
            summary = '、'.join(deleted) if deleted else '没有需要同步的其他语言文件'
            lines = [
                f'已删除第 {index} 条字幕。',
                f'同步：{summary}',
            ]
            if skipped:
                lines.append(f'跳过：{"、".join(skipped)}')
            if sync_error:
                lines.append(f'同步失败：{sync_error}')
            lines.append(
                '问题报告已重新分析。' if analysed
                else '问题报告未能重新分析，详见错误日志。')
            if self.backup_dir:
                lines.append(f'备份：{self.backup_dir}')
            self._cleanup_legacy_baks()
            self._status('已删除字幕条')
            if sync_error or skipped or not analysed:
                messagebox.showwarning(
                    '删除完成（部分失败）', '\n\n'.join(lines),
                    parent=self.winfo_toplevel())
            else:
                messagebox.showinfo(
                    '删除完成', '\n\n'.join(lines),
                    parent=self.winfo_toplevel())
            return False
        if kind == 'undo_done':
            label, error = payload
            if error:
                self._status('撤回失败')
                messagebox.showerror(
                    '撤回失败', error, parent=self.winfo_toplevel())
                return False
            json_path = os.path.join(self.output_dir, NOTICES_DATA_NAME)
            if os.path.isfile(json_path):
                self.load(json_path, silent=True, reset_undo=False)
            self._status(f'已撤回：{label}')
            messagebox.showinfo(
                '撤回完成',
                f'已撤回：{label}\n字幕文件与问题报告已恢复。',
                parent=self.winfo_toplevel())
            return False
        if kind == 'error':
            self._status('重新生成失败')
            messagebox.showerror(
                '重新生成失败', payload[0], parent=self.winfo_toplevel())
            return False
        outdir = payload[0]
        self.dirty = False
        self.load(os.path.join(outdir, NOTICES_DATA_NAME))
        messagebox.showinfo(
            '已更新',
            '字幕与问题报告已重新生成。\n繁体中文已重新经过繁化姬处理。',
            parent=self.winfo_toplevel(),
        )
        return False
