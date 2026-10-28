#!/usr/bin/env python3
"""Build and verify a standalone app on the current operating system."""
import argparse
import hashlib
from importlib.metadata import distribution
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
VERSION = '1.0.1'
NAME = 'Scan Uniformizer'


def run(*args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def collect_licenses(destination):
    destination.mkdir(parents=True)
    for name in ('numpy', 'Pillow', 'opencv-python-headless', 'pyinstaller'):
        dist = distribution(name)
        for file in dist.files or []:
            if any(token in Path(file).name.lower() for token in ('license', 'copying')) and '.dist-info/' in str(file):
                source = Path(dist.locate_file(file))
                if source.is_file():
                    target = destination/name/Path(file).name
                    target.parent.mkdir(exist_ok=True)
                    shutil.copy2(source, target)
    python_license = Path(sysconfig.get_path('stdlib'))/'LICENSE.txt'
    if not python_license.exists():
        python_license = Path(sys.base_prefix)/'LICENSE.txt'
    if not python_license.exists():
        raise RuntimeError('Python license not found')
    shutil.copy2(python_license, destination/'Python-LICENSE.txt')
    # Tcl/Tk packages differ by OS; include the installed license notices.
    import tkinter
    tcl = Path(tkinter.Tcl().eval('info library'))
    for package in ('tcl', 'tk'):
        candidates = [tcl/'license.terms', tcl.parent/f'{package}8.6'/'license.terms'] if package == 'tcl' else [tcl.parent/'tk8.6'/'license.terms']
        candidates += [Path(sys.base_prefix)/'tcl'/f'{package}8.6'/'license.terms',
                       Path('/usr/share/doc')/f'{package}8.6'/'copyright']
        framework = 'Tcl' if package == 'tcl' else 'Tk'
        candidates += list((Path(sys.base_prefix)/'Frameworks'/f'{framework}.framework').glob('Versions/*/Resources/license.terms'))
        found = next((p for p in candidates if p.is_file()), None)
        if not found and tkinter.TclVersion == 8.6 and tkinter.TkVersion == 8.6:
            found = ROOT/'TCL-TK-LICENSES.txt'
        if not found or not found.is_file():
            raise RuntimeError(f'{framework} license not found; add this platform license path')
        shutil.copy2(found, destination/f'{framework}-LICENSE.txt')


def test_bundle(executable):
    with tempfile.TemporaryDirectory(prefix='scan-uniformizer-check-') as directory:
        result = Path(directory)/'result.txt'
        env = os.environ.copy()
        for key in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'TCL_LIBRARY', 'TK_LIBRARY'):
            env.pop(key, None)
        env['PYTHONNOUSERSITE'] = '1'
        env['PATH'] = str(Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32') if sys.platform == 'win32' else '/usr/bin:/bin'
        process = subprocess.run([str(executable), '--self-test', str(result)], env=env, cwd=directory, timeout=120)
        if process.returncode or not result.exists() or result.read_text(encoding='utf-8').strip() != 'PASS':
            raise RuntimeError(result.read_text(encoding='utf-8') if result.exists() else 'Frozen self-test did not complete')
    print('Packaged application passed GUI, color normalization, multipage TIFF, metadata and codec checks.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'dist-standalone')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    machine = platform.machine().lower()
    if sys.platform == 'darwin':
        label = 'macOS-Apple-Silicon' if machine in ('arm64', 'aarch64') else 'macOS-Intel'
    elif sys.platform == 'win32':
        label = 'Windows-x64' if machine in ('amd64', 'x86_64') else f'Windows-{machine}'
    elif sys.platform.startswith('linux'):
        label = 'Linux-x64' if machine in ('amd64', 'x86_64') else f'Linux-{machine}'
    else:
        raise RuntimeError('Unsupported build OS')
    build = ROOT/'build'/'standalone'/label
    # PyInstaller owns this build directory; completed release downloads live elsewhere.
    build.mkdir(parents=True, exist_ok=True)
    license_dir = build/'licenses'
    if license_dir.exists():
        shutil.rmtree(license_dir)
    collect_licenses(license_dir)
    env = os.environ.copy()
    env['PYINSTALLER_CONFIG_DIR'] = str(build/'cache')
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed',
               '--name', NAME, '--distpath', str(build/'dist'), '--workpath', str(build/'work'),
               '--specpath', str(build), '--add-data', f'{ROOT / "LICENSE"}:.',
               '--add-data', f'{ROOT / "scan-uniformizer.png"}:.',
               '--add-data', f'{license_dir}:licenses', '--hidden-import', 'PIL.ImageTk',
               '--hidden-import', 'PIL.ImageCms', '--hidden-import', 'PIL._tkinter_finder',
               '--exclude-module', 'pytest',
               '--exclude-module', 'matplotlib', '--exclude-module', 'IPython']
    if sys.platform in ('darwin', 'win32'):
        command += ['--icon', str(ROOT/'scan-uniformizer.png')]
    if sys.platform == 'darwin':
        command += ['--osx-bundle-identifier', 'io.github.feduol.scan-uniformizer', '--target-arch', machine]
    command += [str(ROOT/'standalone_entry.py')]
    run(*command, env=env, cwd=ROOT)
    bundle = build/'dist'/(NAME+'.app' if sys.platform == 'darwin' else NAME)
    executable = bundle/'Contents'/'MacOS'/NAME if sys.platform == 'darwin' else bundle/(NAME+'.exe' if sys.platform == 'win32' else NAME)
    if sys.platform == 'darwin':
        import plistlib
        info_file = bundle/'Contents'/'Info.plist'
        info = plistlib.loads(info_file.read_bytes())
        icon_file = bundle/'Contents'/'Resources'/info['CFBundleIconFile']
        if icon_file.stem == 'icon-windowed' or not icon_file.is_file():
            raise RuntimeError('Custom macOS app icon is missing')
        info.update(CFBundleShortVersionString=VERSION, CFBundleVersion=VERSION, NSHighResolutionCapable=True,
                    LSMinimumSystemVersion='14.0' if machine == 'arm64' else '13.0')
        info_file.write_bytes(plistlib.dumps(info))
        run('codesign', '--force', '--deep', '--sign', '-', bundle)
        run('codesign', '--verify', '--deep', '--strict', bundle)
    test_bundle(executable)
    package = build/f'Scan-Uniformizer-v{VERSION}-{label}'
    if package.exists():
        shutil.rmtree(package)
    package.mkdir()
    shutil.copytree(bundle, package/bundle.name, symlinks=True)
    shutil.copy2(ROOT/'LICENSE', package/'LICENSE')
    shutil.copy2(ROOT/'scan-uniformizer.png', package/'scan-uniformizer.png')
    shutil.copytree(license_dir, package/'Third-party licenses')
    instructions = 'Scan Uniformizer — standalone application\n\nNo Python installation is needed. Processing stays on your computer.\n\n'
    if sys.platform == 'darwin':
        instructions += 'Drag Scan Uniformizer.app to Applications, then open it.\nThis build is ad-hoc signed, not Apple Developer ID signed or notarized.\nmacOS may block an internet download until you approve it in System Settings > Privacy & Security.\nOnly approve an app you trust; do not disable Gatekeeper.\n'
    elif sys.platform == 'win32':
        instructions += 'Extract the entire ZIP into a folder, then open Scan Uniformizer.exe inside Scan Uniformizer.\nKeep the _internal folder beside the executable. No installation or administrator account is needed.\nThis build is not publisher-signed; Windows may show an unknown-publisher warning.\n'
    else:
        instructions += 'Extract the archive, then open the Scan Uniformizer executable inside its folder.\nKeep the _internal folder beside the executable. A graphical desktop is required.\nBuilt on Ubuntu 22.04 (glibc 2.35); other distributions are not individually tested.\nIf the file manager does not launch executables, run ./"Scan Uniformizer" from that folder.\n'
    instructions += '\nChoose images or a folder, preview the result, then select Normalize batch.\nOutput: RGB TIFF files in output_uniformized with _equalized filenames.\n\nThe Python source and original Python ZIP remain available at:\nhttps://github.com/feduol/scan-uniformizer\n'
    (package/'START HERE.txt').write_text(instructions, encoding='utf-8')
    details = f'Version: {VERSION}\nPlatform: {label}\nBuild OS: {platform.platform()}\nPython: {platform.python_version()}\n'
    details += f'Source SHA256: {hashlib.sha256((ROOT/"scan_uniformizer.py").read_bytes()).hexdigest()}\n'
    details += '\n'.join(f'{p}: {distribution(p).version}' for p in ('numpy', 'Pillow', 'opencv-python-headless', 'pyinstaller'))+'\n'
    (package/'BUILD INFO.txt').write_text(details, encoding='utf-8')
    if sys.platform == 'darwin':
        archive = output/(package.name+'.zip')
        run('ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', package, archive)
    elif sys.platform == 'win32':
        archive = Path(shutil.make_archive(str(output/package.name), 'zip', build, package.name))
    else:
        archive = output/(package.name+'.tar.gz')
        with tarfile.open(archive, 'w:gz') as tar:
            tar.add(package, arcname=package.name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name+'.sha256').write_text(f'{digest}  {archive.name}\n', encoding='utf-8')
    print(f'Created {archive}', flush=True)


if __name__ == '__main__':
    main()
