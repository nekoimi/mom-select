from mom_select.core.time import format_report_time


def test_format_report_time_converts_to_beijing_time() -> None:
    assert (
        format_report_time("2026-08-14T07:20:30Z")
        == "2026年08月14日 15:20:30（北京时间）"
    )
