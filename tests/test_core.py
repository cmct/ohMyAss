"""核心逻辑单元测试（时间轴、样式、术语、报告解析）。

运行：python -m unittest discover -s tests -v
"""

import hashlib
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app_info  # noqa: E402
import config_store  # noqa: E402
import reports  # noqa: E402
import subtitle_core as core  # noqa: E402


class TimeTests(unittest.TestCase):
    def test_parse_ass_time(self):
        self.assertEqual(core.parse_ass_time('0:00:01.23'), 1230)
        self.assertEqual(core.parse_ass_time('1:02:03.45'), 3723450)
        self.assertEqual(core.parse_ass_time('00:00:00.00'), 0)
        self.assertIsNone(core.parse_ass_time('bad'))
        self.assertIsNone(core.parse_ass_time('1:2'))

    def test_format_ass_time(self):
        self.assertEqual(core.format_ass_time(1230), '0:00:01.23')
        self.assertEqual(core.format_ass_time(-5), '0:00:00.00')
        self.assertEqual(core.format_ass_time(3723450), '1:02:03.45')

    def test_srt_time_to_ass(self):
        self.assertEqual(core.srt_time_to_ass('00:00:01,500'), '0:00:01.50')
        self.assertEqual(core.srt_time_to_ass('01:02:03.456'), '1:02:03.45')
        self.assertIsNone(core.srt_time_to_ass('nope'))

    def test_srt_to_ass(self):
        text = (
            '1\n'
            '00:00:01,000 --> 00:00:02,500\n'
            '第一行\n'
            '第二行\n\n'
            '2\n'
            '00:00:03,000 --> 00:00:04,000\n'
            '<i>Hi</i>\n'
        )
        result = core.srt_to_ass(text)
        self.assertIn('Style: Default', result)
        self.assertIn('Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,第一行\\N第二行', result)
        self.assertIn('Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,Hi', result)


class TimelineGapTests(unittest.TestCase):
    def test_gap_reports_previous_event(self):
        sub = core.Aegisub('')
        sub.timeline_events = [
            {'index': 17, 'start': 0, 'end': 1000, 'align': 2},
            {'index': 18, 'start': 1100, 'end': 2000, 'align': 2},
        ]
        sub.check_timeline_gaps()
        self.assertEqual(len(sub.notices), 1)
        self.assertTrue(sub.notices[0].startswith('17,'))
        self.assertIn('下一条(18)', sub.notices[0])
        self.assertIn('200ms', sub.notices[0])

    def test_gap_skips_top8_bottom2_pair(self):
        sub = core.Aegisub('')
        sub.timeline_events = [
            {'index': 3, 'start': 0, 'end': 1000, 'align': 2},
            {'index': 4, 'start': 1100, 'end': 2000, 'align': 8},
        ]
        sub.check_timeline_gaps()
        self.assertEqual(sub.notices, [])


class StyleTests(unittest.TestCase):
    def test_default_style_lines_are_complete(self):
        for kind, _label in core.STYLE_CATEGORIES:
            line = core.default_style_line(kind)
            self.assertTrue(line.startswith(f'Style: {kind},'), kind)
            self.assertEqual(len(line.split(',')), 23, kind)

    def test_style_category_maps(self):
        self.assertIn('Default', core.STYLE_KIND_TO_LABEL)
        self.assertEqual(len(core.STYLE_CATEGORIES), 11)
        for kind, label in core.STYLE_CATEGORIES:
            self.assertEqual(core.STYLE_LABEL_TO_KIND[label], kind)

    def test_mid_black_margins_follow_defaults(self):
        kinds = {kind for kind, _label in core.STYLE_CATEGORIES if kind != 'Default'}
        self.assertEqual(set(core.MID_BLACK_MARGINS), kinds)
        for kind in kinds:
            margin = int(core.default_style_line(kind).split(',')[21])
            self.assertEqual(core.MID_BLACK_MARGINS[kind], margin + 44)

    def test_apply_updated_margin_only(self):
        kind = 'zh_dialogue_bilingual'
        base = core.default_style_line(kind).split(',')
        changed = list(base)
        changed[21] = '99'
        updated = core.apply_updated_margin_v(','.join(changed), kind, 58)
        self.assertEqual(updated.split(',')[21], '58')

        other = list(base)
        other[2] = '123'
        self.assertEqual(
            core.apply_updated_margin_v(','.join(other), kind, 58),
            ','.join(other))

    def test_apply_updated_fontsize_only(self):
        kind = 'zh_dialogue_bilingual'
        base = core.default_style_line(kind).split(',')
        changed = list(base)
        changed[2] = '120'
        updated = core.apply_updated_fontsize(','.join(changed), kind, 80)
        self.assertEqual(updated.split(',')[2], '80')

        other = list(base)
        other[21] = '99'
        self.assertEqual(
            core.apply_updated_fontsize(','.join(other), kind, 80),
            ','.join(other))

    def test_linked_fsvp_kinds(self):
        self.assertEqual(
            core.linked_fsvp_kinds('en_dialogue_bilingual'),
            ('zh_dialogue_bilingual', 'en_dialogue_bilingual'))
        self.assertEqual(
            core.linked_fsvp_kinds('zh_note_double_line'),
            ('zh_note_double_line',))

    def test_default_style_fallback_is_default(self):
        line = core.default_style_line('not_a_real_kind')
        self.assertTrue(line.startswith('Style: Default,'))


