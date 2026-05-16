import requests
import re
import json
import time
import os
from urllib3.exceptions import InsecureRequestWarning

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

html_links = ""


def fetch_chelper_page(url, max_retries=3):
    """CHelper 官网证书偶尔异常，只在这个站点失败后降级重试。"""
    ch_headers = {
        **headers,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Connection": "close",
    }
    last_error = None

    for verify_ssl in (True, False):
        if not verify_ssl:
            requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

        for attempt in range(max_retries):
            try:
                res = requests.get(url, headers=ch_headers, timeout=15, verify=verify_ssl)
                res.raise_for_status()
                if not verify_ssl:
                    print("CHelper HTTPS 校验失败，已仅对此站点关闭证书校验后重试成功")
                res.encoding = 'utf-8'
                return res.text
            except requests.exceptions.SSLError as e:
                last_error = e
                if verify_ssl:
                    break
            except requests.exceptions.RequestException as e:
                last_error = e

            if attempt < max_retries - 1:
                time.sleep(2)

    raise last_error if last_error else RuntimeError("CHelper 页面请求失败")

# ================= 1. 米游社 (强化版：带重试逻辑 + 伪装标头) =================
# 先定义一个专门给米游社用的加强版标头
mys_headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.miyoushe.com/",
    "Host": "bbs-api.miyoushe.com"
}

for attempt in range(3): # 给它 3 次机会
    try:
        mys_api = "https://bbs-api.miyoushe.com/misc/wapi/getLatestPkgVer?channel=miyousheluodi"
        # 使用加强版标头，并设置 10 秒超时
        mys_res = requests.get(mys_api, headers=mys_headers, timeout=10)
        mys_res.raise_for_status()
        
        mys_data = mys_res.json()
        if mys_data.get('data'):
            mys_version = mys_data['data']['version']
            mys_url = f"https://download-bbs.miyoushe.com/app/mihoyobbs_{mys_version}_miyousheluodi.apk"
            html_links += f'    <p>米游社: <a href="{mys_url}">v{mys_version}</a> (文件名参考: mihoyobbs)</p>\n'
            print(f"米游社 抓取成功: v{mys_version}")
            break # 成功了就跳出重试循环
        else:
            print(f"米游社 响应异常: {mys_data}")
            break
            
    except Exception as e:
        if attempt < 2:
            print(f"米游社 抓取碰到玄学报错，2 秒后进行第 {attempt + 1} 次重试... ({e})")
            time.sleep(2)
        else:
            print(f"米游社 彻底抓取失败 (已达最大重试次数): {e}")


# ================= 2. 游戏客户端 (统一逻辑：处理302重定向 + 失败重试) =================
games = [
    {"name": "原神", "api": "https://ys-api.mihoyo.com/event/download_porter/link/ys_cn/official/android_default"},
    {"name": "云·原神", "api": "https://api-takumi.mihoyo.com/event/download_porter/link/clgm_cn/official/android_web"},
    {"name": "云·星穹铁道", "api": "https://act-api-takumi.mihoyo.com/event/download_porter/link/clgm_hkrpg-cn/official/android_default"},
    {"name": "云·绝区零", "api": "https://act-api-takumi.mihoyo.com/event/download_porter/link/clgm_nap-cn/official/android_cloudgame"},
    {"name": "TapTap", "api": "https://d.taptap.cn/latest/seo-bing"}
]

for game in games:
    max_retries = 3  # 最大重试次数
    for attempt in range(max_retries):
        try:
            # 加上 timeout=10，防止被服务器一直挂起卡死
            res = requests.get(game["api"], headers=headers, allow_redirects=False, timeout=10)
            real_url = res.headers.get('Location', '')
            
            if real_url:
                match = re.search(r'_([a-zA-Z0-9\.\-]+)\.apk', real_url)
                version = match.group(1) if match else "未知"
                filename = real_url.split('/')[-1].split('?')[0]
                
                # 针对 TapTap 的拦截逻辑
                                # 针对 TapTap 的拦截逻辑
                if game["name"] == "TapTap":
                    # 👇 新增这行代码：把 '-rel.' 替换成 '-rel#'
                    version = version.replace('-rel.', '-rel#')
                    
                    final_url = "https://d.taptap.cn/latest/seo-bing#taptap_fake.apk"
                else:
                    final_url = real_url

                    
                html_links += f'    <p>{game["name"]}: <a href="{final_url}">v{version}</a> (文件名参考: {filename})</p>\n'
                print(f"{game['name']} 抓取成功: v{version}")
            else:
                print(f"{game['name']} 抓取失败: 未找到跳转链接")
            
            # 走到这里说明成功了，用 break 强行跳出重试循环，去抓下一个游戏！
            break 
            
        except requests.exceptions.RequestException as e:
            # 专门捕获网络异常（包括断连、超时等）
            if attempt < max_retries - 1:
                print(f"{game['name']} 网络连接失败，休息 2 秒后进行第 {attempt + 1} 次重试...")
                time.sleep(2)  # 稍微停顿一下，防止被彻底拉黑
            else:
                print(f"{game['name']} 抓取报错 (已达最大重试次数): {e}")
        except Exception as e:
            # 其他奇怪的代码错误，直接报错不重试
            print(f"{game['name']} 发生未知报错: {e}")
            break

# ================= 4. 好游快爆 (单独处理特殊包名) =================
try:
    # 【注意】这里请填入你刚才抓到这个 302 响应时，真正的“请求 URL (Request URL)”
    kb_api = "https://d.3839.com/Cj" 
    
    # 同样禁止跳转，只抓 Location
    kb_res = requests.get(kb_api, headers=headers, allow_redirects=False)
    kb_real_url = kb_res.headers.get('Location', '')
    
    if kb_real_url:
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
    else:
        print("好游快爆 抓取失败: 未找到跳转链接")
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
