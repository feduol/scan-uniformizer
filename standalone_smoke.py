"""Exercise the packaged libraries, image processing and GUI without user files."""
from pathlib import Path
import tempfile


def smoke_test():
    import tkinter as tk
    from tkinter import ttk, filedialog
    import cv2
    import numpy as np
    from PIL import Image, ImageCms, ImageTk
    from scan_uniformizer import Settings, normalize, process_batch

    # Test both the bundled Tcl/Tk runtime and Pillow's Tk image bridge.
    root = tk.Tk()
    root.withdraw()
    try:
        assert root._scan_icon.width() == 256, 'Bundled app logo was not loaded'
        ttk.Label(root, text='Scan Uniformizer packaging check').pack()
        photo = ImageTk.PhotoImage(Image.new('RGB', (16, 16), 'white'), master=root)
        assert photo.width() == 16
        assert root.tk.call('info', 'patchlevel')
        root.update()
    finally:
        root.destroy()

    good = np.full((600, 800, 3), (240, 235, 220), np.uint8)
    faint = good.copy()
    for y in (140, 160, 180, 200, 220, 380, 400, 420, 440, 460):
        cv2.line(good, (30, y), (770, y), (72, 70, 66), 2)
        cv2.line(faint, (30, y), (770, y), (139, 136, 128), 2)
    for x in range(80, 750, 70):
        for y in (180, 420):
            cv2.ellipse(good, (x, y), (10, 7), -25, 0, 360, (72, 70, 66), -1)
            cv2.ellipse(faint, (x, y), (10, 7), -25, 0, 360, (139, 136, 128), -1)
    source = Image.fromarray(np.concatenate((good, faint), axis=1))
    output, _ = normalize(source, Settings())
    pixels = np.array(output)
    np.testing.assert_array_equal(pixels[:, :650], np.array(source)[:, :650])
    assert pixels[180, 1160, 0] < 100, 'Faint ink was not normalized'
    np.testing.assert_array_equal(pixels[:30], np.array(source)[:30])
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        original = temp / 'scan.tif'
        source.save(original, save_all=True, append_images=[source], dpi=(300, 300),
                    icc_profile=profile, compression='tiff_lzw')
        original_bytes = original.read_bytes()
        report = process_batch([original], temp/'output', Settings(), log=lambda _: None)
        assert report['counts']['saved'] == 2 and report['counts']['error'] == 0
        for index in (1, 2):
            with Image.open(temp/'output'/f'scan.tif_equalized_p{index:04d}.tif') as im:
                assert im.mode == 'RGB' and im.size == source.size
                assert im.info['icc_profile'] == profile
                assert im.info['dpi'] == (300, 300)
                np.testing.assert_array_equal(np.array(im), pixels)
        assert original.read_bytes() == original_bytes
        assert not list((temp/'output').glob('*.json'))
        for extension in ('png', 'jpg', 'webp', 'bmp'):
            file = temp/f'scan.{extension}'
            source.save(file)
            with Image.open(file) as im:
                im.load()
                assert im.size == source.size
