const money=new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'});
const view=document.body.dataset.view;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function action(path,confirmText){
  if(confirmText&&!confirm(confirmText))return;
  const r=await fetch(path,{method:'POST'});
  if(!r.ok){alert((await r.json()).detail||'Request failed');return;}
  await refresh();
}

document.querySelector('#start').onclick=()=>action('/api/start');
document.querySelector('#stop').onclick=()=>action('/api/stop');
document.querySelector('#kill').onclick=()=>action('/api/kill','Cancel orders and close every PAPER position?');

function signalRows(signals){
  const rows=[...signals]
    .sort((a,b)=>(b.score||b.relative_volume||0)-(a.score||a.relative_volume||0))
    .slice(0,10);
  if(!rows.length)return '<div class="empty">Waiting for scanner activity.</div>';
  return `<table>
    <thead><tr><th>Symbol</th><th>State</th><th>Price</th><th>RVOL</th><th>Setup / reason</th></tr></thead>
    <tbody>${rows.map(s=>`<tr>
      <td class="symbol">${esc(s.symbol)}</td>
      <td><span class="pill ${s.decision==='BUY'?'buy':''}">${esc(s.decision)}</span></td>
      <td>${money.format(s.price||0)}</td>
      <td>${Number(s.relative_volume||0).toFixed(2)}×</td>
      <td>${esc(s.reason)}</td>
    </tr>`).join('')}</tbody>
  </table>`;
}

function levelClass(level){
  const value=String(level||'INFO').toLowerCase();
  if(value==='trade')return 'trade';
  if(value==='error')return 'error-level';
  if(value==='warn'||value==='warning')return 'warn';
  if(value==='scan')return 'scan';
  return 'info';
}

function logRows(logs){
  if(!logs.length)return '<div class="console-empty">Waiting for live engine activity…</div>';
  const rows=[...logs]
    .sort((a,b)=>new Date(b.time)-new Date(a.time))
    .slice(0,80);

  return rows.map((l,index)=>{
    const details=Object.keys(l.details||{}).length?JSON.stringify(l.details):'';
    return `<div class="feed-row ${index===0?'newest':''}">
      <span class="feed-marker"></span>
      <time>${new Date(l.time).toLocaleTimeString()}</time>
      <span class="feed-level ${levelClass(l.level)}">${esc(l.level)}</span>
      <span class="feed-message">${esc(l.message)}</span>
      <code>${esc(details)}</code>
    </div>`;
  }).join('');
}

function modelCard(m){
  const pnl=Number(m.realized_pl||0);
  const open=m.open_positions||[];
  return `
    <div class="model-head">
      <div>
        <span class="model-kicker">MODEL ${esc(m.name)}</span>
        <h1>${esc(m.label)}</h1>
      </div>
      <span class="live-dot"><i></i> LIVE VIEW</span>
    </div>

    <div class="metrics">
      <div><span>Virtual capital</span><strong>${money.format(m.capital)}</strong></div>
      <div><span>Model equity</span><strong>${money.format(m.equity)}</strong></div>
      <div><span>Realized P&amp;L</span><strong class="${pnl<0?'neg':pnl>0?'pos':''}">${money.format(pnl)}</strong></div>
      <div><span>Open position</span><strong>${open.length?esc(open.map(p=>p.symbol).join(', ')):'None'}</strong></div>
    </div>

    <section class="subpanel scanner-panel">
      <div class="section-title">
        <h2>Scanner</h2>
        <span>${(m.signals||[]).length} symbols observed</span>
      </div>
      <div class="table-wrap scanner-table">${signalRows(m.signals||[])}</div>
    </section>

    <section class="subpanel console">
      <div class="section-title console-title">
        <div class="console-heading">
          <span class="terminal-icon">&gt;_</span>
          <h2>Live activity</h2>
        </div>
        <span class="latest-label"><i></i> LATEST FIRST · 3s refresh</span>
      </div>
      <div class="console-body">${logRows(m.logs||[])}</div>
    </section>`;
}

function tradesTable(rows){
  if(!rows.length)return '<div class="empty">No trades yet.</div>';
  const sorted=[...rows].sort((a,b)=>new Date(b.filled_at||b.recorded_at)-new Date(a.filled_at||a.recorded_at));
  return `<div class="table-wrap"><table>
    <thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Fill</th><th>Qty</th><th>Reason</th><th>P&amp;L</th></tr></thead>
    <tbody>${sorted.map(t=>`<tr>
      <td>${esc((t.filled_at||t.recorded_at||'').replace('T',' ').slice(0,19))}</td>
      <td class="symbol">${esc(t.symbol)}</td>
      <td><span class="pill ${t.side==='BUY'?'buy':'sell'}">${esc(t.side)}</span></td>
      <td>${t.filled_avg_price?money.format(t.filled_avg_price):'—'}</td>
      <td>${t.filled_qty??t.requested_qty??'—'}</td>
      <td>${esc(t.reason)}</td>
      <td class="${Number(t.realized_pl||0)<0?'neg':Number(t.realized_pl||0)>0?'pos':''}">${t.realized_pl==null?'—':money.format(t.realized_pl)}</td>
    </tr>`).join('')}</tbody>
  </table></div>`;
}

async function refresh(){
  try{
    const r=await fetch('/api/status');
    if(!r.ok)throw new Error((await r.json()).detail);
    const d=await r.json();

    const state=document.querySelector('#state');
    state.textContent=d.running?'● Both models running':'○ Stopped';
    state.classList.toggle('running',Boolean(d.running));

    if(view==='compare'){
      document.querySelector('#compare-view').classList.remove('hidden');
      document.querySelectorAll('.model-panel[data-model]').forEach(el=>{
        el.innerHTML=modelCard(d.models[el.dataset.model]);
      });
    }else if(view==='A'||view==='B'){
      document.querySelector('#compare-view').classList.add('hidden');
      document.querySelector('#single-view').classList.remove('hidden');
      document.querySelector('#single-model').innerHTML=modelCard(d.models[view]);
    }else if(view==='trades'){
      document.querySelector('#compare-view').classList.add('hidden');
      document.querySelector('#trades-view').classList.remove('hidden');
      const all=d.trades||[];
      document.querySelector('#trades-a').innerHTML=tradesTable(all.filter(t=>t.model==='A'));
      document.querySelector('#trades-b').innerHTML=tradesTable(all.filter(t=>t.model==='B'));
      document.querySelector('#trade-total').textContent=`${all.length} orders today`;
    }
  }catch(e){
    document.querySelector('#state').textContent=e.message;
  }
}

refresh();
setInterval(refresh,3000);
