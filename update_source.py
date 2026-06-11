import requests
import re
import random
import time
from urllib3.exceptions import InsecureRequestWarning

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

DEFAULT_TIMEOUT = 10
DEFAULT_RETRIES = 3
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}

html_links = ""


class MissingLocationError(RuntimeError):
    pass


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
    request_headers = request_headers or headers
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
        **headers,
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
        requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)
        res = get_with_retry(
            "CHelper",
            url,
            request_headers=ch_headers,
            timeout=15,
            max_retries=max_retries,
            verify=False,
        )
        print("CHelper HTTPS 校验失败，已仅对此站点关闭证书校验后重试成功")

    res.encoding = 'utf-8'
    return res.text

# ================= 1. 米游社 (强化版：带重试逻辑 + 伪装标头) =================
# 先定义一个专门给米游社用的加强版标头
mys_headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.miyoushe.com/",
    "Host": "bbs-api.miyoushe.com"
}

try:
    mys_api = "https://bbs-api.miyoushe.com/misc/wapi/getLatestPkgVer?channel=miyousheluodi"
    mys_res = get_with_retry("米游社", mys_api, request_headers=mys_headers)

    mys_data = mys_res.json()
    if mys_data.get('data'):
        mys_version = mys_data['data']['version']
        mys_url = f"https://download-bbs.miyoushe.com/app/mihoyobbs_{mys_version}_miyousheluodi.apk"
        html_links += f'    <p>米游社: <a href="{mys_url}">v{mys_version}</a> (文件名参考: mihoyobbs)</p>\n'
        print(f"米游社 抓取成功: v{mys_version}")
    else:
        print(f"米游社 响应异常: {mys_data}")

except requests.exceptions.RequestException as e:
    print(f"米游社 抓取报错: {e}")
except Exception as e:
    print(f"米游社 数据解析报错: {e}")


# ================= 2. 游戏客户端 (统一逻辑：处理302重定向 + 失败重试) =================
games = [
    {"name": "原神", "api": "https://ys-api.mihoyo.com/event/download_porter/link/ys_cn/official/android_default"},
    {"name": "云·原神", "api": "https://api-takumi.mihoyo.com/event/download_porter/link/clgm_cn/official/android_web"},
    {"name": "云·星穹铁道", "api": "https://act-api-takumi.mihoyo.com/event/download_porter/link/clgm_hkrpg-cn/official/android_default"},
    {"name": "云·绝区零", "api": "https://act-api-takumi.mihoyo.com/event/download_porter/link/clgm_nap-cn/official/android_cloudgame"},
    {"name": "植物大战僵尸2", "api": "https://pvz2download.ditwan.cn/download-service/baokai"},
    {"name": "TapTap", "api": "https://d.taptap.cn/latest/seo-bing"}
]

for game in games:
    try:
        res = get_with_retry(
            game["name"],
            game["api"],
            allow_redirects=False,
            require_location=True,
        )
        real_url = res.headers['Location']
        filename = real_url.split('/')[-1].split('?')[0]

        # 🌟 新增：对 PVZ2 使用专属正则提取版本号
        if game["name"] == "植物大战僵尸2":
            # 从 baokai_4.1.3_1817... 中精准抠出 4.1.3
            match = re.search(r'baokai_([\d\.]+)_', real_url)
            version = match.group(1) if match else "未知"
        else:
            # 其他游戏保留原来的通用正则
            match = re.search(r'_([a-zA-Z0-9\.\-]+)\.apk', real_url)
            version = match.group(1) if match else "未知"

        # 下面保留你原本的 TapTap 拦截逻辑
        if game["name"] == "TapTap":
            version = version.replace('-rel.', '-rel#')
            final_url = "https://d.taptap.cn/latest/seo-bing#taptap_fake.apk"
        else:
            final_url = real_url

        html_links += f'    <p>{game["name"]}: <a href="{final_url}">v{version}</a> (文件名参考: {filename})</p>\n'
        print(f"{game['name']} 抓取成功: v{version}")

    except (requests.exceptions.RequestException, MissingLocationError) as e:
        print(f"{game['name']} 抓取报错: {e}")
    except Exception as e:
        # 其他奇怪的代码错误，直接报错不重试
        print(f"{game['name']} 发生未知报错: {e}")

# ================= 4. 好游快爆 (单独处理特殊包名) =================
try:
    # 【注意】这里请填入你刚才抓到这个 302 响应时，真正的“请求 URL (Request URL)”
    kb_api = "https://d.3839.com/Cj" 
    
    # 同样禁止跳转，只抓 Location
    kb_res = get_with_retry(
        "好游快爆",
        kb_api,
        allow_redirects=False,
        require_location=True,
    )
    kb_real_url = kb_res.headers['Location']
    kb_filename = kb_real_url.split('/')[-1].split('?')[0]

    # 用正则精准提取 HYKB 后面的 6 位数字 (例如 158007)
    match = re.search(r'HYKB(\d{6})', kb_real_url)
    if match:
        raw_v = match.group(1)
        # 重新拼装成 1.5.8.007 的格式，让 Obtainium 抓得更准
        kb_version = f"{raw_v[0]}.{raw_v[1]}.{raw_v[2]}.{raw_v[3:]}"
    else:
        kb_version = "未知"

    html_links += f'    <p>好游快爆: <a href="{kb_real_url}">v{kb_version}</a> (文件名参考: {kb_filename})</p>\n'
    print(f"好游快爆 抓取成功: v{kb_version}")
except Exception as e:
    print(f"好游快爆 抓取报错: {e}")

# ================= 5. CHelper (网页抓版本号 + 静态下载直链) =================
try:
    # 目标网页：CHelper 的更新日志文档
    ch_web_url = "https://www.yanceymc.cn/chelper_doc/chelper-release-notes"
    ch_web_text = fetch_chelper_page(ch_web_url)
    
    # 规矩1：一刀切断！把网页按 </head> 劈开，我们只在后半截（正文）里找
    body_content = ch_web_text.split('</head>')[-1]
    
    # 规矩2：精准狙击标题！只寻找 <h1>, <h2> 或 <h3> 开头紧跟着的版本号
    # [^>]* 是为了兼容 VitePress 自动生成的 id 和 class，比如 <h2 id="v1-5-2">
    match = re.search(r'<h[1-3][^>]*>\s*[vV]?(\d+\.\d+\.\d+)', body_content)
    
    # 如果标题里没找到（作者可能没用标题），再用兜底方案在正文里盲抓一次
    if not match:
        match = re.search(r'[vV](\d+\.\d+\.\d+)', body_content)
        
    ch_version = match.group(1) if match else "未知"    
    # 缝合：用抓到的版本号，配上官方的静态下载直链
    ch_download_url = "https://www.yanceymc.cn/api/chelper/CHelper-latest.apk"
    html_links += f'    <p>CHelper: <a href="{ch_download_url}">v{ch_version}</a> (识别标识: chelper)</p>\n'
    print(f"CHelper 网页抓取成功: v{ch_version}")

except Exception as e:
    print(f"CHelper 网页抓取报错: {e}")

    
# ================= 组装并写入 HTML =================
html_content = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
    <h2>我的专属下载源</h2>
{html_links}
</body>
</html>
"""

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_content)
