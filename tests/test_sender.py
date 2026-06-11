"""sender.py 单元测试。

用 mock CDPClient 测试发送逻辑的各个分支：
- 场景A（弹窗）
- 场景B（聊天页）
- 侧栏验证通过/失败
- 按钮不存在
- CDP 连接断开
- dry-run 模式
- 批次策略
"""

import json
from unittest.mock import MagicMock, patch, call

import pytest

from boss_pitch.scraper.cdp import CDPError, CDPSessionError
from boss_pitch.sender.sender import (
    send_pitch,
    send_pitches_batch,
    SendResult,
    BatchResult,
    _verify_delivery_sidebar,
)


# ── fixtures ──────────────────────────────────────

@pytest.fixture
def mock_client():
    """创建一个 mock CDPClient。"""
    client = MagicMock()
    client.navigate = MagicMock()
    client.sleep = MagicMock()
    client.evaluate = MagicMock()
    client.click = MagicMock()
    client.get_targets = MagicMock(return_value=[])
    return MagicMock()


@pytest.fixture
def sample_job():
    """一个标准的岗位信息。"""
    return {
        "id": 1,
        "url": "https://www.zhipin.com/job_detail/abc123.html",
        "company": "固生堂",
        "title": "AI医疗产品经理",
        "boss_name": "陈女士",
    }


@pytest.fixture
def sample_pitch():
    """一条标准话术。"""
    return "陈女士好，我对AI医疗产品经理岗位非常感兴趣，有5年AI产品经验。"


# ── send_pitch 单条测试 ────────────────────────────

