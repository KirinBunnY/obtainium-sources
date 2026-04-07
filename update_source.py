import requests
import json

# 1. 你抓到的真实 API 链接
API_URL = "https://api-takumi.mihoyo.com/xxxx/getLatestPkgVer?channel=miyousheluodi" # 注意替换为你抓到的完整链接

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

try:
    response = requests.get(API_URL, headers=headers)
    response.raise_for_status()
    data = response.json()

    # 2. 从 JSON 中提取版本号
    version = data['data']['version']

    # 3. 动态拼接官方直达下载链接！
    download_url = f"https://download-bbs.miyoushe.com/app/mihoyobbs_{version}_miyousheluodi.apk"

    # 4. 生成极简静态 HTML
    html_content = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>
    <a href="{download_url}">v{version}</a>
</body>
</html>
"""

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_content)
        
    print(f"更新成功: v{version}, 链接: {download_url}")

except Exception as e:
    print(f"抓取或解析失败: {e}")
