"""boss-pitch CLI 入口。

命令分为两类：

CLI 命令（浏览器操作层）：
  search   → 搜索岗位
  list     → 列出搜索结果
  pick     → 挑选目标岗位
  add      → 直接加 URL
  scrape   → 采集详情
  send     → 发送话术（stub）
  status   → 状态总览
  config   → 查看配置

AI 智能层（不在 CLI 里，由 SKILL.md 定义）：
  init     → 简历→profile
  generate → JD→话术
  review   → 审核
"""

from __future__ import annotations

import typer
from rich import print as rprint
from rich.panel import Panel
from rich.table import Table

from boss_pitch import __version__
from boss_pitch.config import get_config
from boss_pitch.store import Store

app = typer.Typer(
    name="boss-pitch",
    help="🎯 BOSS 直聘精品投递工具 — 不海投，精准出击",
    add_completion=False,
    rich_markup_mode="rich",
)


def _get_store() -> Store:
    """获取 Store 实例。"""
    config = get_config()
    return Store(base_dir=str(config.base_dir))


def _require_login(client) -> bool:
    """检查 BOSS 登录态，未登录则提示并返回 False。"""
    logged_in = client.check_login()
    if not logged_in:
        rprint("[red]❌ 未登录 BOSS 直聘[/red]")
        rprint("[dim]请先运行 [bold]boss-pitch login[/bold] 扫码登录[/dim]")
    return logged_in


def version_callback(value: bool):
    if value:
        rprint(f"[bold green]boss-pitch[/] {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool | None = typer.Option(
        None, "--version", "-v", help="显示版本号", callback=version_callback, is_eager=True
    ),
):
    """🎯 BOSS 直聘精品投递工具 — 不海投，精准出击"""
    pass


# ──────────────────────────────────────────────
# CLI 命令
# ──────────────────────────────────────────────


@app.command()
def search(
    keyword: str = typer.Argument(..., help="搜索关键词，如 'AI产品经理'"),
    city: str | None = typer.Option(
        None, "--city", "-c", help="城市筛选（北京/上海/广州/深圳/杭州）"
    ),
    salary: str | None = typer.Option(None, "--salary", "-s", help="薪资范围，如 20-40K"),
    page_limit: int = typer.Option(5, "--pages", "-p", help="最大翻页数"),
):
    """🔍 搜索 BOSS 直聘岗位，结果写入 search.md。

    通过 Chrome CDP 控制浏览器访问 BOSS 直聘搜索页，
    采集搜索结果并追加到今天的 search.md。
    """
    from boss_pitch.scraper.cdp import CDPClient
    from boss_pitch.scraper.search import search_jobs

    store = _get_store()
    city_str = city or ""
    salary_str = salary or ""

    rprint(f"🔍 搜索: [bold]{keyword}[/bold]")
    rprint(f"   城市={city_str or '不限'}  薪资={salary_str or '不限'}  翻页={page_limit}")

    with CDPClient() as client:
        if not _require_login(client):
            raise typer.Exit(1)
        results = search_jobs(client, keyword, city_str, salary_str, page_limit)

    store.append_search_results(results)
    rprint(f"✅ 搜索完成，找到 [bold green]{len(results)}[/bold green] 个岗位，已写入 search.md")


@app.command(name="list")
def list_jobs(
    limit: int = typer.Option(20, "--limit", "-n", help="显示条数"),
):
    """📋 列出搜索结果。

    从 search.md 读取并格式化展示搜索到的岗位。
    """
    store = _get_store()
    results = store.read_search_results()

    if not results:
        rprint("[dim]今天还没有搜索结果，请先运行 search 命令[/dim]")
        return

    table = Table(title="搜索结果")
    table.add_column("#", style="cyan", width=4)
    table.add_column("岗位", style="bold")
    table.add_column("公司")
    table.add_column("薪资", style="green")
    table.add_column("城市")
    table.add_column("URL")

    for r in results[:limit]:
        table.add_row(
            str(r["id"]),
            r["title"],
            r["company"],
            r["salary"],
            r["city"],
            f"[link={r['url']}]链接[/link]",
        )

    rprint(table)
    if len(results) > limit:
        rprint(f"[dim]还有 {len(results) - limit} 条，用 --limit 查看更多[/dim]")


