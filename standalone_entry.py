"""Standalone launcher. The original Python program is kept unchanged."""
import os
from pathlib import Path
import sys
import traceback


def run():
    # Windows GUI applications do not have attached standard streams.
    for name in ('stdout', 'stderr'):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, 'w', encoding='utf-8'))
    if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
        from standalone_smoke import smoke_test
        result = Path(sys.argv[2])
        try:
            smoke_test()
        except Exception:
            result.write_text(traceback.format_exc(), encoding='utf-8')
            return 1
        result.write_text('PASS\n', encoding='utf-8')
        return 0
    from scan_uniformizer import main
    return main()


if __name__ == '__main__':
    sys.exit(run())
