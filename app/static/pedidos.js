/* ---------- Nuevo pedido de cliente (presupuesto o pedido en Odoo) y buscador de Artículos ---------- */
let PE = { cli: null, lin: [] };
try { const g = JSON.parse(localStorage.getItem('pf_pedido') || 'null'); if (g && Array.isArray(g.lin)) PE = g; } catch (_) { }
const peGuarda = () => { try { localStorage.setItem('pf_pedido', JSON.stringify(PE)); } catch (_) { } };
let PE_EMPS = null;

async function peOpen() {
  if (!PE_EMPS) {
    try { PE_EMPS = await api('/api/empresas'); } catch (e) { PE_EMPS = []; }
    const sel = $('#pe-emp'); sel.innerHTML = '';
    PE_EMPS.forEach(e => { const o = document.createElement('option'); o.value = e.id; o.textContent = e.name; sel.appendChild(o); });
    if (PE.emp && PE_EMPS.some(e => e.id == PE.emp)) sel.value = PE.emp;
    // almacén y compras venden siempre con DISTRIPLUS: no se pregunta
    const dis = PE_EMPS.find(e => /DISTRIPLUS/i.test(e.name));
    const soloDis = !ADMIN || ['almacen', 'compras'].includes((window.YO || {}).usuario);
    if (soloDis && dis) { sel.value = dis.id; PE.emp = dis.id; }
    $('#pe-emp-l').hidden = soloDis;
    sel.onchange = () => { PE.emp = sel.value; peGuarda(); };
  }
  $('#pe-ref').value = PE.ref || ''; $('#pe-nota').value = PE.nota || ''; $('#pe-nent').value = PE.nent || '';
  pePinta(); peDirs();
}

// direcciones de entrega del cliente elegido (la de entrega primero, como en Odoo)
async function peDirs() {
  const l = $('#pe-dir-l'), sel = $('#pe-dir');
  $('#pe-hist').hidden = !PE.cli; if (!PE.cli) { $('#pe-hbox').hidden = true; }
  if (!PE.cli) { l.hidden = true; return; }
  try {
    const d = await api(`/api/pedidos/cliente/${PE.cli.id}`);
    sel.innerHTML = d.direcciones.map(x => `<option value="${x.id}">${escH((x.entrega ? '🚚 ' : '') + x.nombre + (x.direccion ? ' — ' + x.direccion : ''))}</option>`).join('');
    if (PE.dir && d.direcciones.some(x => x.id == PE.dir)) sel.value = PE.dir; else PE.dir = d.direcciones[0] ? d.direcciones[0].id : null;
    l.hidden = d.direcciones.length < 2;
    sel.onchange = () => { PE.dir = +sel.value; peGuarda(); };
  } catch (e) { l.hidden = true; }
}

