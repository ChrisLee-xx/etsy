"""
Etsy Scraper GUI - CustomTkinter 桌面应用
"""
import json
import os
import random
import re
import ssl
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Optional, List, Set

# 安全导入 multiprocessing（Windows PyInstaller 环境可能缺失 _multiprocessing）
# 仅用于 freeze_support()，实际抓取使用线程
try:
    import multiprocessing
    multiprocessing.freeze_support()
    _has_multiprocessing = True
except ImportError:
    _has_multiprocessing = False

# Windows 专用模块安全导入
# 这些模块在 macOS/Linux 上不存在，但 PyInstaller 在 Windows 上可能需要它们
if sys.platform == 'win32':
    for _mod_name in ['_overlapped', '_socket', '_ssl', '_multiprocessing', '_ctypes']:
        try:
            __import__(_mod_name)
        except ImportError:
            pass

# 修复跨平台 SSL 证书问题
try:
    import certifi
    _ctx = ssl.create_default_context()
    _ctx.load_verify_locations(certifi.where())
    # 验证证书文件是否真实可访问
    ssl._create_default_https_context = lambda: _ctx
except Exception:
    # certifi 不可用时回退到不验证（macOS/Windows 均可正常工作）
    ssl._create_default_https_context = ssl._create_unverified_context

# PyInstaller 打包后，添加 _MEIPASS 到 sys.path
if getattr(sys, 'frozen', False):
    # Running as compiled
    bundle_dir = sys._MEIPASS
    if bundle_dir not in sys.path:
        sys.path.insert(0, bundle_dir)

import customtkinter as ctk

# 导入核心功能 - 兼容 PyInstaller 打包
_scrape_imported = False

# 尝试1: 作为 etsy_scraper 包导入（开发环境）
try:
    from etsy_scraper.section_scraper import (
        ScrapeProgress, parse_section_url, get_section_info,
        extract_product_links, process_product, ImageNameTracker,
        sanitize_folder_name,
    )
    from etsy_scraper.real_chrome_scraper import (
        extract_data_with_selenium, download_images, sanitize_filename,
        create_patched_driver, start_chrome_with_debug, wait_for_chrome_ready,
        _DriverContext, _is_access_blocked, _is_browser_disconnected, get_random_ua,
    )
    from etsy_scraper.utils import parse_image_selection, parse_filter_words
    _scrape_imported = True
except ImportError:
    pass

# 尝试2: 直接导入（PyInstaller 打包环境）
if not _scrape_imported:
    try:
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
        _scrape_imported = True
    except ImportError:
        pass

