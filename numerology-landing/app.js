(() => {
  'use strict';
  // Must match CONSENT_VERSION in lead_generation_ch_consumer/server.py.
  const CONSENT_VERSION = '2026-11-03.1';
  const startedAt = Date.now();
  const $ = id => document.getElementById(id);
  const show = (el, text, ok) => { el.className = 'msg ' + (ok ? 'ok' : 'err'); el.textContent = text; };

  async function post(url, body) {
    const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    let data = {};
    try { data = await r.json(); } catch { /* keep empty */ }
    return { ok: r.ok, data };
  }

  $('leadform').addEventListener('submit', async e => {
    e.preventDefault();
    const f = e.target, msg = $('formmsg');
    if (!f.checkValidity()) {
      f.reportValidity();
      return show(msg, 'Bitte alle Pflichtfelder ausfüllen und die Einwilligung bestätigen.', false);
    }
    const channel = f.querySelector('input[name=channel]:checked')?.value || '';
    if (channel === 'Telefon' && !f.phone.value.trim()) {
      f.phone.focus();
      return show(msg, 'Für Kontakt per Telefon bitte eine Telefonnummer angeben.', false);
    }
    const body = {
      first_name: f.first_name.value, last_name: f.last_name.value, email: f.email.value,
      phone: f.phone.value, location: f.location.value, channel, product: f.product.value,
      request: f.request.value, consent: f.consent.checked === true, consent_version: CONSENT_VERSION,
      website: f.website.value, started_at: startedAt,
    };
    const btn = f.querySelector('button[type=submit]');
    btn.disabled = true;
    try {
      const { ok, data } = await post('/api/lead', body);
      if (!ok) return show(msg, data.error || 'Senden fehlgeschlagen.', false);
      f.reset();
      msg.className = 'msg ok';
      msg.textContent = 'Danke! Ihre Anfrage ist eingegangen. Wir melden uns über den gewählten Kanal.';
      if (data.revoke_token) {
        const p = document.createElement('p');
        p.append('Ihr persönlicher Widerrufscode (bitte aufbewahren): ');
        const c = document.createElement('code');
        c.textContent = data.revoke_token;
        p.append(c);
        msg.append(p);
      }
    } catch {
      show(msg, 'Keine Verbindung. Bitte später erneut versuchen.', false);
    } finally {
      btn.disabled = false;
    }
  });

  $('revokeform').addEventListener('submit', async e => {
    e.preventDefault();
    const f = e.target, msg = $('revokemsg');
    if (!f.token.value.trim()) return show(msg, 'Bitte Widerrufscode eingeben.', false);
    const del = f.delete.checked;
    try {
      const { ok, data } = await post('/api/revoke', { token: f.token.value.trim(), delete: del });
      if (!ok) return show(msg, data.error || 'Widerruf fehlgeschlagen.', false);
      f.reset();
      show(msg, del ? 'Einwilligung widerrufen und Ihre Daten gelöscht.' : 'Einwilligung widerrufen. Wir kontaktieren Sie nicht mehr.', true);
    } catch {
      show(msg, 'Keine Verbindung. Bitte später erneut versuchen.', false);
    }
  });
})();
