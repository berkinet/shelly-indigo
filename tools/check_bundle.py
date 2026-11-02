"""Verify release metadata, portable dependencies, XML and importability in isolation."""
from pathlib import Path
import ast
import plistlib
import subprocess
import sys
import tempfile
import zipfile
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
version = plistlib.loads((ROOT / 'Shelly.indigoPlugin/Contents/Info.plist').read_bytes())['PluginVersion']
archive = ROOT / 'dist' / f'Shelly-{version}.indigoPlugin.zip'
with zipfile.ZipFile(archive) as package, tempfile.TemporaryDirectory() as temporary:
    package.extractall(temporary)
    bundle = Path(temporary) / 'Shelly.indigoPlugin/Contents'
    info = plistlib.loads((bundle / 'Info.plist').read_bytes())
    assert info['ServerApiVersion'] == '3.9'
    for xml in (bundle / 'Server Plugin').glob('*.xml'):
        ElementTree.parse(xml)
    for source in (bundle / 'Server Plugin').rglob('*.py'):
        ast.parse(source.read_text())
    packages = bundle / 'Packages'
    assert not list(packages.rglob('*.so')), 'Native extension found'
    assert not list(package_name for package_name in package.namelist() if '__pycache__' in package_name)
    for distribution in ('websocket_client', 'zeroconf', 'ifaddr'):
        metadata, = packages.glob(f'{distribution}-*.dist-info')
        assert any(p.name.upper().startswith(('LICENSE', 'COPYING'))
                   for p in metadata.rglob('*')), f'Missing license: {distribution}'
    script = ('import sys; sys.path[:0] = ' + repr([str(packages), str(bundle / 'Server Plugin')]) +
              '; import websocket, zeroconf, ifaddr; from shelly.worker import Worker; '
              'print("Portable bundle imports passed")')
    subprocess.run([sys.executable, '-I', '-S', '-c', script], check=True)
print(f'Validated {archive.name}')