class TestSendPitch:
    """send_pitch 单条发送测试。"""

    def test_dry_run_returns_immediately(self, mock_client, sample_job, sample_pitch):
        """dry-run 模式不调用任何 CDP 命令，直接返回 dry_run 状态。"""
        result = send_pitch(mock_client, sample_job, sample_pitch, dry_run=True)

        assert result.status == "dry_run"
        assert result.scene == "-"
        assert result.job_id == 1
        mock_client.navigate.assert_not_called()
        mock_client.evaluate.assert_not_called()

    def test_no_startchat_button(self, mock_client, sample_job, sample_pitch):
        """沟通按钮不存在时返回 failed。"""
        mock_client.evaluate.return_value = json.dumps({"exists": False, "text": ""})

        result = send_pitch(mock_client, sample_job, sample_pitch)

        assert result.status == "failed"
        assert "未找到沟通按钮" in result.error

    def test_scene_a_popup_send(self, mock_client, sample_job, sample_pitch):
        """场景A：弹窗发送，侧栏验证通过。"""
        # 调用序列：navigate → close_dialogs → check_btn → click_btn → check_scene_b → check_scene_a → send_a → verify_sidebar
        mock_client.evaluate.side_effect = [
            _js_close_dialogs_result(),           # close dialogs
            json.dumps({"exists": True, "text": "立即沟通"}),  # check btn
            "clicked",                              # click btn
            json.dumps({"hasInput": False, "hasSendBtn": False, "currentName": ""}),  # check scene B → 不是B
            json.dumps({"hasTextarea": True, "hasSendBtn": True}),  # check scene A → 是A
            json.dumps({"scene": "A", "sent": True}),  # send scene A
            json.dumps({"found": True, "hasDelivered": True, "hasRead": False, "snippet": "..."}),  # sidebar verify
        ]

        result = send_pitch(mock_client, sample_job, sample_pitch)

        assert result.status == "delivered"
        assert result.scene == "A"

    def test_scene_b_chat_send(self, mock_client, sample_job, sample_pitch):
        """场景B：聊天页发送，侧栏验证通过。"""
        mock_client.evaluate.side_effect = [
            _js_close_dialogs_result(),
            json.dumps({"exists": True, "text": "继续沟通"}),
            "clicked",
            json.dumps({"hasInput": True, "hasSendBtn": True, "currentName": "陈女士"}),  # scene B
            _js_close_dialogs_result(),           # close dialogs in chat page
            json.dumps({"sent": False, "step": "filled", "preview": "陈女士好..."}),  # fill
            json.dumps({"sent": True}),            # click send
            json.dumps({"found": True, "hasDelivered": True, "hasRead": False}),  # sidebar verify
        ]

        result = send_pitch(mock_client, sample_job, sample_pitch)

        assert result.status == "delivered"
        assert result.scene == "B"

    def test_sidebar_verify_fallback(self, mock_client, sample_job, sample_pitch):
        """侧栏验证第一次失败，跳转聊天页重试成功。"""
        mock_client.evaluate.side_effect = [
            _js_close_dialogs_result(),
            json.dumps({"exists": True, "text": "继续沟通"}),
            "clicked",
            json.dumps({"hasInput": True, "hasSendBtn": True, "currentName": "陈女士"}),
            _js_close_dialogs_result(),
            json.dumps({"sent": False, "step": "filled", "preview": "..."}),
            json.dumps({"sent": True}),
            json.dumps({"found": False}),          # 第一次侧栏验证失败
            "navigated",                           # 跳转聊天页
            json.dumps({"found": True, "hasDelivered": True, "hasRead": False}),  # 重试成功
        ]

        result = send_pitch(mock_client, sample_job, sample_pitch)

        assert result.status == "delivered"

    def test_sidebar_verify_both_fail(self, mock_client, sample_job, sample_pitch):
        """侧栏验证两次都失败，返回 sent 但带警告。"""
        mock_client.evaluate.side_effect = [
            _js_close_dialogs_result(),
            json.dumps({"exists": True, "text": "继续沟通"}),
            "clicked",
            json.dumps({"hasInput": True, "hasSendBtn": True, "currentName": "陈女士"}),
            _js_close_dialogs_result(),
            json.dumps({"sent": False, "step": "filled", "preview": "..."}),
            json.dumps({"sent": True}),
            json.dumps({"found": False}),          # 第一次失败
            "navigated",
            json.dumps({"found": False}),          # 第二次也失败
        ]

        result = send_pitch(mock_client, sample_job, sample_pitch)

        assert result.status == "sent"  # 发了但未确认
        assert "侧栏未找到" in result.error

    def test_unknown_scene(self, mock_client, sample_job, sample_pitch):
        """既不是场景A也不是场景B。"""
        mock_client.evaluate.side_effect = [
            _js_close_dialogs_result(),
            json.dumps({"exists": True, "text": "继续沟通"}),
            "clicked",
            json.dumps({"hasInput": False, "hasSendBtn": False, "currentName": ""}),  # 不是B
            json.dumps({"hasTextarea": False, "hasSendBtn": False}),  # 不是A
        ]

        result = send_pitch(mock_client, sample_job, sample_pitch)

        assert result.status == "failed"
        assert "无法识别发送场景" in result.error

    def test_cdp_error(self, mock_client, sample_job, sample_pitch):
        """CDP 连接错误。"""
        mock_client.evaluate.side_effect = CDPError("连接断开")

        result = send_pitch(mock_client, sample_job, sample_pitch)

        assert result.status == "failed"
        assert "CDP 错误" in result.error

    def test_no_boss_name_verify_skipped(self, mock_client):
        """没有 boss_name 时，侧栏验证直接返回 False。"""
        # 空名字 → 不调用 evaluate，直接返回 False
        ok_empty = _verify_delivery_sidebar(mock_client, "")
        assert ok_empty is False
        mock_client.evaluate.assert_not_called()


# ── send_pitches_batch 批次测试 ─────────────────────

