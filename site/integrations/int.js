// Copy buttons for the setup snippets. Nothing else runs on these pages.
document.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-copy],[data-copy-text]');
  if (!button) return;
  const text = button.dataset.copyText || button.parentElement.querySelector('code').textContent;
  try { await navigator.clipboard.writeText(text); button.textContent = 'Copied'; button.classList.add('done'); }
  catch { button.textContent = 'Press Ctrl+C'; }
  setTimeout(() => { button.textContent = 'Copy'; button.classList.remove('done'); }, 1600);
});
