"""抓取各应用官方下载源，生成 Obtainium 自定义源页面。

设计要点（改动见 README 的「版本策略」一节）：

- 网络层统一走一个共享 Session，重试交给 urllib3 的 Retry 策略，超时与
  退避不再手写，避免「重试两次叠加」和错误信息被覆盖。
- 每个来源都有总预算，单个来源卡住不会拖慢整轮。
- 页面按「尽力而为」发布：某个来源失败时，沿用上一次成功抓到的值并在页面
  上标注，而不是让九个应用一起停在旧版本。
- 退出码区分故障等级：0 全部成功，1 有来源降级但页面已更新，2 无法生成页面。
- 本地运行时默认只打印结果，不写文件；加 --write 或运行在 GitHub Actions
  中才写入输出文件。
"""

from __future__ import annotations

import argparse
import html
import json
import os
import random
import re
import shutil
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Callable, Mapping, Sequence

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.exceptions import InsecureRequestWarning
from urllib3.util.retry import Retry

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT}

DEFAULT_TIMEOUT = 10
DEFAULT_RETRIES = 3
DEFAULT_JOBS = 8
DEFAULT_BUDGET = 60
MAX_BUDGET = 600
BACKOFF_BASE = 1.5
BACKOFF_MAX = 8
RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

DEFAULT_OUTPUT = "index.html"
DEFAULT_STATE = "sources-last-good.json"
DEFAULT_STATUS = "sources-status.json"

# 退出码：0 正常，1 有来源降级（页面仍是本次生成的），2 无法生成页面
EXIT_OK = 0
EXIT_DEGRADED = 1
EXIT_FAILED = 2

CACHE_DIRS = ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")

# 版本号格式白名单：宁可明确报失败，也不把残缺值写进页面
ANCHORED_VERSION_PATTERNS = {
    "原神": r"\d+\.\d+\.\d+",
    "云·原神": r"\d+\.\d+\.\d+",
    "云·星穹铁道": r"\d+\.\d+\.\d+",
    "云·绝区零": r"\d+(\.\d+){0,2}",
    "植物大战僵尸2": r"\d+\.\d+\.\d+",
    "TapTap": r"\d+\.\d+\.\d+-rel#\d+",
    "好游快爆": r"\d+\.\d+\.\d+\.\d+",
}

