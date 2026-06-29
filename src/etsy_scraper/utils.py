"""
共享工具函数 - 图片选择和标题过滤

供 real_chrome_scraper.py 和 section_scraper.py 共用
"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import List


def parse_image_selection(spec: str) -> List[int]:
    """
    解析图片选择规格字符串
    
    支持格式:
    - 单个序号: "1" → [1]
    - 多个序号: "1,3,5" → [1, 3, 5]
    - 范围: "2-4" → [2, 3, 4]
    - 混合: "1,3-5,8" → [1, 3, 4, 5, 8]
    
    Args:
        spec: 图片选择规格字符串
        
    Returns:
        排序后的唯一图片序号列表（1-indexed）
        
    Raises:
        ValueError: 如果格式无效
    """
    if not spec or not spec.strip():
        return []
    
    indices = set()
    parts = spec.strip().split(',')
    
    for part in parts:
        part = part.strip()
        if not part:
            continue
            
        # 检查是否是范围格式 (如 "2-4")
        if '-' in part:
            range_match = re.match(r'^(\d+)-(\d+)$', part)
            if not range_match:
                raise ValueError(
                    f"无效的范围格式: '{part}'\n"
                    f"正确格式示例: '2-4' 表示第2到4张"
                )
            start, end = int(range_match.group(1)), int(range_match.group(2))
            if start < 1:
                raise ValueError(f"图片序号必须从 1 开始，不能是 {start}")
            if start > end:
                raise ValueError(f"范围起始值 {start} 不能大于结束值 {end}")
            indices.update(range(start, end + 1))
        else:
            # 单个数字
            if not part.isdigit():
                raise ValueError(
                    f"无效的图片序号: '{part}'\n"
                    f"正确格式示例: '1' 或 '1,3,5' 或 '2-4' 或 '1,3-5,8'"
                )
            num = int(part)
            if num < 1:
                raise ValueError(f"图片序号必须从 1 开始，不能是 {num}")
            indices.add(num)
    
    return sorted(indices)


def filter_title(title: str, filter_words: List[str]) -> str:
    """
    从标题中过滤屏蔽词
    
    过滤规则:
    - 大小写不敏感
    - 移除匹配的词汇
    - 清理多余空格
    - 如果结果为空，返回 "untitled"
    
    Args:
        title: 原始商品标题
        filter_words: 要过滤的词汇列表
        
    Returns:
        过滤后的标题
    """
    if not title:
        return "untitled"
    
    if not filter_words:
        return title
    
    result = title
    
    for word in filter_words:
        if not word:
            continue
        # 大小写不敏感的替换
        # 使用正则表达式确保匹配完整词汇（避免 "art" 误删 "heart" 中的部分）
        # 但为了简单起见，先用简单的替换，后续可以优化
        pattern = re.compile(re.escape(word), re.IGNORECASE)
        result = pattern.sub('', result)
    
    # 清理多余空格
    result = ' '.join(result.split())
    
    # 如果结果为空，返回默认值
    if not result.strip():
        return "untitled"
    
    return result.strip()


def parse_filter_words(spec: str) -> List[str]:
    """
    解析屏蔽词规格字符串
    
    格式: 逗号分隔的词汇列表
    示例: "Canvas,Poster,Wall Art" → ["Canvas", "Poster", "Wall Art"]
    
    Args:
        spec: 屏蔽词规格字符串
        
    Returns:
        清理后的屏蔽词列表
    """
    if not spec or not spec.strip():
        return []
    
    words = []
    for word in spec.split(','):
        word = word.strip()
        if word:
            words.append(word)
    
    return words


def save_failed_image(output_dir: Path, title: str, image_url: str,
                      image_index: int, reason: str, product_url: str = None):
    """
    将下载失败的图片链接保存到 failed_images.json，供后续二次抓取。
    
    文件位置: {output_dir}/failed_images.json
    格式: JSON 数组，每条记录包含标题、图片URL、序号、失败原因、时间戳
    
    Args:
        output_dir: 输出目录
        title: 商品标题
        image_url: 失败的图片 URL（fullxfull 版本）
        image_index: 图片序号（1-indexed）
        reason: 失败原因（如 "HTTP 404", "timeout" 等）
        product_url: 商品页面 URL（可选）
    """
    failed_file = Path(output_dir) / "failed_images.json"
    
    # 读取已有记录
    existing = []
    if failed_file.exists():
        try:
            with open(failed_file, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except Exception:
            existing = []
    
    # 追加新记录
    existing.append({
        "title": title,
        "image_url": image_url,
        "image_index": image_index,
        "product_url": product_url or "",
        "reason": reason,
        "failed_at": datetime.now().isoformat(),
    })
    
    # 写入文件
    try:
        with open(failed_file, 'w', encoding='utf-8') as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
