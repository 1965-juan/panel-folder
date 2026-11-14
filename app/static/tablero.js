/* ---------- Compras: tablero tipo Trello (una columna por proveedor, un pedido en borrador por proveedor) ---------- */
let TB = null, TB_NUEVAS = [];   // columnas vacías añadidas a mano (proveedor sin pedido todavía)

async function tbOpen(juntar = true) {
  $('#tb-board').innerHTML = '<p class="muted">Cargando pedidos en borrador…</p>';
  try {
    if (juntar) await jpost('/api/tablero/juntar', {}).catch(() => null);   // un solo pedido por proveedor
    TB = await api('/api/tablero');
  } catch (e) { $('#tb-board').innerHTML = `<p class="err">${escH(e.message)}</p>`; return; }
  TB_NUEVAS = TB_NUEVAS.filter(n => !TB.columnas.some(c => c.partner_id === n.partner_id));
  tbPinta();
}

function tbPinta() {
  const q = ($('#tb-q').value || '').toLowerCase().trim();
  const cols = TB.columnas.concat(TB_NUEVAS);
  const nLin = TB.columnas.reduce((a, c) => a + c.lineas.length, 0);
  $('#tb-info').textContent = `${TB.columnas.length} proveedores · ${nLin} artículos por pedir`;
  const tarjeta = (l, c) => {
    const otros = (l.proveedores || []).filter(p => p.partner_id !== c.partner_id).slice(0, 3);
    const este = (l.proveedores || []).find(p => p.partner_id === c.partner_id);
    return `<div class="tb-card" draggable="true" data-l="${l.id}" data-p="${c.partner_id}">
      <div class="tb-cn"><b>${escH(l.articulo.replace(/^\[[^\]]*\]\s*/, ''))}</b><button class="tb-x" data-x="${l.id}" title="Quitar del pedido">✕</button></div>
      <div class="tb-cm">${l.ref ? escH(l.ref) + ' · ' : ''}<input type="number" min="0" step="1" value="${+l.cantidad}" data-q="${l.id}"> ${escH(l.ud)} · ${eur(l.precio)}</div>
      ${l.para ? `<div class="tb-para">para ${escH(l.para)}</div>` : ''}
      ${!este ? `<div class="tb-aviso">⚠️ ${l.proveedores.length ? 'este proveedor no está en la ficha del artículo' : 'el artículo no tiene ningún proveedor en su ficha'}
        <br><a href="#" class="lnk" data-fi="${l.id}">＋ ponerlo en su ficha (${eur(l.precio)})</a> · <a class="lnk" target="_blank" rel="noopener" href="${TB.odoo_url}/odoo/action-purchase.product_normal_action_puchased/${l.tmpl_id}">abrir en Odoo ↗</a></div>` : ''}
      ${otros.length ? `<div class="tb-alt">${otros.map(p => `<span title="${escH(p.ref)}">${escH(p.proveedor.split(' ')[0])} ${p.precio ? eur(p.precio) : ''}</span>`).join('')}</div>` : ''}
    </div>`;
  };
  $('#tb-board').innerHTML = cols.map(c => {
    const lanz = c.lanzados || [];
    const ls = c.lineas.filter(l => !q || (l.articulo + ' ' + l.ref + ' ' + c.proveedor + ' ' + l.para).toLowerCase().includes(q));
    const enLanz = lanz.some(z => z.lineas.some(l => (l.articulo + ' ' + l.ref).toLowerCase().includes(q)));
    if (q && !ls.length && !enLanz && !c.proveedor.toLowerCase().includes(q)) return '';
    const po = c.pedidos[0];
    return `<div class="tb-col ${c.mismo ? 'tb-mal' : ''} ${c.grupo ? 'tb-grupo' : ''}" data-p="${c.partner_id}">
      <div class="tb-ch"><b>${escH(c.proveedor)}</b>${c.grupo ? ' <span class="bdg">grupo</span>' : ''}
        ${c.mismo ? '<div class="tb-aviso">⚠️ Distriplus pidiéndose a sí misma: mueve estos artículos a su proveedor</div>' : ''}</div>
      <div class="tb-acu">
        <div class="tb-sub">🛒 Acumulando · ${po ? `<a class="lnk" target="_blank" rel="noopener" href="${TB.odoo_url}/odoo/action-purchase.purchase_rfq/${po.id}">${escH(po.nombre)}</a>` : 'sin pedido'} · ${c.lineas.length} art. · ${eur(c.total)}</div>
        <div class="tb-list" data-p="${c.partner_id}">${ls.map(l => tarjeta(l, c)).join('') || '<p class="tb-vacio">Arrastra aquí artículos</p>'}</div>
        ${po && c.lineas.length && !c.mismo ? `<label class="tb-ok"><input type="checkbox" data-po="${po.id}" data-n="${escH(c.proveedor)}"> Lanzar pedido</label>` : ''}
      </div>
      ${lanz.map(z => `<div class="tb-lanz">
        <div class="tb-fl">✔ Lanzado el ${coDate(z.fecha)}</div>
        <div class="tb-cs"><a class="lnk" target="_blank" rel="noopener" href="${TB.odoo_url}/odoo/action-purchase.purchase_form_action/${z.id}">${escH(z.nombre)}</a>
          · ${z.lineas.length} art. · ${eur(z.total)} · ${z.recepcion === 'partial' ? 'recibido en parte' : 'esperando que llegue'}</div>
        <a href="#" class="lnk tb-oc" data-oc="${z.id}" title="No es un pedido de material (imprenta, servicios…): quitarlo del tablero">🙈 no es de material</a>
        <details><summary>ver artículos</summary>${z.lineas.map(l => `<div class="tb-card tb-fija"><b>${escH(l.articulo.replace(/^\[[^\]]*\]\s*/, ''))}</b>
          <div class="tb-cm">${l.ref ? escH(l.ref) + ' · ' : ''}${+l.cantidad} ${escH(l.ud)} · ${eur(l.precio)}${l.recibido ? ` · <b>recibidas ${+l.recibido}</b>` : ''}</div></div>`).join('')}</details>
      </div>`).join('')}
    </div>`;
  }).join('') + `<div class="tb-col tb-add"><b>＋ Proveedor</b><input id="tb-np" placeholder="Buscar proveedor…" autocomplete="off"><div id="tb-nps"></div></div>`;
  tbEventos();
}