class TestSendPitchesBatch:
    """send_pitches_batch 批量发送测试。"""

    def test_empty_inputs(self, mock_client):
        """空输入返回空结果。"""
        result = send_pitches_batch(mock_client, [], [])
        assert result.total == 0

    def test_consecutive_failures_stop(self, mock_client):
        """连续 2 条失败自动停止。"""
        mock_client.evaluate.return_value = json.dumps({"exists": False, "text": ""})

        jobs = [
            {"id": i, "url": f"https://example.com/{i}", "company": f"C{i}", "title": f"T{i}", "boss_name": f"B{i}"}
            for i in range(1, 6)
        ]
        pitches = [{"id": i, "text": f"话术{i}"} for i in range(1, 6)]

        result = send_pitches_batch(mock_client, jobs, pitches)

        # 连续 2 条失败后停止，不应该发第 3 条
        assert result.total == 2
        assert result.failed_count == 2

    @patch("boss_pitch.sender.sender.time.sleep")
    def test_batch_interval(self, mock_sleep, mock_client):
        """超过 batch_size 后等待 batch_interval。"""
        mock_client.evaluate.return_value = json.dumps({"exists": False, "text": ""})

        jobs = [
            {"id": i, "url": f"https://example.com/{i}", "company": f"C{i}", "title": f"T{i}", "boss_name": f"B{i}"}
            for i in range(1, 6)
        ]
        pitches = [{"id": i, "text": f"话术{i}"} for i in range(1, 6)]

        # batch_size=1, 只会发2条就停（连续失败）
        result = send_pitches_batch(mock_client, jobs, pitches, batch_size=1, batch_interval=10)
        assert result.total == 2

    def test_skip_jobs_without_pitch(self, mock_client):
        """没有对应话术的岗位会被跳过。"""
        mock_client.evaluate.side_effect = [
            json.dumps({"exists": True, "text": "立即沟通"}),
            "clicked",
            json.dumps({"hasInput": False, "hasSendBtn": False, "currentName": ""}),
            json.dumps({"hasTextarea": True, "hasSendBtn": True}),
            json.dumps({"scene": "A", "sent": True}),
            json.dumps({"found": True, "hasDelivered": True, "hasRead": False}),
        ]

        jobs = [
            {"id": 1, "url": "https://example.com/1", "company": "C1", "title": "T1", "boss_name": "B1"},
            {"id": 2, "url": "https://example.com/2", "company": "C2", "title": "T2", "boss_name": "B2"},
        ]
        pitches = [{"id": 1, "text": "话术1"}]  # 只有 id=1 的话术

        result = send_pitches_batch(mock_client, jobs, pitches)
        assert result.total == 1


# ── verify 测试 ────────────────────────────────────

class TestVerifyDelivery:
    """_verify_delivery_sidebar 测试。"""

    def test_delivered(self, mock_client):
        """送达标记存在。"""
        mock_client.evaluate.return_value = json.dumps({
            "found": True,
            "hasDelivered": True,
            "hasRead": False,
        })
        assert _verify_delivery_sidebar(mock_client, "陈女士") is True

    def test_read(self, mock_client):
        """已读标记存在。"""
        mock_client.evaluate.return_value = json.dumps({
            "found": True,
            "hasDelivered": False,
            "hasRead": True,
        })
        assert _verify_delivery_sidebar(mock_client, "陈女士") is True

    def test_not_found(self, mock_client):
        """侧栏找不到该招聘人。"""
        mock_client.evaluate.return_value = json.dumps({"found": False})
        assert _verify_delivery_sidebar(mock_client, "不存在的人") is False

    def test_found_but_no_status(self, mock_client):
        """找到了但没有送达/已读标记（被 BOSS 丢弃）。"""
        mock_client.evaluate.return_value = json.dumps({
            "found": True,
            "hasDelivered": False,
            "hasRead": False,
        })
        assert _verify_delivery_sidebar(mock_client, "陈女士") is False

    def test_empty_name(self, mock_client):
        """名字为空直接返回 False。"""
        assert _verify_delivery_sidebar(mock_client, "") is False


# ── SendResult / BatchResult 数据结构测试 ─────────────

class TestDataStructures:
    """数据结构测试。"""

    def test_batch_result_counts(self):
        """BatchResult 统计正确。"""
        batch = BatchResult(results=[
            SendResult(job_id=1, company="A", title="T", boss_name="B", status="delivered"),
            SendResult(job_id=2, company="A", title="T", boss_name="B", status="sent"),
            SendResult(job_id=3, company="A", title="T", boss_name="B", status="failed"),
            SendResult(job_id=4, company="A", title="T", boss_name="B", status="dry_run"),
        ])
        assert batch.total == 4
        assert batch.sent_count == 3  # delivered + sent + dry_run
        assert batch.failed_count == 1


# ── helper ────────────────────────────────────────

def _js_close_dialogs_result() -> str:
    """模拟关闭对话框的返回值。"""
    return "0"
