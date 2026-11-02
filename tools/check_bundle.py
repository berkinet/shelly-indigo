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
    # Do not pre-add Packages: the entry point must find its own dependencies.
    # Indigo executes plugin.py rather than importing it as a normal module.
    script = """
from pathlib import Path
import sys
import types
server_plugin, packages = (Path(value).resolve() for value in sys.argv[1:])
sys.path.insert(0, str(server_plugin))
sys.modules['indigo'] = types.SimpleNamespace(PluginBase=object)
namespace = {'__name__': 'indigo_plugin_exec'}
source = (server_plugin / 'plugin.py').read_text()
exec(compile(source, 'plugin.py', 'exec'), namespace)
assert '__file__' not in namespace
assert isinstance(namespace['Plugin'], type)
import websocket, zeroconf, ifaddr
for module in (websocket, zeroconf, ifaddr):
    assert Path(module.__file__).resolve().is_relative_to(packages), module.__file__
print('Indigo entry-point execution and bundled dependency imports passed')
"""
    subprocess.run([sys.executable, '-I', '-S', '-c', script,
                    str(bundle / 'Server Plugin'), str(packages)], check=True)
print(f'Validated {archive.name}')
