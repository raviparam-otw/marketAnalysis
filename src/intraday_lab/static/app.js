const money=new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'});
const pct=v=>`${Number(v||0).toFixed(2)}%`;
const view=document.body.dataset.view;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function action(path,confirmText){
  if(confirmText&&!confirm(confirmText))return;
  const r=await fetch(path,{method:'POST'});
  if(!r.ok){
    const body=await r.json().catch(()=>({}));
    alert(body.detail||'Request failed');
    return;
  }
  await refresh();
}

document.querySelector('#start').onclick=()=>action('/api/start');
document.querySelector('#pause').onclick=()=>action('/api/pause');
document.querySelector('#resume').onclick=()=>action('/api/resume');
document.querySelector('#stop').onclick=()=>action('/api/stop','Stop taking new entries and keep managing open positions until the experiment is flat?');
document.querySelector('#kill').onclick=()=>action('/api/kill','EMERGENCY: cancel open orders and flatten all PAPER positions now?');

function setButtonState(d){
  const c=d.controls||{};
  document.querySelector('#start').disabled=!c.can_start;
  document.querySelector('#pause').disabled=!c.can_pause;
  document.querySelector('#resume').disabled=!c.can_resume;
  document.querySelector('#stop').disabled=!c.can_drain;
  document.querySelector('#kill').disabled=!c.can_flatten;
  document.querySelector('#resume').classList.toggle('hidden-control',!c.can_resume);
  document.querySelector('#pause').classList.toggle('hidden-control',!c.can_pause);
}

function renderState(d){
  const el=document.querySelector('#state');
  const state=d.state||'STOPPED';
  el.textContent=state;
  el.className=`state-badge state-${state.toLowerCase()}`;
}

function renderSession(d){
  const e=d.experiment||{};
  const account=d.account||{};
  const started=Boolean(e.session_id);
  const pnl=Number(e.account_pnl||0);
  const exposure=Number(e.global_exposure||0);
  const exposureLimit=Number(e.global_exposure_limit||0);
  const exposurePct=exposureLimit?exposure/exposureLimit*100:0;
  const health=e.last_cycle_error?'ERROR':(d.running?'HEALTHY':'IDLE');

  const cells=[
    ['Account equity',money.format(account.equity||0),'live Alpaca paper'],
    [started?'Session start':'Current balance',money.format(e.starting_equity||account.equity||0),started?'frozen baseline':'split when started'],
    ['Session P&L',money.format(pnl),started?(pnl>=0?'above baseline':'below baseline'):'not started',pnl<0?'neg':pnl>0?'pos':''],
    ['Model A allocation',money.format(e.allocation_a||0),started?'frozen 50%':'preview 50%'],
    ['Model B allocation',money.format(e.allocation_b||0),started?'frozen 50%':'preview 50%'],
    ['Gross exposure',money.format(exposure),`${exposurePct.toFixed(1)}% of risk cap`],
    ['Entry window',e.entry_window_open?'OPEN':'CLOSED',e.entry_window_open?'entries permitted by clock':'management only',e.entry_window_open?'pos':''],
    ['Engine health',health,e.last_cycle_error||`${e.cycle_count||0} cycles`,health==='ERROR'?'neg':health==='HEALTHY'?'pos':'']
  ];

  document.querySelector('#session-strip').innerHTML=cells.map(([label,value,sub,klass])=>`
    <div class="session-cell">
      <span>${esc(label)}</span>
      <strong class="${klass||''}">${esc(value)}</strong>
      <small>${esc(sub)}</small>
    </div>`).join('');

  const startedAt=e.started_at?new Date(e.started_at).toLocaleString():'Not started';
  const lastCycle=e.last_cycle_at?new Date(e.last_cycle_at).toLocaleTimeString():'—';
  const c=d.model_c||{};
  document.querySelector('#session-meta').innerHTML=`
    <span><b>Session</b> ${esc(e.session_id||'preview')}</span>
    <span><b>Started</b> ${esc(startedAt)}</span>
    <span><b>Split</b> ${esc(e.split||'50 / 50')}</span>
    <span><b>Exposure cap</b> ${money.format(exposureLimit)}</span>
    <span><b>Model C</b> ${esc(c.configured?'SHADOW READY':'SHADOW SETUP')}</span>
    <span><b>Last cycle</b> ${esc(lastCycle)}</span>
    <span><b>Market time</b> ${e.market_time?new Date(e.market_time).toLocaleTimeString():'—'}</span>
  `;
}

