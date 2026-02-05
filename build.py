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
MAIN_SCRIPT = "src/etsy_scraper/gui.py"

# Project root
PROJECT_ROOT = Path(__file__).parent.absolute()


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
        icon_ext = "icns"
    elif sys.platform == "win32":
        platform_name = "Windows"
        icon_ext = "ico"
    else:
        platform_name = "Linux"
        icon_ext = "png"
    
    print(f"Target platform: {platform_name}")
    print()
    
    # PyInstaller arguments
    pyinstaller_args = [
        "pyinstaller",
        "--name", APP_NAME,
        "--windowed",  # GUI mode, no console
        "--onedir",    # Package as folder (faster startup than onefile)
        "--noconfirm", # Overwrite existing files
        "--clean",     # Clean temp files
        
        # Hidden imports
        "--hidden-import", "selenium",
        "--hidden-import", "selenium.webdriver",
        "--hidden-import", "selenium.webdriver.chrome.options",
        "--hidden-import", "selenium.webdriver.common.by",
        "--hidden-import", "selenium.webdriver.support.ui",
        "--hidden-import", "selenium.webdriver.support.expected_conditions",
        "--hidden-import", "requests",
        "--hidden-import", "customtkinter",
        "--hidden-import", "PIL",
        "--hidden-import", "PIL._tkinter_finder",
        
        # Collect CustomTkinter resources (themes, fonts, etc.)
        "--collect-all", "customtkinter",
        
        # Main script
        str(PROJECT_ROOT / MAIN_SCRIPT),
    ]
    
    # Check for icon file
    icon_path = PROJECT_ROOT / "assets" / f"icon.{icon_ext}"
    if icon_path.exists():
        pyinstaller_args.extend(["--icon", str(icon_path)])
        print(f"Using icon: {icon_path}")
    
    # macOS specific settings
    if sys.platform == "darwin":
        pyinstaller_args.extend([
            "--osx-bundle-identifier", "com.etsy.scraper",
        ])
    
    print("\nRunning PyInstaller...")
    print("-" * 40)
    
    # Execute build
    result = subprocess.run(pyinstaller_args, cwd=PROJECT_ROOT)
    
    if result.returncode != 0:
        print("\n[ERROR] Build failed!")
        sys.exit(1)
    
    print("\n" + "=" * 60)
    print("[SUCCESS] Build completed!")
    print("=" * 60)
    
    # Show output location
    dist_dir = PROJECT_ROOT / "dist"
    if sys.platform == "darwin":
        app_path = dist_dir / f"{APP_NAME}.app"
        if app_path.exists():
            print(f"\nApplication: {app_path}")
            print(f"\nTo run:")
            print(f"  Double-click {app_path.name}")
            print(f"  Or: open \"{app_path}\"")
    else:
        exe_name = f"{APP_NAME}.exe" if sys.platform == "win32" else APP_NAME
        exe_path = dist_dir / APP_NAME / exe_name
        print(f"\nApplication: {exe_path}")
    
    print(f"\nDistribute: dist/{APP_NAME} folder")


def main():
    """Main function"""
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
