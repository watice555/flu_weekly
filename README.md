# 流感周报与南北方 ILI% 趋势

从中国国家流感中心的周报 PDF 提取南、北方省份哨点医院 ILI%，建立本地 SQLite 数据库，并生成按流感年度对比的静态图表。项目位于 `~/Projects_local/flu_weekly`，使用独立 Git 仓库与虚拟环境。

当前已归集 138 期有效报告、276 条南北方观测，连续覆盖 **2024 年第 1 周至 2026 年第 34 周**。后续随报告导入扩展。Git 默认分支为 `main`，尚未创建远程仓库或部署 GitHub Pages。

## 查看与使用

本机已有 Python 3.12 虚拟环境。新环境安装：

```bash
cd ~/Projects_local/flu_weekly
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

从本地 PDF 构建数据库与网页数据，**不访问网络、不写入坚果云**：

```bash
.venv/bin/python -m flu_data.database
```

增量构建根据 PDF 的 SHA-256 和提取器版本跳过已提取文件；同一期的不同 PDF 内容保留为不同版本。任一文件无法确认周次、日期或南北方数值时，整批导入回滚，已生成网页数据保持不变。

本地预览（只能访问 `site/`，不会暴露 PDF、SQLite 或项目私有文件）：

```bash
.venv/bin/python -m http.server 8765 --bind 127.0.0.1 --directory site
```

打开 `http://127.0.0.1:8765/`。页面提供：

- 南北方两张图，默认显示最近四个已收录的流感年度。
- 第 14 周至次年第 13 周的周次排列；如有第 53 周单独保留，不合并或丢弃。
- 年度开关、悬停/键盘聚焦查看数值、点击数据点查看日期和官方来源。
- 按报告年份查看数值表、导出 CSV。
- 缺失周断开曲线；不完整的历史年度明确标记。本次 `2023-2024` 年度只有 2024 年第 1—13 周，没有虚构 2023 年的数据。

## 数据口径与来源追溯

ILI% 指**流感样病例占哨点医院门急诊病例总数的百分比**，不是流感确诊率或人群感染率。

本项目使用每期报告正文的**当周发布值**，不从图像估读、不插值，也不直接用后续报告的“往年同期”或“前一周”引用覆盖。部分后续引用与当期数值不同，可能涉及修订或历史周次对齐；这里不猜测原因。页面的周变化也按相邻两期的当周发布值计算，因此可能与最新 PDF 中的周变化表述不同。

抽取时校验文件名周次、正文周次、正文中的七天日期区间、南北方 ILI% 的范围，以及摘要与正文的重复数值是否一致。数据库保留全部匹配页码和原文证据，百分比用整数十分位存储，避免浮点数改变报告的一位小数精度。

SQLite 位于 `data/flu.sqlite3`，主要表/视图：

| 表或视图 | 内容 |
| --- | --- |
| `reports` | 报告年/周、当前采用的 PDF 校验值、官方详情与 PDF 链接 |
| `report_versions` | 本地 PDF 的各版本、期号、日期、提取器版本及导入时间 |
| `observations` | 每个 PDF 版本的南北方 ILI% 十分位、匹配页码、原文证据 |
| `report_flags` | 有原文依据的日期或期号异常说明 |
| `current_observations` | 当前采用版本的可查询数值与来源，每期两条 |

例如：

```sql
SELECT year, week, period_start, period_end, region, ili_percent,
       report_number, detail_url, pages_json
FROM current_observations
ORDER BY year, week, region;
```

官方详情和下载链接保存在 `data/sources.json`。以下命令只读取官网列表和详情页，不下载 PDF；支持中断后继续，`--refresh` 可重新检查已有链接：

```bash
.venv/bin/python -m flu_data.sources --min-year 2024
```

网页 JSON 仅含数值、日期、期号、页码、校验值和官方链接，不含 PDF、数据库原件或本机路径。官方链接指向同一期报告的官网资源，不承诺其内容永远等于本机已归档版本；官网可能更新文件。

## 历史归档校验

从 `~/Nutstore Files/Nutstore/新冠等/周报` 复制了 115 份历史 PDF，22 份原有文件校验一致，坚果云原件均未修改。核验后：

- `25-02.pdf` 原来误存了第 1 周内容，已从官网取得第 2 周报告；错存原件保留在 `flu_reports/originals/`。
- 缺失的 `26-26.pdf` 已从官网补齐。
- 2025 年第 52 周的正文一处把 12 月 22 日写成 12 月 12 日；采用摘要中与周次相符的 12 月 22—28 日，原文及异常说明保留。
- 2026 年第 11 周封面有旧期号文本残留；采用当周正文一致的第 900 期，记录封面异常。

