# PyInstaller Runtime Hook for Windows Compatibility
# This hook runs BEFORE any application code
# It ensures critical C extension modules are loaded on Windows

import sys
import os

# === Windows-specific DLL and module pre-loading ===
if sys.platform == 'win32':
    # Step 1: Ensure DLL search paths are set up
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        internal_dir = os.path.join(exe_dir, '_internal')
        
        # Add _internal to PATH for DLL discovery
        if os.path.isdir(internal_dir):
            os.environ['PATH'] = internal_dir + os.pathsep + os.environ.get('PATH', '')
        
        # Register DLL directories (Python 3.8+)
        try:
            if hasattr(os, 'add_dll_directory'):
                os.add_dll_directory(exe_dir)
                if os.path.isdir(internal_dir):
                    os.add_dll_directory(internal_dir)
        except (OSError, FileNotFoundError, AttributeError):
            pass
    
    # Step 2: Pre-import critical C extension modules
    # These must be imported before asyncio/socket/multiprocessing tries to use them
    _critical_modules = [
        '_multiprocessing',
        '_overlapped',
        '_socket',
        '_ssl',
        '_ctypes',
    ]
    
    for _mod_name in _critical_modules:
        try:
            __import__(_mod_name)
        except ImportError:
            pass
    
    # Step 3: Pre-import standard library modules that depend on C extensions
    _stdlib_modules = [
        'multiprocessing',
        'multiprocessing.context',
        'multiprocessing.dummy',
        'multiprocessing.pool',
        'asyncio',
        'asyncio.events',
        'asyncio.windows_events',
        'asyncio.windows_utils',
        'selectors',
        'socket',
        'ssl',
        'subprocess',
        'concurrent.futures',
        'queue',
        'threading',
    ]
    
    for _mod_name in _stdlib_modules:
        try:
            __import__(_mod_name)
        except ImportError:
            pass
        except Exception:
            pass
