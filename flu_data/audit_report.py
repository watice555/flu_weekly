"""Write local, self-contained revision-audit reports; no network or PDF copies."""

import html
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .audit_revisions import KINDS, REGIONS


def percent(value):
    return "—" if value is None else f"{value * 100:.1f}%"


def number(value, digits=2):
    return "—" if value is None else f"{value:.{digits}f}"


def summary_rows(result):
    groups = result["summary"]
    return [(KINDS[kind], groups["by_kind"][kind]) for kind in KINDS] + [("合计", groups["all"])]


def stats_cells(label, stats):
    return [label, str(stats["paired"]), str(stats["different"]), percent(stats["different_rate"]),
            number(stats["mean_absolute_pp_changed"]), number(stats["median_absolute_pp_changed"], 1),
            number(stats["maximum_absolute_pp"], 1)]


HEADERS = ["比较口径", "可配对引用", "有差异", "差异占比", "差异项平均绝对差", "差异项中位绝对差", "最大绝对差"]


def md_table(headers, rows):
    return "\n".join(["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |",
                      *("| " + " | ".join(row) + " |" for row in rows)])


def source(row, original=False, markdown=False):
    prefix = "original" if original else "citing"
    pages = row["original_pages"] if original else row["pages"]
    url = row.get(prefix + "_pdf_url") or row.get(prefix + "_url")
    label = "PDF 第 " + "、".join(map(str, pages)) + " 页"
    if not url:
        return label
    url = url + f"#page={pages[0]}" if row.get(prefix + "_pdf_url") else url
    return f"[{label}]({url})" if markdown else f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">{label}</a>'


def facts(result):
    coverage, stats = result["coverage"], result["summary"]["all"]
    missing = coverage["missing_in_first_displayed_season"]
    leading = f"{missing[0]} 至 {missing[-1]}，共 {len(missing)} 周" if missing else "无"
    internal = "、".join(coverage["missing_internal"]) or "无"
    return [
        f"归档覆盖 {coverage['first_report']} 至 {coverage['last_report']}，共 {coverage['reports']} 期、{coverage['original_observations']} 条南北方当周观测。范围内部缺周：{internal}。",
        f"首个显示的流感年度缺少前段：{leading}。早于归档起点的年份未完整收录，不能视为完整历史；最新归档期之后不计为本次缺失。",
        f"提取 {stats['references']} 条后续引用，其中 {stats['paired']} 条有同一地区、同一目标周的当期原值可配对，另 {stats['references'] - stats['paired']} 条缺少原始报告，未纳入差异率。",
        f"{stats['different']} 条引用与原值不同，涉及 {stats['unique_targets_affected']} 个不同的“地区×目标周”；共核对 {stats['unique_targets_compared']} 个这样的观测。最新一期的两条观测尚无后续引用。",
    ]


METHOD = [
    "当期原值指本地已归档 PDF 的当周发布值；不是对官方最新数据库终值的确认。以 PDF SHA-256 固定版本，官网同一期文件今后可能更新。",
    "每个地区、每份引用报告、每个目标周只计一次；摘要与正文的重复段落合并。不同后续报告引用同一目标周会分别计数，另提供去重后的地区×目标周数量。",
    "前一周按报告周历减一周，跨年保留第53周。往年同期按正文明确年份和相同周号配对；不假定历年日期相同，也不擅自修正年份标签。",
    "差值 = 后续引用值 − 当期原值，单位为百分点。精确按报告一位小数比较；平均绝对差和中位绝对差仅在有差异项中计算，未把一致项混入。",
    "这里只核对文字明确给出的 ILI%；没有从图中估读其他周的曲线。ILI% 是流感样病例占哨点医院门急诊病例总数的百分比。",
    "观察到差异不等于确认正式修订，也可能涉及文字/年份标签错误或历史周次对齐。没有官方逐项解释时保留两种表述，不覆盖数据库或现有趋势图。",
]


def issue_text(issue):
    label = f"{issue['report_id']} {REGIONS[issue['region']]}"
    if "year_label_set" in issue:
        return label + "：往年标签为 " + "、".join(map(str, issue["year_label_set"])) + "。" + issue["note"]
    if issue.get("code") == "comparison_word_inconsistent":
        return (f"{label}：当周值 {issue['current_tenths'] / 10:.1f}%，原文“{issue['clause']}”。" + issue["note"])
    return label + "：存在不完整段落或相互冲突的引用，见 audit.json 的 issues，冲突数值不参与比较。"


