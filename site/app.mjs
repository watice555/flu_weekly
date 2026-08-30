import {COLORS, SYMBOLS, previousWeek, drawChart, weeksInYear} from './chart.mjs';

const $ = id => document.getElementById(id);
let data, seasons;
const make = (tag, text, className) => {const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;};
function sourceLink(label, url) {
  const link = make('a', label);
  try {const parsed=new URL(url);if(!['https:','http:'].includes(parsed.protocol)||parsed.hostname!=='ivdc.chinacdc.cn')return make('span','官网链接待补充');}
  catch {return make('span','官网链接待补充');}
  link.href=url;link.target='_blank';link.rel='noopener noreferrer';return link;
}
function selectReport(row) {
  const panel=$('selection');panel.replaceChildren();
  panel.append(make('strong',`${row.year} 年第 ${row.week} 周`),make('span',`${row.start_date} 至 ${row.end_date}`,'selection-date'),
    make('span',`南方 ${row.south.toFixed(1)}% · 北方 ${row.north.toFixed(1)}%`,'values'),
    sourceLink(`第 ${row.source.report_number} 期 · 官网详情 ↗`,row.source.detail_url),sourceLink('PDF（官网）↗',row.source.pdf_url));
  const pages=[...new Set([...row.source.pages.south,...row.source.pages.north])].sort((a,b)=>a-b);
  panel.append(make('span',`PDF 第 ${pages.join('、')} 页`,'selection-date'));
  for(const note of row.quality_notes || [])panel.append(make('span',`来源说明：${note.message}`,'quality'));
}
function showTooltip(row,region,x,y) {
  const box=$('tooltip');box.replaceChildren(make('strong',`${row.season} · 第 ${row.week} 周`),
    make('div',`${region==='south'?'南方':'北方'} ILI%  ${row[region].toFixed(1)}%`),
    make('span',`${row.start_date} 至 ${row.end_date}`),make('span','点击查看原报告链接'));
  box.hidden=false;const bounds=box.getBoundingClientRect();
  box.style.left=`${Math.max(8,Math.min(x+12,window.innerWidth-bounds.width-8))}px`;
  box.style.top=`${Math.max(8,Math.min(y+14,window.innerHeight-bounds.height-8))}px`;
}
function renderCharts() {
  $('tooltip').hidden=true;
  for(const region of ['south','north'])drawChart($(`${region}-chart`),data.reports,seasons,region,
    {show:showTooltip,hide:()=>$('tooltip').hidden=true,select:selectReport});
}
function renderRows() {
  const rows=data.reports.filter(row=>String(row.year)===$('year-filter').value).toReversed();
  $('rows').replaceChildren();
  for(const row of rows){const tr=make('tr');tr.append(make('td',`${row.year} 年第 ${row.week} 周`),make('td',`${row.start_date} — ${row.end_date}`),make('td',row.south.toFixed(1)),make('td',row.north.toFixed(1)));
    const source=make('td');source.append(sourceLink(`第 ${row.source.report_number} 期`,row.source.detail_url),sourceLink('官网 PDF ↗',row.source.pdf_url));tr.append(source);$('rows').append(tr);}
}
function exportCsv() {
  const rows=data.reports.filter(row=>String(row.year)===$('year-filter').value);
  const quote=value=>`"${String(value??'').replaceAll('"','""')}"`;
  const lines=[['周次','开始日期','结束日期','南方ILI%','北方ILI%','口径','官网详情','官网PDF'],
    ...rows.map(row=>[row.id,row.start_date,row.end_date,row.south.toFixed(1),row.north.toFixed(1),'当期发布值',row.source.detail_url,row.source.pdf_url])];
  const blob=new Blob(['\uFEFF'+lines.map(line=>line.map(quote).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'});
  const url=URL.createObjectURL(blob),link=make('a');link.href=url;link.download=`ili-${$('year-filter').value}.csv`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function start() {
  const response=await fetch('./data/ili.json');if(!response.ok)throw new Error(`数据文件不可用（HTTP ${response.status}）`);
  data=await response.json();
  if(data.schema_version!==1||!Array.isArray(data.reports)||!data.reports.length)throw new Error('尚无可绘制的周报数据');
  for(const row of data.reports){if(!Number.isFinite(row.south)||!Number.isFinite(row.north)||row.south<0||row.north<0||row.south>100||row.north>100)throw new Error('数据包含无效百分比，停止绘图');}
  const latest=data.reports.at(-1),previous=previousWeek(data.reports,latest);
  $('edition').replaceChildren(make('strong',`${latest.year} 年第 ${latest.week} 周`),make('span',`${latest.start_date} 至 ${latest.end_date}`));
  const seasonYears=[...new Set(data.reports.map(row=>row.season_start_year))].sort((a,b)=>a-b);
  seasons=seasonYears.map((year,index)=>({year,selected:index>=seasonYears.length-4,color:COLORS[index%4],shape:index%4}));
  for(const option of seasons){const label=make('label',undefined,'season-option');label.style.setProperty('--season-color',option.color);
    const input=make('input');input.type='checkbox';input.checked=option.selected;input.value=option.year;input.setAttribute('aria-label',`${option.year}-${option.year+1} 流感年度`);
    input.addEventListener('change',()=>{option.selected=input.checked;renderCharts();});
    const partial=option.year<latest.season_start_year&&data.reports.filter(row=>row.season_start_year===option.year).length<weeksInYear(option.year);
    label.append(input,make('span',SYMBOLS[option.shape],'marker'),make('span',`${option.year}-${option.year+1}${partial?'（部分）':''}`));$('season-options').append(label);}
  for(const region of ['south','north']){const box=$(`${region}-latest`),value=make('strong',latest[region].toFixed(1));value.append(make('small','%'));box.append(value);
    const delta=previous?(latest[region]-previous[region]):null;
    box.append(make('span',delta===null?'前一周数据缺失':Math.abs(delta)<.001?'与前期发布值持平':`较前期发布值 ${delta>0?'+':'−'}${Math.abs(delta).toFixed(1)} 个百分点`));}
  const coverage=data.coverage;
  $('coverage').textContent=`已收录 ${coverage.reports} 期报告、${coverage.observations} 条南北方观测，覆盖 ${coverage.first} 至 ${coverage.last}。`+
    (coverage.missing_weeks.length?` 缺失周：${coverage.missing_weeks.join('、')}。`:' 覆盖区间内无缺失周。')+
    ` 最早流感年度 ${seasonYears[0]}-${seasonYears[0]+1} 只展示已收录部分，未收录的前段留空。`;
  const years=[...new Set(data.reports.map(row=>row.year))].sort((a,b)=>b-a);
  for(const year of years){const option=make('option',`${year} 年`);option.value=year;$('year-filter').append(option);}
  $('year-filter').addEventListener('change',renderRows);$('export-csv').addEventListener('click',exportCsv);
  $('report-count').textContent=`${coverage.reports} 期 · 可导出 CSV`;
  $('content').hidden=false;renderCharts();renderRows();selectReport(latest);
}
window.addEventListener('scroll',()=>$('tooltip').hidden=true,{passive:true});
start().catch(error=>{$('error').textContent=`无法加载数据：${error.message}。请先运行本地数据库构建，再通过 HTTP 服务打开 site 目录。`;$('error').hidden=false;$('edition').textContent='数据未就绪';});
