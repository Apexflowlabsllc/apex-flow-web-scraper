const trialForm = document.getElementById('trial');
const message = document.getElementById('message');
/* A trial opened earlier in this browser can always be reopened: the key page link is kept in this browser only. */
let saved = '';
try { saved = localStorage.getItem('apex_scraper_session') || ''; } catch {}
if (!/^[A-Za-z0-9_]{16,160}$/.test(saved)) saved = '';
function offer(text, withPass) {
  message.textContent = text + ' ';
  const add = (label, href) => { const a = document.createElement('a'); a.href = href; a.textContent = label; message.append(a, ' '); };
  if (saved) add('Open my key page →', '/thanks.html?session_id=' + encodeURIComponent(saved));
  if (withPass) add('Or get the $5 pass: 35,000 credits, no subscription →', '/#pricing');
}
if (saved) offer('You already have a key from this browser.', false);
trialForm.addEventListener('submit', async event => {
  event.preventDefault();
  const button = trialForm.querySelector('button');
  button.disabled = true;
  message.textContent = 'Preparing your trial…';
  try {
    const response = await fetch('/api/trial', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({terms: document.getElementById('terms').checked}),
    });
    const data = await response.json();
    if (response.status === 409) { offer(data.error || 'A trial already exists on this network.', true); button.disabled = false; return; }
    if (!response.ok || !/^trial_[a-f0-9]{32}$/.test(data.session_id)) {
      throw new Error(data.error || 'Your trial could not be opened. Please try again.');
    }
    location.assign('/thanks.html?session_id=' + encodeURIComponent(data.session_id));
  } catch (error) {
    message.textContent = error.message;
    button.disabled = false;
  }
});