def write_report(result, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    generated = datetime.fromisoformat(result["generated_at"]).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S Asia/Shanghai")
    paired = [r for r in result["references"] if r["status"] == "matched"]
    paired.sort(key=lambda r: (-abs(r["delta_tenths"]), r["target_id"], r["region"], r["citing_report"]))
    changed = [r for r in paired if r["delta_tenths"]]
    main_table = [stats_cells(label, stats) for label, stats in summary_rows(result)]
    region_table = [stats_cells(f"{REGIONS[region]} · {KINDS[kind]}", result["summary"]["by_region_and_kind"][f"{region}/{kind}"])
                    for kind in KINDS for region in REGIONS]
    year_table = [stats_cells(f"{REGIONS[key.split('/')[0]]} · {key.split('/')[1]}年原值", stats)
                  for key, stats in result["summary"]["historical_by_region_and_target_year"].items()]
    trajectories = [t for t in result["trajectories"] if len(t["distinct_tenths"]) >= 3]
    trajectory_note = (f"同一地区、同一目标周，连同当期原值共有三种或更多不同数值的有 {len(trajectories)} 项。"
                       "完整引用时间序列保存在 audit.json 的 trajectories 中；最新引用不自动视为最准确值。")
    issues = [issue_text(issue) for issue in result["issues"]]
    detail_rows = [[r["target_id"], REGIONS[r["region"]], f"{r['original_tenths'] / 10:.1f}%", r["citing_report"],
                    KINDS[r["kind"]], f"{r['reference_tenths'] / 10:.1f}%", f"{r['delta_tenths'] / 10:+.1f}",
                    source(r, True, True), source(r, markdown=True)] for r in changed]
    markdown = "\n\n".join([
        "# ILI% 缺周与后续引用差异核对", f"生成时间：{generated}",
        "## 覆盖与缺失", *facts(result),
        "## 差异统计", "差值单位：百分点。差异项平均值和中位数仅统计不一致项。",
        md_table(HEADERS, main_table), "### 分南北方", md_table(HEADERS, region_table),
        "### 往年同期：按被引用原值年份划分", md_table(HEADERS, year_table),
        "## 同一周的多种表述", trajectory_note,
        "## 原文异常", "\n".join("- " + item for item in issues) or "无。",
        "## 核对口径与限制", "\n".join(f"{i}. {text}" for i, text in enumerate(METHOD, 1)),
        "## 全部差异清单", "按绝对差从大到小排列；页码为 PDF 文件页序，不是纸面印刷页码。",
        md_table(["目标周", "地区", "当期值", "引用报告", "引用类型", "引用值", "差值", "当期来源", "引用来源"], detail_rows),
    ]) + "\n"
    (output / "summary.md").write_text(markdown, encoding="utf-8")

    escape = html.escape

    def table(rows):
        return ('<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{escape(h)}</th>' for h in HEADERS)
                + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{escape(c)}</td>' for c in row) + '</tr>' for row in rows)
                + '</tbody></table></div>')

    rendered = []
    for row in paired:
        difference = row["delta_tenths"]
        evidence = "".join(f'<p>PDF 第 {item["page"]} 页：{escape(item["text"])}</p>' for item in row["evidence"])
        rendered.append(
            f'<tr data-region="{row["region"]}" data-kind="{row["kind"]}" data-year="{row["target_year"]}" '
            f'data-delta="{abs(difference)}" data-search="{row["target_id"]} {row["citing_report"]}"'
            + (' hidden' if not difference else '') + '>'
            f'<td><b>{row["target_id"]}</b><br>{REGIONS[row["region"]]}</td>'
            f'<td>{row["original_tenths"] / 10:.1f}%<small>{source(row, True)}</small></td>'
            f'<td>{row["citing_report"]}<small>{KINDS[row["kind"]]}</small></td>'
            f'<td>{row["reference_tenths"] / 10:.1f}%<small>{source(row)}</small></td>'
            f'<td class="delta">{difference / 10:+.1f}</td>'
            f'<td><details><summary>引用原文</summary>{evidence}</details></td></tr>')
    year_options = ''.join(f'<option value="{year}">{year}年原值</option>' for year in sorted({r["target_year"] for r in paired}))
    body = f'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ILI% 缺周与引用差异核对</title>
