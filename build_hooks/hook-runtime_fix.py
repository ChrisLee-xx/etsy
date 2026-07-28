# PyInstaller Runtime Hook for Windows Compatibility
# This hook runs before any other code to ensure critical modules are available

import sys
import os

# Ensure _internal directory is in the DLL search path on Windows
if sys.platform == 'win32':
    # Get the directory where the executable is located
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        internal_dir = os.path.join(exe_dir, '_internal')
        
        # Add _internal to PATH for DLL discovery
        if os.path.isdir(internal_dir):
            os.environ['PATH'] = internal_dir + os.pathsep + os.environ.get('PATH', '')
        
        # Add to DLL search directories
        try:
            if hasattr(os, 'add_dll_directory'):
                os.add_dll_directory(exe_dir)
                if os.path.isdir(internal_dir):
                    os.add_dll_directory(internal_dir)
        except (OSError, FileNotFoundError, AttributeError):
            pass