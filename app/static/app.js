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
  d.className = 'card ' + (c.estado || '') + (c.vencida ? ' vencida' : '');
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
$('#close').onclick = () => $('#drawer').hidden = true;
$('#drawer').onclick = e => { if (e.target.id === 'drawer') $('#drawer').hidden = true; };
$('#reload').onclick = load;
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
  load();
})();
