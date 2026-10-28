"""Standalone launcher. The original Python program is kept unchanged."""
import os
from pathlib import Path
import sys
import traceback


def install_window_icon():
    """Brand the launcher's windows without changing the processing program."""
    import tkinter as tk
    from PIL import Image, ImageTk

    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            'io.github.feduol.scan-uniformizer')
    original_tk = tk.Tk

    class BrandedTk(original_tk):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            with Image.open(Path(__file__).with_name('scan-uniformizer.png')) as image:
                image.thumbnail((256, 256), Image.Resampling.LANCZOS)
                self._scan_icon = ImageTk.PhotoImage(image, master=self)
            self.iconphoto(True, self._scan_icon)

    tk.Tk = BrandedTk


def run():
    # Windows GUI applications do not have attached standard streams.
    for name in ('stdout', 'stderr'):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, 'w', encoding='utf-8'))
    if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
        from standalone_smoke import smoke_test
        result = Path(sys.argv[2])
        try:
            install_window_icon()
            smoke_test()
        except Exception:
            result.write_text(traceback.format_exc(), encoding='utf-8')
            return 1
        result.write_text('PASS\n', encoding='utf-8')
        return 0
    install_window_icon()
    from scan_uniformizer import main
    return main()


if __name__ == '__main__':
    sys.exit(run())
