"""boss-pitch CLI 测试。

验证调整后的命令（不含 init/generate/review）：
- test_app_help: CLI --help 能正常输出
- test_version: CLI --version 能正常输出版本号
- test_commands_registered: 验证命令注册正确
- test_store_create_today_dir: Store 能创建日期目录
- test_store_search_results: Store 能读写搜索结果
- test_store_write_detail: Store 能写详情文件
"""

import tempfile
from pathlib import Path

from typer.testing import CliRunner

from boss_pitch.cli import app
from boss_pitch.store import Store

runner = CliRunner()


def test_app_help():
    """CLI --help 能正常输出。"""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "boss-pitch" in result.output.lower() or "BOSS" in result.output


def test_version():
    """CLI --version 能正常输出版本号。"""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_commands_registered():
    """验证命令注册正确。"""
    # 应该存在的命令
    expected_commands = [
        "search", "list", "pick", "add", "scrape", "send", "verify", "status", "config",
    ]
    for cmd in expected_commands:
        result = runner.invoke(app, [cmd, "--help"])
        assert result.exit_code == 0, f"命令 '{cmd}' 注册失败"

    # 不应该存在的命令（AI 层由 skill 文档驱动，不在 CLI 中）
    removed_commands = ["init", "generate", "review", "log", "export"]
    for cmd in removed_commands:
        result = runner.invoke(app, [cmd, "--help"])
        assert result.exit_code != 0, f"命令 '{cmd}' 应该已被移除"


def test_store_create_today_dir():
    """Store 能创建今天的日期目录。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = Store(base_dir=tmpdir)
        today = store.today_dir()
        assert today.exists()
        assert today.parent.name == "jobs"


def test_store_search_results():
    """Store 能读写搜索结果。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = Store(base_dir=tmpdir)

        # 写入
        results = [
            {
                "title": "AI产品经理",
                "company": "固生堂",
                "salary": "20-35K·13薪",
                "city": "杭州",
                "url": "https://www.zhipin.com/job_detail/abc123.html",
            },
            {
                "title": "产品总监",
                "company": "无际云帆",
                "salary": "30-50K",
                "city": "北京",
                "url": "https://www.zhipin.com/job_detail/def456.html",
            },
        ]
        store.append_search_results(results)

        # 读取
        read_back = store.read_search_results()
        assert len(read_back) == 2
        assert read_back[0]["title"] == "AI产品经理"
        assert read_back[0]["company"] == "固生堂"
        assert read_back[1]["title"] == "产品总监"


def test_store_write_detail():
    """Store 能写和读详情文件。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = Store(base_dir=tmpdir)

        job = {
            "id": 1,
            "url": "https://www.zhipin.com/job_detail/abc123.html",
            "company": "固生堂",
            "title": "AI医疗产品经理",
            "salary": "20-35K·13薪",
            "city": "杭州",
            "boss_name": "陈女士",
            "boss_role": "HRBP",
            "status": "scraped",
            "source": "search",
            "jd_text": "岗位职责：\n1. 负责AI产品规划\n2. 推动产品落地",
        }

        # 写入
        filepath = store.write_detail(job)
        assert filepath.exists()
        assert "001-" in filepath.name

        # 读取
        detail = store.read_detail(1)
        assert detail is not None
        assert detail["company"] == "固生堂"
        assert detail["title"] == "AI医疗产品经理"
        assert detail["boss_name"] == "陈女士"
        assert "AI产品规划" in detail["jd_text"]

        # 列出
        details = store.list_details()
        assert len(details) == 1


def test_store_picked():
    """Store 能读写 picked。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = Store(base_dir=tmpdir)

        # 先写入搜索结果
        store.append_search_results([
            {"title": "AI产品经理", "company": "固生堂", "salary": "20-35K", "city": "杭州", "url": "https://example.com/1"},
            {"title": "产品总监", "company": "云帆", "salary": "30-50K", "city": "北京", "url": "https://example.com/2"},
            {"title": "PM", "company": "测试", "salary": "15-25K", "city": "上海", "url": "https://example.com/3"},
        ])

        # 挑选
        store.write_picked([1, 3])
        picked = store.read_picked()
        assert len(picked) == 2
        assert picked[0]["title"] == "AI产品经理"
        assert picked[1]["title"] == "PM"


def test_store_pitches():
    """Store 能读写话术。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = Store(base_dir=tmpdir)

        pitches = [
            {
                "id": 1,
                "company": "固生堂",
                "title": "AI医疗产品经理",
                "boss_name": "陈女士",
                "boss_role": "HRBP",
                "text": "陈女士好，我对AI医疗产品经理岗位非常感兴趣。",
            },
            {
                "id": 2,
                "company": "无际云帆",
                "title": "AI产品经理",
                "boss_name": "李先生",
                "boss_role": "招聘专家",
                "text": "李先生好，我有5年AI产品经验。",
            },
        ]
        store.write_pitches(pitches)

        read_back = store.read_pitches()
        assert len(read_back) == 2
        assert read_back[0]["company"] == "固生堂"
        assert "陈女士" in read_back[0]["text"]

        # 更新话术
        store.update_pitch(0, "陈女士好，更新后的话术。")
        read_back = store.read_pitches()
        assert "更新后" in read_back[0]["text"]


def test_store_status_summary():
    """Store 能统计状态。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = Store(base_dir=tmpdir)

        # 初始状态
        summary = store.get_status_summary()
        assert summary["searched"] == 0
        assert summary["picked"] == 0
        assert summary["scraped"] == 0
        assert summary["pitched"] == 0

        # 写入搜索结果
        store.append_search_results([
            {"title": "AI产品经理", "company": "固生堂", "salary": "20-35K", "city": "杭州", "url": "https://example.com/1"},
        ])
        summary = store.get_status_summary()
        assert summary["searched"] == 1


def test_store_send_log():
    """Store 能读写发送日志。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = Store(base_dir=tmpdir)

        # 初始为空
        logs = store.read_send_log()
        assert logs == []

        # 写入日志
        results = [
            {
                "job_id": 1,
                "company": "固生堂",
                "title": "AI医疗产品经理",
                "boss_name": "陈女士",
                "scene": "A",
                "status": "delivered",
                "error": "",
            },
            {
                "job_id": 2,
                "company": "云帆",
                "title": "产品总监",
                "boss_name": "李先生",
                "scene": "B",
                "status": "failed",
                "error": "侧栏未找到送达标记",
            },
        ]
        store.write_send_log(results)

        # 读取
        logs = store.read_send_log()
        assert len(logs) == 2
        assert logs[0]["job_id"] == 1
        assert logs[0]["company"] == "固生堂"
        assert logs[0]["status"] == "delivered"
        assert logs[1]["status"] == "failed"
        assert "送达标记" in logs[1]["error"]

        # 追加第二批
        store.write_send_log([{
            "job_id": 3,
            "company": "测试",
            "title": "PM",
            "boss_name": "王总",
            "scene": "B",
            "status": "sent",
            "error": "",
        }])

        logs = store.read_send_log()
        assert len(logs) == 3
