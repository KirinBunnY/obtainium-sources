# obtainium-sources

自动抓取常用安卓应用的最新版本信息，生成 [Obtainium](https://obtainium.imranr.com/) 自定义源页面（`index.html`），方便批量安装与更新应用。

## 支持的应用

| 应用 | 版本 / 链接来源 |
| --- | --- |
| 米游社 | 米游社官方版本 API |
| 原神 | 米哈游官方下载接口 |
| 云·原神 | 米哈游官方下载接口 |
| 云·星穹铁道 | 米哈游官方下载接口 |
| 云·绝区零 | 米哈游官方下载接口 |
| 植物大战僵尸2 | 官方下载服务 |
| TapTap | 官方最新版下载链接 |
| 好游快爆 | 官网特殊包名解析 |
| CHelper | 官网 CHANGELOG.md |

## 工作原理

- `update_source.py` 依次抓取各应用的官方下载链接和版本号，生成 `index.html`。
- 每次运行都会实时查询官方渠道，确保拿到最新版本。
- 单个应用抓取失败不会影响其他应用，失败项只输出错误信息。

## 自动更新

仓库内置 GitHub Actions 工作流（`.github/workflows/update.yml`）：

- 每天北京时间 06:30 自动运行一次；
- 检测到版本更新后自动提交并推送（提交信息：`Auto-update: 同步应用最新版本`）；
- 也可以在 Actions 页面手动触发（`workflow_dispatch`）。

## 本地运行

需要 Python 3.10+ 和 `requests`：

```bash
pip install requests
python update_source.py
```

运行后会在仓库根目录重新生成 `index.html`。

## 在 Obtainium 中使用

1. 打开 Obtainium，进入“添加应用”；
2. 选择自定义源（Custom），填入本仓库 `index.html` 的在线地址（GitHub Pages 或 raw 链接均可）；
3. 页面中每行末尾的“文件名参考 / 识别标识”可用来区分不同应用。

> 若使用 GitHub Pages 地址，请先在仓库 Settings → Pages 中启用部署（分支 `main` / 根目录）。

## 免责声明

本项目仅供个人学习与自用。所有下载链接均来自各应用官方渠道，版权归原厂商所有。