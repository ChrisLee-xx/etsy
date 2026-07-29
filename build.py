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
  - "No module named '_multiprocessing'": Handled via PyInstaller hooks + --collect-binaries
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

# PyInstaller hooks directory (contains hook files for Windows C extensions)
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


def build_app():
    """Build application"""
    print("=" * 60)
    print(f"Building {APP_NAME} v{APP_VERSION}")
    print("=" * 60)
    
    # Detect OS
    if sys.platform == "darwin":
        platform_name = "macOS"
    elif sys.platform == "win32":
        platform_name = "Windows"
    else:
        platform_name = "Linux"
    
    print(f"Target platform: {platform_name}")
    print()
    
    # Main script
    main_script = str(SRC_DIR / "gui.py")
    
    pyinstaller_args = [
        "pyinstaller",
        "--name", APP_NAME,
        "--windowed",
        "--onedir",
        "--noconfirm",
        "--clean",
        
        # Add etsy_scraper directory to Python path
        "--paths", str(SRC_DIR),
        
        # Use custom hooks directory for Windows C extension modules
        "--additional-hooks-dir", str(HOOKS_DIR),
        
        # Runtime hook: pre-loads critical modules before app code
        "--runtime-hook", str(HOOKS_DIR / "hook-runtime_fix.py"),
        
        # Collect all resources for key packages
        "--collect-all", "customtkinter",
        "--collect-all", "undetected_chromedriver",
    ]
    
    # === Windows-specific handling ===
    if sys.platform == "win32":
        # Force-collect .pyd binary files from standard library
        # This is the key fix for _multiprocessing, _overlapped, etc.
        pyinstaller_args.extend([
            # Collect ALL binary extensions from Python standard library
            "--collect-binaries", "asyncio",
            "--collect-binaries", "multiprocessing",
            "--collect-binaries", "socket",
            "--collect-binaries", "ssl",
            "--collect-binaries", "selectors",
            "--collect-binaries", "subprocess",
            "--collect-binaries", "concurrent",
            "--collect-binaries", "ctypes",
        ])
        
        # Also collect the standard library DLLs directory
        python_dir = os.path.dirname(sys.executable)
        
        # DLLs directory (Python 3.8+ stores .pyd files here)
        dlls_dir = os.path.join(python_dir, "DLLs")
        if os.path.isdir(dlls_dir):
            # Add all .pyd files from DLLs directory
            for filename in os.listdir(dlls_dir):
                if filename.endswith('.pyd'):
                    pyd_path = os.path.join(dlls_dir, filename)
                    pyinstaller_args.extend(["--add-binary", f"{pyd_path};."])
            print(f"Added {len([f for f in os.listdir(dlls_dir) if f.endswith('.pyd')])} .pyd files from DLLs/")
        
        # Lib directory (older Python versions or some .pyd files)
        lib_dir = os.path.join(python_dir, "Lib")
        if not os.path.isdir(lib_dir):
            lib_dir = os.path.join(os.path.dirname(python_dir), "Lib")
        
        if os.path.isdir(lib_dir):
            for filename in os.listdir(lib_dir):
                if filename.endswith('.pyd'):
                    pyd_path = os.path.join(lib_dir, filename)
                    pyinstaller_args.extend(["--add-binary", f"{pyd_path};."])
        
        # Find and add python311.dll
        python_dll = os.path.join(python_dir, "python311.dll")
        if not os.path.exists(python_dll):
            python_dll = os.path.join(os.path.dirname(python_dir), "python311.dll")
        if not os.path.exists(python_dll):
            # Check DLLs directory
            python_dll = os.path.join(dlls_dir, "python311.dll")
        
        if os.path.exists(python_dll):
            pyinstaller_args.extend(["--add-binary", f"{python_dll};."])
            print(f"Added Python DLL: {os.path.basename(python_dll)}")
        else:
            print("Warning: python311.dll not found in expected locations")
        
        # Collect selenium's chromedriver binary
        try:
            import selenium
            selenium_dir = os.path.dirname(selenium.__file__)
            pyinstaller_args.extend(["--collect-binaries", "selenium"])
        except Exception:
            pass
    
    # === macOS-specific ===
    if sys.platform == "darwin":
        pyinstaller_args.extend([
            "--osx-bundle-identifier", "com.etsy.scraper",
        ])
    
    pyinstaller_args.append(main_script)
    
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
            print("1. Copy the ENTIRE 'dist/EtsyScraper/' folder (including _internal/)")
            print("2. Run 'EtsyScraper.exe' from within that folder")
            print("3. NEVER copy just the .exe file alone - the _internal folder is required!")
            print("4. If antivirus quarantines files, add an exclusion for the folder")
            print("5. First run may take a few seconds (Chrome auto-download)")


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
