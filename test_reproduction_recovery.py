"""Exercise real script execution with bounded, simulated model corrections."""
import json
from pathlib import Path

from reproduction import reproduce


def plan(code):
    return dict(revised_report="value 应为 2，实际为 1", steps="导入并打印 value", reproduction_code=code,
                clarification_required=False, clarification_reasons=[], question="")


def comparison(quote="1", verdict="matched"):
    return dict(comparison=verdict, conclusion="依据实际输出核对报告", evidence=quote,
                clarification_required=verdict != "matched",
                clarification_reasons=[] if verdict == "matched" else ["缺少可核对证据"],
                question="" if verdict == "matched" else "请提供可核对的触发条件")


def run(tmp_path, replies):
    source = tmp_path / "original.py"
    source.write_text("value = 1\n", encoding="utf-8")
    prompts = []
    def model(prompt):
        prompts.append(prompt)
        return json.dumps(replies[len(prompts)-1], ensure_ascii=False)
    record = reproduce({"type": "python_file", "path": str(source)}, "value 应为 2，实际为 1",
                       work_root=tmp_path / "work", model_call=model, execution_backend="host")
    assert source.read_text(encoding="utf-8") == "value = 1\n"
    return record, prompts


def test_summary_evidence_is_corrected_without_rerunning_script(tmp_path):
    record, prompts = run(tmp_path, [plan("from original import value\nprint(value)"),
                                    comparison("实际输出是 1，符合报告"), comparison("1")])
    assert record["status"] == "已复现，与报告一致"
    assert len(prompts) == 3
    assert "服务端校验未通过" in prompts[-1]
    assert len(record["execution_attempts"]) == 1
    assert record["execution"]["stdout"].splitlines() == ["1"]


def test_invented_evidence_never_passes_and_corrections_are_bounded(tmp_path):
    record, prompts = run(tmp_path, [plan("from original import value\nprint(value)"),
                                    comparison("invented"), comparison("still invented")])
    assert record["status"] != "已复现，与报告一致"
    assert len(prompts) == 3
    assert record["error_info"]["code"] == "schema_invalid"


def test_source_root_resolves_source_instead_of_script_directory(tmp_path):
    code = "import runpy\nprint(runpy.run_path(str(SOURCE_ROOT / 'original.py'))['value'])"
    record, prompts = run(tmp_path, [plan(code), comparison()])
    assert record["status"] == "已复现，与报告一致"
    assert record["execution"]["returncode"] == 0
    assert "SOURCE_ROOT" in prompts[0]
    assert record["execution"]["source_root"] != str(Path(record["task_document"]).parent)


def test_script_format_failure_is_corrected_once_with_original_logs_preserved(tmp_path):
    broken = "from original import value\nprint('{!r!s:<20}'.format(value))"
    record, prompts = run(tmp_path, [plan(broken), plan("from original import value\nprint(value)"), comparison()])
    assert record["status"] == "已复现，与报告一致"
    assert len(prompts) == 3
    assert len(record["execution_attempts"]) == 2
    first, second = record["execution_attempts"]
    assert "ValueError" in first["execution"]["stderr"]
    assert Path(first["execution"]["stderr_log"]).is_file()
    assert second["execution"]["stdout"].splitlines() == ["1"]
    assert first["execution"]["cwd"] != second["execution"]["cwd"]


def test_target_failure_is_not_mistaken_for_script_format_failure(tmp_path):
    source = tmp_path / "original.py"
    source.write_text("def f():\n    raise ValueError(\"expected ':' after conversion specifier\")\n", encoding="utf-8")
    replies = [plan("from original import f\nf()"), comparison("ValueError", "inconclusive")]
    prompts = []
    def model(prompt):
        prompts.append(prompt)
        return json.dumps(replies[len(prompts)-1])
    record = reproduce({"type": "python_file", "path": str(source)}, "f 的行为不符合预期",
        work_root=tmp_path / "work", model_call=model, execution_backend="host")
    assert len(prompts) == 2
    assert record["status"] == "需要用户确认"
    assert len(record["execution_attempts"]) == 1


def test_wrong_source_path_retries_from_clean_copy(tmp_path):
    broken = ("from pathlib import Path\nPath('original.py').write_text('value = 99\\n')\n"
              "open(Path(__file__).parent / 'original.py')")
    record, _ = run(tmp_path, [plan(broken), plan("from original import value\nprint(value)"), comparison()])
    assert record["status"] == "已复现，与报告一致"
    assert record["execution"]["stdout"].strip() == "1"
    assert len(record["execution_attempts"]) == 2


def test_repeated_script_error_stops_after_one_retry(tmp_path):
    broken = "from original import value\nprint('{!r!s:<20}'.format(value))"
    record, prompts = run(tmp_path, [plan(broken), plan(broken), comparison("ValueError", "inconclusive")])
    assert record["status"] == "需要用户确认"
    assert len(record["execution_attempts"]) == 2
    assert len(prompts) == 3
