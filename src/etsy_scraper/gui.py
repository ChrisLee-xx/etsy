"""
Etsy Scraper GUI - CustomTkinter 桌面应用
"""
import json
import os
import random
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Optional, List, Set

# PyInstaller 打包后，添加 _MEIPASS 到 sys.path
if getattr(sys, 'frozen', False):
    # Running as compiled
    bundle_dir = sys._MEIPASS
    if bundle_dir not in sys.path:
        sys.path.insert(0, bundle_dir)

import customtkinter as ctk

# 导入核心功能 - 兼容 PyInstaller 打包
try:
    from .section_scraper import (
        ScrapeProgress, parse_section_url, get_section_info,
        extract_product_links, process_product, ImageNameTracker,
        sanitize_folder_name,
    )
    from .real_chrome_scraper import (
        extract_data_with_selenium, download_images, sanitize_filename,
        create_patched_driver, start_chrome_with_debug, wait_for_chrome_ready,
        _DriverContext, _is_access_blocked, get_random_ua,
    )
    from .utils import parse_image_selection, parse_filter_words
except ImportError:
    from section_scraper import (
        ScrapeProgress, parse_section_url, get_section_info,
        extract_product_links, process_product, ImageNameTracker,
        sanitize_folder_name,
    )
    from real_chrome_scraper import (
        extract_data_with_selenium, download_images, sanitize_filename,
        create_patched_driver, start_chrome_with_debug, wait_for_chrome_ready,
        _DriverContext, _is_access_blocked, get_random_ua,
    )
    from utils import parse_image_selection, parse_filter_words


# 设置主题
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


def _get_config_dir() -> Path:
    """获取配置文件目录"""
    config_dir = Path.home() / ".etsy_scraper"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def _get_config_path() -> Path:
    """获取配置文件路径"""
    return _get_config_dir() / "config.json"


def load_config() -> dict:
    """加载保存的配置"""
    config_path = _get_config_path()
    if config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(data: dict):
    """保存配置到文件"""
    config_path = _get_config_path()
    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_default_output_folder() -> str:
    """获取默认输出文件夹：桌面/EtsyScraper_YYYYMMDD，自动复用同日期文件夹"""
    desktop = Path.home() / "Desktop"
    if not desktop.exists():
        desktop = Path.home()
    
    today = datetime.now().strftime("%Y%m%d")
    target_name = f"EtsyScraper_{today}"
    target_path = desktop / target_name
    
    # 如果今天的文件夹已存在，直接复用
    if target_path.exists():
        return str(target_path)
    
    # 查找桌面上最近的 EtsyScraper_ 文件夹，如果存在则复用
    existing = sorted(
        [d for d in desktop.iterdir() if d.is_dir() and d.name.startswith("EtsyScraper_")],
        key=lambda p: p.name,
        reverse=True
    )
    if existing:
        return str(existing[0])
    
    # 没有已有文件夹，创建今天的
    target_path.mkdir(parents=True, exist_ok=True)
    return str(target_path)


