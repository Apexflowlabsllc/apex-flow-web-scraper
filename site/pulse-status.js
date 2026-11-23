(() => {
  'use strict';
  // Same-origin on the main site (no CORS); absolute only on the sibling hosts.
  const API = (location.hostname.startsWith('www.') || location.hostname === 'localhost') ? '/api/v1/pulse' : 'https://www.apexflowlabs.com/api/v1/pulse';
  // Keep these IDs aligned with Pulse tenant IDs (not display names). Several
  // properties have intentional legacy IDs: `rainbo`, `spiker`, and
  // `lukas-deans`. Unknown hosts stay unmapped instead of showing Labs data.
  const TENANT_BY_HOST = {
    'apexflowaivideogenerator.com': 'apex-flow-ai',
    'rainbo.apexflowlabs.com': 'rainbo',
    'audio.apexflowlabs.com': 'apex-flow-audio',
    '3d.apexflowlabs.com': 'apex-flow-3d-studio',
    'digital.apexflowlabs.com': 'apex-flow-digital',
    'scheduler.apexflowlabs.com': 'apex-flow-scheduler',
    'pulse.apexflowlabs.com': 'apex-flow-pulse',
    'revenue.apexflowlabs.com': 'apex-flow-revenue',
    'webscraper.apexflowlabs.com': 'apex-flow-web-scraper',
    'shield.apexflowlabs.com': 'apex-flow-shield',
    'partner-cloud.apexflowlabs.com': 'apex-flow-partner-cloud',
    'beauty.apexflowlabs.com': 'apex-flow-beauty',
    'health.apexflowlabs.com': 'apex-flow-health',
    'pets.apexflowlabs.com': 'apex-flow-pets',
    'gear.apexflowlabs.com': 'apex-flow-gear',
    'kids.apexflowlabs.com': 'apex-flow-kids',
    'lukasdeans.com': 'lukas-deans',
    'books.apexflowlabs.com': 'apex-flow-publishing',
    'academy.apexflowlabs.com': 'apex-flow-academy',
    'media.apexflowlabs.com': 'apex-flow-media',
    'press.apexflowlabs.com': 'apex-flow-press',
    'affiliates.apexflowlabs.com': 'apex-flow-affiliates',
    'spikercarpetandtilecare.com': 'spiker',
  };
  const ROOT_PATHS = [
    ['/academy', 'apex-flow-academy'], ['/gear', 'apex-flow-gear'],
    ['/kids', 'apex-flow-kids'], ['/media', 'apex-flow-media'],
    ['/affiliates', 'apex-flow-affiliates'], ['/press', 'apex-flow-press'],
    ['/books', 'apex-flow-publishing'], ['/partner-cloud', 'apex-flow-partner-cloud'],
    ['/bug-out', 'apex-flow-bug-out'],
  ];
  const routeAccount = () => {
    const host = location.hostname.toLowerCase().replace(/^www\./, '');
    if (TENANT_BY_HOST[host]) return TENANT_BY_HOST[host];
    if (host === 'apexflowlabs.com' || host === 'localhost') {
      const path = location.pathname.replace(/\/$/, '') || '/';
      return ROOT_PATHS.find(([prefix]) => path === prefix || path.startsWith(`${prefix}/`))?.[1]
        || (path === '/' || path === '/flow' || path === '/flow/index.html' ? 'apex-flow-labs' : null);
    }
    return null;
  };

  async function hydrate(node) {
    const requested = node.dataset.apexPulseAccount;
    const account = requested === 'auto' ? routeAccount() : requested;
    if (!account) return;
    node.dataset.apexPulseResolved = account;
    try {
      // The /config probe was dropped: it 404s for accounts without a stored
      // config and every 404 is a console error (Lighthouse Best Practices).
      const connectionResponse = await fetch(`${API}/connections?account=${encodeURIComponent(account)}`);
      if (!connectionResponse.ok) throw new Error('connections unavailable');
      const connectionData = connectionResponse.ok ? await connectionResponse.json() : null;
      const connections = Array.isArray(connectionData?.connections) ? connectionData.connections : [];
      const reconnect = connections.filter((item) => item.needs_reconnect).length;
      const connected = connections.filter((item) => item.connected && !item.needs_reconnect).length;
      node.textContent = reconnect
        ? `Apex Flow Pulse · ${reconnect} connection${reconnect === 1 ? '' : 's'} need attention`
        : connected
          ? `Apex Flow Pulse · ${connected} verified channel${connected === 1 ? '' : 's'} connected`
          : 'Apex Flow Pulse · no authorized channels yet';
      node.dataset.apexPulseState = reconnect ? 'attention' : connected ? 'connected' : 'pending';
    } catch {
      node.textContent = 'Apex Flow Pulse · status unavailable';
      node.dataset.apexPulseState = 'unavailable';
    }
  }

  const run = () => document.querySelectorAll('[data-apex-pulse-account]').forEach(hydrate);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run, { once: true });
  else run();
})();
