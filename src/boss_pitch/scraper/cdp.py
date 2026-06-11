"""CDP (Chrome DevTools Protocol) 底层连接。

通过 WebSocket 与 Chrome 通信，实现对浏览器的程序化控制。

设计原则：
    - 同步 API（CLI 工具不需要异步，同步更简单）
    - 用 httpx 同步客户端访问 /json 端点
    - 用 websockets.sync_client 做 CDP WebSocket 通信
    - 每次操作：发 JSON-RPC → 等响应 → 返回结果

连接方式：
    直接连 page 的 webSocketDebuggerUrl，命令直接发送，不需要 target 代理。

Chrome 启动命令（手动调试用）：
    # macOS
    /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\
        --remote-debugging-port=9222 \\
        --remote-allow-origins=* \\
        --user-data-dir="$HOME/.boss-pitch/chrome-data"
"""

import json
import os
import platform
import subprocess
import time
from typing import Any, Dict, List, Optional

import httpx
from rich.console import Console
from websockets.sync.client import connect as ws_connect

console = Console(stderr=True)


class CDPError(Exception):
    """CDP 通信错误基类。"""


class CDPSessionError(CDPError):
    """Session 未建立或已断开。"""


class CDPCommandError(CDPError):
    """CDP 命令执行失败（服务端返回 error）。"""


class CDPTimeoutError(CDPError):
    """CDP 操作超时。"""


