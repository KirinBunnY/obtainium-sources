import requests
import re
import json
import time
import base64
import re
import cloudscraper
import os
from curl_cffi import requests as cffi_requests

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

html_links = ""

# ================= 1. 米游社 (单独逻辑：解析JSON) =================
try:
    mys_api = "https://bbs-api.miyoushe.com/misc/wapi/getLatestPkgVer?channel=miyousheluodi"
    mys_res = requests.get(mys_api, headers=headers)
    mys_res.raise_for_status()
    mys_version = mys_res.json()['data']['version']
    mys_url = f"https://download-bbs.miyoushe.com/app/mihoyobbs_{mys_version}_miyousheluodi.apk"
    html_links += f'    <p>米游社: <a href="{mys_url}">v{mys_version}</a> (文件名参考: mihoyobbs)</p>\n'
    print(f"米游社 抓取成功: v{mys_version}")
except Exception as e:
    print(f"米游社 抓取报错: {e}")

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
    ch_web_res = requests.get(ch_web_url, headers=headers)
    ch_web_res.encoding = 'utf-8'
    
    # 规矩1：一刀切断！把网页按 </head> 劈开，我们只在后半截（正文）里找
    body_content = ch_web_res.text.split('</head>')[-1]
    
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


# ================= 6. TickTick (Apkmody 终极破盾版 + 本地代理) =================
# ================= 8. TickTick (自动切换环境版) =================
try:
    mody_url = "https://apkmody.com/apps/ticktick/download/0"
    
    # 🌟 极客魔法：判断当前是否在 GitHub Actions 云端环境
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        print("检测到云端环境，切换为直连模式...")
        my_proxies = None  # 云端直连，不需要代理
    else:
        print("检测到本地环境，挂载本地代理...")
        my_proxies = {
            "http": "http://127.0.0.1:7897",
            "https": "http://127.0.0.1:7897"
        }
    
    # 带着智能代理配置发起冲锋
    mody_res = cffi_requests.get(
        mody_url, 
        impersonate="chrome", 
        proxies=my_proxies, # 如果是 None，curl_cffi 会自动忽略
        timeout=15
    )
    mody_res.encoding = 'utf-8'
    
    # --- 下面的切割和正则逻辑完全保持不变 ---
    hash_match = re.search(r'data-href=[\'\"]([A-Za-z0-9+/=]+)[\'\"]', mody_res.text)
    
    if hash_match:
        encoded_hash = hash_match.group(1)
        decoded_url = base64.b64decode(encoded_hash).decode('utf-8')
        
        if "/v2/" in decoded_url:
            original_domain = decoded_url.split('/')[2]
            final_mody_url = decoded_url.replace(original_domain, 's1.1phut.io')
        else:
            final_mody_url = decoded_url

        btn_index = hash_match.start()
        start_idx = max(0, btn_index - 800)
        end_idx = min(len(mody_res.text), btn_index + 400)
        local_html = mody_res.text[start_idx:end_idx]
        
        ver_match = re.search(r'TickTick_v([\d\.]+)', local_html, re.IGNORECASE)
        mody_version = ver_match.group(1) if ver_match else "未知"
        mody_version = mody_version.rstrip('.')
        
        html_links += f'    <p>TickTick: <a href="{final_mody_url}">v{mody_version}</a> (识别标识: ticktick)</p>\n'
        print(f"TickTick 抓取成功: v{mody_version}")
        
    else:
        print("TickTick 抓取失败: 源码中未找到 data-href")
        
except Exception as e:
    print(f"TickTick 抓取报错: {e}")
    
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
