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
- 单个应用抓取失败不会影响其他应用。
- 网络层共用一个 `requests.Session` 连接池，重试与退避交给 urllib3 的 `Retry` 策略；每轮抓取有总预算（默认 60 秒，`--budget` 可调），不会因为某个来源卡住而无限等待。
- 版本号必须匹配该来源的格式（例如 `7.0.0`）；解析出来的值格式不符时会明确报错，而不是把残缺版本号写进页面。
- 运行结束后会自动清理本地生成的缓存文件夹（如 `__pycache__`），用 `--no-cleanup` 可关闭。

## 版本策略：坏一个不再停更全部

页面采用「尽力而为」的发布方式：

1. 抓取成功的来源照常写入页面；
2. 抓取失败的来源，沿用 `sources-last-good.json` 里上一次成功的版本与链接，并在页面上标注 `[沿用 …]` 与一行同步时间；
3. 只有「一个来源都没成功、且没有任何可沿用的历史值」时才不生成页面，保留上一次的 `index.html`。

如果状态文件丢失，脚本会退回到从现有 `index.html` 反推上次成功值（兼容没有 `data-*` 属性的旧页面），所以兜底能力不会因为一次误删而失效。

退出码：

| 退出码 | 含义 | GitHub Actions 表现 |
| --- | --- | --- |
| `0` | 全部来源抓取成功 | 绿色 |
| `1` | 有来源降级，页面已用上次成功值补齐并推送 | 红色（页面已更新，红灯只表示上游接口需要处理） |
| `2` | 无法生成页面（无可用来源且无历史值） | 红色 |

加 `--strict` 后，任何来源失败都返回 `2`（旧的「一坏全停」语义）。

## 自动更新

仓库内置 GitHub Actions 工作流（`.github/workflows/update.yml`）：

- 每天北京时间 06:30 自动运行一次；
- 检测到版本更新后自动提交并推送（提交信息：`Auto-update: 同步应用最新版本`），同时提交 `index.html` 与 `sources-last-good.json`；
- 也可以在 Actions 页面手动触发（`workflow_dispatch`）；
- 定时任务与手动触发会排队执行（`concurrency`），不会互相抢着推送；
- 运行结束后写入运行摘要，列出本次抓取失败的来源与原因；
- 有来源抓取失败时任务变红提醒，但页面已经推送完成。

另外还有 `.github/workflows/tests.yml`，每次推送或提交 PR 时运行单元测试。

## 本地运行

需要 Python 3.10+：

```bash
pip install -r requirements.txt
python update_source.py
```

本地运行只输出各应用的抓取结果，不会生成 `index.html`；`index.html` 由 GitHub Actions 自动更新时生成并提交到 GitHub。

可选参数：

```bash
python update_source.py --write                      # 允许写入文件
python update_source.py --output build/index.html    # 指定输出路径，默认 index.html
python update_source.py --state sources-last-good.json  # 上次成功结果记录，默认同名文件
python update_source.py --jobs 4                     # 调整并发线程数，默认 8
python update_source.py --budget 30                  # 整轮抓取总预算（秒），默认 60
python update_source.py --strict                     # 任一来源失败即返回 2
python update_source.py --no-cleanup                 # 不清理缓存目录，便于反复运行
```

退出码见上文「版本策略」一节，可配合 `echo $?` 之类的检查使用。

## 测试

单元测试不访问网络，覆盖版本号解析与校验、降级合并、状态文件读写、页面格式、重试与预算、写入策略和退出码：

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

## 在 Obtainium 中使用

1. 打开 Obtainium，进入“添加应用”；
2. 选择自定义源（Custom），填入本仓库 `index.html` 的在线地址（GitHub Pages 或 raw 链接均可）；
3. 页面中每行末尾的“文件名参考 / 识别标识”可用来区分不同应用。标注了 `[沿用 …]` 的行表示该来源本次抓取失败，页面上是上一次成功的链接，仍然可以正常安装。

> 若使用 GitHub Pages 地址，请先在仓库 Settings → Pages 中启用部署（分支 `main` / 根目录）。

> 页面每行的结构是 Obtainium 解析的依据：`应用名: <a href="直链">v版本</a> (文件名参考: 标识)`。脚本只在 `<p>` 标签上附加 `data-source` / `data-fetched-at` 属性，并在有降级时于页面顶部加一行同步说明，其余部分保持不变。

## 免责声明

本项目仅供个人学习与自用。所有下载链接均来自各应用官方渠道，版权归原厂商所有。
