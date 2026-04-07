import requests
import re
import json

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

try:
    # ================= 1. 抓取米游社 =================
    mys_api = "https://api-takumi.mihoyo.com/xxxx/getLatestPkgVer?channel=miyousheluodi" # 填入你之前的API
    mys_res = requests.get(mys_api, headers=headers)
    mys_res.raise_for_status()
    mys_version = mys_res.json()['data']['version']
    mys_url = f"https://download-bbs.miyoushe.com/app/mihoyobbs_{mys_version}_miyousheluodi.apk"

    # ================= 2. 抓取原神 =================
    ys_api = "https://ys-api.mihoyo.com/event/download_porter/link/ys_cn/official/android_default"
    # allow_redirects=False 是精髓：只拿真实链接，不下载这头 3GB 的性能巨兽
    ys_res = requests.get(ys_api, headers=headers, allow_redirects=False)
    ys_real_url = ys_res.headers.get('Location', '')
    
    # 用正则从链接里提取版本号 (比如从 Yuanshen_4.5.0.apk 提取 4.5.0)
    ys_match = re.search(r'Yuanshen_(.+?)\.apk', ys_real_url, re.IGNORECASE)
    ys_version = ys_match.group(1) if ys_match else "未知"

    # ================= 3. 生成聚合静态页 =================
    html_content = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
    <h1>我的专属下载源</h1>
    <div id="miyoushe">
        <a href="{mys_url}">v{mys_version}</a>
    </div>
    <div id="yuanshen">
        <a href="{ys_real_url}">v{ys_version}</a>
    </div>
</body>
</html>
"""

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_content)
        
    print(f"更新成功! 米游社: v{mys_version} | 原神: v{ys_version}")

except Exception as e:
    print(f"抓取失败: {e}")