@app.command()
def pick(
    job_ids: list[int] | None = typer.Argument(None, help="要选中的岗位 ID"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="交互式挑选"),
):
    """✅ 挑选目标岗位，写入 picked.md。

    从 search.md 中挑选岗位，写入 picked.md。
    支持 --interactive 模式逐条确认。

    TODO:
        - 交互式模式：rich confirm 逐条选择
    """
    store = _get_store()

    if not job_ids:
        rprint("[dim]请指定岗位 ID，或使用 -i 进入交互模式[/dim]")
        return

    store.write_picked(job_ids)
    rprint(f"✅ 已挑选 {len(job_ids)} 个岗位，写入 picked.md")


@app.command()
def add(
    url: str = typer.Argument(..., help="BOSS 直聘岗位 URL"),
):
    """➕ 直接添加岗位 URL 到 search.md。

    将 URL 追加到 search.md，后续可通过 scrape 采集详情。
    """
    store = _get_store()
    results = store.read_search_results()
    next_id = len(results) + 1

    store.append_search_results([{
        "id": next_id,
        "title": "待采集",
        "company": "待采集",
        "salary": "",
        "city": "",
        "url": url,
    }])
    rprint(f"➕ 已添加: [link={url}]{url}[/link] (#{next_id})")


@app.command()
def scrape(
    job_ids: list[int] | None = typer.Argument(
        None, help="指定岗位 ID，不填则处理所有 picked"
    ),
):
    """📄 采集选中岗位的 JD 详情。

    对 picked 状态的岗位，通过 CDP 逐个访问详情页，
    提取完整 JD 文本、薪资、要求等信息，写入 details/。
    """
    from boss_pitch.scraper.cdp import CDPClient
    from boss_pitch.scraper.detail import scrape_detail

    store = _get_store()
    target_ids = job_ids

    if not target_ids:
        picked = store.read_picked()
        if not picked:
            rprint("[dim]没有可采集的岗位，请先 pick 或指定 ID[/dim]")
            return
        target_ids = [p["id"] for p in picked]

    # 构建 job 列表（需要 url）
    if job_ids:
        # 指定 ID 时，从 search 结果中获取 url
        all_results = store.read_search_results()
        jobs = [r for r in all_results if r["id"] in set(target_ids)]
        if not jobs:
            # 也检查 picked
            picked = store.read_picked()
            jobs = [r for r in picked if r["id"] in set(target_ids)]
    else:
        jobs = picked  # type: ignore[assignment]

    if not jobs:
        rprint(f"[dim]未找到 ID 为 {target_ids} 的岗位[/dim]")
        return

    rprint(f"📄 待采集: {target_ids}")

    with CDPClient() as client:
        if not _require_login(client):
            raise typer.Exit(1)

        for job in jobs:
            url = job.get("url", "")
            if not url:
                rprint(f"  ⚠️ #{job.get('id', '?')} 无 URL，跳过")
                continue
            job_id = job.get("id", 0)

            # 每条 JD 独立 tab（更稳定，避免 WS 断连）
            tab = client.create_tab(url)
            tid = tab.get("targetId")
            client.sleep(6)

            if tid:
                client.attach(target_id=tid)
            else:
                client.attach()

            # 等页面渲染
            client.sleep(4)

            # 提取数据（复用 detail.py 的 JS）
            from boss_pitch.scraper.detail import _JS_EXTRACT_DETAIL
            result = client.evaluate(_JS_EXTRACT_DETAIL) or {}
            result["id"] = job_id
            result["url"] = url

            # 确保 target_id 不丢
            saved_tid = tid

            # detach + 关闭 tab
            client.detach()
            if saved_tid:
                try:
                    client.send_browser_command("Target.closeTarget", {"targetId": saved_tid})
                except Exception:
                    pass

            # 确保字段完整
            for key in ("title", "salary", "company", "city", "experience",
                        "education", "jd_text", "boss_name", "boss_role"):
                result[key] = str(result.get(key, ""))

            store.write_detail(result)
            rprint(f"  ✅ #{job_id} {result.get('company', '')} · {result.get('title', '')}")
            client.sleep(3)

    rprint(f"✅ 采集完成，共 {len(jobs)} 个岗位")


