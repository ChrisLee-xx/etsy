#!/usr/bin/env python3
"""
Etsy Scraper Build Script
Uses PyInstaller to package as standalone executable

Windows Build Instructions:
  1. Copy this project to a Windows machine
  2. Install Python 3.11+ and Poetry
  3. Run: poetry install
  4. Run: python build.py
  5. The output will be in dist/EtsyScraper/

Common Windows Issues & Fixes:
  - "Failed to load Python DLL": Ensure antivirus doesn't quarantine files
  - "No module named '_overlapped'": This is now handled via hidden-import
  - Missing chromedriver: Run the app once to auto-download, then include _internal
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path

# Project info
APP_NAME = "EtsyScraper"
APP_VERSION = "0.1.0"

# Project root
PROJECT_ROOT = Path(__file__).parent.absolute()
SRC_DIR = PROJECT_ROOT / "src" / "etsy_scraper"

# Runtime hooks directory
HOOKS_DIR = PROJECT_ROOT / "build_hooks"


def clean_build():
    """Clean build directories"""
    dirs_to_clean = ['build', 'dist', '__pycache__']
    for d in dirs_to_clean:
        path = PROJECT_ROOT / d
        if path.exists():
            print(f"Cleaning {d}...")
            shutil.rmtree(path)
    
    # Clean .spec files
    for spec_file in PROJECT_ROOT.glob("*.spec"):
        spec_file.unlink()
        print(f"Cleaning {spec_file.name}...")


def create_runtime_hooks():
    """Create runtime hooks for Windows compatibility"""
    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Hook 1: Ensure critical DLLs and modules are available
    hook_content = '''
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
'''
    
    hook_file = HOOKS_DIR / "hook-runtime_fix.py"
    hook_file.write_text(hook_content.strip(), encoding='utf-8')
    print(f"Created runtime hook: {hook_file}")


def build_app():
    """Build application"""
    print("=" * 60)
    print(f"Building {APP_NAME} v{APP_VERSION}")
    print("=" * 60)
    
    # Detect OS
    if sys.platform == "darwin":
        platform_name = "macOS"
        sep = ":"
    elif sys.platform == "win32":
        platform_name = "Windows"
        sep = ";"
    else:
        platform_name = "Linux"
        sep = ":"
    
    print(f"Target platform: {platform_name}")
    print()
    
    # Create runtime hooks
    create_runtime_hooks()
    
    # Main script and other modules
    main_script = str(SRC_DIR / "gui.py")
    
    # Common hidden imports for all platforms
    common_hidden_imports = [
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
        "requests",
        "urllib3",
        "certifi",
        "charset_normalizer",
        "idna",
        "customtkinter",
        "undetected_chromedriver",
        "undetected_chromedriver.patcher",
        "PIL",
        "PIL.Image",
        "PIL._tkinter_finder",
        "tkinter",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "json",
        "threading",
        "datetime",
        "pathlib",
        "platform",
        "re",
        "collections",
        "subprocess",
        # Standard library modules needed by asyncio/socket on Windows
        "_overlapped",
        "_socket",
        "_ssl",
        "_ctypes",
        "ctypes",
        "asyncio",
        "selectors",
        "socket",
        "ssl",
        "multiprocessing",
        "multiprocessing.pool",
        "concurrent",
        "concurrent.futures",
        "queue",
        "threading",
    ]
    
    # Windows-specific hidden imports
    windows_hidden_imports = [
        "winreg",
        "winsound",
        "msvcrt",
        "win32api",
        "win32con",
        "win32process",
        "win32security",
        "ntdll",
        "pywintypes",
        "pythoncom",
        "win32com",
        "win32timezone",
    ]
    
    pyinstaller_args = [
        "pyinstaller",
        "--name", APP_NAME,
        "--windowed",
        "--onedir",
        "--noconfirm",
        "--clean",
        
        # Add etsy_scraper directory to Python path so modules can be found directly
        "--paths", str(SRC_DIR),
        
        # Add runtime hooks
        "--runtime-hook", str(HOOKS_DIR / "hook-runtime_fix.py"),
    ]
    
    # Add common hidden imports
    for imp in common_hidden_imports:
        pyinstaller_args.extend(["--hidden-import", imp])
    
    # Add Windows-specific imports only on Windows
    if sys.platform == "win32":
        for imp in windows_hidden_imports:
            pyinstaller_args.extend(["--hidden-import", imp])
    
    # Collect all resources for key packages
    pyinstaller_args.extend([
        "--collect-all", "customtkinter",
        "--collect-all", "undetected_chromedriver",
    ])
    
    # Collect Python standard library for Windows (ensure _overlapped is included)
    if sys.platform == "win32":
        pyinstaller_args.extend([
            "--collect-submodules", "_overlapped",
            "--collect-submodules", "_socket",
            "--collect-submodules", "asyncio",
        ])
    
    pyinstaller_args.append(main_script)
    
    # macOS specific
    if sys.platform == "darwin":
        pyinstaller_args.extend([
            "--osx-bundle-identifier", "com.etsy.scraper",
        ])
    
    # Windows specific: add the Python DLL explicitly
    if sys.platform == "win32":
        # Find and add python311.dll from the Python installation
        python_dir = os.path.dirname(sys.executable)
        python_dll = os.path.join(python_dir, "python311.dll")
        
        # Also check for python311.dll in root of Python installation
        if not os.path.exists(python_dll):
            python_dll = os.path.join(os.path.dirname(python_dir), "python311.dll")
        
        if os.path.exists(python_dll):
            pyinstaller_args.extend(["--add-binary", f"{python_dll};."])
            print(f"Added Python DLL: {python_dll}")
        else:
            print("Warning: python311.dll not found, PyInstaller will bundle it automatically")
    
    print("Running PyInstaller...")
    print("-" * 40)
    
    result = subprocess.run(pyinstaller_args, cwd=PROJECT_ROOT)
    
    if result.returncode != 0:
        print("\n[ERROR] Build failed!")
        sys.exit(1)
    
    print("\n" + "=" * 60)
    print("[SUCCESS] Build completed!")
    print("=" * 60)
    
    dist_dir = PROJECT_ROOT / "dist"
    if sys.platform == "darwin":
        app_path = dist_dir / f"{APP_NAME}.app"
        if app_path.exists():
            print(f"\nApplication: {app_path}")
    else:
        exe_name = f"{APP_NAME}.exe" if sys.platform == "win32" else APP_NAME
        exe_path = dist_dir / APP_NAME / exe_name
        print(f"\nApplication: {exe_path}")
        
        # Print Windows deployment instructions
        if sys.platform == "win32":
            print("\n" + "=" * 60)
            print("Windows Deployment Instructions:")
            print("=" * 60)
            print("1. Copy the entire 'dist/EtsyScraper/' folder to the target machine")
            print("2. Run 'EtsyScraper.exe' from within that folder")
            print("3. Do NOT copy just the .exe file alone - the _internal folder is required")
            print("4. If antivirus quarantines files, add an exclusion for the folder")
            print("5. First run may take a few seconds to start (Chrome auto-download)")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Etsy Scraper Build Tool")
    parser.add_argument("--clean", action="store_true", help="Only clean build files")
    args = parser.parse_args()
    
    os.chdir(PROJECT_ROOT)
    
    if args.clean:
        clean_build()
        print("[SUCCESS] Clean completed!")
    else:
        clean_build()
        build_app()


if __name__ == "__main__":
    main()