class CDPClient:
    """Chrome DevTools Protocol 客户端。

    封装与 Chrome 浏览器的 CDP 通信，提供页面导航、执行 JS、
    点击元素等基础能力。

    使用示例::

        client = CDPClient()
        if client.is_alive():
            client.attach()
            client.navigate("https://example.com")
            title = client.evaluate("document.title")
            print(title)
            client.close()

    Attributes:
        host: Chrome 调试地址，默认 localhost
        port: CDP 端口，默认 9222
        verbose: 是否输出调试信息
    """

    def __init__(self, host: str = "localhost", port: int = 9222, verbose: bool = False):
        self.host = host
        self.port = port
        self.verbose = verbose

        self._http: Optional[httpx.Client] = None
        self._ws: Optional[Any] = None
        self._ws_url: Optional[str] = None
        self._target_id: Optional[str] = None
        self._cmd_id: int = 0
        self._attached: bool = False
        # 事件缓冲：{event_name: [params, ...]}
        self._event_buffer: Dict[str, List[dict]] = {}
        # Browser-level WebSocket（用于 Target.createTarget 等命令）
        self._browser_ws: Optional[Any] = None
        self._browser_ws_url: Optional[str] = None

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        """HTTP 调试地址。"""
        return f"http://{self.host}:{self.port}"

    @property
    def attached(self) -> bool:
        """当前是否已 attach 到某个页面。"""
        return self._attached and self._ws is not None

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _get_http(self) -> httpx.Client:
        """获取或创建 httpx 同步客户端。"""
        if self._http is None:
            self._http = httpx.Client(timeout=10.0)
        return self._http

    def _next_id(self) -> int:
        """生成下一个命令 ID。"""
        self._cmd_id += 1
        return self._cmd_id

    def _log(self, msg: str) -> None:
        """输出调试日志。"""
        if self.verbose:
            console.log(f"[dim][CDP][/dim] {msg}")

    def _send_command(self, method: str, params: Optional[dict] = None, timeout: float = 30) -> dict:
        """发送 JSON-RPC 命令并等待响应。

        Args:
            method: CDP 方法名，如 "Page.navigate"
            params: 方法参数
            timeout: 等待响应的超时秒数

        Returns:
            dict: CDP 响应中的 result 字段

        Raises:
            CDPSessionError: 未 attach 到页面
            CDPTimeoutError: 等待响应超时
            CDPCommandError: CDP 服务端返回错误
        """
        if not self.attached:
            raise CDPSessionError("未 attach 到任何页面，请先调用 attach()")

        cmd_id = self._next_id()
        message: Dict[str, Any] = {"id": cmd_id, "method": method}
        if params:
            message["params"] = params

        raw = json.dumps(message)
        self._log(f">> {raw}")

        try:
            self._ws.send(raw)
        except Exception as exc:
            self._attached = False
            raise CDPSessionError(f"WebSocket 发送失败: {exc}") from exc

        # 等待匹配 id 的响应
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CDPTimeoutError(f"命令 {method}(id={cmd_id}) 等待响应超时 ({timeout}s)")

            try:
                recv_timeout = min(remaining, 1.0)
                raw_resp = self._ws.recv(timeout=recv_timeout)
            except TimeoutError:
                continue
            except Exception as exc:
                self._attached = False
                raise CDPSessionError(f"WebSocket 接收失败: {exc}") from exc

            try:
                resp = json.loads(raw_resp)
            except json.JSONDecodeError:
                self._log(f"无法解析响应: {raw_resp[:200]}")
                continue

            self._log(f"<< {raw_resp[:300]}")

            # 如果是事件，缓存起来
            if "method" in resp and "id" not in resp:
                event_name = resp["method"]
                if event_name not in self._event_buffer:
                    self._event_buffer[event_name] = []
                self._event_buffer[event_name].append(resp.get("params", {}))
                continue

            # 匹配到命令响应
            if resp.get("id") == cmd_id:
                if "error" in resp:
                    err = resp["error"]
                    raise CDPCommandError(
                        f"CDP 命令 {method} 失败: [{err.get('code')}] {err.get('message')}"
                    )
                return resp.get("result", {})

            # 其他命令的响应，忽略（暂不处理并发）
            self._log(f"收到非匹配响应 id={resp.get('id')}，忽略")

    def _ensure_browser_ws(self) -> None:
        """确保 browser-level WebSocket 已连接。"""
        if self._browser_ws is not None:
            return
        version = self.get_version()
        ws_url = version.get("webSocketDebuggerUrl")
        if not ws_url:
            raise CDPError("无法获取 browser WebSocket URL")
        self._browser_ws_url = ws_url
        self._browser_ws = ws_connect(ws_url)
        self._log(f"已连接 browser WebSocket: {ws_url}")

    def send_browser_command(self, method: str, params: Optional[dict] = None, timeout: float = 30) -> dict:
        """发送 browser-level CDP 命令（如 Target.createTarget）。

        不需要先 attach 到页面，直接通过 browser WebSocket 通信。

        Args:
            method: CDP 方法名，如 "Target.createTarget"
            params: 方法参数
            timeout: 等待响应的超时秒数

        Returns:
            dict: CDP 响应中的 result 字段
        """
        self._ensure_browser_ws()

        cmd_id = self._next_id()
        message: Dict[str, Any] = {"id": cmd_id, "method": method}
        if params:
            message["params"] = params

        raw = json.dumps(message)
        self._log(f">> [browser] {raw}")
        try:
            self._browser_ws.send(raw)
        except Exception as exc:
            self._browser_ws = None
            raise CDPSessionError(f"Browser WebSocket 发送失败: {exc}") from exc

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CDPTimeoutError(f"Browser 命令 {method}(id={cmd_id}) 等待响应超时 ({timeout}s)")
            try:
                recv_timeout = min(remaining, 1.0)
                raw_resp = self._browser_ws.recv(timeout=recv_timeout)
            except TimeoutError:
                continue
            except Exception as exc:
                self._browser_ws = None
                raise CDPSessionError(f"Browser WebSocket 接收失败: {exc}") from exc

            try:
                resp = json.loads(raw_resp)
            except json.JSONDecodeError:
                continue

            if resp.get("id") == cmd_id:
                if "error" in resp:
                    err = resp["error"]
                    raise CDPCommandError(
                        f"Browser 命令 {method} 失败: [{err.get('code')}] {err.get('message')}"
                    )
                return resp.get("result", {})

    def create_tab(self, url: str) -> dict:
        """创建新标签页并导航到指定 URL。

        Args:
            url: 目标 URL

        Returns:
            dict: 包含 targetId 等字段
        """
        result = self.send_browser_command("Target.createTarget", {"url": url})
        self._log(f"已创建新 tab: {result}")
        return result

    def _drain_events(self, event_name: str) -> List[dict]:
        """取出并清空某事件的所有缓存。"""
        events = self._event_buffer.pop(event_name, [])
        return events

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    def get_targets(self) -> List[dict]:
        """获取所有 Chrome targets（页面列表）。

        Returns:
            list[dict]: target 信息列表，每个包含 id, title, url, webSocketDebuggerUrl 等

        Raises:
            CDPError: Chrome 未运行或连接失败
        """
        try:
            client = self._get_http()
            resp = client.get(f"{self.base_url}/json")
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError as exc:
            raise CDPError(
                f"无法连接到 Chrome 调试端口 {self.host}:{self.port}，"
                f"请确认 Chrome 已使用 --remote-debugging-port={self.port} 启动"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise CDPError(f"获取 targets 失败: HTTP {exc.response.status_code}") from exc

    def get_version(self) -> dict:
        """获取 Chrome 版本信息。

        Returns:
            dict: 包含 Browser, Protocol-Version, User-Agent 等字段
        """
        try:
            client = self._get_http()
            resp = client.get(f"{self.base_url}/json/version")
            resp.raise_for_status()
            return resp.json()
        except httpx.ConnectError as exc:
            raise CDPError(
                f"无法连接到 Chrome 调试端口 {self.host}:{self.port}"
            ) from exc

    def is_alive(self) -> bool:
        """检查 Chrome 调试端口是否可用。

        Returns:
            bool: Chrome 是否在运行且 CDP 端口可用
        """
        try:
            self.get_version()
            return True
        except CDPError:
            return False

    def attach(self, target_id: Optional[str] = None) -> None:
        """Attach 到指定页面 target。

        连接到 target 的 webSocketDebuggerUrl，建立 CDP 通信通道。
        如果 target_id 为 None，则自动找第一个 type=page 的 target。

        Args:
            target_id: 目标页面 ID，None 则自动选择第一个页面

        Raises:
            CDPError: 无可用页面或连接失败
        """
        if self.attached:
            self.detach()

        targets = self.get_targets()
        page_targets = [t for t in targets if t.get("type") == "page"]

        if not page_targets:
            raise CDPError("没有可用的页面 target，请先在 Chrome 中打开一个标签页")

        target = None
        if target_id:
            for t in page_targets:
                if t.get("id") == target_id:
                    target = t
                    break
            if target is None:
                raise CDPError(f"未找到 target_id={target_id} 的页面")
        else:
            target = page_targets[0]

        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            raise CDPError(f"target {target.get('id')} 没有 webSocketDebuggerUrl")

        self._log(f"连接 WebSocket: {ws_url}")
        try:
            self._ws = ws_connect(ws_url, close_timeout=5, max_size=10 * 1024 * 1024)
        except Exception as exc:
            raise CDPError(f"WebSocket 连接失败: {exc}") from exc

        self._ws_url = ws_url
        self._target_id = target.get("id")
        self._attached = True
        self._event_buffer.clear()
        self._log(f"已 attach 到页面: {target.get('title', target.get('url', 'unknown'))}")

        # 启用必要的 domain
        # 注意：只启用 Page，不启用 Runtime（Runtime.enable 会产生大量事件把 WS 搞坏）
        try:
            self._send_command("Page.enable", timeout=10)
            self._log("已启用 Page domain")
        except CDPCommandError as exc:
            self._log(f"启用 domain 时警告: {exc}")

    def detach(self) -> None:
        """断开当前页面 session。"""
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

        self._ws_url = None
        self._target_id = None
        self._attached = False
        self._event_buffer.clear()
        self._log("已断开连接")

    # ------------------------------------------------------------------
    # 页面操作
    # ------------------------------------------------------------------

    def navigate(self, url: str, wait: bool = True, timeout: float = 30) -> dict:
        """导航到指定 URL。

        发送 Page.navigate 命令，可选等待页面加载完成。

        Args:
            url: 目标 URL
            wait: 是否等待 Page.loadEventFired 事件
            timeout: 等待超时秒数

        Returns:
            dict: 包含 frameId, loaderId 等字段

        Raises:
            CDPSessionError: 未 attach
            CDPTimeoutError: 等待超时
            CDPCommandError: CDP 命令失败
        """
        # 先清空之前的 loadEventFired 缓存
        self._drain_events("Page.loadEventFired")

        result = self._send_command("Page.navigate", {"url": url}, timeout=timeout)
        self._log(f"导航到: {url}")

        if wait:
            self.wait_for_load(timeout=timeout)

        return result

    def evaluate(self, expression: str, await_promise: bool = False, timeout: float = 30) -> Any:
        """在页面中执行 JavaScript 表达式。

        如果 WS 断开会自动 re-attach 后重试一次。

        Args:
            expression: JS 表达式字符串
            await_promise: 是否等待 Promise 完成
            timeout: 超时秒数

        Returns:
            Any: 表达式返回值（JSON 反序列化后的 Python 对象）

        Raises:
            CDPCommandError: JS 执行抛出异常
        """
        params: Dict[str, Any] = {
            "expression": expression,
            "returnByValue": True,
        }
        if await_promise:
            params["awaitPromise"] = True

        saved_target_id = self._target_id

        for attempt in range(2):
            try:
                result = self._send_command("Runtime.evaluate", params, timeout=timeout)
                break
            except CDPSessionError:
                if attempt == 0 and saved_target_id:
                    # WS 断了，re-attach 后重试
                    self._log("WS 断开，re-attach 后重试")
                    self.detach()
                    time.sleep(1)
                    self.attach(target_id=saved_target_id)
                else:
                    raise

        # 检查 JS 异常
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            exc_text = details.get("text", "未知 JS 异常")
            # 尝试获取更详细的错误信息
            exc_obj = details.get("exception", {})
            if exc_obj and "description" in exc_obj:
                exc_text = exc_obj["description"]
            elif exc_obj and "value" in exc_obj:
                exc_text = str(exc_obj["value"])
            raise CDPCommandError(f"JS 执行异常: {exc_text}")

        # 提取返回值
        remote_obj = result.get("result", {})
        value_type = remote_obj.get("type")

        if value_type == "undefined":
            return None
        if value_type == "object" and remote_obj.get("subtype") == "null":
            return None

        return remote_obj.get("value")

    def click(self, selector: str) -> bool:
        """通过 CSS 选择器点击元素。

        使用 JS document.querySelector(selector).click() 实现。

        Args:
            selector: CSS 选择器

        Returns:
            bool: 是否成功找到并点击了元素
        """
        js = f"""
        (function() {{
            var el = document.querySelector({json.dumps(selector)});
            if (el) {{
                el.click();
                return true;
            }}
            return false;
        }})()
        """
        result = self.evaluate(js)
        if result:
            self._log(f"点击元素: {selector}")
        else:
            self._log(f"未找到元素: {selector}")
        return bool(result)

    def wait_for_selector(self, selector: str, timeout: float = 10) -> bool:
        """轮询等待元素出现在 DOM 中。

        Args:
            selector: CSS 选择器
            timeout: 最大等待秒数

        Returns:
            bool: 是否在超时前找到元素
        """
        js_template = "document.querySelector({}) !== null"
        js_expr = js_template.format(json.dumps(selector))

        interval = 0.3
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            try:
                result = self.evaluate(js_expr, timeout=5)
                if result:
                    self._log(f"元素已出现: {selector}")
                    return True
            except CDPCommandError:
                pass

            remaining = deadline - time.monotonic()
            if remaining > interval:
                time.sleep(interval)
            else:
                break

        self._log(f"等待元素超时: {selector} ({timeout}s)")
        return False

    def wait_for_load(self, timeout: float = 30) -> None:
        """等待 Page.loadEventFired 事件。

        先检查事件缓冲区是否已有该事件，没有则等待。

        Args:
            timeout: 最大等待秒数

        Raises:
            CDPTimeoutError: 超时未收到 loadEventFired
        """
        # 先检查缓冲区
        cached = self._drain_events("Page.loadEventFired")
        if cached:
            self._log("页面已加载（缓存事件）")
            return

        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CDPTimeoutError(f"等待页面加载超时 ({timeout}s)")

            try:
                recv_timeout = min(remaining, 1.0)
                raw = self._ws.recv(timeout=recv_timeout)
            except TimeoutError:
                continue
            except Exception as exc:
                raise CDPSessionError(f"等待加载时连接断开: {exc}") from exc

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            if msg.get("method") == "Page.loadEventFired":
                self._log("页面加载完成 (loadEventFired)")
                # 把期间收到的事件缓存起来（除了 loadEventFired）
                return

            # 缓存其他事件
            if "method" in msg:
                event_name = msg["method"]
                if event_name not in self._event_buffer:
                    self._event_buffer[event_name] = []
                self._event_buffer[event_name].append(msg.get("params", {}))

    def sleep(self, seconds: float) -> None:
        """简单休眠。

        Args:
            seconds: 休眠秒数
        """
        time.sleep(seconds)

    def check_login(self) -> bool:
        """检查 BOSS 直聘登录态。

        创建一个临时 tab 访问 BOSS 搜索页，检查是否被重定向到登录页。
        检查完毕后 detach 并关闭临时 tab。

        Returns:
            bool: True=已登录，False=未登录
        """
        tab = self.create_tab("https://www.zhipin.com/web/geek/job")
        tid = tab.get("targetId")
        if not tid:
            return False

        time.sleep(6)

        try:
            self.attach(target_id=tid)
            url = self.evaluate("window.location.href") or ""
            # 登录页路径包含 /web/user/ 或 /passport/
            is_login_page = "/web/user/" in url or "/passport/" in url
            return not is_login_page
        except Exception:
            return False
        finally:
            # 先 detach 再关闭 tab（顺序很重要，先关 tab 会导致 WS 断开异常）
            self.detach()
            try:
                self.send_browser_command("Target.closeTarget", {"targetId": tid})
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 资源清理
    # ------------------------------------------------------------------

    def close(self) -> None:
        """关闭所有连接，释放资源。"""
        self.detach()
        if self._browser_ws is not None:
            try:
                self._browser_ws.close()
            except Exception:
                pass
            self._browser_ws = None
        if self._http is not None:
            try:
                self._http.close()
            except Exception:
                pass
            self._http = None
        self._log("CDPClient 已关闭")

    def __enter__(self):
        """上下文管理器入口。"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口。"""
        self.close()
        return False

    def __del__(self):
        """析构时尝试清理。"""
        try:
            self.close()
        except Exception:
            pass


# ======================================================================
# Chrome 启动辅助函数
# ======================================================================

def _find_chrome_path() -> str:
    """查找系统中 Chrome 可执行文件路径。

    Returns:
        str: Chrome 可执行文件路径

    Raises:
        CDPError: 未找到 Chrome
    """
    system = platform.system()

    if system == "Darwin":
        # macOS
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif system == "Linux":
        candidates = [
            "google-chrome",
            "google-chrome-stable",
            "chromium-browser",
            "chromium",
        ]
    elif system == "Windows":
        candidates = [
            os.path.expandvars(
                r"C:\Program Files\Google\Chrome\Application\chrome.exe"
            ),
            os.path.expandvars(
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
            ),
            os.path.expandvars(
                r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
            ),
        ]
    else:
        raise CDPError(f"不支持的操作系统: {system}")

    for path in candidates:
        if system == "Linux":
            # Linux 上用 which 检查
            from shutil import which
            if which(path):
                return path
        else:
            if os.path.isfile(path):
                return path

    raise CDPError(
        "未找到 Chrome 浏览器，请安装 Google Chrome 或手动指定路径"
    )


def launch_chrome(
    port: int = 9222,
    user_data_dir: Optional[str] = None,
    headless: bool = False,
    chrome_path: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
) -> subprocess.Popen:
    """启动 Chrome 并等待 CDP 端口可用。

    Args:
        port: CDP 远程调试端口，默认 9222
        user_data_dir: Chrome 用户数据目录，默认 ~/.boss-pitch/chrome-data
        headless: 是否无头模式（--headless=new）
        chrome_path: Chrome 可执行文件路径，None 则自动查找
        extra_args: 额外的 Chrome 启动参数

    Returns:
        subprocess.Popen: Chrome 进程对象

    Raises:
        CDPError: Chrome 启动失败或端口未就绪
    """
    if chrome_path is None:
        chrome_path = _find_chrome_path()

    if user_data_dir is None:
        user_data_dir = os.path.expanduser("~/.boss-pitch/chrome-data")

    os.makedirs(user_data_dir, exist_ok=True)

    cmd = [
        chrome_path,
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={user_data_dir}",
    ]

    if headless:
        cmd.append("--headless=new")

    if extra_args:
        cmd.extend(extra_args)

    console.print(f"[dim]启动 Chrome: {' '.join(cmd[:3])} ...[/dim]")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # macOS/Linux: 让 Chrome 在后台独立运行
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise CDPError(f"Chrome 可执行文件不存在: {chrome_path}") from exc
    except PermissionError as exc:
        raise CDPError(f"无法执行 Chrome: {chrome_path}") from exc

    # 等待 CDP 端口可用
    console.print(f"[dim]等待 CDP 端口 {port} 就绪...")
    client = CDPClient(port=port)
    max_wait = 15  # 最多等 15 秒
    deadline = time.monotonic() + max_wait

    while time.monotonic() < deadline:
        # 检查进程是否已退出
        if proc.poll() is not None:
            raise CDPError(f"Chrome 启动后立即退出，返回码: {proc.returncode}")

        if client.is_alive():
            console.print(f"[green]Chrome 已启动，CDP 端口 {port} 就绪[/green]")
            return proc

        time.sleep(0.5)

    # 超时，杀掉进程
    proc.terminate()
    raise CDPError(f"Chrome 启动超时 ({max_wait}s)，CDP 端口 {port} 未就绪")
