"""verify.py 单元测试。

用 mock CDPClient 测试验证逻辑：
- 送达/已读检测
- 名字+公司双重定位
- 未找到
- JSON 解析异常
"""

import json
from unittest.mock import MagicMock

import pytest

from boss_pitch.scraper.cdp import CDPError
from boss_pitch.sender.verify import (
    verify_single,
    verify_batch,
    VerifyResult,
    VerifyBatch,
)


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.evaluate = MagicMock()
    client.sleep = MagicMock()
    return client


class TestVerifySingle:
    """verify_single 单条验证测试。"""

    def test_delivered(self, mock_client):
        """检测到送达标记。"""
        mock_client.evaluate.return_value = json.dumps({
            "found": True,
            "status": "delivered",
            "hasDelivered": True,
            "hasRead": False,
            "isSending": False,
        })

        result = verify_single(mock_client, job_id=1, boss_name="陈女士", company="固生堂")
        assert result.status == "delivered"

    def test_read(self, mock_client):
        """检测到已读标记。"""
        mock_client.evaluate.return_value = json.dumps({
            "found": True,
            "status": "read",
            "hasDelivered": False,
            "hasRead": True,
            "isSending": False,
        })

        result = verify_single(mock_client, job_id=1, boss_name="陈女士", company="固生堂")
        assert result.status == "read"

    def test_sending(self, mock_client):
        """检测到发送中状态。"""
        mock_client.evaluate.return_value = json.dumps({
            "found": True,
            "status": "sending",
            "hasDelivered": False,
            "hasRead": False,
            "isSending": True,
        })

        result = verify_single(mock_client, job_id=1, boss_name="陈女士")
        assert result.status == "sending"

    def test_not_found(self, mock_client):
        """侧栏找不到该人。"""
        mock_client.evaluate.return_value = json.dumps({"found": False, "name": "不存在"})

        result = verify_single(mock_client, job_id=1, boss_name="不存在")
        assert result.status == "not_found"

    def test_empty_boss_name(self, mock_client):
        """名字为空直接返回 not_found。"""
        result = verify_single(mock_client, job_id=1, boss_name="", company="X")
        assert result.status == "not_found"
        assert "无招聘人姓名" in result.detail
        mock_client.evaluate.assert_not_called()

    def test_cdp_error(self, mock_client):
        """CDP 错误。"""
        mock_client.evaluate.side_effect = CDPError("断连")

        result = verify_single(mock_client, job_id=1, boss_name="陈女士")
        assert result.status == "unknown"
        assert "CDP 错误" in result.detail

    def test_json_parse_error(self, mock_client):
        """JS 返回无法解析的内容。"""
        mock_client.evaluate.return_value = "not valid json {{{"

        result = verify_single(mock_client, job_id=1, boss_name="陈女士")
        assert result.status == "unknown"


class TestVerifyBatch:
    """verify_batch 批量验证测试。"""

    def test_batch_with_results(self, mock_client):
        """批量验证能正确汇总。"""
        mock_client.evaluate.side_effect = [
            "navigated",  # navigate to chat
            json.dumps({"found": True, "status": "delivered", "hasDelivered": True, "hasRead": False, "isSending": False}),
            json.dumps({"found": True, "status": "read", "hasDelivered": False, "hasRead": True, "isSending": False}),
            json.dumps({"found": False}),
        ]

        logs = [
            {"job_id": 1, "boss_name": "陈女士", "company": "固生堂"},
            {"job_id": 2, "boss_name": "李先生", "company": "云帆"},
            {"job_id": 3, "boss_name": "王总", "company": "测试"},
        ]

        result = verify_batch(mock_client, logs)

        assert result.total == 3
        assert result.delivered == 1
        assert result.read == 1
        assert result.failed == 1

    def test_batch_navigation_failure(self, mock_client):
        """导航失败返回空结果。"""
        mock_client.evaluate.side_effect = CDPError("无法连接")

        logs = [{"job_id": 1, "boss_name": "陈女士", "company": "X"}]
        result = verify_batch(mock_client, logs)

        assert result.total == 0

    def test_empty_logs(self, mock_client):
        """空日志列表。"""
        result = verify_batch(mock_client, [])
        assert result.total == 0


class TestVerifyDataStructures:
    """VerifyBatch 统计测试。"""

    def test_counts(self):
        batch = VerifyBatch(results=[
            VerifyResult(job_id=1, company="A", boss_name="B", status="delivered"),
            VerifyResult(job_id=2, company="A", boss_name="B", status="read"),
            VerifyResult(job_id=3, company="A", boss_name="B", status="not_found"),
            VerifyResult(job_id=4, company="A", boss_name="B", status="unknown"),
        ])
        assert batch.total == 4
        assert batch.delivered == 1
        assert batch.read == 1
        assert batch.failed == 2  # not_found + unknown