if not _scrape_imported:
    raise RuntimeError("无法导入核心模块！请检查 Python 路径配置")


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
    # 尝试获取桌面路径，支持多语言系统
    desktop = None
    
    # 尝试常见的桌面路径
    desktop_candidates = [
        Path.home() / "Desktop",           # English
        Path.home() / "桌面",               # Chinese (Simplified)
        Path.home() / "桌面",               # Chinese (Traditional) - same
        Path.home() / "OneDrive" / "Desktop",  # OneDrive backup
    ]
    
    for candidate in desktop_candidates:
        if candidate.exists():
            desktop = candidate
            break
    
    # 如果桌面路径都不存在，使用用户主目录
    if desktop is None:
        desktop = Path.home()
    
    today = datetime.now().strftime("%Y%m%d")
    target_name = f"EtsyScraper_{today}"
    target_path = desktop / target_name
    
    # 如果今天的文件夹已存在，直接复用
    if target_path.exists():
        return str(target_path)
    
    # 查找桌面上最近的 EtsyScraper_ 文件夹，如果存在则复用
    if desktop.exists():
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
                 port: int = 9222,
                 max_count: int = -1):
        self.app = app
        self.mode = mode
        self.urls = urls
        self.output_dir = output_dir
        self.image_selection = image_selection
        self.filter_words = filter_words
        self.delay = delay
        self.resume = resume
        self.port = port
        self.max_count = max_count
        self.chrome_process = None
        self.driver = None
        self._stop_flag = False

        self._thread = None
        self._start_time = time.time()  # 用于检测卡死的 worker
    
    def log(self, msg: str):
        self.app.after(0, lambda: self.app.log(msg))
    
    def update_progress(self, current: int, total: int):
        self.app.after(0, lambda: self.app.update_progress(current, total))
    
    def stop(self):
        self._stop_flag = True
        # 只设置停止标志，driver/进程清理由后台线程在 finally 中自行完成
        # 避免与后台线程竞态操作 driver 导致 InvalidSessionId 等异常

    def _force_cleanup(self):
        """强制清理 driver 和 Chrome 进程（超时保护时调用）"""
        driver = getattr(self, 'driver', None) or getattr(getattr(self, 'ctx', None), 'driver', None)
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        ctx_proc = getattr(getattr(self, 'ctx', None), 'chrome_process', None)
        self._safe_terminate(ctx_proc)
        self._safe_terminate(self.chrome_process)
    
    
    def _safe_terminate(self, proc):
        """安全终止进程，仅当进程存活时才调用 terminate"""
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
    
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
            
            self.log("🔗 连接 Chrome 驱动...")
            self.driver = create_patched_driver(self.port)
            self.ctx = _DriverContext(self.driver, self.chrome_process, self.port)
            self.log("✅ 驱动已连接")
            
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
            tb = traceback.format_exc()
            error_msg = f"{type(e).__name__}: {e}"
            self.log(f"❌ {tb}")
            self.app.after(0, lambda m=error_msg: self.app.on_finished(False, f"错误: {m}"))
        finally:
            # 清理 driver（quit 会同时关闭 chromedriver 进程）
            driver = getattr(self, 'driver', None) or getattr(getattr(self, 'ctx', None), 'driver', None)
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
            # 清理 Chrome 进程
            ctx_proc = getattr(getattr(self, 'ctx', None), 'chrome_process', None)
            self._safe_terminate(ctx_proc)
            self._safe_terminate(self.chrome_process)
    
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

            # 所有商品都显式导航（包括第一个），确保 Selenium 接管页面加载
            # 不能依赖 Chrome 启动时自动加载的 URL，否则 page_source 等操作可能无限阻塞
            try:
                self.log("  → 导航到商品页...")
                self.ctx.driver.get(url)
                time.sleep(2)
            except Exception as e:
                if _is_browser_disconnected(e):
                    self.log(f"  🔌 Chrome 连接断开，立即重启后重试...")
                    if self.ctx.handle_block(url, immediate=True):
                        self.driver = self.ctx.driver
                        self.chrome_process = self.ctx.chrome_process
                        consecutive_fails = 0
                        # 恢复后重新导航到目标商品页
                        try:
                            self.ctx.driver.get(url)
                            time.sleep(2)
                        except Exception:
                            pass
                    else:
                        self.log("  ❌ 无法恢复，停止")
                        break
                else:
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

            try:
                result = extract_data_with_selenium(self.ctx.driver)
            except Exception as e:
                if _is_browser_disconnected(e):
                    self.log("  🔌 Chrome 连接断开，立即重启后重试当前商品...")
                    if self.ctx.handle_block(url, immediate=True):
                        self.driver = self.ctx.driver
                        self.chrome_process = self.ctx.chrome_process
                        # 恢复后重新导航到目标商品页再提取
                        try:
                            self.ctx.driver.get(url)
                            time.sleep(2)
                        except Exception:
                            pass
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

        if not self._stop_flag:
            self.update_progress(total, total)
        if self._stop_flag:
            if success_count > 0:
                self.app.after(0, lambda: self.app.on_finished(True,
                    f"已中断！部分完成 - 成功: {success_count}, 失败: {fail_count}"))
            else:
                self.app.after(0, lambda: self.app.on_finished(False,
                    f"已中断！成功: {success_count}, 失败: {fail_count}"))
        else:
            self.app.after(0, lambda: self.app.on_finished(True, f"完成！成功: {success_count}, 失败: {fail_count}"))
    
    def _scrape_sections(self):
        total_sections = len(self.urls)
        total_success = 0
        total_fail = 0
        stopped_early = False
        
        try:
            from etsy_scraper.section_scraper import (
                is_shop_url, parse_section_url, parse_shop_url, get_shop_info,
                is_search_url, parse_search_url
            )
        except ImportError:
            from section_scraper import (  # type: ignore
                is_shop_url, parse_section_url, parse_shop_url, get_shop_info,
                is_search_url, parse_search_url
            )
        
        for sec_idx, url in enumerate(self.urls, 1):
            if self._stop_flag:
                stopped_early = True
                break
            
            # 区分搜索链接、店铺链接和 Section 链接
            is_search = is_search_url(url)
            is_shop = is_shop_url(url) if not is_search else False
            
            try:
                if is_search:
                    search_query = parse_search_url(url)
                    shop_name = search_query
                    section_id = "search"
                elif is_shop:
                    shop_name = parse_shop_url(url)
                    section_id = "ALL"  # 店铺全品类用特殊标识
                else:
                    shop_name, section_id = parse_section_url(url)
            except ValueError:
                self.log(f"\n❌ 无效 URL: {url}")
                continue
            
            label = f"搜索" if is_search else (f"店铺" if is_shop else f"Section")
            self.log(f"\n[{label} {sec_idx}/{total_sections}] {shop_name}")
            
            if sec_idx > 1:
                try:
                    self.ctx.driver.get(url)
                    time.sleep(2)
                except Exception as e:
                    self.log(f"  ❌ 导航失败: {e}，尝试重启 Chrome...")
                    if self.ctx.handle_block(url, section_url=url):
                        self.driver = self.ctx.driver
                        self.chrome_process = self.ctx.chrome_process
                        self.log("  ✅ 已恢复")
                    else:
                        self.log(f"  ❌ 无法恢复，跳过此 {label}")
                        continue
            
            # 获取信息（在创建输出目录之前），Chrome 断连时自动恢复
            # 搜索页跳过信息获取（总数不可靠，用 max_count 控制抓取量）
            display_name = shop_name
            total_items = 0
            if not is_search:
                try:
                    if is_shop:
                        display_name, total_items = get_shop_info(self.ctx.driver, shop_name)
                    else:
                        try:
                            from etsy_scraper.section_scraper import get_section_info
                        except ImportError:
                            from section_scraper import get_section_info  # type: ignore
                        display_name, total_items = get_section_info(self.ctx.driver, section_id)
                except Exception as e:
                    self.log(f"  ❌ 获取{label}信息失败: {e}，尝试重启 Chrome...")
                    if self.ctx.handle_block(url, section_url=url, immediate=_is_browser_disconnected(e)):
                        self.driver = self.ctx.driver
                        self.chrome_process = self.ctx.chrome_process
                        self.log("  ✅ 已恢复，重试...")
                        try:
                            if is_shop:
                                display_name, total_items = get_shop_info(self.ctx.driver, shop_name)
                            else:
                                display_name, total_items = get_section_info(self.ctx.driver, section_id)
                        except Exception as retry_error:
                            self.log(f"  ❌ 重试仍失败: {retry_error}，跳过此 {label}")
                            continue
                    else:
                        self.log(f"  ❌ 无法恢复，跳过此 {label}")
                        continue
            else:
                # 搜索页显示 max_count 信息
                if self.max_count > 0:
                    self.log(f"  🔍 搜索: {search_query}（最多抓取 {self.max_count} 个）")
                else:
                    self.log(f"  🔍 搜索: {search_query}（无限抓取）")
            
            if not is_search:
                info_label = f"{label}: {display_name}" if not is_shop or display_name != shop_name else f"店铺: {shop_name}"
                self.log(f"  {info_label} ({total_items} 件)")
            
            # 命名文件夹
            if is_search:
                dir_name = sanitize_folder_name(search_query) or "search_results"
            elif is_shop:
                dir_name = sanitize_folder_name(shop_name) + "_ALL"
            elif display_name and display_name != "section":
                dir_name = sanitize_folder_name(display_name)
            else:
                dir_name = f"{shop_name}_{section_id}"
            
            # 同名文件夹冲突检测
            candidate_path = Path(self.output_dir) / dir_name
            if candidate_path.exists():
                progress_file = candidate_path / ".progress.json"
                if progress_file.exists():
                    try:
                        with open(progress_file, 'r', encoding='utf-8') as f:
                            existing_progress = json.load(f)
                        if existing_progress.get('section_id') != section_id:
                            dir_name = f"{dir_name}_{section_id}"
                    except Exception:
                        pass
            
            output_path = Path(self.output_dir) / dir_name
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
            
            if is_search:
                # 搜索页：逐页抓取处理（抓一页处理一页，不预先收集所有 ID）
                # 这样即使中途被封锁，已处理页的图片已经保存
                try:
                    from etsy_scraper.section_scraper import build_page_url, scroll_page
                except ImportError:
                    from section_scraper import build_page_url, scroll_page  # type: ignore
                from selenium.webdriver.common.by import By
                
                name_tracker = ImageNameTracker()
                consecutive_fails = 0
                current_page = 1
                seen_ids = set(completed_ids)
                total_processed = len(completed_ids)
                
                if self.max_count > 0:
                    progress.set_total_found(self.max_count)
                    self.update_progress(total_processed, self.max_count)
                
                while not self._stop_flag:
                    # 构造当前页 URL 并导航
                    page_url = build_page_url(url, current_page)
                    self.log(f"\n  📄 第 {current_page} 页...")
                    
                    try:
                        self.ctx.driver.get(page_url)
                        time.sleep(3)
                        scroll_page(self.ctx.driver)
                    except Exception as e:
                        if _is_browser_disconnected(e):
                            self.log(f"  🔌 Chrome 断连，重启后重试当前页...")
                            if self.ctx.handle_block(page_url, section_url=url, immediate=True):
                                self.driver = self.ctx.driver
                                self.chrome_process = self.ctx.chrome_process
                                continue
                            else:
                                self.log("  ❌ 无法恢复，停止")
                                stopped_early = True
                                break
                        else:
                            self.log(f"  ❌ 页面加载失败: {e}")
                            break
                    
                    # 提取本页 listing_id
                    try:
                        product_cards = self.ctx.driver.find_elements(
                            By.CSS_SELECTOR, 'div.v2-listing-card[data-listing-id]'
                        )
                        page_ids = []
                        for card in product_cards:
                            lid = card.get_attribute('data-listing-id')
                            if lid and lid not in seen_ids:
                                seen_ids.add(lid)
                                page_ids.append(lid)
                    except Exception as e:
                        if _is_browser_disconnected(e):
                            self.log(f"  🔌 Chrome 断连，重启后重试当前页...")
                            if self.ctx.handle_block(page_url, section_url=url, immediate=True):
                                self.driver = self.ctx.driver
                                self.chrome_process = self.ctx.chrome_process
                                continue
                            else:
                                self.log("  ❌ 无法恢复，停止")
                                stopped_early = True
                                break
                        else:
                            self.log(f"  ❌ 提取商品失败: {e}")
                            break
                    
                    self.log(f"  ✓ 本页 {len(page_ids)} 个新商品（累计 {total_processed}）")
                    
                    if not page_ids:
                        self.log("  → 无更多商品，停止")
                        break
                    
                    # 立即处理本页商品（下载图片）
                    for listing_id in page_ids:
                        if self._stop_flag:
                            break
                        
                        if self.max_count > 0 and total_processed >= self.max_count:
                            self.log(f"  → 已达到最大抓取数 {self.max_count}")
                            break
                        
                        if progress.is_completed(listing_id):
                            continue
                        
                        self.log(f"  [{total_processed + 1}] 处理商品 {listing_id}...")
                        
                        if process_product(self.ctx, listing_id, output_path, name_tracker,
                                          image_selection=self.image_selection,
                                          filter_words=self.filter_words,
                                          section_url=url):
                            total_success += 1
                            consecutive_fails = 0
                            progress.save(listing_id)
                            self.log(f"    ✅ 完成")
                        else:
                            total_fail += 1
                            consecutive_fails += 1
                            self.log(f"    ❌ 失败")
                            
                            if consecutive_fails >= 3:
                                product_url = f"https://www.etsy.com/listing/{listing_id}"
                                self.log(f"\n⚠️ 连续 {consecutive_fails} 个失败，疑似被封锁，自动重启 Chrome...")
                                if self.ctx.handle_block(product_url, section_url=url):
                                    self.log("✅ 已恢复，继续抓取")
                                    consecutive_fails = 0
                                else:
                                    self.log("❌ 无法恢复，停止")
                                    stopped_early = True
                                    break
                        
                        total_processed += 1
                        self.driver = self.ctx.driver
                        self.chrome_process = self.ctx.chrome_process
                        
                        if self.max_count > 0:
                            self.update_progress(total_processed, self.max_count)
                        
                        time.sleep(max(1.0, self.delay + random.uniform(-0.5, 1.0)))
                    
                    # 检查是否达到 max_count
                    if self.max_count > 0 and total_processed >= self.max_count:
                        break
                    
                    current_page += 1
                    time.sleep(2)
                
            else:
                # shop/section：保持原有的"先收集再处理"模式
                try:
                    listing_ids = extract_product_links(self.ctx.driver, url, total_items=total_items,
                                                         stop_check=lambda: self._stop_flag)
                except Exception as e:
                    self.log(f"  ❌ 提取商品链接失败: {e}，尝试重启 Chrome...")
                    if self.ctx.handle_block(url, section_url=url, immediate=_is_browser_disconnected(e)):
                        self.driver = self.ctx.driver
                        self.chrome_process = self.ctx.chrome_process
                        self.log("  ✅ 已恢复，重试...")
                        try:
                            listing_ids = extract_product_links(self.ctx.driver, url, total_items=total_items,
                                                                 stop_check=lambda: self._stop_flag)
                        except Exception as retry_error:
                            self.log(f"  ❌ 重试仍失败: {retry_error}，跳过此 {label}")
                            continue
                    else:
                        self.log(f"  ❌ 无法恢复，跳过此 {label}")
                        continue
                
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
                    self.log(f"  [{i}/{len(pending_ids)}] 处理商品 {listing_id}...")
                    
                    if process_product(self.ctx, listing_id, output_path, name_tracker,
                                      image_selection=self.image_selection,
                                      filter_words=self.filter_words,
                                      section_url=url):
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
                            self.log(f"\n⚠️ 连续 {consecutive_fails} 个失败，疑似被封锁，自动重启 Chrome...")
                            if self.ctx.handle_block(product_url, section_url=url):
                                self.log("✅ 已恢复，继续抓取")
                                consecutive_fails = 0
                            else:
                                self.log("❌ 无法恢复，停止当前 Section")
                                stopped_early = True
                                break

                    # driver 可能在封锁恢复后被替换，同步引用
                    self.driver = self.ctx.driver
                    self.chrome_process = self.ctx.chrome_process
                    
                    if i < len(pending_ids):
                        time.sleep(max(1.0, self.delay + random.uniform(-0.5, 1.0)))
                
                if not self._stop_flag:
                    self.update_progress(len(pending_ids), len(pending_ids))
        
        # 区分正常完成和提前退出（stop 或连续失败）
        if stopped_early or self._stop_flag:
            if total_success > 0:
                self.app.after(0, lambda: self.app.on_finished(True,
                    f"已中断！部分完成 - 成功: {total_success}, 失败: {total_fail}"))
            else:
                self.app.after(0, lambda: self.app.on_finished(False,
                    f"已中断！成功: {total_success}, 失败: {total_fail}"))
        else:
            self.app.after(0, lambda: self.app.on_finished(True, f"完成！成功: {total_success}, 失败: {total_fail}"))
    
    def _download_images(self, images: List[str], title: str, output_dir: Path):
        import requests

        if not images or not title:
            return

        try:
            from etsy_scraper.utils import filter_title
        except ImportError:
            from utils import filter_title  # type: ignore
        display_title = title
        if self.filter_words:
            display_title = filter_title(title, self.filter_words)

        safe_title = sanitize_filename(display_title)

        if self.image_selection:
            download_list = [(i, images[i-1]) for i in self.image_selection if 1 <= i <= len(images)]
            if not download_list:
                self.log(f"    ⚠️ 选择的图片序号超出范围，默认下载第1张")
                download_list = [(1, images[0])] if images else []
        else:
            download_list = [(i+1, url) for i, url in enumerate(images)]

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": "https://www.etsy.com/",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Dest": "image",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
        }

        for idx, url in download_list:
            if self._stop_flag:
                return
            try:
                ext = url.split('.')[-1].split('?')[0] or 'jpg'
                if ext not in ('jpg', 'jpeg', 'png', 'gif', 'webp'):
                    ext = 'jpg'
                filename = f"{safe_title}-{idx}.{ext}"
                filepath = output_dir / filename

                resp = requests.get(url, headers=headers, timeout=30)
                try:
                    from etsy_scraper.utils import validate_image_response, save_failed_image
                except ImportError:
                    from utils import validate_image_response, save_failed_image  # type: ignore
                
                valid, reason = validate_image_response(resp)
                if valid:
                    filepath.write_bytes(resp.content)
                    self.log(f"    📥 图片 {idx} ({len(resp.content)//1024}KB)")
                else:
                    save_failed_image(output_dir, title, url, idx, reason)
                    self.log(f"    ❌ 图片 {idx} 下载失败 ({reason})，已保存链接待二次抓取")
            except Exception as e:
                try:
                    from etsy_scraper.utils import save_failed_image
                except ImportError:
                    from utils import save_failed_image  # type: ignore
                save_failed_image(output_dir, title, url, idx, str(e))
                self.log(f"    ❌ 图片 {idx} 下载失败: {e}，已保存链接待二次抓取")

            time.sleep(random.uniform(0.3, 0.8))


