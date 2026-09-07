import platform
import sys


def is_raspberry_pi():
    """Check if running on Raspberry Pi."""
    try:
        with open('/proc/device-tree/model', 'r') as f:
            model = f.read()
            return 'Raspberry Pi' in model
    except (FileNotFoundError, Exception):
        return False


def is_macos():
    """Check if running on macOS."""
    return sys.platform == 'darwin'


def is_linux():
    """Check if running on Linux."""
    return sys.platform == 'linux'


def get_gpu_name():
    """Return GPU type/name for logging."""
    if is_raspberry_pi():
        try:
            with open('/proc/device-tree/model', 'r') as f:
                model = f.read().strip()
                return model
        except Exception:
            return "Unknown Pi"
    elif is_macos():
        try:
            import subprocess
            result = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.split('\n'):
                if 'Chipset Model' in line:
                    return line.split(':')[1].strip()
        except Exception:
            pass
        return "Apple GPU"
    else:
        return "Unknown GPU"
