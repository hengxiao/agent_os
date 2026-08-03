"""N1 摘要泄漏清零(O1,B1;docs/FLOWS-OPTIMIZATION.md 循环 3)。

行级 human_error 全覆盖:browse/why_failed 的行摘要在**后端**就译成人话,
英文错误类名(ProviderError 等)零泄漏;错误原文只留详情 tab(产物层
/api/runs/{id} 不动——本测试同时锚住"数据源原文未改")。
"""

from __future__ import annotations

from agent_os.host.web_platform.orchestrator import Orchestrator, human_error

#: 摘要层禁忌(F03 三轮回归):英文错误类名永不出现在行摘要
_FORBIDDEN = ("ProviderError", "TimeoutError", "AuthError", "SkillLoadError")


def test_human_error_mapping():
    """已知模式 → 人话;未知 → 剥 XxxError 前缀留消息体(不编造)。"""
    assert human_error("ProviderError: quota exceeded") == "模型服务不可用"
    assert human_error("model unavailable: 503") == "模型服务不可用"
    assert human_error("TimeoutError: read timed out") == "请求超时"
    assert human_error("AuthError: 401 unauthorized") == "API Key 无效或过期"
    assert human_error("api key expired") == "API Key 无效或过期"
    assert human_error("WeirdError: 磁盘已满") == "磁盘已满", "未知错误剥前缀留人话体"
    assert human_error("outputs 校验失败: 缺 answer") == "outputs 校验失败: 缺 answer", "中文原文不动"
    assert human_error("") == ""


def test_why_failed_row_is_human():
    """F03:why_failed 行摘要人话(三轮回归点);数据源原文不动(详情层留全量)。"""
    runs = [
        {"run_id": "a1b2c3d4", "skill": "demo.fib", "status": "failed",
         "error": "ProviderError: 401 token expired", "ts": 2},
    ]
    orch = Orchestrator(runs_provider=lambda: runs)
    card = orch.handle({"messages": []}, "这个 run 为什么挂")["cards"][0]
    summary = card["data"]["rows"][0][2]
    for w in _FORBIDDEN:
        assert w not in summary, f"行摘要泄漏 {w}"
    assert summary == "API Key 无效或过期"
    # 详情锚仍在(原文由详情 tab 从产物层拉取,不在摘要层)
    assert card["data"]["ref"] == {"kind": "run", "id": "a1b2c3d4"}
    assert runs[0]["error"] == "ProviderError: 401 token expired", "数据源原文未改"


def test_browse_rows_all_human():
    """browse 每一行(失败/成功/未知错误)摘要层零英文类名。"""
    runs = [
        {"run_id": "r1", "skill": "a", "status": "failed", "error": "ProviderError: down", "ts": 1},
        {"run_id": "r2", "skill": "b", "status": "done", "error": "", "ts": 2},
        {"run_id": "r3", "skill": "c", "status": "failed", "error": "TimeoutError: slow", "ts": 3},
        {"run_id": "r4", "skill": "d", "status": "failed", "error": "WeirdError: 磁盘满", "ts": 4},
    ]
    orch = Orchestrator(runs_provider=lambda: runs)
    card = orch.handle({"messages": []}, "最近有哪些 run")["cards"][0]
    summaries = [r[2] for r in card["data"]["rows"]]
    for s in summaries:
        for w in _FORBIDDEN:
            assert w not in s, f"行摘要泄漏 {w}: {s}"
    assert summaries == ["模型服务不可用", "运行成功", "请求超时", "磁盘满"]