@app.command()
def send(
    job_ids: list[int] | None = typer.Argument(None, help="指定岗位 ID"),
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="模拟发送，不实际操作"),
    batch_size: int = typer.Option(4, "--batch-size", "-b", help="每批最大条数"),
    batch_interval: int = typer.Option(300, "--interval", "-i", help="批次间隔秒数"),
):
    """📤 发送话术到 BOSS 直聘。

    从 pitches.md 读取已审核的话术，通过 CDP 逐个发送。
    支持指定岗位 ID 发送，不指定则发送所有已审核话术。

    安全策略：
        - 每条间隔 8-12 秒（随机）
        - 每批最多 4 条，批次间等 5 分钟
        - 连续 2 条失败自动停止
        - 每条发送后做侧栏交叉验证
    """
    from boss_pitch.scraper.cdp import CDPClient
    from boss_pitch.sender.sender import send_pitches_batch

    store = _get_store()
    pitches = store.read_pitches()

    if not pitches:
        rprint("[dim]没有可发送的话术，请先生成（boss-pitch generate 或 AI 层）[/dim]")
        return

    # 获取岗位详情
    details = store.list_details()
    detail_map = {d["id"]: d for d in details}

    # 筛选目标
    target_ids = set(job_ids) if job_ids else set(p["id"] for p in pitches)

    jobs = []
    matched_pitches = []
    for pitch in pitches:
        pid = pitch["id"]
        if pid not in target_ids:
            continue

        detail = detail_map.get(pid, {})
        job = {
            "id": pid,
            "url": detail.get("url", ""),
            "company": pitch.get("company", detail.get("company", "")),
            "title": pitch.get("title", detail.get("title", "")),
            "boss_name": pitch.get("boss_name", detail.get("boss_name", "")),
        }
        jobs.append(job)
        matched_pitches.append(pitch)

    if not jobs:
        rprint(f"[dim]未找到匹配的岗位 (IDs: {target_ids})[/dim]")
        return

    rprint(f"📤 准备发送 {len(jobs)} 条话术")

    with CDPClient() as client:
        if not _require_login(client):
            raise typer.Exit(1)

        # attach 到第一个 zhipin 页面
        targets = client.get_targets()
        zhipin_targets = [t for t in targets if t.get("type") == "page" and "zhipin.com" in t.get("url", "")]
        if zhipin_targets:
            client.attach(target_id=zhipin_targets[0]["id"])
        else:
            # 没有已打开的页面，创建新 tab
            tab = client.create_tab("https://www.zhipin.com")
            client.sleep(3)
            client.attach(target_id=tab["targetId"])

        result = send_pitches_batch(
            client=client,
            jobs=jobs,
            pitches=matched_pitches,
            dry_run=dry_run,
            batch_size=batch_size,
            batch_interval=batch_interval,
        )

    # 保存发送日志
    log_entries = []
    for r in result.results:
        log_entries.append({
            "job_id": r.job_id,
            "company": r.company,
            "title": r.title,
            "boss_name": r.boss_name,
            "scene": r.scene,
            "status": r.status,
            "error": r.error,
        })
    store.write_send_log(log_entries)
    rprint(f"\n📝 发送日志已写入 send-log.md")


@app.command()
def verify():
    """🔍 验证已发消息的送达状态。

    读取 send-log.md 中已发送的记录，通过 CDP 导航到聊天页，
    检查每条消息的送达/已读状态。
    """
    from boss_pitch.scraper.cdp import CDPClient
    from boss_pitch.sender.verify import verify_batch

    store = _get_store()
    send_log = store.read_send_log()

    if not send_log:
        rprint("[dim]没有发送记录可验证[/dim]")
        return

    # 只验证已发送/送达的记录（跳过 dry_run 和 failed）
    to_verify = [
        log for log in send_log
        if log.get("status") in ("sent", "delivered", "sending")
    ]

    if not to_verify:
        rprint("[dim]没有需要验证的记录（只验证 status=sent/delivered/sending）[/dim]")
        return

    rprint(f"🔍 验证 {len(to_verify)} 条消息的送达状态")

    with CDPClient() as client:
        if not _require_login(client):
            raise typer.Exit(1)

        targets = client.get_targets()
        zhipin_targets = [t for t in targets if t.get("type") == "page" and "zhipin.com" in t.get("url", "")]
        if zhipin_targets:
            client.attach(target_id=zhipin_targets[0]["id"])
        else:
            tab = client.create_tab("https://www.zhipin.com/web/geek/chat")
            client.sleep(3)
            client.attach(target_id=tab["targetId"])

        result = verify_batch(client=client, send_logs=to_verify)

    rprint(f"\n📊 已读: {result.read} | 送达: {result.delivered} | 未确认: {result.failed}")


