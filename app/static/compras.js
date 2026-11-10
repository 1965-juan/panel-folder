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
    alertBox(err.length ? err.map(x => `${x.nombre}: ${x.error}`).join(' · ') : (tipo === 'factura' ? `Subidas ${j.length}: Odoo las está leyendo. Pasan a Facturas.` : `Subidos ${j.length}: ve a Recepciones para recibirlos.`));
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
async function coDocs() {
  $('#co-emp-n').textContent = company === 'all' ? 'elige empresa arriba' : coEmpNombre();
  try {
    const l = (await api(`/api/compras/docs?company=${company}`)).filter(d => d.estado !== 'descartado');
    $('#co-docs').innerHTML = l.length ? '' : '<p class="empty">Todavía no se ha subido nada</p>';
    l.slice(0, 40).forEach(d => {
      const [t, c] = CO_EST[d.estado] || [d.estado, ''];
      $('#co-docs').insertAdjacentHTML('beforeend', `<div class="co-doc"><span class="ic">${d.tipo === 'albaran' ? '📦' : '🧾'}</span>
        <a target="_blank" rel="noopener" href="/api/compras/pdf/${d.id}">${escH(d.nombre)}</a><span class="bdg ${c}">${t}</span>
        <small class="muted">${escH(d.usuario)} · ${hora(d.subido)}</small>
        ${d.compra ? `<span class="bdg b-ok" title="Recepción ${escH((d.compra.recepciones || []).join(', '))}">🛒 ${escH(d.compra.pedido)}</span>`
                   : `<button class="ac-go co-pc" data-id="${d.id}">➜ Pasar a compras</button>`}
        ${d.nota ? `<small class="err-t">${escH(d.nota)}</small>` : ''}</div>`);
    });
    $('#co-docs').querySelectorAll('.co-pc').forEach(b => b.onclick = () => coPasar(b.dataset.id));
  } catch (e) { showErr(e.message); }
}
/* --- pasar un PDF a compras: pedido de compra confirmado (+ factura en borrador enlazada) --- */
const CO_COMO = { 'ref. proveedor': 'ref. proveedor', EAN: 'EAN', referencia: 'referencia', nombre: 'nombre', 'a mano': 'elegido' };
async function coPasar(did, releer) {
  const db = $('#co-db');
  db.innerHTML = `<h3>Pasar a compras</h3><p class="muted">Leyendo el PDF y buscando los artículos en Odoo… (tarda unos segundos)</p>`;
  $('#co-drawer').hidden = false;
  let d;
  try { d = await jpost(`/api/compras/docs/${did}/preparar${releer ? '?releer=1' : ''}`, {}); }
  catch (e) { db.innerHTML = `<h3>Pasar a compras</h3><p class="err">${escH(e.message)}</p>`; return; }
  const L = d.lineas, lec = d.lectura;
  let prov = d.proveedor;
  const eu = n => (Math.round((+n || 0) * 100) / 100).toLocaleString('es-ES', { minimumFractionDigits: 2 }) + ' €';
  const pinta = () => {
    const nuevos = L.filter(l => !l.product_id && l.crear).length, faltan = L.filter(l => !l.product_id && !l.crear && +l.cantidad > 0).length;
    const base = L.reduce((a, l) => a + (+l.cantidad || 0) * (+l.precio || 0) * (1 - (+l.descuento || 0) / 100), 0);
    db.innerHTML = `<h3>Pasar a compras · ${escH(d.doc.nombre)}</h3>
      <p class="muted">${(lec.tipo || d.doc.tipo) === 'factura' ? '🧾 Factura' : (lec.tipo === 'pedido' ? '📋 Confirmación de pedido' : '📦 Albarán')} ${escH(lec.numero || '')} · ${coDate(lec.fecha || '')} · <a href="#" id="pc-rel">volver a leer el PDF</a></p>
      <iframe class="co-pdf" src="/api/compras/pdf/${d.doc.id}#view=FitH"></iframe>
      <div class="pc-prov"><b>Proveedor:</b> ${prov ? `${escH(prov.display_name)} <small class="muted">${escH(prov.vat || '')}</small> <a href="#" id="pc-cprov">cambiar</a>`
        : `<span class="err-t">No lo encuentro (${escH(lec.proveedor || '?')} ${escH(lec.nif_proveedor || '')}).</span>`}
        <div id="pc-bprov" ${prov ? 'hidden' : ''}><input id="pc-qprov" placeholder="Buscar proveedor por nombre o CIF"><div id="pc-lprov"></div></div></div>
      ${d.pedidos_abiertos.length ? `<div class="pc-aviso">⚠️ Este proveedor ya tiene pedidos abiertos: ${d.pedidos_abiertos.map(p => `<b>${escH(p.name)}</b> (${coDate(p.fecha)}, ${eu(p.total)})`).join(', ')}.
        Si este documento es de uno de ellos, no lo pases otra vez: recíbelo desde Recepciones.</div>` : ''}
      <table class="co-t pc-t"><thead><tr><th>Ref.</th><th>Descripción</th><th>Cant.</th><th>Precio</th><th>Dto</th><th>Artículo en Odoo</th></tr></thead><tbody>
      ${L.map((l, i) => `<tr><td>${escH(l.ref)}</td><td>${escH(l.descripcion)}</td>
        <td><input type="number" step="any" min="0" data-i="${i}" data-k="cantidad" value="${l.cantidad}"></td>
        <td><input type="number" step="any" min="0" data-i="${i}" data-k="precio" value="${l.precio}"></td>
        <td>${l.descuento ? l.descuento + '%' : ''}</td>
        <td>${l.product_id ? `<span class="pc-ok">✓ ${escH(l.producto)}</span> <small class="muted">${CO_COMO[l.como] || ''}</small> <a href="#" data-cam="${i}">cambiar</a>`
          : l.crear ? `<span class="pc-new">＋ Se crea nuevo</span> <a href="#" data-cam="${i}">buscar en Odoo</a>`
          : `<span class="pc-que">¿Cuál es?</span> ${(l.sugerencias || []).map(sg => `<a href="#" class="pc-sug" data-i="${i}" data-id="${sg.id}">${escH(sg.name)}</a>`).join('')}
             <a href="#" data-cam="${i}">🔎 buscar otro</a> · <a href="#" class="pc-crea" data-i="${i}">＋ es nuevo, crearlo</a>`}
          <div class="pc-bus" data-b="${i}" hidden><input placeholder="Nombre, ref. o EAN"><div></div></div></td></tr>`).join('')}
      </tbody></table>
      <p class="muted">Base según líneas: <b>${eu(base)}</b>${lec.base ? ` · base del PDF: <b>${eu(lec.base)}</b>` : ''}${lec.total ? ` · total PDF: ${eu(lec.total)}` : ''}${lec.portes ? ` · portes: ${eu(lec.portes)}` : ''}</p>
      ${nuevos ? `<p class="pc-aviso">Se crearán <b>${nuevos}</b> artículos nuevos con su ref. y EAN del proveedor y el coste. Luego revisad el precio de venta y la foto.</p>` : ''}
      <p class="muted">Se crea el pedido de compra confirmado. La recepción queda en <b>Recepciones</b> para que almacén compruebe lo que llega${d.doc.tipo === 'factura' || lec.tipo === 'factura' ? ', y la factura queda en borrador enlazada al pedido para que Mayte le dé el OK' : ''}.</p>
      ${faltan ? `<p class="pc-aviso">Quedan <b>${faltan}</b> líneas por decidir: elige el artículo de Odoo o márcalo como nuevo.</p>` : ''}
      <button class="ac-go" id="pc-go" ${prov && !faltan ? '' : 'disabled'}>🛒 Crear pedido de compra</button><div id="pc-res"></div>`;
    db.querySelectorAll('.pc-sug').forEach(a => a.onclick = e => { e.preventDefault(); const l = L[a.dataset.i]; l.product_id = +a.dataset.id; l.producto = a.textContent; l.como = 'a mano'; pinta(); });
    db.querySelectorAll('.pc-crea').forEach(a => a.onclick = e => { e.preventDefault(); L[a.dataset.i].crear = true; pinta(); });
    $('#pc-rel').onclick = e => { e.preventDefault(); coPasar(did, true); };
    const cp = $('#pc-cprov'); if (cp) cp.onclick = e => { e.preventDefault(); $('#pc-bprov').hidden = false; $('#pc-qprov').focus(); };
    let tp; $('#pc-qprov').oninput = e => { clearTimeout(tp); tp = setTimeout(async () => {
      const r = await api(`/api/compras/proveedores?q=${encodeURIComponent(e.target.value)}&company=${d.doc.empresa}`);
      $('#pc-lprov').innerHTML = r.map(p => `<a href="#" class="pc-opt" data-id="${p.id}">${escH(p.display_name)} <small>${escH(p.vat || '')}</small></a>`).join('');
      $('#pc-lprov').querySelectorAll('a').forEach(a => a.onclick = ev => { ev.preventDefault(); prov = r.find(x => x.id == a.dataset.id); pinta(); });
    }, 300); };
    db.querySelectorAll('input[data-k]').forEach(inp => inp.onchange = () => { L[inp.dataset.i][inp.dataset.k] = parseFloat(inp.value) || 0; });
    db.querySelectorAll('[data-cam]').forEach(a => a.onclick = e => {
      e.preventDefault(); const i = a.dataset.cam, box = db.querySelector(`[data-b="${i}"]`); box.hidden = false;
      const inp = box.querySelector('input'), out = box.querySelector('div'); inp.focus(); let t;
      out.innerHTML = `<a href="#" class="pc-opt" data-nuevo="1">＋ No es ninguno: crear artículo nuevo</a>`;
      const bindNuevo = () => { const n = out.querySelector('[data-nuevo]'); if (n) n.onclick = ev => { ev.preventDefault(); L[i].product_id = null; L[i].producto = null; L[i].como = null; L[i].crear = true; pinta(); }; };
      bindNuevo();
      inp.oninput = () => { clearTimeout(t); t = setTimeout(async () => {
        const r = await api(`/api/ac/productos?q=${encodeURIComponent(inp.value)}&company=${d.doc.empresa}`);
        out.innerHTML = r.map(p => `<a href="#" class="pc-opt" data-id="${p.id}">${escH(p.display_name)}</a>`).join('') + `<a href="#" class="pc-opt" data-nuevo="1">＋ Crear artículo nuevo</a>`;
        out.querySelectorAll('[data-id]').forEach(o => o.onclick = ev => { ev.preventDefault(); const p = r.find(x => x.id == o.dataset.id); L[i].product_id = p.id; L[i].producto = p.display_name; L[i].como = 'a mano'; L[i].crear = false; pinta(); });
        bindNuevo();
      }, 300); };
    });
    $('#pc-go').onclick = async () => {
      if (!confirm(`¿Crear y confirmar el pedido de compra a ${prov.display_name}?`)) return;
      $('#pc-go').disabled = true; $('#pc-go').textContent = 'Creando en Odoo…';
      try {
        const r = await jpost(`/api/compras/docs/${did}/a_compras`, { partner_id: prov.id, lineas: L.map(l => ({ ...l, crear: !l.product_id && !!l.crear, sugerencias: undefined })) });
        $('#pc-res').innerHTML = `<div class="ac-docs"><b>✓ Pedido ${escH(r.pedido)} confirmado.</b><br>
          Recepción para almacén: <b>${escH(r.recepciones.join(', ') || '—')}</b> (en la pestaña Recepciones).
          ${r.factura ? `<br>Factura en borrador enlazada para Mayte (total ${eu(r.factura.total)}).` : ''}
          ${r.creados.length ? `<br>Artículos nuevos: ${r.creados.length}. Revisad precio de venta y foto.` : ''}
          ${r.avisos.map(a => `<br>⚠️ ${escH(a)}`).join('')}</div>`;
        $('#pc-go').remove(); coDocs(); coBadge();
      } catch (e) { alertBox(e.message); $('#pc-go').disabled = false; $('#pc-go').textContent = '🛒 Crear pedido de compra'; }
    };
  };
  if (d.compra) {
    db.innerHTML = `<h3>Ya está en compras</h3><div class="ac-docs">Este documento ya tiene el pedido <b>${escH(d.compra.pedido)}</b> · recepción ${escH((d.compra.recepciones || []).join(', ') || '—')}. No hace falta pasarlo otra vez.</div>`;
    coDocs(); return;
  }
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
      const recibido = !x.sin_pedido && !x.avisos.some(a => a.includes('han llegado'));
      d.innerHTML = `<div class="c1"><span class="ref"></span><b>${eur(x.total)}</b></div>
        <div class="cli">${x.numero ? 'Nº ' + escH(x.numero) : '<i>sin nº</i>'} · ${coDate(x.fecha) || '<i>sin fecha</i>'}${x.abono ? ' · <b>ABONO</b>' : ''}</div>
        <div class="chips">${x.pedidos.map(p => `<span class="chip">${escH(p)}</span>`).join('')}
          ${x.estado === 'leyendo' ? '' : x.sin_pedido ? '<span class="chip gris">Sin pedido de compra (gasto)</span>' : recibido ? '<span class="chip verde">✓ Material recibido</span>' : '<span class="chip rojo">Material sin llegar del todo</span>'}</div>
        <div class="avs"></div>
        <div class="c3"><span class="emp"></span><span>${x.vence ? 'vence ' + coDate(x.vence) : ''}</span></div>
        <div class="fbtn">${x.pdf ? `<a class="lnk" target="_blank" rel="noopener" href="${ODOO}/web/content/${x.pdf}">Ver PDF</a>` : ''}
          <a class="lnk" target="_blank" rel="noopener" href="${ODOO}/odoo/action-account.action_move_in_invoice_type/${x.id}">Abrir en Odoo</a>
          ${x.estado !== 'leyendo' ? `<button class="okb">✓ OK, contabilizar</button>` : ''}</div>`;
      d.querySelector('.ref').textContent = x.proveedor; d.querySelector('.emp').textContent = x.empresa;
      const avs = d.querySelector('.avs');
      [...(x.estado !== 'leyendo' && x.faltan.length ? ['Falta: ' + x.faltan.join(', ')] : []), ...x.avisos].forEach(a => { const v = document.createElement('div'); v.className = 'av'; v.textContent = '⚠ ' + a; avs.appendChild(v); });
      const b = d.querySelector('.okb');
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
  const MOD_N = { ventas: 'Ventas', web: 'Web', whatsapp: 'WhatsApp', presupuestos: 'Presupuestos', ac: 'Panel AC', compras: 'Compras' };
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
