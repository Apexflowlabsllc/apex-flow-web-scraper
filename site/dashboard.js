let apiKey='', markdown='', lastResult=null;
const $=id=>document.getElementById(id);
async function call(path, body) {const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:{Authorization:'Bearer '+apiKey,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.error||'Request failed. Check your API key and try again.');return d;}
async function refresh(){const d=await call('/api/account');$('plan').textContent=d.plan+' · '+(d.active?'Active':'Inactive');$('balance').textContent=d.credits_left.toLocaleString()+' credits';$('period').textContent=d.plan==='free'?'Your one-time free trial balance':d.active?'Paid access through '+new Date(d.period_end*1000).toLocaleDateString():'Open billing to review your subscription.';$('billing').textContent=['free','pass'].includes(d.plan)?'View plans and buy credits →':'Manage billing →';$('account').hidden=false;}
$('login').onsubmit=async e=>{e.preventDefault();apiKey=$('key').value.trim();try{await refresh();$('key').value='';$('login').hidden=true;$('message').textContent='Dashboard connected.';}catch(e){apiKey='';$('message').textContent=e.message;}};
$('logout').onclick=()=>{apiKey='';markdown='';lastResult=null;$('tools-output').textContent='';$('markdown').textContent='';$('account').hidden=true;$('login').hidden=false;$('message').textContent='API key cleared from this tab.';};
$('billing').onclick=async()=>{try{const d=await call('/api/portal',{});const u=new URL(d.url);if(!['billing.stripe.com','webscraper.apexflowlabs.com'].includes(u.hostname))throw new Error('Billing link could not be verified.');location.assign(u.href);}catch(e){$('message').textContent=e.message;}};
$('scrape').onsubmit=async e=>{e.preventDefault();$('run').disabled=true;$('message').textContent='Fetching and cleaning your page…';try{const d=await call('/v1/scrape',{url:$('url').value,render:$('render').value});lastResult=d.result;markdown=d.result.markdown||d.result.text||'';$('markdown').textContent=markdown;$('result').hidden=false;$('message').textContent=`Ready. ${d.credits_used} credit${d.credits_used===1?'':'s'} used.`;await refresh();}catch(e){$('message').textContent=e.message;}finally{$('run').disabled=false;}};
$('save').onclick=()=>{const u=URL.createObjectURL(new Blob([markdown],{type:'text/markdown'}));const a=document.createElement('a');a.href=u;a.download='scraped-page.md';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);};

$('save-json').onclick=()=>{if(!lastResult)return;const u=URL.createObjectURL(new Blob([JSON.stringify(lastResult,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=u;a.download='scraped-page.json';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);};
async function tool(form,path,body){const b=form.querySelector('button');b.disabled=true;$('message').textContent='Working…';try{const data=await call(path,body);$('tools-output').textContent=JSON.stringify(data,null,2);$('tools-result').hidden=false;$('message').textContent='Results ready.';await refresh();}catch(e){$('message').textContent=e.message;}finally{b.disabled=false;}}
$('map-form').onsubmit=e=>{e.preventDefault();tool(e.currentTarget,'/v1/map',{url:$('map-url').value,limit:1000});};
$('search-form').onsubmit=e=>{e.preventDefault();tool(e.currentTarget,'/v1/search',{query:$('query').value});};
$('batch-form').onsubmit=e=>{e.preventDefault();const urls=$('batch-urls').value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);if(urls.length>10){$('message').textContent='Use up to 10 URLs per batch.';return;}tool(e.currentTarget,'/v1/batch',{urls,render:'never'});};

// The HttpOnly checkout cookie proves browser ownership. Never put the API key
// in a URL, browser storage, or a form field during the checkout handoff.
async function connectCheckout() {
  const session = new URLSearchParams(location.search).get('session_id');
  if (!session) return;
  history.replaceState(null, '', location.pathname);
  $('message').textContent = 'Connecting your verified checkout…';
  try {
    if (!/^(?:cs_(?:test|live)_[A-Za-z0-9]+|trial_[a-f0-9]{32})$/.test(session)) throw new Error('Invalid checkout return link.');
    const data = await call('/api/claim', {session_id:session});
    if (!data.key) throw new Error('Checkout is still processing. Return to your checkout confirmation page and retry.');
    apiKey = data.key;
    await refresh();
    $('login').hidden = true;
    $('message').textContent = 'Dashboard connected. Try your first URL below.';
  } catch (error) {
    apiKey = '';
    $('message').textContent = error.message;
  }
}
connectCheckout();