function tbEventos() {
  const B = $('#tb-board');
  B.querySelectorAll('.tb-card').forEach(c => {
    c.ondragstart = e => { e.dataTransfer.setData('text/plain', c.dataset.l + '|' + c.dataset.p); c.classList.add('arr'); };
    c.ondragend = () => c.classList.remove('arr');
  });
  B.querySelectorAll('.tb-col[data-p]').forEach(col => {
    col.ondragover = e => { e.preventDefault(); col.classList.add('over'); };
    col.ondragleave = () => col.classList.remove('over');
    col.ondrop = async e => {
      e.preventDefault(); col.classList.remove('over');
      const [lid, desde] = (e.dataTransfer.getData('text/plain') || '').split('|');
      if (!lid || desde === col.dataset.p) return;
      const card = B.querySelector(`.tb-card[data-l="${lid}"]`); if (card) { card.style.opacity = .4; col.querySelector('.tb-list').prepend(card); }
      try { await jpost('/api/tablero/mover', { line_id: +lid, partner_id: +col.dataset.p }); }
      catch (err) { alertBox(err.message); }
      tbOpen(false);
    };
  });
  B.querySelectorAll('[data-q]').forEach(i => i.onchange = async () => {
    try { await jpost('/api/tablero/cantidad', { line_id: +i.dataset.q, cantidad: i.value }); tbOpen(false); } catch (e) { alertBox(e.message); }
  });
  B.querySelectorAll('[data-x]').forEach(b => b.onclick = async () => {
    if (!confirm('¿Quitar este artículo del pedido? (no se pedirá a ningún proveedor)')) return;
    try { await jpost('/api/tablero/quitar', { line_id: +b.dataset.x }); tbOpen(false); } catch (e) { alertBox(e.message); }
  });
  B.querySelectorAll('.tb-ok input').forEach(b => b.onchange = async () => {
    if (!b.checked) return;
    if (!confirm(`¿Lanzar ya el pedido a ${b.dataset.n}?\n\nSe confirma en Odoo con fecha de hoy y la entrada aparecerá en Recepciones. Lo que se marque después para este proveedor irá a un pedido nuevo.`)) { b.checked = false; return; }
    b.closest('.tb-acu').classList.add('tb-lanzando');
    try { await jpost('/api/tablero/confirmar', { po_id: +b.dataset.po }); tbOpen(false); } catch (e) { b.checked = false; b.closest('.tb-acu').classList.remove('tb-lanzando'); alertBox(e.message); }
  });
  B.querySelectorAll('[data-fi]').forEach(a => a.onclick = async e => { e.preventDefault();
    try { const r = await jpost('/api/tablero/ficha', { line_id: +a.dataset.fi }); a.closest('.tb-aviso').innerHTML = `✅ ${escH(r.proveedor)} añadido a la ficha`; } catch (er) { alertBox(er.message); } });
  B.querySelectorAll('[data-oc]').forEach(a => a.onclick = async e => { e.preventDefault();
    if (!confirm('¿Quitar este pedido del tablero? (no se borra de Odoo; es para pedidos de imprenta o servicios que no son material)')) return;
    try { await jpost('/api/tablero/ocultar', { po_id: +a.dataset.oc }); tbOpen(false); } catch (er) { alertBox(er.message); } });
  const np = $('#tb-np'); let t;
  np.oninput = () => { clearTimeout(t); t = setTimeout(async () => {
    const q = np.value.trim(); if (q.length < 2) return $('#tb-nps').innerHTML = '';
    const r = await api(`/api/tablero/proveedores?q=${encodeURIComponent(q)}`);
    $('#tb-nps').innerHTML = r.map(p => `<a href="#" data-id="${p.id}">${escH(p.nombre)}</a>`).join('') || '<small class="muted">Nada</small>';
    $('#tb-nps').querySelectorAll('a').forEach(a => a.onclick = e => { e.preventDefault();
      const id = +a.dataset.id;
      if (!TB.columnas.some(c => c.partner_id === id) && !TB_NUEVAS.some(c => c.partner_id === id))
        TB_NUEVAS.push({ partner_id: id, proveedor: a.textContent, pedidos: [], lineas: [], lanzados: [], total: 0 });
      tbPinta();
    });
  }, 300); };
}
$('#tb-q').oninput = () => TB && tbPinta();
$('#tb-rec').onclick = () => tbOpen();
