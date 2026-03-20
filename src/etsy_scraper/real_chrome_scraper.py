"""
Real Chrome Scraper - 使用 Selenium 连接真实 Chrome

核心思路：
1. 首先让你在真实的 Chrome 中手动访问 Etsy 并完成验证
2. 然后 Selenium 连接到该浏览器进行数据提取
3. 这样可以复用你手动验证后的会话
"""
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List

import requests


def sanitize_filename(name: str, max_length: int = 100) -> str:
    """清理文件名"""
    if not name:
        return "unnamed"
    sanitized = re.sub(r'[<>:"/\\|?*\n\r\t]', '_', name)
    return sanitized[:max_length].rstrip(' ._') or "unnamed"


def get_chrome_path() -> Optional[str]:
    """获取 Chrome 路径"""
    if sys.platform == "darwin":
        paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif sys.platform == "win32":
        paths = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
    else:
        paths = ["/usr/bin/google-chrome", "/usr/bin/chromium-browser"]
    
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def clean_chrome_profile(full_clean: bool = False):
    """
    清理 Chrome profile：
    - full_clean=False（默认）: 只清除缓存和追踪数据，保留 cookies/登录状态
    - full_clean=True: 删除整个 profile 目录
    """
    import shutil
    profile_dir = Path.home() / ".etsy_scraper_chrome_profile"
    if not profile_dir.exists():
        return
    
    if full_clean:
        try:
            shutil.rmtree(profile_dir)
            print("  ✓ 已彻底清理 Chrome profile")
        except Exception as e:
            print(f"  ⚠️ 清理 Chrome profile 失败: {e}")
        return
    
    # 选择性清理：删除缓存/追踪/限流标记，保留 cookies 和登录状态
    targets = [
        "Default/Cache",
        "Default/Code Cache",
        "Default/GPUCache",
        "Default/Service Worker",
        "Default/Local Storage/leveldb",
        "Default/Session Storage",
        "Default/IndexedDB",
        "ShaderCache",
        "GrShaderCache",
    ]
    cleaned = 0
    for target in targets:
        target_path = profile_dir / target
        if target_path.exists():
            try:
                if target_path.is_dir():
                    shutil.rmtree(target_path)
                else:
                    target_path.unlink()
                cleaned += 1
            except Exception:
                pass
    if cleaned > 0:
        print(f"  ✓ 已清理 {cleaned} 项缓存/追踪数据（保留 cookies/登录状态）")


