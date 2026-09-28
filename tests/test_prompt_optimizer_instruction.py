import importlib.util
import json
from pathlib import Path

import pytest


PLUGIN_ENTRY = (
    Path(__file__).parents[1]
    / "modules/orchestration/chub-task-prompt-optimizer/entry.py"
)


def _plugin_entry():
    spec = importlib.util.spec_from_file_location("prompt_optimizer_entry", PLUGIN_ENTRY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prompt_uses_only_current_input_and_always_returns_chinese():
    builder = _plugin_entry().build_optimization_prompt
    for task in ("Please summarize this in English.", "请总结这段内容。"):
        prompt = builder(task)
        assert "只优化当前请求附带的这一条任务输入" in prompt
        assert "忽略本 Session 的历史消息、此前任务和其他内容" in prompt
        assert "无论原任务输入是中文、英文还是混合语言" in prompt
        assert "生成语义一致的简体中文和英文两个优化版本" in prompt
        assert "中文版本用于提交主任务，英文版本仅供维护者查看" in prompt
        assert "主任务面向用户的执行回复和完成说明必须使用简体中文" in prompt
        assert '"optimized_prompt_zh"' in prompt
        assert '"optimized_prompt_en"' in prompt
        assert task in prompt


def test_result_reader_accepts_a_bilingual_pair_and_rejects_invalid_pairs():
    reader = _plugin_entry().read_optimization_result
    result = reader(
        '{"optimized_prompt_zh":"请总结这段内容。",'
        '"optimized_prompt_en":"Summarize this content."}'
    )
    assert result.chinese == "请总结这段内容。"
    assert result.english == "Summarize this content."
    for invalid in (
        '{"optimized_prompt_zh":"请总结。"}',
        '{"optimized_prompt_zh":"", "optimized_prompt_en":"Summarize this."}',
        '{"optimized_prompt_zh":"Summarize this.", "optimized_prompt_en":"Summarize this."}',
        '{"optimized_prompt_zh":"请总结。", "optimized_prompt_en":""}',
        '{"optimized_prompt_zh":"请总结。", "optimized_prompt_en":"仅中文。"}',
        '{"optimized_prompt_zh":"请总结。", "optimized_prompt_en":"Summarize this.", "extra":true}',
    ):
        with pytest.raises(ValueError):
            reader(invalid)


def test_result_reader_rejects_oversized_version():
    reader = _plugin_entry().read_optimization_result
    too_long = "x" * 8001
    with pytest.raises(ValueError):
        reader(
            '{"optimized_prompt_zh":"请总结。",'
            f'"optimized_prompt_en":{json.dumps(too_long)}}}'
        )


def test_result_reader_accepts_escaped_unicode_near_worker_result_limit():
    reader = _plugin_entry().read_optimization_result
    chinese = "汉" * 8_000
    english = ("A résumé café is ready. " * 400)[:8_000]
    encoded = json.dumps(
        {"optimized_prompt_zh": chinese, "optimized_prompt_en": english},
        ensure_ascii=True,
    )
    assert 60_000 < len(encoded) <= 100_000
    result = reader(encoded)
    assert result.chinese == chinese
    assert result.english == english

    oversized = encoded + (" " * (100_001 - len(encoded)))
    with pytest.raises(ValueError):
        reader(oversized)
