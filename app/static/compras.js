/* ---------- Compras: subir PDF, recepciones, facturas para Mayte y recurrentes ---------- */
let CO_TAB = null, CO_RECS = [], CO_MES = new Date().toISOString().slice(0, 7), CO_ALB = null;
const coEmpNombre = () => { const o = $('#company').selectedOptions[0]; return o ? o.textContent : ''; };
const coDate = s => s ? s.slice(8, 10) + '/' + s.slice(5, 7) + '/' + s.slice(0, 4) : '';
async function coBadge() {
  try { const f = await api(`/api/compras/facturas?company=${company}`); const n = f.filter(x => x.estado !== 'leyendo').length;
    const b = $('#nb-co'); b.textContent = n; b.hidden = !n; return f; } catch (e) { return null; }
}
function coTab(t) {
  CO_TAB = t;
  document.querySelectorAll('.co-tabs button').forEach(b => b.classList.toggle('on', b.dataset.t === t));
  ['subir', 'rec', 'fac', 'recu', 'ban'].forEach(k => $('#co-' + k).hidden = k !== t);
  ({ subir: coDocs, rec: coRec, fac: coFac, recu: coRecu, ban: bnPend })[t]();
}
document.querySelectorAll('.co-tabs button').forEach(b => b.onclick = () => coTab(b.dataset.t));
function coOpen() {
  // Mayte (solo compras) entra directamente en Facturas
  coTab(CO_TAB || (MODS.length && !MODS.includes('ac') ? 'fac' : 'subir'));
}
/* --- subir --- */
async function coSubir(files, tipo) {
  if (!files.length) return;
  if (company === 'all' && $('#company').options.length > 2) return alertBox('Elige arriba la empresa a la que pertenecen estos documentos');
  const fd = new FormData(); fd.append('tipo', tipo); fd.append('company', company);
  [...files].forEach(f => fd.append('archivos', f));
  const box = $('#co-docs'); box.insertAdjacentHTML('afterbegin', `<p class="co-up">Subiendo ${files.length} ${tipo === 'albaran' ? 'albarán(es)' : 'factura(s)'}…</p>`);
  try {
    const r = await fetch('/api/compras/subir', { method: 'POST', body: fd, credentials: 'same-origin' });
    const j = await r.json(); if (!r.ok) throw new Error(j.detail || r.statusText);
    const err = j.filter(x => x.error);
    const ok = j.length - err.length;
    alertBox((ok ? `✓ ${ok} subido(s). Se están leyendo: en unos segundos aparecen abajo en la Bandeja con lo que falta hacer. No hace falta volver a subirlos.` : '')
      + (err.length ? (ok ? ' · ' : '') + err.map(x => `${x.nombre}: ${x.error}`).join(' · ') : ''));
  } catch (e) { alertBox(e.message); }
  coDocs(); coBadge();
}
document.querySelectorAll('.drop').forEach(d => {
  const inp = d.querySelector('input');
  inp.onchange = () => { coSubir(inp.files, inp.dataset.tipo); inp.value = ''; };
  d.ondragover = e => { e.preventDefault(); d.classList.add('over'); };
  d.ondragleave = () => d.classList.remove('over');
  d.ondrop = e => { e.preventDefault(); d.classList.remove('over'); coSubir([...e.dataTransfer.files].filter(f => /pdf$/i.test(f.name) || f.type === 'application/pdf'), inp.dataset.tipo); };
});
const CO_EST = { pendiente: ['Por recibir', 'b-warn'], recibido: ['Recibido', 'b-ok'], en_odoo: ['En Odoo (borrador)', 'b-prov'], contabilizada: ['Contabilizada', 'b-ok'], error: ['Error', 'b-bad'], descartado: ['Descartado', ''] };
let CO_BJ_T = null;
const BJ_PASO = { leyendo: ['⏳ Leyendo', 'b-warn'], pendiente: ['Falta hacer algo', 'b-bad'], espera: ['Esperando almacén', 'b-warn'],
  ok: ['En borrador para Mayte', 'b-ok'], terminado: ['✓ Terminado', 'b-ok'], error: ['Error', 'b-bad'] };
