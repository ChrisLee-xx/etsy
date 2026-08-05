"""
Real Chrome Scraper - 使用 Selenium 连接真实 Chrome

核心思路：
1. 启动真实的 Chrome 并使用 undetected-chromedriver 补丁避免被检测
2. Selenium 连接到该浏览器进行数据提取
3. 遇到封锁自动清理 profile 并重启
"""
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List

import requests

# Windows 专用模块安全导入
if sys.platform == 'win32':
    try:
        import _overlapped  # noqa: F401
    except ImportError:
        pass
    try:
        import _socket  # noqa: F401
    except ImportError:
        pass
    try:
        import _ssl  # noqa: F401
    except ImportError:
        pass

# 兼容 PyInstaller Windows 环境（stdout 可能为 None）
_builtin_print = print
def _safe_print(*args, **kwargs):
    if sys.stdout is not None:
        kwargs.setdefault('flush', True)
        _builtin_print(*args, **kwargs)
print = _safe_print


def sanitize_filename(name: str) -> str:
    """清理文件名"""
    if not name:
        return "unnamed"
    sanitized = re.sub(r'[<>:"/\\|?*\n\r\t]', '_', name)
    return sanitized.rstrip(' ._') or "unnamed"


def get_chrome_path() -> Optional[str]:
    """获取 Chrome 路径"""
    if sys.platform == "darwin":
        paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif sys.platform == "win32":
        # Windows 上支持多种安装路径
        paths = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Chromium\Application\chrome.exe"),
            # Microsoft Edge (Chromium) - 备用
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        ]
    else:
        paths = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
            "/snap/bin/chromium",
        ]

    for p in paths:
        if os.path.exists(p):
            return p
    return None


def start_chrome_with_debug(url: str, port: int = 9222) -> subprocess.Popen:
    """启动带调试端口的 Chrome"""
    chrome_path = get_chrome_path()
    if not chrome_path:
        raise RuntimeError("找不到 Chrome！")

    temp_user_dir = Path.home() / ".etsy_scraper_chrome_profile"
    temp_user_dir.mkdir(exist_ok=True)

    cmd = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={temp_user_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        f"--window-size={random.randint(1200, 1920)},{random.randint(800, 1080)}",
        url
    ]

    # Windows 上需要 CREATE_NO_WINDOW 标志来避免创建控制台窗口
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = subprocess.CREATE_NO_WINDOW
        return subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            startupinfo=startupinfo, creationflags=creationflags
        )
    else:
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_for_chrome_ready(port: int = 9222, timeout: int = 30) -> bool:
    """等待 Chrome 调试端口就绪"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(f"http://localhost:{port}/json", timeout=2)
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def create_patched_driver(port: int = 9222):
    """
    用 undetected_chromedriver 的 Patcher 移除 chromedriver 二进制中的 cdc_ 指纹，
    再用标准 Selenium 连接到已有的 Chrome 实例（通过 debuggerAddress）。
    自动检测 Chrome 版本以下载匹配的 chromedriver。
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    import undetected_chromedriver as uc

    # 从运行中的 Chrome 获取实际版本号，确保 chromedriver 版本匹配
    version_main = None
    try:
        resp = requests.get(f"http://localhost:{port}/json/version", timeout=5)
        browser_str = resp.json().get("Browser", "")
        if "/" in browser_str:
            version_main = int(browser_str.split("/")[1].split(".")[0])
            print(f"  [driver] 检测到 Chrome 版本: {version_main}")
    except Exception:
        print("  [driver] 无法检测 Chrome 版本，将使用默认值")

    print("  [driver] 正在初始化反检测补丁（首次需下载 chromedriver，请稍候）...")
    patcher = uc.Patcher(version_main=version_main)
    
    # 设置可执行权限（避免 Linux/Mac 上 permission denied）
    patcher.auto()
    print(f"  [driver] 补丁完成: {patcher.executable_path}")

    options = Options()
    options.add_experimental_option("debuggerAddress", f"localhost:{port}")
    service = Service(executable_path=patcher.executable_path)
    
    # webdriver.Chrome 连接已有的 Chrome 实例（通过 debuggerAddress）
    # 注意：此调用在后台线程中运行，不能使用 signal 做超时保护
    # 如果 Chrome 未就绪，Selenium 内部会尝试连接直到超时
    driver = webdriver.Chrome(service=service, options=options)

    # 立即设置超时，防止后续任何 Selenium 调用无限阻塞
    try:
        driver.set_page_load_timeout(30)
        driver.set_script_timeout(15)
    except Exception:
        pass

    return driver


USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


def get_random_ua() -> str:
    return random.choice(USER_AGENTS)


# ────────────── 封锁检测与自动恢复 ──────────────

def _is_access_blocked(driver) -> bool:
    """检测当前页面是否触发了 Etsy 的访问限制"""
    try:
        page_source = driver.page_source
        if '访问暂时受限' in page_source:
            return True
    except Exception:
        pass
    return False


def _is_browser_disconnected(exc: Exception) -> bool:
    """判断 Selenium 异常是否属于 Chrome/DevTools session 已断开。"""
    msg = str(exc).lower()
    disconnected_signals = [
        "invalid session id",
        "session deleted",
        "not connected to devtools",
        "disconnected",
        "chrome not reachable",
        "target window already closed",
        "no such window",
        "web view not found",
    ]
    return any(signal in msg for signal in disconnected_signals)


def _restart_chrome_fresh(chrome_process, url: str, port: int = 9222):
    """
    清理 profile 并重启 Chrome，返回 (new_chrome_process, new_driver)。
    用于被封锁后自动恢复。
    """
    print("\n    🔄 自动重启 Chrome（清理旧 session）...")

    # 终止旧 Chrome 进程
    if chrome_process:
        try:
            chrome_process.terminate()
            chrome_process.wait(timeout=5)
        except Exception:
            try:
                chrome_process.kill()
            except Exception:
                pass

    # 等待端口释放
    time.sleep(3)

    # 强制清理可能残留的占用端口的进程
    import platform as _plat
    system = _plat.system()
    if system == 'Darwin':
        try:
            import subprocess as _sp
            _sp.run(['fuser', '-k', f'{port}/tcp'], capture_output=True, timeout=5)
        except Exception:
            pass
    elif system == 'Windows':
        try:
            import subprocess as _sp
            # 查找占用端口的 PID
            result = _sp.run(
                ['netstat', '-ano'], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if f':{port}' in line and 'LISTENING' in line:
                    parts = line.strip().split()
                    pid = parts[-1]
                    # 终止占用端口的进程
                    _sp.run(['taskkill', '/F', '/PID', pid], capture_output=True, timeout=5)
                    break
        except Exception:
            pass
    time.sleep(2)

    profile_dir = Path.home() / ".etsy_scraper_chrome_profile"
    if profile_dir.exists():
        try:
            shutil.rmtree(profile_dir)
            print("    ✓ 已清理旧 profile")
        except Exception:
            pass

    new_chrome = start_chrome_with_debug(url, port)
    if not wait_for_chrome_ready(port, timeout=20):
        print("    ❌ Chrome 重启失败（等待超时）")
        return None, None

    new_driver = create_patched_driver(port)

    # 给新 driver 设置超时
    try:
        new_driver.set_page_load_timeout(30)
        new_driver.set_script_timeout(15)
    except Exception:
        pass

    print("    ✅ Chrome 已重启，全新 session")
    return new_chrome, new_driver


class _DriverContext:
    """在抓取循环中共享可替换的 driver 和 chrome_process"""
    def __init__(self, driver, chrome_process, port: int = 9222):
        self.driver = driver
        self.chrome_process = chrome_process
        self.port = port
        # 设置超时防止无限卡死：页面加载 30s，脚本执行 15s，隐式等待 10s
        try:
            driver.set_page_load_timeout(30)
            driver.set_script_timeout(15)
        except Exception:
            pass

    def handle_block(self, url: str, section_url: str = None, immediate: bool = False) -> bool:
        """
        检测到封锁后恢复浏览，最多尝试 3 次。
        
        轻量恢复策略（不断开 Chrome）：
        1. 等待 5 秒冷却
        2. 访问 Etsy 首页（https://www.etsy.com/），最多重试 3 次
        3. 等待 5 秒
        4. 回到 section 页面
        5. 检查是否仍被限制
        
        如果 Chrome/DevTools 断连，回退到完整重启。
        
        Args:
            url: 当前正在访问的 URL（用于断连重启后导航）
            section_url: Section 页面 URL（轻量恢复时回到此页面）
            immediate: 是否跳过第一次等待（用于断连场景）
        """
        for attempt in range(1, 4):
            try:
                cooldown = 0 if immediate and attempt == 1 else 5
                if cooldown > 0:
                    print(f"    🚫 访问被限制，等待 {cooldown} 秒后恢复（第 {attempt} 次）...")
                    time.sleep(cooldown)
                else:
                    print(f"    🚫 立即尝试恢复（第 {attempt} 次）...")
                
                # 步骤1：访问 Etsy 首页（最多重试 3 次）
                home_ok = False
                for home_attempt in range(1, 4):
                    try:
                        self.driver.get("https://www.etsy.com/")
                        time.sleep(5)
                        if not _is_access_blocked(self.driver):
                            home_ok = True
                            break
                        print(f"    ⚠️ 首页仍被限制，重试第 {home_attempt} 次...")
                    except Exception:
                        print(f"    ⚠️ 访问首页失败，重试第 {home_attempt} 次...")
                
                if not home_ok:
                    print(f"    ⚠️ 首页未能恢复，继续尝试...")
                    continue
                
                # 步骤2：回到 section 页面
                if section_url:
                    self.driver.get(section_url)
                    time.sleep(2)
                
                # 检查是否仍被限制
                if not _is_access_blocked(self.driver):
                    return True
                    
            except Exception as e:
                if _is_browser_disconnected(e):
                    # Chrome 断连，需要完整重启
                    print(f"    🔌 Chrome 连接断开，重启 Chrome（第 {attempt} 次）...")
                    new_chrome, new_driver = _restart_chrome_fresh(
                        self.chrome_process, url, self.port
                    )
                    if not new_driver:
                        continue
                    self.chrome_process = new_chrome
                    self.driver = new_driver
                    try:
                        nav_url = section_url or url
                        self.driver.get(nav_url)
                        time.sleep(random.uniform(3, 5))
                    except Exception:
                        continue
                    if not _is_access_blocked(self.driver):
                        return True
                continue
        
        print("    ❌ 多次恢复仍被限制")
        return False


# ────────────── 图片提取（共享） ──────────────

def _extract_product_images(driver) -> List[str]:
    """
    从 Etsy 商品页面提取所有高清图片 URL。
    供 extract_data_with_selenium 和 extract_product_data_silent 共用。
    """
    from selenium.webdriver.common.by import By

    images = []
    seen_ids = set()

    def extract_image_id(url):
        match = re.search(r'/il_[^.]+\.(\d+)_', url)
        return match.group(1) if match else None

    def convert_to_fullsize(url):
        return re.sub(r'il_[^.]+\.', 'il_fullxfull.', url)

    # 方法0: data-src-zoom-image（最优先，直接获取 fullxfull 版本）
    try:
        zoom_imgs = driver.find_elements(
            By.CSS_SELECTOR,
            'li[data-carousel-pane]:not([data-video-pane]) img[data-src-zoom-image]'
        )
        for img in zoom_imgs:
            zoom_url = img.get_attribute('data-src-zoom-image')
            if zoom_url and 'etsystatic.com' in zoom_url:
                img_id = extract_image_id(zoom_url)
                if img_id and img_id not in seen_ids:
                    seen_ids.add(img_id)
                    images.append(zoom_url)
        if images:
            print(f"  ✓ 从 data-src-zoom-image 直接获取 {len(images)} 张高清主图")
    except Exception as e:
        if _is_browser_disconnected(e):
            raise
        print(f"  方法0失败: {e}")

    # 方法0b: 单图商品回退（无 carousel-pane 结构时，直接找页面上的 data-src-zoom-image）
    if not images:
        try:
            zoom_imgs = driver.find_elements(
                By.CSS_SELECTOR,
                'img[data-src-zoom-image]'
            )
            for img in zoom_imgs:
                zoom_url = img.get_attribute('data-src-zoom-image')
                if zoom_url and 'etsystatic.com' in zoom_url:
                    img_id = extract_image_id(zoom_url)
                    if img_id and img_id not in seen_ids:
                        seen_ids.add(img_id)
                        images.append(zoom_url)
            if images:
                print(f"  ✓ 单图商品回退：从 data-src-zoom-image 获取 {len(images)} 张主图")
        except Exception as e:
            if _is_browser_disconnected(e):
                raise
            print(f"  方法0b失败: {e}")

    # 方法1: 画廊区域 CSS 选择器
    if not images:
        gallery_selectors = [
            'div[data-component="listing-page-image-carousel"] img',
            'ul[data-carousel-pagination-list] img',
            'div.image-carousel-container img',
            'div.listing-page-image-carousel img',
            'ul.carousel-pane-list img[src*="il_"]',
            'div[data-appears-component-name="image_carousel"] img',
            # 单图商品回退选择器
            'div[data-component="listing-page-main-image"] img',
            'div.listing-image-container img',
            'img[data-carousel-first-image]',
        ]
        for selector in gallery_selectors:
            try:
                gallery_imgs = driver.find_elements(By.CSS_SELECTOR, selector)
                for img in gallery_imgs:
                    # 跳过模糊背景图和缩略图
                    class_name = img.get_attribute('class') or ''
                    if 'blur-bg' in class_name or 'thumbnail' in class_name:
                        continue
                    src = img.get_attribute('src') or img.get_attribute('data-src')
                    if src and 'il_' in src and 'etsystatic.com' in src:
                        img_id = extract_image_id(src)
                        if img_id and img_id not in seen_ids:
                            seen_ids.add(img_id)
                            images.append(convert_to_fullsize(src))
                if images:
                    print(f"  ✓ 从画廊区域找到 {len(images)} 张主图")
                    break
            except Exception as e:
                if _is_browser_disconnected(e):
                    raise
                continue

    # 方法2: JavaScript 从页面图片容器提取
    if not images:
        try:
            js_images = driver.execute_script('''
                const images = [];
                const seenIds = new Set();

                function extractImageId(url) {
                    const match = url.match(/\\/il_[^.]+\\.(\\d+)_/);
                    return match ? match[1] : null;
                }

                function convertToFullsize(url) {
                    return url.replace(/il_[^.]+\\./, 'il_fullxfull.');
                }

                const containers = document.querySelectorAll([
                    '[data-component*="image"]',
                    '[class*="listing-page-image"]',
                    '[class*="image-carousel"]',
                    '[data-appears-component-name*="image"]'
                ].join(','));

                containers.forEach(container => {
                    container.querySelectorAll('img').forEach(img => {
                        // 跳过模糊背景图和缩略图
                        const cls = img.className || '';
                        if (cls.includes('blur-bg') || cls.includes('thumbnail')) {
                            return;
                        }
                        let src = img.src || img.dataset.src;
                        if (src && src.includes('etsystatic.com') && src.includes('/il_')) {
                            const imgId = extractImageId(src);
                            if (imgId && !seenIds.has(imgId)) {
                                seenIds.add(imgId);
                                images.push(convertToFullsize(src));
                            }
                        }
                    });
                });

                return images;
            ''')
            if js_images:
                for src in js_images:
                    img_id = extract_image_id(src)
                    if img_id and img_id not in seen_ids:
                        seen_ids.add(img_id)
                        images.append(src)
                print(f"  ✓ 通过JS从图片区域找到 {len(images)} 张主图")
        except Exception as e:
            if _is_browser_disconnected(e):
                raise
            print(f"  JS提取失败: {e}")

    # 方法3: 按位置过滤（页面上半部分的 Etsy 图片）
    if not images:
        try:
            current_url = driver.current_url
            listing_match = re.search(r'/listing/(\d+)/', current_url)
            if listing_match:
                all_imgs = driver.find_elements(By.CSS_SELECTOR, 'img[src*="etsystatic.com/il_"]')
                for img in all_imgs:
                    # 跳过模糊背景图和缩略图
                    class_name = img.get_attribute('class') or ''
                    if 'blur-bg' in class_name or 'thumbnail' in class_name:
                        continue
                    src = img.get_attribute('src')
                    if src:
                        try:
                            location = img.location
                            if location['y'] < 1500:
                                img_id = extract_image_id(src)
                                if img_id and img_id not in seen_ids:
                                    seen_ids.add(img_id)
                                    images.append(convert_to_fullsize(src))
                        except Exception as e:
                            if _is_browser_disconnected(e):
                                raise
                            pass
                if images:
                    print(f"  ✓ 通过位置过滤找到 {len(images)} 张主图")
        except Exception as e:
            if _is_browser_disconnected(e):
                raise
            pass

    result = list(dict.fromkeys(images))[:15]
    print(f"  最终获取 {len(result)} 张商品主图")
    return result


# ────────────── 数据提取 ──────────────

def extract_data_with_selenium(driver) -> Optional[Dict]:
    """
    使用已有的 Selenium driver 提取 Etsy 商品数据（标题、店铺、价格、图片等）。
    """
    from selenium.webdriver.common.by import By

    try:
        current_url = driver.current_url
        print(f"当前页面: {current_url}")

        # 检查是否是有效的 Etsy 产品页面
        is_product_page = False

        product_indicators = [
            'h1[data-buy-box-listing-title="true"]',
            'div[data-appears-component-name="listing_page"]',
            'div.listing-page-image-carousel',
            'button[data-add-to-cart-button]',
            'div[data-buy-box-region="price"]',
        ]

        for selector in product_indicators:
            try:
                el = driver.find_element(By.CSS_SELECTOR, selector)
                if el:
                    is_product_page = True
                    print(f"✓ 检测到产品页面元素: {selector}")
                    break
            except Exception:
                continue

        if not is_product_page:
            if '/listing/' in current_url:
                try:
                    h1 = driver.find_element(By.TAG_NAME, 'h1')
                    h1_text = h1.get_attribute('textContent').strip()
                    if h1_text and len(h1_text) > 5 and '验证' not in h1_text:
                        is_product_page = True
                        print(f"✓ 检测到产品标题: {h1_text[:50]}...")
                except Exception:
                    pass

        if not is_product_page:
            print("⚠️  未检测到产品页面元素，跳过")
            return None

        print("提取数据...")
        data = {}

        # 标题
        try:
            title_el = driver.find_element(By.CSS_SELECTOR, 'h1[data-buy-box-listing-title="true"]')
            data['title'] = title_el.get_attribute('textContent').strip()
        except Exception:
            try:
                title_el = driver.find_element(By.TAG_NAME, 'h1')
                data['title'] = title_el.get_attribute('textContent').strip()
            except Exception:
                data['title'] = None

        # 店铺
        try:
            shop_link = driver.find_element(By.CSS_SELECTOR, 'a[href*="/shop/"]')
            href = shop_link.get_attribute('href')
            match = re.search(r'/shop/([^/?]+)', href)
            data['shop_name'] = match.group(1) if match else None
        except Exception:
            data['shop_name'] = None

        # 价格
        try:
            price_el = driver.find_element(By.CSS_SELECTOR, 'span.currency-value')
            data['price'] = price_el.text.strip()
        except Exception:
            data['price'] = None

        # 图片（使用共享提取函数）
        data['images'] = _extract_product_images(driver)

        # 产品 ID
        product_id_match = re.search(r'/listing/(\d+)/', current_url)
        data['product_id'] = product_id_match.group(1) if product_id_match else None

        data['url'] = current_url
        data['scraped_at'] = datetime.now().isoformat()

        return data

    except Exception as e:
        if _is_browser_disconnected(e):
            raise
        print(f"  ✗ 提取失败: {e}")
        return None


def download_images(images: List[str], title: str, output_dir: Path,
                    image_selection: List[int] = None, filter_words: List[str] = None):
    """下载图片"""
    if not images or not title:
        return

    try:
        from etsy_scraper.utils import filter_title
    except ImportError:
        from utils import filter_title  # type: ignore
    display_title = title
    if filter_words:
        display_title = filter_title(title, filter_words)

    safe_title = sanitize_filename(display_title)

    if image_selection:
        valid_indices = [i for i in image_selection if 1 <= i <= len(images)]
        skipped_indices = [i for i in image_selection if i > len(images)]

        if skipped_indices:
            print(f"⚠️ 跳过不存在的图片序号: {skipped_indices} (共 {len(images)} 张图片)")

        if not valid_indices:
            print(f"⚠️ 选择的图片序号超出范围，默认下载第1张")
            download_list = [(1, images[0])] if images else []
        else:
            download_list = [(i, images[i-1]) for i in valid_indices]
            print(f"\n下载 {len(download_list)}/{len(images)} 张图片 (序号: {valid_indices})...")
    else:
        download_list = [(i+1, url) for i, url in enumerate(images)]
        print(f"\n下载 {len(images)} 张图片...")

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
                print(f"  ✓ [{idx}/{len(images)}] {filename} ({len(resp.content)//1024}KB)")
            else:
                save_failed_image(output_dir, title, url, idx, reason)
                print(f"  ✗ [{idx}/{len(images)}] {reason}，已保存链接待二次抓取")
        except Exception as e:
            try:
                from etsy_scraper.utils import save_failed_image
            except ImportError:
                from utils import save_failed_image  # type: ignore
            save_failed_image(output_dir, title, url, idx, str(e))
            print(f"  ✗ [{idx}/{len(images)}] {e}，已保存链接待二次抓取")

        time.sleep(random.uniform(0.3, 0.8))


def main():
    """主入口"""
    import argparse

    parser = argparse.ArgumentParser(
        description="使用真实 Chrome 的 Etsy 爬虫",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 单个链接
  etsy-real "https://www.etsy.com/listing/123456789"

  # 多个链接（空格分隔）
  etsy-real "https://www.etsy.com/listing/111" "https://www.etsy.com/listing/222"

  # 多个链接 + 选项
  etsy-real "https://www.etsy.com/listing/111" "https://www.etsy.com/listing/222" -i 1
"""
    )

    parser.add_argument("urls", nargs="+", help="Etsy 产品 URL（支持多个）")
    parser.add_argument("--output", "-o", default="output", help="输出目录")
    parser.add_argument("--port", "-p", type=int, default=9222, help="调试端口")
    parser.add_argument("--delay", "-d", type=float, default=2.0, help="多链接间延迟秒数（默认: 2）")
    parser.add_argument("--images", "-i", default=None,
                        help="指定下载哪些图片，如: '1' 或 '1,3,5' 或 '2-4' 或 '1,3-5,8'")
    parser.add_argument("--filter", "-f", default=None,
                        help="从标题中过滤的词汇，逗号分隔，如: 'Canvas,Poster,Wall Art'")

    args = parser.parse_args()

    try:
        from etsy_scraper.utils import parse_image_selection, parse_filter_words
    except ImportError:
        from utils import parse_image_selection, parse_filter_words  # type: ignore

    image_selection = None
    filter_words = None

    if args.images:
        try:
            image_selection = parse_image_selection(args.images)
            print(f"✓ 图片选择: {image_selection}")
        except ValueError as e:
            print(f"❌ {e}")
            return

    if args.filter:
        filter_words = parse_filter_words(args.filter)
        print(f"✓ 标题过滤词: {filter_words}")

    output_path = Path(args.output)
    output_path.mkdir(parents=True, exist_ok=True)

    urls = args.urls
    total_urls = len(urls)

    print("\n" + "=" * 60)
    print("🌐 REAL CHROME ETSY SCRAPER")
    print("=" * 60)
    if total_urls > 1:
        print(f"📋 共 {total_urls} 个链接待处理")
    print("=" * 60)

    print("\n📌 启动 Chrome...")
    chrome_process = start_chrome_with_debug(urls[0], args.port)

    print("等待浏览器就绪...")
    if not wait_for_chrome_ready(args.port):
        print("❌ Chrome 启动失败！请先关闭所有 Chrome 窗口后重试。")
        chrome_process.terminate()
        return

    print("✅ Chrome 已启动！")

    driver = create_patched_driver(args.port)
    ctx = _DriverContext(driver, chrome_process, args.port)
    time.sleep(3)

    # 开始前检测封锁
    if _is_access_blocked(ctx.driver):
        print("🚫 检测到访问限制，自动重启 Chrome...")
        if not ctx.handle_block(urls[0]):
            print("❌ 无法恢复，退出")
            return
        print("✅ 已恢复")

    print("\n📌 提取数据")
    print("-" * 40)

    success_count = 0
    fail_count = 0
    consecutive_fails = 0

    try:
        for idx, url in enumerate(urls, 1):
            if total_urls > 1:
                print(f"\n{'='*60}")
                print(f"[{idx}/{total_urls}] 处理链接:")
                print(f"  {url}")
                print(f"{'='*60}")

            if idx > 1:
                try:
                    ctx.driver.get(url)
                    time.sleep(2)
                except Exception as e:
                    if _is_browser_disconnected(e):
                        print("  🔌 Chrome 连接断开，立即重启后重试...")
                        if ctx.handle_block(url, immediate=True):
                            consecutive_fails = 0
                            # 恢复后重新导航到目标商品页
                            try:
                                ctx.driver.get(url)
                                time.sleep(2)
                            except Exception:
                                pass
                        else:
                            print("❌ 无法恢复，停止")
                            break
                    else:
                        print(f"  ❌ 导航失败: {e}")
                        fail_count += 1
                        consecutive_fails += 1
                        continue

            # 封锁检测
            if _is_access_blocked(ctx.driver):
                if ctx.handle_block(url):
                    consecutive_fails = 0
                    # 恢复后重新导航到目标商品页
                    try:
                        ctx.driver.get(url)
                        time.sleep(2)
                    except Exception:
                        pass
                else:
                    print("❌ 无法恢复，停止")
                    break

            try:
                result = extract_data_with_selenium(ctx.driver)
            except Exception as e:
                if _is_browser_disconnected(e):
                    print("  🔌 Chrome 连接断开，立即重启后重试当前链接...")
                    if ctx.handle_block(url, immediate=True):
                        # 恢复后重新导航到目标商品页再提取
                        try:
                            ctx.driver.get(url)
                            time.sleep(2)
                        except Exception:
                            pass
                        try:
                            result = extract_data_with_selenium(ctx.driver)
                        except Exception as retry_error:
                            print(f"  ❌ 重试仍失败: {retry_error}")
                            result = None
                    else:
                        print("❌ 无法恢复，停止")
                        break
                else:
                    print(f"  ❌ 提取失败: {e}")
                    result = None

            if not result or not result.get('title'):
                print(f"\n❌ 抓取失败！")
                fail_count += 1
                consecutive_fails += 1

                if consecutive_fails >= 3:
                    print(f"\n⚠️ 连续 {consecutive_fails} 次失败，尝试重启 Chrome...")
                    if ctx.handle_block(url):
                        print("✅ 已恢复，继续")
                        consecutive_fails = 0
                    else:
                        print("❌ 无法恢复，停止")
                        break

                if idx < total_urls:
                    print("  继续处理下一个链接...")
                continue

            success_count += 1
            consecutive_fails = 0

            print(f"\n✅ 抓取成功！")
            print(f"  标题: {result.get('title', 'N/A')}")
            print(f"  店铺: {result.get('shop_name', 'N/A')}")
            print(f"  价格: {result.get('price', 'N/A')}")
            print(f"  图片: {len(result.get('images', []))} 张")

            product_id = result.get('product_id', 'unknown')
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            json_path = output_path / f"product_{product_id}_{timestamp}.json"
            json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
            print(f"  ✓ 数据已保存: {json_path}")

            download_images(result.get('images', []), result.get('title', ''), output_path,
                            image_selection=image_selection, filter_words=filter_words)

            if idx < total_urls:
                wait_time = args.delay + random.uniform(-0.5, 1.0)
                wait_time = max(1.0, wait_time)
                print(f"\n⏳ 等待 {wait_time:.1f} 秒后处理下一个链接...")
                time.sleep(wait_time)

        print("\n" + "=" * 60)
        print("🎉 完成！")
        print("=" * 60)
        if total_urls > 1:
            print(f"  总链接数: {total_urls}")
            print(f"  成功: {success_count}")
            print(f"  失败: {fail_count}")
        print(f"  输出目录: {output_path}")

    finally:
        try:
            ctx.chrome_process.terminate()
        except Exception:
            pass
        print("浏览器已关闭")


if __name__ == "__main__":
    main()
