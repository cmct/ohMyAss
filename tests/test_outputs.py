"""输出目录、同步/删除、原子写入、BDN 分段与更新回滚的测试。"""


import os
import struct
import tempfile
import unittest

import app_update
import bdn_sup
import config_store
import subtitle_outputs


HEADER = '''[Script Info]
PlayResX: 1920
PlayResY: 1080
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,微软雅黑,80,&H00EAEAEA,&HF0000000,&H000D0500,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,0,0,66,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''


def ass_text(*dialogues):
    return HEADER + '\n'.join(dialogues) + '\n'


def write(path, text):
    with open(path, 'w', encoding='utf-8-sig', newline='') as handle:
        handle.write(text)


def dialogues(text):
    return [line for line in text.splitlines() if line.startswith('Dialogue:')]


def read(path):
    with open(path, encoding='utf-8-sig') as handle:
        return handle.read()


class LayoutTests(unittest.TestCase):
    def test_bilingual_names(self):
        layout = subtitle_outputs.output_layout(False, False)
        self.assertIn('1-简英双语-zh.ass', layout.outputs)
        self.assertIn('2-简体中文-zh.ass', layout.outputs)
        self.assertEqual(layout.primary_names, ('1-简英双语-zh.ass',))
        self.assertEqual(layout.sdr_name, '')

    def test_hdr_bilingual_names(self):
        layout = subtitle_outputs.output_layout(True, False)
        self.assertEqual(layout.sdr_name, '2-简英双语-SDR-zh.ass')
        self.assertIn('5-英文-HDR-en.ass', layout.outputs)
        roles = dict(layout.targets)
        self.assertEqual(roles['2-简英双语-SDR-zh.ass'], 'sdr')

    def test_chinese_only_names(self):
        layout = subtitle_outputs.output_layout(False, True)
        self.assertEqual(layout.primary_names, ('1-简体中文-zh.ass',))
        self.assertEqual(layout.targets, (('2-繁体中文-zh.ass', 'cht'),))
        hdr = subtitle_outputs.output_layout(True, True)
        self.assertEqual(hdr.sdr_name, '2-简体中文-SDR-zh.ass')

    def test_split_and_measure(self):
        zh, en = subtitle_outputs.split_bilingual_text('中文一句\\NEnglish line')
        self.assertEqual(zh, '中文一句')
        self.assertEqual(en, 'English line')
        stats = subtitle_outputs.measure_text('中文一句\\NEnglish line')
        self.assertEqual(stats['zh'], 4)
        self.assertEqual(stats['en'], 12)
        self.assertFalse(stats['over'])
        stats = subtitle_outputs.measure_text('汉' * 30)
        self.assertTrue(stats['over'])

    def test_dialogue_records_special(self):
        lines = ass_text(
            'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,普通',
            'Dialogue: 0,0:00:02.00,0:00:03.00,Default,,0,0,0,,{\\pos(10,20)}特效',
        ).split('\n')
        records = subtitle_outputs.dialogue_records(lines)
        self.assertEqual(len(records), 2)
        self.assertFalse(records[0][2])
        self.assertTrue(records[1][2])


class PrimaryPathTests(unittest.TestCase):
    def test_respects_chinese_only_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ('1-简英双语-zh.ass', '1-简体中文-zh.ass'):
                write(os.path.join(folder, name), ass_text())
            data = {'output_dir': folder, 'is_hdr': False, 'chinese_only': True}
            self.assertEqual(
                os.path.basename(subtitle_outputs.primary_output_path(data)),
                '1-简体中文-zh.ass')
            data['chinese_only'] = False
            self.assertEqual(
                os.path.basename(subtitle_outputs.primary_output_path(data)),
                '1-简英双语-zh.ass')


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = self.temp.name
        self.primary = os.path.join(self.folder, '1-简英双语-zh.ass')
        write(self.primary, ass_text(
            'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,第一句\\NFirst',
            'Dialogue: 0,0:00:02.10,0:00:03.00,Default,,0,0,0,,第二句\\NSecond',
        ))
        write(os.path.join(self.folder, '2-简体中文-zh.ass'), ass_text(
            'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,旧一',
            'Dialogue: 0,0:00:02.10,0:00:03.00,Default,,0,0,0,,旧二',
        ))
        write(os.path.join(self.folder, '3-繁体中文-zh.ass'), ass_text(
            'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,舊一',
            'Dialogue: 0,0:00:02.10,0:00:03.00,Default,,0,0,0,,舊二',
        ))
        write(os.path.join(self.folder, '4-英文-en.ass'), ass_text(
            'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,{\\blur2}First',
            'Dialogue: 0,0:00:02.10,0:00:03.00,Default,,0,0,0,,{\\blur2}Second',
        ))
        self.layout = subtitle_outputs.output_layout(False, False)

    def tearDown(self):
        self.temp.cleanup()

    def test_sync_updates_all_languages(self):
        lines = subtitle_outputs.read_ass_lines(self.primary)
        lines = [
            line.replace('第一句', '改一').replace('第二句', '改二')
            for line in lines
        ]
        result = subtitle_outputs.sync_outputs(
            self.layout, self.primary, lines, {'taiwan': False}, {})
        self.assertEqual(result.skipped, [])
        self.assertEqual(sorted(result.synced), [
            '2-简体中文-zh.ass', '3-繁体中文-zh.ass', '4-英文-en.ass'])
        chs = read(os.path.join(self.folder, '2-简体中文-zh.ass'))
        eng = read(os.path.join(self.folder, '4-英文-en.ass'))
        cht = read(os.path.join(self.folder, '3-繁体中文-zh.ass'))
        self.assertIn('改一', chs)
        self.assertIn('改二', chs)
        self.assertIn('{\\blur2}First', eng)
        self.assertIn('{\\blur2}Second', eng)
        self.assertIn('改一', cht)

    def test_sync_skips_mismatched_lines(self):
        write(os.path.join(self.folder, '4-英文-en.ass'),
              ass_text('Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,Only one'))
        lines = subtitle_outputs.read_ass_lines(self.primary)
        result = subtitle_outputs.sync_outputs(
            self.layout, self.primary, lines, {'taiwan': False}, {})
        self.assertIn('4-英文-en.ass', result.skipped)
        self.assertNotIn('4-英文-en.ass', result.synced)

    def test_sync_reports_unchanged(self):
        lines = subtitle_outputs.read_ass_lines(self.primary)
        subtitle_outputs.sync_outputs(
            self.layout, self.primary, lines, {'taiwan': False}, {})
        result = subtitle_outputs.sync_outputs(
            self.layout, self.primary, lines, {'taiwan': False}, {})
        self.assertEqual(result.skipped, [])
        self.assertEqual(sorted(result.unchanged), [
            '2-简体中文-zh.ass', '3-繁体中文-zh.ass', '4-英文-en.ass'])

    def test_cht_failure_keeps_original_file(self):
        import subtitle_core
        original = subtitle_core.convert_cht_contents
        subtitle_core.convert_cht_contents = (
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('boom')))
        try:
            lines = subtitle_outputs.read_ass_lines(self.primary)
            result = subtitle_outputs.sync_outputs(
                self.layout, self.primary, lines, {'taiwan': True}, {})
        finally:
            subtitle_core.convert_cht_contents = original
        self.assertIn('3-繁体中文-zh.ass', result.skipped)
        cht = read(os.path.join(self.folder, '3-繁体中文-zh.ass'))
        self.assertIn('舊一', cht)

    def test_delete_removes_matching_rows(self):
        lines = subtitle_outputs.read_ass_lines(self.primary)
        result = subtitle_outputs.delete_dialogue(
            self.layout, self.primary, lines, 0)
        self.assertEqual(result.skipped, [])
        self.assertEqual(sorted(result.deleted), [
            '2-简体中文-zh.ass', '3-繁体中文-zh.ass', '4-英文-en.ass'])
        chs = read(os.path.join(self.folder, '2-简体中文-zh.ass'))
        self.assertEqual(len(dialogues(chs)), 1)
        self.assertIn('旧二', chs)


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_and_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'a.ass')
            config_store.atomic_write_text(path, '第一版')
            self.assertEqual(read(path), '第一版')
            config_store.atomic_write_text(path, '第二版')
            self.assertEqual(read(path), '第二版')
            saved = config_store.write_text_with_fallback(path, '第三版')
            self.assertEqual(saved, path)
            self.assertEqual(read(path), '第三版')


class LastDirectoryTests(unittest.TestCase):
    def test_remember_and_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            original = config_store.CONFIG_PATH
            config_store.CONFIG_PATH = os.path.join(folder, 'config.json')
            try:
                first = os.path.join(folder, 'first')
                second = os.path.join(folder, 'second')
                os.makedirs(first)
                os.makedirs(second)
                self.assertEqual(config_store.last_directory('ass'), '')
                config_store.remember_directory(
                    'ass', os.path.join(first, 'a.ass'))
                self.assertEqual(config_store.last_directory('ass'), first)
                config_store.remember_directory('ass', second)
                self.assertEqual(config_store.last_directory('ass'), second)
                self.assertEqual(
                    config_store.last_directory(
                        'missing', os.path.join(first, 'b.ass')),
                    first)
            finally:
                config_store.CONFIG_PATH = original


class BdnTests(unittest.TestCase):
    def test_segments_cover_range(self):
        intervals = [(0, 1000), (2000, 3000), (5000, 6000), (9000, 10000)]
        segments = bdn_sup._vsfilter_segments(
            intervals, 23.976, 0, 12000, 4, 30)
        self.assertEqual(segments[0][0], 0)
        total = sum(count for _start, count in segments)
        self.assertEqual(total, 12000)
        for (start, count), (next_start, _next_count) in zip(segments, segments[1:]):
            self.assertEqual(start + count, next_start)

    def test_merge_bdn_parts(self):
        with tempfile.TemporaryDirectory() as folder:
            one = os.path.join(folder, '1.xml')
            two = os.path.join(folder, '2.xml')
            template = (
                '<BDN><Description><Name Title="T" Content=""/><Language Code="chi"/>'
                '<Format VideoFormat="1080p" FrameRate="23.976" DropFrame="false"/>'
                '</Description><Events>%s</Events></BDN>'
            )
            event = (
                '<Event InTC="00:00:01:00" OutTC="00:00:02:00" '
                'Graphic="1.png">'
                '<Graphic FileName="1.png"/></Event>'
            )
            write(one, template % event)
            write(two, template % (event + event))
            out = os.path.join(folder, 'merged.xml')
            self.assertTrue(bdn_sup._merge_bdn_parts([one, two], out))
            data = read(out)
            self.assertEqual(data.count('<Event '), 3)
            self.assertIn('NumberofEvents="3"', data)


class RenderRangeTests(unittest.TestCase):
    def test_static_events_keep_only_boundaries(self):
        content = (
            HEADER
            + 'Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,静态字幕\n'
            + 'Dialogue: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,'
              '{\\t(0,500,\\fs80)}动画字幕\n'
        )
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'ranges.ass')
            with open(path, 'w', encoding='utf-8-sig') as handle:
                handle.write(content)
            ranges = bdn_sup._ass_render_ranges(path, 25.0, 200)

        def covered(frame):
            return any(start <= frame < end for start, end in ranges)

        self.assertTrue(covered(25))   # 静态事件开始
        self.assertTrue(covered(50))   # 静态事件结束
        self.assertFalse(covered(35))  # 静态事件中间不渲染
        self.assertTrue(covered(75))   # \t 动画开始
        self.assertTrue(covered(99))   # \t 动画中间帧


class SupToPesTests(unittest.TestCase):
    @staticmethod
    def _sup_segment(pts, dts, seg_type, payload):
        return (b'PG' + struct.pack('>IIBH', pts, dts, seg_type, len(payload))
                + payload)

    def test_sup_to_pes_and_mui(self):
        pcs = b'\x00' * 19
        sup = (self._sup_segment(90000, 90000, 0x16, pcs)
               + self._sup_segment(180000, 180000, 0x80, b''))
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, 'sample.sup')
            with open(source, 'wb') as handle:
                handle.write(sup)
            ok, message, outputs = bdn_sup.sup_to_pes(source)
            self.assertTrue(ok, message)
            self.assertEqual([os.path.basename(p) for p in outputs],
                             ['sample.pes', 'sample.pes.mui'])
            with open(outputs[0], 'rb') as handle:
                pes = handle.read()
            self.assertEqual(
                pes,
                struct.pack('>BH', 0x16, len(pcs)) + pcs
                + struct.pack('>BH', 0x80, 0))
            with open(outputs[1], 'rb') as handle:
                mui = handle.read()
            self.assertEqual(mui[:4], b'\x00\x00\x00\x03')
            self.assertEqual(mui[-14:], b'\xFF' + bytes(13))
            entry = mui[4:18]
            seg_type, size = struct.unpack('>BI', entry[:5])
            self.assertEqual(seg_type, 0x16)
            self.assertEqual(size, len(pcs) + 3)
            payload = entry[5:14]
            dts = ((int.from_bytes(payload[:4], 'big') << 1)
                   | (payload[4] >> 7))
            pts = ((int.from_bytes(payload[5:9], 'big')
                    + ((payload[4] & 0x7F) << 32)) >> 6)
            self.assertEqual(dts, 54090000)
            self.assertEqual(pts, 54090000)

    def test_invalid_sup_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, 'bad.sup')
            with open(source, 'wb') as handle:
                handle.write(b'NO' + bytes(20))
            ok, message, outputs = bdn_sup.sup_to_pes(source)
            self.assertFalse(ok)
            self.assertTrue(message)
            self.assertEqual(outputs, [])


class VsfilterBackendTests(unittest.TestCase):
    def test_x64_bundle_is_preferred(self):
        self.assertEqual(bdn_sup.VS_RENDER_BUNDLES[0]['arch'], 'x64')
        self.assertEqual(bdn_sup.VS_RENDER_BUNDLES[0]['mode'], 'avs2bdnxml')
        self.assertIn('bin64', bdn_sup.VS_RENDER_BUNDLES[0]['source_dir'])
        self.assertIn('avs2bdnxml.exe',
                      bdn_sup.VS_RENDER_BUNDLES[0]['required'])

    def test_bdn_artifact_names_cover_both_renderers(self):
        for name in ('bdn.xml', '00000972_0.png', 'bdn_0000.png',
                     'bdn_0_0001.png'):
            self.assertTrue(bdn_sup.is_bdn_artifact(name), name)
        for name in ('sub.ass', 'sub.avs', 'bdn.xml.bak', 'poster.png'):
            self.assertFalse(bdn_sup.is_bdn_artifact(name), name)


class BdnOutputDirTests(unittest.TestCase):
    def test_same_stem_uses_unique_dirs(self):
        import ui_bdn
        with tempfile.TemporaryDirectory() as folder:
            first = os.path.join(folder, 'A', 'sub.ass')
            second = os.path.join(folder, 'B', 'sub.ass')
            os.makedirs(os.path.dirname(first))
            os.makedirs(os.path.dirname(second))
            jobs = ui_bdn.build_output_dirs([first, second], folder)
            dirs = [out_dir.casefold() for _path, out_dir in jobs]
            self.assertEqual(len(dirs), 2)
            self.assertEqual(len(set(dirs)), 2)


class UpdateScriptTests(unittest.TestCase):
    def test_rollback_script(self):
        script = app_update.build_app_update_script()
        script.encode('ascii')
        self.assertIn('\r\n', script)
        self.assertIn('.old', script)
        self.assertIn('rollback', script)
        self.assertIn('MARKER', script)

    def test_asset_key_ignores_separators(self):
        self.assertEqual(
            app_update._asset_key('ohMyAss Noto.exe'),
            app_update._asset_key('ohMyAss.Noto.exe'))


if __name__ == '__main__':
    unittest.main()
