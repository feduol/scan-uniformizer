import hashlib
from pathlib import Path
import sys
import tempfile
import threading
import unittest

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scan_uniformizer import Settings, normalize, process_batch, discover


def score(contrast=.7, paper=235):
    mask = np.zeros((600, 800), np.uint8)
    for y in (140, 160, 180, 200, 220, 380, 400, 420, 440, 460):
        cv2.line(mask, (30, y), (770, y), 255, 2)
    for x in range(80, 750, 70):
        for y in (180, 420):
            cv2.ellipse(mask, (x, y), (10, 7), -25, 0, 360, 255, -1)
            cv2.line(mask, (x+9, y), (x+9, y-55), 255, 2)
    mask = cv2.GaussianBlur(mask, (3, 3), .5).astype(np.float32) / 255
    array = np.rint(paper * (1 - contrast * mask)).astype(np.uint8)
    return Image.fromarray(array), mask


class NormalizationTests(unittest.TestCase):
    def test_shared_reference_for_faded_and_dark_pages(self):
        good, mask = score(.72, 240)
        faint, _ = score(.32, 210)
        with tempfile.TemporaryDirectory() as temp:
            reference = Path(temp)/'reference.png'; good.save(reference)
            settings = Settings(reference_image=str(reference))
            a, _ = normalize(good, settings)
            b, _ = normalize(faint, settings)
        a, b = np.array(a.convert('L')), np.array(b.convert('L'))
        np.testing.assert_array_equal(a, np.array(good))
        core = mask > .95
        self.assertLess(abs(float(np.median(a[core]))/240-float(np.median(b[core]))/210), .015)
        ba, bb = a < 145, b < 145
        self.assertLess(np.count_nonzero(ba ^ bb)/np.count_nonzero(ba | bb), .04)
        self.assertGreater(np.mean(bb[core]), .99)
        self.assertLess(np.mean(bb[mask == 0]), .001)

    def test_good_passages_exactly_unchanged_faint_passages_matched(self):
        good, mask = score(.70, 235)
        faint, _ = score(.42, 235)
        source = Image.fromarray(np.concatenate((np.array(good), np.array(faint)), axis=1)).convert('RGB')
        output, stats = normalize(source, Settings())
        a, b = np.array(source), np.array(output)
        # Avoid only the small blended boundary between the two exposures.
        np.testing.assert_array_equal(a[:, :650], b[:, :650])
        core = mask[:, 150:] > .95
        target = np.median(a[:, 150:800, 0][core])
        matched = np.median(b[:, 950:, 0][core])
        self.assertLess(abs(float(target)-float(matched)), 5)
        self.assertGreater(np.mean(a[:, 950:, 0][core])-matched, 40)
        self.assertGreater(stats['changed_pixel_fraction'], 0)
        np.testing.assert_array_equal(b[:30], a[:30])

    def test_uniform_page_and_zero_matching_are_exact_identity(self):
        source, _ = score(.7)
        out, stats = normalize(source, Settings())
        np.testing.assert_array_equal(np.array(out.convert('L')), np.array(source))
        self.assertEqual(stats['changed_pixel_fraction'], 0)
        source, _ = score(.3)
        out, _ = normalize(source, Settings(strength=0))
        np.testing.assert_array_equal(np.array(out.convert('L')), np.array(source))

    def test_illumination_and_ink_variation_within_page(self):
        _, mask = score()
        x = np.linspace(0, 1, mask.shape[1])[None, :]
        paper = 190 + 55*x
        contrast = .32 + .4*x
        source = Image.fromarray(np.rint(paper*(1-contrast*mask)).astype(np.uint8))
        output, _ = normalize(source, Settings())
        a = np.array(output.convert('L'))
        core = mask > .95
        left = np.median(a[:, :250][core[:, :250]])
        right = np.median(a[:, -250:][core[:, -250:]])
        self.assertLess(abs(float(left)-float(right)), 12)
        self.assertLess(np.mean(abs(a[mask == 0].astype(float)-np.array(source)[mask == 0])), 1)

    def test_blank_paper_not_amplified(self):
        rng = np.random.default_rng(19)
        a = np.clip(231 + rng.normal(0, .7, (350, 500)), 0, 255).astype(np.uint8)
        out, stats = normalize(Image.fromarray(a), Settings())
        self.assertEqual(stats['ink_regions'], 0)
        np.testing.assert_array_equal(np.array(out.convert('L')), a)

    def test_color_alpha_and_16_bit(self):
        a = np.full((400, 600, 3), (240, 235, 220), np.uint8)
        a[80:100, 50:550] = [180, 35, 30]
        a[260:280, 50:550] = [30, 40, 170]
        out, _ = normalize(Image.fromarray(a), Settings())
        b = np.array(out)
        self.assertEqual(out.mode, 'RGB')
        self.assertAlmostEqual(float(b[90, 300, 1])/float(b[90, 300, 0]), 35/180, delta=.025)
        self.assertAlmostEqual(float(b[270, 300, 0])/float(b[270, 300, 2]), 30/170, delta=.025)
        np.testing.assert_array_equal(b[20, 300], a[20, 300])
        transparent = Image.new('RGBA', (100, 100), (0, 0, 0, 0))
        out, _ = normalize(transparent, Settings())
        self.assertEqual(np.min(np.array(out)), 255)
        source, _ = score()
        out, _ = normalize(Image.fromarray(np.array(source).astype(np.uint16)*257), Settings())
        self.assertEqual(out.mode, 'RGB')
        self.assertEqual(out.size, source.size)

    def test_batch_multipage_metadata_resume_and_corrupt_input(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root/'sources'; source.mkdir()
            output = source/'normalized'
            a, _ = score(); b, _ = score(.4)
            file = source/'score.tif'
            a.save(file, save_all=True, append_images=[b], dpi=(300, 300), compression='tiff_lzw')
            (source/'broken.jpg').write_bytes(b'not an image')
            before = hashlib.sha256(file.read_bytes()).digest()
            expected_dpi = []
            with Image.open(file) as original:
                for i in range(original.n_frames):
                    original.seek(i)
                    expected_dpi.append(original.info.get('dpi'))
            report = process_batch([source], output, Settings(), True, lambda _: None)
            self.assertEqual(report['counts']['saved'], 2)
            self.assertEqual(report['counts']['error'], 1)
            for record in report['pages']:
                if record['status'] != 'saved':
                    continue
                with Image.open(record['output']) as out:
                    self.assertEqual(out.size, a.size)
                    self.assertEqual(out.mode, 'RGB')
                    self.assertEqual(out.info['dpi'], expected_dpi[record['page']-1])
            report = process_batch([source], output, Settings(), True, lambda _: None)
            self.assertEqual(report['counts']['saved'], 0)
            self.assertEqual(report['counts']['skipped'], 2)
            self.assertEqual(hashlib.sha256(file.read_bytes()).digest(), before)

    def test_duplicate_filenames_and_cancel(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sources = []
            a, _ = score()
            for folder in ('a', 'b'):
                (root/folder).mkdir()
                path = root/folder/'page.png'; a.save(path); sources.append(path)
            output = root/'out'
            plan = discover(sources, output, False)
            self.assertNotEqual(plan[0][1], plan[1][1])
            cancelled = threading.Event(); cancelled.set()
            report = process_batch(sources, output, Settings(), log=lambda _: None, cancel=cancelled)
            self.assertTrue(report['cancelled'])
            self.assertEqual(len(list(output.glob('*.tif'))), 0)

    def test_binary_input_rejected(self):
        with self.assertRaises(ValueError):
            normalize(Image.new('1', (100, 100)), Settings())

    def test_weak_lines_match_good_lines_without_darkening_good_region(self):
        good = np.full((600, 800, 3), (240, 235, 220), np.uint8)
        faint = good.copy()
        for y in range(50, 400, 45):
            for x in range(40, 780, 40):
                cv2.ellipse(good, (x,y), (10,7), 0, 0, 360, (72,70,66), -1)
                cv2.ellipse(faint, (x,y), (10,7), 0, 0, 360, (120,117,110), -1)
        for y in (450, 470, 490, 510, 530):
            good[y:y+2, 40:760] = (108,106,99)
            faint[y:y+2, 40:760] = (192,188,176)
        # Existing dark marks within the faint area must remain unchanged too.
        faint[320:328, 400:408] = (35,34,32)
        source = np.concatenate((good, faint), axis=1)
        output, _ = normalize(Image.fromarray(source), Settings(region_size=650))
        b = np.array(output)
        np.testing.assert_array_equal(b[:, :400], source[:, :400])
        np.testing.assert_array_equal(b[320:328,1200:1208], source[320:328,1200:1208])
        before_error = abs(int(source[490,1200,0])-int(source[490,400,0]))
        after_error = abs(int(b[490,1200,0])-int(source[490,400,0]))
        self.assertLess(after_error, before_error/3)

    def test_profile_and_rgb_tiff_roundtrip(self):
        from PIL import ImageCms
        source, _ = score(); source = source.convert('RGB')
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
        source.info['icc_profile'] = profile
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp)/'color.tif'
            source.save(file, icc_profile=profile)
            report = process_batch([file], Path(temp)/'out', Settings(), log=lambda _: None)
            with Image.open(report['pages'][0]['output']) as out:
                self.assertEqual(out.mode, 'RGB')
                self.assertEqual(out.info['icc_profile'], profile)


if __name__ == '__main__':
    unittest.main()
