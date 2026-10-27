#!/usr/bin/env python3
"""Normalize scanned music before ScanTailor. Originals are never modified.

Python 3.10+; NumPy, Pillow, OpenCV. Run without arguments for the desktop UI.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import math
import os
from pathlib import Path
import queue
import re
import sys
import tempfile
import threading

import cv2
import numpy as np
from PIL import Image, ImageOps

EXTENSIONS = {'.tif', '.tiff', '.png', '.jpg', '.jpeg', '.bmp', '.webp'}


@dataclass(frozen=True)
class Settings:
    region_size: int = 0
    tolerance: float = 0.06
    strength: float = 1.0
    max_gain: float = 3.0
    dpi: float | None = None
    reference_image: str | None = None

    def validate(self):
        if self.region_size != 0 and self.region_size < 32:
            raise ValueError('Region size must be 0 (automatic) or at least 32 pixels.')
        if not 0.01 <= self.tolerance <= .30:
            raise ValueError('Match tolerance must be between 1% and 30%.')
        if not 0 <= self.strength <= 1:
            raise ValueError('Matching amount must be between 0% and 100%.')
        if not 1 <= self.max_gain <= 12:
            raise ValueError('Maximum gain must be between 1 and 12.')
        if self.dpi is not None and not 10 <= self.dpi <= 9600:
            raise ValueError('DPI must be between 10 and 9600.')


def paper_field(gray: np.ndarray) -> np.ndarray:
    """Estimate paper at reduced resolution; never morph the output strokes."""
    h, w = gray.shape
    scale = min(1.0, 1500.0 / max(h, w))
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    small = cv2.resize(gray, size, interpolation=cv2.INTER_AREA)
    # Closing fills dark notation in the illumination estimate only.
    kernel_size = max(31, round(w / 40 * scale)) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    paper = cv2.morphologyEx(small, cv2.MORPH_CLOSE, kernel)
    paper = cv2.GaussianBlur(paper.astype(np.float32), (0, 0), max(1, kernel_size / 4))
    return np.maximum(cv2.resize(paper, (w, h), interpolation=cv2.INTER_LINEAR), 16)


def contrast_fields(darkness: np.ndarray, region_size: int, tolerance: float):
    """Measure dark ink and horizontal stroke tones; choose real good ink as reference."""
    h, w = darkness.shape
    region = region_size or max(96, round(w / 12))
    step = max(16, region // 2)
    nx, ny = max(2, math.ceil(w / step)), max(2, math.ceil(h / step))
    cores = np.zeros((ny, nx), np.float32)
    lines = np.zeros_like(cores)
    valid = np.zeros_like(cores, dtype=bool)
    line_valid = np.zeros_like(valid)
    # Analyze long horizontal ridges to distinguish faint lines from dark
    # noteheads. This morphology is used for measurements only.
    length = max(31, round(w / 60))
    horizontal = cv2.morphologyEx(darkness, cv2.MORPH_OPEN, np.ones((1, length), np.uint8))
    peaks = cv2.dilate(horizontal, np.ones((5, 1), np.uint8))
    ridge = (horizontal >= peaks - 1e-6) & (horizontal > .06)
    for iy in range(ny):
        cy = (iy + .5) * h / ny
        y0, y1 = max(0, int(cy-region/2)), min(h, int(cy+region/2))
        for ix in range(nx):
            cx = (ix + .5) * w / nx
            x0, x1 = max(0, int(cx-region/2)), min(w, int(cx+region/2))
            tile = darkness[y0:y1, x0:x1]
            ink = tile[tile > .12]
            if ink.size >= max(16, tile.size * .002):
                cores[iy, ix] = np.percentile(ink, 80)
                valid[iy, ix] = True
                samples = horizontal[y0:y1, x0:x1][ridge[y0:y1, x0:x1]]
                if samples.size >= max(24, (x1-x0)/2):
                    lines[iy, ix] = np.percentile(samples, 40)
                    line_valid[iy, ix] = True
    if not valid.any():
        return None, None, None, {'ink_regions': 0, 'region_size': region}
    # A robust upper quartile, not the single darkest pixel or an invented
    # black target. Samples come only from regions containing actual ink.
    reference_core = float(np.percentile(cores[valid], 75))
    reference_regions = valid & line_valid & (cores >= reference_core*(1-tolerance))
    reference_regions &= cores <= np.percentile(cores[valid], 95)
    reference_line = (float(np.median(lines[reference_regions]))
                      if reference_regions.any() else reference_core*.75)
    reference_line = min(reference_line, reference_core*.95)
    reference = {'core': reference_core, 'line': reference_line}
    # Sparse regions borrow tone measurements; empty paper is still protected
    # pixel by pixel below. Missing line evidence falls back to core-only gain.
    lines[~line_valid & valid] = cores[~line_valid & valid] * reference_line/reference_core
    known = np.argwhere(valid)
    for iy, ix in np.argwhere(~valid):
        nearest = tuple(known[np.argmin(np.sum((known-[iy, ix])**2, axis=1))])
        cores[iy, ix], lines[iy, ix] = cores[nearest], lines[nearest]
    fields = []
    for grid in (cores, lines):
        grid = cv2.GaussianBlur(grid, (0, 0), .65, borderType=cv2.BORDER_REPLICATE)
        fields.append(cv2.resize(grid, (w, h), interpolation=cv2.INTER_LINEAR))
    return *fields, reference, {
        'ink_regions': int(valid.sum()), 'region_size': region,
        'automatic_reference': reference,
        'estimated_ink_contrast_10_50_90': np.percentile(cores[valid], [10, 50, 90]).tolist(),
    }


def prepare_image(image):
    if image.mode == '1':
        raise ValueError('Already black and white: use the original color or grayscale scan.')
    if image.mode in ('I', 'F'):
        raise ValueError('32-bit images are not supported. Export an 8-bit or 16-bit scan first.')
    if image.mode.startswith('I;16'):
        image = Image.fromarray((np.asarray(image).astype(np.float32)/257).clip(0, 255).astype(np.uint8))
    if 'A' in image.getbands() or 'transparency' in image.info:
        image = Image.alpha_composite(Image.new('RGBA', image.size, 'white'), image.convert('RGBA')).convert('RGB')
    return image


def read_reference(settings):
    if not settings.reference_image:
        return None
    with Image.open(settings.reference_image) as source:
        source = prepare_image(source)
        gray = np.asarray(source.convert('L'))
        dark = np.clip(1-gray.astype(np.float32)/paper_field(gray), 0, 1)
        _, _, reference, _ = contrast_fields(dark, settings.region_size, settings.tolerance)
    if reference is None:
        raise ValueError('The reference image contains no measurable ink.')
    return reference


def normalize(image: Image.Image, settings: Settings, reference=None) -> tuple[Image.Image, dict]:
    settings.validate()
    image = prepare_image(image)
    rgb = np.asarray(image.convert('RGB'))
    gray = np.asarray(image.convert('L'))
    paper = paper_field(gray)
    darkness = np.clip(1-gray.astype(np.float32)/paper, 0, 1)
    local_core, local_line, automatic, stats = contrast_fields(darkness, settings.region_size, settings.tolerance)
    reference = reference or read_reference(settings) or automatic
    result = rgb.copy()
    if local_core is not None and settings.strength > 0:
        ref_core, ref_line = reference['core'], reference['line']
        # EXACT identity for regions already matching the reference. The
        # transition begins only outside the tolerance, avoiding seam edges.
        deficit = np.maximum(0, (ref_core-local_core)/max(ref_core, .05))
        amount = np.clip((deficit-settings.tolerance)/.06, 0, 1)
        amount = amount*amount*(3-2*amount)*settings.strength
        middle = np.clip(local_line, .04, local_core*.95)
        # Match both thin strokes and notehead tones to the good passage.
        # Matching is bounded by the observed reference, not a global gamma.
        low = darkness * (ref_line/np.maximum(middle, .04))
        high = ref_line + (darkness-middle)*(ref_core-ref_line)/np.maximum(local_core-middle, .01)
        matched = np.where(darkness <= middle, low, high)
        matched = np.minimum(matched, ref_core)
        matched = np.minimum(matched, darkness*settings.max_gain)
        matched = np.maximum(darkness, matched)
        paper_mask = np.clip((darkness-.025)/.05, 0, 1)
        paper_mask = paper_mask*paper_mask*(3-2*paper_mask)
        desired_dark = darkness + amount*paper_mask*(matched-darkness)
        desired_gray = paper*(1-desired_dark)
        factor = np.minimum(1, desired_gray/np.maximum(gray, 1))
        # Never touch paper-only pixels, matched regions, or pixels already
        # as dark as the good reference, including dark ink in a faint region.
        factor[(amount == 0) | (paper_mask == 0) | (darkness >= ref_core)] = 1
        result = np.rint(rgb.astype(np.float32)*factor[:, :, None]).clip(0, 255).astype(np.uint8)
        stats['eligible_region_fraction'] = float(np.mean(amount > 0))
    stats['reference_used'] = reference
    stats['changed_pixel_fraction'] = float(np.mean(np.any(result != rgb, axis=2)))
    output = Image.fromarray(result)
    if image.mode in ('RGB', 'RGBA') and image.info.get('icc_profile'):
        output.info['icc_profile'] = image.info['icc_profile']
    return output, stats


def discover(inputs: list[str | Path], output: Path, recursive: bool) -> list[tuple[Path, Path]]:
    """Return source and relative output stems; exclude nested destination."""
    output = output.resolve()
    found: dict[Path, Path] = {}
    for entry in inputs:
        p = Path(entry).expanduser().resolve()
        if not p.exists():
            raise ValueError(f'Input does not exist: {p}')
        if p == output:
            raise ValueError('Choose an output folder different from the input folder.')
        if p.is_dir():
            candidates = sorted(p.rglob('*') if recursive else p.iterdir())
            for source in candidates:
                resolved = source.resolve()
                if source.is_file() and source.suffix.lower() in EXTENSIONS:
                    if resolved.is_relative_to(output):
                        continue
                    if re.search(r'_(?:uniform(?:_[0-9a-f]{10})?(?:_v\d+)?|equalized(?:_[0-9a-f]{10})?)(?:_p\d+)?\.tif$', source.name, re.I):
                        continue  # Do not feed prior batches back into the correction.
                    found.setdefault(resolved, source.relative_to(p))
        elif p.suffix.lower() in EXTENSIONS:
            found.setdefault(p, Path(p.name))
        else:
            raise ValueError(f'Unsupported input: {p.name}')
    if not found:
        raise ValueError('No supported images found in the selected input.')
    # Keep the source extension in the stem to distinguish page.jpg and page.tif.
    used: set[str] = set()
    result = []
    for source, relative in sorted(found.items()):
        stem = relative.parent / (relative.name + '_equalized')
        key = str(stem).casefold()
        if key in used:
            digest = hashlib.sha256(str(source).encode()).hexdigest()[:10]
            stem = stem.with_name(stem.name + '_' + digest)
        used.add(str(stem).casefold())
        result.append((source, stem))
    return result


def atomic_tiff(image: Image.Image, destination: Path, dpi: tuple | None):
    """Publish a complete TIFF without replacing an existing file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix='.uniformizer-', suffix='.tif', dir=destination.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        kwargs = {'compression': 'tiff_adobe_deflate'}
        if dpi:
            kwargs['dpi'] = dpi
        if image.info.get('icc_profile'):
            kwargs['icc_profile'] = image.info['icc_profile']
        image.save(temp, format='TIFF', **kwargs)
        # Hard-link publication is atomic and fails if the destination exists.
        os.link(temp, destination)
    finally:
        temp.unlink(missing_ok=True)