// histórico de compras del cliente: un clic lo añade al presupuesto
$('#pe-hist').onclick = async () => {
  const bx = $('#pe-hbox');
  if (!bx.hidden) { bx.hidden = true; return; }
  bx.hidden = false; bx.innerHTML = '<p class="muted">Cargando lo que ha comprado…</p>';
  try {
    const d = await api(`/api/pedidos/historico/${PE.cli.id}`);
    // una sola fila por referencia: la de la última compra (sumando veces y unidades)
    const por = {};
    d.articulos.forEach(a => { const k = a.ref || ('#' + a.product_id), x = por[k];
      if (!x) por[k] = { ...a };
      else { x.veces += a.veces; x.cantidad += a.cantidad; if (a.ultima > x.ultima) Object.assign(x, { product_id: a.product_id, nombre: a.nombre, ultima: a.ultima, precio: a.precio, pvp: a.pvp }); } });
    const r = Object.values(por);
    if (!r.length) { bx.innerHTML = '<p class="muted">No ha comprado nada en los últimos 12 meses.</p>'; return; }
    let orden = 'az';
    const pinta = () => {
      const l = r.slice().sort(orden === 'az' ? (a, b) => a.nombre.localeCompare(b.nombre, 'es') : orden === 'veces' ? (a, b) => b.veces - a.veces || a.nombre.localeCompare(b.nombre, 'es') : (a, b) => b.ultima.localeCompare(a.ultima));
      const q = ($('#pe-hq') ? $('#pe-hq').value : '').toLowerCase();
      bx.querySelector('table').innerHTML = l.map(a => `<tr data-n="${escH((a.nombre + ' ' + a.ref).toLowerCase())}" ${q && !(a.nombre + ' ' + a.ref).toLowerCase().includes(q) ? 'hidden' : ''}><td><b>${escH(a.nombre)}</b><br><small class="muted">${escH(a.ref)} · ${a.veces} ${a.veces === 1 ? 'vez' : 'veces'} · ${+a.cantidad} ud. · último ${fdate(a.ultima)} a ${eur(a.precio)}</small></td>
        <td><button class="btn" data-p="${a.product_id}">${PE.lin.some(x => x.product_id === a.product_id) ? '✔' : '＋'}</button></td></tr>`).join('');
      bx.querySelectorAll('[data-p]').forEach(b => b.onclick = () => { const a = r.find(x => x.product_id === +b.dataset.p); peAnade({ id: a.product_id, nombre: a.nombre, ref: a.ref, pvp: a.pvp }); b.textContent = '✔'; });
      bx.querySelectorAll('[data-o]').forEach(b => b.classList.toggle('on', b.dataset.o === orden));
    };
    bx.innerHTML = `<div class="pe-hh"><b>Lo que ha comprado en 12 meses (${r.length})</b> <input id="pe-hq" type="search" placeholder="filtrar…">
      <span class="pe-ord">Ordenar: <a href="#" data-o="az">A-Z</a> <a href="#" data-o="veces">más pedidos</a> <a href="#" data-o="ultima">más recientes</a></span> <a href="#" class="lnk" id="pe-hx">cerrar</a></div>
      <table class="pe-t"></table>`;
    bx.querySelectorAll('[data-o]').forEach(b => b.onclick = e => { e.preventDefault(); orden = b.dataset.o; pinta(); });
    $('#pe-hx').onclick = e => { e.preventDefault(); bx.hidden = true; };
    $('#pe-hq').oninput = () => pinta();
    pinta();
  } catch (e) { bx.innerHTML = `<p class="err">${escH(e.message)}</p>`; }
};

