const money = new Intl.NumberFormat('en-US',{style:'currency',currency:'USD'});
async function action(path, confirmText){
  if(confirmText && !confirm(confirmText)) return;
  const response=await fetch(path,{method:'POST'});
  if(!response.ok) alert((await response.json()).detail || 'Request failed');
  await refresh();
}
document.querySelector('#start').onclick=()=>action('/api/start');
document.querySelector('#stop').onclick=()=>action('/api/stop');
document.querySelector('#kill').onclick=()=>action('/api/kill','Cancel orders and close all PAPER positions?');
async function refresh(){
  try{
    const response=await fetch('/api/status');
    if(!response.ok) throw new Error((await response.json()).detail);
    const data=await response.json(), account=data.account||{}, limits=data.limits||{};
    document.querySelector('#state').textContent=data.running?'Engine running':'Engine stopped';
    document.querySelector('#equity').textContent=account.equity==null?'—':money.format(account.equity);
    document.querySelector('#cash').textContent=account.cash==null?'—':money.format(account.cash);
    document.querySelector('#target').textContent=money.format(limits.target_equity||0);
    document.querySelector('#exposure').textContent=money.format(limits.max_exposure||0);
    document.querySelector('#signals').innerHTML=data.signals.length?data.signals.map(s=>`<tr><td>${s.symbol}</td><td>${s.decision}</td><td>${money.format(s.price)}</td><td>${money.format(s.vwap)}</td><td>${s.relative_volume.toFixed(2)}×</td><td>${s.reason}</td></tr>`).join(''):'<tr><td colspan="6">No scan yet.</td></tr>';
    document.querySelector('#logs').innerHTML=data.logs.length?data.logs.map(l=>`<div class="log"><span>${new Date(l.time).toLocaleTimeString()}</span><strong>${l.level}</strong><span>${l.message}</span></div>`).join(''):'<p>Waiting for activity.</p>';
  }catch(error){document.querySelector('#state').textContent=error.message;}
}
refresh(); setInterval(refresh,5000);