PAGE_TEMPLATE = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
    <h2>我的专属下载源</h2>
{lines}
</body>
</html>
"""

PAGE_LINE_PATTERN = re.compile(
    r"<p\b[^>]*>\s*(?P<name>[^:<]*?):\s*"
    r"<a href=\"(?P<url>[^\"]*)\">v(?P<version>[^<]*)</a>"
    r"\s*\((?P<reference_label>[^:：]*)[:：]\s*(?P<reference>[^)]*)\)"
    r"(?P<stale>[^<]*)</p>",
    re.DOTALL,
)
PAGE_SOURCE_PATTERN = re.compile(r'<p\b[^>]*data-source="(?P<source>[^"]*)"[^>]*>')
PAGE_FETCHED_PATTERN = re.compile(r'data-fetched-at="(?P<fetched>[^"]*)"')
APK_NAME_PATTERN = re.compile(r"^[^/?#]+\.apk$", re.IGNORECASE)


class MissingLocationError(RuntimeError):
    """响应缺少 Location 跳转链接。"""


class SourceTimeoutError(RuntimeError):
    """来源超出本轮总预算。"""


@dataclass(frozen=True)
class SourceResult:
    """单个应用的抓取结果。"""

    name: str
    ok: bool
    version: str = ""
    url: str = ""
    reference: str = ""
    reference_label: str = "文件名参考"
    fetched_at: str = ""
    stale: bool = False
    message: str = ""


@dataclass(frozen=True)
class RedirectSource:
    """通过 302 跳转获取下载地址的来源。"""

    name: str
    api: str
    version_pattern: str = r"_([a-zA-Z0-9\.\-]+)\.apk"
    version_transform: Callable[[str], str] | None = None
    url_override: str | None = None
    version_validate: str | None = None
    reference_label: str = "文件名参考"
    # 仅在确有不兼容的老站点上显式关闭；关闭时每次都会打印告警
    verify: bool = True
    retries: int = DEFAULT_RETRIES


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


def empty_state() -> dict:
    """last-good 状态文件的空结构。"""
    return {"_version": 1, "updated_at": "", "entries": {}}


class LockedSession(requests.Session):
    """对 requests.Session 加锁，使其可以安全地被多个抓取线程共享。

    实测并发调用未加锁的 Session 会间歇性丢掉请求（requests 限制每 10 个
    请求做一次连接回收，那一步不是线程安全的），所以这里用可重入锁把
    request/send 串行化。串行化只影响「发出请求」这一小段，网络等待期间
    不持锁，因此九个来源依然并发。
    """

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.RLock()

    def request(self, *args, **kwargs):
        with self._lock:
            return super().request(*args, **kwargs)


def build_session(pool_size: int = DEFAULT_JOBS, max_retries: int = 0):
    """构造共享 Session：连接池按并发数放大，重试策略可配。

    日常使用的连接池把 max_retries 设为 1（即不做自动重试），重试改由
    get_with_retry 的循环负责，好在两次尝试之间检查总预算。
    """
    retry = Retry(
        total=max_retries,
        connect=max_retries,
        read=max_retries,
        status=max_retries,
        other=0,
        backoff_factor=BACKOFF_BASE,
        backoff_max=BACKOFF_MAX,
        status_forcelist=sorted(RETRYABLE_STATUS_CODES),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=pool_size, pool_maxsize=pool_size)

    session = LockedSession()
    session.headers.update(HEADERS)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


# 九个来源共用一个连接池。重试一律由 get_with_retry 的循环负责（策略里的
# total=1 表示「不自动重试」），这样每次重试之间都能检查本轮总预算，
# 而不是交给 urllib3 一路跑完。
POOL_SIZE = max(DEFAULT_JOBS, 4)
SESSION = build_session(pool_size=POOL_SIZE, max_retries=1)

_deadline_state = threading.local()
_budget: ContextVar[float] = ContextVar("budget", default=DEFAULT_BUDGET)


def set_active_deadline(deadline: float | None) -> None:
    _deadline_state.deadline = deadline


def active_deadline() -> float | None:
    """当前线程的抓取截止时间；没有预算时返回 None。"""
    return getattr(_deadline_state, "deadline", None)


def remaining_budget(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return deadline - time.monotonic()


def default_backoff(attempt: int) -> float:
    """循环内两次尝试之间的等待（与 Session 策略使用同一组参数）。"""
    return get_retry_delay(attempt, None)


def is_retryable_error(error: Exception) -> bool:
    """只有网络抖动与可重试状态码才值得再试一次；4xx 契约错误立刻失败。

    会话级 SSL 错误也不在这里重试：调用方（例如 CHelper）可能要用
    关闭校验的方式降级，先重试三次只会白等。
    """
    if isinstance(error, requests.exceptions.SSLError):
        return False
    if isinstance(error, requests.exceptions.HTTPError):
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
        return status in RETRYABLE_STATUS_CODES
    return isinstance(error, requests.exceptions.RequestException)


def get_retry_delay(attempt, response=None, base_delay=BACKOFF_BASE, max_delay=BACKOFF_MAX):
    """退避时长。response 可能没有 headers（例如无响应的 HTTPError）。"""
    headers = getattr(response, "headers", None) or {}
    retry_after = headers.get("Retry-After")
    if retry_after:
        try:
            return min(float(retry_after), max_delay)
        except (TypeError, ValueError):
            # HTTP-date 形式的 Retry-After 交给指数退避
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
    session=None,
):
    """带重试的 GET，并在两次尝试之间检查本轮总预算。

    - 始终走不自动重试的 Session：重试由这里的循环负责，这样预算一旦耗尽
      就会立刻收手，而不是被 urllib3 拖到重试跑完；也让 max_retries 在任何
      调用方式下都真实生效。
    - 响应缺少 Location 属于业务判据，直接判失败，不再白白重试三次。
    """
    deadline = active_deadline()
    if session is None:
        session = SESSION

    attempts = max(1, max_retries)
    last_error: Exception | None = None

    for attempt in range(attempts):
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SourceTimeoutError(f"{name} 超出本轮总预算")

        try:
            res = session.get(
                url,
                headers=request_headers or HEADERS,
                allow_redirects=allow_redirects,
                timeout=timeout,
                verify=verify,
            )
            res.raise_for_status()

            if require_location and not res.headers.get("Location"):
                raise MissingLocationError(f"{name} 响应缺少 Location 跳转链接")

            return res
        except MissingLocationError:
            raise
        except requests.exceptions.HTTPError as e:
            if not is_retryable_error(e):
                raise
            last_error = e
        except requests.exceptions.RequestException as e:
            if not is_retryable_error(e):
                raise
            last_error = e

        if attempt == attempts - 1:
            break
        time.sleep(default_backoff(attempt))

    raise last_error if last_error is not None else RuntimeError(f"{name} 请求失败")


def fetch_page(url, *, name="来源", request_headers=None, timeout=DEFAULT_TIMEOUT,
               max_retries=DEFAULT_RETRIES, verify=True):
    """抓一个站点，永不抛出：返回 (text, warning)。"""
    warning = ""
    try:
        res = get_with_retry(
            name,
            url,
            request_headers=request_headers,
            timeout=timeout,
            max_retries=max_retries,
            verify=verify,
        )
    except requests.exceptions.SSLError:
        if not verify:
            raise
        urllib3.disable_warnings(category=InsecureRequestWarning)
        warning = f"{name} HTTPS 证书校验失败，已仅对此站点关闭校验后重试"
        res = get_with_retry(
            name,
            url,
            request_headers=request_headers,
            timeout=timeout,
            max_retries=max_retries,
            verify=False,
        )

    res.encoding = "utf-8"
    return res.text, warning


def parse_chelper_changelog(markdown_text: str) -> str | None:
    """从 CHANGELOG.md 里取最新版本号。"""
    match = VERSION_IN_MARKDOWN.search(markdown_text)
    return match.group(1) if match else None


def parse_chelper_release_notes(page_text: str) -> str | None:
    """兜底方案：从更新日志页静态 HTML 的正文里取版本号。"""
    body = page_text.split("</head>")[-1]
    match = VERSION_IN_HEADING.search(body) or VERSION_ANYWHERE.search(body)
    return match.group(1) if match else None


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def valid_version(version, pattern: str) -> bool:
    """严格匹配：模式必须覆盖整个字符串，避免残缺版本号被当成有效值。"""
    if not version:
        return False
    return re.fullmatch(f"(?:{pattern})", version) is not None


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
            fetched_at=now_iso(),
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
            max_retries=spec.retries,
            verify=spec.verify,
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

    pattern = spec.version_validate or ANCHORED_VERSION_PATTERNS.get(spec.name)
    if pattern and not valid_version(version, pattern):
        return SourceResult(
            name=spec.name,
            ok=False,
            message=f"{spec.name} 版本号格式不符预期（{pattern}）: {version}",
        )

    return SourceResult(
        name=spec.name,
        ok=True,
        version=version,
        url=spec.url_override or real_url,
        reference=filename,
        reference_label=spec.reference_label,
        fetched_at=now_iso(),
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
    if not valid_version(version, ANCHORED_VERSION_PATTERNS[name]):
        return SourceResult(
            name=name,
            ok=False,
            message=f"{name} 版本号格式不符预期: {version}",
        )

    return SourceResult(
        name=name,
        ok=True,
        version=version,
        url=real_url,
        reference=filename,
        fetched_at=now_iso(),
        message=f"{name} 抓取成功: v{version}",
    )


def fetch_chelper() -> SourceResult:
    name = "CHelper"
    try:
        changelog, changelog_warning = fetch_page(
            CHELPER_CHANGELOG_URL,
            name="CHelper",
            timeout=15,
            max_retries=2,
        )
        if changelog_warning:
            print(f"警告: {changelog_warning}")

        version = parse_chelper_changelog(changelog)
        if not version:
            notes, notes_warning = fetch_page(
                CHELPER_RELEASE_NOTES_URL,
                name="CHelper",
                timeout=15,
                max_retries=2,
            )
            if notes_warning:
                print(f"警告: {notes_warning}")
            version = parse_chelper_release_notes(notes)

        if not version:
            return SourceResult(name=name, ok=False, message=f"{name} 版本号解析失败")

        return SourceResult(
            name=name,
            ok=True,
            version=version,
            url=CHELPER_DOWNLOAD_URL,
            reference="chelper",
            reference_label="识别标识",
            fetched_at=now_iso(),
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


def _run_with_deadline(name, fetcher, started):
    """把本轮截止时间放进线程局部，让该线程内的网络调用都能看到。"""
    set_active_deadline(started + budget_seconds())
    try:
        return fetcher()
    finally:
        set_active_deadline(None)


def budget_seconds() -> float:
    return _budget.get()


def fetch_all(jobs: int = DEFAULT_JOBS, deadline: float = DEFAULT_BUDGET) -> list[SourceResult]:
    """并发抓取全部来源；返回顺序与 build_sources 一致，永不抛出。"""
    sources = build_sources()
    workers = max(1, min(jobs, len(sources)))
    started = time.monotonic()
    token = _budget.set(deadline)

    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fetch") as pool:
            futures = [
                (name, pool.submit(_run_source, name, partial(_run_with_deadline, name, fetcher, started)))
                for name, fetcher in sources
            ]
            done, _ = wait([future for _, future in futures], timeout=deadline)

            results = []
            for name, future in futures:
                if future not in done:
                    future.cancel()
                    results.append(
                        SourceResult(
                            name=name,
                            ok=False,
                            message=f"{name} 超过 {deadline:.0f} 秒预算，未完成",
                        )
                    )
                    continue
                try:
                    results.append(future.result())
                except Exception as e:
                    results.append(SourceResult(name=name, ok=False, message=f"{name} 发生未知报错: {e}"))
            return results
    finally:
        _budget.reset(token)


def describe_fetched_at(value: str) -> str:
    """把抓取时间写成人类可读的形式；拿不到时间时返回固定说明。"""
    if not value:
        return "上次成功值"
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return value
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone()
    return stamp.strftime("%Y-%m-%d %H:%M")


def render_line(result: SourceResult) -> str:
    name = html.escape(result.name)
    url = html.escape(result.url, quote=True)
    version = html.escape(result.version)
    reference = html.escape(result.reference)
    reference_label = html.escape(result.reference_label)
    source = html.escape(result.name, quote=True)
    fetched_at = html.escape(result.fetched_at, quote=True)
    stale = (
        f' <span class="stale">[沿用 {html.escape(describe_fetched_at(result.fetched_at))}]</span>'
        if result.stale
        else ""
    )
    return (
        f'    <p data-source="{source}" data-fetched-at="{fetched_at}">{name}: '
        f'<a href="{url}">v{version}</a> '
        f"({reference_label}: {reference}){stale}</p>"
    )


def render_header(results: Sequence[SourceResult], generated_at: str = "") -> str:
    """只在确有降级时输出一行统计，全部成功时页面结构与旧版完全一致。"""
    stale = [r.name for r in results if r.stale]
    if not stale:
        return ""

    stamp = generated_at or now_iso()
    return (
        f"    <p>同步时间：{html.escape(stamp)}，"
        f"{len(stale)} 个来源沿用上次成功值：{html.escape('、'.join(stale))}</p>"
    )


def render_page(results: Sequence[SourceResult], generated_at: str = "") -> str:
    header = render_header(results, generated_at)
    if header:
        header += "\n"
    lines = "".join(f"{render_line(result)}\n" for result in results if result.ok)
    return PAGE_TEMPLATE.format(lines=header + lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抓取应用下载源并生成 Obtainium 自定义源页面")
    parser.add_argument(
        "--write",
        action="store_true",
        help="允许写入输出文件，本地不加此参数时只打印结果",
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help=f"输出文件路径，默认 {DEFAULT_OUTPUT}")
    parser.add_argument(
        "--state",
        default=DEFAULT_STATE,
        help=f"上次成功结果的记录文件，默认 {DEFAULT_STATE}",
    )
    parser.add_argument(
        "--status",
        default=DEFAULT_STATUS,
        help=f"本轮结果快照（供 CI 生成摘要），默认 {DEFAULT_STATUS}",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=DEFAULT_JOBS,
        help=f"并发抓取线程数，默认 {DEFAULT_JOBS}",
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=DEFAULT_BUDGET,
        help=f"整轮抓取总预算（秒），默认 {DEFAULT_BUDGET}",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="任一来源失败都返回 2（旧的「一坏全停」语义）",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="不清理缓存目录，便于本地反复运行与测试",
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


def normalize_budget(value: float) -> float:
    if value <= 0:
        raise ValueError("--budget 必须是正数")
    return min(float(value), float(MAX_BUDGET))


def normalize_jobs(value: int) -> int:
    return max(1, int(value))


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
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def load_state(path: Path) -> dict:
    """读取 last-good 状态；缺失或损坏都当作「没有历史」处理。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return empty_state()

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        print(f"警告: {path} 不是合法 JSON，按无历史记录处理")
        return empty_state()

    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        print(f"警告: {path} 结构不符，按无历史记录处理")
        return empty_state()

    state = empty_state()
    state["updated_at"] = str(data.get("updated_at") or "")
    for name, entry in data["entries"].items():
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url") or "")
        version = str(entry.get("version") or "")
        if not url or not version:
            continue
        state["entries"][str(name)] = {
            "version": version,
            "url": url,
            "reference": str(entry.get("reference") or ""),
            "reference_label": str(entry.get("reference_label") or "文件名参考"),
            "fetched_at": str(entry.get("fetched_at") or ""),
        }
    return state


