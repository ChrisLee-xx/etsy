#!/usr/bin/env python3
"""
Etsy Scraper Build Script
Uses PyInstaller to package as standalone executable
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path

# Project info
APP_NAME = "EtsyScraper"
APP_VERSION = "1.0.0"

# Project root
PROJECT_ROOT = Path(__file__).parent.absolute()
SRC_DIR = PROJECT_ROOT / "src" / "etsy_scraper"


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
        sep = ":"
    elif sys.platform == "win32":
        platform_name = "Windows"
        sep = ";"
    else:
        platform_name = "Linux"
        sep = ":"
    
    print(f"Target platform: {platform_name}")
    print()
    
    # Main script and other modules
    main_script = str(SRC_DIR / "gui.py")
    
    pyinstaller_args = [
        "pyinstaller",
        "--name", APP_NAME,
        "--windowed",
        "--onedir",
        "--noconfirm",
        "--clean",
        
        # Add src directory to Python path
        "--paths", str(SRC_DIR),
        
        # Add other source files as data (will be extracted to _MEIPASS)
        "--add-data", f"{SRC_DIR / 'section_scraper.py'}{sep}.",
        "--add-data", f"{SRC_DIR / 'real_chrome_scraper.py'}{sep}.",
        "--add-data", f"{SRC_DIR / 'utils.py'}{sep}.",
        
        # Hidden imports for our modules
        "--hidden-import", "section_scraper",
        "--hidden-import", "real_chrome_scraper",
        "--hidden-import", "utils",
        
        # Hidden imports for dependencies
        "--hidden-import", "selenium",
        "--hidden-import", "selenium.webdriver",
        "--hidden-import", "selenium.webdriver.chrome.options",
        "--hidden-import", "selenium.webdriver.chrome.service",
        "--hidden-import", "selenium.webdriver.common.by",
        "--hidden-import", "selenium.webdriver.support.ui",
        "--hidden-import", "selenium.webdriver.support.expected_conditions",
        "--hidden-import", "requests",
        "--hidden-import", "urllib3",
        "--hidden-import", "certifi",
        "--hidden-import", "customtkinter",
        "--hidden-import", "PIL",
        "--hidden-import", "PIL.Image",
        "--hidden-import", "PIL._tkinter_finder",
        
        # Collect all customtkinter resources
        "--collect-all", "customtkinter",
        
        main_script,
    ]
    
    # macOS specific
    if sys.platform == "darwin":
        pyinstaller_args.extend([
            "--osx-bundle-identifier", "com.etsy.scraper",
        ])
    
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
