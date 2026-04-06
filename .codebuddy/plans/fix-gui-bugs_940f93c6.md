---
name: fix-gui-bugs
overview: 修复 GUI 代码中的 6 个 Bug：关闭窗口不停止 worker、force_finish 重复调用、finally 块重复 terminate、section 翻页不响应停止、section 全失败仍报成功、图片下载不可中断
todos:
  - id: fix-bug5-finally
    content: 修复 finally 块用 poll() 检查进程存活后再 terminate
    status: completed
  - id: fix-bug6-onclose
    content: 修复 _on_close 先停止 worker 再 destroy 防止孤儿进程
    status: completed
  - id: fix-bug8-forcefinish
    content: 修复 force_finish 开头加 worker is None 守卫防止重复调用
    status: completed
  - id: fix-bug9-section-result
    content: 修复 _scrape_sections 追踪 stopped_early 区分完成和中断状态
    status: completed
  - id: fix-bug10-download
    content: 修复 _download_images 循环开头检查 _stop_flag 提前退出
    status: completed
  - id: fix-bug7-section-stop
    content: 为 extract_product_links 增加 stop_check 回调并在 gui 中传入 lambda
    status: completed
  - id: verify-all
    content: 语法检查确认所有修改无语法错误
    status: completed
    dependencies:
      - fix-bug5-finally
      - fix-bug6-onclose
      - fix-bug8-forcefinish
      - fix-bug9-section-result
      - fix-bug10-download
      - fix-bug7-section-stop
---

## 产品概述

修复上一轮完整 code review 发现的 6 个 GUI 层 Bug，涵盖进程清理、状态管理、停止响应性和结果报告准确性。

## 核心功能

- **Bug #5**: finally 块对已终止的 Chrome 进程避免重复 terminate 调用
- **Bug #6**: 关闭窗口时自动停止正在运行的 worker 线程和 Chrome 进程（高优先级）
- **Bug #7**: Section 模式下翻页循环支持 `_stop_flag` 检查，点击停止后快速响应（中优先级）
- **Bug #8**: `force_finish` 超时回调增加守卫条件，防止与正常 `on_finished` 冲突导致重复日志（高优先级）
- **Bug #9**: `_scrape_sections` 正确区分"全部完成"与"中途退出/部分失败"，不再误报成功
- **Bug #10**: 图片下载循环中检查停止标志，避免在停止后继续等待 30 秒下载超时

## 技术栈

- Python 3.11+ / CustomTkinter (GUI) / Selenium / threading
- 仅修改两个文件：`gui.py` 和 `section_scraper.py`

## 实现方案

### 总体策略

所有修改集中在 `gui.py` 一个主文件 + `section_scraper.py` 的一个函数签名扩展。核心思路是：**让停止信号能传播到每一个阻塞点**。

### Bug #5: finally 块重复 terminate

**位置**: `gui.py:205-212`
**方案**: 在 `finally` 中用 `poll() is None` 判断进程是否存活：

```python
def _safe_terminate(self, proc):
    if proc and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
```

### Bug #6: 关闭窗口时停止 worker

**位置**: `gui.py:889-892`
**方案**: 在 `_on_close` 中先调用 worker.stop() 并等待线程退出（最多等 2 秒），再 destroy。由于 stop() 已经会 driver.quit() + chrome_process.terminate()，线程应该很快退出。

```python
def _on_close(self):
    self._save_current_config()
    if self.worker:
        self.worker.stop()
        if self.worker._thread and self.worker._thread.is_alive():
            self.worker._thread.join(timeout=2.0)
    self.destroy()
```

### Bug #7: Section 翻页响应停止

**位置**: `gui.py:349` + `section_scraper.py:extract_product_links`
**方案**:

1. 在 `section_scraper.py` 的 `extract_product_links()` 函数签名新增可选参数 `stop_check: Optional[Callable[[], bool]] = None`
2. 在其翻页 for 循环的每次迭代开头调用 `if stop_check and stop_check(): break`
3. 在 `gui.py:_scrape_sections()` 第 349 行调用处传入 `stop_check=lambda: self._stop_flag`

### Bug #8: force_finish 防重入

**位置**: `gui.py:841-847`
**方案**: 在 force_finish 开头加一行守卫：

```python
def force_finish():
    if self.worker is None:  # 已被正常的 on_finished 清理过
        return
    ...
```

### Bug #9: section 结果准确性

**位置**: `gui.py:288-402`
**方案**:

1. 在 `_scrape_sections` 开头初始化 `stopped_early = False`
2. 外层 section 循环 break 时设 `stopped_early = True`（无论是 _stop_flag 还是连续失败 break）
3. 最终 on_finished 调用时根据 stopped_early 决定 success 参数：若 early 且 total_success==0 则报失败；若 early 但有部分成功则报部分完成（success=True 但消息说明）；否则正常报成功

### Bug #10: 图片下载可中断

**位置**: `gui.py:430-443`
**方案**: 在 `_download_images` 的 for 循环开头加一行：

```python
for idx, url in download_list:
    if self._stop_flag:
        return
    ...  # 原有下载逻辑不变
```

## 架构设计

修改范围极小，不引入新的类或模块。所有改动都是对现有方法的增强：

```
ScraperWorker (gui.py)
├── stop()              -- 已有，无需改动
├── _run()              -- [MOD] finally 用 poll() 检查 (#5)
├── _scrape_products()  -- 无需改动（已有 _stop_flag 检查）
├── _scrape_sections()  -- [MOD] 传 stop_check 回调 (#7), 追踪 stopped_early (#9)
└── _download_images()  -- [MOD] 循环开头检查 _stop_flag (#10)

App (gui.py)
├── on_stop()           -- [MOD] force_finish 加 None 守卫 (#8)
├── on_finished()       -- 无需改动
└── _on_close()         -- [MOD] 先停 worker 再 destroy (#6)

extract_product_links (section_scraper.py)
└── 新增 stop_check 可选参数，翻页循环中检查 (#7)
```

## 目录结构

```
/Users/linan/Desktop/code/etsy/
├── src/etsy_scraper/
│   ├── gui.py                  # [MOD] 5 处修改 (Bug #5,#6,#8,#9,#10)
│   └── section_scraper.py      # [MOD] 1 处修改 - extract_product_links 增加 stop_check 参数 (Bug #7)
```