def reference_from_url(url: str) -> str:
    """从直链里推断用于 Obtainium 识别的文件名。"""
    candidate = url.split("?")[0].split("#")[0].rstrip("/").split("/")[-1]
    return candidate if APK_NAME_PATTERN.match(candidate) else ""


def parse_page_entries(page_text: str) -> dict:
    """从已有页面反推上次成功值，作为状态文件丢失时的兜底。

    同时兼容没有 data-* 属性的旧页面：那种情况下名称取自标签文本，
    fetched_at 留空（后续会显示为「同步时间」为生成时刻）。
    """
    entries = {}
    for chunk in page_text.split("<p")[1:]:
        match = PAGE_LINE_PATTERN.search("<p" + chunk)
        if not match:
            continue

        source_match = PAGE_SOURCE_PATTERN.search("<p" + chunk)
        if source_match:
            name = html.unescape(source_match.group("source"))
        else:
            name = html.unescape(match.group("name")).strip()

        fetched_match = PAGE_FETCHED_PATTERN.search("<p" + chunk)
        url = html.unescape(match.group("url"))
        reference = html.unescape(match.group("reference"))

        entries[name] = {
            "version": html.unescape(match.group("version")),
            "url": url,
            # 页面上写的识别标识就是抓取时的原值，优先保留；URL 只作兜底
            "reference": reference or reference_from_url(url),
            "reference_label": html.unescape(match.group("reference_label")) or "文件名参考",
            "fetched_at": html.unescape(fetched_match.group("fetched")) if fetched_match else "",
        }
    return entries


