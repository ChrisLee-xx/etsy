"""
Etsy Scraper GUI - CustomTkinter 桌面应用
"""
import json
import os
import random
import ssl
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Optional, List, Set

# 修复 macOS 上 Python 的 SSL 证书问题
ssl._create_default_https_context = ssl._create_unverified_context

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
        _DriverContext, _is_access_blocked, _is_browser_disconnected, get_random_ua,
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
        _DriverContext, _is_access_blocked, _is_browser_disconnected, get_random_ua,
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
    """后台抓取工作器（Selenium + undetected-chromedriver）"""
    
    def __init__(self, app, mode: str, urls: List[str], output_dir: str,
                 image_selection: Optional[List[int]] = None,
                 filter_words: Optional[List[str]] = None,
                 delay: float = 2.0,
                 resume: bool = True,
                 port: int = 9222,
                 use_tabs: bool = False):
        self.app = app
        self.mode = mode
        self.urls = urls
        self.output_dir = output_dir
        self.image_selection = image_selection
        self.filter_words = filter_words
        self.delay = delay
        self.resume = resume
        self.port = port
        self.use_tabs = use_tabs
        self.chrome_process = None
        self.driver = None
        self.ctx = None  # _DriverContext
        
        self._stop_flag = False
        self._pause_flag = threading.Event()
        self._pause_flag.set()  # 初始非暂停
        self._is_paused = False
        
        # 线程安全锁：保护 driver/chrome_process 的读写
        self._driver_lock = threading.Lock()
        
        # 手动介入相关（gui 线程读写，需同步）
        self._skip_blocked_prompt = False
        
        self._thread = None
    
    def log(self, msg: str):
        self.app.after(0, lambda: self.app.log(msg))
    
    def update_progress(self, current: int, total: int):
        self.app.after(0, lambda: self.app.update_progress(current, total))
    
    def _wait_if_paused(self):
        """在关键检查点等待暂停解除"""
        if not self._pause_flag.is_set():
            self._is_paused = True
            self.app.after(0, lambda: self.app._update_pause_ui(True))
            self._pause_flag.wait()
            self._is_paused = False
            self.app.after(0, lambda: self.app._update_pause_ui(False))
    
    def pause(self):
        """暂停抓取（不关闭浏览器）"""
        self._pause_flag.clear()
        self.log("⏸️ 已暂停（浏览器保持运行，可自由操作）")
    
    def resume_worker(self):
        """恢复抓取"""
        self._pause_flag.set()
        self.log("▶️ 已恢复抓取")
    
    def _safe_update_driver(self):
        """线程安全地同步 ctx.driver 到 self.driver（worker 线程调用）"""
        with self._driver_lock:
            if self.ctx:
                self.driver = self.ctx.driver
                self.chrome_process = self.ctx.chrome_process
    
    def stop(self):
        """停止抓取并关闭浏览器（不阻塞 GUI 线程）"""
        self._stop_flag = True
        self._pause_flag.set()  # 解除暂停以便线程退出
        
        with self._driver_lock:
            d = self.driver
            self.driver = None
            p = self.chrome_process
            self.chrome_process = None
        
        # 后台线程清理，避免 driver.quit() 阻塞 GUI
        def _cleanup():
            if d:
                try:
                    d.quit()
                except Exception:
                    pass
            if p and p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
        
        threading.Thread(target=_cleanup, daemon=True).start()
    
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
    
    def _run(self):
        try:
            if self.mode == 'product':
                self._run_product_mode()
            else:
                self._run_section_mode()
        except Exception as e:
            tb = traceback.format_exc()
            self.log(f"❌ {type(e).__name__}: {e}")
            self.log(f"{tb}")
            self.app.after(0, lambda e=e: self.app.on_finished(False, f"错误: {type(e).__name__}: {e}"))
        finally:
            if self.chrome_process and self.chrome_process.poll() is None:
                try:
                    self.chrome_process.terminate()
                except Exception:
                    pass
    
    # ========== 单商品模式 ==========
    
    def _run_product_mode(self):
        self.log("🚀 启动 Chrome 浏览器...")
        self.chrome_process = start_chrome_with_debug(self.urls[0], self.port)
        
        self.log("⏳ 等待浏览器就绪...")
        if not wait_for_chrome_ready(self.port):
            self.app.after(0, lambda: self.app.on_finished(False, "Chrome 启动失败！请先关闭所有 Chrome 窗口。"))
            return
        
        self.log("✅ Chrome 已启动！")
        self.driver = create_patched_driver(self.port)
        self.ctx = _DriverContext(self.driver, self.chrome_process, self.port)
        
        try:
            from selenium.webdriver.support.ui import WebDriverWait as _Wdw
            _Wdw(self.driver, 20).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            self.log("✅ 页面就绪")
        except Exception:
            time.sleep(3)
        
        self._scrape_products()
    
    def _scrape_products(self):
        total = len(self.urls)
        success_count = 0
        fail_count = 0
        consecutive_fails = 0
        output_path = Path(self.output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        for idx, url in enumerate(self.urls, 1):
            self._wait_if_paused()
            if self._stop_flag:
                break

            self.log(f"\n[{idx}/{total}] 处理商品...")
            self.update_progress(idx, total)

            if idx > 1:
                try:
                    self.ctx.driver.get(url)
                    time.sleep(2)
                except Exception as e:
                    if _is_browser_disconnected(e):
                        self.log("  🔌 Chrome 连接断开，尝试重启...")
                        if self.ctx.handle_block(url, immediate=True):
                            self._safe_update_driver()
                            consecutive_fails = 0
                        else:
                            self.log("  ❌ 无法恢复，停止")
                            break
                    else:
                        self.log(f"  ❌ 导航失败: {e}")
                        fail_count += 1
                        continue

            if _is_access_blocked(self.ctx.driver):
                self.log("🚫 检测到访问限制，自动恢复...")
                if self.ctx.handle_block(url):
                    self.log("✅ 已恢复")
                    consecutive_fails = 0
                else:
                    self.log("❌ 无法恢复，停止")
                    break

            try:
                result = extract_data_with_selenium(self.ctx.driver)
            except Exception as e:
                if _is_browser_disconnected(e):
                    self.log("  🔌 连接断开，立即重启后重试...")
                    if self.ctx.handle_block(url, immediate=True):
                        self._safe_update_driver()
                        try:
                            result = extract_data_with_selenium(self.ctx.driver)
                        except Exception as retry_error:
                            self.log(f"  ❌ 重试仍失败: {retry_error}")
                            result = None
                    else:
                        self.log("  ❌ 无法恢复，停止")
                        break
                else:
                    self.log(f"  ❌ 提取失败: {e}")
                    result = None

            if not result or not result.get('title'):
                self.log("  ❌ 抓取失败！")
                fail_count += 1
                consecutive_fails += 1
                if consecutive_fails >= 3:
                    if self.ctx.handle_block(url):
                        consecutive_fails = 0
                    else:
                        break
                continue

            success_count += 1
            consecutive_fails = 0
            self.log(f"  ✅ {result.get('title', '')[:40]}...")

            product_id = result.get('product_id', 'unknown')
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            json_path = output_path / f"product_{product_id}_{timestamp}.json"
            json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))

            self._download_images(result.get('images', []), result.get('title', ''), output_path)
            self._safe_update_driver()

            if idx < total:
                try:
                    from .real_chrome_scraper import human_delay
                except ImportError:
                    from real_chrome_scraper import human_delay
                human_delay(self.delay, 1.0)

        if not self._stop_flag:
            self.update_progress(total, total)
        if self._stop_flag:
            msg = f"已中断！部分完成 - 成功: {success_count}, 失败: {fail_count}"
            self.app.after(0, lambda m=msg: self.app.on_finished(success_count > 0, m))
        else:
            msg = f"完成！成功: {success_count}, 失败: {fail_count}"
            self.app.after(0, lambda m=msg: self.app.on_finished(True, m))
    
    # ========== Section 批量模式（Selenium） ==========
    
    def _run_section_mode(self):
        self.log("🚀 启动 Chrome 浏览器...")
        self.chrome_process = start_chrome_with_debug(self.urls[0], self.port)
        
        self.log("⏳ 等待浏览器就绪...")
        if not wait_for_chrome_ready(self.port):
            self.app.after(0, lambda: self.app.on_finished(False, "Chrome 启动失败！请先关闭所有 Chrome 窗口。"))
            return
        
        self.log("✅ Chrome 已启动！")
        self.driver = create_patched_driver(self.port)
        self.ctx = _DriverContext(self.driver, self.chrome_process, self.port)
        
        try:
            from selenium.webdriver.support.ui import WebDriverWait as _Wdw
            _Wdw(self.driver, 20).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            self.log("✅ 页面就绪")
        except Exception:
            self.log("⏳ 页面就绪检测超时（网络可能较慢），继续尝试...")
            time.sleep(3)
        
        self.log("🔍 开始提取 Section 信息...")
        self._scrape_sections()
    
    def _scrape_sections(self):
        total_sections = len(self.urls)
        total_success = 0
        total_fail = 0
        stopped_early = False
        
        for sec_idx, url in enumerate(self.urls, 1):
            self._wait_if_paused()
            if self._stop_flag:
                stopped_early = True
                break
            
            try:
                shop_name, section_id = parse_section_url(url)
            except ValueError:
                self.log(f"\n❌ 无效 URL: {url}")
                continue
            
            self.log(f"\n[Section {sec_idx}/{total_sections}] {shop_name}")
            
            # Section 间导航
            if sec_idx > 1:
                try:
                    self.ctx.driver.get(url)
                    time.sleep(2)
                    for _ in range(3):
                        scroll_distance = random.randint(300, 500)
                        self.ctx.driver.execute_script(f"window.scrollBy(0, {scroll_distance})")
                        time.sleep(random.uniform(0.8, 1.5))
                    self.ctx.driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.5)")
                    time.sleep(1)
                except Exception as e:
                    self.log(f"  ❌ 导航失败: {e}，跳过此 Section")
                    continue
            
            # 获取 Section 信息
            try:
                self.log(f"  🔍 正在解析 Section 信息 (section_id={section_id})...")
                section_name, total_items = get_section_info(self.ctx.driver, section_id)
            except Exception as e:
                self.log(f"  ❌ 获取 Section 信息失败: {e}，跳过此 Section")
                continue
            self.log(f"  ✓ Section: {section_name} ({total_items} 件商品)")
            
            # 创建输出目录
            dir_name = sanitize_folder_name(section_name) if section_name and section_name != "section" else f"{shop_name}_{section_id}"
            output_path = Path(self.output_dir) / dir_name
            
            if output_path.exists():
                progress_file = output_path / ".progress.json"
                if progress_file.exists():
                    try:
                        with open(progress_file, 'r', encoding='utf-8') as f:
                            existing = json.load(f)
                        if existing.get('section_id') != section_id:
                            output_path = Path(self.output_dir) / f"{dir_name}_{section_id}"
                    except Exception:
                        pass
            
            output_path.mkdir(parents=True, exist_ok=True)
            
            # 进度管理
            progress = ScrapeProgress(output_path, url, shop_name, section_id)
            completed_ids: Set[str] = set()
            if self.resume:
                try:
                    completed_ids = progress.load()
                    if completed_ids:
                        self.log(f"  📋 已完成: {len(completed_ids)} 个")
                except Exception:
                    pass
            
            # 提取商品链接
            try:
                self.log(f"  🔍 正在提取商品链接 (total={total_items})...")
                listing_ids = extract_product_links(self.ctx.driver, url, total_items=total_items,
                                                     stop_check=lambda: self._stop_flag)
            except Exception as e:
                self.log(f"  ❌ 提取商品链接失败: {e}，跳过此 Section")
                continue
            
            if not listing_ids:
                self.log("  ❌ 未找到商品（页面可能未完全加载或已被封锁）")
                continue
            
            self.log(f"  ✅ 找到 {len(listing_ids)} 个商品")
            progress.set_total_found(len(listing_ids))
            
            pending_ids = [lid for lid in listing_ids if lid not in completed_ids]
            skipped = len(completed_ids & set(listing_ids))
            if skipped:
                self.log(f"  📋 断点续传：跳过 {skipped} 个已完成")
            
            if not pending_ids:
                self.log("  ✅ 全部完成")
                continue
            
            name_tracker = ImageNameTracker()
            consecutive_fails = 0
            
            for i, listing_id in enumerate(pending_ids, 1):
                self._wait_if_paused()
                if self._stop_flag:
                    stopped_early = True
                    break
                
                self.update_progress(i, len(pending_ids))
                self.log(f"  [{i}/{len(pending_ids)}] 处理商品 {listing_id}...")
                
                if process_product(self.ctx, listing_id, output_path, name_tracker,
                                  image_selection=self.image_selection,
                                  filter_words=self.filter_words,
                                  section_url=url, log_cb=self.log,
                                  use_tabs=self.use_tabs):
                    total_success += 1
                    consecutive_fails = 0
                    progress.save(listing_id)
                    self.log(f"    ✅ [{i}/{len(pending_ids)}] 完成")
                else:
                    total_fail += 1
                    consecutive_fails += 1
                    self.log(f"    ❌ [{i}/{len(pending_ids)}] 失败")
                    
                    if consecutive_fails >= 3:
                        product_url = f"https://www.etsy.com/listing/{listing_id}"
                        self.log(f"\n⚠️ 连续 {consecutive_fails} 个失败，疑似被封锁…")
                        self._on_blocked_detected()
                        if self.ctx.handle_block(product_url, section_url=url):
                            self.log("✅ 已恢复，继续抓取")
                            consecutive_fails = 0
                        else:
                            self.log("❌ 无法恢复，停止当前 Section")
                            stopped_early = True
                            break
                
                self._safe_update_driver()
                
                if i < len(pending_ids):
                    try:
                        from .real_chrome_scraper import human_delay
                    except ImportError:
                        from real_chrome_scraper import human_delay
                    human_delay(self.delay, 1.0)
            
            if not self._stop_flag:
                self.update_progress(len(pending_ids), len(pending_ids))
        
        if stopped_early or self._stop_flag:
            if total_success > 0:
                msg = f"已中断！部分完成 - 成功: {total_success}, 失败: {total_fail}"
                self.app.after(0, lambda m=msg: self.app.on_finished(True, m))
            else:
                msg = f"已中断！成功: {total_success}, 失败: {total_fail}"
                self.app.after(0, lambda m=msg: self.app.on_finished(False, m))
        else:
            msg = f"完成！成功: {total_success}, 失败: {total_fail}"
            self.app.after(0, lambda m=msg: self.app.on_finished(True, m))
    
    def _on_blocked_detected(self):
        """检测到 Etsy 限流时的回调：通知 GUI 弹窗"""
        if self._skip_blocked_prompt or self._stop_flag:
            return
        self.log("  🚫 检测到访问限制，等待手动操作...")
        # 自动暂停让用户操作浏览器
        self._pause_flag.clear()
        self.app.after(0, self._show_blocked_prompt)
    
    def _show_blocked_prompt(self):
        """显示手动介入弹窗"""
        BlockedPopup(self)
    
    # ========== 图片下载（共用） ==========
    
    def _download_images(self, images: List[str], title: str, output_dir: Path):
        import requests
        if not images or not title:
            return

        try:
            from .utils import filter_title as _ft
        except ImportError:
            from utils import filter_title as _ft
        display_title = title
        if self.filter_words:
            display_title = _ft(title, self.filter_words)

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
            if self._stop_flag:
                return
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


