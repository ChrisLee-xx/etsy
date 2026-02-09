"""
Section Scraper - 批量抓取 Etsy 店铺 Section 下的所有商品图片

工作流程：
1. 输入 Section URL
2. 自动提取该 Section 下所有商品链接
3. 依次访问每个商品页面，下载所有图片
4. 按扁平目录结构组织输出
"""
import argparse
import json
import math
import random
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests


class ScrapeProgress:
    """
    管理抓取进度的持久化
    
    进度文件位置: {output_dir}/.progress.json
    
    进度文件格式:
    {
        "section_url": "https://...",
        "shop_name": "...",
        "section_id": "...",
        "started_at": "2026-02-03T10:00:00Z",
        "updated_at": "2026-02-03T10:30:00Z",
        "completed_ids": ["1234567890", ...],
        "total_found": 41
    }
    """
    
    def __init__(self, output_dir: Path, section_url: str, shop_name: str, section_id: str):
        """
        初始化进度管理器
        
        Args:
            output_dir: 输出目录
            section_url: Section URL
            shop_name: 店铺名称
            section_id: Section ID
        """
        self.progress_file = output_dir / ".progress.json"
        self.section_url = section_url
        self.shop_name = shop_name
        self.section_id = section_id
        self._completed_ids: Set[str] = set()
        self._total_found: int = 0
        self._started_at: Optional[str] = None
    
    def load(self) -> Set[str]:
        """
        加载已完成的 listing_id 集合
        
        Returns:
            已完成的 listing_id 集合
            
        Raises:
            ValueError: 如果进度文件损坏
        """
        if not self.progress_file.exists():
            return set()
        
        try:
            with open(self.progress_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self._completed_ids = set(data.get('completed_ids', []))
            self._total_found = data.get('total_found', 0)
            self._started_at = data.get('started_at')
            
            return self._completed_ids
            
        except json.JSONDecodeError as e:
            raise ValueError(
                f"进度文件损坏，JSON 格式无效: {e}\n"
                f"请删除文件后重试: {self.progress_file}"
            )
        except Exception as e:
            raise ValueError(f"读取进度文件失败: {e}")
    
    def save(self, completed_id: str):
        """
        保存新完成的 listing_id
        
        每次成功下载一个商品后调用此方法，立即写入文件
        
        Args:
            completed_id: 刚完成的商品 listing_id
        """
        self._completed_ids.add(completed_id)
        
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        
        if not self._started_at:
            self._started_at = now
        
        data = {
            "section_url": self.section_url,
            "shop_name": self.shop_name,
            "section_id": self.section_id,
            "started_at": self._started_at,
            "updated_at": now,
            "completed_ids": list(self._completed_ids),
            "total_found": self._total_found
        }
        
        with open(self.progress_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def set_total_found(self, total: int):
        """设置找到的总商品数"""
        self._total_found = total
    
    def is_completed(self, listing_id: str) -> bool:
        """
        检查 listing_id 是否已完成
        
        Args:
            listing_id: 商品 ID
            
        Returns:
            True 如果已完成
        """
        return listing_id in self._completed_ids
    
    def clear(self):
        """清理进度文件"""
        if self.progress_file.exists():
            self.progress_file.unlink()
            self._completed_ids = set()
            self._total_found = 0
            self._started_at = None
    
    @property
    def completed_count(self) -> int:
        """已完成的商品数量"""
        return len(self._completed_ids)
    
    @property
    def total_found(self) -> int:
        """找到的总商品数"""
        return self._total_found

# 复用 real_chrome_scraper 的核心函数
try:
    from .real_chrome_scraper import (
        sanitize_filename,
        get_chrome_path,
        start_chrome_with_debug,
        wait_for_chrome_ready,
        extract_data_with_selenium,
    )
except ImportError:
    from real_chrome_scraper import (
        sanitize_filename,
        get_chrome_path,
        start_chrome_with_debug,
        wait_for_chrome_ready,
        extract_data_with_selenium,
    )


def sanitize_folder_name(name: str) -> str:
    """
    清理文件夹名称，替换文件系统非法字符
    
    Args:
        name: 原始名称
        
    Returns:
        安全的文件夹名称
    """
    # 替换文件系统非法字符为 _
    unsafe_chars = r'/\:*?"<>|'
    result = name
    for char in unsafe_chars:
        result = result.replace(char, '_')
    # 去除首尾空白
    result = result.strip()
    # 合并连续下划线
    result = re.sub(r'_+', '_', result)
    # 去除首尾下划线
    result = result.strip('_')
    return result


def parse_section_url(url: str) -> Tuple[str, str]:
    """
    解析 Section URL，提取 shop_name 和 section_id
    
    支持格式:
    - https://www.etsy.com/shop/{shop_name}?section_id={section_id}
    - https://www.etsy.com/shop/{shop_name}?section_id={section_id}&ref=...
    
    Returns:
        Tuple[shop_name, section_id]
    
    Raises:
        ValueError: 如果 URL 格式无效
    """
    parsed = urlparse(url)
    
    # 提取 shop_name - 从路径 /shop/{shop_name}
    path_match = re.search(r'/shop/([^/?]+)', parsed.path)
    if not path_match:
        raise ValueError(f"无效的 Section URL: 找不到店铺名称\nURL: {url}")
    shop_name = path_match.group(1)
    
    # 提取 section_id - 从查询参数
    query_params = parse_qs(parsed.query)
    if 'section_id' not in query_params:
        raise ValueError(f"无效的 Section URL: 找不到 section_id\nURL: {url}")
    section_id = query_params['section_id'][0]
    
    return shop_name, section_id


def build_page_url(section_url: str, page: int) -> str:
    """
    构造带 page 参数的 Section 页面 URL
    
    Args:
        section_url: 原始 Section URL
        page: 页码（从 1 开始）
        
    Returns:
        带 page=N 参数的 URL
    """
    parsed = urlparse(section_url)
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    query_params['page'] = [str(page)]
    # 将多值参数扁平化为单值
    new_query = urlencode({k: v[0] for k, v in query_params.items()})
    new_url = urlunparse((
        parsed.scheme, parsed.netloc, parsed.path,
        parsed.params, new_query, ''
    ))
    return new_url


def extract_product_links(driver, section_url: str, total_items: int = 0) -> List[str]:
    """
    从 Section 页面提取所有商品链接（基于 URL 参数翻页）
    
    翻页策略：
    1. 先抓取第 1 页，获取实际每页商品数 (items_per_page)
    2. 结合 total_items 计算总页数: ceil(total_items / items_per_page)
    3. 从第 2 页开始逐页构造 URL 访问
    
    Args:
        driver: Selenium WebDriver 实例
        section_url: Section 页面 URL
        total_items: Section 总商品数（用于计算总页数，0 则逐页探测）
        
    Returns:
        商品 listing_id 列表
    """
    from selenium.webdriver.common.by import By
    
    all_listing_ids = []
    seen_ids = set()
    items_per_page = 0  # 从第一页动态获取
    total_pages = None
    current_page = 1
    
    print(f"\n📊 Section 总商品数: {total_items}" if total_items > 0 else "\n📊 总商品数未知，将逐页探测")
    
    while True:
        # 构造当前页 URL
        page_url = build_page_url(section_url, current_page)
        
        if total_pages is not None:
            print(f"\n📄 正在处理第 {current_page}/{total_pages} 页...")
        else:
            print(f"\n📄 正在处理第 {current_page} 页...")
        
        # 导航到当前页
        driver.get(page_url)
        time.sleep(3)  # 等待页面加载
        
        # 滚动页面以触发懒加载
        scroll_page(driver)
        
        # 提取当前页的商品 listing_id
        try:
            product_cards = driver.find_elements(
                By.CSS_SELECTOR, 
                'div.v2-listing-card[data-listing-id]'
            )
            
            page_ids = []
            for card in product_cards:
                listing_id = card.get_attribute('data-listing-id')
                if listing_id and listing_id not in seen_ids:
                    seen_ids.add(listing_id)
                    page_ids.append(listing_id)
                    all_listing_ids.append(listing_id)
            
            print(f"  ✓ 本页找到 {len(page_ids)} 个新商品")
            
            # 如果本页无新商品，停止翻页
            if not page_ids:
                print("  → 本页无新商品，停止翻页")
                break
            
            # 第一页抓取完成后，动态计算每页商品数和总页数
            if current_page == 1 and total_items > 0:
                items_per_page = len(page_ids)
                total_pages = math.ceil(total_items / items_per_page)
                print(f"  📊 每页 {items_per_page} 个商品，预计共 {total_pages} 页")
                
                # 如果只有 1 页，直接结束
                if total_pages <= 1:
                    print(f"  → 仅 1 页，无需翻页")
                    break
            
        except Exception as e:
            print(f"  ✗ 提取商品失败: {e}")
            break
        
        # 检查是否已到达最后一页
        if total_pages is not None:
            if current_page >= total_pages:
                print(f"  → 已到达最后一页 ({current_page}/{total_pages})")
                break
        
        current_page += 1
        time.sleep(2)  # 翻页间延迟
    
    return all_listing_ids


def scroll_page(driver, scroll_times: int = 5):
    """
    滚动页面以触发懒加载
    
    Args:
        driver: Selenium WebDriver 实例
        scroll_times: 滚动次数
    """
    for i in range(scroll_times):
        # 随机滚动距离
        scroll_distance = random.randint(300, 600)
        driver.execute_script(f"window.scrollBy(0, {scroll_distance})")
        time.sleep(random.uniform(0.3, 0.8))
    
    # 滚动到底部确保所有内容加载
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(1)
    
    # 滚动回顶部
    driver.execute_script("window.scrollTo(0, 0)")
    time.sleep(0.5)


def get_section_info(driver, section_id: str = None) -> Tuple[str, int]:
    """
    获取 Section 名称和总商品数
    
    支持两种页面布局：
    - 大屏幕 (lg): 侧边栏用 <li data-section-id="..."> 列表
    - 小屏幕 (xs): 下拉菜单用 <button data-section-id="..."> 按钮
    
    Args:
        driver: Selenium WebDriver 实例
        section_id: 可选，指定要查找的 section_id
        
    Returns:
        Tuple[section_name, total_items]
    """
    from selenium.webdriver.common.by import By
    
    section_name = "section"
    total_items = 0
    
    if section_id:
        # 方法1: 小屏幕下拉菜单 - 查找 button[data-section-id]
        try:
            button = driver.find_element(
                By.CSS_SELECTOR,
                f'button[data-section-id="{section_id}"]'
            )
            # 按钮文本格式: "Canvas (41)"
            button_text = button.text.strip()
            # 解析 "名称 (数量)" 格式
            match = re.match(r'^(.+?)\s*\((\d+)\)\s*$', button_text)
            if match:
                section_name = match.group(1).strip()
                total_items = int(match.group(2))
                print(f"  ✓ 从下拉菜单获取: {section_name} ({total_items} 件商品)")
                return section_name, total_items
        except Exception as e:
            print(f"  方法1 (button data-section-id) 未匹配: {type(e).__name__}")
        
        # 方法2: 大屏幕侧边栏 - 查找 li[data-section-id]
        try:
            tab = driver.find_element(
                By.CSS_SELECTOR,
                f'li[data-section-id="{section_id}"]'
            )
            spans = tab.find_elements(By.CSS_SELECTOR, 'span')
            if len(spans) >= 2:
                section_name = spans[0].text.strip()
                count_text = spans[1].text.strip()
                count_match = re.search(r'(\d+)', count_text)
                if count_match:
                    total_items = int(count_match.group(1))
                print(f"  ✓ 从侧边栏获取: {section_name} ({total_items} 件商品)")
                return section_name, total_items
        except Exception as e:
            print(f"  方法2 (li data-section-id) 未匹配: {type(e).__name__}")
    
    # 方法3: 小屏幕 - 从下拉菜单触发按钮获取当前选中项
    try:
        trigger = driver.find_element(
            By.CSS_SELECTOR,
            '.wt-menu__trigger .wt-menu__trigger__label'
        )
        trigger_text = trigger.text.strip()
        # 格式: "Canvas (41)"
        match = re.match(r'^(.+?)\s*\((\d+)\)\s*$', trigger_text)
        if match:
            section_name = match.group(1).strip()
            total_items = int(match.group(2))
            print(f"  ✓ 从下拉菜单触发器获取: {section_name} ({total_items} 件商品)")
            return section_name, total_items
    except Exception as e:
        print(f"  方法3 (menu trigger) 未匹配: {type(e).__name__}")
    
    # 方法4: 查找选中的 tab（大屏幕侧边栏备用方案）
    selectors = [
        'li.wt-tab__item[aria-selected="true"]',
        'li.wt-tab__item.is-selected',
        'li[role="tab"][aria-selected="true"]',
        'li[role="tab"].is-selected',
    ]
    
    for selector in selectors:
        try:
            selected_tab = driver.find_element(By.CSS_SELECTOR, selector)
            if selected_tab:
                spans = selected_tab.find_elements(By.CSS_SELECTOR, 'span')
                if len(spans) >= 2:
                    section_name = spans[0].text.strip()
                    count_text = spans[1].text.strip()
                    count_match = re.search(r'(\d+)', count_text)
                    if count_match:
                        total_items = int(count_match.group(1))
                    print(f"  ✓ 从侧边栏获取: {section_name} ({total_items} 件商品)")
                    return section_name, total_items
        except:
            continue
    
    print(f"  ⚠️ 未能获取 Section 信息，将使用默认值")
    
    return section_name, total_items


class ImageNameTracker:
    """
    跟踪图片命名，处理同名商品
    
    同名商品处理规则：
    - 第一个商品: poster-1.jpg, poster-2.jpg
    - 第二个同名: poster-1(1).jpg, poster-2(1).jpg
    - 第三个同名: poster-1(2).jpg, poster-2(2).jpg
    """
    
    def __init__(self):
        # 记录每个商品名称出现的次数
        self.name_counts: Dict[str, int] = defaultdict(int)
    
    def get_suffix(self, product_name: str) -> str:
        """
        获取文件名后缀
        
        Args:
            product_name: 商品标题（清理后的）
            
        Returns:
            后缀字符串，如 "" 或 "(1)" 或 "(2)"
        """
        count = self.name_counts[product_name]
        self.name_counts[product_name] += 1
        
        if count == 0:
            return ""
        else:
            return f"({count})"
    
    def generate_filename(self, product_name: str, image_index: int, ext: str = "jpg") -> str:
        """
        生成图片文件名
        
        注意：这个方法应该在处理完一个商品的所有图片后调用一次 get_suffix
        然后用返回的后缀生成所有图片文件名
        
        Args:
            product_name: 商品标题（原始）
            image_index: 图片序号（从 1 开始）
            ext: 文件扩展名
            
        Returns:
            完整文件名，如 "poster-1.jpg" 或 "poster-1(1).jpg"
        """
        safe_name = sanitize_filename(product_name)
        suffix = self.get_suffix(safe_name) if image_index == 1 else self._last_suffix
        
        # 保存后缀供同一商品的其他图片使用
        if image_index == 1:
            self._last_suffix = suffix
        
        return f"{safe_name}-{image_index}{suffix}.{ext}"


def download_images_to_section(
    images: List[str], 
    product_name: str, 
    output_dir: Path,
    name_tracker: ImageNameTracker,
    image_selection: List[int] = None,
    filter_words: List[str] = None
) -> int:
    """
    下载商品图片到 Section 目录
    
    Args:
        images: 图片 URL 列表
        product_name: 商品名称
        output_dir: 输出目录
        name_tracker: 文件名跟踪器
        image_selection: 要下载的图片序号列表（1-indexed），None 表示全部
        filter_words: 从标题中过滤的词汇列表
        
    Returns:
        成功下载的图片数量
    """
    if not images:
        return 0
    
    # 应用标题过滤
    try:
        from .utils import filter_title
    except ImportError:
        from utils import filter_title
    display_name = product_name
    if filter_words:
        display_name = filter_title(product_name, filter_words)
    
    safe_name = sanitize_filename(display_name)
    suffix = name_tracker.get_suffix(safe_name)
    
    # 确定要下载的图片
    if image_selection:
        valid_indices = [i for i in image_selection if 1 <= i <= len(images)]
        skipped_indices = [i for i in image_selection if i > len(images)]
        
        if skipped_indices:
            print(f"    ⚠️ 跳过不存在的序号: {skipped_indices}")
        
        if not valid_indices:
            print("    ⚠️ 没有有效的图片序号")
            return 0
        
        download_list = [(i, images[i-1]) for i in valid_indices]
    else:
        download_list = [(i+1, url) for i, url in enumerate(images)]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://www.etsy.com/"
    }
    
    downloaded = 0
    for idx, url in download_list:
        try:
            # 获取文件扩展名
            ext = url.split('.')[-1].split('?')[0] or 'jpg'
            if ext not in ['jpg', 'jpeg', 'png', 'gif', 'webp']:
                ext = 'jpg'
            
            # 生成文件名
            filename = f"{safe_name}-{idx}{suffix}.{ext}"
            filepath = output_dir / filename
            
            # 下载图片
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                filepath.write_bytes(resp.content)
                print(f"    ✓ {filename}")
                downloaded += 1
            else:
                print(f"    ✗ {filename} (HTTP {resp.status_code})")
                
        except Exception as e:
            print(f"    ✗ 图片 {idx} 下载失败: {e}")
        
        # 短暂延迟
        time.sleep(random.uniform(0.2, 0.5))
    
    return downloaded


def process_product(driver, listing_id: str, output_dir: Path, name_tracker: ImageNameTracker,
                    image_selection: List[int] = None, filter_words: List[str] = None) -> bool:
    """
    处理单个商品：导航、提取数据、下载图片
    
    Args:
        driver: Selenium WebDriver 实例
        listing_id: 商品 ID
        output_dir: 输出目录
        name_tracker: 文件名跟踪器
        image_selection: 要下载的图片序号列表
        filter_words: 标题过滤词列表
        
    Returns:
        是否成功处理
    """
    product_url = f"https://www.etsy.com/listing/{listing_id}"
    
    try:
        # 导航到商品页面
        driver.get(product_url)
        time.sleep(random.uniform(2, 4))  # 随机延迟
        
        # 使用 real_chrome_scraper 的数据提取函数
        # 但我们需要跳过验证检测（因为已经在 Section 页面验证过了）
        data = extract_product_data_silent(driver)
        
        if not data or not data.get('title'):
            print(f"    ⚠️ 无法提取商品数据")
            return False
        
        # 下载图片
        images = data.get('images', [])
        if images:
            downloaded = download_images_to_section(
                images, 
                data['title'], 
                output_dir, 
                name_tracker,
                image_selection=image_selection,
                filter_words=filter_words
            )
            total_to_download = len(image_selection) if image_selection else len(images)
            print(f"    → 下载了 {downloaded}/{total_to_download} 张图片")
            return downloaded > 0
        else:
            print(f"    ⚠️ 没有找到图片")
            return False
            
    except Exception as e:
        print(f"    ✗ 处理失败: {e}")
        return False


def extract_product_data_silent(driver) -> Optional[Dict]:
    """
    静默提取商品数据（不显示验证提示）
    这是 extract_data_with_selenium 的简化版本
    
    Args:
        driver: Selenium WebDriver 实例
        
    Returns:
        商品数据字典
    """
    from selenium.webdriver.common.by import By
    
    data = {}
    
    # 模拟人类滚动
    for _ in range(2):
        scroll_distance = random.randint(200, 400)
        driver.execute_script(f"window.scrollBy(0, {scroll_distance})")
        time.sleep(random.uniform(0.3, 0.8))
    driver.execute_script("window.scrollTo(0, 0)")
    time.sleep(0.5)
    
    # 提取标题
    try:
        title_el = driver.find_element(By.CSS_SELECTOR, 'h1[data-buy-box-listing-title="true"]')
        data['title'] = title_el.text.strip()
    except:
        try:
            title_el = driver.find_element(By.TAG_NAME, 'h1')
            data['title'] = title_el.text.strip()
        except:
            data['title'] = None
    
    # 提取图片 - 使用优化后的方法
    images = []
    seen_ids = set()
    
    def extract_image_id(url):
        match = re.search(r'/il_[^.]+\.(\d+)_', url)
        return match.group(1) if match else None
    
    def convert_to_fullsize(url):
        return re.sub(r'il_[^.]+\.', 'il_fullxfull.', url)
    
    # 方法0: data-src-zoom-image（最优先）
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
    except:
        pass
    
    # 方法1: 画廊区域（备选）
    if not images:
        gallery_selectors = [
            'div[data-component="listing-page-image-carousel"] img',
            'ul[data-carousel-pagination-list] img',
            'ul.carousel-pane-list img[src*="il_"]',
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
                            images.append(convert_to_fullsize(src))
                if images:
                    break
            except:
                continue
    
    # 去重并限制数量
    data['images'] = list(dict.fromkeys(images))[:15]
    
    return data


def process_all_products(
    driver, 
    listing_ids: List[str], 
    output_dir: Path,
    delay: float = 2.0,
    image_selection: List[int] = None,
    filter_words: List[str] = None,
    progress: ScrapeProgress = None
) -> Tuple[int, int]:
    """
    批量处理所有商品
    
    Args:
        driver: Selenium WebDriver 实例
        listing_ids: 商品 ID 列表
        output_dir: 输出目录
        delay: 商品间延迟（秒）
        image_selection: 要下载的图片序号列表
        filter_words: 标题过滤词列表
        progress: 进度管理器（可选）
        
    Returns:
        Tuple[成功数, 失败数]
    """
    total = len(listing_ids)
    success_count = 0
    fail_count = 0
    name_tracker = ImageNameTracker()
    
    print(f"\n{'='*60}")
    print(f"开始处理 {total} 个商品")
    if image_selection:
        print(f"图片选择: {image_selection}")
    if filter_words:
        print(f"标题过滤: {filter_words}")
    print(f"{'='*60}")
    
    for i, listing_id in enumerate(listing_ids, 1):
        print(f"\n[{i}/{total}] 商品 ID: {listing_id}")
        
        if process_product(driver, listing_id, output_dir, name_tracker,
                          image_selection=image_selection, filter_words=filter_words):
            success_count += 1
            # 成功后立即保存进度
            if progress:
                progress.save(listing_id)
        else:
            fail_count += 1
        
        # 随机延迟，避免被封
        if i < total:
            wait_time = delay + random.uniform(-0.5, 1.0)
            wait_time = max(1.0, wait_time)  # 至少等待 1 秒
            print(f"    ⏳ 等待 {wait_time:.1f} 秒...")
            time.sleep(wait_time)
    
    return success_count, fail_count


def main():
    """主入口"""
    parser = argparse.ArgumentParser(
        description="批量抓取 Etsy 店铺 Section 下的所有商品图片",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 单个 Section
  etsy-section "https://www.etsy.com/shop/JayDeeDesignShop?section_id=52625173"
  
  # 多个 Section（空格分隔）
  etsy-section "https://www.etsy.com/shop/Shop1?section_id=111" "https://www.etsy.com/shop/Shop2?section_id=222"
  
  # 带选项
  etsy-section "https://www.etsy.com/shop/MyShop?section_id=12345" --output my_images
  etsy-section "https://www.etsy.com/shop/MyShop?section_id=12345" --delay 3

断点续传:
  - 默认启用断点续传，中断后重新运行会自动跳过已完成的商品
  - 使用 --no-resume 从头开始，忽略之前的进度
  - 使用 --clear-progress 清理进度文件后退出

工作流程:
  1. 启动 Chrome 并打开 Section 页面
  2. 你手动完成验证（如果需要）
  3. 按 Enter 开始自动抓取
  4. 自动遍历所有商品并下载图片（多链接会依次处理）
"""
    )
    
    parser.add_argument("urls", nargs="+", help="Etsy Section URL（支持多个）")
    parser.add_argument("--output", "-o", default="output", help="输出目录（默认: output）")
    parser.add_argument("--port", "-p", type=int, default=9222, help="Chrome 调试端口（默认: 9222）")
    parser.add_argument("--delay", "-d", type=float, default=2.0, help="商品间延迟秒数（默认: 2）")
    parser.add_argument("--section-delay", type=float, default=3.0, help="Section 间延迟秒数（默认: 3）")
    parser.add_argument("--images", "-i", default=None,
                        help="指定下载哪些图片，如: '1' 或 '1,3,5' 或 '2-4' 或 '1,3-5,8'")
    parser.add_argument("--filter", "-f", default=None,
                        help="从标题中过滤的词汇，逗号分隔，如: 'Canvas,Poster,Wall Art'")
    
    # 断点续传参数
    resume_group = parser.add_mutually_exclusive_group()
    resume_group.add_argument("--resume", dest="resume", action="store_true", default=True,
                              help="启用断点续传（默认）")
    resume_group.add_argument("--no-resume", dest="resume", action="store_false",
                              help="禁用断点续传，从头开始抓取")
    parser.add_argument("--clear-progress", action="store_true",
                        help="清理进度文件后退出")
    
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
        except ValueError as e:
            print(f"\n❌ {e}")
            sys.exit(1)
    
    if args.filter:
        filter_words = parse_filter_words(args.filter)
    
    # 验证所有 URL 并解析 section 信息
    sections = []
    for url in args.urls:
        try:
            shop_name, section_id = parse_section_url(url)
            sections.append({
                'url': url,
                'shop_name': shop_name,
                'section_id': section_id
            })
        except ValueError as e:
            print(f"\n❌ 无效的 Section URL: {url}")
            print(f"   {e}")
            sys.exit(1)
    
    total_sections = len(sections)
    
    print(f"\n✓ 解析成功: {total_sections} 个 Section")
    for i, s in enumerate(sections, 1):
        print(f"  [{i}] {s['shop_name']} (Section: {s['section_id']})")
    
    # 处理 --clear-progress 参数
    if args.clear_progress:
        cleared = 0
        output_base = Path(args.output)
        for s in sections:
            target_section_id = s['section_id']
            found = False
            # 扫描所有子目录，查找匹配 section_id 的进度文件
            if output_base.exists():
                for subdir in output_base.iterdir():
                    if subdir.is_dir():
                        progress_file = subdir / ".progress.json"
                        if progress_file.exists():
                            try:
                                with open(progress_file, 'r', encoding='utf-8') as f:
                                    data = json.load(f)
                                if data.get('section_id') == target_section_id:
                                    progress_file.unlink()
                                    print(f"✓ 已清理: {progress_file}")
                                    cleared += 1
                                    found = True
                            except Exception:
                                pass
            if not found:
                print(f"⚠️ 未找到 Section {target_section_id} 的进度文件")
        if cleared == 0:
            print("⚠️ 没有找到任何进度文件")
        else:
            print(f"\n✓ 共清理 {cleared} 个进度文件")
        sys.exit(0)
    
    # 显示过滤选项
    if image_selection:
        print(f"  图片选择: {image_selection}")
    if filter_words:
        print(f"  标题过滤: {filter_words}")
    
    print("\n" + "=" * 60)
    print("🛍️  ETSY SECTION SCRAPER")
    print("=" * 60)
    print("批量抓取店铺 Section 下的所有商品图片")
    if total_sections > 1:
        print(f"\n📋 共 {total_sections} 个 Section 待处理")
    print("=" * 60)
    
    # 步骤 1：启动 Chrome
    print("\n📌 步骤 1: 启动 Chrome")
    print("-" * 40)
    print("⚠️  请先关闭所有 Chrome 窗口！")
    input("准备好后按 Enter 继续...")
    
    print("\n启动 Chrome...")
    chrome_process = start_chrome_with_debug(sections[0]['url'], args.port)
    
    print("等待浏览器就绪...")
    if not wait_for_chrome_ready(args.port):
        print("❌ Chrome 启动失败！")
        chrome_process.terminate()
        sys.exit(1)
    
    print("✓ Chrome 已启动！")
    
    # 步骤 2：等待用户完成验证
    print("\n" + "=" * 60)
    print("📌 步骤 2: 完成验证")
    print("-" * 40)
    print("""
在打开的 Chrome 窗口中：

  1. 如果看到验证页面，请完成「我不是机器人」验证
  2. 等待 Section 页面完全加载
  3. 确认能看到商品列表

⏰ 没有时间限制，慢慢来！
""")
    print("=" * 60)
    
    input("\n✋ 验证完成、页面加载好后，按 Enter 继续...")
    
    # 连接到 Chrome
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    
    options = Options()
    options.add_experimental_option("debuggerAddress", f"localhost:{args.port}")
    driver = webdriver.Chrome(options=options)
    
    # 统计
    total_success = 0
    total_fail = 0
    total_skipped = 0
    sections_completed = 0
    
    try:
        for sec_idx, section in enumerate(sections, 1):
            url = section['url']
            shop_name = section['shop_name']
            section_id = section['section_id']
            
            if total_sections > 1:
                print(f"\n{'='*60}")
                print(f"[Section {sec_idx}/{total_sections}] {shop_name}")
                print(f"  Section ID: {section_id}")
                print(f"{'='*60}")
            
            # 如果不是第一个 Section，需要导航到新页面
            if sec_idx > 1:
                try:
                    driver.get(url)
                    time.sleep(2)
                    # 等待页面加载
                    for _ in range(2):
                        scroll_distance = random.randint(200, 400)
                        driver.execute_script(f"window.scrollBy(0, {scroll_distance})")
                        time.sleep(random.uniform(0.3, 0.8))
                    driver.execute_script("window.scrollTo(0, 0)")
                    time.sleep(1)
                except Exception as e:
                    print(f"  ❌ 导航失败: {e}")
                    continue
            
            # 获取 Section 信息（在创建输出目录之前获取 section 名称）
            print(f"\n  📌 获取 Section 信息...")
            section_name, total_items = get_section_info(driver, section_id)
            print(f"    Section: {section_name}")
            print(f"    预计商品数: {total_items}")
            
            # 创建输出目录（使用 section 实际名称）
            if section_name and section_name != "section":
                section_dir_name = sanitize_folder_name(section_name)
            else:
                section_dir_name = f"{shop_name}_{section_id}"
            
            # 同名文件夹冲突检测
            candidate_path = Path(args.output) / section_dir_name
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
            
            output_path = Path(args.output) / section_dir_name
            output_path.mkdir(parents=True, exist_ok=True)
            print(f"  输出目录: {output_path}")
            
            # 初始化进度管理器
            progress = ScrapeProgress(output_path, url, shop_name, section_id)
            
            # 加载已有进度（如果启用断点续传）
            completed_ids = set()
            if args.resume:
                try:
                    completed_ids = progress.load()
                    if completed_ids:
                        print(f"  📋 检测到进度：已完成 {len(completed_ids)} 个商品")
                except ValueError as e:
                    print(f"  ❌ {e}")
                    continue
            
            # 提取商品链接
            print(f"\n  📌 提取商品链接...")
            
            # 提取所有商品链接（传入 total_items 用于计算翻页）
            listing_ids = extract_product_links(driver, url, total_items=total_items)
            
            if not listing_ids:
                print(f"\n  ❌ 没有找到任何商品！")
                continue
            
            print(f"\n  ✓ 共找到 {len(listing_ids)} 个商品")
            
            # 设置总商品数
            progress.set_total_found(len(listing_ids))
            
            # 过滤已完成的商品
            pending_ids = listing_ids
            skipped_count = 0
            if args.resume and completed_ids:
                pending_ids = [lid for lid in listing_ids if lid not in completed_ids]
                skipped_count = len(listing_ids) - len(pending_ids)
                if skipped_count > 0:
                    print(f"\n  📋 断点续传：跳过 {skipped_count} 个已完成商品")
                    total_skipped += skipped_count
            
            if not pending_ids:
                print(f"\n  ✓ 所有商品已完成！")
                sections_completed += 1
                continue
            
            # 确认继续（只有单个 Section 时询问）
            if total_sections == 1:
                print("\n" + "=" * 60)
                confirm = input(f"是否开始下载 {len(pending_ids)} 个商品的图片? (Y/n): ")
                if confirm.lower() == 'n':
                    print("已取消")
                    chrome_process.terminate()
                    sys.exit(0)
            
            # 处理商品
            print(f"\n  📌 下载商品图片 ({len(pending_ids)} 个)...")
            
            success, fail = process_all_products(
                driver, 
                pending_ids, 
                output_path,
                delay=args.delay,
                image_selection=image_selection,
                filter_words=filter_words,
                progress=progress
            )
            
            total_success += success
            total_fail += fail
            
            # Section 完成状态
            if progress.completed_count == len(listing_ids):
                sections_completed += 1
                print(f"\n  ✓ Section 完成！")
            else:
                print(f"\n  📋 进度: {progress.completed_count}/{len(listing_ids)} 完成")
            
            # Section 间延迟
            if sec_idx < total_sections:
                wait_time = args.section_delay + random.uniform(-0.5, 1.0)
                wait_time = max(1.0, wait_time)
                print(f"\n⏳ 等待 {wait_time:.1f} 秒后处理下一个 Section...")
                time.sleep(wait_time)
        
        # 显示最终结果
        print("\n" + "=" * 60)
        print("🎉 完成！")
        print("=" * 60)
        if total_sections > 1:
            print(f"  Section 总数: {total_sections}")
            print(f"  完成的 Section: {sections_completed}")
        print(f"  商品成功: {total_success}")
        print(f"  商品失败: {total_fail}")
        if total_skipped > 0:
            print(f"  商品跳过 (断点续传): {total_skipped}")
        print(f"  输出目录: {args.output}")
        
    finally:
        # 询问是否关闭浏览器
        close = input("\n是否关闭 Chrome 浏览器? (y/N): ")
        if close.lower() == 'y':
            chrome_process.terminate()
            print("浏览器已关闭")
        else:
            print("浏览器保持打开")


if __name__ == "__main__":
    main()
