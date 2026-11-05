const $ = s => document.querySelector(s);
const eur = n => (n || 0).toLocaleString('es-ES', { style: 'currency', currency: 'EUR' });
const fdate = s => s ? s.slice(8, 10) + '/' + s.slice(5, 7) + '/' + s.slice(2, 4) : '';
let ODOO = '', company = 'all';
try { company = localStorage.getItem('pf_company') || 'all'; } catch (e) {}

async function api(path) {
  const r = await fetch(path, { credentials: 'same-origin' });
  if (r.status === 401) { location.href = '/entrar'; return; }
  const j = await r.json();
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}

function showErr(msg) { const e = $('#err'); e.textContent = msg; e.hidden = !msg; }

function card(c) {
  const d = document.createElement('div');
  d.className = 'card ' + (c.estado || '') + (c.vencida ? ' vencida' : '') + (c.web ? ' web' : '');
  const right = c.model === 'account.move'
    ? `<b>${eur(c.pendiente)}</b>${c.pendiente !== c.total ? `<small> de ${eur(c.total)}</small>` : ''}`
    : `<b>${eur(c.total)}</b>`;
  let foot = fdate(c.fecha);
  if (c.estado === 'presupuesto' && c.caduca) foot += ` · válido hasta ${fdate(c.caduca)}`;
  if (c.estado === 'caducado') foot += ` · caducó ${fdate(c.caduca)}`;
  if (c.estado === 'pedido' && c.entrega === 'partial') foot += ' · servido en parte';
  if (c.model === 'account.move') foot += ` · vence ${fdate(c.vence)}` + (c.vencida ? ' (vencida)' : '');
  d.innerHTML = `<div class="c1"><span class="ref"></span>${right}</div>
    <div class="cli"></div><div class="c3"><span class="emp"></span><span>${foot}</span></div>`;
  d.querySelector('.ref').textContent = c.ref;
  if (c.web) d.querySelector('.ref').insertAdjacentHTML('afterend', '<span class="tag-web">WEB</span>');
  d.querySelector('.cli').textContent = c.cliente;
  d.querySelector('.emp').textContent = c.empresa;
  d.onclick = () => openDetail(c);
  return d;
}

function fill(col, list, sel = '.cards') {
  const box = document.querySelector(`.col[data-k="${col}"] ${sel}`);
  box.innerHTML = '';
  if (!list.length) box.innerHTML = '<p class="empty">Nada pendiente</p>';
  list.forEach(c => box.appendChild(card(c)));
  if (sel === '.cards') document.querySelector(`.col[data-k="${col}"] .n`).textContent = list.length;
}

async function load() {
  showErr('');
  document.body.classList.add('loading');
  try {
    const q = $('#q').value.trim();
    const d = await api(`/api/ventas?company=${company}&q=${encodeURIComponent(q)}`);
    fill('presupuestos', d.presupuestos);
    fill('presupuestos', d.caducados, '.cards.cad');
    $('#ncad').textContent = d.caducados.length;
    fill('pedidos', d.pedidos);
    fill('albaranes', d.albaranes);
    fill('facturas', d.facturas);
    const sum = (l, k = 'total') => eur(l.reduce((a, c) => a + (c[k] || 0), 0));
    $('#k-pre').textContent = d.presupuestos.length; $('#k-pre-e').textContent = sum(d.presupuestos);
    $('#k-ped').textContent = d.pedidos.length; $('#k-ped-e').textContent = sum(d.pedidos);
    $('#k-alb').textContent = d.albaranes.length; $('#k-alb-e').textContent = sum(d.albaranes);
    $('#k-fac').textContent = sum(d.facturas, 'pendiente');
    const venc = d.facturas.filter(f => f.vencida);
    $('#k-fac-e').textContent = `${d.facturas.length} facturas · ${venc.length} vencidas`;
  } catch (e) { showErr(e.message); }
  document.body.classList.remove('loading');
}

