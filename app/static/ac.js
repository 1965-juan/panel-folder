/* ---------- Panel AC ---------- */
let AC = null, ACF = 'todos', ACV = 'todos', ACSEL = {}, ACADD = null;
const acNum = n => (Math.round((n || 0) * 100) / 100).toLocaleString('es-ES');
function acLocs() { return (AC.locations || []); }
function acAvail(l) { return acLocs().filter(x => !x.read_only).reduce((a, x) => a + Math.max(0, l.stock_by_loc[String(x.id)] || 0), 0); }
function acCat(l) {
  if (l.pending_print_docs) return 'imprimir';
  const a = acAvail(l);
  if (a >= l.qty_pending && l.qty_pending > 0) return 'stock';
  if (l.prov_requested) return 'prov';
  return a > 0 ? 'falta' : 'sin';
}
const AC_FILTROS = [['todos', 'Todos'], ['stock', 'Se puede servir'], ['falta', 'Falta stock'], ['sin', 'Sin stock'], ['prov', 'Pedido a prov.'], ['imprimir', 'Pend. imprimir']];
const AC_CAT = { stock: ['Stock', 'b-ok'], falta: ['Falta stock', 'b-warn'], sin: ['Sin stock', 'b-bad'], prov: ['Ped. prov.', 'b-prov'], imprimir: ['Pend. imprimir', 'b-prov'] };
function acGroups() {
  const g = [];
  (AC.orders || []).forEach(o => g.push({ tipo: 'cliente', id: o.order_id, ref: o.order_name, nombre: o.partner_name, fecha: o.date, o, lines: o.lines }));
  (AC.reposicion || []).forEach(p => g.push({ tipo: 'reposicion', id: p.picking_id, ref: p.picking_name, nombre: p.store_name, fecha: p.date, o: p, lines: p.lines.map(l => ({ ...l, line_id: l.move_id })) }));
  return g;
}
function acPass(g, l) {
  if (ACF === 'todos') return true;
  if (ACF === 'stock' && g.tipo === 'cliente') return g.lines.every(x => acCat(x) === 'stock');   // clientes: solo pedidos completos
  return acCat(l) === ACF;
}
function acMatch(g, l, q) {
  if (!q) return true;
  return [g.ref, g.nombre, g.o.ref_cliente, l.product_name, l.product_code, l.product_barcode].some(t => String(t || '').toLowerCase().includes(q));
}
function acChips() {
  const gs = acGroups(), cnt = {};
  AC_FILTROS.forEach(([k]) => cnt[k] = 0);
  gs.forEach(g => g.lines.forEach(l => { cnt.todos++; if (acPass({ ...g }, l) || true) { const c = acCat(l); cnt[c] = (cnt[c] || 0) + 1; } }));
  cnt.stock = gs.reduce((a, g) => a + (g.tipo === 'cliente' ? (g.lines.every(x => acCat(x) === 'stock') ? g.lines.length : 0) : g.lines.filter(x => acCat(x) === 'stock').length), 0);
  $('#ac-f').innerHTML = AC_FILTROS.map(([k, t]) => `<button data-f="${k}" class="${ACF === k ? 'on' : ''}">${t} (${cnt[k] || 0})</button>`).join('');
  $('#ac-f').querySelectorAll('button').forEach(b => b.onclick = () => { ACF = b.dataset.f; acRender(); });
  const nc = (AC.orders || []).length, nt = (AC.reposicion || []).length;
  $('#ac-ver').innerHTML = '<span class="muted">Ver:</span>' + [['todos', `Todos (${nc + nt})`], ['cliente', `Clientes (${nc})`], ['reposicion', `Tiendas (${nt})`]]
    .map(([k, t]) => `<button data-v="${k}" class="${ACV === k ? 'on' : ''}">${t}</button>`).join('');
  $('#ac-ver').querySelectorAll('button').forEach(b => b.onclick = () => { ACV = b.dataset.v; acRender(); });
}
function acSelCount() { return Object.values(ACSEL).filter(s => Object.values(s.t).some(v => v > 0)).length; }
function acRender() {
  acChips();
  const q = $('#ac-q').value.trim().toLowerCase(), box = $('#ac-list'), locs = acLocs();
  const ncol = 7 + locs.length;
  box.innerHTML = `<table class="act"><colgroup><col class="c-ck"><col class="c-art"><col class="c-n"><col class="c-n"><col class="c-n"><col class="c-est">
    ${locs.map(x => `<col class="c-loc">`).join('')}<col class="c-acc"></colgroup>
    <thead><tr><th></th><th class="l">Artículo</th><th>Pedido</th><th>Entreg.</th><th>Pend.</th><th>Estado</th>
    ${locs.map(x => `<th class="${x.read_only ? 'ro' : ''}${x.is_central ? ' cen' : ''}" title="${escH(x.name)}">${escH(x.code)}</th>`).join('')}<th></th></tr></thead><tbody></tbody></table>`;
  const tb = box.querySelector('tbody');
  let n = 0;
  acGroups().forEach(g => {
    if (ACV !== 'todos' && g.tipo !== ACV) return;
    const lines = g.lines.filter(l => acPass(g, l) && acMatch(g, l, q));
    if (!lines.length) return;
    n++;
    const o = g.o, h = document.createElement('tr'); h.className = 'ord ' + g.tipo;
    const tags = g.tipo === 'cliente'
      ? `${o.delivery_label ? `<span class="t-del">${escH(o.delivery_label)}</span>` : ''}${!o.has_payment_term ? '<span class="t-bad">Sin condiciones de pago</span>' : ''}${o.ref_cliente ? `<span class="t-cli">👤 ${escH(o.ref_cliente)}</span>` : ''}`
      : '<span class="t-tie">Reposición tienda</span>';
    const odooHref = g.tipo === 'cliente' ? `${ODOO}/odoo/sale.order/${g.id}` : `${ODOO}/odoo/action-stock.action_picking_tree_all/${g.id}`;
    h.innerHTML = `<td colspan="${ncol}"><div class="ordin"><b>${escH(g.ref)}</b><span class="nm">${escH(g.nombre)}</span><span class="muted">${escH(g.fecha)}</span>${tags}
      <span class="muted">${g.lines.length} línea${g.lines.length > 1 ? 's' : ''}</span>
      <span class="sp"><button class="lnk" data-add>+ Añadir artículo</button><button class="lnk" data-auto>Rellenar desde stock</button><a class="lnk" target="_blank" rel="noopener" href="${odooHref}">Abrir en Odoo</a></span></div></td>`;
    h.querySelector('[data-add]').onclick = () => acAddOpen(g);
    h.querySelector('[data-auto]').onclick = () => { lines.forEach(l => acAuto(g, l)); acRender(); };
    tb.appendChild(h);
    lines.forEach((l, i) => tb.appendChild(acLine(g, l, locs, i)));
  });
  if (!n) tb.insertAdjacentHTML('beforeend', `<tr><td colspan="${ncol}" class="empty" style="padding:14px">No hay nada con este filtro</td></tr>`);
  const c = acSelCount(); $('#ac-ok').disabled = !c; $('#ac-ok').textContent = c ? `✓ Validar selección (${c})` : '✓ Validar selección';
}
function acKey(g, l) { return g.tipo + ':' + l.line_id; }
function acAuto(g, l) {
  let falta = l.qty_pending; const t = {};
  const locs = acLocs().filter(x => !x.read_only).sort((a, b) => (b.is_central - a.is_central));
  for (const x of locs) { if (falta <= 0) break; const s = Math.max(0, l.stock_by_loc[String(x.id)] || 0); const q = Math.min(s, falta); if (q > 0) { t[x.id] = q; falta -= q; } }
  if (Object.keys(t).length) ACSEL[acKey(g, l)] = { tipo: g.tipo, id: l.line_id, t };
}
function acLine(g, l, locs, i) {
  const r = document.createElement('tr');
  const k = acKey(g, l), sel = (ACSEL[k] || {}).t || {}, cat = acCat(l), [bt, bc] = AC_CAT[cat];
  r.className = 'ln ' + cat + (i % 2 ? ' odd' : '');
  r.innerHTML = `<td class="ckc"><input type="checkbox" class="ck" ${Object.values(sel).some(v => v > 0) ? 'checked' : ''}></td>
    <td class="art"></td>
    <td class="n qe" title="Pulsa para cambiar la cantidad"><b>${acNum(l.qty_ordered)}</b> <span class="ed">✎</span></td>
    <td class="n">${acNum(l.qty_delivered)}</td><td class="n pend">${acNum(l.qty_pending)}</td>
    <td class="est"><span class="bdg ${bc}">${bt}</span>${l.qty_on_order ? `<small class="oo">${acNum(l.qty_on_order)} pedidas</small>` : ''}</td>
    ${locs.map(x => { const s = l.stock_by_loc[String(x.id)] || 0, max = Math.max(0, Math.min(s, l.qty_pending));
      return `<td class="loc ${x.read_only ? 'ro' : ''}${x.is_central ? ' cen' : ''}">
      <span data-fill="${x.id}" class="stk ${s > 0 ? (x.read_only ? 'vp' : 'si can') : 'no'}" title="Stock en ${escH(x.name)}${s > 0 && !x.read_only ? ' · pulsa para coger' : ''}">${acNum(s)}</span>
      ${x.read_only ? '<span class="hueco"></span>' : `<input type="number" min="0" max="${max}" step="1" data-loc="${x.id}" value="${sel[x.id] || 0}" ${s > 0 ? '' : 'disabled'}>`}</td>`; }).join('')}
    <td class="acts"><button class="del" title="Quitar la línea">🗑</button><button class="val">Validar</button></td>`;
  r.querySelector('.art').textContent = l.product_name;
  r.querySelector('.art').title = l.product_name + (l.product_barcode ? ' · ' + l.product_barcode : '');
  const save = () => {
    const t = {}; r.querySelectorAll('input[data-loc]').forEach(i => { const v = parseFloat(i.value); if (v > 0) t[i.dataset.loc] = v; });
    if (Object.keys(t).length) ACSEL[k] = { tipo: g.tipo, id: l.line_id, t }; else delete ACSEL[k];
    r.querySelector('.ck').checked = !!Object.keys(t).length;
    const c = acSelCount(); $('#ac-ok').disabled = !c; $('#ac-ok').textContent = c ? `✓ Validar selección (${c})` : '✓ Validar selección';
  };
  r.querySelectorAll('input[data-loc]').forEach(i => i.oninput = save);
  r.querySelectorAll('.stk.can').forEach(s => s.onclick = () => {
    const i = r.querySelector(`input[data-loc="${s.dataset.fill}"]`);
    const ya = [...r.querySelectorAll('input[data-loc]')].filter(x => x !== i).reduce((a, x) => a + (parseFloat(x.value) || 0), 0);
    i.value = Math.max(0, Math.min(parseFloat(s.textContent.replace(',', '.')) || 0, l.qty_pending - ya)); save();
  });
  r.querySelector('.ck').onchange = e => { if (e.target.checked) { acAuto(g, l); acRender(); } else { delete ACSEL[k]; acRender(); } };
  const qe = r.querySelector('.qe');
  qe.onclick = () => {
    if (qe.querySelector('input')) return;
    qe.innerHTML = `<input class="qin" type="number" min="0.01" step="any" value="${l.qty_ordered}">`;
    const inp = qe.querySelector('input'); inp.focus(); inp.select();
    let hecho = false;
    const guardar = async () => {
      if (hecho) return; hecho = true;
      const qty = parseFloat(String(inp.value).replace(',', '.'));
      if (!(qty > 0) || qty === l.qty_ordered) { if (qty <= 0) alertBox('Para quitar la línea usa la papelera'); return acRender(); }
      try { await jpost(`/api/ac/cantidad?company=${company}`, { tipo: g.tipo, id: l.line_id, qty }); acLoad(); } catch (e) { alertBox(e.message); acRender(); }
    };
    inp.onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); guardar(); } else if (e.key === 'Escape') { hecho = true; acRender(); } };
    inp.onblur = guardar;
  };
  // Enter en una casilla de ubicación valida esa línea
  r.querySelectorAll('input[data-loc]').forEach(i => i.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); save(); if (ACSEL[k]) acValidar([k]); } }));
  r.querySelector('.del').onclick = async () => {
    if (!confirm(`¿Quitar del pedido ${g.ref}?\n${l.product_name}`)) return;
    try { await jpost(`/api/ac/borrar?company=${company}`, { tipo: g.tipo, id: l.line_id }); delete ACSEL[k]; acLoad(); } catch (e) { alertBox(e.message); }
  };
  r.querySelector('.val').onclick = () => { if (!ACSEL[k]) acAuto(g, l); if (!ACSEL[k]) return alertBox('No hay stock que coger para esta línea'); acValidar([k]); };
  return r;
}
async function acValidar(keys) {
  const body = { lineas: [], reposicion: [] };
  keys.forEach(k => { const s = ACSEL[k]; if (s) (s.tipo === 'cliente' ? body.lineas : body.reposicion).push({ id: s.id, transfers: s.t }); });
  if (!body.lineas.length && !body.reposicion.length) return;
  const b = $('#ac-ok'); b.disabled = true; b.textContent = 'Validando…';
  try {
    const r = await jpost(`/api/ac/validar?company=${company}`, body);
    keys.forEach(k => delete ACSEL[k]);
    const d = $('#ac-docs');
    d.innerHTML = `<b>✓ Validado.</b> ${r.picking_names.length ? 'Traspasos: ' + r.picking_names.map(escH).join(', ') : ''}
      ${r.documents.length ? '<span>Para imprimir:</span> ' + r.documents.map(x => `<a target="_blank" rel="noopener" href="${ODOO}/report/pdf/${encodeURIComponent(x.report_name)}/${x.id}">🖨 ${escH(x.name || x.type || 'documento')}</a>`).join(' ') : ''}
      <button class="x2" onclick="this.parentNode.hidden=true">×</button>`;
    d.hidden = false;
    acLoad();
  } catch (e) { alertBox(e.message); acRender(); }
}
$('#ac-ok').onclick = () => acValidar(Object.keys(ACSEL));
let acT; $('#ac-q').oninput = () => { clearTimeout(acT); acT = setTimeout(acRender, 200); };
async function acLoad() {
  try { AC = await api(`/api/ac/datos?company=${company}`); acRender(); } catch (e) { showErr(e.message); }
}
function acAddOpen(g) {
  ACADD = g; $('#ac-add-t').textContent = `Añadir artículo a ${g.ref} · ${g.nombre}`;
  $('#ac-add-q').value = ''; $('#ac-add-n').value = 1; $('#ac-add-r').innerHTML = '<p class="muted">Escribe al menos 2 letras o pasa el código de barras</p>';
  $('#ac-modal').hidden = false; setTimeout(() => $('#ac-add-q').focus(), 50);
}
$('#ac-add-x').onclick = () => $('#ac-modal').hidden = true;
$('#ac-modal').onclick = e => { if (e.target.id === 'ac-modal') $('#ac-modal').hidden = true; };
let acQ; $('#ac-add-q').oninput = () => { clearTimeout(acQ); acQ = setTimeout(async () => {
  const l = await api(`/api/ac/productos?company=${company}&q=${encodeURIComponent($('#ac-add-q').value)}`);
  const box = $('#ac-add-r'); box.innerHTML = l.length ? '' : '<p class="muted">No hay artículos con ese texto (no se crea ninguno nuevo)</p>';
  l.forEach(p => { const b = document.createElement('button'); b.className = 'ac-pick';
    b.innerHTML = `<span></span><b>${eur(p.lst_price)}</b>`; b.firstChild.textContent = p.display_name;
    b.onclick = async () => { try { await jpost(`/api/ac/anadir?company=${company}`, { tipo: ACADD.tipo, id: ACADD.id, product_id: p.id, qty: parseFloat($('#ac-add-n').value) || 1 }); $('#ac-modal').hidden = true; acLoad(); } catch (e) { alertBox(e.message); } };
    box.appendChild(b); });
}, 300); };
