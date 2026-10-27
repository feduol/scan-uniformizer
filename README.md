# Scan Uniformizer

**Adaptive local contrast equalization for scanned images**

Scan Uniformizer is a local desktop and command-line tool for evening out faded ink in scanned sheet music before processing it in ScanTailor. It selectively brings faint regions closer to existing good ink, while keeping matching dark regions unchanged.

## Download

Download **Scan-Uniformizer-v1.0.0.zip** from the [latest release](https://github.com/feduol/scan-uniformizer/releases/latest), then unzip it.

The package includes the Python program and a macOS launcher. It is not a standalone compiled application; Python is required.

## Features

- Selective local correction, with an automatic reference from each page or one good reference image for an entire batch.
- RGB TIFF output that retains paper tint and ink hues.
- Batch processing of individual images, folders, subfolders, and multipage TIFFs.
- Side-by-side preview, actual-pixel inspection, changed-pixel highlighting, and a same-threshold diagnostic view.
- Original files remain untouched. Matching regions and already-dark pixels are protected.
- Existing outputs are skipped. Progress and warnings appear in the window; no JSON batch report is created.
- Processing runs locally; scans are not uploaded.

## Requirements

- Python 3.10 or newer, with Tkinter for the desktop interface.
- NumPy, Pillow, and OpenCV (listed in `requirements.txt`).
- The clickable launcher is for macOS. The Python program can also be run on Windows and Linux; those platforms have not been interactively tested for this release.

## Quick start on macOS

1. Unzip the release into a writable folder.
2. Double-click **Start Scan Uniformizer.command**.
3. If Python is missing, install Python with Tk support, then reopen the launcher. Standard macOS installers from [python.org](https://www.python.org/downloads/macos/) include Tk.
4. The launcher uses existing image libraries when available. Otherwise, it creates a `.venv` inside the program folder and installs the requirements. That first setup needs internet access; image processing does not.
5. Choose original scans or a folder. Start with **6% match tolerance** and **100% matching amount**.
6. Use **Preview first page** and **Highlight changed pixels** to check the proposed adjustments.
7. Click **Normalize batch**. The default output folder is `output_uniformized`.
8. Import the TIFFs into ScanTailor and check representative good and faint passages with your actual threshold settings.

If launching from source and executable permissions were not retained, run `bash "Start Scan Uniformizer.command"` from the project folder.

## Matching controls

**Match tolerance** controls how much difference is left alone. At 6%, regions whose measured ink contrast is within 6% of the reference, or stronger, remain unchanged. Higher tolerance protects more regions.

**Matching amount** controls how far eligible faint regions move toward the measured reference. 100% applies the full match; 50% applies half; 0% makes no changes. It is not a global darkening adjustment.

**Optional good-passage image** supplies one shared reference for the batch. Leave it blank to use stronger ink within each page. If an entire page is faint, choose a known-good passage from the same book, preferably at the same scan scale.

**DPI override** changes resolution metadata only. Leave it blank to retain source metadata, or enter the true scan DPI if known.

## Input and output

Supported inputs: TIFF (including multipage TIFF), PNG, JPEG, BMP, and WebP. The program accepts 8-bit color/grayscale and converts 16-bit grayscale to 8-bit RGB output. It does not accept PDF, RAW, 32-bit/floating-point images, or already binary scans.

Output is losslessly compressed RGB TIFF. For example:

- `page.tif` → `page.tif_equalized.tif`
- A multipage TIFF → `book.tif_equalized_p0001.tif`, `book.tif_equalized_p0002.tif`, etc.

The original extension is retained in the basename to distinguish inputs such as `page.jpg` and `page.tif`. Existing outputs are skipped, so use a new destination folder when changing settings. Previous Uniformizer output filenames are excluded from folder discovery.

Pixel dimensions are retained except when stored orientation requires rotation. RGB ICC profiles and per-page DPI metadata are retained. Transparency is composited onto white; other camera/EXIF metadata is not copied.

## Command line

Create a virtual environment and install dependencies:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, activate with `.venv\Scripts\activate` and use your installed Python command. Linux may also require the operating system's `python3-tk` package.

Open the desktop interface:

```sh
python scan_uniformizer.py --gui
```

Process a folder:

```sh
python scan_uniformizer.py "/path/to/originals" -o "/path/to/output_uniformized"
```

Use a shared reference and include subfolders:

```sh
python scan_uniformizer.py "/path/to/originals" -o "/path/to/output_uniformized" --reference "/path/to/good passage.png" --recursive
```

Additional options: `--tolerance .06`, `--strength 1`, `--region-size 0`, `--max-gain 3`, and `--dpi 300` (only when that is the known scan resolution). Run `python scan_uniformizer.py --help` for details.

## How it works and limits

The program measures local paper brightness, dark ink, and horizontal stroke contrast, then matches weaker regions to measured good ink. Its morphology is used only for temporary measurement maps; output notation is not dilated or eroded. There is no generative reconstruction or music recognition.

Automatic detection is an estimate. Pencil marks, different print styles, sparse notation, heavy scanner borders, illustrations, and show-through can affect the measurements. Use the preview and a known-good reference where needed. A uniformly faint page without an external reference is conservatively left alone.

Levels cannot restore strokes already lost to thresholding or blur. The goal is to reduce threshold adjustments in ScanTailor, not to guarantee one perfect threshold for every scan. The threshold preview is a simple diagnostic, not a reproduction of ScanTailor's processing.

## Validation

```sh
python -m unittest discover -s tests -v
```

The included tests cover unchanged good regions, matching of faint regions, preservation of dark marks within faint regions, shared-reference matching, weak staff lines, color/profile retention, multipage processing, DPI, skipping completed outputs, original-file preservation, corrupt inputs, and cancellation.

## License

[MIT License](LICENSE).
