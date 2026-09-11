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

- `update_source.py` 并发抓取各应用的官方下载链接和版本号，再按固定顺序生成 `index.html`。
- 每次运行都会实时查询官方渠道，确保拿到最新版本。
- 单个应用抓取失败不会影响其他应用，失败项只输出错误信息。
- 若本次有应用抓取失败，则不会生成新的 `index.html`，而是保留上一次成功的结果。
- 有抓取失败时脚本返回非 0 退出码，因此 GitHub Actions 会变红提醒，而不是静默停在旧版本。
- 运行结束后会自动清理本地生成的缓存文件夹（如 `__pycache__`）。

## 自动更新

仓库内置 GitHub Actions 工作流（`.github/workflows/update.yml`）：

- 每天北京时间 06:30 自动运行一次；
- 检测到版本更新后自动提交并推送（提交信息：`Auto-update: 同步应用最新版本`）；
- 也可以在 Actions 页面手动触发（`workflow_dispatch`）；
- 定时任务与手动触发会排队执行（`concurrency`），不会互相抢着推送；
- 有来源抓取失败时任务失败并保留旧页面，由 GitHub 的失败通知提醒你处理。

另外还有 `.github/workflows/tests.yml`，每次推送或提交 PR 时运行单元测试。

## 本地运行

需要 Python 3.10+：

```bash
pip install -r requirements.txt
python update_source.py
```

本地运行只输出各应用的抓取结果，不会在本地生成 `index.html`；`index.html` 由 GitHub Actions 自动更新时生成并提交到 GitHub。

可选参数：

```bash
python update_source.py --write                      # 允许写入文件
python update_source.py --output build/index.html    # 指定输出路径，默认 index.html
python update_source.py --jobs 4                     # 调整并发线程数，默认 8
```

有来源抓取失败时退出码为 1（本地同样如此），可以配合 `echo $?` 之类的检查使用。

## 测试

单元测试不访问网络，覆盖版本号解析、页面格式和写入策略：

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

## 在 Obtainium 中使用

1. 打开 Obtainium，进入“添加应用”；
2. 选择自定义源（Custom），填入本仓库 `index.html` 的在线地址（GitHub Pages 或 raw 链接均可）；
3. 页面中每行末尾的“文件名参考 / 识别标识”可用来区分不同应用。

> 若使用 GitHub Pages 地址，请先在仓库 Settings → Pages 中启用部署（分支 `main` / 根目录）。

## 免责声明

本项目仅供个人学习与自用。所有下载链接均来自各应用官方渠道，版权归原厂商所有。
