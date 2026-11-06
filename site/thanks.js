let key = '';
const session = new URLSearchParams(location.search).get('session_id');
async function claim() {
  document.getElementById('retry').hidden = true;
  try {
    if (!session) throw new Error('This page needs your Stripe checkout return link. Start from the pricing page.');
    const r = await fetch('/api/claim',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:session})});
    const d = await r.json();
    if (r.status === 202) throw new Error('Your payment is still processing. Check again in a moment.');
    if (!r.ok || !d.key) throw new Error(d.error || 'We could not verify this checkout. Please contact support.');
    key = d.key;
    document.getElementById('title').textContent = 'Your API is ready.';
    document.getElementById('message').textContent = 'Access verified. Save your key and start building.';
    document.getElementById('api-key').textContent = '••••••••••••••••••••••••';
    document.getElementById('dashboard-link').href = '/dashboard.html?session_id=' + encodeURIComponent(session);
    document.getElementById('summary').textContent = `${d.plan[0].toUpperCase()+d.plan.slice(1)} · ${d.credits_left.toLocaleString()} credits available`;
    document.getElementById('access').hidden = false;
  } catch(e) {
    document.getElementById('message').textContent = e.message;
    document.getElementById('retry').hidden = !session;
  }
}
document.getElementById('retry').onclick = claim;
document.getElementById('reveal').onclick = () => {
  const shown = document.getElementById('reveal').getAttribute('aria-pressed') === 'true';
  document.getElementById('api-key').textContent = shown ? '••••••••••••••••••••••••' : key;
  document.getElementById('reveal').setAttribute('aria-pressed', String(!shown));
  document.getElementById('reveal').textContent = shown ? 'Reveal API key' : 'Hide API key';
};
document.getElementById('copy').onclick = async () => {try{await navigator.clipboard.writeText(key);document.getElementById('message').textContent='API key copied.';}catch{document.getElementById('message').textContent='Select your key above and copy it manually.';}};
document.getElementById('download').onclick = () => {const url=URL.createObjectURL(new Blob([`APEX FLOW WEB SCRAPER\nAPI base: https://webscraper.apexflowlabs.com/v1\nAPI key: ${key}\nDocs: https://webscraper.apexflowlabs.com/docs.html\nKeep this file private.\n`],{type:'text/plain'}));const a=document.createElement('a');a.href=url;a.download='apex-web-scraper-access.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
claim();
