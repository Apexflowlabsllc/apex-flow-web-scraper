const trialForm = document.getElementById('trial');
trialForm.addEventListener('submit', async event => {
  event.preventDefault();
  const button = trialForm.querySelector('button');
  const message = document.getElementById('message');
  button.disabled = true;
  message.textContent = 'Preparing your trial…';
  try {
    const response = await fetch('/api/trial', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({terms: document.getElementById('terms').checked}),
    });
    const data = await response.json();
    if (!response.ok || !/^trial_[a-f0-9]{32}$/.test(data.session_id)) {
      throw new Error(data.error || 'Your trial could not be opened. Please try again.');
    }
    location.assign('/thanks.html?session_id=' + encodeURIComponent(data.session_id));
  } catch (error) {
    message.textContent = error.message;
    button.disabled = false;
  }
});
