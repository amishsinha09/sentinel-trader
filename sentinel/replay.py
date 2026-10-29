"""Animated backtest replay: one self-contained HTML file (no external scripts).

Shows the account balance ticking, the equity curve drawing against buy & hold, a node map where
each strategy (or each traded symbol, when there is one strategy) flashes green/red as trades close,
a monthly heatmap, a trade log and a leaderboard. The dev / test / holdout periods are shaded and the
page states the strategy's validation status, so a good-looking replay can't hide a failed gate.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd

from .portfolio import PortfolioResult, monthly_returns

PALETTE = ["#2dd4bf", "#60a5fa", "#c084fc", "#fbbf24", "#f472b6", "#34d399", "#fb923c", "#a3e635",
           "#38bdf8", "#e879f9", "#facc15", "#4ade80", "#f87171", "#818cf8", "#22d3ee"]


def build_payload(res: PortfolioResult, windows: dict | None, title: str, note: str, costs_bps: float) -> dict:
    eq = res.equity
    dates = [d.strftime("%Y-%m-%d") for d in eq.index]
    pos = {d: i for i, d in enumerate(eq.index)}
    by_strategy = res.trades["strategy"].nunique() > 1 if len(res.trades) else False
    node_key = "strategy" if by_strategy else "symbol"
    trades = []
    for t in res.trades.itertuples(index=False):
        if t.exit_reason == "end":  # positions force-closed on the last day are bookkeeping, not real trades
            continue
        xi = pos.get(pd.Timestamp(t.exit_date))
        if xi is None:
            xi = int(eq.index.searchsorted(pd.Timestamp(t.exit_date)))
        trades.append([min(xi, len(eq) - 1), getattr(t, node_key), round(float(t.pnl), 2), round(float(t.ret) * 100, 2),
                       t.symbol, t.strategy, str(pd.Timestamp(t.entry_date).date())])
    nodes = sorted({t[1] for t in trades}) or ["(no trades)"]
    sub = {}
    if not by_strategy and len(res.trades):
        sub = {n: res.trades.loc[res.trades.symbol == n, "strategy"].iloc[0] for n in nodes if n != "(no trades)"}
    mr = monthly_returns(eq)
    months = [[d.year, d.month, round(float(r) * 100, 2), int(eq.index.searchsorted(d, side="right") - 1)]
              for d, r in mr.items()]
    return {
        "title": title, "note": note, "costs_bps": costs_bps,
        "capital": round(float(eq.iloc[0]), 2), "dates": dates,
        "equity": [round(float(v), 2) for v in eq.values],
        "bench": [round(float(v), 2) for v in res.benchmark.values],
        "trades": trades, "nodes": nodes, "node_kind": node_key,
        "node_sub": sub, "colors": {n: PALETTE[i % len(PALETTE)] for i, n in enumerate(nodes)},
        "strategies": sorted(res.trades["strategy"].unique().tolist()) if len(res.trades) else [],
        "months": months, "windows": windows or {},
    }


def render(payload: dict, out_path) -> Path:
    page = TEMPLATE.replace("__TITLE__", html.escape(payload["title"])).replace(
        "__DATA__", json.dumps(payload, separators=(",", ":")).replace("</", "<\\/"))
    p = Path(out_path)
    p.write_text(page, encoding="utf-8")
    return p


TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Sentinel Replay</title>
<style>
:root{--bg:#0a0e13;--panel:#0f151c;--line:#1e2a36;--fg:#e6edf3;--mut:#7d8b99;--acc:#2dd4bf;--bench:#5b6670;
--win:#22c55e;--loss:#ef4444;--warn:#fbbf24;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 var(--mono)}
.wrap{max-width:1180px;margin:0 auto;padding:16px}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
.brand{font-weight:800;letter-spacing:.04em;font-size:18px}.brand b{color:var(--acc)}
.date{font-size:18px;font-weight:700}.sub{color:var(--mut);margin:2px 0 12px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:12px 14px;position:relative;min-width:0}
.lbl{color:var(--mut);font-size:11px;letter-spacing:.08em;text-transform:uppercase;margin:0 0 6px}
.bal{font-size:clamp(34px,7vw,64px);font-weight:800;line-height:1;margin:4px 0}
.pnl{font-size:16px;margin-bottom:12px}.up{color:var(--win)}.down{color:var(--loss)}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.stats div span{display:block;color:var(--mut);font-size:10px;letter-spacing:.06em}
.stats div b{font-size:18px}
canvas{width:100%;height:190px;display:block}
.legend{display:flex;gap:14px;flex-wrap:wrap;color:var(--mut);font-size:12px;margin-top:6px}
.sw{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:6px}
.mapwrap{grid-column:1/-1}.maphead{display:flex;justify-content:space-between;font-weight:700;letter-spacing:.06em}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin:0 6px 0 14px}
svg.map{width:100%;height:auto;display:block}
.nodebox{fill:#0d1218;stroke-width:1.2}.narrow .nodetxt{font-size:19px}.narrow .nodesub{font-size:16px}.nodetxt{font:700 13px var(--mono);fill:var(--fg)}.nodesub{font:11px var(--mono);fill:var(--mut)}
.acct{fill:#0d1218;stroke:var(--acc);stroke-width:1.5}.ring{fill:none;stroke-width:2}
.heat{display:grid;grid-template-columns:44px repeat(12,1fr);gap:3px;font-size:10px;color:var(--mut)}
.heat i{display:block;height:15px;border-radius:2px;background:#16202a}
.log{font-size:12px;width:100%;border-collapse:collapse}.log td,.log th{padding:4px 6px;border-bottom:1px solid var(--line);text-align:left}.log td:first-child,.log td:last-child{white-space:nowrap}
.log th{color:var(--mut);font-weight:400;font-size:10px;letter-spacing:.06em}
.lb{display:grid;grid-template-columns:110px 1fr 90px;gap:6px;align-items:center;font-size:12px;margin:5px 0}
.bar{height:9px;background:#16202a;border-radius:2px;overflow:hidden}.bar i{display:block;height:100%}
.ctl{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:12px 0}
button{background:#14202b;color:var(--fg);border:1px solid var(--line);border-radius:4px;padding:6px 12px;font:12px var(--mono);cursor:pointer}
button.on{border-color:var(--acc);color:var(--acc)}input[type=range]{flex:1;min-width:160px;accent-color:var(--acc)}
.phase{font-size:11px;padding:2px 8px;border-radius:999px;border:1px solid var(--line);color:var(--mut)}
.note{border:1px solid var(--warn);color:var(--warn);border-radius:6px;padding:10px 12px;margin-top:12px;font-size:12px}
@media (max-width:760px){.grid{grid-template-columns:1fr}.stats{grid-template-columns:repeat(2,1fr)}}
</style></head><body><div class="wrap">
<div class="top"><div class="brand">SENTINEL <b>//</b> REPLAY</div><div class="date" id="date">—</div></div>
<div class="sub" id="sub"></div>
<div class="ctl"><button id="play">❚❚ Pause</button><button data-s="0.5">0.5×</button><button data-s="1" class="on">1×</button>
<button data-s="2">2×</button><button data-s="4">4×</button><button id="restart">↺ Restart</button>
<input type="range" id="scrub" min="0" max="1000" value="0" aria-label="Scrub through time"><span class="phase" id="phase">—</span></div>
<div class="grid">
<div class="panel"><p class="lbl">// account balance</p><div class="bal" id="bal">—</div><div class="pnl" id="pnl">—</div>
<div class="stats"><div><span>TRADES</span><b id="ntr">0</b></div><div><span>WIN RATE</span><b id="wr">—</b></div>
<div><span>BIGGEST DROP</span><b id="dd" class="down">0.0%</b></div><div><span id="nlab">NODES</span><b id="nn">0</b></div></div></div>
<div class="panel"><p class="lbl">// balance over time</p><canvas id="cv"></canvas>
<div class="legend"><span><i class="sw" style="background:var(--acc)"></i>Strategy</span><span><i class="sw" style="background:var(--bench)"></i>Buy &amp; hold, same ETFs</span>
<span id="shadeleg"></span></div></div>
<div class="panel mapwrap"><div class="maphead"><span id="maptitle">EACH NODE = ONE MARKET</span><span><span class="dot" style="background:var(--win)"></span>WIN<span class="dot" style="background:var(--loss)"></span>LOSS</span></div>
<svg class="map" id="map" viewBox="0 0 1000 360" preserveAspectRatio="xMidYMid meet"></svg></div>
<div class="panel"><p class="lbl" id="hlabel">// monthly returns</p><div class="heat" id="heat"></div></div>
<div class="panel"><p class="lbl">// latest closed trades</p><div style="overflow-x:auto"><table class="log"><thead><tr><th>CLOSED</th><th>NODE</th><th>RESULT</th></tr></thead><tbody id="log"></tbody></table></div></div>
<div class="panel mapwrap"><p class="lbl">// leaderboard — profit by node</p><div id="lb"></div></div>
</div>
<div class="note" id="note"></div>
</div>
<script>
const D=__DATA__;
const N=D.dates.length, $=id=>document.getElementById(id);
const fmt=v=>'$'+Math.round(v).toLocaleString('en-US');
const pct=v=>(v>=0?'+':'')+v.toFixed(1)+'%';
const yrs=D.dates[0].slice(0,4)+' → '+D.dates[N-1].slice(0,4);
$('sub').textContent=`${D.strategies.length||1} strateg${(D.strategies.length||1)>1?'ies trade':'y trades'} one ${fmt(D.capital)} account · ${yrs} · costs ${D.costs_bps} bps per trade side`;
$('note').textContent=D.note;
$('nlab').textContent=D.node_kind==='strategy'?'STRATEGIES':'MARKETS';
$('maptitle').textContent=D.node_kind==='strategy'?'EACH NODE = ONE STRATEGY':'EACH NODE = ONE MARKET';
$('nn').textContent=D.node_kind==='strategy'?D.strategies.length:D.nodes.length;
const W=D.windows||{}, inWin=(i,k)=>W[k]&&D.dates[i]>=W[k][0]&&D.dates[i]<=W[k][1];
const phaseName=i=>inWin(i,'holdout')?'FINAL-EXAM YEARS (unseen)':inWin(i,'validation')?'TEST YEARS (not built on)':inWin(i,'dev')?'BUILT-ON YEARS':'';
if(W.dev)$('shadeleg').textContent='Shaded: test years · darker: final-exam years';
// ---- trades by day
const byDay=new Map();D.trades.forEach(t=>{if(!byDay.has(t[0]))byDay.set(t[0],[]);byDay.get(t[0]).push(t)});
// ---- node map
const svg=$('map'),NS='http://www.w3.org/2000/svg',nodeEls={};
function el(t,a,p){const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);(p||svg).appendChild(e);return e}
const narrow=svg.clientWidth<600,M=D.nodes.length;
const [VW,VH]=narrow?[600,860]:[1000,360];svg.setAttribute('viewBox',`0 0 ${VW} ${VH}`);
const cx=VW/2,cy=VH/2,rx=narrow?215:400,ry=narrow?370:135,K=narrow?1.45:1;if(narrow)svg.classList.add('narrow');
el('ellipse',{cx,cy,rx,ry,fill:'none',stroke:'#1e2a36','stroke-dasharray':'2 6'});
el('ellipse',{cx,cy,rx:rx*.6,ry:ry*.6,fill:'none',stroke:'#16202a','stroke-dasharray':'2 6'});
const acc=el('g',{});el('rect',{x:cx-100*K,y:cy-32*K,width:200*K,height:64*K,rx:4,class:'acct'},acc);
el('text',{x:cx,y:cy-6*K,'text-anchor':'middle',class:'nodetxt'},acc).textContent='THE ACCOUNT';
const accVal=el('text',{x:cx,y:cy+16*K,'text-anchor':'middle',class:'nodetxt',fill:'var(--acc)'},acc);
// place nodes at equal distances ALONG the ellipse (equal angles crowd the ends of a stretched oval)
const S=2000,pts=[],cum=[0];for(let j=0;j<=S;j++){const a=-Math.PI/2+2*Math.PI*j/S;pts.push([cx+rx*Math.cos(a),cy+ry*Math.sin(a)]);
 if(j)cum.push(cum[j-1]+Math.hypot(pts[j][0]-pts[j-1][0],pts[j][1]-pts[j-1][1]))}
const posAt=k=>{const target=cum[S]*k/M;let j=cum.findIndex(v=>v>=target);return pts[Math.max(j,0)]};
D.nodes.forEach((n,k)=>{const [x,y]=posAt(k),c=D.colors[n];
 const ln=el('line',{x1:cx,y1:cy,x2:x,y2:y,stroke:c,'stroke-opacity':.08});
 const g=el('g',{});const w=Math.max(70,n.length*9+24)*K;
 el('rect',{x:x-w/2,y:y-24*K,width:w,height:36*K,rx:3,class:'nodebox',stroke:c},g);
 el('text',{x,y:y-8*K,'text-anchor':'middle',class:'nodetxt',fill:c},g).textContent=n;
 const sub=el('text',{x,y:y+6*K,'text-anchor':'middle',class:'nodesub'},g);
 const ring=el('circle',{cx:x,cy:y,r:0,class:'ring',stroke:c,opacity:0});
 nodeEls[n]={x,y,ln,sub,ring,c,flash:0,col:c};});
// ---- heatmap
const years=[...new Set(D.months.map(m=>m[0]))],heat=$('heat'),cells={};
heat.insertAdjacentHTML('beforeend','<span></span>'+'JFMAMJJASOND'.split('').map(c=>`<span style="text-align:center">${c}</span>`).join(''));
years.forEach(y=>{heat.insertAdjacentHTML('beforeend',`<span>${y}</span>`);for(let m=1;m<=12;m++){const i=document.createElement('i');heat.appendChild(i);cells[y+'-'+m]=i}});
function heatColor(r){const a=Math.min(Math.abs(r)/6,1)*.85+.15;return r>=0?`rgba(34,197,94,${a})`:`rgba(239,68,68,${a})`}
// ---- canvas chart
const cv=$('cv'),ctx=cv.getContext('2d');let lo=Infinity,hi=-Infinity;
D.equity.concat(D.bench).forEach(v=>{if(v<lo)lo=v;if(v>hi)hi=v});
function drawChart(i){const r=devicePixelRatio||1,w=cv.clientWidth,h=cv.clientHeight;cv.width=w*r;cv.height=h*r;ctx.setTransform(r,0,0,r,0,0);
 ctx.clearRect(0,0,w,h);const pl=62,pr=8,pt=8,pb=18,iw=w-pl-pr,ih=h-pt-pb,X=k=>pl+k/(N-1)*iw,Y=v=>pt+ih-(v-lo)/(hi-lo)*ih;
 [['validation','rgba(45,212,191,.06)'],['holdout','rgba(251,191,36,.08)']].forEach(([k,c])=>{if(!W[k])return;
  const a=D.dates.findIndex(d=>d>=W[k][0]);let b=D.dates.findIndex(d=>d>W[k][1]);if(b<0)b=N-1;if(a>=0){ctx.fillStyle=c;ctx.fillRect(X(a),pt,X(b)-X(a),ih)}});
 ctx.fillStyle='#7d8b99';ctx.font='11px ui-monospace,monospace';ctx.textAlign='right';
 for(let k=0;k<=3;k++){const v=lo+(hi-lo)*k/3,y=Y(v);ctx.strokeStyle='#1a2530';ctx.beginPath();ctx.moveTo(pl,y);ctx.lineTo(w-pr,y);ctx.stroke();ctx.fillText(fmt(v),pl-6,y+4)}
 ctx.textAlign='center';const ys=[...new Set(D.dates.map(d=>d.slice(0,4)))],st=Math.max(1,Math.ceil(ys.length/7));
 ys.filter((_,k)=>k%st===0).forEach(yr=>{const k=D.dates.findIndex(d=>d.startsWith(yr));ctx.fillText(yr,X(k),h-4)});
 const line=(arr,c,wd,dash)=>{ctx.strokeStyle=c;ctx.lineWidth=wd;ctx.setLineDash(dash);ctx.beginPath();const st=Math.max(1,Math.floor(i/1500));
  for(let k=0;k<=i;k+=st)k?ctx.lineTo(X(k),Y(arr[k])):ctx.moveTo(X(k),Y(arr[k]));ctx.lineTo(X(i),Y(arr[i]));ctx.stroke();ctx.setLineDash([])};
 line(D.bench,'#5b6670',1.4,[4,3]);line(D.equity,'#2dd4bf',2,[]);
 ctx.fillStyle='#2dd4bf';ctx.beginPath();ctx.arc(X(i),Y(D.equity[i]),3.5,0,7);ctx.fill();}
// ---- playback state
let prog=0,speed=1,playing=true,last=null,shown=-1;const DUR=24000;
const st={tr:0,win:0,peak:D.capital,dd:0,pnl:{},log:[]};
function reset(){st.tr=0;st.win=0;st.peak=D.capital;st.dd=0;st.pnl={};st.log=[];D.nodes.forEach(n=>st.pnl[n]=0);shown=-1}
function advance(to){for(let d=shown+1;d<=to;d++){const v=D.equity[d];if(v>st.peak)st.peak=v;st.dd=Math.max(st.dd,1-v/st.peak);
 (byDay.get(d)||[]).forEach(t=>{st.tr++;if(t[2]>0)st.win++;st.pnl[t[1]]+=t[2];st.log.unshift(t);if(st.log.length>8)st.log.pop();
  const ne=nodeEls[t[1]];if(ne){ne.flash=1;ne.col=t[2]>0?'var(--win)':'var(--loss)'}})}shown=to}
function seek(to){reset();advance(to)}
function paint(i){const v=D.equity[i],p=v-D.capital;$('date').textContent=D.dates[i];$('bal').textContent=fmt(v);
 $('pnl').innerHTML=`<span class="${p>=0?'up':'down'}">${p>=0?'+':'−'}${fmt(Math.abs(p))} profit (${pct(p/D.capital*100)})</span> <span style="color:var(--mut)">· buy &amp; hold ${pct((D.bench[i]/D.capital-1)*100)}</span>`;
 $('ntr').textContent=st.tr;$('wr').textContent=st.tr?Math.round(st.win/st.tr*100)+'%':'—';$('dd').textContent=(st.dd*100).toFixed(1)+'%';
 $('phase').textContent=phaseName(i)||'—';accVal.textContent=fmt(v);
 D.nodes.forEach(n=>{const ne=nodeEls[n],s=st.pnl[n]||0;ne.sub.textContent=(s>=0?'+':'−')+fmt(Math.abs(s))+(D.node_sub[n]?'':'');
  ne.ring.setAttribute('r',8+ (1-ne.flash)*26);ne.ring.setAttribute('opacity',ne.flash*.9);ne.ring.setAttribute('stroke',ne.col);
  ne.ln.setAttribute('stroke',ne.flash>0.05?ne.col:ne.c);ne.ln.setAttribute('stroke-opacity',.08+ne.flash*.6);ne.ln.setAttribute('stroke-width',1+ne.flash*2);ne.flash*=.9});
 D.months.forEach(m=>{const c=cells[m[0]+'-'+m[1]];if(!c)return;if(m[3]<=i){c.style.background=heatColor(m[2]);c.title=`${m[0]}-${String(m[1]).padStart(2,'0')}: ${pct(m[2])}`}else{c.style.background='';c.title=''}});
 $('log').innerHTML=st.log.map(t=>`<tr><td>${D.dates[t[0]]}</td><td style="color:${D.colors[t[1]]}">${t[1]}${D.node_kind==='strategy'?' · '+t[4]:''}</td><td class="${t[2]>0?'up':'down'}">${t[2]>0?'+':'−'}${fmt(Math.abs(t[2]))} (${pct(t[3])})</td></tr>`).join('')||'<tr><td colspan=3 style="color:var(--mut)">No closed trades yet</td></tr>';
 const ranked=D.nodes.slice().sort((a,b)=>(st.pnl[b]||0)-(st.pnl[a]||0)).slice(0,8),mx=Math.max(1,...ranked.map(n=>Math.abs(st.pnl[n]||0)));
 $('lb').innerHTML=ranked.map(n=>{const s=st.pnl[n]||0;return `<div class="lb"><span style="color:${D.colors[n]}">${n}</span><div class="bar"><i style="width:${Math.abs(s)/mx*100}%;background:${s>=0?'var(--win)':'var(--loss)'}"></i></div><span class="${s>=0?'up':'down'}" style="text-align:right">${s>=0?'+':'−'}${fmt(Math.abs(s))}</span></div>`}).join('');
 drawChart(i);$('scrub').value=Math.round(prog*1000)}
function frame(ts){if(last===null)last=ts;const dt=ts-last;last=ts;
 if(playing){prog=Math.min(1,prog+dt*speed/DUR);if(prog>=1){playing=false;$('play').textContent='▶ Play'}}
 const i=Math.round(prog*(N-1));if(i<shown)seek(i);else advance(i);paint(i);requestAnimationFrame(frame)}
$('play').onclick=()=>{if(prog>=1){prog=0;seek(0)}playing=!playing;$('play').textContent=playing?'❚❚ Pause':'▶ Play'};
$('restart').onclick=()=>{prog=0;seek(0);playing=true;$('play').textContent='❚❚ Pause'};
document.querySelectorAll('button[data-s]').forEach(b=>b.onclick=()=>{speed=+b.dataset.s;document.querySelectorAll('button[data-s]').forEach(x=>x.classList.toggle('on',x===b))});
$('scrub').oninput=e=>{prog=e.target.value/1000;const i=Math.round(prog*(N-1));seek(i)};
if(matchMedia('(prefers-reduced-motion: reduce)').matches){prog=1;playing=false;$('play').textContent='▶ Play'}
reset();requestAnimationFrame(frame);
</script></body></html>"""
