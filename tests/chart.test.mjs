import test from 'node:test';
import assert from 'node:assert/strict';
import {weeksInYear,axisWeeks,seasonalSeries,previousWeek} from '../site/chart.mjs';

test('ISO week 53 is included only when a selected season has it',()=>{
  assert.equal(weeksInYear(2026),53);assert.equal(weeksInYear(2025),52);
  assert.ok(axisWeeks([2026]).includes(53));assert.ok(!axisWeeks([2025]).includes(53));
  assert.equal(axisWeeks([2025])[0],14);assert.equal(axisWeeks([2025]).at(-1),13);
});
test('a missing report splits the curve; values are never interpolated',()=>{
  const data=[14,15,17].map(week=>({week,season_start_year:2025,south:4}));
  const segments=seasonalSeries(data,2025,'south',axisWeeks([2025]));
  assert.deepEqual(segments.map(segment=>segment.map(point=>point.week)),[[14,15],[17]]);
});
test('a 52-week season stays continuous across a shared 53-week axis',()=>{
  const data=[52,1].map(week=>({week,season_start_year:2025,north:3}));
  const segments=seasonalSeries(data,2025,'north',axisWeeks([2025,2026]));
  assert.deepEqual(segments.map(segment=>segment.map(point=>point.week)),[[52,1]]);
});
test('weekly change cannot silently use the previous available report',()=>{
  const latest={start_date:'2026-08-17'};
  assert.equal(previousWeek([{start_date:'2026-08-03'}],latest),undefined);
  assert.equal(previousWeek([{start_date:'2026-08-10'}],latest).start_date,'2026-08-10');
});
