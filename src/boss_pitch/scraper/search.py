"""BOSS 直聘搜索页采集。

通过浏览器内 fetch API 调用 BOSS 直聘搜索接口，获取结构化 JSON 结果。
比 DOM 选择器更稳定、更快、不受页面改版影响。

原理：
    1. 通过 CDP 创建新 tab，导航到 BOSS 页面获取 cookie
    2. 在该页面上下文中执行 fetch('/wapi/zpgeek/search/joblist.json')
    3. 解析返回的 JSON，提取岗位列表
    4. 翻页通过 page 参数实现
    5. 关闭 tab，返回结果

注意：
    - 需要已登录的浏览器 session（cookie）
    - fetch 必须在 zhipin.com 域名下执行（否则没有 cookie）
"""

from __future__ import annotations

import json
from urllib.parse import quote_plus

from rich.console import Console

from boss_pitch.scraper.cdp import CDPClient

console = Console(stderr=True)

# 城市名 → 城市编码映射
CITY_CODES: dict[str, str] = {
    "北京": "101010100",
    "上海": "101020100",
    "广州": "101280100",
    "深圳": "101280600",
    "杭州": "101210100",
}

# 浏览器内 fetch 搜索 API 的 JS 模板
# 注意：用 IIFE (async()=>{...})() 而不是 async()=>{...}
# 后者只是函数声明，前者立即执行返回 Promise
_JS_FETCH_SEARCH = """
(async () => {
    try {
        const params = {params};
        const queryStr = Object.entries(params)
            .map(([k, v]) => `${k}=${encodeURIComponent(v)}`)
            .join('&');
        const url = `https://www.zhipin.com/wapi/zpgeek/search/joblist.json?${queryStr}`;

        const resp = await fetch(url, {
            credentials: "include",
            headers: {
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://www.zhipin.com/web/geek/job",
            }
        });
        const data = await resp.json();
        return JSON.stringify(data);
    } catch (e) {
        return JSON.stringify({error: e.toString()});
    }
})()
"""


def _build_api_params(keyword: str, city: str = "", page: int = 1,
                      page_size: int = 30) -> dict:
    """构建搜索 API 参数。"""
    params = {
        "scene": "1",
        "query": keyword,
        "page": str(page),
        "pageSize": str(page_size),
    }
    if city:
        code = CITY_CODES.get(city, city)
        params["city"] = code
    return params


def _parse_api_response(raw_json: str) -> tuple[list[dict], bool]:
    """解析搜索 API 返回的 JSON。

    Returns:
        (jobs, has_more): 岗位列表 + 是否还有下一页
    """
    data = json.loads(raw_json)

    if data.get("code") != 0:
        console.print(f"  ⚠️ API 返回错误: code={data.get('code')} msg={data.get('message', '')}")
        return [], False

    job_list = data.get("zpData", {}).get("jobList", [])
    has_more = len(job_list) > 0

    results = []
    for job in job_list:
        encrypt_id = job.get("encryptJobId", "")
        results.append({
            "title": job.get("jobName", ""),
            "company": job.get("brandName", ""),
            "salary": job.get("salaryDesc", ""),
            "city": job.get("cityName", ""),
            "district": job.get("areaDistrict", ""),
            "business_district": job.get("businessDistrict", ""),
            "experience": job.get("jobExperience", ""),
            "education": job.get("jobDegree", ""),
            "skills": job.get("skills", []),
            "labels": job.get("jobLabels", []),
            "boss_name": job.get("bossInfo", {}).get("bossName", "") if isinstance(job.get("bossInfo"), dict) else "",
            "boss_title": job.get("bossInfo", {}).get("bossTitle", "") if isinstance(job.get("bossInfo"), dict) else "",
            "url": f"https://www.zhipin.com/job_detail/{encrypt_id}.html" if encrypt_id else "",
            "encrypt_job_id": encrypt_id,
        })

    return results, has_more


def search_jobs(
    client: CDPClient,
    keyword: str,
    city: str = "",
    salary: str = "",
    page_limit: int = 5,
    delay: float = 3.0,
    page_size: int = 30,
) -> list[dict]:
    """搜索 BOSS 直聘岗位。

    通过浏览器内 fetch API 调用搜索接口，获取结构化结果。
    需要 CDP client 已连接到已登录的浏览器。

    Args:
        client: CDPClient 实例
        keyword: 搜索关键词
        city: 城市名（北京/上海/广州/深圳/杭州）或城市编码
        salary: 薪资筛选（如 "20-40K"）
        page_limit: 最大翻页数（默认 5）
        delay: 翻页间隔秒数（默认 3.0）
        page_size: 每页条数（默认 30）

    Returns:
        list[dict]: 搜索结果列表
    """
    city_str = city
    console.print(f"🔍 搜索: {keyword}  城市={city_str or '不限'}  翻页={page_limit}")

    # 创建新 tab，导航到 BOSS 页面以获取 cookie 上下文
    tab_info = client.create_tab("https://www.zhipin.com/web/geek/job")
    target_id = tab_info.get("targetId")
    client.sleep(6)

    if target_id:
        client.attach(target_id=target_id)
    else:
        client.attach()

    all_results: list[dict] = []
    seen_ids: set[str] = set()

    try:
        for page_num in range(1, page_limit + 1):
            console.print(f"  📄 第 {page_num} 页...")

            params = _build_api_params(keyword, city_str, page_num, page_size)
            js = _JS_FETCH_SEARCH.replace("{params}", json.dumps(params, ensure_ascii=False))

            raw = client.evaluate(js, await_promise=True, timeout=30)
            if not raw:
                console.print(f"  ⚠️ 第 {page_num} 页返回为空，停止")
                break

            page_results, has_more = _parse_api_response(raw)

            if not page_results:
                console.print(f"  ⚠️ 第 {page_num} 页无结果，停止")
                break

            # 去重（基于 encryptJobId）
            new_count = 0
            for item in page_results:
                eid = item.get("encrypt_job_id", "")
                if eid and eid not in seen_ids:
                    seen_ids.add(eid)
                    all_results.append(item)
                    new_count += 1

            console.print(f"  ✅ 第 {page_num} 页: {len(page_results)} 条，新增 {new_count} 条")

            if new_count == 0 or not has_more:
                console.print("  🏁 没有更多结果，停止")
                break

            if page_num >= page_limit:
                console.print(f"  🏁 已达到 page_limit={page_limit}，停止")
                break

            # 翻页间隔
            client.sleep(delay)

    finally:
        # 清理：detach 并关闭 tab
        client.detach()
        if target_id:
            try:
                client.send_browser_command("Target.closeTarget", {"targetId": target_id})
            except Exception:
                pass

    console.print(f"🔍 搜索完成，共 {len(all_results)} 个岗位（去重后）")
    return all_results
