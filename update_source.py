"""抓取各应用官方下载源，生成 Obtainium 自定义源页面。

本地运行时默认只打印抓取结果，不写文件；加 --write 参数或运行在 GitHub
Actions 中才写入输出文件。只要有一个来源抓取失败，就不写文件并返回非 0
退出码：自动任务会因此变红提醒，同时上一次成功生成的 index.html 保持不动。
"""

from __future__ import annotations

import argparse
import html
import os
import random
import re
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Callable, Sequence

import requests
import urllib3
from urllib3.exceptions import InsecureRequestWarning

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT}

DEFAULT_TIMEOUT = 10
DEFAULT_RETRIES = 3
DEFAULT_JOBS = 8
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}

CACHE_DIRS = ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")

PAGE_TEMPLATE = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
    <h2>我的专属下载源</h2>
{lines}
</body>
</html>
"""


class MissingLocationError(RuntimeError):
    """响应缺少 Location 跳转链接。"""


@dataclass(frozen=True)
class SourceResult:
    """单个应用的抓取结果。"""

    name: str
    ok: bool
    version: str = ""
    url: str = ""
    reference: str = ""
    reference_label: str = "文件名参考"
    message: str = ""


@dataclass(frozen=True)
class RedirectSource:
    """通过 302 跳转获取下载地址的来源。"""

    name: str
    api: str
    version_pattern: str = r"_([a-zA-Z0-9\.\-]+)\.apk"
    version_transform: Callable[[str], str] | None = None
    url_override: str | None = None


REDIRECT_SOURCES = (
    RedirectSource(
        "原神",
        "https://ys-api.mihoyo.com/event/download_porter/link/ys_cn/official/android_default",
    ),
    RedirectSource(
        "云·原神",
        "https://api-takumi.mihoyo.com/event/download_porter/link/clgm_cn/official/android_web",
    ),
    RedirectSource(
        "云·星穹铁道",
        "https://act-api-takumi.mihoyo.com/event/download_porter/link/clgm_hkrpg-cn/official/android_default",
    ),
    RedirectSource(
        "云·绝区零",
        "https://act-api-takumi.mihoyo.com/event/download_porter/link/clgm_nap-cn/official/android_cloudgame",
    ),
    RedirectSource(
        "植物大战僵尸2",
        "https://pvz2download.ditwan.cn/download-service/baokai",
        version_pattern=r"baokai_([\d\.]+)_",
    ),
    RedirectSource(
        "TapTap",
        "https://d.taptap.cn/latest/seo-bing",
        version_transform=lambda version: version.replace("-rel.", "-rel#"),
        url_override="https://d.taptap.cn/latest/seo-bing#taptap_fake.apk",
    ),
)

MIYOUSHE_API = (
    "https://bbs-api.miyoushe.com/misc/wapi/getLatestPkgVer?channel=miyousheluodi"
)
MIYOUSHE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.miyoushe.com/",
}

KUAIBAO_API = "https://d.3839.com/Cj"

CHELPER_CHANGELOG_URL = "https://www.yanceymc.cn/api/chelper/CHANGELOG.md"
CHELPER_RELEASE_NOTES_URL = "https://www.yanceymc.cn/chelper_doc/chelper-release-notes"
CHELPER_DOWNLOAD_URL = "https://www.yanceymc.cn/api/chelper/CHelper-latest.apk"

VERSION_IN_MARKDOWN = re.compile(r"^[vV]?(\d+\.\d+\.\d+)", re.MULTILINE)
VERSION_IN_HEADING = re.compile(r"<h[1-3][^>]*>\s*[vV]?(\d+\.\d+\.\d+)")
VERSION_ANYWHERE = re.compile(r"[vV](\d+\.\d+\.\d+)")


def get_retry_delay(attempt, response=None, base_delay=1.5, max_delay=8):
    retry_after = response.headers.get("Retry-After") if response is not None else None
    if retry_after:
        try:
            return min(float(retry_after), max_delay)
        except ValueError:
            pass

    delay = min(base_delay * (2 ** attempt), max_delay)
    return delay + random.uniform(0, 0.5)


def get_with_retry(
    name,
    url,
    *,
    request_headers=None,
    timeout=DEFAULT_TIMEOUT,
    max_retries=DEFAULT_RETRIES,
    allow_redirects=True,
    verify=True,
    require_location=False,
    retry_ssl_errors=True,
):
    request_headers = request_headers or HEADERS
    last_error = None
    last_response = None

    for attempt in range(max_retries):
        try:
            res = requests.get(
                url,
                headers=request_headers,
                allow_redirects=allow_redirects,
                timeout=timeout,
                verify=verify,
            )
            last_response = res

            if res.status_code in RETRYABLE_STATUS_CODES:
                raise requests.exceptions.HTTPError(
                    f"{name} 返回可重试状态码 HTTP {res.status_code}",
                    response=res,
                )

            res.raise_for_status()

            if require_location and not res.headers.get("Location"):
                raise MissingLocationError(f"{name} 响应缺少 Location 跳转链接")

            return res

        except requests.exceptions.SSLError as e:
            if not retry_ssl_errors:
                raise
            last_error = e
            last_response = None
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else None
            if status_code not in RETRYABLE_STATUS_CODES:
                raise
            last_error = e
            last_response = e.response
        except requests.exceptions.RequestException as e:
            last_error = e
            last_response = getattr(e, "response", None)
        except MissingLocationError as e:
            last_error = e

        if attempt == max_retries - 1:
            break

        delay = get_retry_delay(attempt, last_response)
        print(f"{name} 请求失败，第 {attempt + 2}/{max_retries} 次尝试将在 {delay:.1f} 秒后开始... ({last_error})")
        time.sleep(delay)

    raise last_error if last_error else RuntimeError(f"{name} 请求失败")


def fetch_chelper_page(url, max_retries=3):
    """CHelper 官网证书偶尔异常，只在这个站点失败后降级重试。"""
    ch_headers = {
        **HEADERS,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "close",
    }

    try:
        res = get_with_retry(
            "CHelper",
            url,
            request_headers=ch_headers,
            timeout=15,
            max_retries=max_retries,
            retry_ssl_errors=False,
        )
    except requests.exceptions.SSLError:
        urllib3.disable_warnings(category=InsecureRequestWarning)
        res = get_with_retry(
            "CHelper",
            url,
            request_headers=ch_headers,
            timeout=15,
            max_retries=max_retries,
            verify=False,
        )
        print("CHelper HTTPS 校验失败，已仅对此站点关闭证书校验后重试成功")

    res.encoding = "utf-8"
    return res.text


def parse_chelper_changelog(markdown_text: str) -> str | None:
    """从 CHANGELOG.md 里取最新版本号。"""
    match = VERSION_IN_MARKDOWN.search(markdown_text)
    return match.group(1) if match else None


def parse_chelper_release_notes(page_text: str) -> str | None:
    """兜底方案：从更新日志页静态 HTML 的正文里取版本号。"""
    body = page_text.split("</head>")[-1]
    match = VERSION_IN_HEADING.search(body) or VERSION_ANYWHERE.search(body)
    return match.group(1) if match else None


def fetch_miyoushe() -> SourceResult:
    name = "米游社"
    try:
        res = get_with_retry(name, MIYOUSHE_API, request_headers=MIYOUSHE_HEADERS)
        data = res.json()
        version = (data.get("data") or {}).get("version")
        if not version:
            return SourceResult(name=name, ok=False, message=f"{name} 响应异常: {data}")

        url = f"https://download-bbs.miyoushe.com/app/mihoyobbs_{version}_miyousheluodi.apk"
        return SourceResult(
            name=name,
            ok=True,
            version=version,
            url=url,
            reference="mihoyobbs",
            message=f"{name} 抓取成功: v{version}",
        )
    except requests.exceptions.RequestException as e:
        return SourceResult(name=name, ok=False, message=f"{name} 抓取报错: {e}")
    except Exception as e:
        return SourceResult(name=name, ok=False, message=f"{name} 数据解析报错: {e}")


def fetch_redirect_source(spec: RedirectSource) -> SourceResult:
    try:
        res = get_with_retry(
            spec.name,
            spec.api,
            allow_redirects=False,
            require_location=True,
        )
    except (requests.exceptions.RequestException, MissingLocationError) as e:
        return SourceResult(name=spec.name, ok=False, message=f"{spec.name} 抓取报错: {e}")

    real_url = res.headers["Location"]
    filename = real_url.split("/")[-1].split("?")[0]

    match = re.search(spec.version_pattern, real_url)
    if not match:
        return SourceResult(
            name=spec.name,
            ok=False,
            message=f"{spec.name} 版本号解析失败: {real_url}",
        )

    version = match.group(1)
    if spec.version_transform is not None:
        version = spec.version_transform(version)

    return SourceResult(
        name=spec.name,
        ok=True,
        version=version,
        url=spec.url_override or real_url,
        reference=filename,
        message=f"{spec.name} 抓取成功: v{version}",
    )


def fetch_kuaibao() -> SourceResult:
    name = "好游快爆"
    try:
        res = get_with_retry(name, KUAIBAO_API, allow_redirects=False, require_location=True)
    except (requests.exceptions.RequestException, MissingLocationError) as e:
        return SourceResult(name=name, ok=False, message=f"{name} 抓取报错: {e}")

    real_url = res.headers["Location"]
    filename = real_url.split("/")[-1].split("?")[0]

    match = re.search(r"HYKB(\d{6})", real_url)
    if not match:
        return SourceResult(
            name=name,
            ok=False,
            message=f"{name} 版本号解析失败: {real_url}",
        )

    # 文件名里的 HYKB 后 6 位数字还原成 x.y.z.build，便于 Obtainium 比较版本
    digits = match.group(1)
    version = f"{digits[0]}.{digits[1]}.{digits[2]}.{digits[3:]}"
    return SourceResult(
        name=name,
        ok=True,
        version=version,
        url=real_url,
        reference=filename,
        message=f"{name} 抓取成功: v{version}",
    )


def fetch_chelper() -> SourceResult:
    name = "CHelper"
    try:
        version = parse_chelper_changelog(fetch_chelper_page(CHELPER_CHANGELOG_URL))
        if not version:
            version = parse_chelper_release_notes(fetch_chelper_page(CHELPER_RELEASE_NOTES_URL))

        if not version:
            return SourceResult(name=name, ok=False, message=f"{name} 版本号解析失败")

        return SourceResult(
            name=name,
            ok=True,
            version=version,
            url=CHELPER_DOWNLOAD_URL,
            reference="chelper",
            reference_label="识别标识",
            message=f"{name} 网页抓取成功: v{version}",
        )
    except Exception as e:
        return SourceResult(name=name, ok=False, message=f"{name} 网页抓取报错: {e}")


def build_sources() -> list[tuple[str, Callable[[], SourceResult]]]:
    """按页面输出顺序返回所有来源。"""
    sources: list[tuple[str, Callable[[], SourceResult]]] = [("米游社", fetch_miyoushe)]
    sources += [
        (spec.name, partial(fetch_redirect_source, spec)) for spec in REDIRECT_SOURCES
    ]
    sources.append(("好游快爆", fetch_kuaibao))
    sources.append(("CHelper", fetch_chelper))
    return sources


def _run_source(name: str, fetcher: Callable[[], SourceResult]) -> SourceResult:
    try:
        return fetcher()
    except Exception as e:
        # 单个来源的意外错误不应该影响其它来源
        return SourceResult(name=name, ok=False, message=f"{name} 发生未知报错: {e}")


def fetch_all(jobs: int = DEFAULT_JOBS) -> list[SourceResult]:
    """并发抓取全部来源，返回顺序与 build_sources 一致。"""
    sources = build_sources()
    workers = max(1, min(jobs, len(sources)))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            (name, pool.submit(_run_source, name, fetcher)) for name, fetcher in sources
        ]
        return [future.result() for _, future in futures]


def render_line(result: SourceResult) -> str:
    name = html.escape(result.name)
    url = html.escape(result.url, quote=True)
    version = html.escape(result.version)
    reference = html.escape(result.reference)
    reference_label = html.escape(result.reference_label)
    return (
        f'    <p>{name}: <a href="{url}">v{version}</a> '
        f"({reference_label}: {reference})</p>"
    )


def render_page(results: Sequence[SourceResult]) -> str:
    lines = "".join(f"{render_line(result)}\n" for result in results if result.ok)
    return PAGE_TEMPLATE.format(lines=lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抓取应用下载源并生成 Obtainium 自定义源页面")
    parser.add_argument(
        "--write",
        action="store_true",
        help="允许写入输出文件，本地不加此参数时只打印结果",
    )
    parser.add_argument("--output", default="index.html", help="输出文件路径，默认 index.html")
    parser.add_argument(
        "--jobs",
        type=int,
        default=DEFAULT_JOBS,
        help=f"并发抓取线程数，默认 {DEFAULT_JOBS}",
    )
    return parser.parse_args(argv)


def write_allowed(args: argparse.Namespace) -> bool:
    return args.write or os.environ.get("GITHUB_ACTIONS") == "true"


def cleanup_caches(base_dir: Path) -> list[str]:
    removed = []
    for name in CACHE_DIRS:
        path = base_dir / name
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            removed.append(name)
    return removed


def write_page(output: Path, content: str) -> None:
    """Create parent directories and atomically replace the output file."""
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
            temporary_path = Path(temporary_file.name)

        temporary_path.replace(output)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def run(args: argparse.Namespace) -> int:
    results = fetch_all(jobs=args.jobs)
    for result in results:
        print(result.message)

    failures = [result for result in results if not result.ok]
    if failures:
        print(f"本次有 {len(failures)} 个应用抓取失败，保留上一次的 index.html 结果，不生成新文件")
        return 1

    if not write_allowed(args):
        print("本地运行不生成 index.html（仅 GitHub Actions 自动更新或加 --write 参数时生成）")
        return 0

    output = Path(args.output)
    write_page(output, render_page(results))
    print(f"已生成 {output}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    finally:
        for name in cleanup_caches(Path(__file__).resolve().parent):
            print(f"已清理缓存文件夹: {name}")


if __name__ == "__main__":
    sys.exit(main())
