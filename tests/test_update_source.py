"""update_source.py 的离线单元测试，不访问网络。"""

import sys
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


def redirect_source(name):
    return next(spec for spec in us.REDIRECT_SOURCES if spec.name == name)


def patch_location(monkeypatch, location):
    monkeypatch.setattr(
        us,
        "get_with_retry",
        lambda *args, **kwargs: FakeResponse(headers={"Location": location}),
    )


def test_redirect_source_parses_version_and_reference(monkeypatch):
    patch_location(monkeypatch, YUANSHEN_LOCATION)

    result = us.fetch_redirect_source(redirect_source("原神"))

    assert result.ok
    assert result.version == "7.0.0"
    assert result.url == YUANSHEN_LOCATION
    assert result.reference == "yuanshen_7.0.0.apk"
    assert us.render_line(result) == (
        f'    <p>原神: <a href="{YUANSHEN_LOCATION}">v7.0.0</a> '
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
        "fetch_chelper_page",
        lambda url, **kwargs: "未发布内容\n\n26.1.0\n\n- 修复若干问题\n",
    )

    result = us.fetch_chelper()

    assert result.ok
    assert result.version == "26.1.0"
    assert result.reference_label == "识别标识"
    assert us.render_line(result).endswith("(识别标识: chelper)</p>")


def test_chelper_falls_back_to_release_notes(monkeypatch):
    pages = {
        us.CHELPER_CHANGELOG_URL: "暂无版本号",
        us.CHELPER_RELEASE_NOTES_URL: "<html><head></head><body><h2>v1.2.3</h2></body></html>",
    }
    monkeypatch.setattr(us, "fetch_chelper_page", lambda url, **kwargs: pages[url])

    result = us.fetch_chelper()

    assert result.ok
    assert result.version == "1.2.3"


def test_chelper_without_any_version_is_failure(monkeypatch):
    monkeypatch.setattr(us, "fetch_chelper_page", lambda url, **kwargs: "no version here")

    result = us.fetch_chelper()

    assert not result.ok
    assert "版本号解析失败" in result.message


def test_render_page_keeps_obtainium_compatible_layout():
    results = [
        us.SourceResult(
            name="米游社",
            ok=True,
            version="2.115.0",
            url="https://example.com/mihoyobbs.apk",
            reference="mihoyobbs",
        )
    ]

    assert us.render_page(results) == (
        "<!DOCTYPE html>\n"
        "<html>\n"
        '<head><meta charset="utf-8"></head>\n'
        "<body>\n"
        "    <h2>我的专属下载源</h2>\n"
        '    <p>米游社: <a href="https://example.com/mihoyobbs.apk">v2.115.0</a> '
        "(文件名参考: mihoyobbs)</p>\n"
        "\n"
        "</body>\n"
        "</html>\n"
    )


def test_render_page_escapes_untrusted_values():
    results = [
        us.SourceResult(
            name="原神",
            ok=True,
            version="1.0",
            url='https://example.com/a.apk?a=1&b="2"',
            reference="a.apk",
            reference_label="文件名<参考>",
        )
    ]

    page = us.render_page(results)

    assert "&amp;" in page
    assert "&quot;" in page
    assert 'a=1&amp;b=&quot;2&quot;' in page
    assert '"2"' not in page
    assert "文件名&lt;参考&gt;" in page
    assert page.count("<p>") == 1


def test_get_with_retry_retries_retryable_status(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return FakeResponse(status_code=503)
        return FakeResponse(status_code=200)

    monkeypatch.setattr(us.requests, "get", fake_get)
    monkeypatch.setattr(us.time, "sleep", lambda seconds: None)

    response = us.get_with_retry("测试来源", "https://example.com")

    assert response.status_code == 200
    assert len(calls) == 2


def test_get_with_retry_raises_on_client_error(monkeypatch):
    monkeypatch.setattr(us.requests, "get", lambda url, **kwargs: FakeResponse(status_code=404))
    monkeypatch.setattr(us.time, "sleep", lambda seconds: None)

    with pytest.raises(requests.exceptions.HTTPError):
        us.get_with_retry("测试来源", "https://example.com")


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


def test_cleanup_caches_removes_only_cache_dirs(tmp_path):
    for name in ("__pycache__", ".pytest_cache"):
        (tmp_path / name).mkdir()
    (tmp_path / "keep.txt").write_text("keep", encoding="utf-8")

    removed = us.cleanup_caches(tmp_path)

    assert set(removed) == {"__pycache__", ".pytest_cache"}
    assert not (tmp_path / "__pycache__").exists()
    assert (tmp_path / "keep.txt").exists()


def test_main_writes_output_when_all_sources_succeed(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(us, "cleanup_caches", lambda base_dir: [])
    results = [
        us.SourceResult(
            name="米游社",
            ok=True,
            version="1.0.0",
            url="https://example.com/a.apk",
            reference="mihoyobbs",
        )
    ]
    monkeypatch.setattr(us, "fetch_all", lambda *args, **kwargs: results)
    output = tmp_path / "index.html"

    code = us.main(["--write", "--output", str(output)])

    assert code == 0
    assert output.read_text(encoding="utf-8") == us.render_page(results)


def test_main_creates_output_parent_directories(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(us, "cleanup_caches", lambda base_dir: [])
    results = [us.SourceResult(name="米游社", ok=True, version="1.0.0")]
    monkeypatch.setattr(us, "fetch_all", lambda *args, **kwargs: results)
    output = tmp_path / "nested" / "build" / "index.html"

    code = us.main(["--write", "--output", str(output)])

    assert code == 0
    assert output.read_text(encoding="utf-8") == us.render_page(results)


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


def test_main_skips_write_without_flag(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(us, "cleanup_caches", lambda base_dir: [])
    monkeypatch.setattr(
        us,
        "fetch_all",
        lambda *args, **kwargs: [us.SourceResult(name="米游社", ok=True, version="1.0.0")],
    )
    output = tmp_path / "index.html"

    code = us.main(["--output", str(output)])

    assert code == 0
    assert not output.exists()


def test_github_actions_environment_enables_write(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(us, "cleanup_caches", lambda base_dir: [])
    monkeypatch.setattr(
        us,
        "fetch_all",
        lambda *args, **kwargs: [us.SourceResult(name="米游社", ok=True, version="1.0.0")],
    )
    output = tmp_path / "index.html"

    code = us.main(["--output", str(output)])

    assert code == 0
    assert output.exists()


def test_main_returns_failure_and_keeps_old_file(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(us, "cleanup_caches", lambda base_dir: [])
    monkeypatch.setattr(
        us,
        "fetch_all",
        lambda *args, **kwargs: [
            us.SourceResult(name="米游社", ok=True, version="1.0.0"),
            us.SourceResult(name="原神", ok=False, message="原神 抓取报错"),
        ],
    )
    output = tmp_path / "index.html"
    output.write_text("旧内容", encoding="utf-8")

    code = us.main(["--write", "--output", str(output)])

    assert code == 1
    assert output.read_text(encoding="utf-8") == "旧内容"
