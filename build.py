#!/usr/bin/env python3
"""
Etsy Scraper 打包脚本
使用 PyInstaller 打包为独立可执行文件
"""
import os
import sys
import shutil
import subprocess
from pathlib import Path

# 项目信息
APP_NAME = "EtsyScraper"
APP_VERSION = "1.0.0"
MAIN_SCRIPT = "src/etsy_scraper/gui.py"

# 获取项目根目录
PROJECT_ROOT = Path(__file__).parent.absolute()


def clean_build():
    """清理构建目录"""
    dirs_to_clean = ['build', 'dist', '__pycache__']
    for d in dirs_to_clean:
        path = PROJECT_ROOT / d
        if path.exists():
            print(f"清理 {d}...")
            shutil.rmtree(path)
    
    # 清理 .spec 文件
    for spec_file in PROJECT_ROOT.glob("*.spec"):
        spec_file.unlink()
        print(f"清理 {spec_file.name}...")


def build_app():
    """构建应用"""
    print("=" * 60)
    print(f"构建 {APP_NAME} v{APP_VERSION}")
    print("=" * 60)
    
    # 检测操作系统
    if sys.platform == "darwin":
        platform_name = "macOS"
        icon_ext = "icns"
    elif sys.platform == "win32":
        platform_name = "Windows"
        icon_ext = "ico"
    else:
        platform_name = "Linux"
        icon_ext = "png"
    
    print(f"目标平台: {platform_name}")
    print()
    
    # PyInstaller 参数
    pyinstaller_args = [
        "pyinstaller",
        "--name", APP_NAME,
        "--windowed",  # GUI 模式，不显示控制台
        "--onedir",    # 打包为文件夹（比 onefile 启动更快）
        "--noconfirm", # 覆盖已有文件
        "--clean",     # 清理临时文件
        
        # 隐藏导入（PyInstaller 可能检测不到的依赖）
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
        
        # 收集 CustomTkinter 的所有资源文件（主题、字体等）
        "--collect-all", "customtkinter",
        
        # 主脚本
        str(PROJECT_ROOT / MAIN_SCRIPT),
    ]
    
    # 检查是否有图标文件
    icon_path = PROJECT_ROOT / "assets" / f"icon.{icon_ext}"
    if icon_path.exists():
        pyinstaller_args.extend(["--icon", str(icon_path)])
        print(f"使用图标: {icon_path}")
    
    # macOS 特定设置
    if sys.platform == "darwin":
        pyinstaller_args.extend([
            "--osx-bundle-identifier", "com.etsy.scraper",
        ])
    
    print("\n执行 PyInstaller...")
    print("-" * 40)
    
    # 执行打包
    result = subprocess.run(pyinstaller_args, cwd=PROJECT_ROOT)
    
    if result.returncode != 0:
        print("\n❌ 打包失败！")
        sys.exit(1)
    
    print("\n" + "=" * 60)
    print("✅ 打包成功！")
    print("=" * 60)
    
    # 显示输出位置
    dist_dir = PROJECT_ROOT / "dist"
    if sys.platform == "darwin":
        app_path = dist_dir / f"{APP_NAME}.app"
        if app_path.exists():
            print(f"\n📦 应用位置: {app_path}")
            print(f"\n运行方式:")
            print(f"  双击 {app_path.name}")
            print(f"  或: open \"{app_path}\"")
    else:
        exe_name = f"{APP_NAME}.exe" if sys.platform == "win32" else APP_NAME
        exe_path = dist_dir / APP_NAME / exe_name
        print(f"\n📦 应用位置: {exe_path}")
    
    print(f"\n💡 提示: 将 dist/{APP_NAME} 文件夹分发给用户")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Etsy Scraper 打包工具")
    parser.add_argument("--clean", action="store_true", help="仅清理构建文件")
    
    args = parser.parse_args()
    
    os.chdir(PROJECT_ROOT)
    
    if args.clean:
        clean_build()
        print("✅ 清理完成！")
    else:
        clean_build()
        build_app()


if __name__ == "__main__":
    main()