class ScraperWorker:
    """后台抓取工作器"""
    
    def __init__(self, app, mode: str, urls: List[str], output_dir: str,
                 image_selection: Optional[List[int]] = None,
                 filter_words: Optional[List[str]] = None,
                 delay: float = 2.0,
                 resume: bool = True,
                 port: int = 9222):
        self.app = app
        self.mode = mode
        self.urls = urls
        self.output_dir = output_dir
        self.image_selection = image_selection
        self.filter_words = filter_words
        self.delay = delay
        self.resume = resume
        self.port = port
        self.chrome_process = None
        self.driver = None
        self._stop_flag = False
        
        self._thread = None
    
    def log(self, msg: str):
        self.app.after(0, lambda: self.app.log(msg))
    
    def update_progress(self, current: int, total: int):
        self.app.after(0, lambda: self.app.update_progress(current, total))
    
    def stop(self):
        self._stop_flag = True
    
    
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
    
    def _run(self):
        try:
            self.log("🚀 启动 Chrome 浏览器...")
            self.chrome_process = start_chrome_with_debug(self.urls[0], self.port)
            
            self.log("⏳ 等待浏览器就绪...")
            if not wait_for_chrome_ready(self.port):
                self.app.after(0, lambda: self.app.on_finished(False, "Chrome 启动失败！请先关闭所有 Chrome 窗口。"))
                return
            
            self.log("✅ Chrome 已启动！")
            time.sleep(3)
            self.log("✅ 开始抓取...")
            
            self.driver = create_patched_driver(self.port)
            self.ctx = _DriverContext(self.driver, self.chrome_process, self.port)
            
            # 开始前检测封锁，自动重启恢复
            if _is_access_blocked(self.driver):
                self.log("🚫 检测到访问限制，自动重启 Chrome...")
                if self.ctx.handle_block(self.urls[0]):
                    self.driver = self.ctx.driver
                    self.chrome_process = self.ctx.chrome_process
                    self.log("✅ 已恢复，继续抓取")
                else:
                    self.app.after(0, lambda: self.app.on_finished(False, "访问被限制，多次重启仍无法恢复"))
                    return
            
            if self.mode == 'product':
                self._scrape_products()
            else:
                self._scrape_sections()
            
        except Exception as e:
            self.app.after(0, lambda: self.app.on_finished(False, f"错误: {str(e)}"))
        finally:
            try:
                if hasattr(self, 'ctx') and self.ctx and self.ctx.chrome_process:
                    self.ctx.chrome_process.terminate()
                elif self.chrome_process:
                    self.chrome_process.terminate()
            except Exception:
                pass
    
    def _scrape_products(self):
        total = len(self.urls)
        success_count = 0
        fail_count = 0
        consecutive_fails = 0
        output_path = Path(self.output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        for idx, url in enumerate(self.urls, 1):
            if self._stop_flag:
                break

            self.log(f"\n[{idx}/{total}] 处理商品...")
            self.update_progress(idx, total)

            if idx > 1:
                try:
                    self.ctx.driver.get(url)
                    time.sleep(2)
                except Exception as e:
                    self.log(f"  ❌ 导航失败: {e}")
                    fail_count += 1
                    consecutive_fails += 1
                    continue

            # 封锁检测
            if _is_access_blocked(self.ctx.driver):
                self.log("🚫 检测到访问限制，自动重启 Chrome...")
                if self.ctx.handle_block(url):
                    self.log("✅ 已恢复")
                    consecutive_fails = 0
                else:
                    self.log("❌ 无法恢复，停止")
                    break

            result = extract_data_with_selenium(self.ctx.driver)

            if not result or not result.get('title'):
                self.log("  ❌ 抓取失败！")
                fail_count += 1
                consecutive_fails += 1

                if consecutive_fails >= 3:
                    self.log(f"\n⚠️ 连续 {consecutive_fails} 次失败，尝试重启 Chrome...")
                    if self.ctx.handle_block(url):
                        self.log("✅ 已恢复，继续")
                        consecutive_fails = 0
                    else:
                        self.log("❌ 无法恢复，停止")
                        break
                continue

            success_count += 1
            consecutive_fails = 0
            self.log(f"  ✅ {result.get('title', '')[:40]}...")
            self.log(f"  📷 图片: {len(result.get('images', []))} 张")

            product_id = result.get('product_id', 'unknown')
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            json_path = output_path / f"product_{product_id}_{timestamp}.json"
            json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))

            self._download_images(result.get('images', []), result.get('title', ''), output_path)

            # driver 可能在封锁恢复后被替换，同步引用
            self.driver = self.ctx.driver
            self.chrome_process = self.ctx.chrome_process

            if idx < total:
                time.sleep(max(1.0, self.delay + random.uniform(-0.5, 1.0)))

        self.update_progress(total, total)
        self.app.after(0, lambda: self.app.on_finished(True, f"完成！成功: {success_count}, 失败: {fail_count}"))
    
    def _scrape_sections(self):
        total_sections = len(self.urls)
        total_success = 0
        total_fail = 0
        
        for sec_idx, url in enumerate(self.urls, 1):
            if self._stop_flag:
                break
            
            try:
                shop_name, section_id = parse_section_url(url)
            except ValueError:
                self.log(f"\n❌ 无效 URL: {url}")
                continue
            
            self.log(f"\n[Section {sec_idx}/{total_sections}] {shop_name}")
            
            if sec_idx > 1:
                try:
                    self.driver.get(url)
                    time.sleep(2)
                except Exception as e:
                    self.log(f"  ❌ 导航失败: {e}")
                    continue
            
            # 获取 Section 信息（在创建输出目录之前）
            section_name, total_items = get_section_info(self.driver, section_id)
            self.log(f"  Section: {section_name} ({total_items} 件)")
            
            # 使用 section 名称命名文件夹
            if section_name and section_name != "section":
                section_dir_name = sanitize_folder_name(section_name)
            else:
                section_dir_name = f"{shop_name}_{section_id}"
            
            # 同名文件夹冲突检测
            candidate_path = Path(self.output_dir) / section_dir_name
            if candidate_path.exists():
                progress_file = candidate_path / ".progress.json"
                if progress_file.exists():
                    try:
                        with open(progress_file, 'r', encoding='utf-8') as f:
                            existing_progress = json.load(f)
                        if existing_progress.get('section_id') != section_id:
                            section_dir_name = f"{section_dir_name}_{section_id}"
                    except Exception:
                        pass
            
            output_path = Path(self.output_dir) / section_dir_name
            output_path.mkdir(parents=True, exist_ok=True)
            
            progress = ScrapeProgress(output_path, url, shop_name, section_id)
            completed_ids: Set[str] = set()
            if self.resume:
                try:
                    completed_ids = progress.load()
                    if completed_ids:
                        self.log(f"  📋 已完成: {len(completed_ids)} 个")
                except Exception:
                    pass
            
            listing_ids = extract_product_links(self.driver, url, total_items=total_items)
            
            if not listing_ids:
                self.log("  ❌ 没有找到商品")
                continue
            
            self.log(f"  ✅ 找到 {len(listing_ids)} 个商品")
            progress.set_total_found(len(listing_ids))
            
            pending_ids = [lid for lid in listing_ids if lid not in completed_ids]
            
            if not pending_ids:
                self.log("  ✅ 全部完成")
                continue
            
            name_tracker = ImageNameTracker()
            consecutive_fails = 0
            
            for i, listing_id in enumerate(pending_ids, 1):
                if self._stop_flag:
                    break
                
                self.update_progress(i, len(pending_ids))
                
                if process_product(self.ctx, listing_id, output_path, name_tracker,
                                  image_selection=self.image_selection,
                                  filter_words=self.filter_words):
                    total_success += 1
                    consecutive_fails = 0
                    progress.save(listing_id)
                else:
                    total_fail += 1
                    consecutive_fails += 1
                    
                    if consecutive_fails >= 3:
                        product_url = f"https://www.etsy.com/listing/{listing_id}"
                        self.log(f"\n⚠️ 连续 {consecutive_fails} 个失败，疑似被封锁，自动重启 Chrome...")
                        if self.ctx.handle_block(product_url):
                            self.log("✅ 已恢复，继续抓取")
                            consecutive_fails = 0
                        else:
                            self.log("❌ 无法恢复，停止当前 Section")
                            break

                # driver 可能在封锁恢复后被替换，同步引用
                self.driver = self.ctx.driver
                self.chrome_process = self.ctx.chrome_process
                
                if i < len(pending_ids):
                    time.sleep(max(1.0, self.delay + random.uniform(-0.5, 1.0)))
            
            self.update_progress(len(pending_ids), len(pending_ids))
        
        self.app.after(0, lambda: self.app.on_finished(True, f"完成！成功: {total_success}, 失败: {total_fail}"))
    
    def _download_images(self, images: List[str], title: str, output_dir: Path):
        import requests

        if not images or not title:
            return

        try:
            from .utils import filter_title
        except ImportError:
            from utils import filter_title
        display_title = title
        if self.filter_words:
            display_title = filter_title(title, self.filter_words)

        safe_title = sanitize_filename(display_title)

        if self.image_selection:
            download_list = [(i, images[i-1]) for i in self.image_selection if 1 <= i <= len(images)]
        else:
            download_list = [(i+1, url) for i, url in enumerate(images)]

        headers = {
            "User-Agent": get_random_ua(),
            "Referer": "https://www.etsy.com/"
        }

        for idx, url in download_list:
            try:
                ext = url.split('.')[-1].split('?')[0] or 'jpg'
                filename = f"{safe_title}-{idx}.{ext}"
                filepath = output_dir / filename

                resp = requests.get(url, headers=headers, timeout=30)
                if resp.status_code == 200:
                    filepath.write_bytes(resp.content)
                    self.log(f"    📥 图片 {idx}")
            except Exception:
                self.log(f"    ❌ 图片 {idx} 下载失败")

            time.sleep(random.uniform(0.3, 0.8))


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        self.title("Etsy Scraper")
        self.geometry("950x850")
        self.minsize(900, 800)
        
        self.worker: Optional[ScraperWorker] = None
        
        self.setup_ui()
        self._load_saved_config()
        
        # 关闭窗口时保存配置
        self.protocol("WM_DELETE_WINDOW", self._on_close)
    
    def setup_ui(self):
        # 主容器
        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.pack(fill="both", expand=True, padx=30, pady=30)
        
        # ========== 标题 ==========
        title_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        title_frame.pack(fill="x", pady=(0, 20))
        
        title_label = ctk.CTkLabel(
            title_frame,
            text="🛍️ Etsy Scraper",
            font=ctk.CTkFont(size=32, weight="bold")
        )
        title_label.pack(anchor="w")
        
        subtitle_label = ctk.CTkLabel(
            title_frame,
            text="使用真实 Chrome 浏览器的 Etsy 商品图片爬虫",
            font=ctk.CTkFont(size=14),
            text_color="gray"
        )
        subtitle_label.pack(anchor="w")
        
        # ========== Tab ==========
        self.tabview = ctk.CTkTabview(self.main_frame, height=320)
        self.tabview.pack(fill="x", pady=(0, 15))
        
        self.tabview.add("📂 Section 批量")
        self.tabview.add("📦 单商品抓取")
        
        self.setup_section_tab(self.tabview.tab("📂 Section 批量"))
        self.setup_product_tab(self.tabview.tab("📦 单商品抓取"))
        
        self.tabview.set("📂 Section 批量")
        
        # ========== 进度条和按钮（放在日志上方） ==========
        control_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        control_frame.pack(fill="x", pady=(0, 10))
        
        # 左边：进度条
        progress_frame = ctk.CTkFrame(control_frame, fg_color="transparent")
        progress_frame.pack(side="left", fill="x", expand=True)
        
        self.progress_bar = ctk.CTkProgressBar(progress_frame, height=20)
        self.progress_bar.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.progress_bar.set(0)
        
        self.progress_label = ctk.CTkLabel(
            progress_frame,
            text="就绪",
            font=ctk.CTkFont(size=14, weight="bold"),
            width=80
        )
        self.progress_label.pack(side="left")
        
        # 右边：按钮
        self.stop_btn = ctk.CTkButton(
            control_frame,
            text="⏹️ 停止",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#dc3545",
            hover_color="#c82333",
            width=100,
            height=40,
            state="disabled",
            command=self.on_stop
        )
        self.stop_btn.pack(side="right")
        
        # ========== 日志区域 ==========
        log_label = ctk.CTkLabel(
            self.main_frame,
            text="📋 运行日志",
            font=ctk.CTkFont(size=16, weight="bold")
        )
        log_label.pack(anchor="w", pady=(0, 5))
        
        self.log_text = ctk.CTkTextbox(
            self.main_frame,
            height=180,
            font=ctk.CTkFont(family="Menlo", size=13),
            fg_color="#1a1a2e",
            text_color="#eee"
        )
        self.log_text.pack(fill="both", expand=True)
    
    def setup_product_tab(self, parent):
        # URL 输入
        url_label = ctk.CTkLabel(
            parent,
            text="商品链接（多个链接换行分隔）：",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        url_label.pack(anchor="w", pady=(10, 5))
        
        self.product_urls = ctk.CTkTextbox(parent, height=80, font=ctk.CTkFont(size=14))
        self.product_urls.pack(fill="x", pady=(0, 15))
        self.product_urls.insert("0.0", "")
        
        # 选项区域
        options_frame = ctk.CTkFrame(parent, fg_color="transparent")
        options_frame.pack(fill="x")
        
        # 第一行：输出目录
        row1 = ctk.CTkFrame(options_frame, fg_color="transparent")
        row1.pack(fill="x", pady=8)
        
        ctk.CTkLabel(row1, text="输出目录：", font=ctk.CTkFont(size=14), width=100).pack(side="left")
        self.product_output = ctk.CTkEntry(row1, font=ctk.CTkFont(size=14), height=40)
        self.product_output.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.product_output.insert(0, get_default_output_folder())
        
        browse_btn = ctk.CTkButton(
            row1, text="浏览...", width=80, height=40,
            fg_color="#6c757d", hover_color="#5a6268",
            command=lambda: self.browse_folder(self.product_output)
        )
        browse_btn.pack(side="right")
        
        # 第二行：图片选择
        row2 = ctk.CTkFrame(options_frame, fg_color="transparent")
        row2.pack(fill="x", pady=8)
        
        ctk.CTkLabel(row2, text="图片选择：", font=ctk.CTkFont(size=14), width=100).pack(side="left")
        self.product_images = ctk.CTkEntry(
            row2, font=ctk.CTkFont(size=14), height=40,
            placeholder_text="如: 1 或 1,3,5 或 2-4（留空下载全部）"
        )
        self.product_images.pack(side="left", fill="x", expand=True)
        
        # 第三行：标题过滤
        row3 = ctk.CTkFrame(options_frame, fg_color="transparent")
        row3.pack(fill="x", pady=8)
        
        ctk.CTkLabel(row3, text="标题过滤：", font=ctk.CTkFont(size=14), width=100).pack(side="left")
        self.product_filter = ctk.CTkEntry(
            row3, font=ctk.CTkFont(size=14), height=40,
            placeholder_text="如: Canvas,Poster,Wall Art"
        )
        self.product_filter.pack(side="left", fill="x", expand=True)
        
        # 第四行：延迟和端口
        row4 = ctk.CTkFrame(options_frame, fg_color="transparent")
        row4.pack(fill="x", pady=8)
        
        ctk.CTkLabel(row4, text="延迟(秒)：", font=ctk.CTkFont(size=14), width=100).pack(side="left")
        self.product_delay = ctk.CTkEntry(row4, font=ctk.CTkFont(size=14), height=40, width=80)
        self.product_delay.pack(side="left")
        self.product_delay.insert(0, "2.0")
        
        ctk.CTkLabel(row4, text="Chrome端口：", font=ctk.CTkFont(size=14), width=120).pack(side="left", padx=(30, 0))
        self.product_port = ctk.CTkEntry(row4, font=ctk.CTkFont(size=14), height=40, width=80)
        self.product_port.pack(side="left")
        self.product_port.insert(0, "9222")
        
        # 开始按钮
        self.product_start_btn = ctk.CTkButton(
            parent,
            text="🚀 开始抓取",
            font=ctk.CTkFont(size=18, weight="bold"),
            height=50,
            command=self.start_product_scrape
        )
        self.product_start_btn.pack(fill="x", pady=(20, 10))
    
    def setup_section_tab(self, parent):
        # URL 输入
        url_label = ctk.CTkLabel(
            parent,
            text="Section 链接（多个链接换行分隔）：",
            font=ctk.CTkFont(size=14, weight="bold")
        )
        url_label.pack(anchor="w", pady=(10, 5))
        
        self.section_urls = ctk.CTkTextbox(parent, height=80, font=ctk.CTkFont(size=14))
        self.section_urls.pack(fill="x", pady=(0, 15))
        
        # 选项区域
        options_frame = ctk.CTkFrame(parent, fg_color="transparent")
        options_frame.pack(fill="x")
        
        # 第一行：输出目录
        row1 = ctk.CTkFrame(options_frame, fg_color="transparent")
        row1.pack(fill="x", pady=8)
        
        ctk.CTkLabel(row1, text="输出目录：", font=ctk.CTkFont(size=14), width=100).pack(side="left")
        self.section_output = ctk.CTkEntry(row1, font=ctk.CTkFont(size=14), height=40)
        self.section_output.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.section_output.insert(0, get_default_output_folder())
        
        browse_btn = ctk.CTkButton(
            row1, text="浏览...", width=80, height=40,
            fg_color="#6c757d", hover_color="#5a6268",
            command=lambda: self.browse_folder(self.section_output)
        )
        browse_btn.pack(side="right")
        
        # 第二行：图片选择
        row2 = ctk.CTkFrame(options_frame, fg_color="transparent")
        row2.pack(fill="x", pady=8)
        
        ctk.CTkLabel(row2, text="图片选择：", font=ctk.CTkFont(size=14), width=100).pack(side="left")
        self.section_images = ctk.CTkEntry(
            row2, font=ctk.CTkFont(size=14), height=40,
            placeholder_text="如: 1 或 1,3,5 或 2-4（留空下载全部）"
        )
        self.section_images.pack(side="left", fill="x", expand=True)
        
        # 第三行：标题过滤
        row3 = ctk.CTkFrame(options_frame, fg_color="transparent")
        row3.pack(fill="x", pady=8)
        
        ctk.CTkLabel(row3, text="标题过滤：", font=ctk.CTkFont(size=14), width=100).pack(side="left")
        self.section_filter = ctk.CTkEntry(
            row3, font=ctk.CTkFont(size=14), height=40,
            placeholder_text="如: Canvas,Poster,Wall Art"
        )
        self.section_filter.pack(side="left", fill="x", expand=True)
        
        # 第四行：延迟、端口、断点续传
        row4 = ctk.CTkFrame(options_frame, fg_color="transparent")
        row4.pack(fill="x", pady=8)
        
        ctk.CTkLabel(row4, text="延迟(秒)：", font=ctk.CTkFont(size=14), width=100).pack(side="left")
        self.section_delay = ctk.CTkEntry(row4, font=ctk.CTkFont(size=14), height=40, width=80)
        self.section_delay.pack(side="left")
        self.section_delay.insert(0, "2.0")
        
        ctk.CTkLabel(row4, text="Chrome端口：", font=ctk.CTkFont(size=14), width=120).pack(side="left", padx=(30, 0))
        self.section_port = ctk.CTkEntry(row4, font=ctk.CTkFont(size=14), height=40, width=80)
        self.section_port.pack(side="left")
        self.section_port.insert(0, "9222")
        
        self.section_resume = ctk.CTkCheckBox(
            row4,
            text="断点续传",
            font=ctk.CTkFont(size=14)
        )
        self.section_resume.pack(side="left", padx=(30, 0))
        self.section_resume.select()
        
        # 开始按钮
        self.section_start_btn = ctk.CTkButton(
            parent,
            text="🚀 开始抓取",
            font=ctk.CTkFont(size=18, weight="bold"),
            height=50,
            command=self.start_section_scrape
        )
        self.section_start_btn.pack(fill="x", pady=(20, 10))
    
    def browse_folder(self, entry: ctk.CTkEntry):
        folder = filedialog.askdirectory()
        if folder:
            entry.delete(0, "end")
            entry.insert(0, folder)
    
    def log(self, msg: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.insert("end", f"[{timestamp}] {msg}\n")
        self.log_text.see("end")
    
    def update_progress(self, current: int, total: int):
        if total > 0:
            self.progress_bar.set(current / total)
            self.progress_label.configure(text=f"{current} / {total}")
    
    def start_product_scrape(self):
        urls_text = self.product_urls.get("0.0", "end").strip()
        if not urls_text:
            messagebox.showwarning("提示", "请输入商品链接！")
            return
        
        urls = [u.strip() for u in urls_text.split('\n') if u.strip()]
        
        for url in urls:
            # 支持各种地区前缀的 Etsy 链接，如 etsy.com/listing/ 或 etsy.com/sg-en/listing/
            if 'etsy.com' not in url or '/listing/' not in url:
                messagebox.showerror("错误", f"无效链接:\n{url}")
                return
        
        image_selection = None
        img_text = self.product_images.get().strip()
        if img_text:
            try:
                image_selection = parse_image_selection(img_text)
            except ValueError as e:
                messagebox.showerror("错误", str(e))
                return
        
        filter_words = None
        filter_text = self.product_filter.get().strip()
        if filter_text:
            filter_words = parse_filter_words(filter_text)
        
        try:
            delay = float(self.product_delay.get())
            port = int(self.product_port.get())
        except ValueError:
            messagebox.showerror("错误", "延迟和端口必须是数字！")
            return
        
        self.start_worker(
            mode='product',
            urls=urls,
            output_dir=self.product_output.get(),
            image_selection=image_selection,
            filter_words=filter_words,
            delay=delay,
            port=port
        )
    
    def start_section_scrape(self):
        urls_text = self.section_urls.get("0.0", "end").strip()
        if not urls_text:
            messagebox.showwarning("提示", "请输入 Section 链接！")
            return
        
        urls = [u.strip() for u in urls_text.split('\n') if u.strip()]
        
        for url in urls:
            if 'section_id=' not in url:
                messagebox.showerror("错误", f"无效链接:\n{url}")
                return
        
        image_selection = None
        img_text = self.section_images.get().strip()
        if img_text:
            try:
                image_selection = parse_image_selection(img_text)
            except ValueError as e:
                messagebox.showerror("错误", str(e))
                return
        
        filter_words = None
        filter_text = self.section_filter.get().strip()
        if filter_text:
            filter_words = parse_filter_words(filter_text)
        
        try:
            delay = float(self.section_delay.get())
            port = int(self.section_port.get())
        except ValueError:
            messagebox.showerror("错误", "延迟和端口必须是数字！")
            return
        
        self.start_worker(
            mode='section',
            urls=urls,
            output_dir=self.section_output.get(),
            image_selection=image_selection,
            filter_words=filter_words,
            delay=delay,
            resume=self.section_resume.get(),
            port=port
        )
    
    def start_worker(self, **kwargs):
        self.log_text.delete("0.0", "end")
        self.product_start_btn.configure(state="disabled")
        self.section_start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress_bar.set(0)
        self.progress_label.configure(text="启动中...")
        
        self.worker = ScraperWorker(self, **kwargs)
        self.worker.start()
    
    def on_stop(self):
        if self.worker:
            self.worker.stop()
            self.log("⚠️ 正在停止...")
    
    def on_finished(self, success: bool, message: str):
        self.product_start_btn.configure(state="normal")
        self.section_start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        
        if success:
            self.log(f"\n🎉 {message}")
            self.progress_label.configure(text="✅ 完成")
            messagebox.showinfo("完成", message)
        else:
            self.log(f"\n❌ {message}")
            self.progress_label.configure(text="❌ 失败")
            if "取消" not in message:
                messagebox.showerror("错误", message)


    def _load_saved_config(self):
        """启动时加载上次保存的配置"""
        config = load_config()
        
        # 恢复过滤词
        product_filter = config.get('product_filter', '')
        if product_filter:
            self.product_filter.insert(0, product_filter)
        
        section_filter = config.get('section_filter', '')
        if section_filter:
            self.section_filter.insert(0, section_filter)
    
    def _save_current_config(self):
        """保存当前配置"""
        config = load_config()
        
        # 保存过滤词
        config['product_filter'] = self.product_filter.get().strip()
        config['section_filter'] = self.section_filter.get().strip()
        
        save_config(config)
    
    def _on_close(self):
        """关闭窗口时保存配置"""
        self._save_current_config()
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
