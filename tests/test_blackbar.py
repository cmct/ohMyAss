"""黑边检测：按实际画面比例推算 1080p 等效黑边。"""


import os
import random
import tempfile
import unittest

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = ImageDraw = None

import subtitle_core


def make_image(path, width, height, top=0, bottom=0):
    image = Image.new('RGB', (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle(
        [0, top, width - 1, height - bottom - 1],
        fill=(220, 220, 220))
    image.save(path)


@unittest.skipIf(Image is None, '需要 Pillow')
class BlackBarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = self.temp.name

    def tearDown(self):
        self.temp.cleanup()

    def detect(self, name, width, height, top=0, bottom=0):
        path = os.path.join(self.folder, name)
        make_image(path, width, height, top, bottom)
        return subtitle_core.detect_black_bars(path)

    def test_cropped_scope_uses_picture_aspect(self):
        result = self.detect('cropped.png', 480, 201)
        self.assertEqual(result['top'], 0)
        self.assertEqual(result['bottom'], 0)
        self.assertAlmostEqual(result['scaled_bottom'], 138.0, delta=0.5)
        self.assertEqual(result['type'], '大黑边')
        self.assertTrue(result['cropped'])

    def test_letterboxed_scope(self):
        result = self.detect('bars.png', 480, 270, top=34, bottom=35)
        self.assertGreater(result['top'], 0)
        self.assertGreater(result['bottom'], 0)
        self.assertAlmostEqual(result['scaled_bottom'], 138.0, delta=0.5)
        self.assertEqual(result['type'], '大黑边')
        self.assertFalse(result['cropped'])

    def test_full_16_9_has_no_bars(self):
        result = self.detect('full.png', 480, 270)
        self.assertEqual(result['top'], 0)
        self.assertEqual(result['bottom'], 0)
        self.assertEqual(result['scaled_bottom'], 0)
        self.assertEqual(result['type'], '无黑边')
        self.assertFalse(result['cropped'])

    def test_dark_picture_top_is_not_a_bar(self):
        path = os.path.join(self.folder, 'dark.png')
        make_image(path, 480, 201)
        image = Image.open(path)
        draw = ImageDraw.Draw(image)
        random.seed(3)
        for y in range(15):
            for x in range(0, 480, 2):
                value = random.randint(0, 40)
                draw.point((x, y), fill=(value, value, value))
        image.save(path)
        result = subtitle_core.detect_black_bars(path)
        self.assertEqual(result['top'], 0)
        self.assertEqual(result['type'], '大黑边')
        self.assertTrue(result['cropped'])

    def test_type_thresholds(self):
        self.assertEqual(subtitle_core.get_black_bar_type(0), '无黑边')
        self.assertEqual(subtitle_core.get_black_bar_type(50), '小黑边')
        self.assertEqual(subtitle_core.get_black_bar_type(51), '中黑边')
        self.assertEqual(subtitle_core.get_black_bar_type(108), '中黑边')
        self.assertEqual(subtitle_core.get_black_bar_type(109), '大黑边')
        self.assertEqual(subtitle_core.get_black_bar_type(149), '大黑边')
        self.assertEqual(subtitle_core.get_black_bar_type(150), '超大黑边')


if __name__ == '__main__':
    unittest.main()
