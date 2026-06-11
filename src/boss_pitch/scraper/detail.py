"""BOSS 直聘岗位详情页采集。

对已挑选（picked）的岗位，访问其详情页，提取完整的 JD 文本。

工作流：
    1. 通过 CDP 导航到 JD 详情页
    2. 等待页面加载完成
    3. 提取岗位名称、薪资、公司、城市、经验、学历
    4. 提取 JD 正文（岗位职责和要求）
    5. 提取招聘人姓名和角色
    6. 返回组装好的详情字典

注意事项：
    - 详情页加载可能较慢，需要合理等待
    - .job-banner .name h1 里可能包含 span（薪资标签），要取第一个 text node
    - .boss-info-attr 是可靠的招聘人角色来源，格式是"公司名·职位"
    - JD 正文用 .job-sec-text 的 innerText
"""

from __future__ import annotations

from rich.console import Console

from boss_pitch.scraper.cdp import CDPClient

console = Console(stderr=True)

# 抓取详情页的 JS 表达式
_JS_EXTRACT_DETAIL = """
(() => {
    const title = document.querySelector('.job-banner .name h1');
    const salary = document.querySelector('.job-banner .salary');
    const companyMatch = document.title.match(/招聘」_(.+?)招聘/);
    const company = companyMatch ? companyMatch[1] : '';
    const jdText = document.querySelector('.job-sec-text');
    const bossNameEl = document.querySelector('.job-boss-info h2.name');
    const bossName = bossNameEl ? bossNameEl.childNodes[0].textContent.trim() : '';
    const bossAttr = document.querySelector('.boss-info-attr');
    const activeTime = document.querySelector('.boss-active-time');

    return {
        title: title ? title.childNodes[0].textContent.trim() : '',
        salary: salary ? salary.textContent.trim() : '',
        company: company,
        city: '',
        experience: '',
        education: '',
        jd_text: jdText ? jdText.innerText.trim() : '',
        boss_name: bossName,
        boss_role: bossAttr ? bossAttr.textContent.trim() : '',
        boss_active: activeTime ? activeTime.textContent.trim() : ''
    };
})()
"""


def scrape_detail(client: CDPClient, url: str, job_id: int = 0) -> dict:
    """采集单个 JD 详情。

    导航到详情页，等待加载完成后提取所有信息。

    Args:
        client: CDPClient 实例
        url: JD 详情页 URL
        job_id: 岗位编号（用于日志）

    Returns:
        dict: 包含 title, company, salary, city, experience, education,
              jd_text, boss_name, boss_role 等字段
    """
    console.print(f"  📄 采集 #{job_id}: {url}")

    # 导航到详情页
    client.navigate(url, wait=True, timeout=30)

    # 等待页面渲染完成（不用 wait_for_selector，会搞坏 WS）
    client.sleep(4)

    # 执行 JS 提取数据
    result = client.evaluate(_JS_EXTRACT_DETAIL)

    if not result:
        console.print(f"  ⚠️ #{job_id} 提取结果为空")
        return {
            "title": "",
            "salary": "",
            "company": "",
            "city": "",
            "experience": "",
            "education": "",
            "jd_text": "",
            "boss_name": "",
            "boss_role": "",
        }

    # 确保所有字段都是字符串
    for key in ("title", "salary", "company", "city", "experience", "education",
                "jd_text", "boss_name", "boss_role"):
        result[key] = str(result.get(key, ""))

    console.print(
        f"  ✅ #{job_id} {result.get('company', '')} · "
        f"{result.get('title', '')} · {result.get('salary', '')}"
    )
    return result