<style>
:root{{color-scheme:light;font-family:system-ui,-apple-system,"PingFang SC",sans-serif;color:#1b2c3b;background:#f3f5f7}}
*{{box-sizing:border-box}} body{{margin:0}} main{{max-width:1200px;margin:auto;padding:40px 24px 80px}}
h1{{font-size:30px;line-height:1.3;margin:8px 0 16px}} h2{{font-size:21px;margin:32px 0 14px}} p,li{{line-height:1.8}}
a{{color:#0063a6;text-underline-offset:3px}} .meta,small{{color:#52677a;font-size:13px}} small{{display:block;margin-top:5px}}
.card{{background:white;border:1px solid #d7e0e7;border-radius:12px;padding:20px 24px;margin:20px 0}}
.note{{background:#fff6df;border-left:4px solid #b78422;padding:10px 16px;line-height:1.8}}
.scroll{{overflow-x:auto}} table{{width:100%;border-collapse:collapse;font-size:14px;text-align:left}}
th,td{{padding:12px 10px;border-bottom:1px solid #e0e6eb;vertical-align:top}} th{{background:#eaf0f5;white-space:nowrap;font-weight:600}}
td{{line-height:1.7}} #entries td:nth-child(-n+5){{white-space:nowrap}} .delta{{font-variant-numeric:tabular-nums;font-weight:700}}
summary{{cursor:pointer;color:#185579}} details p{{min-width:230px;max-width:400px;white-space:normal;font-size:13px}}
.controls{{display:flex;flex-wrap:wrap;gap:14px;margin:18px 0}} label{{font-size:13px;display:flex;flex-direction:column;gap:5px}}
select,input{{font:inherit;padding:8px;border:1px solid #9caebc;border-radius:5px;background:white;color:inherit;min-height:38px}}
input{{width:205px}} :focus-visible{{outline:3px solid #5d9acf;outline-offset:2px}} [hidden]{{display:none!important}}
@media(max-width:650px){{main{{padding:22px 14px}}h1{{font-size:25px}}.card{{padding:16px}}}}
@media print{{.controls{{display:none}}main{{max-width:none;padding:0}}.card{{border:0}}table{{font-size:10px}}}}
</style>
<main><div class="meta">本地归档核对 · {escape(generated)}</div><h1>ILI% 缺周与后续引用差异</h1>
<p class="note">保留当期发布值，单独核对后续引用。这里的“差异”不等于已确认的正式修订，单位均为百分点。</p>
<section class="card"><h2 style="margin-top:0">覆盖与缺失</h2>{''.join('<p>'+escape(f)+'</p>' for f in facts(result))}</section>
<h2>有多少差异，幅度多大</h2><p>分母为有原报告可配对的引用条数。平均值与中位数只统计有差异项。</p>
{table(main_table)}<h2>分南北方</h2>{table(region_table)}
<details class="card"><summary>按历史原值年份拆分</summary>{table(year_table)}</details>
<p>{escape(trajectory_note)}</p>
<details class="card"><summary>原文异常与核对口径</summary><ul>{''.join('<li>'+escape(i)+'</li>' for i in issues)}</ul>
<ol>{''.join('<li>'+escape(m)+'</li>' for m in METHOD)}</ol></details>
<h2>逐条核对清单</h2><p>默认显示全部 {len(changed)} 条差异，按绝对差降序。来源链接指向官网 PDF；页码是 PDF 文件页序。</p>
<div class="controls">
<label>地区<select id="region"><option value="">南北方</option><option value="south">南方</option><option value="north">北方</option></select></label>
<label>引用类型<select id="kind"><option value="">全部类型</option><option value="previous_week">前一周</option><option value="historical_same_week">往年同期</option></select></label>
<label>目标年份<select id="year"><option value="">全部年份</option>{year_options}</select></label>
<label>绝对差<select id="minimum"><option value="1">有差异（≥0.1）</option><option value="5">≥0.5 个百分点</option><option value="10">≥1.0 个百分点</option><option value="0">全部可配对引用</option></select></label>
<label>目标周或引用报告<input id="search" placeholder="例如 2024-W28" type="search"></label></div>
<p id="count" aria-live="polite">显示 {len(changed)} 条 / {len(paired)} 条可配对引用</p>
<div class="scroll"><table id="entries"><thead><tr><th>目标周 / 地区</th><th>当期值</th><th>引用报告</th><th>后续引用值</th><th>差值（百分点）</th><th>原文证据</th></tr></thead>
<tbody>{''.join(rendered)}</tbody></table></div>
<p class="meta">输出文件：summary.md 是含完整差异表的文字报告；audit.json 含全部引用、未配对项、异常、SHA-256 和按目标周整理的引用时间序列。原始 PDF 未打包到本报告。</p>
</main><script>
const fields = Object.fromEntries(['region','kind','year','minimum','search'].map(id => [id,document.getElementById(id)]));
const rows = Array.from(document.querySelectorAll('#entries tbody tr'));
function filterRows() {{
  let visible = 0;
  for (const row of rows) {{
    const d = row.dataset;
    row.hidden = ['region','kind','year'].some(key => fields[key].value && fields[key].value !== d[key])
      || Number(d.delta) < Number(fields.minimum.value)
      || !d.search.toLowerCase().includes(fields.search.value.trim().toLowerCase());
    if (!row.hidden) visible++;
  }}
  document.getElementById('count').textContent = `显示 ${{visible}} 条 / ${{rows.length}} 条可配对引用`;
}}
for (const field of Object.values(fields)) field.addEventListener('input', filterRows);
filterRows();
</script></html>'''
    (output / "index.html").write_text(body, encoding="utf-8")