async function coDocs() {
  $('#co-emp-n').textContent = company === 'all' ? 'elige empresa arriba' : coEmpNombre();
  clearTimeout(CO_BJ_T);
  let l;
  try { l = await api(`/api/compras/bandeja?company=${company}`); } catch (e) { showErr(e.message); return; }
  const pend = l.filter(x => x.paso !== 'terminado'), fin = l.filter(x => x.paso === 'terminado');
  const n = k => pend.filter(x => x.paso === k).length;
  $('#bj-res').textContent = pend.length ? `${pend.length} en curso · ${n('pendiente')} por hacer · ${n('espera')} esperando almacén · ${n('ok')} listas para el OK${n('leyendo') ? ' · ' + n('leyendo') + ' leyendo' : ''}` : 'Todo terminado';
  const eu2 = v => v || v === 0 ? eur(+v) : '';
  const card = x => {
    const [pt, pc] = BJ_PASO[x.paso] || [x.paso, ''];
    const tipo = x.tipo === 'albaran' ? '📦 Albarán' : '🧾 Factura';
    const btn = { cuadrar: `<button class="ac-go" data-a="cuadrar">⇄ ${x.tipo === 'factura' ? 'Cuadrar / crear pedido desde el PDF' : 'Cuadrar con pedido'}</button>`, cuadrar_factura: `<button class="lnk" data-a="cuadrar_factura">🔗 Enlazar con pedido existente</button>`,
      gasto: `<button class="lnk" data-a="gasto">🧾 Es un gasto (sin entrada)</button>`, recibido: `<button class="lnk" data-a="recibido">📦 Material recibido</button>`,
      ok: `<button class="ac-go" data-a="ok">✓ OK, contabilizar</button>`, quitar_marca: `<button class="lnk" data-a="quitar_marca">quitar marca</button>`,
      fichas: `<button class="lnk" data-a="fichas">📷 Fichas</button>`, releer: `<button class="lnk" data-a="releer">volver a leer</button>`,
      descartar: `<button class="lnk del" data-a="descartar">descartar</button>` };
    return `<div class="bj-card p-${x.paso}" data-id="${x.id}"><div class="bj-1"><span>${tipo}</span><b>${escH(x.proveedor || x.nombre)}</b>
        <span class="bdg ${pc}">${pt}</span></div>
      <div class="bj-2">${x.numero ? 'Nº ' + escH(x.numero) : ''} ${x.fecha ? '· ' + coDate(x.fecha) : ''} ${x.total ? '· <b>' + eu2(x.total) + '</b>' : ''}
        ${x.tipo_leido && x.tipo_leido !== (x.tipo === 'albaran' ? 'albaran' : 'factura') ? `<small class="muted">(el PDF parece ${escH(x.tipo_leido)})</small>` : ''}
        · <a target="_blank" rel="noopener" href="/api/compras/pdf/${x.id}">${escH(x.nombre)}</a> <small class="muted">${escH(x.usuario)} · ${hora(x.subido)}</small></div>
      <div class="bj-3">${escH(x.texto || '')}${x.aviso ? `<div class="pc-aviso">⚠️ ${escH(x.aviso)}</div>` : ''}</div>
      <div class="bj-4">${x.aviso && /proveedor/i.test(x.aviso) ? `<button class="ac-go" data-a="prov">＋ Buscar o crear el proveedor del PDF</button>` : ''}${(x.acciones || []).map(a => btn[a] || '').join(' ')}
        ${x.odoo_id ? `<a class="lnk" target="_blank" rel="noopener" href="${ODOO}/odoo/action-account.action_move_in_invoice_type/${x.odoo_id}">Abrir en Odoo</a>` : ''}</div></div>`;
  };
  $('#co-docs').innerHTML = pend.length ? pend.map(card).join('') : '<p class="empty">No hay nada pendiente. Arrastra arriba los PDF de albaranes o facturas.</p>';
  $('#bj-nfin').textContent = fin.length; $('#bj-fin-l').innerHTML = fin.map(card).join('');
  document.querySelectorAll('.bj-card [data-a]').forEach(b => b.onclick = async () => {
    const x = l.find(y => y.id === b.closest('.bj-card').dataset.id), a = b.dataset.a;
    try {
      if (a === 'cuadrar') return coPasar(x.id);
      if (a === 'prov') { if (!confirm(`¿Crear en Odoo el proveedor «${x.proveedor}» con el CIF de la factura?`)) return; const r = await jpost(`/api/compras/docs/${x.id}/proveedor_nuevo`, {}); alertBox(`Proveedor ${r.nombre} listo. Vuelvo a cuadrar el documento…`); setTimeout(coDocs, 8000); return coDocs(); }
      if (a === 'fichas') return coFichas({ pedido: x.compra.pedido });
      if (a === 'cuadrar_factura') return coFacCuadrar({ id: x.odoo_id, proveedor: x.proveedor, base: +x.base || 0 });
      if (a === 'releer') { await jpost(`/api/compras/docs/${x.id}/releer`, {}); return coDocs(); }
      if (a === 'descartar') { if (!confirm('¿Descartar este documento de la bandeja? (no borra nada de Odoo)')) return; await fetch('/api/compras/docs/' + x.id, { method: 'DELETE' }); return coDocs(); }
      if (a === 'recibido' && !confirm('¿Confirmas que el material de esta factura ha llegado? Quedará apuntado a tu nombre.')) return;
      if (a === 'gasto' || a === 'recibido' || a === 'quitar_marca') { await jpost(`/api/compras/facturas/${x.odoo_id}/marcar`, { tipo: a === 'quitar_marca' ? '' : a }); return coDocs(); }
      if (a === 'ok') { b.disabled = true; b.textContent = 'Contabilizando…'; await jpost(`/api/compras/facturas/${x.odoo_id}/ok?company=${company}`, {}); alertBox('Factura contabilizada'); coBadge(); return coDocs(); }
    } catch (e) { alertBox(e.message); coDocs(); }
  });
  if (pend.some(x => x.paso === 'leyendo') && !$('#co-subir').hidden) CO_BJ_T = setTimeout(coDocs, 6000);
}
/* --- cuadrar un albarán/factura con el pedido de compra abierto del proveedor --- */
async function coPasar(did, opts = {}) {
  const db = $('#co-db');
  db.innerHTML = `<h3>Cuadrar con el pedido</h3><p class="muted">Leyendo el PDF y cruzándolo con los pedidos de compra… (tarda unos segundos)</p>`;
  $('#co-drawer').hidden = false;
  let d;
  const qs = new URLSearchParams(); if (opts.releer) qs.set('releer', 1); if (opts.pedido) qs.set('pedido', opts.pedido);
  try { d = await jpost(`/api/compras/docs/${did}/cuadrar?${qs}`, opts.partner_id ? { partner_id: opts.partner_id } : {}); }
  catch (e) { db.innerHTML = `<h3>Cuadrar con el pedido</h3><p class="err">${escH(e.message)}</p>`; return; }
  const lec = d.lectura || {};
  const eu = n => (Math.round((+n || 0) * 100) / 100).toLocaleString('es-ES', { minimumFractionDigits: 2 }) + ' €';
  const fiBtn = (ref) => { db.insertAdjacentHTML('beforeend', `<button class="ac-go" id="pc-fi">📷 Completar fichas (fotos y datos del fabricante)</button>`); $('#pc-fi').onclick = () => coFichas(ref); };
  if (d.compra) { db.innerHTML = `<h3>Ya está cuadrado</h3><div class="ac-docs">Pedido <b>${escH(d.compra.pedido)}</b> · entrada ${escH((d.compra.recepciones || []).join(', ') || '—')}${d.compra.pendiente ? ` · lo que no vino está en ${escH(d.compra.pendiente)}` : ''}</div>`; fiBtn({ pedido: d.compra.pedido }); return; }
  if (d.ya_cuadrado) { db.innerHTML = `<h3>Ya está en compras</h3><div class="ac-docs">El documento ${escH(lec.numero)} ya está en el pedido confirmado <b>${escH(d.ya_cuadrado)}</b>. Recíbelo desde Recepciones.</div>`; fiBtn({ pedido: d.ya_cuadrado }); return; }
  const L = d.lineas || [];
  const cab = `<h3>Cuadrar con el pedido · ${escH(d.doc.nombre)}</h3>
    <p class="muted">${escH(lec.proveedor || '')} · nº ${escH(lec.numero || '?')} · ${coDate(lec.fecha || '')} · base ${eu(lec.base)} · <a href="#" id="pc-rel">volver a leer el PDF</a></p>
    <iframe class="co-pdf" src="/api/compras/pdf/${d.doc.id}#view=FitH"></iframe>`;
  const busProv = `<div><input id="pc-qprov" placeholder="Buscar proveedor por nombre o CIF"><div id="pc-lprov"></div></div>`;
  const bindProv = () => { let tp; const q = $('#pc-qprov'); if (!q) return; q.oninput = e => { clearTimeout(tp); tp = setTimeout(async () => {
    const r = await api(`/api/compras/proveedores?q=${encodeURIComponent(e.target.value)}`);
    $('#pc-lprov').innerHTML = r.map(p => `<a href="#" class="pc-opt" data-id="${p.id}">${escH(p.display_name)} <small>${escH(p.vat || '')}</small></a>`).join('');
    $('#pc-lprov').querySelectorAll('a').forEach(a => a.onclick = ev => { ev.preventDefault(); coPasar(did, { partner_id: +a.dataset.id }); });
  }, 300); }; };
  if (!d.proveedor) { db.innerHTML = cab + `<p class="err-t">No encuentro el proveedor (${escH(lec.proveedor || '?')} ${escH(lec.nif_proveedor || '')}). Búscalo:</p>` + busProv; bindProv();
    $('#pc-rel').onclick = e => { e.preventDefault(); coPasar(did, { releer: 1 }); }; return; }
  const prov = d.proveedor, po = d.pedido;
  const ESTADO = l => {
    if (l.saltar) return ['⏭ No se mete', 'pc-skip'];
    if (!l.product_id && l.crear) return ['＋ Artículo nuevo (se crea con su EAN)', 'pc-new'];
    if (!l.product_id) return ['? Sin identificar', 'pc-que'];
    if (!l.po_line) return ['＋ Se añade al pedido', 'pc-new'];
    const dq = +l.cantidad !== +l.pedido_cant, dp = Math.abs(+l.precio - +l.pedido_precio) > 0.0005 || (+l.descuento || 0) !== (+l.pedido_dto || 0);
    return dq || dp ? ['≠ Se ajusta ' + [dq ? `cant. ${l.pedido_cant}→${l.cantidad}` : '', dp ? `precio ${l.pedido_precio}→${l.precio}` : ''].filter(Boolean).join(', '), 'pc-adj'] : ['✓ Cuadra', 'pc-ok'];
  };
  const pinta = () => {
    const faltan = L.filter(l => !l.product_id && !l.saltar && !l.crear).length;
    const base = L.filter(l => (l.product_id || l.crear) && !l.saltar).reduce((a, l) => a + Math.round((+l.cantidad || 0) * (+l.precio || 0) * (1 - (+l.descuento || 0) / 100) * 100) / 100, 0);
    db.innerHTML = cab + `
      <div class="pc-prov"><b>Proveedor:</b> ${escH(prov.display_name)} <small class="muted">${escH(prov.vat || '')}</small> <a href="#" id="pc-cprov">cambiar</a><div id="pc-bprov" hidden>${busProv}</div></div>
      <div class="pc-prov"><b>Pedido:</b> ${po ? `<b>${escH(po.name)}</b> <small class="muted">${escH(po.origen)}</small>` : '<span class="pc-new">no hay pedido abierto: se crea uno nuevo con lo identificado</span>'}
        ${d.pedidos_abiertos.length > 1 ? `<select id="pc-po">${d.pedidos_abiertos.map(p => `<option value="${p.id}" ${po && p.id === po.id ? 'selected' : ''}>${escH(p.name)} · ${coDate(p.fecha)} · ${eu(p.base)} ${escH(p.origen)}</option>`).join('')}</select>` : ''}</div>
      ${d.confirmados && d.confirmados.length ? `<div class="pc-aviso">Este proveedor también tiene pedidos ya confirmados esperando mercancía: <b>${d.confirmados.map(escH).join(', ')}</b>. Si el documento es de uno de ellos, no lo cuadres aquí: recíbelo en Recepciones.</div>` : ''}
      <table class="co-t pc-t"><thead><tr><th>Ref. / EAN</th><th>Documento</th><th>Cant.</th><th>Precio</th><th>Dto</th><th>Artículo en Odoo</th><th>Qué pasa</th></tr></thead><tbody>
      ${L.map((l, i) => { const [et, ec] = ESTADO(l); return `<tr class="${l.saltar ? 'pc-off' : ''}"><td>${escH(l.ref)}<br><small class="muted">${escH(l.ean)}</small></td><td>${escH(l.descripcion)}</td>
        <td><input type="number" step="any" min="0" data-i="${i}" data-k="cantidad" value="${l.cantidad}"></td>
        <td><input type="number" step="any" min="0" data-i="${i}" data-k="precio" value="${l.precio}"></td>
        <td>${l.descuento ? l.descuento + '%' : ''}</td>
        <td>${l.product_id ? `${escH(l.producto)} <small class="muted">(${escH(l.como || '')})</small>` : ''} <a href="#" data-cam="${i}">${l.product_id ? 'cambiar' : '🔎 buscar'}</a>
          ${!l.product_id && !l.saltar ? ` · <a href="#" class="pc-sal" data-i="${i}">no meter</a>` : ''}
          ${!l.product_id && !l.saltar && /\d{8,}/.test(l.ean || '') ? ` · <a href="#" class="pc-crea2" data-i="${i}">${l.crear ? 'no crear' : '＋ es nuevo, crearlo'}</a>` : ''}${l.saltar ? ` · <a href="#" class="pc-sal" data-i="${i}">meter</a>` : ''}
          <div class="pc-bus" data-b="${i}" hidden><input placeholder="Nombre, ref. o EAN"><div></div></div></td>
        <td class="${ec}">${et}</td></tr>`; }).join('')}
      </tbody></table>
      ${d.no_vienen && d.no_vienen.length ? `<h4>En el pedido pero no vienen en el documento (${d.no_vienen.length})</h4>
        <p class="muted">Se pasan a un pedido nuevo en borrador "Pendiente de ${escH(po.name)}" y se quitan de ${escH(po.name)}.</p>
        <table class="co-t pc-t"><tbody>${d.no_vienen.map(x => `<tr><td>${escH(x.producto)}</td><td>${x.cantidad}</td><td>${x.precio}</td></tr>`).join('')}</tbody></table>` : ''}
      <p>Base con lo identificado: <b>${eu(base)}</b> · base del documento: <b>${eu((+lec.base || 0) - (+lec.portes || 0))}</b>${Math.abs(base - ((+lec.base || 0) - (+lec.portes || 0))) > 0.03 ? ' <span class="err-t" style="display:inline">no cuadra todavía</span>' : ' ✓'}</p>
      ${faltan ? `<p class="pc-aviso">Quedan <b>${faltan}</b> líneas sin identificar: búscalas en Odoo, márcalas como nuevas (solo si traen código de barras que no existe en Odoo) o "no meter".</p>` : ''}
      <p class="muted">Al cuadrar: el pedido queda igual que el documento (cantidades, precios y descuentos), se pone el nº ${escH(lec.numero || '')} como referencia del proveedor, se confirma y la entrada queda en <b>Recepciones</b> para que almacén compruebe lo que llega. También se actualizan las líneas de proveedor de las fichas y el coste si estaba a 0.</p>
      <button class="ac-go" id="pc-go" ${faltan ? 'disabled' : ''}>✓ Cuadrar y confirmar pedido</button><div id="pc-res"></div>`;
    bindProv();
    $('#pc-rel').onclick = e => { e.preventDefault(); coPasar(did, { releer: 1 }); };
    $('#pc-cprov').onclick = e => { e.preventDefault(); $('#pc-bprov').hidden = false; $('#pc-qprov').focus(); };
    const sel = $('#pc-po'); if (sel) sel.onchange = () => coPasar(did, { pedido: +sel.value, partner_id: prov.id });
    db.querySelectorAll('input[data-k]').forEach(inp => inp.onchange = () => { L[inp.dataset.i][inp.dataset.k] = parseFloat(inp.value) || 0; pinta(); });
    db.querySelectorAll('.pc-sal').forEach(a => a.onclick = e => { e.preventDefault(); const l = L[a.dataset.i]; l.saltar = !l.saltar; pinta(); });
    db.querySelectorAll('.pc-crea2').forEach(a => a.onclick = e => { e.preventDefault(); const l = L[a.dataset.i]; l.crear = !l.crear; pinta(); });
    db.querySelectorAll('[data-cam]').forEach(a => a.onclick = e => {
      e.preventDefault(); const i = a.dataset.cam, box = db.querySelector(`[data-b="${i}"]`); box.hidden = false;
      const inp = box.querySelector('input'), out = box.querySelector('div'); inp.value = L[i].ref || ''; inp.focus(); let t;
      const busca = async () => {
        const r = await api(`/api/ac/productos?q=${encodeURIComponent(inp.value)}`);
        out.innerHTML = r.length ? r.map(p => `<a href="#" class="pc-opt" data-id="${p.id}">${escH(p.display_name)}</a>`).join('') : '<small class="muted">Nada. Prueba con el EAN o parte del nombre.</small>';
        out.querySelectorAll('[data-id]').forEach(o => o.onclick = ev => { ev.preventDefault(); const p = r.find(x => x.id == o.dataset.id);
          Object.assign(L[i], { product_id: p.id, producto: p.display_name, como: 'elegido', saltar: false });
          pinta(); });
      };
      inp.oninput = () => { clearTimeout(t); t = setTimeout(busca, 300); }; if (inp.value.length >= 2) busca();
    });
    $('#pc-go').onclick = async () => {
      if (!confirm(`¿Cuadrar ${po ? po.name : 'un pedido nuevo'} con este documento y confirmarlo?`)) return;
      $('#pc-go').disabled = true; $('#pc-go').textContent = 'Cuadrando en Odoo…';
      try {
        const r = await jpost(`/api/compras/docs/${did}/cuadrar/aplicar`, { partner_id: prov.id, pedido_id: po ? po.id : 0, lineas: L });
        $('#pc-res').innerHTML = `<div class="ac-docs"><b>✓ ${escH(r.pedido)} cuadrado y confirmado</b> (base ${eu(r.base)}, total ${eu(r.total)}).<br>
          Entrada para almacén: <b>${escH(r.recepciones.join(', ') || '—')}</b> → pestaña Recepciones.
          ${r.pendiente ? `<br>Lo que no ha venido queda en <b>${escH(r.pendiente)}</b> (borrador).` : ''}
          ${r.anadidas.length ? `<br>Añadidas al pedido: ${r.anadidas.map(escH).join(', ')}.` : ''}
          ${r.factura ? '<br>' + escH(r.factura) : ''}${r.avisos.map(a => `<br>⚠️ ${escH(a)}`).join('')}</div>
          <button class="ac-go" id="pc-fi">📷 Completar fichas (fotos y datos del fabricante)</button>`;
        $('#pc-fi').onclick = () => coFichas({ pedido_id: r.pedido_id });
        $('#pc-go').remove(); coDocs();
      } catch (e) { alertBox(e.message); $('#pc-go').disabled = false; $('#pc-go').textContent = '✓ Cuadrar y confirmar pedido'; }
    };
  };
  pinta();
}
/* --- completar fichas desde la web del fabricante --- */
async function coFichas(ref) {
  const db = $('#co-db'); $('#co-drawer').hidden = false;
  db.innerHTML = `<h3>Completar fichas</h3><p class="muted">Cargando artículos…</p>`;
  let d; try { d = await jpost('/api/fichas/de_pedido', ref); } catch (e) { db.innerHTML = `<h3>Completar fichas</h3><p class="err">${escH(e.message)}</p>`; return; }
  const P = d.productos, W = {};
  P.forEach(p => p.marcar = !p.tiene_foto || !p.ean);
  const pinta = (estado) => {
    db.innerHTML = `<h3>Completar fichas · ${escH(d.pedido)}</h3><p class="muted">${escH(d.proveedor)} · ${P.length} artículos. Se busca la ficha del fabricante y solo se rellena lo que esté vacío.</p>
      ${estado || ''}
      <table class="co-t pc-t"><thead><tr><th></th><th>Ahora en Odoo</th><th>Encontrado en la web</th></tr></thead><tbody>
      ${P.map((p, i) => { const w = W[p.product_id]; return `<tr><td><input type="checkbox" data-m="${i}" ${p.marcar ? 'checked' : ''}></td>
        <td>${p.tiene_foto ? `<img class="fi-img" src="/api/fichas/foto_odoo/${p.product_id}">` : '<span class="pc-que">sin foto</span>'}<br><b>${escH(p.nombre)}</b><br>
          <small>EAN: ${escH(p.ean) || '<span class="pc-que" style="display:inline">falta</span>'} · Ref.: ${escH(p.ref_interna) || '<span class="pc-que" style="display:inline">falta</span>'}${p.cod_proveedor ? ' · Cód. prov.: ' + escH(p.cod_proveedor) : ''}</small></td>
        <td>${!w ? '<small class="muted">—</small>' : w.error ? `<span class="err-t">${escH(w.error)}</span><small class="muted">${escH(w.nota || '')}</small>` : `
          ${w.foto_id ? `<img class="fi-img big" src="/api/fichas/foto/${w.foto_id}">` : `<span class="pc-que">sin foto válida</span>`}
          ${w.seguro === false ? '<div class="pc-aviso">⚠️ No está seguro de que sea el mismo: revísalo</div>' : ''}
          <div class="fi-d"><b>${escH(w.marca || '')} ${escH(w.nombre || '')}</b>
          ${w.url_ficha ? `<a target="_blank" rel="noopener" href="${escH(w.url_ficha)}">ver ficha</a>` : ''}<br>
          ${[['foto', w.foto_id && (p.tiene_foto ? 'cambiar foto' : 'poner foto')], ['ean', w.ean && !p.ean && 'EAN ' + w.ean], ['ref', !p.ref_interna && p.cod_proveedor && 'ref. ' + p.cod_proveedor],
             ['descripcion', w.descripcion && !p.descripcion && 'descripción'], ['proveedor', p.x_proveedor === false && 'proveedor'], ['tpv', p.en_tpv === false && 'disponible en TPV']]
            .filter(x => x[1]).map(([k, txt]) => `<label class="fi-op"><input type="checkbox" data-o="${i}" data-k="${k}" ${k === 'foto' && p.tiene_foto ? '' : 'checked'}> ${escH(txt)}</label>`).join('')}
          ${w.descripcion ? `<small class="muted">${escH(w.descripcion)}</small>` : ''}<small class="muted">${escH(w.nota || '')}</small></div>`}</td></tr>`; }).join('')}
      </tbody></table>
      <button class="ac-go" id="fi-bus">🔎 Buscar en la web los marcados</button> <button class="ac-go" id="fi-ap" ${Object.keys(W).length ? '' : 'hidden'}>✓ Guardar en Odoo lo marcado</button><div id="fi-res"></div>`;
    db.querySelectorAll('input[data-m]').forEach(c => c.onchange = () => P[c.dataset.m].marcar = c.checked);
    $('#fi-bus').onclick = async () => {
      const sel = P.filter(p => p.marcar); if (!sel.length) return alertBox('Marca algún artículo');
      $('#fi-bus').disabled = true; $('#fi-bus').textContent = 'Buscando…';
      try {
        const { trabajo } = await jpost('/api/fichas/buscar', { productos: sel });
        const tick = async () => {
          const t = await api('/api/fichas/trabajo/' + trabajo); Object.assign(W, t.res);
          if (!t.fin) { pinta(`<p class="pc-aviso">Buscando en las webs de los fabricantes: ${t.hechos} de ${t.total}… (tarda un poco, puedes dejarlo abierto)</p>`); $('#fi-bus').disabled = true; setTimeout(tick, 4000); }
          else pinta(`<p class="muted">Búsqueda terminada. Revisa y marca lo que quieras guardar.</p>`);
        };
        tick();
      } catch (e) { alertBox(e.message); $('#fi-bus').disabled = false; }
    };
    $('#fi-ap').onclick = async () => {
      const items = [];
      P.forEach((p, i) => { const w = W[p.product_id]; if (!w || w.error) return; const it = { product_id: p.product_id };
        db.querySelectorAll(`input[data-o="${i}"]:checked`).forEach(c => { const k = c.dataset.k;
          if (k === 'foto') { it.foto_id = w.foto_id; it.pisar_foto = p.tiene_foto; } if (k === 'ean') it.ean = w.ean; if (k === 'ref') it.ref = p.cod_proveedor;
          if (k === 'descripcion') it.descripcion = w.descripcion; if (k === 'proveedor') it.proveedor = d.proveedor; if (k === 'tpv') it.tpv = true; });
        if (Object.keys(it).length > 1) items.push(it); });
      if (!items.length) return alertBox('No hay nada marcado para guardar');
      $('#fi-ap').disabled = true;
      try { const r = await jpost('/api/fichas/aplicar', { items });
        $('#fi-res').innerHTML = `<div class="ac-docs"><b>✓ Guardado en Odoo</b>${r.hechos.map(h => '<br>' + escH(h)).join('')}${r.avisos.map(a => '<br>⚠️ ' + escH(a)).join('')}</div>`;
      } catch (e) { alertBox(e.message); $('#fi-ap').disabled = false; }
    };
  };
  pinta();
}
/* --- recepciones --- */
async function coRec() {
  try {
    const [albs, recs] = await Promise.all([api(`/api/compras/docs?tipo=albaran&estado=pendiente&company=${company}`), api(`/api/compras/recepciones?company=${company}`)]);
    CO_RECS = recs;
    $('#co-albs').innerHTML = albs.length ? '' : '<p class="empty">No hay albaranes esperando. Súbelos en "Subir PDF".</p>';
    albs.forEach(a => {
      const d = document.createElement('div'); d.className = 'co-alb' + (CO_ALB && CO_ALB.id === a.id ? ' on' : '');
      d.innerHTML = `<span class="ic">📦</span><div><b></b><small class="muted">${hora(a.subido)} · ${escH(a.usuario)}</small></div>
        <a class="lnk" target="_blank" rel="noopener" href="/api/compras/pdf/${a.id}">Ver</a><button class="lnk sel">${CO_ALB && CO_ALB.id === a.id ? 'Elegido ✓' : 'Recibir con este'}</button><button class="lnk del" title="Descartar">×</button>`;
      d.querySelector('b').textContent = a.nombre;
      d.querySelector('.sel').onclick = () => { CO_ALB = CO_ALB && CO_ALB.id === a.id ? null : a; coRec(); if (CO_ALB) alertBox('Ahora elige a la derecha el pedido que corresponde a este albarán'); };
      d.querySelector('.del').onclick = async () => { if (!confirm('¿Descartar este albarán? (no borra nada de Odoo)')) return; await fetch('/api/compras/docs/' + a.id, { method: 'DELETE' }); if (CO_ALB && CO_ALB.id === a.id) CO_ALB = null; coRec(); };
      $('#co-albs').appendChild(d);
    });
    coRecRender();
  } catch (e) { showErr(e.message); }
}
function coRecRender() {
  const q = ($('#co-rq').value || '').toLowerCase(), box = $('#co-recs'); box.innerHTML = '';
  const l = CO_RECS.filter(r => !q || [r.proveedor, r.ref, r.pedido, r.origen, ...r.lineas.map(x => x.producto)].join(' ').toLowerCase().includes(q));
  if (!l.length) box.innerHTML = '<p class="empty">No hay nada pendiente de llegar</p>';
  l.forEach(r => {
    const d = document.createElement('div'); d.className = 'card co-rcard' + (r.dias > 7 ? ' tarde' : '');
    d.innerHTML = `<div class="c1"><span class="ref"></span><span>${r.dias > 0 ? `<span class="bdg ${r.dias > 7 ? 'b-bad' : 'b-warn'}">${r.dias} días</span>` : `<span class="bdg b-ok">Previsto ${coDate(r.fecha)}</span>`}</span></div>
      <div class="cli"><b></b></div><div class="c3"><span>${escH(r.pedido || r.origen)}${r.pendiente_de ? ' · resto de ' + escH(r.pendiente_de) : ''}</span><span>${r.lineas.length} artículo${r.lineas.length > 1 ? 's' : ''}</span></div>`;
    d.querySelector('.ref').textContent = r.ref; d.querySelector('.cli b').textContent = r.proveedor;
    d.onclick = () => coRecibirOpen(r);
    box.appendChild(d);
  });
}
$('#co-rq').oninput = coRecRender;
function coRecibirOpen(r) {
  if (!CO_ALB && r.doc) CO_ALB = r.doc;
  const db = $('#co-db');
  db.innerHTML = `<h3>${escH(r.ref)} · ${escH(r.proveedor)}</h3><p class="muted">${escH(r.pedido || r.origen)} · ${escH(r.empresa)}${CO_ALB ? ` · albarán: <a target="_blank" rel="noopener" href="/api/compras/pdf/${CO_ALB.id}">${escH(CO_ALB.nombre)}</a>` : ''}</p>
    ${CO_ALB ? `<iframe class="co-pdf" src="/api/compras/pdf/${CO_ALB.id}#view=FitH"></iframe>` : '<p class="hint">Consejo: elige primero el PDF en "Albaranes subidos sin recibir" y quedará adjunto a la recepción en Odoo.</p>'}
    <label class="co-alb-n">Nº de albarán del proveedor <input id="co-an" placeholder="A-695686"></label>
    <div class="co-q"><button class="lnk" id="co-todo">Ha llegado todo</button><button class="lnk" id="co-nada">Poner todo a 0</button></div>
    <table class="co-t"><thead><tr><th>Artículo</th><th>Pedido</th><th>Ha llegado</th></tr></thead><tbody>${r.lineas.map(l => `<tr><td></td><td>${l.pedido}</td>
      <td><input type="number" min="0" step="any" data-m="${l.move_id}" value="${l.pedido}"></td></tr>`).join('')}</tbody></table>
    <p class="muted">Lo que no llegue se queda pendiente en una recepción nueva, como hace Odoo.</p>
    <button id="co-ok" class="ac-go">✓ Recibir en Odoo</button><div id="co-res"></div>`;
  db.querySelectorAll('tbody tr').forEach((tr, i) => tr.firstChild.textContent = r.lineas[i].producto);
  $('#co-todo').onclick = () => db.querySelectorAll('input[data-m]').forEach((i, k) => i.value = r.lineas[k].pedido);
  $('#co-nada').onclick = () => db.querySelectorAll('input[data-m]').forEach(i => i.value = 0);
  $('#co-ok').onclick = async () => {
    const cant = {}; db.querySelectorAll('input[data-m]').forEach(i => cant[i.dataset.m] = parseFloat(i.value) || 0);
    $('#co-ok').disabled = true; $('#co-ok').textContent = 'Recibiendo…';
    try {
      const res = await jpost(`/api/compras/recibir?company=${company}`, { picking_id: r.id, cantidades: cant, albaran: $('#co-an').value.trim(), doc_id: CO_ALB && CO_ALB.id });
      CO_ALB = null;
      $('#co-res').innerHTML = `<div class="ac-docs"><b>✓ Recibido en Odoo.</b> ${res.queda_pendiente ? 'Lo que faltaba queda pendiente.' : 'Pedido completo.'}</div>
        ${res.servir.length ? `<h4>Ya se puede servir</h4><table class="co-t"><thead><tr><th>Para</th><th>Pedido</th><th>Artículo</th><th>Pend.</th></tr></thead><tbody>${res.servir.map(s => `<tr><td>${escH(s.quien)}</td><td>${escH(s.ref)}</td><td>${escH(s.producto)}</td><td>${s.pendiente}</td></tr>`).join('')}</tbody></table>
          <a class="btn" href="#ac" onclick="$('#co-drawer').hidden=true">Ir al Panel AC a servirlo</a>` : '<p class="muted">Nadie lo tenía pedido: queda en stock.</p>'}`;
      $('#co-ok').remove(); coRec();
    } catch (e) { alertBox(e.message); $('#co-ok').disabled = false; $('#co-ok').textContent = '✓ Recibir en Odoo'; }
  };
  $('#co-drawer').hidden = false;
}
$('#co-x').onclick = () => $('#co-drawer').hidden = true;
$('#co-drawer').onclick = e => { if (e.target.id === 'co-drawer') $('#co-drawer').hidden = true; };
/* --- facturas (Mayte) --- */
async function coFac() {
  const f = await coBadge(); if (!f) return;
  const ok = f.filter(x => x.estado === 'ok'), dif = f.filter(x => x.estado === 'diferencias'), lee = f.filter(x => x.estado === 'leyendo');
  $('#kf-l').textContent = lee.length; $('#kf-o').textContent = ok.length; $('#kf-d').textContent = dif.length;
  $('#kf-oe').textContent = eur(ok.reduce((a, x) => a + x.total, 0));
  const pinta = (k, l) => {
    const box = document.querySelector(`.col[data-k="${k}"] .cards`); box.innerHTML = l.length ? '' : '<p class="empty">Nada por aquí</p>';
    document.querySelector(`.col[data-k="${k}"] .n`).textContent = l.length;
    l.forEach(x => {
      const d = document.createElement('div'); d.className = 'card fcard ' + x.estado;
      const MARCA = { gasto: '🧾 Gasto sin entrada', recibido: '📦 Marcado como recibido', pedido: '⇄ Cruzada con pedido del almacén' };
      const chipEntrada = x.marca && x.marca.tipo !== 'pedido' ? `<span class="chip verde" title="${escH(x.marca.usuario)}">${MARCA[x.marca.tipo]} · ${escH(x.marca.usuario)}</span>`
        : x.recepcion === 'recibido' ? '<span class="chip verde">✓ Material recibido</span>'
        : x.recepcion === 'pendiente' ? '<span class="chip rojo">Almacén no lo ha recibido todo</span>' : '<span class="chip rojo">Sin cuadrar con albarán / pedido</span>';
      d.innerHTML = `<div class="c1"><span class="ref"></span><b>${eur(x.total)}</b></div>
        <div class="cli">${x.numero ? 'Nº ' + escH(x.numero) : '<i>sin nº</i>'} · ${coDate(x.fecha) || '<i>sin fecha</i>'}${x.abono ? ' · <b>ABONO</b>' : ''}</div>
        <div class="chips">${x.pedidos.map(p => `<span class="chip">${escH(p)}</span>`).join('')}
          ${x.estado === 'leyendo' ? '' : chipEntrada}</div>
        ${x.estado === 'leyendo' ? '' : `<div class="fent">${x.marca ? `<button class="lnk" data-a="quitar">quitar marca</button>`
          : `${x.recepcion === 'sin_pedido' ? `<button class="lnk" data-a="cuadrar">⇄ Cuadrar con pedido</button>` : ''}
             <button class="lnk" data-a="gasto">🧾 Es un gasto (sin entrada)</button>
             ${x.recepcion !== 'recibido' ? `<button class="lnk" data-a="recibido">📦 Material recibido</button>` : ''}`}</div>`}
        <div class="avs"></div>
        <div class="c3"><span class="emp"></span><span>${x.vence ? 'vence ' + coDate(x.vence) : ''}</span></div>
        <div class="fbtn">${x.pdf ? `<a class="lnk" target="_blank" rel="noopener" href="${ODOO}/web/content/${x.pdf}">Ver PDF</a>` : ''}
          <a class="lnk" target="_blank" rel="noopener" href="${ODOO}/odoo/action-account.action_move_in_invoice_type/${x.id}">Abrir en Odoo</a>
          ${x.estado !== 'leyendo' ? `<button class="okb">✓ OK, contabilizar</button>` : ''}</div>`;
      d.querySelector('.ref').textContent = x.proveedor; d.querySelector('.emp').textContent = x.empresa;
      const avs = d.querySelector('.avs');
      [...(x.estado !== 'leyendo' && x.faltan.length ? ['Falta: ' + x.faltan.join(', ')] : []), ...x.avisos].forEach(a => { const v = document.createElement('div'); v.className = 'av'; v.textContent = '⚠ ' + a; avs.appendChild(v); });
      d.querySelectorAll('.fent [data-a]').forEach(bt => bt.onclick = async () => {
        const a = bt.dataset.a;
        if (a === 'cuadrar') return coFacCuadrar(x);
        if (a === 'recibido' && !confirm('¿Confirmas que el material de esta factura ha llegado? Quedará apuntado a tu nombre.')) return;
        try { await jpost(`/api/compras/facturas/${x.id}/marcar`, { tipo: a === 'quitar' ? '' : a }); coFac(); } catch (e) { alertBox(e.message); }
      });
      const b = d.querySelector('.okb');
      if (b && !x.entrada_ok) { b.disabled = true; b.title = 'Primero: cuadrarla con su pedido, que almacén reciba el material, o marcarla como gasto / recibido'; }
      if (b) b.onclick = async () => {
        if (x.estado === 'diferencias' && !confirm('Esta factura tiene diferencias:\n\n' + [...x.faltan.map(f => 'Falta ' + f), ...x.avisos].join('\n') + '\n\n¿Contabilizarla igualmente?')) return;
        b.disabled = true; b.textContent = 'Contabilizando…';
        try { await jpost(`/api/compras/facturas/${x.id}/ok?company=${company}`, {}); alertBox(`Factura de ${x.proveedor} contabilizada`); coFac(); }
        catch (e) { alertBox(e.message); b.disabled = false; b.textContent = '✓ OK, contabilizar'; }
      };
      box.appendChild(d);
    });
  };
  pinta('f-ok', ok); pinta('f-dif', dif); pinta('f-lee', lee);
}
async function coFacCuadrar(x) {
  const db = $('#co-db'); $('#co-drawer').hidden = false;
  db.innerHTML = `<h3>Cuadrar factura · ${escH(x.proveedor)}</h3><p class="muted">Buscando pedidos de este proveedor…</p>`;
  try {
    const d = await api(`/api/compras/facturas/${x.id}/pedidos`);
    db.innerHTML = `<h3>Cuadrar factura · ${escH(x.proveedor)}</h3><p class="muted">Factura ${escH(d.factura.ref || '')} · base ${eur(d.factura.base)}. Elige el pedido al que corresponde: las líneas de la factura pasan a ser las del pedido y ya se puede comprobar con lo que ha llegado.</p>
      ${d.pedidos.length ? d.pedidos.map(p => `<div class="co-alb ${p.coincide ? 'on' : ''}"><span class="ic">🛒</span><div><b>${escH(p.name)}</b> ${p.coincide ? '<span class="bdg b-ok">mismo nº de documento</span>' : ''}
        <small class="muted">${escH(p.empresa || '')} · ${coDate(p.fecha)} · base ${eur(p.base)} · ${escH(p.ref)} · ${p.recibido === 'full' ? 'recibido' : p.recibido === 'partial' ? 'recibido en parte' : 'sin recibir'}</small></div>
        <button class="lnk" data-p="${p.id}">Cuadrar con este</button></div>`).join('')
      : '<p class="empty">Este proveedor no tiene pedidos pendientes de facturar. Si es material, primero súbelo como albarán y cuádralo con su pedido; si es un gasto, márcala como gasto.</p>'}`;
    db.querySelectorAll('[data-p]').forEach(bt => bt.onclick = async () => {
      bt.disabled = true;
      try { const r = await jpost(`/api/compras/facturas/${x.id}/enlazar`, { pedido_id: +bt.dataset.p });
        db.insertAdjacentHTML('beforeend', `<div class="ac-docs">✓ Factura cuadrada con ${escH(r.pedido)} · base ${eur(r.base)} (la del PDF era ${eur(x.base)}). Revisa la diferencia si la hay.</div>`); coFac(); coDocs();
      } catch (e) { alertBox(e.message); bt.disabled = false; }
    });
  } catch (e) { db.innerHTML += `<p class="err">${escH(e.message)}</p>`; }
}
/* --- facturar albaranes (asistente de Nextdoo, con la empresa por defecto de cada cliente) --- */
let FA = null;
async function faCargar() {
  $('#fa-l').innerHTML = '<p class="muted">Cargando albaranes…</p>'; $('#fa-out').innerHTML = '';
  try { FA = await api('/api/facturar/albaranes'); } catch (e) { $('#fa-l').innerHTML = `<p class="err">${escH(e.message)}</p>`; return; }
  FA.grupos.forEach(g => g.albaranes.forEach(a => a.marcado = false));
  $('#fa-emp').innerHTML = '<option value="">la de cada cliente</option>' + FA.empresas.map(e => `<option value="${e.id}">${escH(e.nombre.split(' ')[0])}</option>`).join('');
  if (!$('#fa-fecha').value) $('#fa-fecha').value = new Date().toISOString().slice(0, 10);
  faPinta();
}
let FA_TAB = 'alb';
function faTab(t) {
  FA_TAB = t;
  document.querySelectorAll('.fa-tabs button').forEach(b => b.classList.toggle('on', b.dataset.t === t));
  ['alb', 'env', 'ent'].forEach(k => $('#co-' + k).hidden = k !== t);
  ({ alb: faCargar, env: feCargar, ent: enCargar })[t]();
}
document.querySelectorAll('.fa-tabs button').forEach(b => b.onclick = () => faTab(b.dataset.t));
function faOpen() { faTab(FA_TAB); }
$('#fa-emp').onchange = () => { const v = +$('#fa-emp').value; if (!v) return; FA.grupos.forEach(g => { if (g.albaranes.some(a => a.marcado)) g.empresa = v; }); $('#fa-emp').value = ''; faPinta(); };
let FE = [];
async function feCargar() {
  try { FE = await api('/api/facturar/sin_enviar'); } catch (e) { $('#fe-l').innerHTML = `<p class="err">${escH(e.message)}</p>`; return; }
  $('#fe-n').textContent = FE.length;
  $('#fe-l').innerHTML = FE.length ? `<table class="co-t pc-t"><tbody>${FE.map((f, i) => `<tr><td><input type="checkbox" data-f="${i}" ${f.marcado ? 'checked' : ''} ${f.email ? '' : 'disabled'}></td>
    <td><b>${escH(f.nombre)}</b>${f.abono ? ' <span class="bdg b-warn">abono</span>' : ''}</td><td>${escH(f.cliente)}</td><td>${f.email ? escH(f.email) : '<span class="err-t" style="display:inline">sin email</span>'}</td>
    <td>${coDate(f.fecha)}</td><td>${escH(f.empresa)}</td><td>${eur(f.total)}</td></tr>`).join('')}</tbody></table>` : '<p class="empty">Todas las facturas están enviadas</p>';
  $('#fe-l').querySelectorAll('[data-f]').forEach(c => c.onchange = () => FE[c.dataset.f].marcado = c.checked);
}
$('#fe-all').onchange = e => { FE.forEach(f => f.marcado = !!f.email && e.target.checked); feCargar2(); };
function feCargar2() { $('#fe-l').querySelectorAll('[data-f]').forEach(c => c.checked = !!FE[c.dataset.f].marcado); }
$('#fe-go').onclick = async () => {
  const ids = FE.filter(f => f.marcado).map(f => f.id); if (!ids.length) return alertBox('Marca alguna factura');
  if (!confirm(`¿Enviar ${ids.length} factura(s) por email a sus clientes?`)) return;
  $('#fe-go').disabled = true; $('#fe-res').textContent = 'Enviando…';
  try { const r = await jpost('/api/facturar/enviar', { ids });
    $('#fe-res').textContent = `${r.enviadas.length} enviadas` + (r.errores.length ? ` · ${r.errores.length} con problema: ${r.errores.join(' | ')}` : '');
    await feCargar();
  } catch (e) { alertBox(e.message); } finally { $('#fe-go').disabled = false; }
};
function faPinta() {
  const q = ($('#fa-q').value || '').toLowerCase();
  const opt = sel => FA.empresas.map(e => `<option value="${e.id}" ${e.id === sel ? 'selected' : ''}>${escH(e.nombre.split(' ')[0])}</option>`).join('');
  const gs = FA.grupos.filter(g => !q || (g.cliente + ' ' + g.albaranes.map(a => a.ref + ' ' + a.pedido).join(' ')).toLowerCase().includes(q));
  $('#fa-l').innerHTML = gs.length ? gs.map(g => { const i = FA.grupos.indexOf(g); return `<div class="fa-g">
      <div class="fa-h"><label><input type="checkbox" data-g="${i}" ${g.albaranes.every(a => a.marcado) ? 'checked' : ''}> <b>${escH(g.cliente)}</b></label>
        <span>${eur(g.total)}</span><select data-e="${i}">${opt(g.empresa)}</select><small class="muted">${g.ultima ? 'última: ' + escH(g.ultima) : 'sin facturas anteriores'}</small></div>
      ${g.albaranes.map((a, k) => `<label class="fa-a"><input type="checkbox" data-g="${i}" data-k="${k}" ${a.marcado ? 'checked' : ''}> ${escH(a.ref)} · ${escH(a.pedido)} · ${coDate(a.fecha)} · ${eur(a.importe)}
        ${a.almacen.startsWith('AC') ? '' : `<span class="bdg b-warn">sale de ${escH(a.almacen)}</span>`}</label>`).join('')}</div>`; }).join('')
    : '<p class="empty">No hay albaranes pendientes de facturar</p>';
  $('#fa-l').querySelectorAll('select[data-e]').forEach(s => s.onchange = () => { FA.grupos[s.dataset.e].empresa = +s.value; faRes(); });
  $('#fa-l').querySelectorAll('input[data-g]').forEach(c => c.onchange = () => {
    const g = FA.grupos[c.dataset.g];
    if (c.dataset.k === undefined) g.albaranes.forEach(a => a.marcado = c.checked); else g.albaranes[c.dataset.k].marcado = c.checked;
    faPinta();
  });
  faRes();
}
function faRes() {
  const por = {};
  FA.grupos.forEach(g => g.albaranes.filter(a => a.marcado).forEach(a => { const e = FA.empresas.find(x => x.id === g.empresa); const k = e ? e.nombre.split(' ')[0] : g.empresa; por[k] = (por[k] || 0) + a.importe; }));
  $('#fa-res').textContent = Object.keys(por).length ? 'Marcado: ' + Object.entries(por).map(([k, v]) => `${k} ${eur(v)}`).join(' · ') + ' (sin IVA)' : 'Marca los albaranes que quieras facturar';
}
$('#fa-q').oninput = () => FA && faPinta();
async function faFacturar(modo) {
  const grupos = FA.grupos.map(g => ({ empresa: g.empresa, picking_ids: g.albaranes.filter(a => a.marcado).map(a => a.id) })).filter(g => g.picking_ids.length);
  if (!grupos.length) return alertBox('Marca algún albarán');
  const fch = $('#fa-fecha').value ? coDate($('#fa-fecha').value) : 'hoy';
  if (!confirm((modo === 'directo' ? '¿Facturar y contabilizar directamente los albaranes marcados' : '¿Crear las facturas en borrador') + ` con fecha ${fch}?`)) return;
  $('#fa-out').innerHTML = '<p class="muted">Facturando en Odoo…</p>';
  try {
    const r = await jpost('/api/facturar/albaranes', { grupos, modo, fecha: $('#fa-fecha').value });
    $('#fa-out').innerHTML = `<div class="ac-docs"><b>✓ Hecho</b>${r.hechos.map(h => h.facturas.map(f => `<br>${escH(f.nombre)} · ${escH(f.cliente)} · ${eur(f.total)} ${f.estado === 'draft' ? '(borrador)' : ''}
      <a class="lnk" target="_blank" rel="noopener" href="${ODOO}/odoo/action-account.action_move_out_invoice_type/${f.id}">abrir</a>`).join('') + (h.errores || []).map(e => `<br>⚠️ ${escH(e)}`).join('')).join('')}
      ${modo === 'directo' ? '<br>Ya puedes enviarlas por email en la pestaña "✉ Enviar facturas".' : ''}</div>`;
    const out = $('#fa-out').innerHTML; await faCargar(); $('#fa-out').innerHTML = out;
  } catch (e) { $('#fa-out').innerHTML = `<p class="err">${escH(e.message)}</p>`; }
}
$('#fa-bor').onclick = () => faFacturar('borrador'); $('#fa-dir').onclick = () => faFacturar('directo');
/* --- cuentas entre empresas --- */
let EN_MES = (() => { const d = new Date(); d.setDate(0); return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0'); })();
$('#en-prev').onclick = () => { const [y, m] = EN_MES.split('-').map(Number); const d = new Date(y, m - 2, 1); EN_MES = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0'); enCargar(); };
$('#en-next').onclick = () => { const [y, m] = EN_MES.split('-').map(Number); const d = new Date(y, m, 1); EN_MES = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0'); enCargar(); };
async function enCargar() {
  const box = $('#en-box'); box.innerHTML = '<p class="muted">Calculando…</p>';
  let d; try { d = await api('/api/entre_empresas?mes=' + EN_MES); } catch (e) { box.innerHTML = `<p class="err">${escH(e.message)}</p>`; return; }
  $('#en-mes').textContent = d.texto_mes.charAt(0).toUpperCase() + d.texto_mes.slice(1);
  const tarjeta = (sentido, tit, sub, importe, n) => { const h = d.hechas[sentido]; return `<div class="kpi en-k"><span>${tit}</span><b>${eur(importe)}</b><small>${sub} · ${n} líneas · sin IVA</small>
    <div class="fa-bar"><a class="lnk" target="_blank" rel="noopener" href="/api/entre_empresas/pdf?mes=${d.mes}&sentido=${sentido}">Ver PDF del detalle</a>
    ${h ? `<span class="bdg b-ok">Facturas creadas (${escH(h.usuario)})</span> <a class="lnk" target="_blank" rel="noopener" href="${ODOO}/odoo/action-account.action_move_out_invoice_type/${h.venta_id}">venta</a> <a class="lnk" target="_blank" rel="noopener" href="${ODOO}/odoo/action-account.action_move_in_invoice_type/${h.compra_id}">compra</a>`
        : importe > 0 ? `<button class="ac-go" data-s="${sentido}">Crear las dos facturas (borrador)</button>` : ''}</div></div>`; };
  box.innerHTML = `<section class="kpis">${tarjeta('distri_a_logi', 'Distriplus factura a Logipaper', 'material del almacén central que vendió Logipaper', d.distri_a_logi, d.ventas.length)}
    ${tarjeta('logi_a_distri', 'Logipaper factura a Distriplus', 'compras de Logipaper que entraron en el almacén central', d.logi_a_distri, d.compras.length)}</section>
    <p class="muted">Cada liquidación crea la factura de venta en una empresa y la de compra en la otra, en borrador, con una sola línea "Venta general mes de ${escH(d.texto_mes)} según PDF adjunto" y el PDF del detalle adjunto. Lo que se venden entre ellas como cliente (Tienda 3) va aparte y no entra aquí.</p>
    <details><summary>Detalle: material vendido por Logipaper (${d.ventas.length})</summary><table class="co-t pc-t"><thead><tr><th>Fecha</th><th>Factura</th><th>Cliente</th><th>Artículo</th><th>Cant.</th><th>Últ. compra</th><th>Importe</th></tr></thead><tbody>
      ${d.ventas.map(v => `<tr><td>${coDate(v.fecha)}</td><td>${escH(v.factura)}</td><td>${escH(v.cliente)}</td><td>${escH(v.producto)}</td><td>${v.cantidad}</td><td title="${escH(v.origen_coste)}">${eur(v.coste)}</td><td>${eur(v.importe)}</td></tr>`).join('')}</tbody></table></details>
    <details><summary>Detalle: compras de Logipaper entradas en el almacén central (${d.compras.length})</summary><table class="co-t pc-t"><thead><tr><th>Fecha</th><th>Factura proveedor</th><th>Proveedor</th><th>Pedido</th><th>Base</th><th>Importe</th></tr></thead><tbody>
      ${d.compras.map(x => `<tr><td>${coDate(x.fecha)}</td><td>${escH(x.factura)}</td><td>${escH(x.proveedor)}</td><td>${escH(x.pedido)}</td><td>${eur(x.base)}</td><td>${eur(x.importe)}</td></tr>`).join('')}</tbody></table></details>`;
  box.querySelectorAll('[data-s]').forEach(b => b.onclick = async () => {
    if (!confirm('¿Crear en borrador la factura de venta y la de compra de esta liquidación?')) return;
    b.disabled = true;
    try { await jpost('/api/entre_empresas/crear', { mes: d.mes, sentido: b.dataset.s }); enCargar(); } catch (e) { alertBox(e.message); b.disabled = false; }
  });
}
/* --- recurrentes --- */
function coMes(delta) { const [y, m] = CO_MES.split('-').map(Number); const d = new Date(y, m - 1 + delta, 1); CO_MES = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0'); coRecu(); }
$('#rc-prev').onclick = () => coMes(-1); $('#rc-next').onclick = () => coMes(1);
async function coRecu() {
  const [y, m] = CO_MES.split('-').map(Number);
  const t = new Date(y, m - 1, 1).toLocaleDateString('es-ES', { month: 'long', year: 'numeric' }); $('#rc-mes').textContent = t.charAt(0).toUpperCase() + t.slice(1);
  try {
    const d = await api(`/api/compras/recurrentes?mes=${CO_MES}&company=${company}`), l = d.lista;
    const ok = l.filter(x => x.llegada), falta = l.filter(x => !x.llegada);
    $('#rc-bar').style.width = (l.length ? ok.length / l.length * 100 : 0) + '%';
    $('#rc-txt').textContent = l.length ? `${ok.length} de ${l.length} recibidas` : 'Todavía no hay recurrentes: añade la lista de Mayte';
    const fila = (x, llegada) => {
      const e = document.createElement('div'); e.className = 'rc ' + (llegada ? 'ok' : x.retraso ? 'tarde' : '');
      e.innerHTML = `<div><b></b><small class="muted"></small></div><span class="imp">${x.importe ? '≈ ' + eur(x.importe) : ''}</span>
        <span class="st">${llegada ? (x.factura ? `<a target="_blank" rel="noopener" href="${ODOO}/odoo/action-account.action_move_in_invoice_type/${x.factura.id}">✓ ${escH(x.factura.numero || 'factura')} · ${eur(x.factura.total)}${x.factura.borrador ? ' (borrador)' : ''}</a>` : '✓ ' + escH(x.manual))
          : (x.retraso ? `<span class="bdg b-bad">Reclamar · llega el ${x.dia}</span>` : `<span class="bdg b-warn">Suele llegar el ${x.dia || '?'}</span>`)}</span>
        <span class="acc">${llegada ? (x.manual ? '<button class="lnk un">Desmarcar</button>' : '') : '<button class="lnk mk">Marcar recibida</button>'}<button class="lnk ed">✎</button><button class="lnk del">×</button></span>`;
      e.querySelector('b').textContent = x.partner_nombre + (x.concepto ? ' · ' + x.concepto : '');
      e.querySelector('small').textContent = x.empresa_nombre;
      const mk = e.querySelector('.mk'); if (mk) mk.onclick = async () => { const n = prompt('Nota (opcional): por ejemplo "llegó por correo, pendiente de subir"', ''); if (n === null) return; await jpost(`/api/compras/recurrentes/${x.id}/marca`, { mes: CO_MES, nota: n || 'Marcada a mano' }); coRecu(); };
      const un = e.querySelector('.un'); if (un) un.onclick = async () => { await jpost(`/api/compras/recurrentes/${x.id}/marca`, { mes: CO_MES, quitar: 1 }); coRecu(); };
      e.querySelector('.ed').onclick = () => coRecForm(x);
      e.querySelector('.del').onclick = async () => { if (!confirm('¿Quitar de la lista de recurrentes?')) return; await fetch('/api/compras/recurrentes/' + x.id, { method: 'DELETE' }); coRecu(); };
      return e;
    };
    $('#rc-faltan').innerHTML = falta.length ? '' : '<p class="empty">✓ No falta ninguna</p>'; falta.forEach(x => $('#rc-faltan').appendChild(fila(x, false)));
    $('#rc-ok').innerHTML = ok.length ? '' : '<p class="empty">Todavía no ha llegado ninguna este mes</p>'; ok.forEach(x => $('#rc-ok').appendChild(fila(x, true)));
  } catch (e) { showErr(e.message); }
}
$('#rc-add').onclick = () => coRecForm({});
function coRecForm(x) {
  const emps = [...$('#company').options].filter(o => o.value !== 'all');
  $('#co-db').innerHTML = `<h3>${x.id ? 'Editar' : 'Nueva'} factura recurrente</h3>
    <label class="co-f">Proveedor<input id="rf-p" placeholder="Escribe para buscar en Odoo…" autocomplete="off" value="${escH(x.partner_nombre || '')}"></label><div id="rf-pr"></div>
    <label class="co-f">Concepto<input id="rf-c" placeholder="Alquiler Tienda 1, luz, teléfono…" value="${escH(x.concepto || '')}"></label>
    <label class="co-f">Empresa<select id="rf-e">${emps.map(o => `<option value="${o.value}" ${String(x.empresa) === o.value ? 'selected' : ''}>${escH(o.textContent)}</option>`).join('')}</select></label>
    <div class="co-row"><label class="co-f">Importe aproximado<input id="rf-i" type="number" step="any" value="${x.importe || ''}"></label>
    <label class="co-f">Día del mes en que suele llegar<input id="rf-d" type="number" min="1" max="31" value="${x.dia || ''}"></label></div>
    <button id="rf-ok" class="ac-go">Guardar</button>`;
  let pid = x.partner_id || null, pn = x.partner_nombre || '';
  let t; $('#rf-p').oninput = () => { pid = null; clearTimeout(t); t = setTimeout(async () => {
    const l = await api(`/api/compras/proveedores?q=${encodeURIComponent($('#rf-p').value)}&company=${company}`);
    $('#rf-pr').innerHTML = ''; l.forEach(p => { const b = document.createElement('button'); b.className = 'ac-pick'; b.innerHTML = `<span></span><small>${escH(p.vat || '')}</small>`; b.firstChild.textContent = p.display_name;
      b.onclick = () => { pid = p.id; pn = p.display_name; $('#rf-p').value = pn; $('#rf-pr').innerHTML = ''; }; $('#rf-pr').appendChild(b); });
  }, 300); };
  $('#rf-ok').onclick = async () => {
    if (!pid) return alertBox('Elige el proveedor de la lista (tiene que existir en Odoo)');
    const e = $('#rf-e');
    try { await jpost('/api/compras/recurrentes', { id: x.id, partner_id: pid, partner_nombre: pn, concepto: $('#rf-c').value, empresa: e.value, empresa_nombre: e.selectedOptions[0].textContent, importe: $('#rf-i').value, dia: $('#rf-d').value });
      $('#co-drawer').hidden = true; coRecu(); } catch (er) { alertBox(er.message); }
  };
  $('#co-drawer').hidden = false;
}
/* --- usuarios (solo Juan) --- */
$('#usr-btn').onclick = async () => {
  const l = await api('/api/usuarios'), emps = [...$('#company').options].filter(o => o.value !== 'all').map(o => o.textContent);
  const MOD_N = { ventas: 'Ventas', web: 'Web', whatsapp: 'WhatsApp', presupuestos: 'Presupuestos', ac: 'Panel AC', compras: 'Compras', facturacion: 'Facturación', correo: 'Correo', vencimientos: 'Vencimientos' };
  $('#co-db').innerHTML = `<h3>Usuarios del panel</h3><table class="co-t"><thead><tr><th>Usuario</th><th>Empresas</th><th>Apartados</th><th>Contraseña</th></tr></thead><tbody>
    ${l.map(u => `<tr><td><b>${escH(u.usuario)}</b></td><td>${escH(u.empresas.join(', '))}</td><td>${u.modulos.map(m => MOD_N[m]).join(', ')}</td><td>${u.tiene_clave ? 'puesta' : '<i>la elige al entrar</i>'}</td></tr>`).join('')}</tbody></table>
    <h4>Añadir o cambiar usuario</h4>
    <label class="co-f">Nombre de usuario<input id="us-n" placeholder="mayte"></label>
    <p class="muted">Empresas que ve:</p><div class="chk-l"><label><input type="checkbox" value="*" checked> Todas</label>${emps.map(e => `<label><input type="checkbox" value="${escH(e.split(' ')[0])}"> ${escH(e)}</label>`).join('')}</div>
    <p class="muted">Apartados:</p><div class="chk-m">${Object.entries(MOD_N).map(([k, n]) => `<label><input type="checkbox" value="${k}" ${['ventas', 'compras'].includes(k) ? 'checked' : ''}> ${n}</label>`).join('')}</div>
    <label class="chk1"><input type="checkbox" id="us-r"> Borrar su contraseña (la vuelve a elegir al entrar)</label>
    <button id="us-ok" class="ac-go">Guardar usuario</button>
    <p class="muted">La primera vez que entre, la contraseña que escriba quedará como la suya.</p>`;
  $('#us-ok').onclick = async () => {
    const empresas = [...document.querySelectorAll('.chk-l input:checked')].map(i => i.value), modulos = [...document.querySelectorAll('.chk-m input:checked')].map(i => i.value);
    try { await jpost('/api/usuarios', { usuario: $('#us-n').value, empresas: empresas.includes('*') ? ['*'] : empresas, modulos, reiniciar_clave: $('#us-r').checked });
      alertBox('Usuario guardado'); $('#usr-btn').click(); } catch (e) { alertBox(e.message); }
  };
  $('#co-drawer').hidden = false;
};

/* --- bancos --- */
const bnDrop = document.querySelector('.d-ban');
bnDrop.ondragover = e => { e.preventDefault(); bnDrop.classList.add('over'); };
bnDrop.ondragleave = () => bnDrop.classList.remove('over');
bnDrop.ondrop = e => { e.preventDefault(); bnDrop.classList.remove('over'); bnLeer([...e.dataTransfer.files]); };
$('#bn-f').onchange = () => { bnLeer([...$('#bn-f').files]); $('#bn-f').value = ''; };
async function bnLeer(files, diario) {
  for (const f of files) {
    const fd = new FormData(); fd.append('archivo', f); if (diario) fd.append('diario', diario);
    const box = document.createElement('div'); box.className = 'bn-box'; box.innerHTML = `<p class="muted">Leyendo ${escH(f.name)}…</p>`; $('#bn-prev').prepend(box);
    try {
      const r = await fetch('/api/bancos/leer', { method: 'POST', body: fd, credentials: 'same-origin' }); const d = await r.json(); if (!r.ok) throw new Error(d.detail || r.statusText);
      bnPinta(box, d, f);
    } catch (e) { box.innerHTML = `<p class="err">${escH(f.name)}: ${escH(e.message)}</p>`; }
  }
}
function bnPinta(box, d, file) {
  const nuevas = d.filas.filter(x => !x.ya_en_odoo), ya = d.filas.length - nuevas.length;
  box.innerHTML = `<div class="bn-h"><b>${escH(d.archivo)}</b>
    <label>Cuenta: <select>${d.diario ? '' : '<option value="">— elige la cuenta —</option>'}${d.diarios.map(j => `<option value="${j.id}" ${d.diario && d.diario.id === j.id ? 'selected' : ''}>${escH(j.nombre)} · ${escH(j.empresa.split(' ')[0])} · …${escH(j.iban.slice(-4))}</option>`).join('')}</select></label>
    <span class="bdg b-ok">${nuevas.length} nuevos</span>${ya ? `<span class="bdg">${ya} ya en Odoo</span>` : ''}</div>
    <table class="co-t bn-t"><thead><tr><th>Fecha</th><th>Concepto</th><th>Más datos</th><th>Importe</th><th></th></tr></thead><tbody>${d.filas.map(x => `<tr class="${x.ya_en_odoo ? 'ya' : ''}"><td>${coDate(x.fecha)}</td><td>${escH(x.concepto)}</td><td>${escH(x.empresa)}</td>
      <td class="${x.importe < 0 ? 'neg' : 'pos'}">${eur(x.importe)}</td><td>${x.ya_en_odoo === undefined ? '' : x.ya_en_odoo ? 'ya está' : 'nuevo'}</td></tr>`).join('')}</tbody></table>
    ${d.diario ? (nuevas.length ? `<button class="ac-go bn-go">Meter ${nuevas.length} movimientos en Odoo</button>` : '<p class="muted">✓ Todo este extracto ya está en Odoo.</p>') : '<p class="err">No reconozco la cuenta: elígela arriba.</p>'}`;
  box.querySelector('select').onchange = e => { box.remove(); bnLeer([file], e.target.value); };
  const b = box.querySelector('.bn-go');
  if (b) b.onclick = async () => {
    b.disabled = true; b.textContent = 'Metiendo…';
    try { const r = await jpost('/api/bancos/importar', { diario: d.diario.id, filas: d.filas }); box.innerHTML = `<div class="ac-docs"><b>✓ ${escH(d.diario.nombre)}:</b> ${r.creadas} movimientos metidos en Odoo.</div>`; bnPend(); }
    catch (e) { alertBox(e.message); b.disabled = false; b.textContent = 'Reintentar'; }
  };
}
async function bnPend() {
  const box = $('#bn-pend'); box.innerHTML = '<p class="muted">Cargando…</p>';
  try {
    const d = await api('/api/bancos/pendientes'); $('#bn-odoo').href = d.odoo_url + '/odoo/accounting';
    if (!d.lineas.length) { box.innerHTML = '<p class="empty">✓ No queda nada sin conciliar en los bancos</p>'; return; }
    const g = {}; d.lineas.forEach(l => (g[l.cuenta] = g[l.cuenta] || []).push(l));
    box.innerHTML = Object.entries(g).map(([c, ls]) => `<h4>${escH(c)} <span class="muted">(${ls.length})</span></h4>` + ls.map(l => `<div class="bn-l"><span>${coDate(l.fecha)}</span><span class="cc"><b>${escH(l.concepto)}</b> <small>${escH(l.empresa)}</small></span>
      <span class="${l.importe < 0 ? 'neg' : 'pos'}">${eur(l.importe)}</span>
      <span class="sg">${l.sugerencias.length ? l.sugerencias.map(s => `<a target="_blank" rel="noopener" href="${d.odoo_url}/odoo/action-account.action_move_${l.importe > 0 ? 'out' : 'in'}_invoice_type/${s.id}">💡 ${escH(s.factura)} · ${escH(s.cliente)}</a>`).join('') : '<small class="muted">sin factura que cuadre por importe</small>'}</span></div>`).join('')).join('');
  } catch (e) { box.innerHTML = `<p class="err">${escH(e.message)}</p>`; }
}
