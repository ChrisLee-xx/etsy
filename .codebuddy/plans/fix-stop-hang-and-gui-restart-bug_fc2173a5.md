---
name: fix-stop-hang-and-gui-restart-bug
overview: "修复两个 bug: 1) 点击停止后卡住不动（停止逻辑不完整，缺少主动中断 driver 操作和 Chrome 进程清理）；2) 停止后点开始会重复开新 GUI 窗口（on_finished 未重置 worker 引用导致状态异常）"
---

<plan_result>
<req>

## 产品概述

修复 Etsy Scraper GUI 应用中两个关键 bug：

1. **停止按钮卡死** — 点击停止后界面卡在"正在停止..."不再响应
2. **停止后重新开始行为异常** — 停止未完成清理，导致状态混乱

## 核心功能

### Bug 1: 停止卡住不动 (ScraperWorker._run 线程无法及时响应 stop 信号)

**根因**：`stop()` 方法仅设置 `_stop_flag = True`，但 `_run()` 中存在多处长时间阻塞调用（`time.sleep()`、`driver.get()`、`extract_data_with_selenium()`、`process_product()` 等），这些调用之间没有频繁检查标志位。同时 finally 块中的 Chrome 进程清理依赖线程自然结束，如果线程卡在某个阻塞调用上，finally 永远不会执行。

**修复方案**：

- 增强 `stop()` 方法：除了设标志位外，主动调用 `driver.quit()` 终止 Selenium session（这会中断所有正在进行的 `driver.xxx()` 调用），然后 `terminate()` 关闭 Chrome 进程
- 在 `_run()` 关键阻塞点增加 `_stop_flag` 检查
- `on_stop()` 回调中增加超时保护：如果线程在一定时间内不退出，强制执行清理并恢复 UI 状态
- 确保 `on_finished()` **总是被调用**，无论正常结束还是停止，保证按钮状态正确恢复
- 将 worker 引用置空防止悬挂引用

### Bug 2: 停止后按开始再开 GUI 页面

**根因**：这是 Bug 1 的连锁问题。由于 `on_finished()` 未被调用：

- 开始/停止按钮状态未恢复（开始按钮仍 disabled，停止按钮仍 normal）
- `self.worker` 仍持有旧引用
- 用户看到界面无反应后可能重启应用导致多个窗口

修复 Bug 1 后此问题自动解决。额外增加防御性代码：`start_worker()` 中检查是否已有运行中的 worker。

### 涉及修改的文件

仅需修改一个文件：`src/etsy_scraper/gui.py`

具体改动：

**ScraperWorker 类改造：**

1. `stop()` — 增加 driver.quit() + chrome_process.terminate() 主动中断
2. `_run()` — 在 while/for 循环的关键阻塞点后增加 _stop_flag 检查；except 块中判断是否因停止而退出，如果是则走 on_finished 而非报错
3. 新增 `_is_stopped` 属性辅助判断

**App 类改造：**

4. `on_stop()` — 启动守护逻辑或设置超时定时器，确保最终调用 on_finished
5. `on_finished()` — 增加 self.worker = None 清理引用
6. `start_worker()` — 如果已有运行中的 worker，先停止旧 worker
</req>
<tech>

## Tech Stack

- **语言**: Python 3.11+
- **GUI 框架**: CustomTkinter (ctk)
- **浏览器自动化**: Selenium WebDriver + undetected-chromedriver
- **多线程**: threading.Thread (daemon)

## 实现方案

采用 **主动中断 + 防御性状态管理** 策略：

### 核心设计决策

**决策 1: 为什么用 driver.quit() 而不是只靠标志位检查**

Selenium 的 `driver.quit()` 会关闭整个 browser session，这会立即中断所有正在进行的 `driver.get()`、`driver.find_element()`、`driver.execute_script()` 等阻塞调用，抛出异常被 try/except 捕获。这是让线程从任意阻塞点快速退出的最可靠方式。配合 `_stop_flag` 标志位做逻辑判断，双重保障。

**决策 2: on_stop() 不使用独立守护线程**

CustomTkinter 的 `after()` 方法可以在主线程安全地执行回调。使用 `after()` 设置一个 5 秒超时定时器比创建新线程更轻量，且不需要额外的同步机制。

**决策 3: start_worker() 防重入**

