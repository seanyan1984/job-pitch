"""送达验证模块。

通过 CDP 在 BOSS 直聘聊天页检查已发消息的送达/已读状态。

工作流：
    1. 导航到聊天页 /web/geek/chat
    2. 用 innerHTML 搜索侧栏中每个招聘人的消息状态
    3. 检测 [送达] / [已读] 标记
    4. 汇总结果

注意：
    - BOSS 聊天列表是虚拟化组件，querySelector 无法精确搜索单个条目
    - 但 innerHTML 包含完整渲染内容，indexOf 可靠
    - 侧栏标记是唯一可靠验证标准（对话内验证有假阳性）
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from rich.console import Console
from rich.table import Table

from boss_pitch.scraper.cdp import CDPClient, CDPError

console = Console(stderr=True)


@dataclass
class VerifyResult:
    """单条验证结果。"""
    job_id: int
    company: str
    boss_name: str
    status: str  # "delivered" | "read" | "not_found" | "sending" | "unknown"
    detail: str = ""


@dataclass
class VerifyBatch:
    """批量验证结果。"""
    results: list[VerifyResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def delivered(self) -> int:
        return sum(1 for r in self.results if r.status == "delivered")

    @property
    def read(self) -> int:
        return sum(1 for r in self.results if r.status == "read")

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status in ("not_found", "unknown"))


_JS_CHECK_DELIVERY = """
(function() {
    var html = document.body.innerHTML;
    var name = %s;
    var company = %s;
    var idx = -1;

    // 先用名字+公司双重定位
    if (name && company) {
        var nameIdx = html.indexOf(name);
        while (nameIdx >= 0) {
            var context = html.substring(Math.max(0, nameIdx - 50), nameIdx + 400);
            if (context.indexOf(company) >= 0) {
                idx = nameIdx;
                break;
            }
            nameIdx = html.indexOf(name, nameIdx + 1);
        }
    }

    // 回退：只用名字
    if (idx < 0 && name) {
        idx = html.indexOf(name);
    }

    if (idx < 0) return JSON.stringify({found: false, name: name});

    var snippet = html.substring(Math.max(0, idx - 20), idx + 400);
    var hasDelivered = snippet.indexOf('[送达]') >= 0
        || snippet.indexOf('status-delivery') >= 0
        || snippet.indexOf('statusDelivery') >= 0;
    var hasRead = snippet.indexOf('[已读]') >= 0
        || snippet.indexOf('status-read') >= 0
        || snippet.indexOf('statusRead') >= 0;
    var isSending = snippet.indexOf('正在沟通') >= 0
        || snippet.indexOf('status-loading') >= 0;

    var status = "unknown";
    if (hasRead) status = "read";
    else if (hasDelivered) status = "delivered";
    else if (isSending) status = "sending";

    return JSON.stringify({
        found: true,
        status: status,
        hasDelivered: hasDelivered,
        hasRead: hasRead,
        isSending: isSending,
        snippet: snippet.substring(0, 200)
    });
})()
"""


def verify_single(
    client: CDPClient,
    job_id: int,
    boss_name: str,
    company: str = "",
) -> VerifyResult:
    """验证单条消息的送达状态。

    Args:
        client: CDP 客户端（需已 attach）
        job_id: 岗位 ID
        boss_name: 招聘人姓名
        company: 公司名称（辅助定位）

    Returns:
        VerifyResult: 验证结果
    """
    result = VerifyResult(
        job_id=job_id,
        company=company,
        boss_name=boss_name,
        status="unknown",
    )

    if not boss_name:
        result.status = "not_found"
        result.detail = "无招聘人姓名"
        return result

    try:
        js = _JS_CHECK_DELIVERY % (
            json.dumps(boss_name, ensure_ascii=False),
            json.dumps(company, ensure_ascii=False),
        )
        raw = client.evaluate(js)
        try:
            check = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            result.detail = "JSON 解析失败"
            return result

        if not check.get("found"):
            result.status = "not_found"
            result.detail = f"侧栏未找到 {boss_name}"
            return result

        result.status = check.get("status", "unknown")
        result.detail = check.get("snippet", "")[:100]

    except CDPError as e:
        result.detail = f"CDP 错误: {e}"

    return result


def verify_batch(
    client: CDPClient,
    send_logs: list[dict],
) -> VerifyBatch:
    """批量验证已发送消息的送达状态。

    Args:
        client: CDP 客户端
        send_logs: 发送记录列表，每项含 job_id, company, boss_name

    Returns:
        VerifyBatch: 批量验证结果
    """
    batch = VerifyBatch()

    console.print(f"\n🔍 开始验证 {len(send_logs)} 条消息的送达状态\n")

    # 导航到聊天页
    try:
        client.evaluate("window.location.href = 'https://www.zhipin.com/web/geek/chat'")
        client.sleep(4)
    except CDPError as e:
        console.print(f"[red]导航到聊天页失败: {e}[/red]")
        return batch

    for log in send_logs:
        job_id = log.get("job_id", 0)
        boss_name = log.get("boss_name", "")
        company = log.get("company", "")

        result = verify_single(client, job_id, boss_name, company)
        batch.results.append(result)

        status_icon = {
            "delivered": "[green]✓ 送达[/]",
            "read": "[green]✓ 已读[/]",
            "sending": "[yellow]… 发送中[/]",
            "not_found": "[red]✗ 未找到[/]",
            "unknown": "[yellow]? 未知[/]",
        }.get(result.status, result.status)

        console.print(f"  #{job_id} {boss_name} ({company}) → {status_icon}")

        # 间隔避免被反爬
        time.sleep(1)

    # 打印汇总
    table = Table(title="送达验证汇总")
    table.add_column("#", style="cyan", width=4)
    table.add_column("招聘人")
    table.add_column("公司")
    table.add_column("状态", width=10)

    for r in batch.results:
        table.add_row(
            str(r.job_id),
            r.boss_name,
            r.company,
            r.status,
        )

    console.print(table)
    console.print(
        f"\n📊 总计: {batch.total} | "
        f"已读: {batch.read} | "
        f"送达: {batch.delivered} | "
        f"未确认: {batch.failed}"
    )

    return batch