def process_batch(inputs, output, settings, recursive=False, log=print, cancel=None):
    settings.validate()
    output = Path(output).expanduser().resolve()
    files = discover(inputs, output, recursive)
    reference = read_reference(settings)
    output.mkdir(parents=True, exist_ok=True)
    protected = {p for p, _ in files}
    report = {'version': '3.0', 'started': datetime.now().isoformat(),
              'settings': asdict(settings), 'pages': [], 'cancelled': False}
    for index, (source, relative) in enumerate(files, 1):
        if cancel and cancel.is_set():
            report['cancelled'] = True
            break
        log(f'[{index}/{len(files)}] {source.name}')
        try:
            with Image.open(source) as original:
                frames = getattr(original, 'n_frames', 1)
                for frame in range(frames):
                    if cancel and cancel.is_set():
                        report['cancelled'] = True
                        break
                    suffix = f'_p{frame+1:04d}' if frames > 1 else ''
                    destination = output / relative.parent / (relative.name + suffix + '.tif')
                    record = {'source': str(source), 'page': frame+1, 'output': str(destination)}
                    report['pages'].append(record)
                    try:
                        if destination.resolve() in protected:
                            raise ValueError('Output would replace an input image.')
                        if destination.exists():
                            record['status'] = 'skipped'
                            log(f'  Already exists; skipped: {destination.name}')
                            continue
                        original.seek(frame)
                        dpi = original.info.get('dpi')
                        if settings.dpi is not None:
                            dpi = (settings.dpi, settings.dpi)
                        elif dpi:
                            dpi = tuple(float(x) for x in dpi)
                            if len(dpi) != 2 or not all(math.isfinite(x) and x > 0 for x in dpi):
                                dpi = None
                        if dpi is None or min(dpi) < 50:
                            record['resolution_warning'] = 'Missing or implausible scan DPI'
                            log('  Note: source DPI is missing or implausible')
                        # Pillow loads TIFF orientation itself; ImageOps handles other formats.
                        if original.format == 'TIFF':
                            page = original.copy()
                        else:
                            orientation = original.getexif().get(274, 1)
                            page = ImageOps.exif_transpose(original)
                            if dpi and orientation in (5, 6, 7, 8):
                                dpi = dpi[::-1]
                        corrected, stats = normalize(page, settings, reference)
                        if cancel and cancel.is_set():
                            record['status'] = 'cancelled'
                            report['cancelled'] = True
                            break
                        atomic_tiff(corrected, destination, dpi)
                        record.update(status='saved', size=list(corrected.size), dpi=dpi, **stats)
                        log(f'  Saved: {destination.name}')
                        log(f"  Changed {stats['changed_pixel_fraction']:.1%} of pixels; matched dark regions kept.")
                    except FileExistsError:
                        record['status'] = 'skipped'
                        log(f'  Already exists; skipped: {destination.name}')
                    except Exception as exc:
                        record.update(status='error', error=str(exc))
                        log(f'  Error on page {frame+1}: {exc}')
        except Exception as exc:
            report['pages'].append({'source': str(source), 'status': 'error', 'error': str(exc)})
            log(f'  Error: {exc}')
    report['finished'] = datetime.now().isoformat()
    report['counts'] = {s: sum(p['status'] == s for p in report['pages'])
                        for s in ('saved', 'skipped', 'error', 'cancelled')}
    log(f"Finished: {report['counts']['saved']} saved, {report['counts']['skipped']} skipped, {report['counts']['error']} errors.")
    return report


