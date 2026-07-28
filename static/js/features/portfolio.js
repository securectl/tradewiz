// Portfolio Advisor — import holdings (Fidelity/Schwab CSV) + AI cut/add analysis.
// Admin-gated per user (server enforces; the tab is only revealed when enabled).

function _pfEsc(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function _pfAct(a){return a==='ADD'?'var(--accent-green)':a==='SELL'?'var(--accent-red)':a==='TRIM'?'var(--accent-orange)':'var(--text-secondary)';}
function _pfNum(n,pre){return (n==null)?'—':(pre||'')+Number(n).toLocaleString(undefined,{maximumFractionDigits:2});}
function _pfPct(n){return (n==null)?'—':(n>=0?'+':'')+Number(n).toFixed(1)+'%';}

function initPortfolio(){ loadPortfolio(); }

async function loadPortfolio(){
  const body=document.getElementById('pf-body'); if(!body) return;
  try{
    const r=await fetch('/api/portfolio');
    if(r.status===403){ body.innerHTML='<div style="color:var(--text-secondary);padding:24px;">Portfolio Advisor is not enabled for your account.</div>'; return; }
    const d=await r.json();
    renderPortfolio(d.holdings||[], d.analysis);
  }catch(e){ /* ignore */ }
}

async function pfImport(){
  const csv=((document.getElementById('pf-csv')||{}).value||'');
  const msg=document.getElementById('pf-import-msg');
  if(!csv.trim()){ if(msg){msg.style.color='var(--accent-red)';msg.textContent='Paste your CSV first.';} return; }
  if(msg){msg.style.color='var(--text-secondary)';msg.textContent='Importing…';}
  try{
    const r=await fetch('/api/portfolio/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csv:csv})});
    const d=await r.json();
    if(d.ok){ if(msg){msg.style.color='var(--accent-green)';msg.textContent='Imported '+d.imported+' holdings ('+(d.source||'csv')+'). Now click Analyze.';} loadPortfolio(); }
    else if(msg){ msg.style.color='var(--accent-red)'; msg.textContent=d.error||'Import failed.'; }
  }catch(e){ if(msg){msg.style.color='var(--accent-red)';msg.textContent='Request failed.';} }
}

async function pfAnalyze(){
  const btn=document.getElementById('pf-analyze-btn'), body=document.getElementById('pf-body');
  if(btn){btn.disabled=true; var old=btn.textContent; btn.textContent='Analyzing…';}
  try{
    const r=await fetch('/api/portfolio/analyze',{method:'POST'});
    const d=await r.json();
    if(d.ok){ renderPortfolio(null, d.analysis); }
    else if(body){ body.insertAdjacentHTML('afterbegin','<div style="color:var(--accent-red);padding:10px;">'+_pfEsc(d.error||'Analysis failed.')+'</div>'); }
  }catch(e){ /* ignore */ }
  if(btn){btn.disabled=false; btn.textContent=old;}
}

function renderPortfolio(holdings, analysis){
  const body=document.getElementById('pf-body'); if(!body) return;
  const a = analysis;
  if(!a && (!holdings||!holdings.length)){
    body.innerHTML='<div style="color:var(--text-secondary);padding:24px;">No holdings yet — paste your Fidelity/Schwab CSV above and Import.</div>';
    return;
  }
  const rows = a ? a.holdings : holdings.map(h=>({symbol:h.symbol, shares:h.shares, cost_basis:h.cost_basis}));
  const s = a && a.summary;
  let html='';

  if(s){
    const conc = s.concentration_flag ? '<span style="color:var(--accent-orange)">⚠ '+s.top_position+' is '+s.top_position_pct+'% of the book</span>' : (s.top_position?('Top: '+s.top_position+' '+s.top_position_pct+'%'):'');
    html += '<div style="display:flex;gap:14px;flex-wrap:wrap;margin:8px 0 16px;">'+
      _pfTile('Holdings', s.holdings)+
      _pfTile('Total value', _pfNum(s.total_value,'$'))+
      _pfTile('Cut', (s.cut||[]).join(', ')||'—', 'var(--accent-red)')+
      _pfTile('Add', (s.add||[]).join(', ')||'—', 'var(--accent-green)')+
      '</div>'+ (conc?('<div style="font-size:12px;margin-bottom:14px;color:var(--text-secondary)">'+conc+'</div>'):'');
  }

  const note = a && a.advisor_note;
  if(note){
    html += '<div style="background:var(--bg-card);border:1px solid var(--border-color);border-left:3px solid var(--accent-blue);border-radius:8px;padding:16px;margin-bottom:16px;">'+
      '<div style="font-weight:700;color:var(--text-bright);margin-bottom:8px;">Advisor read <span style="font-weight:400;color:var(--text-secondary);font-size:12px;">(25-yr perspective)</span></div>'+
      (note.overview?('<p style="font-size:13.5px;margin-bottom:8px;">'+_pfEsc(note.overview)+'</p>'):'')+
      (note.where_to_cut?('<p style="font-size:13px;margin-bottom:6px;"><b style="color:var(--accent-red)">Where to cut:</b> '+_pfEsc(note.where_to_cut)+'</p>'):'')+
      (note.where_to_add?('<p style="font-size:13px;margin-bottom:6px;"><b style="color:var(--accent-green)">Where to add:</b> '+_pfEsc(note.where_to_add)+'</p>'):'')+
      (note.risk?('<p style="font-size:13px;color:var(--text-secondary);"><b>Risk:</b> '+_pfEsc(note.risk)+'</p>'):'')+
      '</div>';
  }

  html += '<table style="width:100%;border-collapse:collapse;font-size:13px;"><thead><tr style="text-align:left;color:var(--text-secondary);">'+
    '<th style="padding:8px 6px;">Symbol</th><th>Shares</th><th>Value</th><th>P/L</th><th>Trend</th><th>Money flow</th><th>RSI</th><th>Action</th><th>Why</th></tr></thead><tbody>';
  rows.forEach(r=>{
    const act=r.action||'—';
    html += '<tr style="border-top:1px solid var(--border-subtle);">'+
      '<td style="padding:8px 6px;font-weight:700;">'+_pfEsc(r.symbol)+'</td>'+
      '<td>'+_pfNum(r.shares)+'</td>'+
      '<td>'+_pfNum(r.value,'$')+'</td>'+
      '<td style="color:'+((r.pnl_pct||0)>=0?'var(--accent-green)':'var(--accent-red)')+'">'+_pfPct(r.pnl_pct)+'</td>'+
      '<td>'+_pfEsc(r.trend||'—')+'</td>'+
      '<td>'+_pfEsc(r.mf_label||'—')+'</td>'+
      '<td>'+(r.rsi==null?'—':r.rsi)+'</td>'+
      '<td><span style="font-weight:800;color:'+_pfAct(act)+'">'+_pfEsc(act)+'</span></td>'+
      '<td style="color:var(--text-secondary);max-width:280px;">'+_pfEsc(r.reason||'')+'</td>'+
      '</tr>';
  });
  html += '</tbody></table>';
  if(a && a.generated_at){ html += '<div style="font-size:11px;color:var(--text-secondary);margin-top:10px;">Educational analysis — not personalized investment advice. Generated '+_pfEsc(String(a.generated_at).slice(0,19).replace("T"," "))+'.</div>'; }
  body.innerHTML=html;
}

function _pfTile(label,val,color){
  return '<div style="background:var(--bg-card);border:1px solid var(--border-color);border-radius:8px;padding:10px 14px;min-width:110px;">'+
    '<div style="font-size:11px;color:var(--text-secondary);text-transform:uppercase;letter-spacing:.4px;">'+label+'</div>'+
    '<div style="font-size:15px;font-weight:700;color:'+(color||'var(--text-bright)')+';">'+_pfEsc(val==null?'—':val)+'</div></div>';
}

// Reveal the Portfolio tab for entitled users (called on app load).
async function revealPortfolioTab(){
  try{
    const r=await fetch('/api/portfolio/access'); if(!r.ok) return;
    const d=await r.json();
    const btn=document.getElementById('tab-portfolio');
    if(btn && d.enabled) btn.style.display='';
  }catch(e){ /* ignore */ }
}