class GlossaryTests(unittest.TestCase):
    def test_normalize_glossary(self):
        data = [
            {'source': ' Apple ', 'target': '苹果'},
            {'source': 'Apple', 'target': '苹果公司', 'case_sensitive': True},
            {'source': '', 'target': '空'},
            ['Banana', '香蕉', True, '备注'],
            'invalid',
        ]
        result = core.normalize_glossary(data)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['source'], 'Apple')
        self.assertEqual(result[0]['target'], '苹果公司')
        self.assertTrue(result[0]['case_sensitive'])
        self.assertEqual(result[1]['note'], '备注')
        self.assertEqual(core.normalize_glossary(None), [])

    def test_term_in_text(self):
        self.assertTrue(core.term_in_text('Hello World', 'world'))
        self.assertFalse(core.term_in_text('Worldwide', 'world'))
        self.assertFalse(core.term_in_text('Hello World', 'world', case_sensitive=True))
        self.assertTrue(core.term_in_text('这是测试文本', '测试'))
        self.assertFalse(core.term_in_text('', '测试'))

    def test_glossary_consistency(self):
        notices = []

        class FakeSub:
            glossary = core.normalize_glossary([
                {'source': 'Apple', 'target': '苹果'},
                {'source': 'Pear', 'target': '梨子'},
            ])

            def add_notice(self, text):
                notices.append(text)

        sub = FakeSub()
        core.check_glossary_consistency(
            sub,
            [{'text': '我喜欢苹果'}],
            [{'text': 'I like Apple'}],
        )
        self.assertEqual(notices, [])

        notices.clear()
        core.check_glossary_consistency(
            sub,
            [{'text': '我喜欢凤梨'}],
            [{'text': 'I like Pear'}],
        )
        self.assertEqual(len(notices), 1)
        self.assertIn('术语表不符', notices[0])

        notices.clear()
        sub.glossary = []
        core.check_glossary_consistency(sub, [{'text': 'x'}], [{'text': 'Apple'}])
        self.assertEqual(notices, [])


class ReportTests(unittest.TestCase):
    def test_parse_issue_entries(self):
        data = {
            'notices': [
                {'index': 3, 'need': '请处理', 'problem': '对白行数大于2'},
                {'index': '4', 'need': '请确认', 'problem': '疑似英文缺失'},
                {'index': 5, 'need': '请确认', 'problem': '译文缺失'},
                {'index': 5, 'need': '请处理', 'problem': '中文对白行数大于1'},
                {'index': 'bad', 'need': '请处理', 'problem': 'x'},
                {'index': 0, 'need': '请处理', 'problem': 'x'},
                {'index': 6, 'need': '请检查', 'problem': 'x'},
            ]
        }
        entries = reports.parse_issue_entries(data)
        self.assertEqual(set(entries), {3, 4, 5})
        self.assertEqual(entries[3]['need'], '请处理')
        self.assertEqual(entries[4]['need'], '请确认')
        self.assertEqual(entries[5]['need'], '请处理')
        self.assertEqual(entries[5]['problems'],
                         ['译文缺失', '中文对白行数大于1'])
        self.assertEqual(reports.parse_issue_entries([]), {})

    def test_build_notices_html(self):
        page = reports.build_notices_html(
            'demo.ass', '片名', [
                '3,对白行数大于2,请处理',
                '5,疑似英文缺失,请确认',
                '6,译文缺失,请检查',
            ])
        self.assertIn('对白行数大于2', page)
        self.assertIn('请处理', page)
        self.assertIn('请确认', page)
        self.assertIn('请检查', page)
        self.assertIn('问题总数<b>3</b>', page)

    def test_build_notices_html_escapes(self):
        page = reports.build_notices_html(
            'x.ass', '<b>片名</b>', ['1,<script>,请处理'])
        self.assertIn('&lt;b&gt;片名&lt;/b&gt;', page)
        self.assertIn('&lt;script&gt;', page)

    def test_global_notice_shows_full_text(self):
        page = reports.build_notices_html(
            'x.ass', '片名', ['0,繁化姬台湾化失败,请处理'])
        self.assertIn('全文', page)
        self.assertIn('繁化姬台湾化失败', page)