def start_chrome_with_debug(url: str, port: int = 9222, clean: bool = False) -> subprocess.Popen:
    """
    启动带调试端口的 Chrome
    
    Args:
        url: 打开的 URL
        port: 调试端口
        clean: 是否先清理 profile（清除 cookies/缓存）
    """
    chrome_path = get_chrome_path()
    if not chrome_path:
        raise RuntimeError("找不到 Chrome！")
    
    if clean:
        clean_chrome_profile()
    
    # 创建临时用户目录避免与现有 Chrome 冲突
    temp_user_dir = Path.home() / ".etsy_scraper_chrome_profile"
    temp_user_dir.mkdir(exist_ok=True)
    
    width = random.randint(1200, 1920)
    height = random.randint(800, 1080)
    cmd = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={temp_user_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--disable-dev-shm-usage",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--lang=en-US,en",
        "--accept-lang=en-US,en;q=0.9",
        f"--window-size={width},{height}",
        url
    ]
    
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def apply_stealth(driver):
    """
    注入反检测脚本，隐藏 Selenium/WebDriver 指纹。
    必须在每次创建 driver 连接后调用。
    """
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined,
                    configurable: true
                });
                if (!window.chrome) { window.chrome = {}; }
                if (!window.chrome.runtime) {
                    window.chrome.runtime = {
                        connect: function() {},
                        sendMessage: function() {}
                    };
                }
                Object.defineProperty(navigator, 'plugins', {
                    get: () => [1, 2, 3, 4, 5],
                    configurable: true
                });
                Object.defineProperty(navigator, 'languages', {
                    get: () => ['en-US', 'en'],
                    configurable: true
                });

                // 伪造 permissions query 行为（DataDome 会检查）
                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );

                // 伪造 WebGL vendor/renderer（反指纹）
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {
                    if (parameter === 37445) return 'Intel Inc.';
                    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
                    return getParameter.call(this, parameter);
                };

                // 移除 Selenium 注入的 cdc_ 变量
                const cleanCdc = () => {
                    for (const key of Object.keys(document)) {
                        if (key.match(/^cdc_|^\\$cdc_/)) { delete document[key]; }
                    }
                };
                cleanCdc();
                const observer = new MutationObserver(cleanCdc);
                observer.observe(document, {childList: true, subtree: true});

                // 覆盖 toString，防止检测脚本通过 toString 发现函数被重写
                const nativeToString = Function.prototype.toString;
                Function.prototype.toString = function() {
                    if (this === Function.prototype.toString) return 'function toString() { [native code] }';
                    if (this === navigator.permissions.query) return 'function query() { [native code] }';
                    return nativeToString.call(this);
                };
            """
        })
    except Exception:
        pass


USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:134.0) Gecko/20100101 Firefox/134.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
]


def get_random_ua() -> str:
    return random.choice(USER_AGENTS)


def detect_access_block(driver) -> bool:
    """
    检测当前页面是否触发了 Etsy / DataDome 的访问限制。
    返回 True 表示被限制，False 表示正常。
    """
    try:
        current_url = driver.current_url.lower()
        if 'captcha' in current_url or 'geo.captcha' in current_url or 'datadome' in current_url:
            return True

        page_source = driver.page_source
        if not page_source:
            return False
        page_lower = page_source.lower()

        block_indicators = [
            'datadome',
            'captcha-container',
            'captcha-delivery',
            'blocked your access',
            'access to this page has been denied',
            'unusual traffic',
            'please verify you are a human',
            'are you a human',
            'challenge-platform',
            'cf-challenge',
            'just a moment',
            'checking your browser',
            'verify you are human',
            # Etsy 中文限制页面特征
            '访问暂时受限',
            '我不是机器人',
            '浏览网页的速度异常',
            '网路机器人',
            '非正常操作',
            '验证程式',
            # Etsy 英文限制页面
            'temporarily limited',
            'rate limited',
            'too many requests',
        ]
        for indicator in block_indicators:
            if indicator in page_lower:
                return True

        if 'geo.captcha-delivery.com' in page_lower:
            return True

        # 检测页面是否几乎为空（只有 Etsy logo + 限制文字，没有产品内容）
        # 限制页面的 HTML 通常很短（<5000字符），且没有产品相关元素
        if 'etsy.com' in current_url and len(page_source) < 5000:
            from selenium.webdriver.common.by import By
            try:
                product_elements = driver.find_elements(
                    By.CSS_SELECTOR,
                    'h1[data-buy-box-listing-title], div.v2-listing-card, div[data-appears-component-name="listing_page"]'
                )
                if not product_elements:
                    title_el = driver.find_elements(By.TAG_NAME, 'h1')
                    # 页面很短 + 无产品元素 + 没有正常标题 → 大概率是限制页面
                    if not title_el or (title_el and len(title_el[0].text) < 3):
                        return True
            except Exception:
                pass

    except Exception:
        pass
    return False


def wait_for_block_resolution(driver, timeout: int = 300, check_interval: int = 3) -> bool:
    """
    CLI 模式：检测到封锁后等待用户手动解除。
    每 check_interval 秒检查一次页面是否恢复正常。
    timeout 秒后放弃。返回 True 表示已解除。
    """
    print("\n" + "=" * 60)
    print("⚠️  检测到 Etsy 访问限制！")
    print("   请在浏览器中手动完成验证（人机验证/CAPTCHA）")
    print("   验证通过后程序会自动继续")
    print("=" * 60)

    start = time.time()
    while time.time() - start < timeout:
        time.sleep(check_interval)
        if not detect_access_block(driver):
            print("✅ 验证通过，继续抓取...")
            time.sleep(random.uniform(2, 4))
            return True
        elapsed = int(time.time() - start)
        print(f"   等待验证中... ({elapsed}s / {timeout}s)")

    print("❌ 等待超时，验证未通过")
    return False


def human_like_delay(base: float = 2.0):
    """更拟人化的延迟：对数正态分布 + 偶发长停顿"""
    import math
    delay = random.lognormvariate(math.log(base), 0.4)
    if random.random() < 0.12:
        delay += random.uniform(5, 15)
    return min(delay, 30.0)


def simulate_mouse_movement(driver):
    """在页面上模拟随机鼠标移动事件"""
    try:
        driver.execute_script("""
            (function() {
                const moves = Math.floor(Math.random() * 5) + 3;
                for (let i = 0; i < moves; i++) {
                    const x = Math.floor(Math.random() * window.innerWidth);
                    const y = Math.floor(Math.random() * window.innerHeight);
                    document.dispatchEvent(new MouseEvent('mousemove', {
                        clientX: x, clientY: y, bubbles: true
                    }));
                }
            })();
        """)
    except Exception:
        pass


def wait_for_chrome_ready(port: int = 9222, timeout: int = 30) -> bool:
    """等待 Chrome 调试端口就绪"""
    import time
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(f"http://localhost:{port}/json", timeout=2)
            if resp.status_code == 200:
                return True
        except:
            pass
        time.sleep(1)
    return False


def extract_data_with_selenium(port: int = 9222) -> Optional[Dict]:
    """使用 Selenium 连接并提取数据"""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    
    options = Options()
    options.add_experimental_option("debuggerAddress", f"localhost:{port}")
    
    driver = webdriver.Chrome(options=options)
    apply_stealth(driver)
    
    try:
        # 获取当前 URL
        current_url = driver.current_url
        print(f"当前页面: {current_url}")
        
        # 检查是否是有效的 Etsy 产品页面
        # 方法：看是否能找到产品页面的关键元素，而不是检测"验证"关键词
        is_product_page = False
        
        # 尝试查找产品页面特有的元素
        product_indicators = [
            'h1[data-buy-box-listing-title="true"]',  # 产品标题
            'div[data-appears-component-name="listing_page"]',  # 产品页面标记
            'div.listing-page-image-carousel',  # 图片轮播
            'button[data-add-to-cart-button]',  # 加入购物车按钮
            'div[data-buy-box-region="price"]',  # 价格区域
        ]
        
        for selector in product_indicators:
            try:
                el = driver.find_element(By.CSS_SELECTOR, selector)
                if el:
                    is_product_page = True
                    print(f"✓ 检测到产品页面元素: {selector}")
                    break
            except:
                continue
        
        # 备用检测：看 URL 是否包含 listing 且页面有 h1 标题
        if not is_product_page:
            if '/listing/' in current_url:
                try:
                    h1 = driver.find_element(By.TAG_NAME, 'h1')
                    h1_text = h1.text.strip()
                    # 确保 h1 不是验证页面的标题
                    if h1_text and len(h1_text) > 5 and '验证' not in h1_text and 'robot' not in h1_text.lower():
                        is_product_page = True
                        print(f"✓ 检测到产品标题: {h1_text[:50]}...")
                except:
                    pass
        
        if not is_product_page:
            # 先检查是否被封锁
            if detect_access_block(driver):
                resolved = wait_for_block_resolution(driver)
                if resolved:
                    # 重新检测产品页面
                    for selector in product_indicators:
                        try:
                            el = driver.find_element(By.CSS_SELECTOR, selector)
                            if el:
                                is_product_page = True
                                break
                        except:
                            continue
                if not is_product_page:
                    return None
            else:
                print("⚠️  未检测到产品页面元素！")
                print("   可能原因：")
                print("   1. 还在验证页面")
                print("   2. 页面未完全加载")
                print("   3. 不是有效的 Etsy 产品页面")
                
                force = input("\n是否强制继续抓取? (y/N): ")
                if force.lower() != 'y':
                    return None
                print("强制继续...")
        
        # 模拟人类浏览行为
        print("模拟浏览行为...")
        simulate_mouse_movement(driver)
        for _ in range(random.randint(2, 4)):
            scroll_distance = random.randint(200, 500)
            driver.execute_script(f"window.scrollBy(0, {scroll_distance})")
            time.sleep(random.uniform(0.5, 1.5))
        if random.random() < 0.3:
            driver.execute_script(f"window.scrollBy(0, -{random.randint(100, 200)})")
            time.sleep(random.uniform(0.3, 0.6))
        simulate_mouse_movement(driver)
        
        driver.execute_script("window.scrollTo(0, 0)")
        time.sleep(random.uniform(0.8, 1.5))
        
        # 提取数据
        print("提取数据...")
        
        data = {}
        
        # 标题
        try:
            title_el = driver.find_element(By.CSS_SELECTOR, 'h1[data-buy-box-listing-title="true"]')
            data['title'] = title_el.text.strip()
        except:
            try:
                title_el = driver.find_element(By.TAG_NAME, 'h1')
                data['title'] = title_el.text.strip()
            except:
                data['title'] = None
        
        # 店铺
        try:
            shop_link = driver.find_element(By.CSS_SELECTOR, 'a[href*="/shop/"]')
            href = shop_link.get_attribute('href')
            match = re.search(r'/shop/([^/?]+)', href)
            data['shop_name'] = match.group(1) if match else None
        except:
            data['shop_name'] = None
        
        # 价格
        try:
            price_el = driver.find_element(By.CSS_SELECTOR, 'span.currency-value')
            data['price'] = price_el.text.strip()
        except:
            data['price'] = None
        
        # 图片 - 只获取商品详情主图，排除 "More from this shop" 等杂图
        images = []
        seen_ids = set()  # 使用图片ID去重，而不是URL
        
        def extract_image_id(url):
            """从 URL 中提取图片唯一 ID，如 7261901436"""
            # 匹配 il_xxxxx.数字ID_后缀.jpg 格式
            match = re.search(r'/il_[^.]+\.(\d+)_', url)
            return match.group(1) if match else None
        
        def convert_to_fullsize(url):
            """将任何尺寸的图片 URL 转换为全尺寸"""
            # 匹配各种尺寸格式: _794xN, _570xN, _1588xN, _fullxfull, _300x300 等
            return re.sub(r'il_[^.]+\.', 'il_fullxfull.', url)
        
        # 方法0（最优先）: 直接从 data-src-zoom-image 属性获取最高清图片
        # 这个属性直接包含 fullxfull 版本的 URL，无需转换
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
            print(f"  方法0失败: {e}")
        
        # 方法1: 从产品图片轮播/画廊区域获取（备选）
        if not images:
            gallery_selectors = [
                # 主图片画廊容器
                'div[data-component="listing-page-image-carousel"] img',
                'ul[data-carousel-pagination-list] img',
                'div.image-carousel-container img',
                'div.listing-page-image-carousel img',
                # 缩略图列表
                'ul.carousel-pane-list img[src*="il_"]',
                'div[data-appears-component-name="image_carousel"] img',
            ]
            
            for selector in gallery_selectors:
                try:
                    gallery_imgs = driver.find_elements(By.CSS_SELECTOR, selector)
                    for img in gallery_imgs:
                        src = img.get_attribute('src') or img.get_attribute('data-src')
                        if src and 'il_' in src and 'etsystatic.com' in src:
                            img_id = extract_image_id(src)
                            if img_id and img_id not in seen_ids:
                                seen_ids.add(img_id)
                                full_src = convert_to_fullsize(src)
                                images.append(full_src)
                    if images:
                        print(f"  ✓ 从画廊区域找到 {len(images)} 张主图")
                        break
                except:
                    continue
        
        # 方法2: 如果前面方法没找到，使用 JavaScript 从页面顶部区域提取
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
                    
                    // 获取产品图片区域（通常在页面左侧/顶部）
                    const containers = document.querySelectorAll([
                        '[data-component*="image"]',
                        '[class*="listing-page-image"]',
                        '[class*="image-carousel"]',
                        '[data-appears-component-name*="image"]'
                    ].join(','));
                    
                    containers.forEach(container => {
                        container.querySelectorAll('img').forEach(img => {
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
                print(f"  JS提取失败: {e}")
        
        # 方法3: 最后的备选方案 - 只获取带有特定 listing ID 的图片
        if not images:
            try:
                # 从 URL 获取 listing ID
                listing_match = re.search(r'/listing/(\d+)/', current_url)
                if listing_match:
                    all_imgs = driver.find_elements(By.CSS_SELECTOR, 'img[src*="etsystatic.com/il_"]')
                    
                    for img in all_imgs:
                        src = img.get_attribute('src')
                        if src:
                            # 检查图片是否在页面上半部分（产品详情区域）
                            try:
                                location = img.location
                                # 只获取页面上部的图片（y < 1500 像素）
                                if location['y'] < 1500:
                                    img_id = extract_image_id(src)
                                    if img_id and img_id not in seen_ids:
                                        seen_ids.add(img_id)
                                        full_src = convert_to_fullsize(src)
                                        images.append(full_src)
                            except:
                                pass
                    
                    if images:
                        print(f"  ✓ 通过位置过滤找到 {len(images)} 张主图")
            except:
                pass
        
        # 去重并限制数量（一般商品主图不会超过10张）
        images = list(dict.fromkeys(images))[:15]
        print(f"  最终获取 {len(images)} 张商品主图")
        
        data['images'] = images
        
        # 产品 ID
        product_id_match = re.search(r'/listing/(\d+)/', current_url)
        data['product_id'] = product_id_match.group(1) if product_id_match else None
        
        data['url'] = current_url
        data['scraped_at'] = datetime.now().isoformat()
        
        return data
        
    finally:
        # 不要关闭浏览器，让用户可以继续使用
        pass


def download_images(images: List[str], title: str, output_dir: Path, 
                    image_selection: List[int] = None, filter_words: List[str] = None):
    """下载图片
    
    Args:
        images: 图片 URL 列表
        title: 商品标题
        output_dir: 输出目录
        image_selection: 要下载的图片序号列表（1-indexed），None 表示全部
        filter_words: 从标题中过滤的词汇列表
    """
    if not images or not title:
        return
    
    # 应用标题过滤
    try:
        from .utils import filter_title
    except ImportError:
        from utils import filter_title
    display_title = title
    if filter_words:
        display_title = filter_title(title, filter_words)
    
    safe_title = sanitize_filename(display_title)
    
    # 确定要下载的图片
    if image_selection:
        # 过滤出有效的序号
        valid_indices = [i for i in image_selection if 1 <= i <= len(images)]
        skipped_indices = [i for i in image_selection if i > len(images)]
        
        if skipped_indices:
            print(f"⚠️ 跳过不存在的图片序号: {skipped_indices} (共 {len(images)} 张图片)")
        
        if not valid_indices:
            print("⚠️ 没有有效的图片序号可下载")
            return
        
        download_list = [(i, images[i-1]) for i in valid_indices]
        print(f"\n下载 {len(download_list)}/{len(images)} 张图片 (序号: {valid_indices})...")
    else:
        download_list = [(i+1, url) for i, url in enumerate(images)]
        print(f"\n下载 {len(images)} 张图片...")
    
    session = requests.Session()
    session.headers.update({
        "User-Agent": get_random_ua(),
        "Referer": "https://www.etsy.com/",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })

    for idx, url in download_list:
        success = False
        for attempt in range(3):
            try:
                if attempt > 0:
                    session.headers["User-Agent"] = get_random_ua()
                    time.sleep(random.uniform(1, 3))
                
                resp = session.get(url, timeout=30)
                if resp.status_code == 200:
                    ext = url.split('.')[-1].split('?')[0] or 'jpg'
                    filename = f"{safe_title}-{idx}.{ext}"
                    filepath = output_dir / filename
                    filepath.write_bytes(resp.content)
                    print(f"  ✓ [{idx}/{len(images)}] {filename}")
                    success = True
                    break
                elif resp.status_code == 429:
                    print(f"  ⏳ [{idx}] 被限速，等待后重试...")
                    time.sleep(random.uniform(5, 10))
                else:
                    print(f"  ✗ [{idx}/{len(images)}] HTTP {resp.status_code}")
                    break
            except Exception as e:
                if attempt < 2:
                    continue
                print(f"  ✗ [{idx}/{len(images)}] {e}")
        
        time.sleep(random.uniform(0.3, 0.8))
    
    session.close()


def main():
    """主入口"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="使用真实 Chrome 的 Etsy 爬虫 - 最强反爬虫方案",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 单个链接
  etsy-real "https://www.etsy.com/listing/123456789"
  
  # 多个链接（空格分隔）
  etsy-real "https://www.etsy.com/listing/111" "https://www.etsy.com/listing/222"
  
  # 多个链接 + 选项
  etsy-real "https://www.etsy.com/listing/111" "https://www.etsy.com/listing/222" -i 1

