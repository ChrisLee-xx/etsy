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


def start_chrome_with_debug(url: str, port: int = 9222) -> subprocess.Popen:
    """启动带调试端口的 Chrome"""
    chrome_path = get_chrome_path()
    if not chrome_path:
        raise RuntimeError("找不到 Chrome！")
    
    # 创建临时用户目录避免与现有 Chrome 冲突
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
    
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
            print("⚠️  未检测到产品页面元素！")
            print("   可能原因：")
            print("   1. 还在验证页面")
            print("   2. 页面未完全加载")
            print("   3. 不是有效的 Etsy 产品页面")
            
            # 询问用户是否要强制继续
            force = input("\n是否强制继续抓取? (y/N): ")
            if force.lower() != 'y':
                return None
            print("强制继续...")
        
        # 模拟人类滚动
        print("模拟浏览行为...")
        for _ in range(3):
            scroll_distance = random.randint(200, 500)
            driver.execute_script(f"window.scrollBy(0, {scroll_distance})")
            time.sleep(random.uniform(0.5, 1.5))
        
        # 滚动回顶部
        driver.execute_script("window.scrollTo(0, 0)")
        time.sleep(1)
        
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
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
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
                print(f"  ✓ [{idx}/{len(images)}] {filename}")
            else:
                print(f"  ✗ [{idx}/{len(images)}] HTTP {resp.status_code}")
        except Exception as e:
            print(f"  ✗ [{idx}/{len(images)}] {e}")
        
        time.sleep(random.uniform(0.3, 0.8))


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
        
        # 如果不是第一个链接，需要导航到新页面
        if idx > 1:
            try:
                driver.get(url)
                # 等待页面加载
                time.sleep(2)
                # 模拟人类滚动
                for _ in range(2):
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
        
        # 多链接间延迟
        if idx < total_urls:
            wait_time = args.delay + random.uniform(-0.5, 1.0)
            wait_time = max(1.0, wait_time)
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