function pePinta() {
  $('#pe-cli').innerHTML = PE.cli ? `<b>${escH(PE.cli.nombre)}</b>${PE.cli.nif ? ' · ' + escH(PE.cli.nif) : ''}${PE.cli.email ? `<br><small class="muted">${escH(PE.cli.email)}</small>` : ''} <a href="#" id="pe-cx" class="lnk">cambiar</a>` : '<span class="muted">Ningún cliente elegido</span>';
  const cx = $('#pe-cx'); if (cx) cx.onclick = e => { e.preventDefault(); PE.cli = null; PE.dir = null; peGuarda(); pePinta(); peDirs(); $('#pe-cq').focus(); };
  const tb = $('#pe-lin'); tb.innerHTML = '';
  PE.lin.forEach((l, i) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td><b></b><br><small class="muted"></small></td><td><input type="number" min="0" step="1" value="${l.cantidad}" class="pe-n"></td>
      <td><input type="number" min="0" step="0.01" value="${l.precio ?? ''}" placeholder="${l.pvp ? (+l.pvp).toFixed(2) : ''}" class="pe-p"></td><td><button class="tb-x" title="Quitar">✕</button></td>`;
    tr.querySelector('b').textContent = l.nombre; tr.querySelector('small').textContent = [l.ref, l.ref_fab].filter(Boolean).join(' · ');
    tr.querySelector('.pe-n').onchange = e => { l.cantidad = +e.target.value || 0; peGuarda(); };
    tr.querySelector('.pe-p').onchange = e => { l.precio = e.target.value === '' ? null : +e.target.value; peGuarda(); };
    tr.querySelector('.tb-x').onclick = () => { PE.lin.splice(i, 1); peGuarda(); pePinta(); };
    tb.appendChild(tr);
  });
  $('#pe-vacio').hidden = !!PE.lin.length;
  $('#pe-avi').disabled = !(PE.cli && PE.cli.email);
  $('#pe-avi-e').textContent = PE.cli ? (PE.cli.email ? `(${PE.cli.email})` : '(el cliente no tiene email)') : '';
  const n = document.querySelector('.side nav a[data-v="pedidos"]');
  if (n) n.textContent = PE.lin.length ? `Nuevo pedido (${PE.lin.length})` : 'Nuevo pedido';
}

function peAnade(a) {
  const ya = PE.lin.find(l => l.product_id === a.id);
  if (ya) ya.cantidad = (+ya.cantidad || 0) + 1;
  else PE.lin.push({ product_id: a.id, nombre: a.nombre, ref: a.ref, ref_fab: a.ref_fab, pvp: a.pvp, cantidad: 1, precio: null });
  peGuarda(); pePinta();
}

// buscadores con espera (no lanzar una búsqueda por tecla)
const peDebounce = (fn, ms = 300) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

$('#pe-cq').oninput = peDebounce(async () => {
  const q = $('#pe-cq').value.trim(), box = $('#pe-csug');
  if (q.length < 2) { box.innerHTML = ''; return; }
  try {
    const r = await api(`/api/pedidos/clientes?q=${encodeURIComponent(q)}`);
    box.innerHTML = r.map((c, i) => `<a href="#" data-i="${i}"><b>${escH(c.nombre)}</b> <small class="muted">${escH([c.nif, c.ciudad].filter(Boolean).join(' · '))}</small></a>`).join('') || '<small class="muted">Ningún cliente con ese nombre</small>';
    box.querySelectorAll('a').forEach(a => a.onclick = e => { e.preventDefault(); PE.cli = r[+a.dataset.i]; PE.dir = null; peGuarda(); box.innerHTML = ''; $('#pe-cq').value = ''; pePinta(); peDirs(); });
  } catch (e) { box.innerHTML = `<small class="err">${escH(e.message)}</small>`; }
});

$('#pe-aq').oninput = peDebounce(async () => {
  const q = $('#pe-aq').value.trim(), box = $('#pe-asug');
  if (q.length < 2) { box.innerHTML = ''; return; }
  try {
    const r = (await api(`/api/articulos?q=${encodeURIComponent(q)}`)).articulos;
    box.innerHTML = r.map((a, i) => `<a href="#" data-i="${i}"><b>${escH(a.nombre)}</b> <small class="muted">${escH(a.ref)}${a.ref_fab ? ' · ' + escH(a.ref_fab) : ''} · ${eur(a.pvp)} · stock ${a.stock.map(s => `${s.donde.replace('Tienda ', 'T')} ${+s.hay}`).join(' / ')}</small></a>`).join('') || '<small class="muted">Ningún artículo</small>';
    box.querySelectorAll('a').forEach(x => x.onclick = e => { e.preventDefault(); peAnade(r[+x.dataset.i]); });
  } catch (e) { box.innerHTML = `<small class="err">${escH(e.message)}</small>`; }
});
$('#pe-aq').onkeydown = e => { if (e.key === 'Escape') { $('#pe-asug').innerHTML = ''; } };
$('#pe-ref').onchange = e => { PE.ref = e.target.value; peGuarda(); };
$('#pe-nota').onchange = e => { PE.nota = e.target.value; peGuarda(); };
$('#pe-nent').onchange = e => { PE.nent = e.target.value; peGuarda(); };
$('#pe-borra').onclick = () => { if (!PE.lin.length && !PE.cli) return; if (!confirm('¿Vaciar el pedido que estás haciendo?')) return; PE = { cli: null, lin: [], emp: PE.emp }; peGuarda(); peOpen(); $('#pe-res').innerHTML = ''; };

async function peCrea(confirmar) {
  if (!PE.cli) return alertBox('Elige primero el cliente');
  if (!PE.lin.length) return alertBox('Añade algún artículo');
  const emp = $('#pe-emp'), nomEmp = emp.options[emp.selectedIndex]?.text || '';
  const avi = confirmar && $('#pe-avi').checked && PE.cli.email;
  if (!confirm(`¿${confirmar ? 'Crear el PEDIDO' + (avi ? ' y enviar la confirmación a ' + PE.cli.email : '') : 'Guardar el PRESUPUESTO'} de ${PE.cli.nombre} (${PE.lin.length} artículos) en ${nomEmp}?`)) return;
  const bs = [$('#pe-pre'), $('#pe-ped')]; bs.forEach(b => b.disabled = true);
  try {
    const r = await jpost('/api/pedidos/crear', { empresa: +emp.value, partner_id: PE.cli.id, ref: $('#pe-ref').value, nota: $('#pe-nota').value, confirmar,
      entrega: PE.dir || 0, nota_entrega: $('#pe-nent').value, avisar: confirmar && $('#pe-avi').checked && !!PE.cli.email,
      lineas: PE.lin.map(l => ({ product_id: l.product_id, cantidad: l.cantidad, precio: l.precio })) });
    $('#pe-res').innerHTML = `<div class="pe-ok"><b>✅ ${r.estado === 'sale' ? 'Pedido' : 'Presupuesto'} ${escH(r.nombre)} creado</b>${r.avisado ? ' · 📧 confirmación enviada al cliente' : ''}${r.aviso ? ' · ⚠️ ' + escH(r.aviso) : ''} · ${escH(PE.cli.nombre)} · ${eur(r.base)} + IVA = <b>${eur(r.total)}</b>
      <table class="pe-t">${r.lineas.map(l => `<tr><td>${escH(l.articulo)}</td><td>${+l.cantidad}</td><td>${eur(l.precio)}${l.dto ? ` −${l.dto}%` : ''}</td><td>${eur(l.subtotal)}</td></tr>`).join('')}</table>
      <a class="btn" target="_blank" rel="noopener" href="${r.odoo}">Abrir en Odoo ↗</a> <a class="btn" href="#ventas">Ver en Ventas</a></div>`;
    PE = { cli: null, lin: [], emp: PE.emp }; peGuarda(); $('#pe-ref').value = ''; $('#pe-nota').value = ''; $('#pe-nent').value = ''; $('#pe-hbox').hidden = true; pePinta(); peDirs();
  } catch (e) { alertBox(e.message); }
  bs.forEach(b => b.disabled = false);
}
$('#pe-pre').onclick = () => peCrea(false);
$('#pe-ped').onclick = () => peCrea(true);

/* ---------- Artículos ---------- */
function arOpen() { $('#ar-q').focus(); if (!$('#ar-list').innerHTML) $('#ar-list').innerHTML = '<p class="muted">Escribe al menos 2 letras: nombre, ref. Folder, ref. fabricante o código de barras.</p>'; }

$('#ar-q').oninput = peDebounce(async () => {
  const q = $('#ar-q').value.trim(), box = $('#ar-list');
  if (q.length < 2) return;
  $('#ar-info').textContent = 'Buscando…';
  try {
    const d = await api(`/api/articulos?q=${encodeURIComponent(q)}`), r = d.articulos;
    $('#ar-info').textContent = r.length >= 40 ? 'Hay más de 40: afina la búsqueda' : `${r.length} artículos`;
    box.innerHTML = r.map((a, i) => `<div class="ar-c">
      <div class="ar-h"><b>${escH(a.nombre)}</b><span>PVP <b>${eur(a.pvp)}</b>${a.coste ? ` · coste ${eur(a.coste)}` : ''}</span></div>
      <div class="muted">Ref. Folder <b>${escH(a.ref || '—')}</b>${a.ref_fab ? ` · Ref. fabricante <b>${escH(a.ref_fab)}</b>` : ''}${a.ean ? ' · EAN ' + escH(a.ean) : ''} · ${escH(a.ud)}${a.venta ? '' : ' · <span class="err">no se vende</span>'}</div>
      <div class="ar-st">${a.stock.map(s => `<span class="${s.hay - s.reservado > 0 ? 'si' : 'no'}">${escH(s.donde)}: <b>${+s.hay}</b>${s.reservado ? ` <small>(${+s.reservado} res.)</small>` : ''}</span>`).join('')}</div>
      ${a.proveedores.length ? `<div class="ar-pr">${a.proveedores.map(p => `<span>${escH(p.proveedor)} ${p.precio ? eur(p.precio) : ''}${p.ref ? ` <small>(${escH(p.ref)})</small>` : ''}</span>`).join('')}</div>` : '<div class="ar-pr muted">Sin proveedor en la ficha</div>'}
      <div class="ar-b"><button class="btn" data-i="${i}">＋ Añadir al pedido</button> <a class="lnk" target="_blank" rel="noopener" href="${d.odoo_url}/odoo/action-sale.product_template_action/${a.tmpl_id}">abrir en Odoo ↗</a></div>
    </div>`).join('') || '<p class="muted">Ningún artículo.</p>';
    box.querySelectorAll('[data-i]').forEach(b => b.onclick = () => { peAnade(r[+b.dataset.i]); b.textContent = `✔ En el pedido (${PE.lin.find(l => l.product_id === r[+b.dataset.i].id).cantidad})`; });
  } catch (e) { $('#ar-info').textContent = ''; box.innerHTML = `<p class="err">${escH(e.message)}</p>`; }
}, 400);

pePinta();
