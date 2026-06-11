"""话术发送模块。

通过 CDP 控制 BOSS 直聘聊天窗口，发送已审核通过的话术。

核心流程（每条话术）：
    1. 导航到 JD 详情页
    2. 点击「立即沟通/继续沟通」
    3. 根据场景（弹窗 A / 聊天页 B）定位输入框，填入话术
    4. 点击发送
    5. 侧栏交叉验证是否真正送达

安全规则：
    - 每条消息间隔 8-12 秒（随机）
    - 每批最多 4 条，批次间等 5 分钟
    - 连续 2 条失败则停止
    - 发送前关闭可能弹出的「完善简历」等对话框
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import Optional

from rich.console import Console
from rich.table import Table

from boss_pitch.scraper.cdp import CDPClient, CDPError

console = Console(stderr=True)


# ── 数据结构 ──────────────────────────────────────

@dataclass
class SendResult:
    """单条发送结果。"""
    job_id: int
    company: str
    title: str
    boss_name: str
    status: str  # "sent" | "delivered" | "failed" | "skipped" | "dry_run"
    scene: str = ""  # "A" | "B" | ""
    error: str = ""
    timestamp: str = ""


@dataclass
class BatchResult:
    """批量发送结果。"""
    results: list[SendResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def sent_count(self) -> int:
        return sum(1 for r in self.results if r.status in ("sent", "delivered", "dry_run"))

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if r.status == "failed")


# ── JS 片段 ────────────────────────────────────────

_JS_CLOSE_DIALOGS = """
(function() {
    var dialogs = document.querySelectorAll('.dialog-wrap');
    var closed = 0;
    for (var i = 0; i < dialogs.length; i++) {
        var d = dialogs[i];
        var closeBtn = d.querySelector('.icon-close, .close');
        if (closeBtn) { closeBtn.click(); closed++; }
        d.style.display = 'none';
    }
    return closed;
})()
"""

_JS_CHECK_STARTCHAT_BTN = """
(function() {
    var btn = document.querySelector('a.btn-startchat');
    if (!btn) return JSON.stringify({exists: false, text: ""});
    return JSON.stringify({exists: true, text: btn.textContent.trim()});
})()
"""

_JS_CLICK_STARTCHAT = "document.querySelector('a.btn-startchat').click(); 'clicked'"

_JS_CHECK_SCENE_A = """
(function() {
    var ta = document.querySelector('textarea.input-area');
    var sendBtn = document.querySelector('div.send-message');
    return JSON.stringify({
        hasTextarea: !!ta,
        hasSendBtn: !!sendBtn
    });
})()
"""

_JS_SEND_SCENE_A = """
(function() {
    var msg = %s;
    var ta = document.querySelector('textarea.input-area');
    if (!ta) return JSON.stringify({scene: "A", sent: false, error: "no textarea"});
    ta.focus();
    ta.value = msg;
    ta.dispatchEvent(new Event('input', {bubbles: true}));
    ta.dispatchEvent(new Event('change', {bubbles: true}));
    var btn = document.querySelector('div.send-message');
    if (!btn) return JSON.stringify({scene: "A", sent: false, error: "no send btn"});
    btn.click();
    return JSON.stringify({scene: "A", sent: true});
})()
"""

_JS_CHECK_SCENE_B = """
(function() {
    var chatInput = document.querySelector('.chat-input');
    var sendBtn = document.querySelector('.btn-send');
    var nameEl = document.querySelector('.name-text');
    return JSON.stringify({
        hasInput: !!chatInput,
        hasSendBtn: !!sendBtn,
        currentName: nameEl ? nameEl.textContent.trim() : ""
    });
})()
"""

_JS_FILL_AND_SEND_SCENE_B = """
(function() {
    var msg = %s;
    var input = document.querySelector('.chat-input');
    if (!input) return JSON.stringify({sent: false, error: "no chat-input"});
    input.focus();
    document.execCommand('insertText', false, msg);
    return JSON.stringify({sent: false, step: "filled", preview: input.innerText.substring(0, 30)});
})()
"""

_JS_CLICK_SEND_B = """
(function() {
    var btn = document.querySelector('.btn-send');
    if (!btn) return JSON.stringify({sent: false, error: "no btn-send"});
    btn.click();
    return JSON.stringify({sent: true});
})()
"""

_JS_VERIFY_SIDEBAR = """
(function() {
    var html = document.body.innerHTML;
    var name = %s;
    var idx = html.indexOf(name);
    if (idx < 0) return JSON.stringify({found: false, name: name});
    var snippet = html.substring(idx - 20, idx + 300);
    var hasDelivered = snippet.indexOf('[送达]') >= 0 || snippet.indexOf('status-delivery') >= 0;
    var hasRead = snippet.indexOf('[已读]') >= 0 || snippet.indexOf('status-read') >= 0;
    return JSON.stringify({
        found: true,
        hasDelivered: hasDelivered,
        hasRead: hasRead,
        snippet: snippet.substring(0, 150)
    });
})()
"""

_JS_NAVIGATE_CHAT = "window.location.href = 'https://www.zhipin.com/web/geek/chat'"


# ── 发送核心 ────────────────────────────────────────

def _json_str(text: str) -> str:
    """将文本转为 JSON 字符串字面量，用于嵌入 JS 代码。"""
    return json.dumps(text, ensure_ascii=False)


def send_pitch(
    client: CDPClient,
    job: dict,
    pitch_text: str,
    dry_run: bool = False,
) -> SendResult:
    """发送单条话术。

    Args:
        client: 已连接的 CDP 客户端
        job: 岗位信息（需含 url, company, title, boss_name）
        pitch_text: 话术文本
        dry_run: 是否模拟发送

    Returns:
        SendResult: 发送结果
    """
    job_id = job.get("id", 0)
    company = job.get("company", "")
    title = job.get("title", "")
    boss_name = job.get("boss_name", "")
    url = job.get("url", "")

    result = SendResult(
        job_id=job_id,
        company=company,
        title=title,
        boss_name=boss_name,
        status="failed",
        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
    )

    if dry_run:
        result.status = "dry_run"
        result.scene = "-"
        console.print(f"  [yellow]DRY-RUN[/] #{job_id} {company} · {title}")
        return result

    try:
        # Step 1: 导航到 JD 详情页
        console.print(f"  📄 导航到 #{job_id} {company} · {title}")
        client.navigate(url, wait=True)
        client.sleep(4)

        # 关闭可能弹出的对话框
        client.evaluate(_JS_CLOSE_DIALOGS)
        client.sleep(0.5)

        # Step 2: 点击「立即沟通/继续沟通」
        btn_info_raw = client.evaluate(_JS_CHECK_STARTCHAT_BTN)
        try:
            btn_info = json.loads(btn_info_raw) if isinstance(btn_info_raw, str) else btn_info_raw
        except (json.JSONDecodeError, TypeError):
            btn_info = {"exists": False}

        if not btn_info.get("exists"):
            result.error = "未找到沟通按钮（可能未登录或页面异常）"
            console.print(f"  [red]✗[/] #{job_id}: {result.error}")
            return result

        btn_text = btn_info.get("text", "")
        console.print(f"  🔘 按钮文字: {btn_text}")

        client.evaluate(_JS_CLICK_STARTCHAT)
        client.sleep(3)

        # Step 3: 检测场景并填入话术
        # 先检查场景 B（聊天页面）
        scene_b_raw = client.evaluate(_JS_CHECK_SCENE_B)
        try:
            scene_b = json.loads(scene_b_raw) if isinstance(scene_b_raw, str) else scene_b_raw
        except (json.JSONDecodeError, TypeError):
            scene_b = {"hasInput": False}

        if scene_b.get("hasInput"):
            # 场景 B：聊天页面
            result.scene = "B"
            current_name = scene_b.get("currentName", "")
            console.print(f"  💬 场景B 聊天页，当前对话: {current_name}")

            # 关闭弹窗
            client.evaluate(_JS_CLOSE_DIALOGS)
            client.sleep(0.3)

            # 等待 500ms 让前端稳定（关键！）
            client.sleep(0.5)

            # 填入话术
            js_fill = _JS_FILL_AND_SEND_SCENE_B % _json_str(pitch_text)
            fill_raw = client.evaluate(js_fill)
            try:
                fill_result = json.loads(fill_raw) if isinstance(fill_raw, str) else fill_raw
            except (json.JSONDecodeError, TypeError):
                fill_result = {"step": "unknown"}

            if fill_result.get("error"):
                result.error = f"填入失败: {fill_result['error']}"
                console.print(f"  [red]✗[/] #{job_id}: {result.error}")
                return result

            # 等待 500ms 让框架感知输入
            client.sleep(0.5)

            # 点击发送
            send_raw = client.evaluate(_JS_CLICK_SEND_B)
            try:
                send_result = json.loads(send_raw) if isinstance(send_raw, str) else send_raw
            except (json.JSONDecodeError, TypeError):
                send_result = {"sent": False}

            if not send_result.get("sent"):
                result.error = f"发送失败: {send_result.get('error', 'unknown')}"
                console.print(f"  [red]✗[/] #{job_id}: {result.error}")
                return result

        else:
            # 检查场景 A（弹窗）
            scene_a_raw = client.evaluate(_JS_CHECK_SCENE_A)
            try:
                scene_a = json.loads(scene_a_raw) if isinstance(scene_a_raw, str) else scene_a_raw
            except (json.JSONDecodeError, TypeError):
                scene_a = {"hasTextarea": False}

            if scene_a.get("hasTextarea"):
                result.scene = "A"
                console.print(f"  💬 场景A 弹窗")

                js_send_a = _JS_SEND_SCENE_A % _json_str(pitch_text)
                send_a_raw = client.evaluate(js_send_a)
                try:
                    send_a_result = json.loads(send_a_raw) if isinstance(send_a_raw, str) else send_a_raw
                except (json.JSONDecodeError, TypeError):
                    send_a_result = {"sent": False}

                if not send_a_result.get("sent"):
                    result.error = f"弹窗发送失败: {send_a_result.get('error', 'unknown')}"
                    console.print(f"  [red]✗[/] #{job_id}: {result.error}")
                    return result
            else:
                result.error = "无法识别发送场景（既不是弹窗也不是聊天页）"
                console.print(f"  [red]✗[/] #{job_id}: {result.error}")
                return result

        # Step 4: 侧栏交叉验证
        client.sleep(3)
        verify_ok = _verify_delivery_sidebar(client, boss_name)
        if verify_ok:
            result.status = "delivered"
            console.print(f"  [green]✓[/] #{job_id} {company} → {boss_name} [送达]")
        else:
            # 侧栏验证失败，再导航到聊天页重试一次
            console.print(f"  [yellow]…[/] #{job_id} 侧栏验证中，跳转聊天页...")
            client.evaluate(_JS_NAVIGATE_CHAT)
            client.sleep(4)
            verify_ok2 = _verify_delivery_sidebar(client, boss_name)
            if verify_ok2:
                result.status = "delivered"
                console.print(f"  [green]✓[/] #{job_id} {company} → {boss_name} [送达]")
            else:
                result.status = "sent"  # 发了但未确认送达
                result.error = "侧栏未找到送达标记（可能被BOSS后端丢弃）"
                console.print(f"  [yellow]⚠[/] #{job_id} 发送了但未确认送达")

    except CDPError as e:
        result.error = f"CDP 错误: {e}"
        console.print(f"  [red]✗[/] #{job_id}: {result.error}")
    except Exception as e:
        result.error = f"未知错误: {e}"
        console.print(f"  [red]✗[/] #{job_id}: {result.error}")

    return result


def _verify_delivery_sidebar(client: CDPClient, boss_name: str) -> bool:
    """侧栏交叉验证是否真正送达。

    Args:
        client: CDP 客户端
        boss_name: 招聘人姓名

    Returns:
        bool: 是否确认送达
    """
    if not boss_name:
        return False

    js_verify = _JS_VERIFY_SIDEBAR % _json_str(boss_name)
    raw = client.evaluate(js_verify)
    try:
        verify = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return False

    if not verify.get("found"):
        return False

    return verify.get("hasDelivered") or verify.get("hasRead")


def send_pitches_batch(
    client: CDPClient,
    jobs: list[dict],
    pitches: list[dict],
    dry_run: bool = False,
    batch_size: int = 4,
    batch_interval: int = 300,
) -> BatchResult:
    """批量发送所有已审核的话术。

    批次策略：每批最多 batch_size 条，批次间等 batch_interval 秒。
    连续 2 条失败则停止。

    Args:
        client: CDP 客户端
        jobs: 岗位列表
        pitches: 话术列表
        dry_run: 是否模拟
        batch_size: 每批最大条数
        batch_interval: 批次间隔秒数

    Returns:
        BatchResult: 批量发送结果
    """
    batch = BatchResult()
    consecutive_failures = 0

    # 构建 id → pitch 映射
    pitch_map = {p["id"]: p for p in pitches}

    # 筛选有话术的岗位
    to_send = []
    for job in jobs:
        jid = job.get("id", 0)
        if jid in pitch_map:
            to_send.append((job, pitch_map[jid]))
        else:
            console.print(f"  [dim]跳过 #{jid}（无话术）[/dim]")

    total = len(to_send)
    console.print(f"\n📤 准备发送 {total} 条话术 (dry_run={dry_run})\n")

    for i, (job, pitch) in enumerate(to_send):
        # 批次间等待
        if i > 0 and i % batch_size == 0:
            console.print(f"\n[yellow]⏳ 第 {i // batch_size} 批完成，等待 {batch_interval}s...[/yellow]\n")
            time.sleep(batch_interval)

        result = send_pitch(
            client=client,
            job=job,
            pitch_text=pitch.get("text", ""),
            dry_run=dry_run,
        )
        batch.results.append(result)

        # 连续失败检测
        if result.status == "failed":
            consecutive_failures += 1
            if consecutive_failures >= 2:
                console.print(f"\n[red]⚠ 连续 {consecutive_failures} 条失败，停止发送[/red]")
                break
        else:
            consecutive_failures = 0

        # 发送间隔（随机 8-12 秒）
        if i < total - 1 and not dry_run:
            delay = random.uniform(8, 12)
            console.print(f"  [dim]等待 {delay:.1f}s...[/dim]")
            time.sleep(delay)

    # 打印汇总
    table = Table(title="发送结果汇总")
    table.add_column("#", style="cyan", width=4)
    table.add_column("公司", style="bold")
    table.add_column("岗位")
    table.add_column("场景", width=4)
    table.add_column("状态", width=10)
    table.add_column("说明")

    for r in batch.results:
        status_style = {
            "delivered": "[green]送达✓[/]",
            "sent": "[yellow]已发⚠[/]",
            "dry_run": "[blue]模拟[/]",
            "failed": "[red]失败✗[/]",
            "skipped": "[dim]跳过[/]",
        }.get(r.status, r.status)

        table.add_row(
            str(r.job_id),
            r.company,
            r.title[:20],
            r.scene,
            status_style,
            r.error[:30] if r.error else "",
        )

    console.print(table)
    console.print(f"\n📊 总计: {batch.total} | 送达: {batch.sent_count} | 失败: {batch.failed_count}")

    return batch
