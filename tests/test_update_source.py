"""update_source.py 的离线单元测试，不访问网络。"""

import json
import sys
import threading
import time
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import update_source as us

YUANSHEN_LOCATION = (
    "https://autopatchcn.yuanshen.com/client_app/download/Android/"
    "20260803155301_dVPuDB44YQg4Wkwy/mihoyo/yuanshen_7.0.0.apk"
)


class FakeResponse:
    def __init__(self, *, status_code=200, headers=None, payload=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(
                f"HTTP {self.status_code}", response=self
            )

    def json(self):
        if self._payload is None:
            raise ValueError("响应不是 JSON")
        return self._payload


class FakeSession:
    """替换共享 Session；记录收到的关键字参数，便于断言 verify/allow_redirects。"""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture
def fake_session(monkeypatch):
    def install(response):
        session = FakeSession(response)
        monkeypatch.setattr(us, "SESSION", session)
        return session

    return install


def redirect_source(name):
    return next(spec for spec in us.REDIRECT_SOURCES if spec.name == name)


def patch_location(monkeypatch, location):
    monkeypatch.setattr(
        us,
        "get_with_retry",
        lambda *args, **kwargs: FakeResponse(headers={"Location": location}),
    )


def sample_result(name="米游社", version="2.115.0", url="https://example.com/a.apk"):
    return us.SourceResult(
        name=name,
        ok=True,
        version=version,
        url=url,
        reference="mihoyobbs",
        fetched_at="2026-09-12T06:30:00+08:00",
    )


def write_state(path, entries, updated_at="2026-09-10T06:30:00+08:00"):
    path.write_text(
        json.dumps(
            {"_version": 1, "updated_at": updated_at, "entries": entries},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def run_args(tmp_path, *extra):
    return [
        "--write",
        "--output",
        str(tmp_path / "index.html"),
        "--state",
        str(tmp_path / "state.json"),
        "--status",
        str(tmp_path / "status.json"),
        "--no-cleanup",
        *extra,
    ]


# --- 解析与渲染 ---------------------------------------------------------


def test_redirect_source_parses_version_and_reference(monkeypatch):
    patch_location(monkeypatch, YUANSHEN_LOCATION)

    result = us.fetch_redirect_source(redirect_source("原神"))

    assert result.ok
    assert result.version == "7.0.0"
    assert result.url == YUANSHEN_LOCATION
    assert result.reference == "yuanshen_7.0.0.apk"
    assert result.fetched_at
    assert us.render_line(result) == (
        f'    <p data-source="原神" data-fetched-at="{result.fetched_at}">原神: '
        f'<a href="{YUANSHEN_LOCATION}">v7.0.0</a> '
        "(文件名参考: yuanshen_7.0.0.apk)</p>"
    )


def test_pvz2_uses_its_own_version_pattern(monkeypatch):
    patch_location(
        monkeypatch,
        "https://pvz2apk-cdn.hrgame.com.cn/423/baokai_4.2.3_1856_664_dj2.0-4.0.0.apk",
    )

    result = us.fetch_redirect_source(redirect_source("植物大战僵尸2"))

    assert result.ok
    assert result.version == "4.2.3"


def test_taptap_rewrites_version_and_download_url(monkeypatch):
    patch_location(
        monkeypatch,
        "https://d.taptap.cn/latest/taptap_2.96.10-rel.100000.apk",
    )

    result = us.fetch_redirect_source(redirect_source("TapTap"))

    assert result.ok
    assert result.version == "2.96.10-rel#100000"
    assert result.url == "https://d.taptap.cn/latest/seo-bing#taptap_fake.apk"
    assert result.reference == "taptap_2.96.10-rel.100000.apk"


def test_unparsable_version_is_reported_as_failure(monkeypatch):
    patch_location(monkeypatch, "https://example.com/download/unknown-build.zip")

    result = us.fetch_redirect_source(redirect_source("原神"))

    assert not result.ok
    assert "版本号解析失败" in result.message


def test_malformed_version_is_rejected_instead_of_rendered(monkeypatch):
    """P2-2：正则能匹配但格式不符预期时，必须判失败而不是渲染残缺值。"""
    patch_location(monkeypatch, "https://example.com/download/yuanshen_7.0.apk")

    result = us.fetch_redirect_source(redirect_source("原神"))

    assert not result.ok
    assert "版本号格式不符预期" in result.message


def test_version_validate_field_overrides_the_default_pattern(monkeypatch):
    spec = us.RedirectSource(
        "测试来源",
        "https://example.com/api",
        version_pattern=r"_([\d\.]+)\.apk",
        version_validate=r"\d+\.\d+\.\d+\.\d+",
    )
    patch_location(monkeypatch, "https://example.com/download/app_1.2.3.apk")

    result = us.fetch_redirect_source(spec)

    assert not result.ok
    assert "版本号格式不符预期" in result.message


def test_valid_version_requires_a_full_match():
    assert us.valid_version("7.0.0", r"\d+\.\d+\.\d+")
    assert us.valid_version("3.2", r"\d+(\.\d+){0,2}")
    assert not us.valid_version("7.0", r"\d+\.\d+\.\d+")
    assert not us.valid_version("7.0.0-beta", r"\d+\.\d+\.\d+")
    assert not us.valid_version("", r"\d+")


def test_missing_location_is_reported_as_failure(monkeypatch):
    def raise_missing(*args, **kwargs):
        raise us.MissingLocationError("响应缺少 Location 跳转链接")

    monkeypatch.setattr(us, "get_with_retry", raise_missing)

    result = us.fetch_redirect_source(redirect_source("云·原神"))

    assert not result.ok
    assert "抓取报错" in result.message


def test_kuaibao_builds_four_part_version(monkeypatch):
    filename = "HYKB15820420260819wap1.apk"
    patch_location(monkeypatch, f"https://d.3839app.net/video/hykb/{filename}")

    result = us.fetch_kuaibao()

    assert result.ok
    assert result.version == "1.5.8.204"
    assert result.reference == filename


def test_miyoushe_builds_download_url_from_api_version(monkeypatch):
    monkeypatch.setattr(
        us,
        "get_with_retry",
        lambda *args, **kwargs: FakeResponse(payload={"data": {"version": "2.115.0"}}),
    )

    result = us.fetch_miyoushe()

    assert result.ok
    assert result.url == (
        "https://download-bbs.miyoushe.com/app/mihoyobbs_2.115.0_miyousheluodi.apk"
    )
    assert result.reference == "mihoyobbs"


def test_miyoushe_empty_payload_is_failure(monkeypatch):
    monkeypatch.setattr(
        us,
        "get_with_retry",
        lambda *args, **kwargs: FakeResponse(payload={"data": None}),
    )

    result = us.fetch_miyoushe()

    assert not result.ok
    assert "响应异常" in result.message


def test_chelper_prefers_changelog_version(monkeypatch):
    monkeypatch.setattr(
        us,
        "fetch_page",
        lambda url, **kwargs: ("未发布内容\n\n26.1.0\n\n- 修复若干问题\n", ""),
    )

    result = us.fetch_chelper()

    assert result.ok
    assert result.version == "26.1.0"
    assert result.reference_label == "识别标识"
    assert us.render_line(result).endswith("(识别标识: chelper)</p>")


def test_chelper_falls_back_to_release_notes(monkeypatch):
    pages = {
        us.CHELPER_CHANGELOG_URL: ("暂无版本号", ""),
        us.CHELPER_RELEASE_NOTES_URL: (
            "<html><head></head><body><h2>v1.2.3</h2></body></html>",
            "",
        ),
    }
    monkeypatch.setattr(us, "fetch_page", lambda url, **kwargs: pages[url])

    result = us.fetch_chelper()

    assert result.ok
    assert result.version == "1.2.3"


def test_chelper_without_any_version_is_failure(monkeypatch):
    monkeypatch.setattr(us, "fetch_page", lambda url, **kwargs: ("no version here", ""))

    result = us.fetch_chelper()

    assert not result.ok
    assert "版本号解析失败" in result.message


def test_chelper_ssl_fallback_disables_verify_only_for_that_call(monkeypatch):
    """P1-8：证书失败时的降级必须显式、可观察。"""

    class PageResponse:
        status_code = 200
        headers = {}
        text = "body"
        encoding = None

    calls = []

    def fake_get(name, url, **kwargs):
        calls.append(kwargs.get("verify"))
        if len(calls) == 1:
            raise requests.exceptions.SSLError("certificate verify failed")
        return PageResponse()

    monkeypatch.setattr(us, "get_with_retry", fake_get)
    monkeypatch.setattr(us.urllib3, "disable_warnings", lambda **kwargs: None)

    text, warning = us.fetch_page("https://example.com/x", name="测试", verify=True)

    assert calls == [True, False]
    assert text == "body"
    assert "关闭校验" in warning


def test_fetch_page_does_not_downgrade_when_verify_already_disabled(monkeypatch):
    def fake_get(name, url, **kwargs):
        raise requests.exceptions.SSLError("certificate verify failed")

    monkeypatch.setattr(us, "get_with_retry", fake_get)

    with pytest.raises(requests.exceptions.SSLError):
        us.fetch_page("https://example.com/x", name="测试", verify=False)


def test_render_page_keeps_obtainium_compatible_layout():
    result = us.SourceResult(
        name="米游社",
        ok=True,
        version="2.115.0",
        url="https://example.com/mihoyobbs.apk",
        reference="mihoyobbs",
        fetched_at="2026-09-12T06:30:00+08:00",
    )

    assert us.render_page([result]) == (
        "<!DOCTYPE html>\n"
        "<html>\n"
        '<head><meta charset="utf-8"></head>\n'
        "<body>\n"
        "    <h2>我的专属下载源</h2>\n"
        '    <p data-source="米游社" data-fetched-at="2026-09-12T06:30:00+08:00">'
        '米游社: <a href="https://example.com/mihoyobbs.apk">v2.115.0</a> '
        "(文件名参考: mihoyobbs)</p>\n"
        "\n"
        "</body>\n"
        "</html>\n"
    )


def test_render_page_without_fallbacks_has_no_extra_header():
    page = us.render_page([sample_result()])

    assert "同步时间" not in page
    assert page.count("<p") == 1


def test_render_page_marks_and_summarizes_fallbacks():
    stale = us.SourceResult(
        name="原神",
        ok=True,
        version="7.0.0",
        url="https://example.com/yuanshen_7.0.0.apk",
        reference="yuanshen_7.0.0.apk",
        fetched_at="2026-09-10T06:30:00+08:00",
        stale=True,
    )

    page = us.render_page([sample_result(), stale], "2026-09-12T06:31:00+08:00")

    assert "同步时间：2026-09-12T06:31:00+08:00" in page
    assert "1 个来源沿用上次成功值：原神" in page
    assert "[沿用 2026-09-10 06:30]" in page
    assert 'data-source="原神" data-fetched-at="2026-09-10T06:30:00+08:00"' in page
    # 新增的只是属性与状态字，链接与括号内容保持原样
    assert '原神: <a href="https://example.com/yuanshen_7.0.0.apk">v7.0.0</a>' in page
    assert "(文件名参考: yuanshen_7.0.0.apk)" in page


def test_stale_marker_without_timestamp_says_so():
    """旧页面兜底时拿不到抓取时间，不能渲染出空白说明。"""
    entry = us.SourceResult(
        name="原神",
        ok=True,
        version="7.0.0",
        url="https://example.com/yuanshen_7.0.0.apk",
        reference="yuanshen_7.0.0.apk",
        stale=True,
    )

    assert "[沿用 上次成功值]" in us.render_line(entry)
    assert us.describe_fetched_at("") == "上次成功值"
    assert us.describe_fetched_at("2026-09-10T06:30:00+08:00") == "2026-09-10 06:30"


def test_render_page_escapes_untrusted_values():
    results = [
        us.SourceResult(
            name="原神",
            ok=True,
            version="1.0",
            url='https://example.com/a.apk?a=1&b="2"',
            reference="a.apk",
            reference_label="文件名<参考>",
            fetched_at='2026"09',
        )
    ]

    page = us.render_page(results)

    assert "&amp;" in page
    assert "&quot;" in page
    assert 'a=1&amp;b=&quot;2&quot;' in page
    assert '"2"' not in page
    assert "文件名&lt;参考&gt;" in page
    assert page.count("<p") == 1


# --- 网络层重试 ---------------------------------------------------------


def test_get_retry_delay_tolerates_response_without_headers():
    """P0-1：无响应的 HTTPError 不能再抛 AttributeError。"""
    delay = us.get_retry_delay(0, requests.exceptions.HTTPError("no response"))

    assert 1.5 <= delay <= 2.5


def test_get_retry_delay_honours_retry_after_header():
    response = FakeResponse(headers={"Retry-After": "5"})

    assert us.get_retry_delay(0, response) == 5.0


def test_get_retry_delay_ignores_http_date_retry_after():
    response = FakeResponse(headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})

    delay = us.get_retry_delay(0, response)

    assert 1.5 <= delay <= 2.5


def test_session_retry_policy_covers_retryable_status_codes():
    """P1-6：连接池共享、可复用，重试策略本身也能被直接断言。"""
    retry = us.SESSION.get_adapter("https://example.com").max_retries

    # total=1 表示连接池自己不做自动重试，重试交给 get_with_retry 的循环
    assert retry.total == 1
    assert set(us.RETRYABLE_STATUS_CODES).issubset(set(retry.status_forcelist))
    assert retry.allowed_methods == frozenset({"GET"})
    assert retry.raise_on_status is False
    assert retry.respect_retry_after_header is True


def test_shared_session_pool_matches_job_count():
    adapter = us.SESSION.get_adapter("https://example.com")

    assert adapter._pool_maxsize == us.POOL_SIZE >= us.DEFAULT_JOBS


def test_get_with_retry_requires_location_without_retrying(fake_session):
    session = fake_session(FakeResponse(status_code=302, headers={}))

    with pytest.raises(us.MissingLocationError):
        us.get_with_retry("测试来源", "https://example.com", require_location=True)

    assert len(session.calls) == 1


def test_get_with_retry_passes_verification_flag(fake_session):
    session = fake_session(FakeResponse(headers={"Location": "https://example.com/a.apk"}))

    us.get_with_retry(
        "测试来源",
        "https://example.com",
        allow_redirects=False,
        verify=False,
        require_location=True,
    )

    _, kwargs = session.calls[0]
    assert kwargs["verify"] is False
    assert kwargs["allow_redirects"] is False


def test_get_with_retry_raises_for_client_error(fake_session):
    fake_session(FakeResponse(status_code=404))

    with pytest.raises(requests.exceptions.HTTPError):
        us.get_with_retry("测试来源", "https://example.com")


# --- 并发与预算 ---------------------------------------------------------


def test_fetch_all_keeps_declared_order(monkeypatch):
    def make_fetcher(name):
        return lambda: us.SourceResult(name=name, ok=True, message=name)

    monkeypatch.setattr(
        us,
        "build_sources",
        lambda: [(name, make_fetcher(name)) for name in ["甲", "乙", "丙", "丁"]],
    )

    results = us.fetch_all(jobs=4)

    assert [result.name for result in results] == ["甲", "乙", "丙", "丁"]


def test_fetch_all_turns_crash_into_failure(monkeypatch):
    def boom():
        raise RuntimeError("explode")

    monkeypatch.setattr(us, "build_sources", lambda: [("坏来源", boom)])

    results = us.fetch_all(jobs=1)

    assert len(results) == 1
    assert not results[0].ok
    assert "explode" in results[0].message


def test_fetch_all_marks_slow_source_as_failed_but_keeps_others(monkeypatch):
    """P1-7：卡住的来源不会拖住其它来源，且被记为失败。

    注意：Python 无法强杀卡在 socket 读取里的线程，等它的是解释器对工作
    线程的 join（上限就是该请求自己的超时）。所以这里断言的是「记账正确
    且预算生效」，而不是挂钟时间。
    """
    release = threading.Event()

    def slow():
        release.wait(5)
        return us.SourceResult(name="慢来源", ok=True, message="慢来源")

    def quick():
        return us.SourceResult(name="快来源", ok=True, message="快来源")

    monkeypatch.setattr(us, "build_sources", lambda: [("慢来源", slow), ("快来源", quick)])

    try:
        results = us.fetch_all(jobs=2, deadline=0.2)
    finally:
        release.set()

    assert [r.name for r in results] == ["慢来源", "快来源"]
    assert not results[0].ok
    assert "预算" in results[0].message
    assert results[1].ok


def test_get_with_retry_stops_when_budget_is_exhausted(fake_session):
    """预算已在网络调用之间被检查：耗尽后立刻收手，不再重试。"""
    session = fake_session(FakeResponse(status_code=503))
    us.set_active_deadline(time.monotonic() - 1)
    try:
        with pytest.raises(us.SourceTimeoutError):
            us.get_with_retry("测试来源", "https://example.com")
    finally:
        us.set_active_deadline(None)

    assert session.calls == []


def test_get_with_retry_does_not_retry_client_errors(fake_session):
    """4xx 是契约错误，重试没有意义。"""
    session = fake_session(FakeResponse(status_code=404))

    with pytest.raises(requests.exceptions.HTTPError):
        us.get_with_retry("测试来源", "https://example.com", max_retries=3, session=session)

    assert len(session.calls) == 1


def test_get_with_retry_retries_retryable_status(fake_session):
    """5xx 会重试，且第二次不再退避等待。"""
    session = fake_session(FakeResponse(status_code=503))
    sleeps = []
    original_sleep = us.time.sleep
    us.time.sleep = sleeps.append
    try:
        with pytest.raises(requests.exceptions.HTTPError):
            us.get_with_retry("测试来源", "https://example.com", max_retries=2, session=session)
    finally:
        us.time.sleep = original_sleep

    assert len(session.calls) == 2
    assert len(sleeps) == 1


def test_is_retryable_error_classification():
    retryable = requests.exceptions.HTTPError(
        "503", response=FakeResponse(status_code=503)
    )
    client_error = requests.exceptions.HTTPError(
        "404", response=FakeResponse(status_code=404)
    )

    assert us.is_retryable_error(retryable)
    assert not us.is_retryable_error(client_error)
    assert us.is_retryable_error(requests.exceptions.ConnectionError("boom"))
    assert not us.is_retryable_error(requests.exceptions.SSLError("cert"))


def test_build_sources_covers_every_app():
    names = [name for name, _ in us.build_sources()]

    assert names == [
        "米游社",
        "原神",
        "云·原神",
        "云·星穹铁道",
        "云·绝区零",
        "植物大战僵尸2",
        "TapTap",
        "好游快爆",
        "CHelper",
    ]


# --- 状态文件与降级 -----------------------------------------------------


def test_state_roundtrip(tmp_path):
    state_path = tmp_path / "state.json"
    us.write_page(state_path, json.dumps(us.fallback_state({}, [sample_result()]), ensure_ascii=False))

    state = us.load_state(state_path)

    assert state["updated_at"]
    assert state["entries"]["米游社"]["version"] == "2.115.0"
    assert state["entries"]["米游社"]["url"] == "https://example.com/a.apk"


def test_corrupt_state_is_ignored(tmp_path, capsys):
    state_path = tmp_path / "state.json"
    state_path.write_text("{ this is not json", encoding="utf-8")

    assert us.load_state(state_path)["entries"] == {}
    assert "警告" in capsys.readouterr().out


def test_load_state_drops_incomplete_entries(tmp_path):
    state_path = tmp_path / "state.json"
    write_state(
        state_path,
        {
            "有链接无版本": {"url": "https://example.com/a.apk"},
            "有版本无链接": {"version": "1.0.0"},
            "完好": {"url": "https://example.com/b.apk", "version": "1.0.0"},
        },
    )

    entries = us.load_state(state_path)["entries"]

    assert set(entries) == {"完好"}
    assert entries["完好"]["reference_label"] == "文件名参考"


def test_merge_with_last_good_keeps_fresh_and_marks_stale():
    results = [
        sample_result("米游社", version="2.116.0"),
        us.SourceResult(name="原神", ok=False, message="原神 抓取报错"),
        us.SourceResult(name="新应用", ok=False, message="新应用 抓取报错"),
    ]
    state = {
        "entries": {
            "原神": {
                "version": "7.0.0",
                "url": "https://example.com/yuanshen_7.0.0.apk",
                "reference": "yuanshen_7.0.0.apk",
                "reference_label": "文件名参考",
                "fetched_at": "2026-09-10T06:30:00+08:00",
            }
        }
    }

    merged, stale = us.merge_with_last_good(results, state)

    assert [r.name for r in merged] == ["米游社", "原神"]
    assert merged[0].version == "2.116.0"
    assert not merged[0].stale
    assert merged[1].stale
    assert merged[1].version == "7.0.0"
    assert merged[1].fetched_at == "2026-09-10T06:30:00+08:00"
    assert stale == ["原神"]


def test_merge_without_history_drops_failed_sources():
    results = [us.SourceResult(name="原神", ok=False, message="原神 抓取报错")]

    merged, stale = us.merge_with_last_good(results, us.empty_state())

    assert merged == []
    assert stale == []


def test_parse_page_entries_recovers_values_from_an_existing_page():
    """状态文件丢失时，从已有页面反推上次成功值。"""
    page = us.render_page([sample_result("米游社", version="2.115.0")])

    entries = us.parse_page_entries(page)

    assert entries["米游社"]["version"] == "2.115.0"
    assert entries["米游社"]["url"] == "https://example.com/a.apk"
    assert entries["米游社"]["reference"] == "mihoyobbs"
    assert entries["米游社"]["fetched_at"] == "2026-09-12T06:30:00+08:00"


def test_parse_page_entries_reads_the_legacy_page_format():
    """仓库里已提交的旧页面没有 data-* 属性，兜底必须也能读懂它。"""
    legacy_page = (
        "<!DOCTYPE html>\n<html>\n<head><meta charset=\"utf-8\"></head>\n<body>\n"
        "    <h2>我的专属下载源</h2>\n"
        '    <p>原神: <a href="https://example.com/x/yuanshen_7.0.0.apk">v7.0.0</a> '
        "(文件名参考: yuanshen_7.0.0.apk)</p>\n"
        '    <p>CHelper: <a href="https://example.com/CHelper-latest.apk">v26.1.0</a> '
        "(识别标识: chelper)</p>\n"
        "\n</body>\n</html>\n"
    )

    entries = us.parse_page_entries(legacy_page)

    assert set(entries) == {"原神", "CHelper"}
    assert entries["原神"]["version"] == "7.0.0"
    assert entries["原神"]["url"] == "https://example.com/x/yuanshen_7.0.0.apk"
    assert entries["原神"]["reference"] == "yuanshen_7.0.0.apk"
    assert entries["原神"]["fetched_at"] == ""
    assert entries["CHelper"]["reference_label"] == "识别标识"
    assert entries["CHelper"]["reference"] == "chelper"


def test_parse_page_entries_ignores_unrelated_paragraphs():
    page = "<p>这一行不是应用条目</p>\n" + us.render_page([sample_result()])

    assert set(us.parse_page_entries(page)) == {"米游社"}


def test_reference_from_url_accepts_only_apk_names():
    assert us.reference_from_url("https://e.com/a/app_1.2.3.apk") == "app_1.2.3.apk"
    assert us.reference_from_url("https://e.com/a/app_1.2.3.apk?x=1") == "app_1.2.3.apk"
    assert us.reference_from_url("https://d.taptap.cn/latest/seo-bing#taptap_fake.apk") == ""
    assert us.reference_from_url("https://e.com/download") == ""


def test_cleanup_caches_removes_only_cache_dirs(tmp_path):
    for name in ("__pycache__", ".pytest_cache"):
        (tmp_path / name).mkdir()
    (tmp_path / "keep.txt").write_text("keep", encoding="utf-8")

    removed = us.cleanup_caches(tmp_path)

    assert set(removed) == {"__pycache__", ".pytest_cache"}
    assert not (tmp_path / "__pycache__").exists()
    assert (tmp_path / "keep.txt").exists()


# --- 退出码与写入策略 ---------------------------------------------------


def test_main_writes_output_and_state_when_all_sources_succeed(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    results = [sample_result()]
    monkeypatch.setattr(us, "fetch_all", lambda *args, **kwargs: results)
    output = tmp_path / "index.html"
    state = tmp_path / "state.json"

    code = us.main(run_args(tmp_path))

    assert code == us.EXIT_OK
    assert output.read_text(encoding="utf-8") == us.render_page(results)
    assert json.loads(state.read_text(encoding="utf-8"))["entries"]["米游社"]["version"] == "2.115.0"


def test_main_creates_output_parent_directories(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(
        us, "fetch_all", lambda *args, **kwargs: [us.SourceResult(name="米游社", ok=True, version="1.0.0")]
    )
    output = tmp_path / "nested" / "build" / "index.html"

    code = us.main(["--write", "--output", str(output), "--state", str(tmp_path / "s.json")])

    assert code == us.EXIT_OK
    assert output.exists()


def test_main_publishes_page_when_one_source_fails(monkeypatch, tmp_path):
    """P1-2/P1-3 的核心：一个来源失败不再让九个应用一起停更。"""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    state = tmp_path / "state.json"
    write_state(
        state,
        {
            "原神": {
                "version": "7.0.0",
                "url": "https://example.com/yuanshen_7.0.0.apk",
                "reference": "yuanshen_7.0.0.apk",
                "fetched_at": "2026-09-10T06:30:00+08:00",
            }
        },
    )
    monkeypatch.setattr(
        us,
        "fetch_all",
        lambda *args, **kwargs: [
            sample_result("米游社", version="2.116.0"),
            us.SourceResult(name="原神", ok=False, message="原神 抓取报错"),
        ],
    )
    output = tmp_path / "index.html"

    code = us.main(run_args(tmp_path))

    assert code == us.EXIT_DEGRADED
    page = output.read_text(encoding="utf-8")
    assert "v2.116.0" in page
    assert "v7.0.0" in page
    assert "[沿用 2026-09-10 06:30]" in page


def test_main_refuses_to_write_when_no_source_succeeds(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(
        us,
        "fetch_all",
        lambda *args, **kwargs: [us.SourceResult(name="原神", ok=False, message="原神 抓取报错")],
    )
    output = tmp_path / "index.html"
    output.write_text("旧内容", encoding="utf-8")

    code = us.main(run_args(tmp_path))

    assert code == us.EXIT_FAILED
    assert output.read_text(encoding="utf-8") == "旧内容"


def test_main_returns_failed_when_only_failures_and_history(tmp_path, monkeypatch):
    """页面里已无可用条目时，即使状态文件有历史也不能发布空页面。"""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(
        us,
        "fetch_all",
        lambda *args, **kwargs: [us.SourceResult(name="原神", ok=False, message="原神 抓取报错")],
    )
    state = tmp_path / "state.json"
    write_state(state, {"米游社": {"url": "https://example.com/a.apk", "version": "1.0.0"}})

    code = us.main(run_args(tmp_path))

    assert code == us.EXIT_FAILED
    assert not (tmp_path / "index.html").exists()


def test_strict_mode_escalates_degraded_to_failed(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    state = tmp_path / "state.json"
    write_state(state, {"原神": {"url": "https://example.com/a.apk", "version": "7.0.0"}})
    monkeypatch.setattr(
        us,
        "fetch_all",
        lambda *args, **kwargs: [
            sample_result(),
            us.SourceResult(name="原神", ok=False, message="原神 抓取报错"),
        ],
    )

    code = us.main(run_args(tmp_path, "--strict"))

    assert code == us.EXIT_FAILED


def test_github_actions_environment_enables_write(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(us, "fetch_all", lambda *args, **kwargs: [sample_result()])
    output = tmp_path / "index.html"

    code = us.main(["--output", str(output), "--state", str(tmp_path / "s.json")])

    assert code == us.EXIT_OK
    assert output.exists()


def test_main_skips_write_without_flag(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(us, "fetch_all", lambda *args, **kwargs: [sample_result()])
    output = tmp_path / "index.html"

    code = us.main(["--output", str(output), "--state", str(tmp_path / "s.json")])

    assert code == us.EXIT_OK
    assert not output.exists()


def test_write_page_keeps_old_file_when_replace_fails(monkeypatch, tmp_path):
    output = tmp_path / "index.html"
    output.write_text("旧内容", encoding="utf-8")

    def fail_replace(self, target):
        raise OSError("replace failed")

    monkeypatch.setattr(us.Path, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        us.write_page(output, "新内容")

    assert output.read_text(encoding="utf-8") == "旧内容"
    assert list(tmp_path.glob("*.tmp")) == []


# --- 参数与入口 ---------------------------------------------------------


def test_normalize_budget_rejects_non_positive_and_caps():
    assert us.normalize_budget(30) == 30
    assert us.normalize_budget(10_000) == us.MAX_BUDGET
    with pytest.raises(ValueError):
        us.normalize_budget(0)


def test_normalize_jobs_floors_at_one():
    assert us.normalize_jobs(0) == 1
    assert us.normalize_jobs(-5) == 1
    assert us.normalize_jobs(4) == 4


def test_main_does_not_clean_caches(monkeypatch, tmp_path):
    """P2-1：入口不再产生删除缓存目录的副作用。"""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(us, "fetch_all", lambda *args, **kwargs: [sample_result()])
    calls = []
    monkeypatch.setattr(us, "cleanup_caches", lambda base: calls.append(base) or [])

    us.main(["--output", str(tmp_path / "index.html"), "--state", str(tmp_path / "s.json")])

    assert calls == []


def test_status_snapshot_records_duration_and_published_flag(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(us, "fetch_all", lambda *args, **kwargs: [sample_result()])
    status = tmp_path / "status.json"

    us.main(run_args(tmp_path))

    snapshot = json.loads(status.read_text(encoding="utf-8"))
    assert snapshot["published"] is True
    assert isinstance(snapshot["sources"][0]["duration_ms"], int)


def test_run_warns_when_every_source_falls_back(monkeypatch, tmp_path, capsys):
    """提交是绿的但页面全是旧数据——这种最容易被误读的情况必须显式警告。"""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    state = tmp_path / "state.json"
    write_state(state, {"原神": {"url": "https://example.com/a.apk", "version": "7.0.0"}})
    monkeypatch.setattr(
        us,
        "fetch_all",
        lambda *args, **kwargs: [us.SourceResult(name="原神", ok=False, message="原神 抓取报错")],
    )

    code = us.main(run_args(tmp_path))

    out = capsys.readouterr().out
    assert code == us.EXIT_DEGRADED
    assert "全部抓取失败" in out
    assert "没有任何新数据" in out


def test_run_does_not_warn_when_at_least_one_source_is_fresh(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    state = tmp_path / "state.json"
    write_state(state, {"原神": {"url": "https://example.com/a.apk", "version": "7.0.0"}})
    monkeypatch.setattr(
        us,
        "fetch_all",
        lambda *args, **kwargs: [
            sample_result(),
            us.SourceResult(name="原神", ok=False, message="原神 抓取报错"),
        ],
    )

    us.main(run_args(tmp_path))

    assert "全部抓取失败" not in capsys.readouterr().out


def test_default_budget_is_generous_enough_for_slow_runners():
    """历史观测里成功运行最慢约 191 秒，预算必须高于它，否则会把正常运行判死。"""
    assert us.DEFAULT_BUDGET >= 180


def test_status_snapshot_is_written_for_ci_summary(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(
        us,
        "fetch_all",
        lambda *args, **kwargs: [
            sample_result("米游社", version="2.116.0"),
            us.SourceResult(name="原神", ok=False, message="原神 抓取报错"),
        ],
    )
    status = tmp_path / "status.json"

    us.main(run_args(tmp_path))

    snapshot = json.loads(status.read_text(encoding="utf-8"))
    assert snapshot["generated_at"]
    assert snapshot["published"] is True
    assert [(s["name"], s["ok"]) for s in snapshot["sources"]] == [
        ("米游社", True),
        ("原神", False),
    ]
    # 抓取结果（而不是合并结果）进入快照，便于 CI 报告哪个来源坏了
    assert snapshot["sources"][1]["message"] == "原神 抓取报错"


def test_status_snapshot_is_written_even_when_nothing_publishes(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(
        us,
        "fetch_all",
        lambda *args, **kwargs: [us.SourceResult(name="原神", ok=False, message="原神 抓取报错")],
    )
    status = tmp_path / "status.json"

    code = us.main(run_args(tmp_path))

    assert code == us.EXIT_FAILED
    snapshot = json.loads(status.read_text(encoding="utf-8"))
    assert snapshot["published"] is False
    assert snapshot["sources"][0]["ok"] is False
