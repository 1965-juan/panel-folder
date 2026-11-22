/* ---------- Correo sin atender: lo que piden los clientes y no tiene presupuesto ni pedido en Odoo ---------- */
let SG = null, SG_T = 'P', SG_TM = null, SG_TIPO = '', SG_CTA = '';
const SG_TABS = [['P', '📥 Sin atender'], ['R', '🔔 Recordatorios'], ['K', '💬 Contestados, sin presupuesto'], ['X', '🗄️ Archivados']];
const sgF = s => s ? s.slice(8, 10) + '/' + s.slice(5, 7) : '';
const sgHoy = (d = 0) => { const x = new Date(Date.now() + d * 864e5); return x.toISOString().slice(0, 10); };
const SG_COL = ['#4B2A91', '#F57925', '#1f7a4d', '#2563a8', '#a8326e', '#7a5b1f', '#3f6b6b'];
const sgCol = c => SG_COL[Math.abs([...(c || '')].reduce((a, ch) => a * 31 + ch.charCodeAt(0) | 0, 7)) % SG_COL.length];
const sgCta = c => (c || '').replace(/\.(es|com)$/, '');

async function sgBadge() {
  try { const r = await api('/api/seg/aviso'); const nb = $('#nb-sg'); if (nb) { nb.textContent = r.n; nb.hidden = !r.n; } } catch (e) { }
}
async function sgOpen(recalcular) {
  clearTimeout(SG_TM);
  try { SG = await api('/api/seg' + (recalcular ? '?recalcular=1' : '')); } catch (e) { $('#sg-l').innerHTML = `<p class="err">${escH(e.message)}</p>`; return; }
  sgEstado(); sgFiltros(); sgTabs(); sgLista(); sgBadge();
  if (!SG.cuentas.length && $('#sg-ctas').hidden) { $('#sg-ctas').hidden = false; sgCuentas(); }
  if ((SG.calculando || SG.lectura.leyendo) && !$('#v-sg').hidden) SG_TM = setTimeout(() => sgOpen(), 6000);
}
function sgEstado() {
  const L = SG.lectura, e = $('#sg-est');
  let h = '';
  if (!SG.cuentas.length) h = `<p class="pc-aviso">Añade las cuentas de correo aquí abajo, en «Cuentas de correo».</p>`;
  else if (!SG.correos && !L.leyendo) h = `<p class="pc-aviso">Las cuentas están puestas. Pulsa <b>📥 Leer el correo desde…</b> para cargar el correo desde enero.</p>`;
  else h = `<b>${SG.correos.toLocaleString('es-ES')}</b> correos leídos de ${SG.cuentas.length} cuentas${SG.desde ? ' desde el ' + fdate(SG.desde) : ''}. Aquí solo sale lo que un cliente pide y no tiene todavía presupuesto ni pedido en Odoo; lo que ya está presupuestado se sigue desde Ventas.`;
  if (L.leyendo) {
    const c = Object.entries(L.cuentas).map(([k, v]) => v.error ? `🔴 ${escH(k)}: ${escH(v.error)}` : `${escH(k)} ${v.leidos}/${v.total}`).join(' · ');
    h += ` <span class="bdg b-warn">⏳ ${escH(L.fase)}${L.por_clasificar && /Clasif|PDF/.test(L.fase) ? ` ${L.clasificados}/${L.por_clasificar}` : ''}</span><br><small>${c}</small>`;
  } else if (L.error) h += ` <span class="bdg b-bad">${escH(L.error)}</span>`;
  if (SG.calculando) h += ' <span class="bdg b-warn">⏳ cruzando con Odoo…</span>';
  if (SG.error) h += ` <span class="bdg b-bad">${escH(SG.error)}</span>`;
  e.innerHTML = h;
  $('#sg-leer').hidden = !SG.cuentas.length;
}
function sgFiltros() {
  const cs = [...new Set(SG.items.concat(SG.archivados || []).map(i => i.cuenta).filter(Boolean))].sort();
  $('#sg-fcta').innerHTML = '<option value="">Todas las cuentas</option>' + cs.map(c => `<option ${c === SG_CTA ? 'selected' : ''} value="${escH(c)}">${escH(c)}</option>`).join('');
}
function sgDe(t) {
  const dorm = $('#sg-dorm').checked;
  let l = t === 'R' ? SG.avisos : t === 'X' ? (SG.archivados || []) : SG.items.filter(i => (t === 'K' ? i.contestado : !i.contestado) && (dorm || !(i.marca && i.marca.estado === 'recordar')));
  return l.filter(i => (!SG_TIPO || i.tipo === SG_TIPO) && (!SG_CTA || i.cuenta === SG_CTA));
}
function sgTabs() {
  $('#sg-tabs').innerHTML = SG_TABS.map(([k, n]) => `<button data-t="${k}" class="${k === SG_T ? 'on' : ''}">${n} <b>${sgDe(k).length}</b></button>`).join('');
  $('#sg-tabs').querySelectorAll('button').forEach(b => b.onclick = () => { SG_T = b.dataset.t; sgTabs(); sgLista(); });
}
function sgLista() {
  const q = ($('#sg-q').value || '').toLowerCase();
  let l = sgDe(SG_T).filter(i => !q || [i.cliente, i.asunto, i.que, i.de, i.nombre, i.cuenta].join(' ').toLowerCase().includes(q));
  l = l.slice().sort((a, b) => (b.reabierto ? 1 : 0) - (a.reabierto ? 1 : 0) || (b.fecha > a.fecha ? 1 : b.fecha < a.fecha ? -1 : 0));
  const box = $('#sg-l');
  if (!l.length) { box.innerHTML = `<p class="empty">${SG.calculando ? 'Calculando…' : SG_T === 'P' ? 'Nada sin atender 👍' : 'Nada aquí'}</p>`; sgSel(); return; }
  box.innerHTML = l.map(i => { const m = i.marca;
    return `<div class="sg-i" data-k="${escH(i.clave)}"><label class="sg-ck"><input type="checkbox" data-k="${escH(i.clave)}"></label>
      <div class="sg-c"><div class="sg-1"><span class="sg-cta-b" style="background:${sgCol(i.cuenta)}" title="Entró en esta cuenta">📥 ${escH(sgCta(i.cuenta))}</span>
        <b>${escH(i.cliente)}</b>${i.ficha ? '' : ' <span class="bdg">sin ficha en Odoo</span>'}
        <span class="bdg ${i.tipo === 'C' ? 'b-prov' : 'b-warn'}">${i.tipo === 'C' ? '🛒 pide material' : '💬 pide precio'}</span>
        ${i.reabierto ? '<span class="bdg b-bad">el cliente ha vuelto a escribir</span>' : ''}
        <span class="muted">${sgF(i.fecha)} · hace ${i.dias} d${i.n > 1 ? ` · ${i.n} correos` : ''}</span>${m && m.estado === 'recordar' ? `<span class="bdg b-warn">⏰ ${fdate(m.recordar)}</span>` : ''}</div>
        <div class="sg-2"><b>${escH(i.asunto || '(sin asunto)')}</b>${i.que ? ` — ${escH(i.que)}` : ''} <small class="muted">${escH(i.nombre && i.nombre !== i.de ? i.nombre + ' · ' : '')}${escH(i.de)}</small></div>
        ${m && m.nota ? `<div class="sg-nota">📝 ${escH(m.nota)}</div>` : ''}
        <div class="sg-3"><button class="lnk" data-a="ver">✉️ Ver correo</button><button class="lnk" data-a="resp">↩️ Responder</button>
          <button class="lnk" data-a="pres">📝 Crear presupuesto o pedido</button><button class="lnk" data-a="rec">⏰ Recordar</button>
          ${SG_T === 'X' ? '<button class="lnk" data-a="abierto">↩ Sacar de archivados</button>' : '<button class="lnk del" data-a="archivado">🗄️ Archivar</button>'}</div>
        <div class="sg-x"></div></div></div>`; }).join('');
  box.querySelectorAll('.sg-i').forEach(d => {
    const it = l.find(x => x.clave === d.dataset.k);
    d.querySelectorAll('[data-a]').forEach(b => b.onclick = () => sgAccion(it, b.dataset.a, d));
    d.querySelector('input[type=checkbox]').onchange = sgSel;
  });
  sgSel();
}
function sgSel() { const n = $('#sg-l').querySelectorAll('.sg-ck input:checked').length; $('#sg-seln').textContent = n; $('#sg-sel').hidden = !n; }
async function sgMarca(claves, estado, extra = {}) {
  await jpost('/api/seg/marca', { claves, estado, ...extra });
  SG = await api('/api/seg'); sgTabs(); sgLista(); sgBadge();
}
async function sgVer(it, x) {
  x.innerHTML = '<p class="muted">Cargando…</p>';
  try { const L = await api('/api/seg/hilo?mid=' + encodeURIComponent(it.mid));
    x.innerHTML = L.map(m => `<div class="sg-m ${m.enviado ? 'env' : ''}"><div class="muted"><b>${m.enviado ? '➡️ Nosotros' : escH(m.nombre || m.de)}</b> ${m.enviado ? '' : `&lt;${escH(m.de)}&gt;`} · ${escH(m.fecha)} · en ${escH(m.cuenta)}</div>
      <div><b>${escH(m.asunto || '(sin asunto)')}</b></div><div class="sg-t">${escH(m.texto || '').replace(/\n/g, '<br>') || '<i class="muted">(sin texto)</i>'}</div>
      ${m.adjuntos.length ? `<div class="sg-adj">${m.adjuntos.map((a, k) => `<a target="_blank" rel="noopener" href="/api/seg/adjunto?mid=${encodeURIComponent(m.id)}&i=${k}">📎 ${escH(a)}</a>`).join('')}</div>` : ''}
      ${m.citado ? `<details><summary class="muted">ver el mensaje citado</summary><div class="sg-t muted">${escH(m.citado).replace(/\n/g, '<br>')}</div></details>` : ''}</div>`).join('') || '<p class="muted">No se encuentra el correo</p>';
  } catch (e) { x.innerHTML = `<p class="err">${escH(e.message)}</p>`; }
}
const SG_RAP = [['Recibido', 'Hola,\n\nRecibido, gracias. Te preparamos el presupuesto y te lo enviamos en breve.'],
  ['Faltan datos', 'Hola,\n\nPara poder pasarte el presupuesto necesitamos que nos indiques:\n- Cantidades\n- Medidas / formato\n- Plazo de entrega\n\nGracias.'],
  ['Te paso precio', 'Hola,\n\nTe adjuntamos el presupuesto que nos pediste. Cualquier duda nos dices.'],
  ['¿Seguimos?', 'Hola,\n\n¿Pudiste ver lo que nos comentabas? Quedamos pendientes para ponerlo en marcha.']];
