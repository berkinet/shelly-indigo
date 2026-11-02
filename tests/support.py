from pathlib import Path
import sys
bundle = Path(__file__).resolve().parents[1] / 'Shelly.indigoPlugin/Contents'
sys.path.insert(0, str(bundle / 'Packages'))
sys.path.insert(0, str(bundle / 'Server Plugin'))