function signalRows(signals){
  const rows=[...signals]
    .sort((a,b)=>(b.score||b.relative_volume||0)-(a.score||a.relative_volume||0))
    .slice(0,12);
  if(!rows.length)return '<div class="empty">Waiting for scanner activity.</div>';
  return `<table>
    <thead><tr><th>Symbol</th><th>State</th><th>Price</th><th>RVOL</th><th>Gap</th><th>Change</th><th>Setup / reason</th></tr></thead>
    <tbody>${rows.map(s=>`<tr>
      <td class="symbol">${esc(s.symbol)}</td>
      <td><span class="pill ${s.decision==='BUY'?'buy':''}">${esc(s.decision)}</span></td>
      <td>${money.format(s.price||0)}</td>
      <td>${Number(s.relative_volume||0).toFixed(2)}×</td>
      <td>${pct(s.gap_pct)}</td>
      <td class="${Number(s.change_pct||0)<0?'neg':Number(s.change_pct||0)>0?'pos':''}">${pct(s.change_pct)}</td>
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
  if(value==='risk')return 'risk';
  if(value==='control')return 'control-level';
  return 'info';
}

function logRows(logs){
  if(!logs.length)return '<div class="console-empty">Waiting for live engine activity…</div>';
  const rows=[...logs].sort((a,b)=>new Date(b.time)-new Date(a.time)).slice(0,100);
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

function positionBlock(open){
  if(!open.length)return '<div class="position-flat"><span class="flat-dot"></span> FLAT · no open position</div>';
  return open.map(p=>`
    <div class="position-row">
      <div><span>Symbol</span><strong class="symbol">${esc(p.symbol)}</strong></div>
      <div><span>Entry</span><strong>${money.format(p.entry)}</strong></div>
      <div><span>Current</span><strong>${money.format(p.current)}</strong></div>
      <div><span>Qty</span><strong>${Number(p.qty).toFixed(2)}</strong></div>
      <div><span>Stop</span><strong>${money.format(p.stop)}</strong></div>
      <div><span>Exposure</span><strong>${money.format(p.exposure)}</strong></div>
      <div><span>Unrealized</span><strong class="${p.unrealized_pl<0?'neg':p.unrealized_pl>0?'pos':''}">${money.format(p.unrealized_pl)}</strong></div>
      <div><span>Status</span><strong>${p.exit_pending?'EXIT PENDING':'MANAGED'}</strong></div>
    </div>`).join('');
}

function modelCard(m,d){
  const totalPnl=Number(m.realized_pl||0)+Number(m.unrealized_pl||0);
  const stats=m.stats||{};
  const risk=m.risk||{};
  return `
    <div class="model-head">
      <div class="model-title-wrap">
        <span class="role-tag ${m.role==='CONTROL'?'control':'challenger'}">${esc(m.role)}</span>
        <div>
          <span class="model-kicker">MODEL ${esc(m.name)}</span>
          <h1>${esc(m.label)}</h1>
        </div>
      </div>
      <span class="live-dot"><i></i> ${d.running?'LIVE':'READY'}</span>
    </div>

    <div class="metrics pro-metrics">
      <div><span>Allocation</span><strong>${money.format(m.capital)}</strong></div>
      <div><span>Model equity</span><strong>${money.format(m.equity)}</strong></div>
      <div><span>Total P&amp;L</span><strong class="${totalPnl<0?'neg':totalPnl>0?'pos':''}">${money.format(totalPnl)}</strong></div>
      <div><span>Return</span><strong class="${m.return_pct<0?'neg':m.return_pct>0?'pos':''}">${pct(m.return_pct)}</strong></div>
      <div><span>Win rate</span><strong>${pct(stats.win_rate_pct)}</strong><small>${stats.wins||0}W / ${stats.losses||0}L</small></div>
      <div><span>Closed trades</span><strong>${stats.closed_trades||0}</strong><small>max ${risk.max_trades||0}/day</small></div>
      <div><span>Open exposure</span><strong>${money.format(m.open_exposure||0)}</strong></div>
      <div><span>Drawdown</span><strong class="${Number(m.drawdown_pct||0)>0?'neg':''}">${pct(m.drawdown_pct)}</strong></div>
    </div>

    <div class="risk-rail">
      <div><span>Risk / trade</span><b>${money.format(risk.risk_per_trade||0)}</b><small>${pct(risk.risk_per_trade_pct)}</small></div>
      <div><span>Max position</span><b>${money.format(risk.max_position||0)}</b><small>${pct(risk.max_position_pct)}</small></div>
      <div><span>Daily stop</span><b>${money.format(risk.daily_loss_limit||0)}</b><small>${pct(risk.daily_loss_pct)}</small></div>
      <div><span>Loss room left</span><b>${money.format(risk.remaining_daily_loss||0)}</b></div>
      <div><span>Consecutive loss cap</span><b>${risk.consecutive_loss_limit||0}</b><small>current ${stats.consecutive_losses||0}</small></div>
    </div>

    <section class="position-panel">
      <div class="section-title"><h2>Position monitor</h2><span>broker marks</span></div>
      ${positionBlock(m.open_positions||[])}
    </section>

    <section class="subpanel scanner-panel">
      <div class="section-title">
        <h2>Scanner</h2>
        <span>${(m.signals||[]).length} symbols observed</span>
      </div>
      <div class="table-wrap scanner-table">${signalRows(m.signals||[])}</div>
    </section>

    <section class="subpanel console">
      <div class="section-title console-title">
        <div class="console-heading"><span class="terminal-icon">&gt;_</span><h2>Live activity</h2></div>
        <span class="latest-label"><i></i> LATEST FIRST · 3s refresh</span>
      </div>
      <div class="console-body">${logRows(m.logs||[])}</div>
    </section>`;
}

function tradesTable(rows){
  if(!rows.length)return '<div class="empty">No orders yet.</div>';
  const sorted=[...rows].sort((a,b)=>new Date(b.filled_at||b.recorded_at)-new Date(a.filled_at||a.recorded_at));
  return `<div class="table-wrap"><table>
    <thead><tr><th>Time</th><th>Trade ID</th><th>Symbol</th><th>Side</th><th>Status</th><th>Fill</th><th>Qty</th><th>Reason</th><th>P&amp;L</th></tr></thead>
    <tbody>${sorted.map(t=>`<tr>
      <td>${esc((t.filled_at||t.recorded_at||'').replace('T',' ').slice(0,19))}</td>
      <td class="mono">${esc(t.trade_id||'—')}</td>
      <td class="symbol">${esc(t.symbol)}</td>
      <td><span class="pill ${t.side==='BUY'?'buy':'sell'}">${esc(t.side)}</span></td>
      <td>${esc(t.status)}</td>
      <td>${t.filled_avg_price?money.format(t.filled_avg_price):'—'}</td>
      <td>${t.filled_qty??t.requested_qty??'—'}</td>
      <td>${esc(t.reason)}</td>
      <td class="${Number(t.realized_pl||0)<0?'neg':Number(t.realized_pl||0)>0?'pos':''}">${t.realized_pl==null?'—':money.format(t.realized_pl)}</td>
    </tr>`).join('')}</tbody>
  </table></div>`;
}

function tradeSummaryCard(m){
  const s=m.stats||{};
  return `<div class="performance-card">
    <div class="performance-head"><span class="role-tag ${m.role==='CONTROL'?'control':'challenger'}">${esc(m.role)}</span><b>Model ${esc(m.name)}</b></div>
    <div class="performance-grid">
      <div><span>Realized</span><strong class="${m.realized_pl<0?'neg':m.realized_pl>0?'pos':''}">${money.format(m.realized_pl||0)}</strong></div>
      <div><span>Win rate</span><strong>${pct(s.win_rate_pct)}</strong></div>
      <div><span>Avg trade</span><strong>${money.format(s.avg_trade||0)}</strong></div>
      <div><span>Profit factor</span><strong>${s.profit_factor??'—'}</strong></div>
      <div><span>Best</span><strong class="pos">${money.format(s.best_trade||0)}</strong></div>
      <div><span>Worst</span><strong class="neg">${money.format(s.worst_trade||0)}</strong></div>
    </div>
  </div>`;
}

async function refresh(){
  try{
    const r=await fetch('/api/status');
    if(!r.ok)throw new Error((await r.json()).detail);
    const d=await r.json();

    renderState(d);
    setButtonState(d);
    renderSession(d);

    if(view==='compare'){
      document.querySelector('#compare-view').classList.remove('hidden');
      document.querySelectorAll('.model-panel[data-model]').forEach(el=>{
        el.innerHTML=modelCard(d.models[el.dataset.model],d);
      });
    }else if(view==='A'||view==='B'){
      document.querySelector('#compare-view').classList.add('hidden');
      document.querySelector('#single-view').classList.remove('hidden');
      document.querySelector('#single-model').innerHTML=modelCard(d.models[view],d);
    }else if(view==='trades'){
      document.querySelector('#compare-view').classList.add('hidden');
      document.querySelector('#trades-view').classList.remove('hidden');
      const all=d.trades||[];
      document.querySelector('#trades-a').innerHTML=tradesTable(all.filter(t=>t.model==='A'));
      document.querySelector('#trades-b').innerHTML=tradesTable(all.filter(t=>t.model==='B'));
      document.querySelector('#trade-total').textContent=`${all.length} orders today`;
      document.querySelector('#trade-summary').innerHTML=tradeSummaryCard(d.models.A)+tradeSummaryCard(d.models.B);
    }
  }catch(e){
    const state=document.querySelector('#state');
    state.textContent='ERROR';
    state.className='state-badge state-error';
    document.querySelector('#session-meta').innerHTML=`<span class="neg">${esc(e.message)}</span>`;
  }
}

refresh();
setInterval(refresh,3000);