def fallback_state(state: Mapping, merged: Sequence[SourceResult]) -> dict:
    entries = {
        result.name: {
            "version": result.version,
            "url": result.url,
            "reference": result.reference,
            "reference_label": result.reference_label,
            "fetched_at": result.fetched_at,
        }
        for result in merged
        if result.ok
    }
    return {
        "_version": 1,
        "updated_at": now_iso(),
        "entries": entries,
    }


def merge_with_last_good(
    results: Sequence[SourceResult],
    state: Mapping,
) -> tuple[list[SourceResult], list[str]]:
    """抓取失败的来源沿用上次成功值；返回 (合并结果, 沿用者名单)。"""
    entries = state.get("entries") or {}
    merged: list[SourceResult] = []
    stale: list[str] = []

    for result in results:
        if result.ok:
            merged.append(replace(result, stale=False))
            continue

        entry = entries.get(result.name)
        if not isinstance(entry, Mapping) or not entry.get("url") or not entry.get("version"):
            continue

        stale.append(result.name)
        merged.append(
            SourceResult(
                name=result.name,
                ok=True,
                version=str(entry["version"]),
                url=str(entry["url"]),
                reference=str(entry.get("reference") or ""),
                reference_label=str(entry.get("reference_label") or "文件名参考"),
                fetched_at=str(entry.get("fetched_at") or ""),
                stale=True,
                message=f"{result.name} 本次抓取失败，沿用上次成功值 v{entry['version']}（{result.message}）",
            )
        )

    return merged, stale


