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
  ['subir', 'rec', 'fac', 'recu'].forEach(k => $('#co-' + k).hidden = k !== t);
  ({ subir: coDocs, rec: coRec, fac: coFac, recu: coRecu })[t]();
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
        <small class="muted">${escH(d.usuario)} · ${hora(d.subido)}</small>${d.nota ? `<small class="err-t">${escH(d.nota)}</small>` : ''}</div>`);
    });
  } catch (e) { showErr(e.message); }
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
