# PyInstaller Hook for etsy_scraper
# This hook explicitly imports all modules that PyInstaller's analysis
# might miss, especially Windows C extension modules needed by asyncio/socket/multiprocessing

# Force import of Windows-specific C extension modules
# These are part of Python's standard library but PyInstaller's static analysis
# cannot detect them because they're loaded dynamically

hiddenimports = []
datas = []
binaries = []

import sys
import os

if sys.platform == 'win32':
    # Windows C extension modules that must be bundled
    # These are loaded implicitly by asyncio, socket, multiprocessing, etc.
    
    windows_hidden_imports = [
        # Socket/Network related
        "_socket",
        "_ssl",
        "_overlapped",
        # Multiprocessing
        "_multiprocessing",
        "_multiprocessing.pool",
        # C types
        "_ctypes",
        "_ctypes.test",
        # Windows specific
        "winreg",
        "winsound",
        "msvcrt",
        "_msi",
        # Python runtime extensions
        "_json",
        "_pickle",
        "_csv",
        "_datetime",
        "_decimal",
        "_hashlib",
        "_sqlite3",
        "_bz2",
        "_lzma",
        "_ssl",
        # Asyncio dependencies
        "asyncio",
        "asyncio.events",
        "asyncio.tasks",
        "asyncio.transports",
        "asyncio.proactor_events",
        "asyncio.windows_events",
        "asyncio.windows_utils",
        # Additional standard lib
        "selectors",
        "socket",
        "ssl",
        "multiprocessing",
        "multiprocessing.context",
        "multiprocessing.dummy",
        "concurrent",
        "concurrent.futures",
        "queue",
        "threading",
        "_thread",
        "subprocess",
        "shutil",
        "tempfile",
        "filecmp",
        "stat",
        "platform",
        "ctypes",
        "ctypes.util",
        "ctypes.wintypes",
    ]
    
    hiddenimports.extend(windows_hidden_imports)
    
    # Also try to locate and add the actual .pyd files as binaries
    python_dir = os.path.dirname(sys.executable)
    
    # Standard library directory
    stdlib_dir = os.path.join(python_dir, 'Lib')
    if not os.path.isdir(stdlib_dir):
        # Try alternative location
        stdlib_dir = os.path.join(os.path.dirname(python_dir), 'Lib')
    
    # Site-packages (for our dependencies)
    site_packages = []
    for p in sys.path:
        if 'site-packages' in p and os.path.isdir(p):
            site_packages.append(p)
    
    # .pyd files to look for in standard library
    pyd_modules = [
        '_multiprocessing',
        '_overlapped',
        '_socket',
        '_ssl',
        '_ctypes',
        '_json',
        '_pickle',
        '_csv',
        '_datetime',
        '_decimal',
        '_hashlib',
        '_sqlite3',
        '_bz2',
        '_lzma',
        '_msi',
    ]
    
    for pyd_name in pyd_modules:
        # Check in DLLs directory (Python 3.8+)
        dlls_dir = os.path.join(python_dir, 'DLLs')
        if os.path.isdir(dlls_dir):
            pyd_path = os.path.join(dlls_dir, f'{pyd_name}.pyd')
            if os.path.isfile(pyd_path):
                binaries.append((pyd_path, '.'))
        
        # Check in Lib directory
        if os.path.isdir(stdlib_dir):
            pyd_path = os.path.join(stdlib_dir, f'{pyd_name}.pyd')
            if os.path.isfile(pyd_path):
                binaries.append((pyd_path, '.'))

# Common hidden imports for all platforms
common_hidden_imports = [
    # Standard library
    "json",
    "threading",
    "datetime",
    "pathlib",
    "platform",
    "re",
    "collections",
    "subprocess",
    "socket",
    "ssl",
    "selectors",
    "multiprocessing",
    "concurrent.futures",
    "queue",
    # Our modules
    "section_scraper",
    "real_chrome_scraper",
    "utils",
    # Dependencies
    "selenium",
    "selenium.webdriver",
    "selenium.webdriver.chrome.options",
    "selenium.webdriver.chrome.service",
    "selenium.webdriver.common.by",
    "selenium.webdriver.common.action_chains",
    "selenium.webdriver.support.ui",
    "selenium.webdriver.support.expected_conditions",
    "selenium.common.exceptions",
    "requests",
    "urllib3",
    "urllib3.util",
    "certifi",
    "charset_normalizer",
    "idna",
    "customtkinter",
    "undetected_chromedriver",
    "undetected_chromedriver.patcher",
    "PIL",
    "PIL.Image",
    "tkinter",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "tkinter.ttk",
]

for imp in common_hidden_imports:
    if imp not in hiddenimports:
        hiddenimports.append(imp)
