"""Markdown 文件存储。

所有数据用 markdown 文件，按日期组织：

    ~/.boss-pitch/
    ├── config.yaml
    ├── profile.yaml
    ├── chrome-data/
    └── jobs/
        └── 2026-06-04/
            ├── search.md
            ├── picked.md
            ├── details/
            │   ├── 001-固生堂-AI医疗产品经理.md
            │   └── ...
            └── pitches.md
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import yaml


class Store:
    """Markdown 文件存储。"""

    def __init__(self, base_dir: str = "~/.boss-pitch"):
        self.base_dir = Path(base_dir).expanduser()
        self.jobs_dir = self.base_dir / "jobs"

    def today_dir(self) -> Path:
        """返回今天的目录，不存在则创建。"""
        d = self.jobs_dir / datetime.now().strftime("%Y-%m-%d")
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── search.md 操作 ──────────────────────────

    def append_search_results(self, results: list[dict]) -> None:
        """追加搜索结果到 search.md。

        格式为 markdown 表格：
        | # | 岗位 | 公司 | 薪资 | 城市 | URL |
        |---|------|------|------|------|-----|
        | 1 | AI产品经理 | 固生堂 | 20-35K·13薪 | 杭州 | [链接](url) |

        Args:
            results: 搜索结果列表，每项包含
                title, company, salary, city, url 等字段
        """
        today = self.today_dir()
        search_file = today / "search.md"

        # 读取已有结果，确定起始编号
        existing = self.read_search_results()
        start_idx = len(existing) + 1

        lines: list[str] = []
        if not search_file.exists():
            lines.append(f"# 搜索结果 {datetime.now().strftime('%Y-%m-%d')}\n")
            lines.append("| # | 岗位 | 公司 | 薪资 | 城市 | URL |")
            lines.append("|---|------|------|------|------|-----|")

        for i, r in enumerate(results, start=start_idx):
            title = r.get("title", "")
            company = r.get("company", "")
            salary = r.get("salary", "")
            city = r.get("city", "")
            url = r.get("url", "")
            lines.append(
                f"| {i} | {title} | {company} | {salary} | {city} | [链接]({url}) |"
            )

        with open(search_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def read_search_results(self) -> list[dict]:
        """读取 search.md 解析为列表。

        Returns:
            搜索结果列表，每项包含
            id, title, company, salary, city, url 字段
        """
        today = self.today_dir()
        search_file = today / "search.md"
        if not search_file.exists():
            return []

        text = search_file.read_text(encoding="utf-8")
        results: list[dict] = []
        for line in text.splitlines():
            # 匹配表格行 | 1 | AI产品经理 | 固生堂 | 20-35K·13薪 | 杭州 | [链接](url) |
            m = re.match(
                r"\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*\[.*?\]\((.+?)\)\s*\|",
                line,
            )
            if m:
                results.append({
                    "id": int(m.group(1)),
                    "title": m.group(2).strip(),
                    "company": m.group(3).strip(),
                    "salary": m.group(4).strip(),
                    "city": m.group(5).strip(),
                    "url": m.group(6).strip(),
                })
        return results

    # ── picked.md 操作 ──────────────────────────

    def write_picked(self, job_ids: list[int]) -> None:
        """写 picked.md，从 search.md 中挑出的条目。

        Args:
            job_ids: 要选中的岗位 ID 列表
        """
        today = self.today_dir()
        all_results = self.read_search_results()
        picked = [r for r in all_results if r["id"] in job_ids]

        lines = [f"# 已选岗位 {datetime.now().strftime('%Y-%m-%d')}\n"]
        lines.append("| # | 岗位 | 公司 | 薪资 | 城市 | URL |")
        lines.append("|---|------|------|------|------|-----|")
        for r in picked:
            lines.append(
                f"| {r['id']} | {r['title']} | {r['company']} | {r['salary']} "
                f"| {r['city']} | [链接]({r['url']}) |"
            )

        picked_file = today / "picked.md"
        picked_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def read_picked(self) -> list[dict]:
        """读取 picked.md。

        Returns:
            已选岗位列表，格式同 read_search_results
        """
        today = self.today_dir()
        picked_file = today / "picked.md"
        if not picked_file.exists():
            return []

        text = picked_file.read_text(encoding="utf-8")
        results: list[dict] = []
        for line in text.splitlines():
            m = re.match(
                r"\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*\[.*?\]\((.+?)\)\s*\|",
                line,
            )
            if m:
                results.append({
                    "id": int(m.group(1)),
                    "title": m.group(2).strip(),
                    "company": m.group(3).strip(),
                    "salary": m.group(4).strip(),
                    "city": m.group(5).strip(),
                    "url": m.group(6).strip(),
                })
        return results

    # ── details/ 操作 ───────────────────────────

    def write_detail(self, job: dict) -> Path:
        """写 details/001-公司-岗位.md。

        格式为带 YAML frontmatter 的 markdown 文件。

        Args:
            job: 岗位信息字典，需要包含
                id, url, company, title, salary, city,
                boss_name, boss_role, jd_text 等字段

        Returns:
            写入文件的路径
        """
        today = self.today_dir()
        details_dir = today / "details"
        details_dir.mkdir(parents=True, exist_ok=True)

        job_id = job.get("id", 0)
        company = job.get("company", "未知公司")
        title = job.get("title", "未知岗位")
        # 文件名安全化
        safe_company = re.sub(r"[^\w\u4e00-\u9fff-]", "", company)
        safe_title = re.sub(r"[^\w\u4e00-\u9fff-]", "", title)
        filename = f"{job_id:03d}-{safe_company}-{safe_title}.md"
        filepath = details_dir / filename

        frontmatter = {
            "id": job_id,
            "url": job.get("url", ""),
            "company": company,
            "title": title,
            "salary": job.get("salary", ""),
            "city": job.get("city", ""),
            "boss_name": job.get("boss_name", ""),
            "boss_role": job.get("boss_role", ""),
            "status": job.get("status", "scraped"),
            "source": job.get("source", "search"),
            "scraped_at": datetime.now().isoformat(),
        }

        content = "---\n"
        content += yaml.dump(
            frontmatter, allow_unicode=True, default_flow_style=False
        )
        content += "---\n\n"
        content += "## 岗位描述\n\n"
        content += job.get("jd_text", "（待采集）")
        content += "\n"

        filepath.write_text(content, encoding="utf-8")
        return filepath

    def read_detail(self, job_id: int) -> dict | None:
        """读单个详情文件。

        Args:
            job_id: 岗位 ID

        Returns:
            岗位详情字典（frontmatter + jd_text），文件不存在则返回 None
        """
        today = self.today_dir()
        details_dir = today / "details"
        if not details_dir.exists():
            return None

        # 按 ID 前缀匹配文件
        prefix = f"{job_id:03d}-"
        for f in details_dir.iterdir():
            if f.name.startswith(prefix) and f.suffix == ".md":
                return self._parse_detail_file(f)
        return None

    def list_details(self) -> list[dict]:
        """列出所有已采集的详情。

        Returns:
            岗位详情列表
        """
        today = self.today_dir()
        details_dir = today / "details"
        if not details_dir.exists():
            return []

        results: list[dict] = []
        for f in sorted(details_dir.glob("*.md")):
            detail = self._parse_detail_file(f)
            if detail:
                results.append(detail)
        return results

    def update_detail_status(self, job_id: int, status: str) -> None:
        """更新详情文件的 status 字段（YAML frontmatter）。

        Args:
            job_id: 岗位 ID
            status: 新状态值
        """
        detail = self.read_detail(job_id)
        if detail is None:
            return

        today = self.today_dir()
        details_dir = today / "details"
        prefix = f"{job_id:03d}-"
        for f in details_dir.iterdir():
            if f.name.startswith(prefix) and f.suffix == ".md":
                # 重新构建文件
                detail["status"] = status
                self.write_detail(detail)
                return

    def _parse_detail_file(self, filepath: Path) -> dict | None:
        """解析单个详情文件。

        Args:
            filepath: 详情文件路径

        Returns:
            解析后的字典（frontmatter + jd_text）
        """
        text = filepath.read_text(encoding="utf-8")
        # 解析 YAML frontmatter
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
        if not m:
            return None

        try:
            frontmatter = yaml.safe_load(m.group(1))
        except yaml.YAMLError:
            return None

        if not isinstance(frontmatter, dict):
            return None

        # 提取 JD 正文（去掉 "## 岗位描述" 标题）
        body = m.group(2).strip()
        body = re.sub(r"^## 岗位描述\s*\n*", "", body)

        frontmatter["jd_text"] = body
        return frontmatter

    # ── pitches.md 操作 ─────────────────────────

    def write_pitches(self, pitches: list[dict]) -> None:
        """写 pitches.md。

        格式：
            # 话术汇总 2026-06-04

            ## #1 固生堂 · AI医疗产品经理 · 陈女士(HRBP)

            陈女士好，...

            ---

        Args:
            pitches: 话术列表，每项包含
                id, company, title, boss_name, boss_role, text 字段
        """
        today = self.today_dir()
        pitches_file = today / "pitches.md"

        lines = [f"# 话术汇总 {datetime.now().strftime('%Y-%m-%d')}\n"]
        for p in pitches:
            boss_info = f"{p.get('boss_name', '')}"
            boss_role = p.get("boss_role", "")
            if boss_role:
                boss_info += f"({boss_role})"
            job_id = p.get("id", 0)
            company = p.get("company", "")
            title = p.get("title", "")
            header = f"## #{job_id} {company} · {title} · {boss_info}"
            lines.append(header)
            lines.append("")
            lines.append(p.get("text", ""))
            lines.append("")
            lines.append("---")
            lines.append("")

        pitches_file.write_text("\n".join(lines), encoding="utf-8")

    def read_pitches(self) -> list[dict]:
        """解析 pitches.md 为列表。

        Returns:
            话术列表，每项包含 id, company, title, boss_name, boss_role, text
        """
        today = self.today_dir()
        pitches_file = today / "pitches.md"
        if not pitches_file.exists():
            return []

        text = pitches_file.read_text(encoding="utf-8")
        results: list[dict] = []

        # 按 ## # 开头的行分割各条话术
        sections = re.split(r"(?=^## #\d+)", text, flags=re.MULTILINE)
        for section in sections:
            section = section.strip()
            if not section:
                continue
            # 解析标题 ## #1 固生堂 · AI医疗产品经理 · 陈女士(HRBP)
            header_m = re.match(
                r"##\s*#(\d+)\s+(.+?)\s*·\s*(.+?)\s*·\s*(.+?)(?:\((.+?)\))?\s*$",
                section,
                re.MULTILINE,
            )
            if not header_m:
                continue

            job_id = int(header_m.group(1))
            company = header_m.group(2).strip()
            title = header_m.group(3).strip()
            boss_name = header_m.group(4).strip()
            boss_role = header_m.group(5) or ""

            # 提取话术正文（标题之后的内容）
            lines = section.split("\n")
            body_start = 0
            for i, line in enumerate(lines):
                if line.startswith("## #"):
                    body_start = i + 1
                    break
            body = "\n".join(lines[body_start:]).strip()

            results.append({
                "id": job_id,
                "company": company,
                "title": title,
                "boss_name": boss_name,
                "boss_role": boss_role,
                "text": body,
            })

        return results

    def update_pitch(self, index: int, new_text: str) -> None:
        """更新第 N 条话术。

        Args:
            index: 话术索引（从 0 开始）
            new_text: 新的话术文本
        """
        pitches = self.read_pitches()
        if 0 <= index < len(pitches):
            pitches[index]["text"] = new_text
            self.write_pitches(pitches)

    # ── send-log.md 操作 ──────────────────────────

    def write_send_log(self, results: list[dict]) -> None:
        """追加发送日志到 send-log.md。

        格式：
            ## 发送记录 2026-06-04 14:30:00

            | # | 公司 | 岗位 | 招聘人 | 场景 | 状态 | 说明 |
            |---|------|------|--------|------|------|------|

        Args:
            results: 发送结果列表，每项包含
                job_id, company, title, boss_name, scene, status, error
        """
        today = self.today_dir()
        log_file = today / "send-log.md"

        lines = [f"\n## 发送记录 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"]
        lines.append("| # | 公司 | 岗位 | 招聘人 | 场景 | 状态 | 说明 |")
        lines.append("|---|------|------|--------|------|------|------|")

        for r in results:
            job_id = r.get("job_id", 0)
            company = r.get("company", "")
            title = r.get("title", "")
            boss_name = r.get("boss_name", "")
            scene = r.get("scene", "")
            status = r.get("status", "")
            error = r.get("error", "")
            lines.append(
                f"| {job_id} | {company} | {title} | {boss_name} "
                f"| {scene} | {status} | {error} |"
            )

        lines.append("")

        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def read_send_log(self) -> list[dict]:
        """读取 send-log.md 解析为列表。

        Returns:
            发送记录列表
        """
        today = self.today_dir()
        log_file = today / "send-log.md"
        if not log_file.exists():
            return []

        text = log_file.read_text(encoding="utf-8")
        results: list[dict] = []
        for line in text.splitlines():
            m = re.match(
                r"\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*"
                r"\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|",
                line,
            )
            if m:
                results.append({
                    "job_id": int(m.group(1)),
                    "company": m.group(2).strip(),
                    "title": m.group(3).strip(),
                    "boss_name": m.group(4).strip(),
                    "scene": m.group(5).strip(),
                    "status": m.group(6).strip(),
                    "error": m.group(7).strip(),
                })
        return results

    # ── status 操作 ─────────────────────────────

    def get_status_summary(self) -> dict:
        """统计各状态数量。

        Returns:
            状态统计字典，如
            {"searched": 20, "picked": 5, "scraped": 3, "pitched": 2}
        """
        searched = len(self.read_search_results())
        picked = len(self.read_picked())
        scraped = len(self.list_details())
        pitched = len(self.read_pitches())
        return {
            "searched": searched,
            "picked": picked,
            "scraped": scraped,
            "pitched": pitched,
        }