@app.command()
def status():
    """📊 查看状态总览。

    统计各状态的岗位数量：searched / picked / scraped / pitched。
    """
    store = _get_store()
    summary = store.get_status_summary()

    table = Table(title="投递状态总览")
    table.add_column("阶段", style="bold")
    table.add_column("数量", style="cyan", justify="right")

    table.add_row("🔍 已搜索", str(summary["searched"]))
    table.add_row("✅ 已挑选", str(summary["picked"]))
    table.add_row("📄 已采集", str(summary["scraped"]))
    table.add_row("💬 已话术", str(summary["pitched"]))

    rprint(table)


@app.command()
def config(
    key: str | None = typer.Argument(None, help="配置项名称"),
    value: str | None = typer.Argument(None, help="配置项值"),
    list_all: bool = typer.Option(False, "--list", "-l", help="列出所有配置"),
):
    """⚙️ 查看/修改配置。

    不带参数 → 显示当前配置
    key value → 设置配置项
    --list → 列出所有配置（含默认值）
    """
    cfg = get_config()

    if list_all:
        rprint(Panel("⚙️ 所有配置项", subtitle="来自环境变量和默认值"))
        for k, v in cfg.model_dump().items():
            rprint(f"  {k}: [cyan]{v}[/cyan]")
    elif key and value:
        rprint(f"⚙️ 设置: {key} = {value}")
        rprint("[dim]暂未实现配置持久化[/dim]")
    elif key:
        v = getattr(cfg, key, None)
        if v is not None:
            rprint(f"⚙️ {key} = [cyan]{v}[/cyan]")
        else:
            rprint(f"[red]未知配置项: {key}[/red]")
    else:
        rprint(Panel("⚙️ 当前配置"))
        for k, v in cfg.model_dump().items():
            rprint(f"  {k}: [cyan]{v}[/cyan]")


@app.command()
def login():
    """🔑 登录 BOSS 直聘。

    打开 Chrome 浏览器到 BOSS 登录页，等待用户扫码登录。
    登录态保存在 ~/.boss-pitch/chrome-data/ 中，后续自动复用。
    """
    import time
    from boss_pitch.scraper.cdp import CDPClient, launch_chrome

    cfg = get_config()

    # 检查 Chrome 是否在跑
    client = CDPClient(port=cfg.cdp_port)
    if not client.is_alive():
        rprint("🚀 正在启动 Chrome...")
        launch_chrome(port=cfg.cdp_port, user_data_dir=str(cfg.user_data_dir))
        time.sleep(5)

    with CDPClient(port=cfg.cdp_port) as c:
        # 创建 tab 导航到 BOSS 登录页
        result = c.create_tab("https://www.zhipin.com/web/user/?from=passport-zp")
        tid = result["targetId"]
        time.sleep(3)
        c.attach(target_id=tid)

        rprint(Panel(
            "请在 Chrome 浏览器中扫码登录 BOSS 直聘\n"
            "登录成功后按 [bold]Enter[/bold] 继续",
            title="🔑 BOSS 直聘登录",
            border_style="green",
        ))

        # 等待用户确认
        input()

        # 验证登录态
        url = c.evaluate("window.location.href")
        title = c.evaluate("document.title")

        if "zhipin.com/web/user" in url and "登录" in title:
            rprint("[yellow]⚠️ 当前仍在登录页，登录可能未完成[/yellow]")
            rprint("[dim]如果已扫码，请刷新页面后重试[/dim]")
        else:
            rprint("[green]✅ 登录成功！Cookie 已保存在 Chrome profile 中[/green]")
            rprint("[dim]现在可以运行 boss-pitch search 搜索岗位了[/dim]")
