export const sectorBoardStyles=String.raw`
#sector-board[hidden],#cards[hidden],#market-grid[hidden]{display:none}.sector-board-intro{margin-bottom:20px}.sector-board-controls{display:flex;gap:14px;flex-wrap:wrap;align-items:end;margin-bottom:18px}.sector-board-controls label{display:flex;flex-direction:column;gap:5px;font-size:14px;color:var(--muted)}.sector-board-controls input,.sector-board-controls select{border:1px solid var(--line);border-radius:6px;background:white;color:var(--ink);padding:8px 12px;min-height:40px;font-size:14px}.sector-board-controls input{width:240px;max-width:100%}.sector-board-method{font-size:14px;color:#607183;line-height:1.7;margin:12px 0}.sector-board-method summary{cursor:pointer;color:var(--accent)}.sector-board-method code{white-space:normal;overflow-wrap:anywhere}.sector-board-method a,.sector-name a{color:var(--accent)}.sector-legend{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;color:#607183;margin:12px 0 18px}.sector-legend .hot{color:#b13c46}.sector-legend .cold{color:#117164}.sector-board-group{margin-bottom:24px}.sector-board-group .panel-head{padding-bottom:14px;flex-wrap:wrap;align-items:start}.sector-board-group h2{font-size:17px}.sector-board-group .caption{font-size:13px;max-width:100%}/* Fit all ten dates without a nested scroll area. */
.sector-board-wrap{container-type:inline-size;container-name:sector-table}
.sector-board-table{width:100%;table-layout:fixed;font-size:14px;line-height:1.35}
.sector-board-table th,.sector-board-table td{padding:6px 4px;min-width:0}
.sector-board-table thead th{background:#244e66;color:white;font-weight:500;border-bottom:0}
.sector-board-table th:first-child{width:280px;padding-left:12px;background:white;text-align:left;white-space:normal;overflow-wrap:anywhere}
.sector-board-table thead th:first-child{background:#244e66}
.sector-board-table tbody tr:nth-child(even) th:first-child{background:#f3f6f8}
.sector-board-table tbody th{color:var(--ink);font-weight:500}
.sector-name{display:flex;gap:6px;align-items:baseline;white-space:nowrap}
.sector-name a{color:var(--ink);text-decoration:none}
.sector-name a:hover{text-decoration:underline}
.sector-code{display:inline;flex-shrink:0;font-size:12px;color:#6c7d8b;font-weight:400}
.sector-added{flex-shrink:0;font-size:12px;color:#47716b;border:1px solid #cadfd9;border-radius:4px;padding:0 4px;font-weight:400}
.sector-board-table .sector-latest{border-left:2px solid #c1d8d1;border-right:2px solid #c1d8d1;font-weight:600}
.sector-board-table thead .sector-latest{background:#236b61}
.sector-cell-hot{color:#a83440}.sector-cell-cold{color:#086b5d}.sector-cell-missing{color:#82919e;background:#f5f7f9}.sector-board-notes{font-size:13px;color:#697b8b;padding:12px 20px 18px;line-height:1.6}.sector-board-notes details{margin-top:8px}.sector-board-notes ul{padding-left:20px;margin-bottom:0}.sector-board-empty{padding:30px 20px;font-size:14px;color:#71808d}.sector-group-meta{font-size:13px;color:var(--muted);margin-top:5px}.sector-board-count{font-size:14px;color:var(--muted);margin-left:auto;padding-bottom:9px}
@container sector-table (max-width:820px){
.sector-board-table,.sector-board-table tbody{display:block}
.sector-board-table thead{display:none}
.sector-board-table tbody tr{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));padding:8px 6px;gap:4px 0;border-bottom:1px solid var(--line)}
.sector-board-table tbody tr:nth-child(even){background:#f8fafb}
.sector-board-table tbody th:first-child{grid-column:1/-1;width:auto;max-width:none;padding:2px 4px 6px;border:0;background:none}
.sector-board-table td{display:block;text-align:right;padding:4px;border:0}
.sector-board-table td::before{content:attr(data-date);display:block;margin-bottom:3px;font-size:14px;font-weight:400;color:var(--muted)}
.sector-board-table .sector-latest{border:0;background:#eef6f2}
}
@media(max-width:720px){.sector-board-controls{gap:12px}.sector-board-controls input{width:100%}.sector-board-controls label:first-child{width:100%}.sector-board-count{width:100%;margin-left:0;padding:0}.sector-board-notes{padding-left:16px;padding-right:16px}}
@media print{.sector-board-controls{display:none}.sector-board-wrap{container-type:normal}.sector-board-group{break-inside:auto}.sector-board-table tr{break-inside:avoid}}
`;
export const sectorBoardHtml=String.raw`
<section id="sector-board" hidden aria-label="板块20日EMA乖离率">
<div class="sector-board-intro">
<div class="sector-board-controls"><label>查找板块<input id="sector-search" type="search" placeholder="名称或指数代码"></label><label>观察范围<select id="sector-scope"><option value="all">参考列表 + 补充</option><option value="reference">仅参考列表</option></select></label><label>排列方式<select id="sector-sort"><option value="desc">最新乖离率 · 从高到低</option><option value="asc">最新乖离率 · 从低到高</option><option value="reference">参考列表顺序</option></select></label><span class="sector-board-count" id="sector-board-count"></span></div>
<details class="sector-board-method"><summary>计算口径与来源</summary><p>采用自然对数减法版：<code>100 × [LN(收盘价) − EMA20(LN(收盘价))]</code>。先取对数再计算 EMA，平滑系数为 2/21，以首个有效对数收盘价初始化；每个展示日使用至少 250 根有效日线预热。<a href="https://xueqiu.com/3559889031/354857867" target="_blank" rel="noopener noreferrer">张翼轸的公式说明</a></p><p>行情来源为东方财富及中证、国证官方指数日线，按各指数本币价格点位计算，A 股为人民币、港股为港元；不含分红再投资。点击板块名称可查看官方指数资料。港股独立使用港交所日历，休市时注明实际数据日期；港股历史逐日核对交易日历；历史不足或缺失不补零。</p></details>
<div class="sector-legend"><span>最新日期在左 · 每组最近 10 个交易日</span><span class="hot">&gt; 5% 标红</span><span class="cold">&lt; −5% 标绿</span><span>颜色仅标示偏离幅度</span></div>
</div><div id="sector-groups"></div>
</section>`;
export const sectorBoardScript=String.raw`
function renderSectorBoard(){
var panel=el('sector-board'),box=el('sector-groups');panel.hidden=view!=='stocks';if(panel.hidden)return;box.replaceChildren();
var board=report&&report.market_data&&report.market_data.sector_board;
if(!board||board.date!==report.date||board.method!=='ln-close-minus-ema20-ln-close-v1'||!Array.isArray(board.groups)||!board.groups.length){el('sector-board-count').textContent='暂无板块历史';box.append(node('div',board&&board.date!==report.date?'板块数据日期与报告不一致，已隐藏。':'这份历史报告尚未保存板块 EMA 观察数据；新日报会随每日复盘一同更新。','panel sector-board-empty'));return}
var query=el('sector-search').value.trim().toLowerCase(),scope=el('sector-scope').value,sort=el('sector-sort').value,shown=0,total=0;
board.groups.forEach(function(group){
var section=node('section',undefined,'panel sector-board-group');section.dataset.group=group.key;
var head=node('div',undefined,'panel-head'),heading=node('div');heading.append(node('h2',group.name+' · 对数 20EMA 乖离率（%）'));
var latest=group.dates[0],meta='数据截至 '+(latest||'日期待核验')+' · '+(group.key==='HK'?'港元':'人民币')+'价格指数 · 可用 '+group.sample_size+' / '+group.configured_count;
heading.append(node('div',meta,'sector-group-meta'));head.append(heading);section.append(head);
var rows=group.rows.filter(function(r){return (scope!=='reference'||!r.supplemental)&&(!query||[r.name,r.official_name,r.code].join(' ').toLowerCase().includes(query))});shown+=rows.length;total+=group.rows.length;
function latestValue(r){return r.values&&r.values[0]&&typeof r.values[0].value==='number'?r.values[0].value:null}
if(sort!=='reference')rows.sort(function(a,b){var x=latestValue(a),y=latestValue(b);return x===null?(y===null?0:1):y===null?-1:(sort==='asc'?x-y:y-x)});
if(!rows.length)section.append(node('div','没有匹配的板块','sector-board-empty'));
else if(!group.dates.length)section.append(node('div','本市场交易日历缺失，暂不能计算板块指标。','sector-board-empty'));
else{
var wrap=node('div',undefined,'sector-board-wrap'),table=node('table',undefined,'sector-board-table'),thead=node('thead'),tr=node('tr');wrap.setAttribute('role','region');wrap.setAttribute('aria-label',group.name+'乖离率表');table.setAttribute('aria-label',group.name+'最近10个交易日，最新在左');var nameHead=node('th','板块 / 指数');nameHead.scope='col';tr.append(nameHead);
group.dates.forEach(function(d,i){var th=node('th',d.slice(5),i===0?'sector-latest':undefined);th.scope='col';th.title=d;th.setAttribute('aria-label',d+(i===0?'，最新交易日':''));tr.append(th)});thead.append(tr);table.append(thead);var tbody=node('tbody');
rows.forEach(function(r){var row=node('tr');row.dataset.code=r.code;var th=node('th'),name=node('div',undefined,'sector-name'),link=node('a',r.name);th.scope='row';link.href=r.identity_url;link.target='_blank';link.rel='noopener noreferrer';link.title=r.official_name;name.append(link,node('span',r.issuer+' · '+r.code,'sector-code'));if(r.supplemental)name.append(node('span','补充','sector-added'));th.append(name);row.append(th);
var byDate=new Map((r.values||[]).map(function(p){return [p.date,p.value]}));group.dates.forEach(function(d,i){var v=byDate.get(d),valid=typeof v==='number'&&Number.isFinite(v),cell=node('td',valid?num(v,2):'—');cell.dataset.date=d.slice(5);if(i===0)cell.classList.add('sector-latest');if(valid){if(v>5)cell.classList.add('sector-cell-hot');if(v< -5)cell.classList.add('sector-cell-cold');if(Math.abs(v)>5)cell.style.backgroundColor=(v>0?'rgba(200,76,79,':'rgba(22,133,119,')+Math.min(.18,.035+Math.abs(v)/160)+')'}else cell.classList.add('sector-cell-missing');var hint=r.name+' · '+d+'：'+(valid?num(v,2)+'%':r.reason||'数据缺失');cell.title=hint;cell.setAttribute('aria-label',hint);row.append(cell)});tbody.append(row)});table.append(tbody);wrap.append(table);section.append(wrap)
}
var notes=node('div',undefined,'sector-board-notes');notes.append(node('div','共 '+group.configured_count+' 个观察指数；补充项单独标记。— 表示数据缺失，不是 0%。'));
(group.observations||[]).forEach(function(t){notes.append(node('div',t))});var missing=group.rows.filter(function(r){return r.status!=='available'});if(missing.length){var detail=node('details'),list=node('ul');detail.append(node('summary','缺失说明（'+missing.length+'）'));missing.forEach(function(r){list.append(node('li',r.name+' · '+r.code+'：'+r.reason))});detail.append(list);notes.append(detail)}section.append(notes);box.append(section)
});el('sector-board-count').textContent='显示 '+shown+' / '+total+' 个板块'
}
el('sector-search').oninput=renderSectorBoard;el('sector-scope').onchange=renderSectorBoard;el('sector-sort').onchange=renderSectorBoard;
`;
