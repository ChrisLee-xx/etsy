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
        _builtin_print(*args, **kwargs)
print = _safe_print


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
    from etsy_scraper.real_chrome_scraper import (
        sanitize_filename,
        get_chrome_path,
        start_chrome_with_debug,
        wait_for_chrome_ready,
        create_patched_driver,
        get_random_ua,
        _is_access_blocked,
        _is_browser_disconnected,
        _restart_chrome_fresh,
        _DriverContext,
        _extract_product_images,
    )
except ImportError:
    from real_chrome_scraper import (  # type: ignore
        sanitize_filename,
        get_chrome_path,
        start_chrome_with_debug,
        wait_for_chrome_ready,
        create_patched_driver,
        get_random_ua,
        _is_access_blocked,
        _is_browser_disconnected,
        _restart_chrome_fresh,
        _DriverContext,
        _extract_product_images,
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


def parse_shop_url(url: str) -> str:
    """
    解析店铺 URL，提取 shop_name
    
    支持格式:
    - https://www.etsy.com/shop/{shop_name}
    - https://www.etsy.com/shop/{shop_name}?ref=...
    
    Returns:
        shop_name 字符串
    
    Raises:
        ValueError: 如果 URL 格式无效
    """
    parsed = urlparse(url)
    path_match = re.search(r'/shop/([^/?]+)', parsed.path)
    if not path_match:
        raise ValueError(f"无效的店铺 URL: 找不到店铺名称\nURL: {url}")
    return path_match.group(1)


def is_shop_url(url: str) -> bool:
    """判断是否为店铺级 URL（无 section_id 的 /shop/xxx 链接）"""
    parsed = urlparse(url)
    if '/shop/' not in parsed.path:
        return False
    query_params = parse_qs(parsed.query)
    return 'section_id' not in query_params


def is_search_url(url: str) -> bool:
    """判断是否为搜索页 URL（/search?q=...）"""
    parsed = urlparse(url)
    return '/search' in parsed.path


def parse_search_url(url: str) -> str:
    """
    解析搜索页 URL，提取搜索关键词

    支持格式:
    - https://www.etsy.com/search?q=car+poster&ref=pagination&page=1
    - https://www.etsy.com/search?q=car%20poster

    Returns:
        搜索关键词字符串（如 "car poster"），无 q 参数时返回 "search_results"
    """
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    if 'q' in query_params and query_params['q'][0]:
        return query_params['q'][0]
    return "search_results"


def get_shop_info(driver, shop_name: str) -> Tuple[str, int]:
    """
    从店铺页面获取信息：店铺显示名称 + 总商品数
    
    Args:
        driver: Selenium WebDriver 实例（已在店铺页面上）
        shop_name: 店铺名称（从 URL 提取）
        
    Returns:
        Tuple[显示名称, 总商品数]
    """
    from selenium.webdriver.common.by import By
    
    try:
        # 尝试从页面标题或 header 获取店铺显示名
        title_elements = driver.find_elements(
            By.CSS_SELECTOR,
            'h1.shop-name, [data-shop-name], .shop-title'
        )
        display_name = None
        if title_elements:
            display_name = title_elements[0].text.strip()
        
        # 尝试获取总商品数
        total_items = 0
        count_elements = driver.find_elements(
            By.CSS_SELECTOR,
            '[data-product-count], .listing-count, .results-count'
        )
        for el in count_elements:
            text = el.text.strip()
            match = re.search(r'(\d[\d,]*)', text)
            if match:
                total_items = int(match.group(1).replace(',', ''))
                break
        
        return display_name or shop_name, total_items
        
    except Exception as e:
        print(f"  ⚠️ 获取店铺信息失败: {e}")
        return shop_name, 0


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


def extract_product_links(driver, section_url: str, total_items: int = 0,
                          stop_check=None, max_count: int = -1) -> List[str]:
    """
    从 Section/搜索 页面提取所有商品链接（基于 URL 参数翻页）
    
    翻页策略：
    1. 先抓取第 1 页，获取实际每页商品数 (items_per_page)
    2. 结合 total_items 计算总页数: ceil(total_items / items_per_page)
    3. 从第 2 页开始逐页构造 URL 访问
    
    Args:
        driver: Selenium WebDriver 实例
        section_url: Section/搜索 页面 URL
        total_items: 总商品数（用于计算总页数，0 则逐页探测）
        stop_check: 可选的停止检查回调函数，返回 True 时提前退出翻页循环
        max_count: 最大抓取数量，-1 表示无限（主要用于搜索页）
        
    Returns:
        商品 listing_id 列表
    """
    from selenium.webdriver.common.by import By
    
    all_listing_ids = []
    seen_ids = set()
    items_per_page = 0  # 从第一页动态获取
    total_pages = None
    current_page = 1
    
    if max_count > 0:
        print(f"\n📊 最大抓取数量: {max_count}（达到后自动停止）")
    elif total_items > 0:
        print(f"\n📊 Section 总商品数: {total_items}")
    else:
        print(f"\n📊 总商品数未知，将逐页探测")
    
    while True:
        # 检查停止信号
        if stop_check and stop_check():
            print("  → 收到停止信号，停止翻页")
            break

        # 构造当前页 URL
        page_url = build_page_url(section_url, current_page)
        
        if total_pages is not None:
            print(f"\n📄 正在处理第 {current_page}/{total_pages} 页...")
        else:
            print(f"\n📄 正在处理第 {current_page} 页...")
        
        # 导航到当前页并滚动触发懒加载；Chrome 断连时向外抛出，交给上层重启恢复
        try:
            driver.get(page_url)
            time.sleep(3)  # 等待页面加载
            scroll_page(driver)
        except Exception as e:
            if _is_browser_disconnected(e):
                raise
            print(f"  ✗ 页面加载失败: {e}")
            break
        
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
            
            print(f"  ✓ 本页找到 {len(page_ids)} 个新商品（累计 {len(all_listing_ids)}）")
            
            # 如果本页无新商品，停止翻页
            if not page_ids:
                print("  → 本页无新商品，停止翻页")
                break
            
            # 达到最大抓取数量，截断并停止
            if max_count > 0 and len(all_listing_ids) >= max_count:
                all_listing_ids = all_listing_ids[:max_count]
                print(f"  → 已达到最大抓取数量 {max_count}，停止翻页")
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
            if _is_browser_disconnected(e):
                raise
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
            if _is_browser_disconnected(e):
                raise
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
            if _is_browser_disconnected(e):
                raise
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
        if _is_browser_disconnected(e):
            raise
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
        except Exception as e:
            if _is_browser_disconnected(e):
                raise
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
        self._last_suffix: str = ""
    
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
        from etsy_scraper.utils import filter_title
    except ImportError:
        from utils import filter_title  # type: ignore
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
            print(f"    ⚠️ 选择的图片序号超出范围，默认下载第1张")
            download_list = [(1, images[0])] if images else []
        else:
            download_list = [(i, images[i-1]) for i in valid_indices]
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
            
            # 下载图片并验证内容是否为真实图片
            resp = requests.get(url, headers=headers, timeout=30)
            try:
                from etsy_scraper.utils import validate_image_response, save_failed_image
            except ImportError:
                from utils import validate_image_response, save_failed_image  # type: ignore
            
            valid, reason = validate_image_response(resp)
            if valid:
                filepath.write_bytes(resp.content)
                print(f"    ✓ {filename} ({len(resp.content)//1024}KB)")
                downloaded += 1
            else:
                save_failed_image(output_dir, product_name, url, idx, reason)
                print(f"    ✗ {filename} ({reason})，已保存链接待二次抓取")
                
        except Exception as e:
            try:
                from etsy_scraper.utils import save_failed_image
            except ImportError:
                from utils import save_failed_image  # type: ignore
            save_failed_image(output_dir, product_name, url, idx, str(e))
            print(f"    ✗ 图片 {idx} 下载失败: {e}，已保存链接待二次抓取")
        
        # 短暂延迟
        time.sleep(random.uniform(0.2, 0.5))
    
    return downloaded


def process_product(ctx, listing_id: str, output_dir: Path, name_tracker: ImageNameTracker,
                    image_selection: List[int] = None, filter_words: List[str] = None,
                    retry_on_disconnect: bool = True, section_url: str = None) -> bool:
    """
    处理单个商品：从 section 页面导航到商品、提取数据、下载图片、回到 section 页面

    Args:
        ctx: _DriverContext（包含 driver 和 chrome_process，封锁时可自动重启）
        listing_id: 商品 ID
        output_dir: 输出目录
        name_tracker: 文件名跟踪器
        image_selection: 要下载的图片序号列表
        filter_words: 标题过滤词列表
        retry_on_disconnect: 断连后是否重试
        section_url: Section 页面 URL（用于回到 section 页面和封锁恢复）

    Returns:
        是否成功处理
    """
    import time as _time
    product_url = f"https://www.etsy.com/listing/{listing_id}"
    t_start = _time.time()
    _navigated_back = False  # 标记是否已导航回 section 页面（防止递归时重复导航）

    try:
        # 步骤1：从 section 页面导航到商品页面
        print(f"    → 导航到商品页...")
        t1 = _time.time()
        ctx.driver.get(product_url)
        nav_elapsed = _time.time() - t1
        print(f"    → 导航完成 ({nav_elapsed:.1f}s)")

        wait_time = random.uniform(2, 4)
        time.sleep(wait_time)

        # 步骤2：检测访问限制 → 轻量恢复后重新访问商品
        if _is_access_blocked(ctx.driver):
            print(f"    ⚠️ 检测到访问被限制，尝试恢复...")
            if not ctx.handle_block(product_url, section_url=section_url):
                print(f"    ❌ 封锁恢复失败")
                return False
            # 恢复成功后（已在 section 页面），重新导航到商品
            print(f"    → 重新导航到商品页...")
            ctx.driver.get(product_url)
            time.sleep(random.uniform(2, 4))
            if _is_access_blocked(ctx.driver):
                print(f"    ❌ 恢复后仍被限制")
                return False

        # 步骤3：提取商品数据
        print(f"    → 提取商品数据...")
        t2 = _time.time()
        data = extract_product_data_silent(ctx.driver)
        extract_elapsed = _time.time() - t2
        print(f"    → 数据提取完成 ({extract_elapsed:.1f}s)")

        if not data or not data.get('title'):
            print(f"    ⚠️ 无法提取商品数据 (title={data.get('title') if data else None})")
            return False

        # 步骤4：下载图片
        images = data.get('images', [])
        if images:
            print(f"    → 找到 {len(images)} 张图片，开始下载...")
            t3 = _time.time()
            downloaded = download_images_to_section(
                images,
                data['title'],
                output_dir,
                name_tracker,
                image_selection=image_selection,
                filter_words=filter_words
            )
            dl_elapsed = _time.time() - t3
            total_to_download = len(image_selection) if image_selection else len(images)
            print(f"    → 下载完成: {downloaded}/{total_to_download} 张 ({dl_elapsed:.1f}s)")
            if downloaded > 0:
                total_elapsed = _time.time() - t_start
                print(f"    ✅ 总耗时 {total_elapsed:.1f}s")
                return True
            else:
                print(f"    ⚠️ 所有图片下载均失败")
                return False
        else:
            print(f"    ⚠️ 没有找到图片")
            return False

    except Exception as e:
        elapsed = _time.time() - t_start
        err_type = type(e).__name__
        if _is_browser_disconnected(e) and retry_on_disconnect:
            print(f"    🔌 Chrome 连接断开 ({err_type}: {e})，重启后重试当前商品...")
            if ctx.handle_block(product_url, section_url=section_url, immediate=True):
                # 递归调用会自行处理 finally 导航，标记跳过外层
                _navigated_back = True
                return process_product(
                    ctx,
                    listing_id,
                    output_dir,
                    name_tracker,
                    image_selection=image_selection,
                    filter_words=filter_words,
                    retry_on_disconnect=False,
                    section_url=section_url,
                )
        print(f"    ✗ 处理失败 [{err_type}] ({elapsed:.1f}s): {e}")
        return False

    finally:
        # 无论成功或失败，都回到 section 页面（递归重试时跳过，避免重复导航）
        if section_url and not _navigated_back:
            try:
                ctx.driver.get(section_url)
                time.sleep(2)
            except Exception:
                pass


def extract_product_data_silent(driver) -> Optional[Dict]:
    """
    静默提取商品数据（标题 + 图片）。
    批量模式使用，图片提取复用 _extract_product_images。
    """
    from selenium.webdriver.common.by import By

    data = {}

    # 提取标题
    try:
        title_el = driver.find_element(By.CSS_SELECTOR, 'h1[data-buy-box-listing-title="true"]')
        data['title'] = title_el.get_attribute('textContent').strip()
    except Exception:
        try:
            title_el = driver.find_element(By.TAG_NAME, 'h1')
            data['title'] = title_el.get_attribute('textContent').strip()
        except Exception:
            data['title'] = None

    # 图片（使用共享提取函数，包含全部 4 种回退方法）
    data['images'] = _extract_product_images(driver)

    return data


def process_all_products(
    ctx,
    listing_ids: List[str], 
    output_dir: Path,
    delay: float = 2.0,
    image_selection: List[int] = None,
    filter_words: List[str] = None,
    progress: ScrapeProgress = None,
    section_url: str = None
) -> Tuple[int, int]:
    """
    批量处理所有商品
    
    Args:
        ctx: _DriverContext（封锁时可自动重启 Chrome）
        listing_ids: 商品 ID 列表
        output_dir: 输出目录
        delay: 商品间延迟（秒）
        image_selection: 要下载的图片序号列表
        filter_words: 标题过滤词列表
        progress: 进度管理器（可选）
        section_url: Section 页面 URL（用于回到 section 页面和封锁恢复）
        
    Returns:
        Tuple[成功数, 失败数]
    """
    total = len(listing_ids)
    success_count = 0
    fail_count = 0
    consecutive_fails = 0
    max_consecutive_fails = 3
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
        
        if process_product(ctx, listing_id, output_dir, name_tracker,
                          image_selection=image_selection, filter_words=filter_words,
                          section_url=section_url):
            success_count += 1
            consecutive_fails = 0
            if progress:
                progress.save(listing_id)
        else:
            fail_count += 1
            consecutive_fails += 1
            
            # 连续失败达到阈值，很可能是被封了
            if consecutive_fails >= max_consecutive_fails:
                product_url = f"https://www.etsy.com/listing/{listing_id}"
                print(f"\n    ⚠️ 连续 {consecutive_fails} 个商品失败，疑似被封锁，尝试重启 Chrome...")
                if ctx.handle_block(product_url, section_url=section_url):
                    print(f"    ✅ 已恢复，继续抓取")
                    consecutive_fails = 0
                else:
                    print(f"    ❌ 无法恢复，停止抓取")
                    break
        
        if i < total:
            wait_time = delay + random.uniform(-0.5, 1.0)
            wait_time = max(1.0, wait_time)
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
  1. 自动启动 Chrome 并打开 Section 页面
  2. 自动遍历所有商品并下载图片
  3. 遇到访问限制自动重启 Chrome 恢复
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
        from etsy_scraper.utils import parse_image_selection, parse_filter_words
    except ImportError:
        from utils import parse_image_selection, parse_filter_words  # type: ignore
    
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
    
    print("启动 Chrome...")
    chrome_process = start_chrome_with_debug(sections[0]['url'], args.port)
    
    print("等待浏览器就绪...")
    if not wait_for_chrome_ready(args.port):
        print("❌ Chrome 启动失败！请先关闭所有 Chrome 窗口后重试。")
        chrome_process.terminate()
        sys.exit(1)
    
    print("✅ Chrome 已启动！")
    
    driver = create_patched_driver(args.port)
    ctx = _DriverContext(driver, chrome_process, args.port)
    
    # 等待页面加载
    time.sleep(3)
    
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
                    ctx.driver.get(url)
                    time.sleep(2)
                    for _ in range(2):
                        scroll_distance = random.randint(200, 400)
                        ctx.driver.execute_script(f"window.scrollBy(0, {scroll_distance})")
                        time.sleep(random.uniform(0.3, 0.8))
                    ctx.driver.execute_script("window.scrollTo(0, 0)")
                    time.sleep(1)
                except Exception as e:
                    print(f"  ❌ 导航失败: {e}，尝试重启 Chrome...")
                    if ctx.handle_block(url, section_url=url, immediate=_is_browser_disconnected(e)):
                        print("  ✅ 已恢复")
                    else:
                        print("  ❌ 无法恢复，跳过此 Section")
                        continue
            
            # 获取 Section 信息（在创建输出目录之前获取 section 名称），Chrome 断连时自动恢复
            print(f"\n  📌 获取 Section 信息...")
            try:
                section_name, total_items = get_section_info(ctx.driver, section_id)
            except Exception as e:
                print(f"  ❌ 获取 Section 信息失败: {e}，尝试重启 Chrome...")
                if ctx.handle_block(url, section_url=url, immediate=_is_browser_disconnected(e)):
                    print("  ✅ 已恢复，重试...")
                    try:
                        section_name, total_items = get_section_info(ctx.driver, section_id)
                    except Exception as retry_error:
                        print(f"  ❌ 重试仍失败: {retry_error}，跳过此 Section")
                        continue
                else:
                    print("  ❌ 无法恢复，跳过此 Section")
                    continue
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
            try:
                listing_ids = extract_product_links(ctx.driver, url, total_items=total_items)
            except Exception as e:
                print(f"  ❌ 提取商品链接失败: {e}，尝试重启 Chrome...")
                if ctx.handle_block(url, section_url=url, immediate=_is_browser_disconnected(e)):
                    print("  ✅ 已恢复，重试...")
                    try:
                        listing_ids = extract_product_links(ctx.driver, url, total_items=total_items)
                    except Exception as retry_error:
                        print(f"  ❌ 重试仍失败: {retry_error}，跳过此 Section")
                        continue
                else:
                    print("  ❌ 无法恢复，跳过此 Section")
                    continue
            
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
            
            # 处理商品
            print(f"\n  📌 下载商品图片 ({len(pending_ids)} 个)...")
            
            success, fail = process_all_products(
                ctx, 
                pending_ids, 
                output_path,
                delay=args.delay,
                image_selection=image_selection,
                filter_words=filter_words,
                progress=progress,
                section_url=url
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
        if ctx.chrome_process and ctx.chrome_process.poll() is None:
            try:
                ctx.chrome_process.terminate()
            except Exception:
                pass
        print("浏览器已关闭")


if __name__ == "__main__":
    main()
