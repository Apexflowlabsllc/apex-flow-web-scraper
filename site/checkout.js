fetch('/api/event',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({event:'page_view'}),keepalive:true}).catch(()=>{});
document.querySelectorAll('[data-plan]').forEach(button => button.addEventListener('click', async () => {
  const buttons = document.querySelectorAll('[data-plan]');
  buttons.forEach(b => b.disabled = true);
  document.querySelectorAll('.checkout-feedback').forEach(e => e.textContent = 'Opening secure checkout…');
  try {
    const response = await fetch('/api/checkout', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({plan:button.dataset.plan})});
    const data = await response.json();
    if (!response.ok || !data.url) throw new Error(data.error || 'Checkout is unavailable. Please retry.');
    const url = new URL(data.url);
    if (url.hostname !== 'checkout.stripe.com') throw new Error('Checkout could not be verified.');
    location.assign(url.href);
  } catch (error) {
    document.querySelectorAll('.checkout-feedback').forEach(e => e.textContent = error.message);
    buttons.forEach(b => b.disabled = false);
  }
}));
