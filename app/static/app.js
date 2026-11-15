const $ = s => document.querySelector(s);
const eur = n => (n || 0).toLocaleString('es-ES', { style: 'currency', currency: 'EUR' });
const fdate = s => s ? s.slice(8, 10) + '/' + s.slice(5, 7) + '/' + s.slice(2, 4) : '';
let ODOO = '', company = 'all', MODS = [];
try { company = localStorage.getItem('pf_company') || 'all'; } catch (e) {}

async function api(path) {
  const r = await fetch(path, { credentials: 'same-origin' });
  if (r.status === 401) { location.href = '/entrar'; return; }
  const j = await r.json();
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}

function showErr(msg) { const e = $('#err'); e.textContent = msg; e.hidden = !msg; }

// sin ORD/nº de pedido del cliente: vacío o con nuestro propio nº de pedido (S01234) puesto por Odoo
const faltaOrd = c => !!c.pide_ord && (!c.su_ref || /^S\d{4,}$/i.test(c.su_ref.trim()));

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
  if (c.model === 'account.move' && !c.borrador) foot += ` · vence ${fdate(c.vence)}` + (c.vencida ? ' (vencida)' : '');
  if (c.borrador && c.origen) { const o = c.origen.split(/,\s*/); foot += ` · ${o.slice(0, 2).join(', ')}${o.length > 2 ? ` y ${o.length - 2} más` : ''}`; }
  d.innerHTML = `<div class="c1"><span class="ref"></span>${right}</div>
    <div class="cli"></div><div class="c3"><span class="emp"></span><span>${foot}</span></div>`;
  d.querySelector('.ref').textContent = c.ref;
  if (c.enviado || c.reclamado) d.querySelector('.cli').insertAdjacentHTML('afterend', `<div class="marcas">${c.enviado ? `<span class="mk mk-env">📧 Enviado${/\d/.test(c.enviado) ? ' ' + fdate(c.enviado) : ''}</span>` : ''}${c.reclamado ? `<span class="mk mk-rec">🔔 Reclamado ${fdate(c.reclamado)}</span>` : ''}</div>`);
  if (c.borrador) d.querySelector('.cli').insertAdjacentHTML('afterend', `<div class="marcas">${faltaOrd(c) ? '<span class="mk mk-rec">⚠ Falta la ORD / nº pedido del cliente (Gimeno)</span>' : `${c.su_ref ? `<span class="mk mk-env">Ref. ${escH(c.su_ref)}</span>` : ''}<span class="mk">Sin validar ni enviar</span>`}</div>`);
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
    const verFac = Array.isArray(d.facturas);
    document.querySelectorAll('.col[data-k="facturas"], .kpi.k-fac').forEach(x => x.hidden = !verFac);
    d.facturas = d.facturas || [];
    if (verFac) fill('facturas', d.facturas);
    const sum = (l, k = 'total') => eur(l.reduce((a, c) => a + (c[k] || 0), 0));
    document.querySelectorAll('.col[data-k="borradores"], .kpi.k-bor').forEach(x => x.hidden = !verFac);
    d.borradores = d.borradores || [];
    if (verFac) fill('borradores', d.borradores);
    const sinOrd = d.borradores.filter(b => faltaOrd(b)).length;
    $('#k-bor').textContent = d.borradores.length;
    $('#k-bor-e').textContent = `${sum(d.borradores)}${sinOrd ? ` · ${sinOrd} sin ORD` : ''}`;
    $('#bor-h').textContent = sinOrd ? `Sin validar ni enviar · ⚠ ${sinOrd} sin ORD` : 'Sin validar ni enviar';
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
  $('#d-hist').innerHTML = ''; $('#d-acc').innerHTML = '';
  $('#drawer').hidden = false;
  if (c.model !== 'sale.order') {
    tb.innerHTML = c.borrador
      ? (faltaOrd(c)
        ? '<tr><td colspan="5">Factura en borrador <b>sin la ORD</b> (Grupo Gimeno · pdf-facturas). Ábrela en Odoo, pon la ORD en «Referencia del cliente», pulsa <b>Confirmar</b> y luego <b>Enviar</b>.</td></tr>'
        : '<tr><td colspan="5">Factura en borrador. Ábrela en Odoo, pulsa <b>Confirmar</b> y luego <b>Enviar</b>.</td></tr>')
      : '<tr><td colspan="5">Para ver la factura, ábrela en Odoo.</td></tr>';
    return;
  }
  try {
    const d = await api(`/api/ventas/${c.id}?company=${company}`);
    dAcciones(c, d);
    tb.innerHTML = '';
    d.lineas.forEach(l => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td></td><td>${l.product_uom_qty}</td><td>${l.qty_delivered}</td><td>${eur(l.price_unit)}</td><td>${eur(l.price_subtotal)}</td>`;
      tr.firstChild.textContent = l.product_id ? l.product_id[1] : l.name;
      tb.appendChild(tr);
      if (l.stock || (l.pendiente && l.pendiente.length)) {
        const t2 = document.createElement('tr'); t2.className = 'd-stk';
        t2.innerHTML = `<td colspan="5">${(l.stock || []).map(s => `<span class="${s.hay > 0 ? 'si' : 'no'}">${escH(s.donde)} <b>${+s.hay}</b></span>`).join('')}
          ${(l.pendiente || []).map(p => p.borrador
            ? `<span class="pd pd-b" title="Está en el pedido en borrador del proveedor: falta lanzarlo desde Compras">🛒 ${+p.cantidad} pedido a ${escH(p.proveedor.split(' ').slice(0, 2).join(' '))} (${escH(p.po)}, sin lanzar)</span>`
            : `<span class="pd">🚚 ${+p.cantidad} de ${escH(p.proveedor.split(' ').slice(0, 2).join(' '))} (${escH(p.po)}${p.fecha ? ', prev. ' + fdate(p.fecha) : ''})</span>`).join('')}
          ${d.estado === 'sale' && l.product_id ? '<a href="#" class="lnk d-pv-t">🛒 Pedir a proveedor</a><div class="d-pv" hidden></div>' : ''}</td>`;
        tb.appendChild(t2);
        const tg = t2.querySelector('.d-pv-t');
        if (tg) tg.onclick = e => { e.preventDefault(); const bx = t2.querySelector('.d-pv'); bx.hidden = !bx.hidden; if (!bx.hidden && !bx.dataset.ok) dPedirProv(bx, c, l); };
      }
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

// el PDF del albarán se abre en la empresa del pedido (si no, Odoo da «403 registros ultrasecretos» con otra empresa activa)
const albPdf = (id, cid) => `${ODOO}/report/pdf/stock.report_deliveryslip/${id}` + (cid ? `?context=${encodeURIComponent(JSON.stringify({ allowed_company_ids: [cid] }))}` : '');

// desplegable dentro de cada línea del pedido: proveedores de la ficha con su precio y pedirlo (se añade al borrador de ese proveedor)
async function dPedirProv(bx, c, l) {
  bx.innerHTML = '<small class="muted">Cargando proveedores…</small>';
  try {
    const d = await api(`/api/ac/proveedor/${l.product_id[0]}?company=${c.cid || company}`), pv = d.proveedores || [], e = d.estado || {};
    const falta = Math.max(1, (l.product_uom_qty || 0) - (l.qty_delivered || 0));
    bx.dataset.ok = 1;
    const otro = `<details class="d-np" ${pv.length ? '' : 'open'}><summary>＋ Añadir otro proveedor</summary>
        <input class="d-npq" placeholder="Buscar proveedor…" autocomplete="off"><div class="d-nps"></div>
        <div class="d-npf" hidden><b class="d-npn"></b> · precio de compra <input class="d-npp" type="number" min="0" step="0.01" placeholder="€" style="width:80px">
          <button class="ac-go d-npok">Añadir a la ficha</button></div></details>`;
    bx.innerHTML = pv.length ? `${e.total_on_order ? `<small class="muted">Ya pedido a proveedor: ${+e.total_on_order}</small>` : ''}
      <div class="d-pvl">${pv.map((p, i) => `<label><input type="radio" name="pv${l.id}" value="${p.partner_id}" ${i ? '' : 'checked'}> ${escH(p.partner_name)} <b>${eur(p.price)}</b></label>`).join('')}</div>
      <label>Cantidad <input type="number" min="1" step="any" value="${falta}" class="d-pvq"></label> <button class="ac-go d-pvgo">Pedir</button>${otro}`
      : `<small class="muted">Este artículo no tiene proveedores en su ficha.</small>${otro}`;
    // nuevo proveedor: se pone en la ficha del artículo con su precio y vuelve a salir la lista para pedírselo
    let npId = 0, npT;
    bx.querySelector('.d-npq').oninput = ev => { clearTimeout(npT); npT = setTimeout(async () => {
      const q = ev.target.value.trim(), sg = bx.querySelector('.d-nps'); if (q.length < 2) return sg.innerHTML = '';
      try { const r = await api(`/api/tablero/proveedores?q=${encodeURIComponent(q)}`);
        sg.innerHTML = r.map(p => `<a href="#" data-id="${p.id}">${escH(p.nombre)}</a>`).join('') || '<small class="muted">Nada</small>';
        sg.querySelectorAll('a').forEach(a => a.onclick = x => { x.preventDefault(); npId = +a.dataset.id; bx.querySelector('.d-npn').textContent = a.textContent;
          bx.querySelector('.d-npf').hidden = false; sg.innerHTML = ''; bx.querySelector('.d-npp').focus(); });
      } catch (x) { sg.innerHTML = `<small class="err">${escH(x.message)}</small>`; } }, 300); };
    bx.querySelector('.d-npok').onclick = async () => {
      if (!npId) return;
      try { await jpost(`/api/ac/proveedor_ficha?company=${c.cid || company}`, { product_id: l.product_id[0], partner_id: npId, price: bx.querySelector('.d-npp').value });
        delete bx.dataset.ok; dPedirProv(bx, c, l); } catch (x) { alertBox(x.message); } };
    if (!pv.length) return;
    bx.querySelector('.d-pvgo').onclick = async ev => {
      const b = ev.target, pid = bx.querySelector('input[type=radio]:checked').value, qty = parseFloat(bx.querySelector('.d-pvq').value);
      if (!(qty > 0)) return alertBox('Cantidad no válida');
      b.disabled = true; b.textContent = 'Pidiendo…';
      try { const r = await jpost(`/api/ac/pedir?company=${c.cid || company}`, { product_id: l.product_id[0], partner_id: +pid, qty, line_id: l.id });
        bx.innerHTML = `<small>✅ ${r.is_new ? 'Pedido de compra ' + escH(r.po_name) + ' creado' : 'Añadido al borrador ' + escH(r.po_name)} (${qty} ud.). Sale en Compras.</small>`;
      } catch (x) { alertBox(x.message); b.disabled = false; b.textContent = 'Pedir'; }
    };
  } catch (x) { bx.innerHTML = `<small class="err">${escH(x.message)}</small>`; }
}

function dAcciones(c, d) {
  const A = $('#d-acc'); let h = '';
  if (d.estado === 'draft' || d.estado === 'sent') {
    h += `<div class="d-ok">${c.enviado || c.reclamado ? `<div class="marcas">${c.enviado ? `<span class="mk mk-env">📧 Presupuesto enviado${/\d/.test(c.enviado) ? ' el ' + fdate(c.enviado) : ''}</span>` : '<span class="mk">Sin enviar</span>'}${c.reclamado ? `<span class="mk mk-rec">🔔 Reclamado el ${fdate(c.reclamado)}</span>` : ''}</div>` : '<div class="marcas"><span class="mk">Sin enviar ni reclamar</span></div>'}<button class="ac-go" id="d-conf">✔ Aceptar: pasar a pedido</button>
      <label><input type="checkbox" id="d-avi" ${d.email ? 'checked' : 'disabled'}> Avisar al cliente por email de que estamos preparando su material
      ${d.email ? `<small class="muted">(${escH(d.email)})</small>` : '<small class="muted">(el cliente no tiene email)</small>'}</label>
      <small class="d-ver">👁 Ver cómo quedan: <a target="_blank" href="/ventas/${c.id}/vista?tipo=preparacion&company=${company}">pedido en preparación</a> ·
        <a target="_blank" href="/ventas/${c.id}/vista?tipo=presupuesto&company=${company}">envío del presupuesto</a> ·
        <a target="_blank" href="/ventas/${c.id}/vista?tipo=sin_confirmar&company=${company}">sin confirmar</a></small>
      <div class="d-mails"><button class="btn" data-m="presupuesto" ${d.email ? '' : 'disabled'}>📧 Enviar el presupuesto por email</button>
      <button class="btn" data-m="sin_confirmar" ${d.email ? '' : 'disabled'}>🔔 Avisar: no hemos recibido confirmación (¿archivamos o cambios?)</button></div></div>`;
  }
  if (d.albaranes && d.albaranes.length) {
    const est = { draft: 'borrador', waiting: 'esperando', confirmed: 'esperando material', assigned: 'listo para servir', done: 'servido' };
    h += `<div class="d-alb">${d.albaranes.map(a => `<a class="btn" target="_blank" rel="noopener" href="${albPdf(a.id, c.cid)}">🖨 Albarán ${escH(a.nombre)}</a>
      <small class="muted">${est[a.estado] || a.estado}</small>`).join(' ')}</div>`;
  }
  if (d.facturar) {
    const hoy = new Date().toISOString().slice(0, 10);
    h += `<div class="d-fac"><b>🧾 Facturar lo servido</b>
      <label>Serie <select id="f-dia">${(d.diarios || []).map(j => `<option value="${j.id}">${escH(j.nombre)}</option>`).join('')}</select></label>
      <label>Fecha factura <input type="date" id="f-fec" value="${hoy}"></label>
      ${d.pide_ord ? `<label>ORD / nº pedido del cliente (Gimeno) <input id="f-ref" value="${escH(/^S\d{4,}$/i.test(d.su_ref || '') ? '' : (d.su_ref || ''))}" placeholder="obligatorio para subirla a su plataforma"></label>`
        : `<label>Ref. cliente (opcional) <input id="f-ref" value="${escH(d.su_ref || '')}"></label>`}
      <label><input type="checkbox" id="f-env" ${d.email_factura ? 'checked' : 'disabled'}> Enviar la factura por email ${d.email_factura ? `<small class="muted">(${escH(d.email_factura)})</small>` : '<small class="muted">(el cliente no tiene email)</small>'}</label>
      <div class="d-mails"><button class="ac-go" id="f-go">🧾 Facturar${d.email_factura ? ' y enviar' : ''}</button><button class="btn" id="f-bor">Dejar en borrador</button></div></div>`;
  }
  if (c.model === 'sale.order') {
    const bs = [];
    if (d.estado === 'draft' || d.estado === 'sent') bs.push(['cancelar', '🗑 Eliminar presupuesto', `¿Eliminar el presupuesto ${c.ref}? (se cancela en Odoo)`]);
    else if (c.estado === 'pedido' && c.entrega !== 'partial') bs.push(['cancelar', '🗑 Cancelar pedido', `¿Cancelar el pedido ${c.ref}? No se ha servido nada.`]);
    if (c.estado === 'pedido' && (d.albaranes || []).some(a => !['done', 'cancel'].includes(a.estado)))
      bs.unshift(['servir', '🚚 Servido: validar albarán y pasar a Albaranes', `¿Dar por servido TODO lo pendiente de ${c.ref}? Se valida el albarán en Odoo con las cantidades del pedido (descuenta stock) y pasa a Albaranes para facturar.`]);
    if (c.estado === 'pedido' && c.entrega === 'partial') bs.push(['cerrar_resto', '✂️ Dar por terminado (cancelar lo que falta)', `¿Cancelar lo que falta por servir de ${c.ref}? Lo ya servido pasará a pendiente de facturar.`]);
    bs.push(['ocultar', '🙈 Archivar del panel', `¿Quitar ${c.ref} del panel? En Odoo no se toca nada.`]);
    h += `<div class="d-del">${bs.map(x => `<button class="btn" data-ac="${x[0]}" data-q="${escH(x[2])}">${x[1]}</button>`).join('')}</div>`;
  }
  A.innerHTML = h;
  A.querySelectorAll('[data-ac]').forEach(x => x.onclick = async () => {
    if (!confirm(x.dataset.q)) return;
    x.disabled = true;
    try { const r = await jpost(`/api/ventas/${c.id}/accion?company=${company}`, { accion: x.dataset.ac });
      alertBox(r.mensaje); $('#drawer').hidden = true; load();
      if (r.albaranes) r.albaranes.forEach(a => window.open(albPdf(a.id, r.cid), '_blank')); }
    catch (e) { alertBox(e.message); x.disabled = false; }
  });
  A.querySelectorAll('[data-m]').forEach(m => m.onclick = async () => {
    const txt = m.dataset.m === 'presupuesto' ? `¿Enviar el presupuesto ${c.ref} en PDF a ${d.email}?` : `¿Enviar a ${d.email} el aviso de que no hemos recibido confirmación de ${c.ref}?`;
    if (!confirm(txt)) return;
    const t0 = m.textContent; m.disabled = true; m.textContent = 'Enviando…';
    try { await jpost(`/api/ventas/${c.id}/email?company=${company}`, { tipo: m.dataset.m }); m.textContent = '✅ Enviado'; alertBox(`Email enviado a ${d.email}`); load(); }
    catch (e) { alertBox(e.message); m.disabled = false; m.textContent = t0; }
  });
  const fac = async borrador => {
    const ref = ($('#f-ref').value || '').trim(), env = $('#f-env').checked && !borrador;
    const dia = $('#f-dia'), serie = dia.options[dia.selectedIndex]?.text || '';
    if (!borrador && d.pide_ord && !ref) { if (!confirm('Es del Grupo Gimeno y no tiene ORD: sin ella no la aceptan en su plataforma. ¿Dejarla en borrador hasta tener la ORD?')) return; return fac(true); }
    if (!confirm(borrador ? `¿Crear la factura de ${c.ref} en borrador (${serie})?` : `¿Facturar ${c.ref} en ${serie} con fecha ${fdate($('#f-fec').value)}${env ? ` y enviarla a ${d.email_factura}` : ''}?`)) return;
    const bt = $('#f-go'); bt.disabled = true; bt.textContent = 'Facturando…';
    try {
      const r = await jpost(`/api/ventas/${c.id}/accion?company=${company}`, { accion: 'facturar', diario: dia.value, fecha: $('#f-fec').value, ref, enviar: env, borrador });
      alertBox(r.mensaje); $('#drawer').hidden = true; load();
      if (r.facturas) r.facturas.forEach(f => window.open(`${ODOO}/report/pdf/account.report_invoice/${f.id}?context=${encodeURIComponent(JSON.stringify({ allowed_company_ids: [r.cid] }))}`, '_blank'));
    } catch (e) { alertBox(e.message); bt.disabled = false; bt.textContent = '🧾 Facturar'; }
  };
  if ($('#f-go')) { $('#f-go').onclick = () => fac(false); $('#f-bor').onclick = () => fac(true); }
  const b = $('#d-conf'); if (b) b.onclick = async () => {
    const avi = $('#d-avi').checked;
    if (!confirm(`¿Pasar ${c.ref} a pedido${avi ? ' y avisar al cliente por email' : ''}?`)) return;
    b.disabled = true; b.textContent = 'Confirmando…';
    try { const r = await jpost(`/api/ventas/${c.id}/confirmar?company=${company}`, { avisar: avi });
      alertBox(`${r.pedido} ya es pedido${r.avisado ? ' · cliente avisado por email' : ''}${r.aviso ? ' · ⚠️ ' + r.aviso : ''}`);
      $('#drawer').hidden = true; load();
    } catch (e) { alertBox(e.message); b.disabled = false; b.textContent = '✔ Aceptar: pasar a pedido'; }
  };
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
    if (ADMIN && !(window.YO && YO.viendo_como)) webClientes();
  } catch (e) { showErr(e.message); }
}
async function webClientes() {
  $('#wc-sec').hidden = false; $('#wc-t').innerHTML = '<p class="muted">Cargando…</p>';
  let L; try { L = await api('/api/web/clientes_activos'); } catch (e) { $('#wc-t').innerHTML = `<p class="err">${escH(e.message)}</p>`; return; }
  const activos = L.filter(x => x.ultimo).length;
  $('#wc-info').textContent = `${L.length} con acceso · ${activos} han entrado alguna vez · últimos 90 días`;
  $('#wc-t').innerHTML = L.length ? `<table class="co-t wc-t"><thead><tr><th>Cliente</th><th>Último acceso</th><th class="num">Entradas</th><th>Ha mirado</th><th class="num">Favoritos</th><th>Carrito</th><th></th></tr></thead><tbody>
    ${L.map(x => `<tr><td><b>${escH(x.empresa)}</b>${x.contacto !== x.empresa ? `<br><small class="muted">${escH(x.contacto)}</small>` : ''}<br><small class="muted">alta ${fdate(x.alta)}</small></td>
      <td>${x.ultimo ? fdate(x.ultimo.slice(0, 10)) + ' ' + x.ultimo.slice(11, 16) : '<span class="muted">nunca</span>'}</td>
      <td class="num">${x.entradas}</td>
      <td><small>${x.vistos.map(escH).join('<br>') || '<span class="muted">—</span>'}</small></td>
      <td class="num">${x.favoritos || (x.ultimo ? '<span class="tag-new">0</span>' : '0')}</td>
      <td>${x.carrito ? `<a class="lnk" target="_blank" href="${ODOO}/odoo/sale.order/${x.carrito.id}">${escH(x.carrito.ref)} · ${eur(x.carrito.total)}</a>` : ''}</td>
      <td><button class="btn" data-fv="${x.empresa_id}">⭐ Cargar favoritos</button></td></tr>`).join('')}</tbody></table>` : '<p class="empty">Ningún cliente con acceso todavía</p>';
  $('#wc-t').querySelectorAll('[data-fv]').forEach(b => b.onclick = async () => {
    b.disabled = true; b.textContent = 'Cargando…';
    try { const r = await jpost('/api/web/favoritos', { partner_id: +b.dataset.fv }); alertBox(`${r.cliente}: ${r.anadidos} favoritos nuevos (${r.en_web} artículos comprados en la web)`); webClientes(); }
    catch (e) { alertBox(e.message); b.disabled = false; b.textContent = '⭐ Cargar favoritos'; }
  });
}
(() => {
  const inp = $('#wc-q'); if (!inp) return; let t;
  inp.oninput = () => { clearTimeout(t); t = setTimeout(async () => {
    const q = inp.value.trim(); if (q.length < 2) return $('#wc-sug').innerHTML = '';
    const r = await api(`/api/web/clientes?q=${encodeURIComponent(q)}`).catch(() => []);
    $('#wc-sug').innerHTML = r.map(p => `<a href="#" data-id="${p.id}">${escH(p.nombre)}</a>`).join('') || '<small class="muted">Nada</small>';
    $('#wc-sug').querySelectorAll('a').forEach(a => a.onclick = async e => { e.preventDefault();
      if (!confirm(`¿Cargar en la web los favoritos de ${a.textContent} (lo comprado en los últimos 12 meses)?`)) return;
      try { const x = await jpost('/api/web/favoritos', { partner_id: +a.dataset.id }); alertBox(`${x.cliente}: ${x.anadidos} favoritos añadidos (${x.en_web} artículos)`); $('#wc-sug').innerHTML = ''; inp.value = ''; webClientes(); }
      catch (er) { alertBox(er.message); } });
  }, 300); };
})();
function route() {
  let v = ({ '#web': 'web', '#whatsapp': 'whatsapp', '#ac': 'ac', '#compras': 'compras', '#facturacion': 'facturacion', '#correo': 'correo', '#vencimientos': 'vencimientos', '#etiquetas': 'etiquetas', '#tablero': 'tablero', '#tienda': 'tienda', '#pedidos': 'pedidos', '#articulos': 'articulos' })[location.hash.split('?')[0]] || 'ventas';
  if (!location.hash && MODS.includes('compras') && !MODS.includes('ac')) v = 'compras';
  if (MODS.length && !MODS.includes(v)) v = MODS.includes('compras') ? 'compras' : MODS[0];
  document.querySelectorAll('.side nav a[data-v]').forEach(a => a.classList.toggle('on', a.dataset.v === v));
  $('#v-web').hidden = v !== 'web'; $('#v-ventas').hidden = v !== 'ventas'; $('#v-wa').hidden = v !== 'whatsapp'; $('#v-ac').hidden = v !== 'ac'; $('#v-co').hidden = v !== 'compras'; $('#v-fa').hidden = v !== 'facturacion'; $('#v-ma').hidden = v !== 'correo'; $('#v-ve').hidden = v !== 'vencimientos'; $('#v-et').hidden = v !== 'etiquetas'; $('#v-tb').hidden = v !== 'tablero'; $('#v-ti').hidden = v !== 'tienda'; $('#v-pe').hidden = v !== 'pedidos'; $('#v-ar').hidden = v !== 'articulos'; document.body.classList.toggle('et-on', v === 'etiquetas');
  $('#h1').textContent = ({ web: 'Web', whatsapp: 'WhatsApp', ac: 'Panel AC', compras: 'Albaranes/Facturas', tablero: 'Compras', tienda: 'Mi tienda', pedidos: 'Nuevo pedido', articulos: 'Artículos', facturacion: 'Facturación', correo: 'Correo', vencimientos: 'Vencimientos', etiquetas: 'Etiquetas' })[v] || 'Ventas';
  $('#q').hidden = v !== 'ventas'; $('#company').hidden = !['ventas', 'ac', 'compras'].includes(v);
  if (v === 'web') loadWeb(); else if (v === 'whatsapp') waOpen(); else if (v === 'ac') acLoad(); else if (v === 'compras') coOpen(); else if (v === 'facturacion') faOpen(); else if (v === 'correo') maOpen(); else if (v === 'vencimientos') veOpen(); else if (v === 'etiquetas') etOpen(); else if (v === 'tablero') tbOpen(); else if (v === 'tienda') tiOpen(); else if (v === 'pedidos') peOpen(); else if (v === 'articulos') arOpen(); else load();
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
    $('#upd').hidden = !yo.puede_actualizar; $('#clave-cl').hidden = !yo.puede_actualizar;
    ADMIN = !!yo.admin; MODS = yo.modulos || [];
    document.querySelectorAll('.side nav a[data-m]').forEach(a => a.hidden = !MODS.includes(a.dataset.m));
    $('#usr-btn').hidden = !(ADMIN || yo.viendo_como);
    window.YO = yo;
    // aviso de versión nueva del panel (se actualiza en el NAS para todos; aquí basta con recargar)
    (() => { const v0 = yo.version; let avisado = false;
      const mira = async () => { if (avisado || document.hidden) return;
        try { const r = await fetch('/api/yo', { cache: 'no-store' }); if (!r.ok) return; const j = await r.json();
          if (j.version && v0 && j.version !== v0) { avisado = true;
            const b = document.createElement('div'); b.className = 'upd-ban';
            b.innerHTML = `🔄 Hay una actualización del panel (versión ${escH(j.version)}) <button>Actualizar ahora</button>`;
            b.querySelector('button').onclick = () => location.reload(); document.body.appendChild(b); } } catch (_) { } };
      setInterval(mira, 120000); document.addEventListener('visibilitychange', mira); window.addEventListener('focus', mira); })();
    if (yo.admin && !yo.viendo_como) avisosTpv();
    // los enlaces a Odoo se abren solo con las empresas de este usuario marcadas
    document.addEventListener('click', e => {
      const a = e.target.closest && e.target.closest('a[href]');
      if (!a || !ODOO || !a.href.startsWith(ODOO) || !(YO.empresas_ids || []).length) return;
      try { const u = new URL(a.href); u.searchParams.set('cids', YO.empresas_ids.join('-')); a.href = u.toString(); } catch (_) { }
    }, true);
    if (yo.viendo_como) { const vb = $('#vc-ban'); vb.hidden = false; vb.innerHTML = `👁 Viendo como <b>${escH(yo.nombre)}</b><br><a href="#" id="vc-salir">volver a ADMINISTRADOR</a>`;
      $('#vc-salir').onclick = async e => { e.preventDefault(); await jpost('/api/ver_como', { usuario: '' }); location.href = '/'; }; }
    const emps = await api('/api/empresas');
    emps.forEach(e => { const o = document.createElement('option'); o.value = e.id; o.textContent = e.name; $('#company').appendChild(o); });
    if (emps.length === 1) {
      $('#company').querySelector('option[value=all]').remove();
      company = String(emps[0].id); $('#company').disabled = true;
    }
    $('#company').value = company;
    if ($('#company').value !== company) { company = 'all'; $('#company').value = 'all'; }
  } catch (e) { showErr(e.message); }
  route(); if (MODS.includes('web')) loadWeb(true); if (MODS.includes('whatsapp')) waBadge(); if (MODS.includes('compras')) coBadge();
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
function alertBox(m) { const e = $('#info'); e.textContent = m; e.hidden = !m; clearTimeout(alertBox.t); alertBox.t = setTimeout(() => e.hidden = true, 9000); }
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

/* ---------- asistente general (en todas las pantallas) ---------- */
(function () {
  const log = $('#as-log'), KEY = 'as_conv_v1';
  let H = []; try { H = JSON.parse(localStorage.getItem(KEY) || '[]'); } catch (e) { H = []; }
  const guarda = () => { try { localStorage.setItem(KEY, JSON.stringify(H.slice(-30))); } catch (e) {} };
  const pantalla = () => {
    const v = (document.querySelector('.side nav a.on') || {}).textContent || 'Ventas';
    const t = document.querySelector('section:not([hidden]) .wa-tabs button.on, section:not([hidden]) .ac-tabs button.on');
    return v.replace(/\d+/g, '').trim() + (t ? ' › ' + t.textContent.trim() : '');
  };
  const inl = t => escH(t).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
  const md = t => {
    const out = []; let tabla = [];
    const cierra = () => { if (!tabla.length) return; const filas = tabla.filter(f => !/^\|?\s*:?-{2,}/.test(f.replace(/\|/g, '|').trim().replace(/^\|/, '')));
      out.push('<table class="as-t">' + filas.map((f, i) => '<tr>' + f.trim().replace(/^\||\|$/g, '').split('|').map(c => `<${i ? 'td' : 'th'}>${inl(c.trim())}</${i ? 'td' : 'th'}>`).join('') + '</tr>').join('') + '</table>'); tabla = []; };
    t.split('\n').forEach(l => { if (/^\s*\|/.test(l)) { tabla.push(l); return; } cierra();
      if (/^\s*[·\-•] /.test(l)) out.push(`<span class="as-li">• ${inl(l.replace(/^\s*[·\-•] /, ''))}</span>`);
      else if (/^#{1,4} /.test(l)) out.push(`<b class="as-h4">${inl(l.replace(/^#+ /, ''))}</b>`);
      else out.push(inl(l) + '<br>'); });
    cierra(); return out.join('').replace(/(<br>){3,}/g, '<br><br>');
  };
  const grafico = g => {
    const v = (g.valores || []).map(Number), max = Math.max(...v, 1), et = g.etiquetas || [];
    return `<div class="as-g"><b>${escH(g.titulo || '')}</b>${v.map((x, i) => `<div class="as-gb"><span title="${escH(String(et[i]))}">${escH(String(et[i] ?? '')).slice(0, 26)}</span>
      <i style="width:${Math.max(2, x / max * 100)}%"></i><em>${x.toLocaleString('es-ES', { maximumFractionDigits: 2 })}</em></div>`).join('')}</div>`;
  };
  const pinta = (t, c) => {
    const d = document.createElement('div'); d.className = 'as-m ' + c;
    if (c !== 'a') { d.textContent = t; log.appendChild(d); log.scrollTop = 1e9; return d; }
    let txt = t, sug = [], gs = [];
    txt = txt.replace(/```sugerencias\s*([\s\S]*?)```/g, (_, j) => { try { sug = JSON.parse(j); } catch (e) {} return ''; });
    txt = txt.replace(/```grafico\s*([\s\S]*?)```/g, (_, j) => { try { gs.push(JSON.parse(j)); } catch (e) {} return ''; });
    d.innerHTML = md(txt.trim()) + gs.map(grafico).join('');
    log.appendChild(d);
    if (sug.length) { const s = document.createElement('div'); s.className = 'as-sug'; s.innerHTML = sug.map(x => `<button>${escH(x)}</button>`).join('');
      s.querySelectorAll('button').forEach(b => b.onclick = () => { $('#as-t').value = b.textContent; $('#as-f').requestSubmit(); }); log.appendChild(s); }
    log.scrollTop = 1e9; return d;
  };
  H.forEach(m => pinta(m.content, m.role === 'user' ? 'u' : 'a'));
  const abre = () => { $('#as').hidden = false; $('#as-bar').hidden = true; setTimeout(() => $('#as-t').focus(), 50); };
  $('#as-fab').onclick = abre;
  $('#as-x').onclick = () => { $('#as').hidden = true; $('#as-bar').hidden = false; };
  $('#as-bar').onsubmit = e => { e.preventDefault(); const t = $('#as-bar-t').value.trim(); abre(); if (t) { $('#as-t').value = t; $('#as-bar-t').value = ''; $('#as-f').requestSubmit(); } };
  $('#as-bar-t').onfocus = () => { if (!$('#as-bar-t').value) {} };
  $('#as-new').onclick = () => { H = []; guarda(); log.querySelectorAll('.as-m:not(.ini),.as-sug').forEach(x => x.remove()); };
  $('#as-big').onclick = () => $('#as').classList.toggle('grande');
  $('#as-t').onkeydown = e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('#as-f').requestSubmit(); } };
  $('#as-f').onsubmit = async e => {
    e.preventDefault(); const t = $('#as-t').value.trim(); if (!t) return;
    $('#as-t').value = ''; pinta(t, 'u'); const w = pinta('Mirando en Odoo…', 'w'); const b = $('#as-f button'); b.disabled = true;
    try {
      const r = await fetch('/api/asistente', { method: 'POST', credentials: 'same-origin', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ mensaje: t, historial: H, pantalla: pantalla() }) });
      const j = await r.json(); if (!r.ok) throw new Error(j.detail || r.statusText);
      w.remove(); pinta(j.texto || 'Hecho.', 'a'); H.push({ role: 'user', content: t }, { role: 'assistant', content: j.texto || 'Hecho.' }); guarda();
      if (j.cambios) { if (location.hash === '#ac') acLoad(); else route(); }
    } catch (er) { w.remove(); pinta(er.message, 'e'); }
    b.disabled = false; $('#as-t').focus();
  };
})();

