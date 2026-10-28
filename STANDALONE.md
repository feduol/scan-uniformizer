# Standalone applications

The Python program, its source launcher, and `Scan-Uniformizer-v1.0.0.zip` remain available unchanged. Standalone applications are **additional downloads**. They include Python, Tcl/Tk, NumPy, Pillow, and OpenCV; end users do not install Python or run pip.

## Downloads and use

Get the archive for your computer from the [release page](https://github.com/feduol/scan-uniformizer/releases/tag/v1.0.0).

| Download suffix | Computer | Launch |
| --- | --- | --- |
| `macOS-Apple-Silicon.zip` | Mac with an M-series processor | Extract, drag `Scan Uniformizer.app` to Applications, open it |
| `macOS-Intel.zip` | Mac with an Intel processor | Extract, drag `Scan Uniformizer.app` to Applications, open it |
| `Windows-x64.zip` | 64-bit Intel/AMD Windows 10 or 11 | Extract the entire archive; open `Scan Uniformizer.exe` in its folder |
| `Linux-x64.tar.gz` | 64-bit Intel/AMD Linux, glibc 2.35 or later | Extract; open the `Scan Uniformizer` executable in its folder |

On Windows and Linux, keep the `_internal` folder next to the executable. Linux requires a graphical desktop and standard system graphics libraries; it is built/tested on Ubuntu 22.04, and other distributions are not individually tested. macOS packages declare macOS 14+ for Apple Silicon and 13+ for Intel, but older macOS versions require separate compatibility testing.

The apps are not publisher-signed or notarized. macOS may require approval in System Settings → Privacy & Security after the first open attempt. Windows may show an unknown-publisher warning. Only approve downloads you trust. No security protection needs to be disabled. A publicly trusted Mac release requires the maintainer's Apple Developer ID certificate and notarization; a Windows publisher signature requires a Windows code-signing certificate.

The same controls, color output, `_equalized` filenames, and `output_uniformized` default folder apply. The original normalization implementation is bundled without modification.

## Rebuilding

Build separately on the target OS and processor architecture. Python is required only on the build machine:

```sh
python -m venv .venv-build
# Activate .venv-build using your operating system's activation command.
python -m pip install -r requirements-build.txt
python -m unittest discover -s tests -v
python build_standalone.py
```

Linux needs Tcl/Tk and a display; in headless CI, run `xvfb-run -a python build_standalone.py`. The GitHub Actions workflow installs the required Linux system packages.

The build script creates archives and SHA-256 checksums in `dist-standalone/`. It includes license notices and a `BUILD INFO.txt` with the source hash and dependency versions. The standalone launcher is separate (`standalone_entry.py`); `scan_uniformizer.py` remains the source/CLI entry point.

Before packaging, the actual frozen executable runs `--self-test <temporary-result-file>`. This checks bundled Tcl/Tk, Pillow ImageTk, OpenCV/NumPy, faint-region correction, unchanged good regions, RGB/ICC/DPI preservation, multipage TIFF output, input preservation, and common image codecs. It runs with Python environment variables removed and a system-only PATH. The original 11 tests also run on every CI platform. These checks do not replace interactive testing on every supported OS version.

## GitHub build workflow

Under Actions, choose **Build standalone applications** → **Run workflow**. With publishing unchecked, all four builds are available as workflow artifacts. With publishing checked, successful builds append assets to the selected existing release only after all four build jobs pass. Existing asset names are never overwritten; the original Python ZIP is preserved. The default release is `v1.0.0`.

Build output and caches are not part of the Python source distribution.