class BlockedPopup(ctk.CTkToplevel):
    """Etsy 访问限制时的手动介入弹窗"""
    
    def __init__(self, worker: ScraperWorker):
        super().__init__()
        self.worker = worker
        self._start_time = time.time()
        
        self.title("Etsy 访问限制")
        self.geometry("450x280")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        
        # 内容
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=25, pady=20)
        
        ctk.CTkLabel(
            frame,
            text="🚫 检测到 Etsy 访问限制",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#dc3545",
        ).pack(anchor="w", pady=(0, 10))
        
        ctk.CTkLabel(
            frame,
            text="请手动操作浏览器 1-2 分钟：\n"
                 " • 随机浏览几个 Etsy 商品页面\n"
                 " • 正常滑动、点击即可\n"
                 " • 白屏消失后点击【继续抓取】",
            font=ctk.CTkFont(size=14),
            justify="left",
        ).pack(anchor="w", pady=(0, 10))
        
        self.timer_label = ctk.CTkLabel(
            frame,
            text="⏱️ 已等待 0 秒",
            font=ctk.CTkFont(size=13),
            text_color="gray",
        )
        self.timer_label.pack(anchor="w", pady=(0, 15))
        
        # 按钮
        btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
        btn_frame.pack(fill="x")
        
        ctk.CTkCheckBox(
            btn_frame,
            text="本轮不再提示（自动等待恢复）",
            font=ctk.CTkFont(size=13),
            command=self._on_skip_toggle,
        ).pack(side="left")
        
        ctk.CTkButton(
            btn_frame,
            text="继续抓取",
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#28a745",
            hover_color="#218838",
            width=100,
            height=35,
            command=self._on_resume,
        ).pack(side="right", padx=(10, 0))
        
        ctk.CTkButton(
            btn_frame,
            text="放弃",
            font=ctk.CTkFont(size=14),
            fg_color="#6c757d",
            hover_color="#5a6268",
            width=80,
            height=35,
            command=self._on_cancel,
        ).pack(side="right")
        
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self._update_timer()
    
    def _update_timer(self):
        if self.winfo_exists():
            elapsed = int(time.time() - self._start_time)
            self.timer_label.configure(text=f"⏱️ 已等待 {elapsed} 秒")
            self.after(1000, self._update_timer)
    
    def _on_skip_toggle(self):
        self.worker._skip_blocked_prompt = True
    
    def _on_resume(self):
        self.worker.resume_worker()
        self.destroy()
    
    def _on_cancel(self):
        self.worker._stop_flag = True
        self.worker._pause_flag.set()
        self.worker.log("⚠️ 用户放弃等待，停止抓取")
        self.destroy()


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
        
        # 右边：按钮组（暂停 | 继续 | 停止）
        self.stop_btn = ctk.CTkButton(
            control_frame,
            text="⏹️ 停止",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#dc3545",
            hover_color="#c82333",
            width=85,
            height=40,
            state="disabled",
            command=self.on_stop
        )
        self.stop_btn.pack(side="right", padx=(5, 0))
        
        self.resume_btn = ctk.CTkButton(
            control_frame,
            text="▶️ 继续",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#28a745",
            hover_color="#218838",
            width=85,
            height=40,
            state="disabled",
            command=self.on_resume
        )
        self.resume_btn.pack(side="right", padx=(5, 0))
        
        self.pause_btn = ctk.CTkButton(
            control_frame,
            text="⏸️ 暂停",
            font=ctk.CTkFont(size=13),
            fg_color="#ffc107",
            hover_color="#e0a800",
            text_color="#212529",
            width=85,
            height=40,
            state="disabled",
            command=self.on_pause
        )
        self.pause_btn.pack(side="right", padx=(5, 0))
        
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
        
        self.section_tabs = ctk.CTkCheckBox(
            row4,
            text="新标签页模式（会切换窗口，影响正常使用）",
            font=ctk.CTkFont(size=14)
        )
        self.section_tabs.pack(side="left", padx=(30, 0))
        
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
            port=port,
            use_tabs=self.section_tabs.get(),
        )
    
    def start_worker(self, **kwargs):
        if self.worker is not None:
            return
        
        self.log_text.delete("0.0", "end")
        self.product_start_btn.configure(state="disabled")
        self.section_start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.pause_btn.configure(state="normal")
        self.resume_btn.configure(state="disabled")
        self.progress_bar.set(0)
        self.progress_label.configure(text="启动中...")
        
        self.worker = ScraperWorker(self, **kwargs)
        self.worker.start()
    
    def on_pause(self):
        """暂停：不关闭浏览器，worker 线程在检查点阻塞"""
        if self.worker:
            self.worker.pause()
            self.pause_btn.configure(state="disabled")
            self.resume_btn.configure(state="normal")
            self.progress_label.configure(text="⏸️ 已暂停")
    
    def on_resume(self):
        """恢复：解除暂停，继续抓取"""
        if self.worker:
            self.worker.resume_worker()
            self.pause_btn.configure(state="normal")
            self.resume_btn.configure(state="disabled")
            self.progress_label.configure(text="▶️ 运行中")
    
    def _update_pause_ui(self, paused: bool):
        """worker 内部暂停/恢复时更新按钮状态"""
        if paused:
            self.pause_btn.configure(state="disabled")
            self.resume_btn.configure(state="normal")
            self.progress_label.configure(text="⏸️ 已暂停")
        else:
            self.pause_btn.configure(state="normal")
            self.resume_btn.configure(state="disabled")
            self.progress_label.configure(text="▶️ 运行中")
    
    def on_stop(self):
        if self.worker:
            self.worker.stop()
            self.log("⚠️ 正在停止...")
        
        self.pause_btn.configure(state="disabled")
        self.resume_btn.configure(state="disabled")
        
        def force_finish():
            if self.worker is None:
                return
            if self.worker._thread.is_alive():
                self.log("⏱️ 强制结束抓取...")
            self.on_finished(False, "已手动停止")
        
        self.after(5000, force_finish)
    
    def on_finished(self, success: bool, message: str):
        self.product_start_btn.configure(state="normal")
        self.section_start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.pause_btn.configure(state="disabled")
        self.resume_btn.configure(state="disabled")
        self.worker = None
        
        if success:
            self.log(f"\n🎉 {message}")
            self.progress_label.configure(text="✅ 完成")
            messagebox.showinfo("完成", message)
        else:
            self.log(f"\n❌ {message}")
            self.progress_label.configure(text="❌ 失败")
            if "取消" not in message and "手动停止" not in message:
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
        """关闭窗口时停止 worker 并保存配置"""
        if self.worker:
            self.worker.stop()
            if self.worker._thread and self.worker._thread.is_alive():
                self.worker._thread.join(timeout=2.0)
        self._save_current_config()
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