class EffectStyleTests(unittest.TestCase):
    def test_effect_keeps_source_style(self):
        content = (
            '[Script Info]\n'
            'Title: t\n'
            'ScriptType: v4.00+\n'
            'PlayResX: 1920\n'
            'PlayResY: 1080\n'
            '[V4+ Styles]\n'
            'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, '
            'OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, '
            'ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
            'Alignment, MarginL, MarginR, MarginV, Encoding\n'
            'Style: Default,微软雅黑,80,&H00EAEAEA,&HF0000000,&H000D0500,&H00000000,'
            '0,0,0,0,100,100,0,0,1,2,0,2,0,0,66,1\n'
            'Style: FxStyle,微软雅黑,80,&H00EAEAEA,&HF0000000,&H000D0500,&H00000000,'
            '0,0,0,0,100,100,0,0,1,2,0,2,0,0,30,1\n'
            '[Events]\n'
            'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
            'Dialogue: 0,0:00:01.00,0:00:02.00,FxStyle,,0,0,0,,{\\pos(960,540)}特效文字\n'
            'Dialogue: 0,0:00:02.00,0:00:03.00,Default,,0,0,0,,普通中文\n'
        )
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, 'effect.ass')
            with open(source, 'w', encoding='utf-8-sig') as handle:
                handle.write(content)
            meta = {
                'title': '特效测试',
                'script_updated_by': 'tester',
                'taiwan': False,
                'output_root': folder,
                'style_fsvp': {},
                'glossary': [],
                'hdr_brightness': 100,
            }
            _, out_dir, _ = core.process_file(source, False, False, meta)
            text = ''
            for name in os.listdir(out_dir):
                if name.endswith('.ass') and '简体中文' in name:
                    with open(os.path.join(out_dir, name), 'r', encoding='utf-8-sig') as handle:
                        text = handle.read()
                    break
            self.assertIn('Style: FxStyle,', text)
            self.assertIn('Dialogue: 0,0:00:01.00,0:00:02.00,FxStyle,', text)
            self.assertNotIn('Dialogue: 0,0:00:01.00,0:00:02.00,Default,', text)


class FontTests(unittest.TestCase):
    def test_ass_font_names_includes_inline_overrides(self):
        import bdn_sup

        content = (
            '[Script Info]\n'
            'ScriptType: v4.00+\n'
            '[V4+ Styles]\n'
            'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, '
            'OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, '
            'ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
            'Alignment, MarginL, MarginR, MarginV, Encoding\n'
            'Style: Default,样式字体,80,&H00EAEAEA,&HF0000000,&H000D0500,&H00000000,'
            '0,0,0,0,100,100,0,0,1,2,0,2,0,0,14,1\n'
            '[Events]\n'
            'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
            'Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,{\\fn行内字体}第一句\n'
            'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\fn@竖排字体}第二句\n'
            'Dialogue: 0,0:00:02.00,0:00:03.00,Default,,0,0,0,,{\\fn}重置后仍是样式字体\n'
        )
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'fonts.ass')
            with open(path, 'w', encoding='utf-8-sig') as handle:
                handle.write(content)
            self.assertEqual(
                bdn_sup.ass_font_names(path),
                ['样式字体', '行内字体', '竖排字体'])

    def test_ass_vertical_font_names_detects_styles_and_inline(self):
        import bdn_sup

        content = (
            '[Script Info]\n'
            'ScriptType: v4.00+\n'
            '[V4+ Styles]\n'
            'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, '
            'OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, '
            'ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
            'Alignment, MarginL, MarginR, MarginV, Encoding\n'
            'Style: Default,@SimSun,80,&H00EAEAEA,&HF0000000,&H000D0500,'
            '&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,0,0,14,1\n'
            '[Events]\n'
            'Format: Layer, Start, End, Style, Name, MarginL, MarginR, '
            'MarginV, Effect, Text\n'
            'Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,'
            '{\\fn@Source Han Sans}测试\n'
        )
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'vertical.ass')
            with open(path, 'w', encoding='utf-8-sig') as handle:
                handle.write(content)
            self.assertEqual(
                bdn_sup.ass_vertical_font_names(path),
                ['@SimSun', '@Source Han Sans'])