/* ---------- Aviso al administrador: encargos recuperados en el TPV con el albarán ya validado ---------- */
async function avisosTpv() {
  let L; try { L = await api('/api/avisos/tpv_dobles'); } catch (_) { return; }
  document.querySelectorAll('.av-ban').forEach(x => x.remove());
  if (!L || !L.length) return;
  const b = document.createElement('button'); b.className = 'av-ban';
  b.innerHTML = `⚠️ ${L.length} encargo${L.length > 1 ? 's' : ''} recuperado${L.length > 1 ? 's' : ''} en el TPV con el albarán ya validado`;
  b.onclick = () => avisosVer(L); document.body.appendChild(b);
}
function avisosVer(L) {
  let m = document.getElementById('av-mod'); if (m) m.remove();
  m = document.createElement('div'); m.id = 'av-mod'; m.className = 'av-mod';
  m.innerHTML = `<div class="av-in"><button class="x" id="av-x">×</button><h3>⚠️ Encargos recuperados en el TPV con el albarán ya validado</h3>
    <p class="muted">El material salió dos veces del stock (albarán + ticket del TPV). Estos encargos se tenían que cobrar desde el panel (Mi tienda → Cobrar). Revisa también que el dinero haya entrado en la caja de la tienda que toca.</p>
    ${L.map(a => `<div class="av-c" data-p="${a.pos_id}"><div><b>${escH(a.ticket)}</b> · ${escH(a.tpv)} · ${escH(a.fecha.replace('T', ' '))} · <b>${eur(a.importe)}</b></div>
      <div class="muted">Cobrado en: ${a.pagos.map(p => `${escH(p.forma)} ${eur(p.importe)}`).join(' + ') || '—'}</div>
      ${a.pedidos.map(p => `<div>Pedido <b>${escH(p.nombre)}</b> · ${escH(p.cliente)} · almacén ${escH(p.almacen)} · albarán ${p.albaranes.map(x => `${escH(x.nombre)} (${fdate(x.fecha)})`).join(', ')}</div>`).join('')}
      <div class="av-b"><button class="ac-go" data-c="1">↩︎ Devolver al stock lo descontado de más</button><button class="btn" data-c="0">Ya está bien, quitar aviso</button></div></div>`).join('')}</div>`;
  document.body.appendChild(m);
  m.querySelector('#av-x').onclick = () => m.remove();
  m.onclick = e => { if (e.target === m) m.remove(); };
  m.querySelectorAll('[data-c]').forEach(x => x.onclick = async () => {
    const card = x.closest('.av-c'), corr = x.dataset.c === '1';
    if (corr && !confirm('¿Devolver al stock las unidades de los albaranes validados de este encargo?')) return;
    card.querySelectorAll('button').forEach(y => y.disabled = true);
    try { const r = await jpost('/api/avisos/tpv_dobles/resolver', { pos_id: +card.dataset.p, corregir: corr });
      card.innerHTML = `<div>✅ ${escH(r.mensaje)}</div>`; avisosTpv(); }
    catch (e) { alertBox(e.message); card.querySelectorAll('button').forEach(y => y.disabled = false); }
  });
}