补正下载的 URL、新旧 SHA-256 及原因记录在 `data/archive_corrections.json`。有效报告按 `YY-WW.pdf` 放在 `flu_reports/` 根目录；`originals/` 不参加数值导入。

## 核对缺周与后续引用差异

在本地归档和数据库准备好后运行：

```bash
.venv/bin/python -m flu_data.audit_revisions
```

此命令离线、只读访问主数据库，校验 PDF SHA-256 后，单独抽取“前一周”和“往年同期”的文字引用。摘要与正文合并计数；前一周处理跨年周次，历史引用严格保留原文年份与同一周号。未找到当期原报告的引用不进入差异率，重复段落数值冲突会单列并排除，年份标签和比较措辞异常保留记录。不会修改当期数据或网页曲线。

结果保存在被 Git 忽略的 `data/revision_audit/`：

- `index.html`：可直接打开的核对清单，按南北方、引用类型、目标年份、差异幅度及周次筛选；包含官方 PDF 链接、文件页码与引用原文。只包含文本和链接，不嵌入 PDF。
- `summary.md`：统计口径、缺周范围、分组统计、异常说明与完整差异表。
- `audit.json`：全部引用（含未配对项）、SHA-256、原值、引用值、原文证据和每个目标周的引用时间序列。`delta_tenths` 是“引用值减当期值”的十分之一百分点；`status=matched` 表示存在原值可配对，并不表示数值一致。

2026-08-31 对 138 期归档的核对：2024-W01 至 2026-W34 无内部缺周，首个显示年度缺 2023-W14 至 W52 共 39 周。提取 1,104 条引用，512 条可配对，其中 241 条不同，涉及 139 个“地区×目标周”。前一周为 81/274 条不同，最大 0.9 个百分点；往年同期为 160/238 条不同，最大 1.4 个百分点。该结果是固定归档版本的文字差异，不代表官方确认的修订次数。另 592 条引用缺少早年当期报告，无法验证原值。

本地预览核对报告时，仅服务结果目录：

```bash
.venv/bin/python -m http.server 8766 --bind 127.0.0.1 --directory data/revision_audit
```

本次审计不批量补抓更早报告、不修改坚果云、定时任务、主数据库或原有图表，也不将审计结果自动加入公开站点。

## 现有下载与本机任务

`main.py` 仍是任务入口，会实际访问官网、下载最新一期，并同步到两个位置：

- 项目内 `flu_reports/`（根据脚本位置解析，不再依赖当前工作目录）。
- `~/Nutstore Files/Nutstore/新冠等/周报`。

它同时记录官方来源，更新数据库和网页 JSON。没有新 PDF 时也会离线重建索引，但不重复下载或复制 PDF。下载中断或收到非 PDF 响应不会覆盖已有文件；数据库构建失败不会推进 `state.json` 的成功状态。

需要真实更新时运行：

```bash
.venv/bin/python main.py
```

既有 LaunchAgent `~/Library/LaunchAgents/com.wuth.flu-weekly.plist` 仍通过 `~/Cron/Scripts/flu-weekly.sh` 在每周六 **07:10（本机 Asia/Shanghai）**运行。日志仍在 `~/Cron/Logs/flu-weekly.out.log` 和 `flu-weekly.err.log`。本次没有改变计划或手动触发任务。复制/克隆项目不会自动安装任务。

## Git 与网页发布边界

源码、测试和文档进入 Git；`flu_reports/`、`data/`、`site/data/`、PDF、SQLite、缓存、私有配置及临时文件均忽略。新克隆没有数据，需要先把本地归档复制到 `flu_reports/` 再构建。

生成可供 GitHub Pages 使用的文件包：

```bash
.venv/bin/python -m flu_data.stage_site
```

命令创建新的临时发布目录，仅允许 `index.html`、`styles.css`、`app.mjs`、`chart.mjs`、`.nojekyll` 和 `data/ili.json`，**不会上传或部署**。只发布这个文件包，不能上传整个仓库；PDF 仅通过官网链接访问。SQLite 留在本地。

当前未与美团项目合并，未做相关性计算。保留了自然日期、指标标识和南北方维度，便于未来明确城市映射、数据版本与时间聚合方式后共同展示。

## 离线验证

```bash
.venv/bin/python -m unittest discover -s tests -v
node --test tests/chart.test.mjs
.venv/bin/python -m compileall -q flu_data main.py
node --check site/app.mjs
node --check site/chart.mjs
git diff --check
```

单元测试不需要真实 PDF，不访问官网、不写坚果云、不触发任务。完整归档构建使用本地真实 PDF 验证。