function sgFirma() { try { return localStorage.getItem('pf_firma') || ''; } catch (_) { return ''; } }
function sgResp(it, x) {
  const firma = sgFirma() || 'Un saludo,\nJuan\nFolder Castellón\nWhatsApp 673 10 96 93 · www.foldercastellon.com';
  const asunto = /^\s*re\s*:/i.test(it.asunto || '') ? it.asunto : 'Re: ' + (it.asunto || '');
  x.innerHTML = `<form class="sg-resp"><div class="sg-rf"><label>Desde <select name="cuenta">${SG.cuentas.map(c => `<option ${c === it.cuenta ? 'selected' : ''}>${escH(c)}</option>`).join('')}</select></label>
      <label>Para <input name="para" value="${escH(it.de)}"></label><label>CC <input name="cc" placeholder="opcional"></label></div>
    <label>Asunto <input name="asunto" value="${escH(asunto)}"></label>
    <div class="ma-chips">${SG_RAP.map((r, k) => `<button type="button" class="lnk" data-r="${k}">${escH(r[0])}</button>`).join('')}</div>
    <textarea name="texto" rows="7" placeholder="Escribe la respuesta…"></textarea>
    <label>Firma <textarea name="firma" rows="3">${escH(firma)}</textarea></label>
    <label>Adjuntar <input type="file" name="adjuntos" multiple></label>
    <div><button class="ac-go">Enviar</button> <small class="muted">Sale desde la cuenta elegida, en el mismo hilo, y queda en Enviados (lo verás en Spark). Debajo va citado el correo del cliente.</small></div>
    <div class="sg-rr"></div></form>`;
  const f = x.querySelector('form');
  f.querySelectorAll('[data-r]').forEach(b => b.onclick = () => { f.texto.value = SG_RAP[+b.dataset.r][1]; f.texto.focus(); });
  f.onsubmit = async e => {
    e.preventDefault();
    if (!f.texto.value.trim()) return alertBox('Escribe la respuesta');
    if (!confirm(`¿Enviar la respuesta a ${f.para.value} desde ${f.cuenta.value}?`)) return;
    try { localStorage.setItem('pf_firma', f.firma.value); } catch (_) { }
    const fd = new FormData(f); fd.set('texto', f.texto.value.trim() + '\n\n' + f.firma.value.trim()); fd.delete('firma'); fd.set('mid', it.ultimo_id || it.mid);
    const bt = f.querySelector('.ac-go'); bt.disabled = true; f.querySelector('.sg-rr').textContent = '⏳ Enviando…';
    try {
      const r = await fetch('/api/seg/responder', { method: 'POST', body: fd, credentials: 'same-origin' });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.detail || ('Error ' + r.status));
      f.querySelector('.sg-rr').innerHTML = `<span class="ok-t">✅ Enviado${j.enviados ? ' (copia en Enviados)' : ''}${j.adjuntos.length ? ' con ' + j.adjuntos.length + ' adjunto(s)' : ''}</span>`;
      setTimeout(() => sgOpen(), 2500);
    } catch (er) { f.querySelector('.sg-rr').innerHTML = `<span class="err-t">❌ ${escH(er.message)}</span>`; bt.disabled = false; }
  };
}
function sgPres(it) {
  if ((PE.lin.length || PE.cli) && !confirm('En «Nuevo pedido» hay otro pedido a medias. ¿Lo cambio por este cliente?')) return;
  PE = { cli: null, lin: [], emp: PE.emp, ref: it.ref || '', nota: '', sg: { clave: it.clave, cliente: it.cliente, que: it.que, asunto: it.asunto, de: it.de } };
  if (it.ficha) PE.cli = { id: it.ficha.contacto || it.ficha.id, nombre: it.ficha.nombre, email: it.de };
  peGuarda();
  location.hash = '#pedidos';
  if (!it.ficha) setTimeout(() => { const q = $('#pe-cq'); if (q) { q.value = (it.cli || it.nombre || it.de.split('@')[1] || '').slice(0, 40); q.dispatchEvent(new Event('input')); q.focus(); } }, 400);
}
async function sgAccion(it, a, d) {
  const x = d.querySelector('.sg-x');
  if (a === 'ver') { if (x.dataset.v === 'ver' && x.innerHTML) { x.innerHTML = ''; x.dataset.v = ''; return; } x.dataset.v = 'ver'; return sgVer(it, x); }
  if (a === 'resp') { if (x.dataset.v === 'resp' && x.innerHTML) { x.innerHTML = ''; x.dataset.v = ''; return; } x.dataset.v = 'resp'; return sgResp(it, x); }
  if (a === 'pres') return sgPres(it);
  if (a === 'rec') {
    x.dataset.v = 'rec';
    x.innerHTML = `<div class="sg-rec"><label>Recordar el <input type="date" value="${sgHoy(3)}"></label>${[1, 3, 7, 15].map(n => `<button class="lnk" data-n="${n}">+${n} d</button>`).join('')}
      <input class="sg-nt" placeholder="Nota (qué falta, a quién llamar…)" value="${escH((it.marca && it.marca.nota) || '')}"><button class="ac-go">Guardar</button></div>`;
    const di = x.querySelector('input[type=date]');
    x.querySelectorAll('[data-n]').forEach(b => b.onclick = () => di.value = sgHoy(+b.dataset.n));
    x.querySelector('.ac-go').onclick = () => sgMarca([it.clave], 'recordar', { recordar: di.value, nota: x.querySelector('.sg-nt').value });
    return;
  }
  try { await sgMarca([it.clave], a); } catch (e) { alertBox(e.message); }
}
$('#sg-q').oninput = () => sgLista();
$('#sg-ftipo').onchange = e => { SG_TIPO = e.target.value; sgTabs(); sgLista(); };
$('#sg-fcta').onchange = e => { SG_CTA = e.target.value; sgTabs(); sgLista(); };
$('#sg-dorm').onchange = () => { sgTabs(); sgLista(); };
$('#sg-rec').onclick = () => sgOpen(true);
document.querySelectorAll('#sg-sel [data-bulk]').forEach(b => b.onclick = async () => {
  const ks = [...$('#sg-l').querySelectorAll('.sg-ck input:checked')].map(c => c.dataset.k);
  if (!ks.length) return;
  if (!confirm(`¿Archivar ${ks.length} correos?`)) return;
  try { await sgMarca(ks, b.dataset.bulk); } catch (e) { alertBox(e.message); }
});
$('#sg-leer').onclick = async () => {
  const d = prompt('¿Desde qué fecha leo el correo? (AAAA-MM-DD). Lee entrada y enviados de todas las cuentas; la primera vez tarda un rato.', SG.desde || '2026-01-01');
  if (!d) return;
  try { await jpost('/api/seg/leer', { desde: d }); sgOpen(); } catch (e) { alertBox(e.message); }
};
/* cuentas de correo */
const SG_SPARK = ['foldercastellon.es', 'regalosfolder.es', 'distripluslevante.es', 'foldercs.es'].map(d => 'info' + String.fromCharCode(64) + d);
async function sgCuentas() {
  const L = await api('/api/seg/cuentas').catch(() => []);
  $('#sg-cl').innerHTML = L.length ? L.map(c => `<div class="sg-cta">🟢 <b>${escH(c.correo)}</b> <span class="muted">${escH(c.servidor)}</span> <button class="lnk del" data-q="${escH(c.correo)}">quitar</button></div>`).join('')
    : '<p class="muted">Todavía no hay ninguna. Añádelas una a una:</p>';
  const faltan = SG_SPARK.filter(x => !L.some(c => c.correo === x));
  $('#sg-dl').innerHTML = faltan.map(x => `<option value="${x}">`).join('');
  if (faltan.length && !$('#sg-cm').value) $('#sg-cm').value = faltan[0];
  $('#sg-cl').querySelectorAll('[data-q]').forEach(b => b.onclick = async () => { if (!confirm('¿Dejar de leer ' + b.dataset.q + '?')) return; await jpost('/api/seg/cuentas/quitar', { correo: b.dataset.q }); sgCuentas(); });
  return L;
}
$('#sg-cbtn').onclick = () => { const p = $('#sg-ctas'); p.hidden = !p.hidden; if (!p.hidden) sgCuentas(); };
$('#sg-cf').onsubmit = async e => {
  e.preventDefault(); const r = $('#sg-cr'); r.textContent = '⏳ Probando…'; r.className = 'muted';
  try { const x = await jpost('/api/seg/cuentas', { correo: $('#sg-cm').value, clave: $('#sg-cp').value, servidor: $('#sg-cs').value });
    r.textContent = `✅ ${x.correo} conectada (${x.proveedor || x.servidor}, ${x.bandeja} correos en la bandeja)`; r.className = 'ok-t';
    $('#sg-cp').value = ''; $('#sg-cm').value = ''; $('#sg-cs').value = ''; await sgCuentas(); sgOpen(); }
  catch (er) { r.textContent = '❌ ' + er.message; r.className = 'err-t'; $('#sg-cp').value = ''; }
};