在创建新 worker 前检查并停止旧 worker，避免用户快速连续点击开始按钮导致多个抓取任务同时运行、多个 Chrome 实例同时启动的问题。

### 架构影响

仅修改 `gui.py` 单文件，不影响 `real_chrome_scraper.py` 和 `section_scraper.py`。改动集中在 ScraperWorker 类和 App 类的交互边界上。

### 性能考虑

- `stop()` 中的 `driver.quit()` 是 O(1) 操作，立即生效
- `chrome_process.terminate()` 发送 SIGTERM/SIGKILL，无需等待
- 无额外轮询或 busy-wait 开销
- 超时定时器仅一个 `after()` 调用，几乎零开销

## 目录结构

```
src/etsy_scraper/
├── gui.py          # [MODIFY] 唯一需要修改的文件
├── real_chrome_scraper.py  # 不变
├── section_scraper.py       # 不变
└── utils.py                 # 不变
```

## 关键代码结构（变更部分）

```python
# ScraperWorker.stop() 改造后
def stop(self):
    self._stop_flag = True
    # 主动中断 Selenium session（立即解除所有 driver 阻塞调用）
    if self.driver:
        try:
            self.driver.quit()
        except Exception:
            pass
        self.driver = None
    # 强制关闭 Chrome 进程
    if self.chrome_process:
        try:
            self.chrome_process.terminate()
        except Exception:
            pass
        self.chrome_process = None
```

```python
# App.start_worker() 增加防重入
def start_worker(self, **kwargs):
    # 如果已有运行中的 worker，先停止它
    if self.worker:
        self.on_stop()
    
    # ... 原有初始化代码 ...
```

```python
# App.on_stop() 增加超时保护
def on_stop(self):
    if self.worker:
        self.worker.stop()
        self.log("⚠️ 正在停止...")
    
    # 5秒超时保护：如果线程未及时退出，强制恢复 UI
    def force_finish():
        if self.worker and self.worker._thread.is_alive():
            self.log("⏱️ 强制结束抓取...")
        self.on_finished(False, "已手动停止")
    
    self.after(5000, force_finish)
```

```python
# App.on_finished() 清理引用
def on_finished(self, success: bool, message: str):
    self.product_start_btn.configure(state="normal")
    self.section_start_btn.configure(state="normal")
    self.stop_btn.configure(state="disabled")
    self.worker = None  # 新增：清空引用
    
    # ... 原有日志和弹窗逻辑 ...
```

</tech>
<design framework="CustomTkinter" component="">
<description>
此任务是对现有 GUI 的 bug 修复，不涉及新建 UI 或大幅视觉改造。修改集中在后台工作器 (ScraperWorker) 的生命周期管理和 App 类的状态控制逻辑上。UI 本身（布局、样式、组件）不做任何改变。</description>
<style_keywords>
<keyword>功能增强</keyword>
<keyword>bugfix</keyword>
<keyword>状态管理</keyword>
</style_keywords>
<font_system fontFamily="System Default">
<heading size="16" weight="600"></heading>
<subheading size="14" weight="500"></subheading>
<body size="13" weight="400"></body>
</font_system>
<color_system>
<primary_colors>
<color>#0D6EFD</color>
<color>#3B82F6</color>
</primary_colors>
<background_colors>
<color>#FFFFFF</color>
<color>#F8F9FA</color>
</background_colors>
<text_colors>
<color>#212529</color>
<color>#6C757D</color>
</text_colors>
<functional_colors>
<color>#DC3545</color>
<color>#198754</color>
<color="#FFC107</color>
</functional_colors>
</color_system>
</design>
<extensions>
</extensions>
<todolist>
<item id="fix-stop-hang" deps="">修复 ScraperWorker.stop() 卡死问题：增加 driver.quit() 主动中断 Selenium session 和 chrome_process.terminate() 关闭 Chrome</item>
<item id="fix-onstop-timeout" deps="">为 App.on_stop() 增加 5 秒超时保护机制，确保 UI 状态最终恢复</item>
<item id="fix-onfinished-cleanup" deps="">在 App.on_finished() 中清空 self.worker 引用，防止悬挂引用</item>
<item id="fix-startworker-guard" deps="">在 App.start_worker() 中增加防重入检查，已有运行中 worker 时先停止</item>
<item id="verify-local" deps="">本地验证修复效果：测试开始 -> 停止 -> 再开始的完整流程</item>
</todolist>
</plan_result>