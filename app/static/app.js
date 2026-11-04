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
  const v = location.hash === '#web' ? 'web' : 'ventas';
  document.querySelectorAll('.side nav a[data-v]').forEach(a => a.classList.toggle('on', a.dataset.v === v));
  $('#v-web').hidden = v !== 'web'; $('#v-ventas').hidden = v !== 'ventas';
  $('#h1').textContent = v === 'web' ? 'Web' : 'Ventas';
  $('#q').hidden = $('#company').hidden = v === 'web';
  if (v === 'web') loadWeb(); else load();
}
window.addEventListener('hashchange', route);
setInterval(() => loadWeb(true), 5 * 60 * 1000);

$('#close').onclick = () => $('#drawer').hidden = true;
$('#drawer').onclick = e => { if (e.target.id === 'drawer') $('#drawer').hidden = true; };
$('#reload').onclick = () => route();
$('#tgcad').onclick = () => { const b = document.querySelector('.cards.cad'); b.hidden = !b.hidden; };
let t; $('#q').oninput = () => { clearTimeout(t); t = setTimeout(load, 400); };
$('#company').onchange = e => { company = e.target.value; try { localStorage.setItem('pf_company', company); } catch (x) {} load(); };

(async () => {
  try {
    const yo = await api('/api/yo');
    $('#who').textContent = yo.usuario.charAt(0).toUpperCase() + yo.usuario.slice(1);
    ODOO = yo.odoo_url; $('#demo').hidden = !yo.demo;
    $('#ver').textContent = 'versión ' + yo.version;
    $('#upd').hidden = !yo.puede_actualizar;
    const emps = await api('/api/empresas');
    emps.forEach(e => { const o = document.createElement('option'); o.value = e.id; o.textContent = e.name; $('#company').appendChild(o); });
    if (emps.length === 1) {
      $('#company').querySelector('option[value=all]').remove();
      company = String(emps[0].id); $('#company').disabled = true;
    }
    $('#company').value = company;
    if ($('#company').value !== company) { company = 'all'; $('#company').value = 'all'; }
  } catch (e) { showErr(e.message); }
  route(); loadWeb(true);
})();