def desktop():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    root = tk.Tk()
    root.title('Scan Uniformizer')
    root.geometry('880x820')
    root.minsize(840, 760)
    root.configure(padx=22, pady=18)
    inputs: list[str] = []
    events = queue.Queue()
    cancellation = threading.Event()
    running = False
    selected = tk.StringVar(value='Choose original scans or a folder to begin.')
    output = tk.StringVar()
    recursive = tk.BooleanVar(value=False)
    tolerance = tk.StringVar(value='6')
    strength = tk.StringVar(value='100')
    reference_file = tk.StringVar()
    dpi = tk.StringVar()
    status = tk.StringVar(value='Ready. Originals are kept unchanged.')

    ttk.Label(root, text='Scan Uniformizer', font=('', 23, 'bold')).pack(anchor='w')
    ttk.Label(root, text='Adaptive local contrast equalization for scanned images', wraplength=740).pack(anchor='w', pady=(5, 18))
    controls = ttk.Frame(root)
    controls.pack(fill='x')
    def choose_files():
        chosen = filedialog.askopenfilenames(title='Choose original scans', filetypes=[('Scanned images', '*.tif *.tiff *.png *.jpg *.jpeg *.bmp *.webp'), ('All files', '*')])
        if chosen:
            inputs[:] = chosen
            selected.set(f'{len(inputs)} image file(s) selected')
            if not output.get():
                output.set(str(Path(inputs[0]).parent / 'output_uniformized'))
    def choose_folder():
        chosen = filedialog.askdirectory(title='Choose a folder of original scans')
        if chosen:
            inputs[:] = [chosen]
            selected.set(chosen)
            output.set(str(Path(chosen) / 'output_uniformized'))
    buttons = []
    for label, action in [('Choose images…', choose_files), ('Choose folder…', choose_folder)]:
        b = ttk.Button(controls, text=label, command=action)
        b.pack(side='left', padx=(0, 8)); buttons.append(b)
    ttk.Label(root, textvariable=selected, wraplength=720).pack(anchor='w', pady=8)
    subfolders = ttk.Checkbutton(root, text='Include subfolders', variable=recursive)
    subfolders.pack(anchor='w')
    ttk.Label(root, text='Save normalized copies to', font=('', 12, 'bold')).pack(anchor='w', pady=(18, 5))
    destination_row = ttk.Frame(root); destination_row.pack(fill='x')
    destination_entry = ttk.Entry(destination_row, textvariable=output)
    destination_entry.pack(side='left', fill='x', expand=True)
    def choose_output():
        chosen = filedialog.askdirectory(title='Choose output folder')
        if chosen:
            output.set(chosen)
    browse = ttk.Button(destination_row, text='Browse…', command=choose_output)
    browse.pack(side='left', padx=(8, 0)); buttons.append(browse)
    options = ttk.LabelFrame(root, text='Settings used for the whole batch', padding=12)
    options.pack(fill='x', pady=16)
    ttk.Label(options, text='Always color TIFF · original paper tint and ink hues retained.').pack(anchor='w', pady=(3, 10))
    ttk.Label(options, text='Automatic reference: the stronger ink already present on each page.').pack(anchor='w', pady=(0, 8))
    ref_row = ttk.Frame(options); ref_row.pack(fill='x', pady=(0, 8))
    ttk.Label(ref_row, text='Optional good-passage image:').pack(side='left')
    ref_entry = ttk.Entry(ref_row, textvariable=reference_file)
    ref_entry.pack(side='left', padx=8, fill='x', expand=True)
    def choose_reference():
        path = filedialog.askopenfilename(title='Choose a GOOD passage or page to match across the batch')
        if path:
            reference_file.set(path)
    ref_button = ttk.Button(ref_row, text='Choose…', command=choose_reference)
    ref_button.pack(side='left')
    def clear_reference():
        reference_file.set('')
    clear_ref = ttk.Button(ref_row, text='Clear', command=clear_reference)
    clear_ref.pack(side='left', padx=(6, 0))
    level_row = ttk.Frame(options); level_row.pack(fill='x', pady=(0, 8))
    ttk.Label(level_row, text='Match tolerance (%):').pack(side='left')
    tolerance_entry = ttk.Spinbox(level_row, from_=1, to=30, increment=1, textvariable=tolerance, width=5)
    tolerance_entry.pack(side='left', padx=8)
    ttk.Label(level_row, text='6 recommended · higher leaves more passages unchanged').pack(side='left')
    amount_row = ttk.Frame(options); amount_row.pack(fill='x', pady=(0, 8))
    ttk.Label(amount_row, text='Matching amount (%):').pack(side='left')
    strength_entry = ttk.Spinbox(amount_row, from_=0, to=100, increment=10, textvariable=strength, width=5)
    strength_entry.pack(side='left', padx=8)
    ttk.Label(amount_row, text='100 = match reference · never a global darkening control').pack(side='left')
    dpi_row = ttk.Frame(options); dpi_row.pack(fill='x', pady=(8, 0))
    ttk.Label(dpi_row, text='DPI override:').pack(side='left')
    dpi_entry = ttk.Entry(dpi_row, textvariable=dpi, width=7)
    dpi_entry.pack(side='left', padx=8)
    ttk.Label(dpi_row, text='Leave blank to keep source metadata.').pack(side='left')
    actions = ttk.Frame(root); actions.pack(fill='x')
    progress = ttk.Progressbar(root, mode='indeterminate')
    progress.pack(fill='x', pady=(12, 8))
    ttk.Label(root, textvariable=status, wraplength=720).pack(anchor='w')
    log_box = tk.Text(root, height=8, wrap='word', state='disabled', font=('Menlo', 11))
    log_box.pack(fill='both', expand=True, pady=(8, 0))
    editable = buttons + [subfolders, destination_entry, ref_entry, ref_button, clear_ref, tolerance_entry, strength_entry, dpi_entry]
    def show_preview(before, after, source_name):
        from PIL import ImageTk
        window = tk.Toplevel(root)
        window.title('Preview — ' + source_name)
        window.geometry('1120x760')
        toolbar = ttk.Frame(window, padding=10); toolbar.pack(fill='x')
        ttk.Label(toolbar, text='Click either page to inspect that passage at actual pixels.').pack(side='left')
        binary = tk.BooleanVar(value=False)
        highlight = tk.BooleanVar(value=False)
        threshold = tk.StringVar(value='160')
        changed = np.any(np.asarray(before.convert('RGB')) != np.asarray(after), axis=2)
        row = ttk.Frame(window, padding=(10, 0)); row.pack(fill='x')
        ttk.Label(row, text='Original', anchor='center').pack(side='left', fill='x', expand=True)
        ttk.Label(row, text='Normalized color output', anchor='center').pack(side='left', fill='x', expand=True)
        panels = ttk.Frame(window); panels.pack(fill='both', expand=True)
        canvases = [tk.Canvas(panels, width=540, height=600, background='#dededb', highlightthickness=0) for _ in range(2)]
        for canvas in canvases:
            canvas.pack(side='left', expand=True, fill='both', padx=5, pady=5)
        ttk.Label(window, text=f'{np.mean(changed):.1%} of pixels changed. Highlights and threshold view are preview-only; saved TIFFs remain in their original colors.', wraplength=1080).pack(padx=12, pady=10)
        view = {'center': None, 'maps': []}
        photos = []
        def draw():
            photos.clear(); view['maps'].clear()
            try:
                cutoff = max(0, min(255, int(threshold.get())))
            except ValueError:
                cutoff = 160
            for canvas, original in zip(canvases, (before, after)):
                cw, ch = max(100, canvas.winfo_width()), max(100, canvas.winfo_height())
                if view['center'] is None:
                    crop_box = (0, 0, original.width, original.height)
                    scale = min(cw/original.width, ch/original.height, 1)
                else:
                    cx, cy = view['center']
                    width, height = min(cw, original.width), min(ch, original.height)
                    x = max(0, min(original.width-width, int(cx-width/2)))
                    y = max(0, min(original.height-height, int(cy-height/2)))
                    crop_box = (x, y, x+width, y+height); scale = 1
                im = original.crop(crop_box)
                if binary.get():
                    im = im.convert('L').point(lambda x: 255 if x >= cutoff else 0)
                elif original is after and highlight.get():
                    pixels = np.array(im.convert('RGB'))
                    x0, y0, x1, y1 = crop_box
                    selected_pixels = changed[y0:y1, x0:x1]
                    pixels[selected_pixels] = (255, 45, 100)
                    im = Image.fromarray(pixels)
                im = im.resize((max(1, round(im.width*scale)), max(1, round(im.height*scale))), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(im); photos.append(photo)
                dx, dy = (cw-im.width)//2, (ch-im.height)//2
                canvas.delete('all'); canvas.create_image(dx, dy, anchor='nw', image=photo)
                view['maps'].append((dx, dy, scale, crop_box))
        def inspect(event, index):
            if len(view['maps']) != 2:
                return
            dx, dy, scale, box = view['maps'][index]
            x, y = box[0] + (event.x-dx)/scale, box[1] + (event.y-dy)/scale
            if 0 <= x < before.width and 0 <= y < before.height:
                view['center'] = (x, y); draw()
        def whole():
            view['center'] = None; draw()
        ttk.Button(toolbar, text='Whole page', command=whole).pack(side='right')
        options_row = ttk.Frame(window, padding=(10, 4)); options_row.pack(before=row, fill='x')
        ttk.Checkbutton(options_row, text='Test the same threshold on both images:', variable=binary, command=draw).pack(side='left')
        entry = ttk.Spinbox(options_row, from_=0, to=255, textvariable=threshold, width=5, command=draw)
        entry.pack(side='left', padx=8); entry.bind('<Return>', lambda e: draw())
        ttk.Checkbutton(options_row, text='Highlight changed pixels (color view)', variable=highlight, command=draw).pack(side='left', padx=12)
        for i, canvas in enumerate(canvases):
            canvas.bind('<Button-1>', lambda e, n=i: inspect(e, n))
            canvas.bind('<Configure>', lambda e: draw())
        window.after_idle(draw)
    def append_log(text):
        log_box.configure(state='normal'); log_box.insert('end', text+'\n')
        log_box.see('end'); log_box.configure(state='disabled')
    def start(preview=False):
        nonlocal running
        try:
            if not inputs or not output.get().strip():
                raise ValueError('Choose input scans and an output folder first.')
            settings = Settings(tolerance=float(tolerance.get().replace(',', '.'))/100,
                                strength=float(strength.get().replace(',', '.'))/100,
                                reference_image=reference_file.get().strip() or None,
                                dpi=float(dpi.get()) if dpi.get().strip() else None)
            settings.validate()
            discover(inputs, Path(output.get()), recursive.get())
        except Exception as exc:
            messagebox.showerror('Check settings', str(exc)); return
        chosen_inputs, chosen_output, recurse = list(inputs), output.get(), recursive.get()
        cancellation.clear(); running = True
        for widget in editable:
            widget.configure(state='disabled')
        start_button.configure(state='disabled'); preview_button.configure(state='disabled'); stop_button.configure(state='normal')
        progress.start(); status.set('Preparing preview…' if preview else 'Processing…')
        def worker():
            try:
                if preview:
                    source = discover(chosen_inputs, Path(chosen_output), recurse)[0][0]
                    with Image.open(source) as im:
                        before = im.copy() if im.format == 'TIFF' else ImageOps.exif_transpose(im)
                    after, _ = normalize(before, settings)
                    events.put(('preview', (before.convert('RGB'), after, source.name)))
                    return
                result = process_batch(chosen_inputs, chosen_output, settings, recurse,
                                       lambda text: events.put(('log', text)), cancellation)
                events.put(('done', result))
            except Exception as exc:
                events.put(('error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()
    def stop():
        cancellation.set(); status.set('Stopping after the current calculation…')
        stop_button.configure(state='disabled')
    start_button = ttk.Button(actions, text='Normalize batch', command=start)
    start_button.pack(side='left')
    preview_button = ttk.Button(actions, text='Preview first page…', command=lambda: start(True))
    preview_button.pack(side='left', padx=8)
    stop_button = ttk.Button(actions, text='Stop', command=stop, state='disabled')
    stop_button.pack(side='left', padx=8)
    def poll():
        nonlocal running
        while not events.empty():
            kind, payload = events.get_nowait()
            if kind == 'log':
                append_log(payload)
            else:
                running = False; progress.stop()
                for widget in editable:
                    widget.configure(state='normal')
                start_button.configure(state='normal'); preview_button.configure(state='normal'); stop_button.configure(state='disabled')
                if kind == 'error':
                    status.set('Stopped with an error.'); append_log(payload)
                    messagebox.showerror('Batch error', payload)
                elif kind == 'preview':
                    status.set('Preview ready. No output files saved.')
                    if not cancellation.is_set():
                        show_preview(*payload)
                    else:
                        status.set('Preview cancelled.')
                else:
                    counts = payload['counts']
                    status.set(('Stopped. ' if payload['cancelled'] else 'Finished. ') +
                               f"{counts['saved']} saved · {counts['skipped']} skipped · {counts['error']} errors")
        root.after(120, poll)
    def close():
        if running:
            stop()
            status.set('Stopping safely. Close the window after processing stops.')
        else:
            root.destroy()
    root.protocol('WM_DELETE_WINDOW', close)
    root.after(120, poll)
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('inputs', nargs='*', help='Original image files or folders')
    parser.add_argument('-o', '--output', type=Path, help='Destination folder (required for batch command)')
    parser.add_argument('--recursive', action='store_true', help='Include subfolders')
    parser.add_argument('--reference', help='Good passage/page image to use for the entire batch; otherwise each page supplies its own reference')
    parser.add_argument('--tolerance', type=float, default=.06, help='Relative ink difference left unchanged; default .06')
    parser.add_argument('--strength', type=float, default=1, help='Matching amount: 0–1; default 1; never darkens beyond the reference')
    parser.add_argument('--region-size', type=int, default=0, help='Local measurement window in pixels; default automatic')
    parser.add_argument('--max-gain', type=float, default=3, help='Cap on weak-detail gain; default 3')
    parser.add_argument('--dpi', type=float, help='Override resolution metadata; no resampling')
    parser.add_argument('--gui', action='store_true', help='Open desktop interface')
    args = parser.parse_args()
    if args.gui or not args.inputs:
        desktop(); return 0
    if args.output is None:
        parser.error('--output is required with input files or folders')
    settings = Settings(region_size=args.region_size, max_gain=args.max_gain,
                        tolerance=args.tolerance, strength=args.strength,
                        reference_image=args.reference, dpi=args.dpi)
    try:
        report = process_batch(args.inputs, args.output, settings, args.recursive)
        return 1 if report['counts']['error'] else 0
    except (ValueError, OSError) as exc:
        parser.exit(2, f'Error: {exc}\n')


if __name__ == '__main__':
    sys.exit(main())