def status_snapshot(results: Sequence[SourceResult], generated_at: str, published: bool) -> dict:
    """本轮抓取结果的机器可读快照，供 CI 生成摘要，不参与页面渲染。

    记录的是原始抓取结果（而不是合并后的页面内容），这样某次降级是「哪个
    来源真的坏了」一眼可见。
    """
    return {
        "_version": 1,
        "generated_at": generated_at,
        "published": published,
        "sources": [
            {
                "name": result.name,
                "ok": result.ok,
                "stale": result.stale,
                "version": result.version,
                "url": result.url,
                "message": result.message,
            }
            for result in results
        ],
    }


def run(args: argparse.Namespace) -> int:
    output = Path(args.output)
    state_path = Path(args.state)
    status_path = Path(args.status)

    results = fetch_all(jobs=normalize_jobs(args.jobs), deadline=normalize_budget(args.budget))
    for result in results:
        print(result.message)

    failures = [result for result in results if not result.ok]

    state = load_state(state_path)
    if not state["entries"]:
        try:
            state["entries"] = parse_page_entries(output.read_text(encoding="utf-8"))
        except OSError:
            pass

    merged, stale = merge_with_last_good(results, state)
    generated_at = now_iso()

    if not merged:
        write_page(
            status_path,
            json.dumps(
                status_snapshot(results, generated_at, published=False),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        print(
            f"本次有 {len(failures)} 个应用抓取失败，且没有可沿用的历史值，"
            f"保留上一次的 {output} 不生成新文件"
        )
        return EXIT_FAILED

    if not write_allowed(args):
        print(f"本地运行不生成 {output}（仅 GitHub Actions 自动更新或加 --write 参数时生成）")
        return EXIT_DEGRADED if (failures or stale) and not args.strict else EXIT_OK

    write_page(output, render_page(merged, generated_at))
    write_page(state_path, json.dumps(fallback_state(state, merged), ensure_ascii=False, indent=2) + "\n")
    write_page(
        status_path,
        json.dumps(
            status_snapshot(results, generated_at, published=True),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )

    if stale:
        print(f"已生成 {output}（{len(stale)} 个来源沿用上次成功值：{'、'.join(stale)}）")
    else:
        print(f"已生成 {output}")

    if stale or failures:
        on_page = {result.name for result in merged}
        missing = [result.name for result in results if not result.ok and result.name not in on_page]
        if missing:
            print(f"注意: {len(missing)} 个应用本次没有出现在页面上：{'、'.join(missing)}")
        return EXIT_FAILED if args.strict else EXIT_DEGRADED
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    """纯入口：只解析参数并执行，不产生缓存目录副作用（方便测试与复用）。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):  # 非文本流或不可重配置时忽略
        pass

    return run(parse_args(argv))


def run_cli(argv: Sequence[str] | None = None) -> int:
    """命令行入口：执行后清理本地生成的缓存目录。"""
    args = parse_args(argv)
    cleanup = not args.no_cleanup
    try:
        return run(args)
    finally:
        if cleanup:
            for name in cleanup_caches(Path(__file__).resolve().parent):
                print(f"已清理缓存文件夹: {name}")


if __name__ == "__main__":
    sys.exit(run_cli())