class UpdateScriptTests(unittest.TestCase):
    def test_update_script_is_ascii_and_crlf(self):
        import app_update

        script = app_update.build_app_update_script()
        self.assertTrue(script.isascii())
        self.assertIn('copy /Y "%SOURCE%" "%TARGET%"', script)
        self.assertIn('start "" "%TARGET%"', script)
        self.assertIn('%LOG%', script)
        data = script.encode('ascii')
        # 之前的 bug：文本模式写入导致每行变成 CR CR LF
        self.assertNotIn(b'\r\r\n', data)
        self.assertNotIn(b'\n', data.replace(b'\r\n', b''))

    def test_update_environment_removes_pyinstaller_vars(self):
        import app_update

        base = {
            'PATH': 'x',
            '_PYI_APPLICATION_HOME_DIR': r'C:\Temp\_MEI123',
            '_PYI_ARCHIVE_FILE': r'C:\app.exe',
            '_PYI_PARENT_PROCESS_LEVEL': '1',
            '_MEIPASS2': r'C:\Temp\_MEI456',
            'NORMAL': '1',
        }
        env = app_update.update_environment(base)
        self.assertEqual(env, {'PATH': 'x', 'NORMAL': '1'})
        self.assertIn('set "_PYI_APPLICATION_HOME_DIR="',
                      app_update.build_app_update_script())

    def test_verify_file_digest(self):
        import app_update

        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'a.bin')
            with open(path, 'wb') as handle:
                handle.write(b'ohMyAss update payload')
            digest = hashlib.sha256(b'ohMyAss update payload').hexdigest()
            self.assertTrue(app_update.verify_file_digest(path, 'sha256:' + digest))
            self.assertTrue(app_update.verify_file_digest(path, digest))
            self.assertTrue(app_update.verify_file_digest(path, ''))
            self.assertFalse(app_update.verify_file_digest(path, 'sha256:' + '0' * 64))

    def test_folder_writable(self):
        import app_update

        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(app_update.folder_writable(folder))
            self.assertFalse(
                app_update.folder_writable(os.path.join(folder, 'missing')))


class ConfigTests(unittest.TestCase):
    def test_config_roundtrip_and_error_log(self):
        with tempfile.TemporaryDirectory() as folder:
            old_config = config_store.CONFIG_PATH
            old_log = config_store.ERROR_LOG_PATH
            config_store.CONFIG_PATH = os.path.join(folder, 'ohMyAssNoto.json')
            config_store.ERROR_LOG_PATH = os.path.join(folder, 'error.log')
            try:
                config_store.save_config({'theme_mode': 'dark'})
                self.assertEqual(config_store.load_config(), {'theme_mode': 'dark'})

                with open(config_store.CONFIG_PATH, 'w', encoding='utf-8') as handle:
                    handle.write('{broken')
                self.assertEqual(config_store.load_config(), {})
                self.assertTrue(os.path.isfile(config_store.ERROR_LOG_PATH))
            finally:
                config_store.CONFIG_PATH = old_config
                config_store.ERROR_LOG_PATH = old_log

    def test_sanitize_folder_name(self):
        self.assertEqual(config_store.sanitize_folder_name('a:b*c?'), 'a：b＊c？')
        self.assertEqual(config_store.sanitize_folder_name('name. '), 'name')
        self.assertEqual(config_store.sanitize_folder_name('CON'), 'CON_')
        self.assertEqual(config_store.sanitize_folder_name(''), '')

    def test_cleanup_stale_temp_folders(self):
        with tempfile.TemporaryDirectory() as folder:
            old = os.path.join(folder, '_MEI111')
            fresh = os.path.join(folder, '_MEI222')
            other = os.path.join(folder, 'other_dir')
            os.makedirs(old)
            os.makedirs(fresh)
            os.makedirs(other)
            old_time = time.time() - 2 * 86400
            os.utime(old, (old_time, old_time))
            config_store.cleanup_stale_temp_folders(
                base=folder, max_age_seconds=86400)
            self.assertFalse(os.path.exists(old))
            self.assertTrue(os.path.exists(fresh))
            self.assertTrue(os.path.exists(other))


class VersionTests(unittest.TestCase):
    def test_version_tuple(self):
        self.assertEqual(app_info.version_tuple('v0.5.8'), (0, 5, 8))
        self.assertGreater(
            app_info.version_tuple('v0.5.10'),
            app_info.version_tuple('v0.5.9'))
        self.assertEqual(app_info.version_tuple(''), (0,))


if __name__ == '__main__':
    unittest.main()
