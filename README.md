# 流感监测周报自动下载脚本

这个目录里的 [main.py](main.py) 用于自动抓取中国疾控中心流感监测周报的最新 PDF，并保存到本地。

项目位于 `~/Projects_local/flu_weekly`，不依赖同级的日报或美团指数项目。

## 功能

- 访问周报列表页：`https://ivdc.chinacdc.cn/cnic/zyzx/lgzb/`
- 自动定位最新一期周报详情页
- 从详情页提取 PDF 链接并下载
- 下载文件按 `YY-XX.pdf` 命名，例如 `26-10.pdf`
- 通过 `flu_reports/state.json` 记录上次下载的 PDF 链接，避免重复下载
- 下载成功后同时保存到两个目录：
  - 当前项目目录下的 `flu_reports`
  - `~/Nutstore Files/Nutstore/新冠等/周报`

## 环境要求

- Python 3.9+
- 依赖包：`requests`、`beautifulsoup4`

创建独立虚拟环境并安装依赖：

```bash
cd ~/Projects_local/flu_weekly
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## 运行方式

脚本会访问疾控网站并写入本地及坚果云目录。需要实际下载时，在项目根目录执行（`flu_reports` 按当前工作目录解析）：

```bash
cd ~/Projects_local/flu_weekly
.venv/bin/python main.py
```

成功时会输出类似：

```text
已下载: flu_reports/26-10.pdf
已同步: /Users/<本机用户>/Nutstore Files/Nutstore/新冠等/周报/26-10.pdf
PDF: https://ivdc.chinacdc.cn/...
详情: https://ivdc.chinacdc.cn/...
```

如果没有更新，会输出：

```text
没有新周报。
```

## 目录结构

```text
.
├─ main.py
├─ README.md
├─ requirements.txt
├─ IMPLEMENTATION_LOG.md
├─ .gitignore
├─ .venv/                # 本机依赖，不提交
└─ flu_reports/
   ├─ *.pdf
   └─ state.json
```

## 本机定时任务

- 现有 LaunchAgent：`~/Library/LaunchAgents/com.wuth.flu-weekly.plist`。
- 执行入口：`~/Cron/Scripts/flu-weekly.sh`，工作目录和 Python 路径已指向本项目。
- 计划：本机时区下每周六 `07:10`；当前本机时区为 `Asia/Shanghai`。
- 日志仍在 `~/Cron/Logs/flu-weekly.out.log` 和 `flu-weekly.err.log`。
- 这些配置保留在项目外；移动项目时须同步检查路径。复制项目不会自动安装或启动定时任务。

## Git 与后续站点

按用户确认已初始化本地 Git 仓库，默认分支为 `main`；尚未建立 GitHub 远程仓库或发布站点。`.gitignore` 已排除周报 PDF/状态目录、虚拟环境、缓存和私有配置，供后续版本管理使用。

当前脚本只下载和归档 PDF，尚无网页或周报指标解析。后续可以将周报目录与美团指数接入同一个站点入口，保留各自的数据源、更新计划和失败处理；是否合并仓库另行决定。坚果云同步属于本机功能，不应直接搬入云端工作流。

## 说明

- 中文路径可以正常使用，`~/Nutstore Files/Nutstore/新冠等/周报` 会在首次同步时自动创建
- 如果坚果云目录暂时不可写，脚本会在同步阶段报错
- 如果网站页面结构变化，可能需要调整 [main.py](main.py) 里的解析逻辑
