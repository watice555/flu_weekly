export const COLORS = ['#13a36d', '#e1a70b', '#8254ad', '#e04b4d'];
export const SYMBOLS = ['■', '●', '◆', '▲'];

export function weeksInYear(year) {
  const weekday = new Date(Date.UTC(year, 0, 1)).getUTCDay();
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  return weekday === 4 || (weekday === 3 && leap) ? 53 : 52;
}

export function axisWeeks(seasons) {
  const last = seasons.some(year => weeksInYear(year) === 53) ? 53 : 52;
  return [...Array.from({length:last - 13}, (_, index) => index + 14), ...Array.from({length:13}, (_, index) => index + 1)];
}

export function seasonalSeries(reports, season, region, axis) {
  const rows = new Map(reports.filter(row => row.season_start_year === season).map(row => [row.week, row]));
  const segments = [];
  let current = [];
  for (let index = 0; index < axis.length; index++) {
    const week = axis[index];
    if (week === 53 && weeksInYear(season) === 52) continue;
    const row = rows.get(week);
    if (!row || !Number.isFinite(row[region])) {
      if (current.length) segments.push(current);
      current = [];
      continue;
    }
    current.push({index, week, value:row[region], row});
  }
  if (current.length) segments.push(current);
  return segments;
}

export function previousWeek(reports, row) {
  const target = Date.parse(`${row.start_date}T00:00:00Z`) - 7 * 86400000;
  return reports.find(item => Date.parse(`${item.start_date}T00:00:00Z`) === target);
}

export function svgElement(name, attrs = {}, text) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

export function drawChart(container, reports, seasonOptions, region, callbacks) {
  container.replaceChildren();
  const selected = seasonOptions.filter(option => option.selected);
  if (!selected.length) {
    const empty = document.createElement('div'); empty.className = 'empty';
    empty.textContent = '请选择至少一个流感年度'; container.append(empty); return;
  }
  const axis = axisWeeks(selected.map(option => option.year));
  const width = 1180, height = 285, left = 47, right = 16, top = 26, bottom = 42;
  const plotWidth = width - left - right, plotHeight = height - top - bottom;
  const selectedYears = new Set(selected.map(option => option.year));
  const largest = Math.max(2, ...reports.filter(row => selectedYears.has(row.season_start_year)).flatMap(row => [row.south, row.north]));
  const step = largest > 8 ? 2 : 1;
  const maximum = Math.ceil((largest + .35) / step) * step;
  const x = index => left + index * plotWidth / (axis.length - 1);
  const y = value => top + (1 - value / maximum) * plotHeight;
  const svg = svgElement('svg', {viewBox:`0 0 ${width} ${height}`, role:'img', 'aria-label':`${region === 'south' ? '南方' : '北方'}省份 ILI% 流感年度对比，缺失周不连线`});
  svg.append(svgElement('title', {}, '流感年度 ILI% 对比'));
  svg.append(svgElement('text', {x:9, y:16, class:'axis-title'}, 'ILI (%)'));
  for (let value = 0; value <= maximum; value += step) {
    svg.append(svgElement('line', {x1:left, x2:width-right, y1:y(value), y2:y(value), class:'grid'}));
    svg.append(svgElement('text', {x:left-12, y:y(value)+4, 'text-anchor':'end'}, value));
  }
  axis.forEach((week, index) => {
    if (week === 1 || week === 53 || (week >= 14 && week % 2 === 0) || (week < 14 && week % 2 === 0)) {
      svg.append(svgElement('text', {x:x(index), y:height-22, 'text-anchor':'middle'}, String(week).padStart(2,'0')));
    }
  });
  const boundary = x(axis.indexOf(1)) - plotWidth / (axis.length - 1) / 2;
  svg.append(svgElement('line', {x1:boundary, x2:boundary, y1:top, y2:height-bottom, class:'year-boundary'}));
  svg.append(svgElement('text', {x:width/2, y:height-3, 'text-anchor':'middle', class:'axis-title'}, '周次'));
  for (const option of selected) {
    const segments = seasonalSeries(reports, option.year, region, axis);
    for (const points of segments) {
      svg.append(svgElement('path', {d:points.map((point,index) => `${index ? 'L' : 'M'}${x(point.index)},${y(point.value)}`).join(' '), stroke:option.color, class:'series-line'}));
      for (const point of points) {
        const px = x(point.index), py = y(point.value), size = 3.1;
        let marker;
        if (option.shape === 0) marker = svgElement('rect', {x:px-size,y:py-size,width:size*2,height:size*2,fill:option.color});
        else if (option.shape === 2) marker = svgElement('path', {d:`M${px},${py-4} l4,4 l-4,4 l-4,-4 Z`,fill:option.color});
        else if (option.shape === 3) marker = svgElement('path', {d:`M${px},${py-4} l4,7 h-8 Z`,fill:option.color});
        else marker = svgElement('circle', {cx:px,cy:py,r:size,fill:option.color});
        svg.append(marker);
        const hit = svgElement('circle', {cx:px,cy:py,r:7,class:'hit-target',tabindex:'0',role:'button',
          'data-report':point.row.id,'aria-label':`${point.row.year}年第${point.week}周 ${region === 'south' ? '南方' : '北方'} ${point.value.toFixed(1)}%，查看官方报告`});
        hit.addEventListener('pointerenter', event => callbacks.show(point.row, region, event.clientX, event.clientY));
        hit.addEventListener('pointerleave', callbacks.hide);
        hit.addEventListener('focus', () => {const box=hit.getBoundingClientRect();callbacks.show(point.row,region,box.x,box.y);});
        hit.addEventListener('blur', callbacks.hide);
        hit.addEventListener('click', () => callbacks.select(point.row));
        hit.addEventListener('keydown', event => {if (event.key === 'Enter' || event.key === ' ') {event.preventDefault();callbacks.select(point.row);}});
        svg.append(hit);
      }
    }
  }
  container.append(svg);
}