class RetryFailedWorker:
    """重试失败图片的工作器，从 failed_images.json 读取并重新下载"""

    def __init__(self, app, output_dir: str, failed_list: list):
        self.app = app
        self.output_dir = Path(output_dir)
        self.failed_list = failed_list
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
        import requests as _req

        total = len(self.failed_list)
        success_count = 0
        remaining_records = []

        self.log(f"🔄 开始重试 {total} 张失败图片...\n")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": "https://www.etsy.com/",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Dest": "image",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
        }

        for i, record in enumerate(self.failed_list, 1):
            if self._stop_flag:
                # 停止时剩余记录全部保留
                remaining_records.extend(self.failed_list[i-1:])
                break

            self.update_progress(i, total)
            title = record.get('title', 'unnamed')
            url = record.get('image_url', '')
            idx = record.get('image_index', i)

            if not url:
                remaining_records.append(record)
                continue

            safe_title = sanitize_filename(title)
            ext = url.split('.')[-1].split('?')[0] or 'jpg'
            if ext not in ('jpg', 'jpeg', 'png', 'gif', 'webp'):
                ext = 'jpg'
            filename = f"{safe_title}-{idx}.{ext}"
            filepath = self.output_dir / filename

            try:
                resp = _req.get(url, headers=headers, timeout=30)
                try:
                    from etsy_scraper.utils import validate_image_response
                except ImportError:
                    from utils import validate_image_response  # type: ignore

                valid, reason = validate_image_response(resp)
                if valid:
                    filepath.write_bytes(resp.content)
                    success_count += 1
                    self.log(f"  ✅ [{i}/{total}] {filename} ({len(resp.content)//1024}KB)")
                else:
                    # 仍然失败，保留记录
                    record['reason'] = reason
                    record['retry_at'] = datetime.now().isoformat()
                    remaining_records.append(record)
                    self.log(f"  ❌ [{i}/{total}] {filename} ({reason})")
            except Exception as e:
                record['reason'] = str(e)
                record['retry_at'] = datetime.now().isoformat()
                remaining_records.append(record)
                self.log(f"  ❌ [{i}/{total}] {filename} ({e})")

            time.sleep(random.uniform(0.3, 0.8))

        # 更新 failed_images.json：只保留仍然失败的记录
        failed_file = self.output_dir / "failed_images.json"
        try:
            if remaining_records:
                with open(failed_file, 'w', encoding='utf-8') as f:
                    json.dump(remaining_records, f, ensure_ascii=False, indent=2)
            else:
                # 全部成功，删除文件
                failed_file.unlink(missing_ok=True)
        except Exception:
            pass

        if not self._stop_flag:
            self.update_progress(total, total)

        remaining = len(remaining_records)
        self.app.after(0, lambda: self.app.on_retry_finished(success_count, total, remaining))


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
        self.tabview = ctk.CTkTabview(self.main_frame, height=340)
        self.tabview.pack(fill="x", pady=(0, 15))
        
        self.tabview.add("📂 批量抓取")
        self.tabview.add("📦 单商品抓取")
        
        self.setup_section_tab(self.tabview.tab("📂 批量抓取"))
        self.setup_product_tab(self.tabview.tab("📦 单商品抓取"))
        
        self.tabview.set("📂 批量抓取")
        
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
        self.retry_failed_btn = ctk.CTkButton(
            control_frame,
            text="🔄 重试失败图片",
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#fd7e14",
            hover_color="#e8630a",
            width=140,
            height=40,
            command=self.on_retry_failed
        )
        self.retry_failed_btn.pack(side="right", padx=(0, 8))

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
            text="店铺/Section/搜索 链接（多个链接换行分隔）：",
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
        
        # 第四行：延迟、抓取数量、端口、断点续传
        row4 = ctk.CTkFrame(options_frame, fg_color="transparent")
        row4.pack(fill="x", pady=8)
        
        ctk.CTkLabel(row4, text="延迟(秒)：", font=ctk.CTkFont(size=14), width=100).pack(side="left")
        self.section_delay = ctk.CTkEntry(row4, font=ctk.CTkFont(size=14), height=40, width=80)
        self.section_delay.pack(side="left")
        self.section_delay.insert(0, "2.0")
        
        ctk.CTkLabel(row4, text="最大抓取数：", font=ctk.CTkFont(size=14), width=110).pack(side="left", padx=(30, 0))
        self.section_max_count = ctk.CTkEntry(
            row4, font=ctk.CTkFont(size=14), height=40, width=80,
            placeholder_text="-1 无限"
        )
        self.section_max_count.pack(side="left")
        self.section_max_count.insert(0, "-1")
        
        ctk.CTkLabel(row4, text="端口：", font=ctk.CTkFont(size=14), width=60).pack(side="left", padx=(30, 0))
        self.section_port = ctk.CTkEntry(row4, font=ctk.CTkFont(size=14), height=40, width=80)
        self.section_port.pack(side="left")
        self.section_port.insert(0, "9222")
        
        self.section_resume = ctk.CTkCheckBox(
            row4,
            text="断点续传",
            font=ctk.CTkFont(size=14)
        )
        self.section_resume.pack(side="left", padx=(20, 0))
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
        
        # 清理 URL：去除每行首尾空白，合并被换行截断的 URL
        # 策略：以 https:// 或 http:// 开头作为新 URL 的起始，后续不以 http 开头的行视为上一行的延续
        raw_lines = [line.strip() for line in urls_text.split('\n') if line.strip()]
        urls = []
        for line in raw_lines:
            if line.startswith('http://') or line.startswith('https://'):
                urls.append(line)
            elif urls:
                # 当前行不以 http 开头，拼接到上一个 URL（被换行截断的情况）
                urls[-1] += line
            else:
                urls.append(line)
        
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
            messagebox.showwarning("提示", "请输入 Section、店铺或搜索链接！")
            return
        
        # 清理 URL：去除每行首尾空白，合并被换行截断的 URL
        raw_lines = [line.strip() for line in urls_text.split('\n') if line.strip()]
        urls = []
        for line in raw_lines:
            if line.startswith('http://') or line.startswith('https://'):
                urls.append(line)
            elif urls:
                urls[-1] += line
            else:
                urls.append(line)
        
        # 验证：支持 Section 链接（含 section_id）、店铺链接（/shop/xxx）、搜索链接（/search）
        try:
            from etsy_scraper.section_scraper import is_shop_url, parse_section_url, parse_shop_url, is_search_url
        except ImportError:
            from section_scraper import is_shop_url, parse_section_url, parse_shop_url, is_search_url  # type: ignore
        for url in urls:
            if 'etsy.com' not in url:
                messagebox.showerror("错误", f"无效链接（非 Etsy）:\n{url}")
                return
            if '/shop/' not in url and 'section_id=' not in url and '/search' not in url:
                messagebox.showerror("错误", f"无效链接:\n{url}\n\n支持格式:\n- Section: .../shop/xxx?section_id=yyy\n- 店铺:   .../shop/xxx\n- 搜索:   .../search?q=关键词")
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
            max_count = int(self.section_max_count.get())
        except ValueError:
            messagebox.showerror("错误", "延迟、端口和抓取数必须是数字！")
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
            max_count=max_count
        )
    
    def start_worker(self, **kwargs):
        # 如果已有运行中的 worker，检查它是否真的还在运行
        if self.worker is not None:
            # 检查 worker 线程是否还活着
            thread = getattr(self.worker, '_thread', None)
            start_time = getattr(self.worker, '_start_time', 0)
            elapsed = time.time() - start_time if start_time else 0

            if thread is not None and thread.is_alive() and elapsed < 600:
                # 线程确实在运行且未超时，忽略本次请求
                return
            # 线程已死，或存活超过 10 分钟（卡死），强制清理后继续
            if elapsed >= 600:
                self.log("⚠️ 上次抓取疑似卡死，强制清理...")
            self.worker = None
        
        self.log_text.delete("0.0", "end")
        self.product_start_btn.configure(state="disabled")
        self.section_start_btn.configure(state="disabled")
        self.retry_failed_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress_bar.set(0)
        self.progress_label.configure(text="启动中...")
        
        self.worker = ScraperWorker(self, **kwargs)
        self.worker.start()
    
    def on_stop(self):
        if self.worker:
            self.worker.stop()
            self.log("⚠️ 正在停止...")
        
        # 5秒超时保护：如果线程未及时退出，强制清理并恢复 UI
        def force_finish():
            if self.worker is None:  # 已被正常的 on_finished 清理过
                return
            if self.worker._thread.is_alive():
                self.log("⏱️ 强制结束抓取...")
                # 强制清理 driver 和 Chrome 进程（仅 ScraperWorker 有此方法）
                if hasattr(self.worker, '_force_cleanup'):
                    self.worker._force_cleanup()
            self.on_finished(False, "已手动停止")
        
        self.after(5000, force_finish)
    
    def on_finished(self, success: bool, message: str):
        self.product_start_btn.configure(state="normal")
        self.section_start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.retry_failed_btn.configure(state="normal")
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

    def on_retry_failed(self):
        """重试 failed_images.json 中的失败图片"""
        if self.worker is not None:
            messagebox.showwarning("提示", "当前有任务正在运行，请等待完成后再重试。")
            return

        # 确定输出目录：使用当前选中 tab 的输出目录
        current_tab = self.tabview.get()
        if "批量" in current_tab or "Section" in current_tab:
            output_dir = self.section_output.get().strip()
        else:
            output_dir = self.product_output.get().strip()

        if not output_dir:
            messagebox.showwarning("提示", "请先设置输出目录。")
            return

        failed_file = Path(output_dir) / "failed_images.json"
        if not failed_file.exists():
            messagebox.showinfo("提示", f"未找到失败记录文件：\n{failed_file}")
            return

        try:
            with open(failed_file, 'r', encoding='utf-8') as f:
                failed_list = json.load(f)
        except Exception as e:
            messagebox.showerror("错误", f"读取失败记录失败: {e}")
            return

        if not failed_list:
            messagebox.showinfo("提示", "没有需要重试的失败图片。")
            return

        # 启动重试线程
        self.log_text.delete("0.0", "end")
        self.product_start_btn.configure(state="disabled")
        self.section_start_btn.configure(state="disabled")
        self.retry_failed_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress_bar.set(0)
        self.progress_label.configure(text="重试中...")

        self.worker = RetryFailedWorker(
            self, output_dir, failed_list
        )
        self.worker.start()

    def on_retry_finished(self, success_count: int, total: int, remaining: int):
        """重试完成回调"""
        self.product_start_btn.configure(state="normal")
        self.section_start_btn.configure(state="normal")
        self.retry_failed_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.worker = None

        msg = f"重试完成！成功: {success_count}/{total}，剩余失败: {remaining}"
        self.log(f"\n{'🎉' if remaining == 0 else '⚠️'} {msg}")
        self.progress_label.configure(text="✅ 完成" if remaining == 0 else f"剩余 {remaining}")
        messagebox.showinfo("重试完成", msg)


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