async function openDetail(c) {
  $('#d-title').textContent = `${c.ref} · ${c.cliente}`;
  $('#d-sub').textContent = `${c.empresa} · ${fdate(c.fecha)} · ${eur(c.total)}`;
  $('#d-odoo').href = `${ODOO}/odoo/${c.model}/${c.id}`;
  const tb = $('#d-lines tbody'); tb.innerHTML = '<tr><td colspan="5">Cargando…</td></tr>';
  $('#d-hist').innerHTML = '';
  $('#drawer').hidden = false;
  if (c.model !== 'sale.order') { tb.innerHTML = '<tr><td colspan="5">Para ver la factura, ábrela en Odoo.</td></tr>'; return; }
  try {
    const d = await api(`/api/ventas/${c.id}?company=${company}`);
    tb.innerHTML = '';
    d.lineas.forEach(l => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td></td><td>${l.product_uom_qty}</td><td>${l.qty_delivered}</td><td>${eur(l.price_unit)}</td><td>${eur(l.price_subtotal)}</td>`;
      tr.firstChild.textContent = l.product_id ? l.product_id[1] : l.name;
      tb.appendChild(tr);
    });
    d.historial.forEach(h => {
      const li = document.createElement('li');
      li.innerHTML = '<span class="muted"></span> <b></b> <span></span>';
      li.children[0].textContent = h.fecha.replace('T', ' ');
      li.children[1].textContent = h.quien;
      li.children[2].textContent = h.texto;
      $('#d-hist').appendChild(li);
    });
  } catch (e) { tb.innerHTML = `<tr><td colspan="5">${e.message}</td></tr>`; }
}

$('#upd').onclick = async () => {
  const b = $('#upd'); b.disabled = true; b.textContent = 'Actualizando…';
  await fetch('/api/actualizar', { method: 'POST', credentials: 'same-origin' }).catch(() => {});
  const wait = async () => { try { const r = await fetch('/api/salud'); if (r.ok) return location.reload(); } catch (e) {} setTimeout(wait, 2000); };
  setTimeout(wait, 5000);
};
/* ---------- vista Web ---------- */
function simple(html, href) {
  const d = document.createElement('a'); d.className = 'card wcard'; d.href = href; d.target = '_blank'; d.rel = 'noopener';
  d.innerHTML = html; return d;
}
const escH = t => String(t ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
function fillW(k, list, mk) {
  const box = document.querySelector(`.col[data-k="${k}"] .cards`); box.innerHTML = '';
  if (!list.length) box.innerHTML = '<p class="empty">Nada por aquí</p>';
  list.forEach(x => box.appendChild(mk(x)));
  document.querySelector(`.col[data-k="${k}"] .n`).textContent = list.length;
}
async function loadWeb(badgeOnly) {
  try {
    const d = await api('/api/web');
    const nuevos = d.solicitudes.filter(s => !s.asignado).length;
    const nb = $('#nb-web'); nb.textContent = nuevos; nb.hidden = !nuevos;
    if (badgeOnly) return;
    $('#kw-ped').textContent = d.pedidos.length; $('#kw-ped-e').textContent = eur(d.pedidos.reduce((a, c) => a + c.total, 0));
    $('#kw-car').textContent = d.carritos.length; $('#kw-car-e').textContent = eur(d.carritos.reduce((a, c) => a + c.total, 0)) + ' · ' + d.carritos.filter(c => c.conocido).length + ' de clientes conocidos';
    $('#kw-sol').textContent = d.solicitudes.length; $('#kw-sol-e').textContent = nuevos + ' sin atender · ' + d.spam + ' de spam descartadas';
    fillW('w-sol', d.solicitudes, s => simple(`<div class="c1"><span class="ref">${escH(s.nombre || 'Sin nombre')}</span>${!s.asignado ? '<span class="tag-new">SIN ATENDER</span>' : ''}</div>
      <div class="cli">${escH(s.servicio)}</div><div class="msg">${escH(s.mensaje)}</div>
      <div class="c3"><span>${escH([s.email, s.telefono].filter(Boolean).join(' · '))}</span><span>${fdate(s.fecha)}</span></div>`, `${ODOO}/odoo/crm.lead/${s.id}`));
    fillW('w-ped', d.pedidos, o => simple(`<div class="c1"><span class="ref">${escH(o.ref)}</span><span class="tag-web">WEB</span><b>${eur(o.total)}</b></div>
      <div class="cli">${escH(o.cliente)}</div><div class="c3"><span>${o.estado === 'sale' ? 'Confirmado' : 'Pendiente de confirmar'}</span><span>${fdate(o.fecha)}</span></div>`, `${ODOO}/odoo/sale.order/${o.id}`));
    fillW('w-car', d.carritos, o => simple(`<div class="c1"><span class="ref">${escH(o.cliente)}</span><b>${eur(o.total)}</b></div>
      <div class="msg">${o.lineas.map(escH).join('<br>')}</div><div class="c3"><span>${escH(o.ref)}</span><span>${fdate(o.fecha)}</span></div>`, `${ODOO}/odoo/sale.order/${o.id}`));
  } catch (e) { showErr(e.message); }
}
function route() {
  const v = ({ '#web': 'web', '#whatsapp': 'whatsapp', '#ac': 'ac' })[location.hash] || 'ventas';
  document.querySelectorAll('.side nav a[data-v]').forEach(a => a.classList.toggle('on', a.dataset.v === v));
  $('#v-web').hidden = v !== 'web'; $('#v-ventas').hidden = v !== 'ventas'; $('#v-wa').hidden = v !== 'whatsapp'; $('#v-ac').hidden = v !== 'ac';
  $('#h1').textContent = ({ web: 'Web', whatsapp: 'WhatsApp', ac: 'Panel AC' })[v] || 'Ventas';
  $('#q').hidden = v !== 'ventas'; $('#company').hidden = v !== 'ventas' && v !== 'ac';
  if (v === 'web') loadWeb(); else if (v === 'whatsapp') waOpen(); else if (v === 'ac') acLoad(); else load();
}
window.addEventListener('hashchange', route);
setInterval(() => loadWeb(true), 5 * 60 * 1000);
setInterval(() => waBadge(), 60 * 1000);

$('#close').onclick = () => $('#drawer').hidden = true;
$('#drawer').onclick = e => { if (e.target.id === 'drawer') $('#drawer').hidden = true; };
$('#reload').onclick = () => route();
$('#tgcad').onclick = () => { const b = document.querySelector('.cards.cad'); b.hidden = !b.hidden; };
let t; $('#q').oninput = () => { clearTimeout(t); t = setTimeout(load, 400); };
$('#company').onchange = e => { company = e.target.value; try { localStorage.setItem('pf_company', company); } catch (x) {} route(); };

(async () => {
  try {
    const yo = await api('/api/yo');
    $('#who').textContent = yo.usuario.charAt(0).toUpperCase() + yo.usuario.slice(1);
    ODOO = yo.odoo_url; $('#demo').hidden = !yo.demo;
    $('#ver').textContent = 'versión ' + yo.version;
    $('#upd').hidden = !yo.puede_actualizar;
    ADMIN = !!yo.admin; $('#nav-pre').hidden = !ADMIN;
    const emps = await api('/api/empresas');
    emps.forEach(e => { const o = document.createElement('option'); o.value = e.id; o.textContent = e.name; $('#company').appendChild(o); });
    if (emps.length === 1) {
      $('#company').querySelector('option[value=all]').remove();
      company = String(emps[0].id); $('#company').disabled = true;
    }
    $('#company').value = company;
    if ($('#company').value !== company) { company = 'all'; $('#company').value = 'all'; }
  } catch (e) { showErr(e.message); }
  route(); loadWeb(true); waBadge();
})();

/* ---------- WhatsApp ---------- */
let ADMIN = false, WA = null, WAST = {}, waTimer = null, PLANT = [];
async function jpost(path, body, method = 'POST') {
  const r = await fetch(path, { method, credentials: 'same-origin', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body || {}) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}
const hora = ts => { const d = new Date(ts * 1000), hoy = new Date(); return d.toDateString() === hoy.toDateString() ? d.toTimeString().slice(0, 5) : d.toLocaleDateString('es-ES', { day: '2-digit', month: '2-digit' }) + ' ' + d.toTimeString().slice(0, 5); };
async function waBadge() {
  try { WAST = await api('/api/wa/estado'); const b = $('#nb-wa'); b.textContent = WAST.pendientes; b.hidden = !WAST.pendientes; } catch (e) {}
}
function waTab(t) {
  document.querySelectorAll('.wa-tabs button').forEach(b => b.classList.toggle('on', b.dataset.t === t));
  $('#wa-chats').hidden = t !== 'chats'; $('#wa-camp').hidden = t !== 'camp'; $('#wa-bot').hidden = t !== 'bot';
  if (t === 'camp') waCampLoad(); if (t === 'bot') waBotLoad();
}
document.querySelectorAll('.wa-tabs button').forEach(b => b.onclick = () => waTab(b.dataset.t));
async function waOpen() {
  document.querySelectorAll('.wa-tabs .adm').forEach(b => b.hidden = !ADMIN);
  await waBadge();
  $('#wa-st').textContent = WAST.conectado ? `Conectado a WhatsApp (${WAST.proveedor}) · bot ${WAST.bot_activo ? 'activo' : 'parado'}${WAST.claude ? '' : ' · falta la clave de Claude'}`
    : 'Sin conectar todavía: puedes probar el bot con "Probar el bot"';
  $('#wa-sim').hidden = WAST.conectado || !ADMIN;
  waList(); clearInterval(waTimer); waTimer = setInterval(() => { if (location.hash === '#whatsapp') { waList(); if (WA) waChat(WA, true); } }, 8000);
}
async function waList() {
  const l = await api('/api/wa/conversaciones?q=' + encodeURIComponent($('#wa-q').value.trim()));
  const box = $('#wa-convs'); box.innerHTML = l.length ? '' : '<p class="empty">Aún no hay conversaciones</p>';
  l.forEach(c => {
    const d = document.createElement('div'); d.className = 'wa-c' + (c.necesita_persona ? ' pers' : '') + (c.wa_id === WA ? ' on' : '');
    const est = c.necesita_persona ? '<span class="pill per">PERSONA</span>' : c.humano ? '<span class="pill hum">EQUIPO</span>' : c.bot ? '<span class="pill bot">BOT</span>' : '';
    d.innerHTML = `<div class="t"><b>${escH(c.nombre || '+' + c.wa_id)}</b>${c.no_leidos ? `<span class="u">${c.no_leidos}</span>` : ''}</div>
      <div class="t"><span class="l">${escH(c.ultimo_texto || '')}</span>${est}</div>`;
    d.onclick = () => { WA = c.wa_id; waChat(c.wa_id); waList(); };
    box.appendChild(d);
  });
}
async function waChat(id, quiet) {
  const d = await api('/api/wa/conversaciones/' + id), c = d.contacto;
  const head = $('#wa-head');
  head.innerHTML = `<b>${escH(c.nombre || '')}</b><span class="muted">+${escH(c.wa_id)}</span>
    <span class="sp"><label><input type="checkbox" id="wa-bot1" ${c.bot ? 'checked' : ''}> Bot</label>
    <label><input type="checkbox" id="wa-opt" ${c.optin ? 'checked' : ''}> Acepta ofertas</label>
    ${!WAST.conectado ? `<a class="wa-open" target="_blank" rel="noopener" href="https://wa.me/${c.wa_id}?text=${encodeURIComponent(WAST.aviso_texto || '')}">Abrir en WhatsApp</a>` : ''}
    ${c.necesita_persona ? '<button id="wa-ok">Marcar atendido</button>' : ''}</span>
    ${c.necesita_persona && c.resumen ? `<div class="res">⚠️ ${escH(c.resumen)}</div>` : ''}
    ${c.humano ? '<div class="muted" style="flex-basis:100%;font-size:12px">La lleva una persona: el bot no contesta durante 12 h (o vuelve a activarlo con la casilla).</div>' : ''}`;
  $('#wa-bot1').onchange = e => jpost(`/api/wa/conversaciones/${id}/ajustes`, { bot: e.target.checked }).then(() => waChat(id));
  $('#wa-opt').onchange = e => jpost(`/api/wa/conversaciones/${id}/ajustes`, { optin: e.target.checked });
  if ($('#wa-ok')) $('#wa-ok').onclick = () => jpost(`/api/wa/conversaciones/${id}/ajustes`, { atendido: 1 }).then(() => { waChat(id); waList(); waBadge(); });
  const box = $('#wa-msgs'), abajo = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
  box.innerHTML = '';
  d.mensajes.forEach(m => {
    const b = document.createElement('div');
    if (m.dir === 'sys') { b.className = 'bub sys'; b.textContent = '📞 Llamada recibida · secretaria IA\n' + (m.texto || ''); b.insertAdjacentHTML('beforeend', `<small>${hora(m.ts)}</small>`); box.appendChild(b); return; }
    b.className = 'bub ' + m.dir + (m.autor === 'bot' ? ' bot' : m.autor === 'campaña' ? ' camp' : '');
    const quien = m.dir === 'in' ? '' : m.autor === 'bot' ? '🤖 bot · ' : m.autor === 'móvil' ? '📱 móvil · ' : m.autor === 'campaña' ? '📣 campaña · ' : escH(m.autor) + ' · ';
    b.textContent = m.texto || '';
    b.insertAdjacentHTML('beforeend', `<small>${quien}${hora(m.ts)}${m.estado === 'read' ? ' ✓✓' : m.estado === 'delivered' ? ' ✓' : m.estado === 'failed' ? ' ⚠️ no entregado' : ''}</small>`);
    box.appendChild(b);
  });
  if (!quiet || abajo) box.scrollTop = box.scrollHeight;
  $('#wa-form').hidden = false;
  if (!quiet) waBadge();
}
$('#wa-form').onsubmit = async e => {
  e.preventDefault(); const t = $('#wa-txt').value.trim(); if (!t || !WA) return;
  try { await jpost(`/api/wa/conversaciones/${WA}/enviar`, { texto: t }); $('#wa-txt').value = ''; waChat(WA); } catch (x) { alertBox(x.message); }
};
$('#wa-txt').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#wa-form').requestSubmit(); } };
let wq; $('#wa-q').oninput = () => { clearTimeout(wq); wq = setTimeout(waList, 300); };
$('#sim-go').onclick = async () => {
  const t = $('#sim-txt').value.trim(); if (!t) return;
  const tel = $('#sim-tel').value.replace(/\D/g, '') || '34600000000';
  await jpost('/api/wa/simular', { wa_id: tel, texto: t }); $('#sim-txt').value = ''; WA = tel;
  waList(); waChat(tel); setTimeout(() => waChat(tel, true), 4000); setTimeout(() => { waChat(tel, true); waList(); }, 10000);
};
function alertBox(m) { showErr(m); setTimeout(() => showErr(''), 8000); }
async function waCampLoad() {
  try {
    PLANT = await api('/api/wa/plantillas');
    const sel = $('#c-pl'); sel.innerHTML = '';
    PLANT.filter(p => p.status === 'APPROVED').forEach((p, i) => { const o = document.createElement('option'); o.value = i; o.textContent = `${p.name} (${p.language}, ${p.category})`; sel.appendChild(o); });
    if (!sel.options.length) sel.innerHTML = '<option value="">No hay plantillas aprobadas todavía</option>';
    sel.onchange = waVars; waVars();
    $('#c-n').textContent = (await api('/api/wa/destinatarios')).length;
    const tb = $('#c-hist tbody'); tb.innerHTML = '';
    (await api('/api/wa/campanas')).forEach(c => { const tr = document.createElement('tr'); tr.innerHTML = `<td></td><td>${hora(c.creada)}</td><td>${c.total}</td><td>${c.enviados}</td><td>${c.fallidos}</td>`; tr.firstChild.textContent = c.nombre + (c.estado === 'enviando' ? ' (enviando…)' : ''); tb.appendChild(tr); });
  } catch (e) { alertBox(e.message); }
}
function waVars() {
  const p = PLANT.filter(p => p.status === 'APPROVED')[$('#c-pl').value]; $('#c-txt').textContent = p ? p.texto : '';
  const n = p ? new Set((p.texto.match(/\{\{\d+\}\}/g) || [])).size : 0, box = $('#c-vars'); box.innerHTML = '';
  for (let i = 1; i <= n; i++) box.insertAdjacentHTML('beforeend', `<label>Variable {{${i}}}<input class="c-var" ${i === 1 ? 'value="{nombre}"' : ''}></label>`);
}
$('#c-go').onclick = async () => {
  const p = PLANT.filter(p => p.status === 'APPROVED')[$('#c-pl').value]; if (!p) return;
  const n = $('#c-n').textContent;
  if (!confirmar(`Se va a enviar "${p.name}" a ${n} contactos (≈ ${(n * 0.06).toFixed(2)} €).`)) return;
  try { const r = await jpost('/api/wa/campanas', { nombre: $('#c-nom').value, plantilla: p.name, idioma: p.language, variables: [...document.querySelectorAll('.c-var')].map(i => i.value) }); alertBox(`Campaña en marcha: ${r.total} mensajes`); waCampLoad(); } catch (e) { alertBox(e.message); }
};
function confirmar(msg) { const b = $('#c-go'); if (b.dataset.ok === '1') { b.dataset.ok = ''; b.textContent = 'Enviar campaña'; return true; } b.dataset.ok = '1'; b.textContent = 'Confirmar envío'; alertBox(msg + ' Pulsa "Confirmar envío" para mandarla.'); return false; }
$('#c-imp').onclick = async () => {
  try { const r = await jpost('/api/wa/contactos/importar', { lista: $('#c-lista').value, consentimiento: $('#c-ok').checked }); alertBox(`${r.importados} contactos añadidos`); $('#c-lista').value = ''; waCampLoad(); } catch (e) { alertBox(e.message); }
};
async function waBotLoad() {
  try {
    const a = await api('/api/wa/ajustes');
    $('#b-act').checked = a.bot_activo === 'si'; $('#b-pre').checked = a.bot_precios === 'si'; $('#b-info').value = a.bot_info;
    $('#l-auto').checked = a.llamadas_auto === 'si'; $('#l-pl').value = a.llamadas_plantilla; $('#l-txt').value = a.aviso_texto; $('#l-tok').value = a.llamadas_token;
    $('#b-con').textContent = `WhatsApp: ${WAST.conectado ? 'conectado' : 'sin conectar'} · Claude: ${WAST.claude ? 'clave puesta' : 'falta la clave'} · modelo ${a.modelo}`;
  } catch (e) { alertBox(e.message); }
}
$('#b-save').onclick = async () => { await jpost('/api/wa/ajustes', { bot_activo: $('#b-act').checked ? 'si' : 'no', bot_precios: $('#b-pre').checked ? 'si' : 'no', bot_info: $('#b-info').value, llamadas_auto: $('#l-auto').checked ? 'si' : 'no', llamadas_plantilla: $('#l-pl').value.trim(), aviso_texto: $('#l-txt').value }, 'PUT'); alertBox('Guardado'); waBadge(); };
$('#b-hook').onclick = async () => { try { await jpost('/api/wa/registrar-webhook', { url_publica: $('#b-url').value.trim() }); alertBox('Conectado: WhatsApp ya enviará los mensajes al panel'); } catch (e) { alertBox(e.message); } };