工作流程:
  1. 启动真实 Chrome 浏览器（带调试端口）
  2. 你在浏览器中手动完成验证/登录
  3. 验证完成后，按 Enter 继续
  4. 自动提取数据和下载图片（多链接会依次处理）

为什么这样能绕过反爬虫?
  - 使用真实 Chrome，不是自动化浏览器
  - 你手动完成验证，不需要绕过
  - 复用已验证的会话状态
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
    
    # 解析图片选择和过滤词参数
    try:
        from .utils import parse_image_selection, parse_filter_words
    except ImportError:
        from utils import parse_image_selection, parse_filter_words
    
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
    print("这个爬虫使用真实的 Chrome 浏览器")
    print("你需要手动完成验证，然后自动抓取数据")
    if total_urls > 1:
        print(f"\n📋 共 {total_urls} 个链接待处理")
    print("=" * 60)
    
    # 步骤 1：启动 Chrome
    print("\n📌 步骤 1: 启动 Chrome")
    print("-" * 40)
    print("⚠️  请先关闭所有 Chrome 窗口！")
    input("准备好后按 Enter 继续...")
    
    print("\n启动 Chrome...")
    chrome_process = start_chrome_with_debug(urls[0], args.port)
    
    print("等待浏览器就绪...")
    if not wait_for_chrome_ready(args.port):
        print("❌ Chrome 启动失败！")
        chrome_process.terminate()
        return
    
    print("✓ Chrome 已启动！")
    
    # 步骤 2：等待用户完成验证
    print("\n" + "=" * 60)
    print("📌 步骤 2: 完成验证")
    print("-" * 40)
    print("""
在打开的 Chrome 窗口中：

  1. 如果看到验证页面，请完成「我不是机器人」验证
  2. 等待产品页面完全加载
  3. 你也可以登录 Etsy 账号（推荐）

⏰ 没有时间限制，慢慢来！
""")
    print("=" * 60)
    
    input("\n✋ 验证完成、页面加载好后，按 Enter 继续...")
    
    # 连接 Selenium
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    
    options = Options()
    options.add_experimental_option("debuggerAddress", f"localhost:{args.port}")
    driver = webdriver.Chrome(options=options)
    apply_stealth(driver)
    
    # 步骤 3：处理所有链接
    print("\n📌 步骤 3: 提取数据")
    print("-" * 40)
    
    success_count = 0
    fail_count = 0
    
    for idx, url in enumerate(urls, 1):
        if total_urls > 1:
            print(f"\n{'='*60}")
            print(f"[{idx}/{total_urls}] 处理链接:")
            print(f"  {url}")
            print(f"{'='*60}")
        
        if idx > 1:
            try:
                driver.get(url)
                time.sleep(random.uniform(2, 4))
                # 导航后检测封锁
                if detect_access_block(driver):
                    resolved = wait_for_block_resolution(driver)
                    if not resolved:
                        print("  ❌ 验证未通过，跳过此链接")
                        fail_count += 1
                        continue
                    driver.get(url)
                    time.sleep(random.uniform(2, 4))
                simulate_mouse_movement(driver)
                for _ in range(random.randint(2, 3)):
                    scroll_distance = random.randint(200, 400)
                    driver.execute_script(f"window.scrollBy(0, {scroll_distance})")
                    time.sleep(random.uniform(0.3, 0.8))
                driver.execute_script("window.scrollTo(0, 0)")
                time.sleep(1)
            except Exception as e:
                print(f"  ❌ 导航失败: {e}")
                fail_count += 1
                continue
        
        result = extract_data_with_selenium(args.port)
        
        if not result or not result.get('title'):
            # 检查是否被封锁导致失败
            if detect_access_block(driver):
                print("  ⚠️ 检测到访问限制！")
                resolved = wait_for_block_resolution(driver)
                if resolved:
                    driver.get(url)
                    time.sleep(random.uniform(2, 4))
                    result = extract_data_with_selenium(args.port)
            
            if not result or not result.get('title'):
                print(f"\n❌ 抓取失败！")
                fail_count += 1
                if idx < total_urls:
                    print("  继续处理下一个链接...")
                continue
        
        success_count += 1
        
        # 显示结果
        print(f"\n✅ 抓取成功！")
        print(f"  标题: {result.get('title', 'N/A')}")
        print(f"  店铺: {result.get('shop_name', 'N/A')}")
        print(f"  价格: {result.get('price', 'N/A')}")
        print(f"  图片: {len(result.get('images', []))} 张")
        
        # 保存 JSON
        product_id = result.get('product_id', 'unknown')
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_path = output_path / f"product_{product_id}_{timestamp}.json"
        json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"  ✓ 数据已保存: {json_path}")
        
        # 下载图片
        download_images(result.get('images', []), result.get('title', ''), output_path,
                        image_selection=image_selection, filter_words=filter_words)
        
        if idx < total_urls:
            wait_time = human_like_delay(args.delay)
            print(f"\n⏳ 等待 {wait_time:.1f} 秒后处理下一个链接...")
            time.sleep(wait_time)
    
    # 显示最终统计
    print("\n" + "=" * 60)
    print("🎉 完成！")
    print("=" * 60)
    if total_urls > 1:
        print(f"  总链接数: {total_urls}")
        print(f"  成功: {success_count}")
        print(f"  失败: {fail_count}")
    print(f"  输出目录: {output_path}")
    
    # 询问是否关闭浏览器
    close = input("\n是否关闭 Chrome 浏览器? (y/N): ")
    if close.lower() == 'y':
        chrome_process.terminate()
        print("浏览器已关闭")
    else:
        print("浏览器保持打开，你可以继续使用")


if __name__ == "__main__":
    main()
