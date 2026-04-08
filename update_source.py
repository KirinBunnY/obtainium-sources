import requests
import re
import json

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

# ================= 2. 游戏客户端 (统一逻辑：处理302重定向) =================
games = [
    {"name": "原神", "api": "https://ys-api.mihoyo.com/event/download_porter/link/ys_cn/official/android_default"},
    {"name": "云·原神", "api": "https://api-takumi.mihoyo.com/event/download_porter/link/clgm_cn/official/android_web"},
    {"name": "云·星穹铁道", "api": "https://act-api-takumi.mihoyo.com/event/download_porter/link/clgm_hkrpg-cn/official/android_default"},
    {"name": "云·绝区零", "api": "https://act-api-takumi.mihoyo.com/event/download_porter/link/clgm_nap-cn/official/android_cloudgame"}
    {"name": "好游快爆", "api": "https://d.3839.com/Cj"}
]

for game in games:
    try:
        # allow_redirects=False：绝不下载文件，只偷看跳转地址！
        res = requests.get(game["api"], headers=headers, allow_redirects=False)
        real_url = res.headers.get('Location', '')
        
        if real_url:
            # 用正则从链接中提取版本号 (寻找夹在下划线或点之间的数字)
            match = re.search(r'_([\d\.]+)[_\.]', real_url)
            version = match.group(1) if match else "未知"
            
            # 提取安装包的文件名，方便你在网页上直接看
            filename = real_url.split('/')[-1].split('?')[0]
            
            html_links += f'    <p>{game["name"]}: <a href="{real_url}">v{version}</a> (文件名参考: {filename})</p>\n'
            print(f"{game['name']} 抓取成功: v{version}")
        else:
            print(f"{game['name']} 抓取失败: 未找到跳转链接")
    except Exception as e:
        print(f"{game['name']} 抓取报错: {e}")

# ================= 3. 组装并写入 HTML =================
html_content = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
    <h2>我的专属米哈游下载源</h2>
{html_links}
</body>
</html>
"""

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html_content)
