/* ---------- Mi tienda: reposiciones pedidas a almacén y encargos de clientes ---------- */
let TI = null;
async function tiOpen() {
  $('#ti-rep').innerHTML = $('#ti-enc').innerHTML = '<p class="muted">Cargando…</p>';
  try { TI = await api(`/api/tienda/datos?t=${encodeURIComponent($('#ti-sel').value || '')}`); }
  catch (e) { $('#ti-rep').innerHTML = `<p class="err">${escH(e.message)}</p>`; $('#ti-enc').innerHTML = ''; return; }
  const sel = $('#ti-sel');
  sel.hidden = TI.fija;
  if (!TI.fija && !sel.options.length) { sel.innerHTML = TI.tiendas.map(t => `<option value="${t.codigo}">${escH(t.nombre)}</option>`).join(''); sel.value = TI.tienda; }
  $('#ti-tpv').href = TI.tpv; $('#ti-tpv').textContent = `🧾 Abrir el TPV de ${TI.nombre}`;
  $('#h1').textContent = TI.nombre;
  const tabla = ls => `<table class="co-t ti-t"><thead><tr><th>Artículo</th><th class="num">Pedido</th><th class="num">Servido</th><th class="num">Falta</th></tr></thead><tbody>
    ${ls.map(l => `<tr><td>${escH(l.articulo)}${l.estado ? ` <span class="bdg">${escH(l.estado)}</span>` : ''}</td><td class="num">${+l.pedido}</td><td class="num">${+l.servido}</td><td class="num"><b>${+l.pendiente}</b></td></tr>`).join('')}</tbody></table>`;
  const rep = TI.reposiciones.filter(r => !r.error);
  $('#ti-rep').innerHTML = (TI.reposiciones.find(r => r.error) ? `<p class="err">${escH(TI.reposiciones.find(r => r.error).error)}</p>` : '') +
    (rep.length ? rep.map(r => `<div class="ti-card"><div class="ti-h"><b>${escH(r.ref)}</b><span class="muted">${escH(r.fecha)}</span></div>${tabla(r.lineas)}</div>`).join('')
      : '<p class="empty">No tenéis reposiciones pendientes</p>');
  $('#ti-enc').innerHTML = TI.encargos.length ? TI.encargos.map(o => `<div class="ti-card"><div class="ti-h"><b>${escH(o.ref)}</b> ${escH(o.cliente)}${o.ref_cliente ? ` · <i>${escH(o.ref_cliente)}</i>` : ''}
      <span class="muted">${coDate(o.fecha)} · ${eur(o.total)}</span></div>${tabla(o.lineas)}</div>`).join('') : '<p class="empty">No hay encargos pendientes de servir</p>';
  $('#ti-cob').innerHTML = TI.por_cobrar.length ? `<table class="co-t ti-t"><tbody>${TI.por_cobrar.map(o => `<tr><td><b>${escH(o.ref)}</b> ${escH(o.cliente)}${o.ref_cliente ? ` · <i>${escH(o.ref_cliente)}</i>` : ''}
      <br><small class="muted">${coDate(o.fecha)}${o.factura ? ' · ' + escH(o.factura) : ' · se factura al cobrar'}</small></td><td class="num"><b>${eur(o.importe)}</b></td>
      <td class="ti-cb"><button class="ac-go" data-c="${o.id}" data-f="caja">💶 Caja</button><button class="ac-go ti-tar" data-c="${o.id}" data-f="tarjeta">💳 TPV banco</button></td></tr>`).join('')}</tbody></table>`
    : '<p class="empty">No hay nada servido pendiente de cobrar</p>';
  $('#ti-cob').querySelectorAll('[data-c]').forEach(b => b.onclick = async () => {
    const o = TI.por_cobrar.find(x => x.id === +b.dataset.c), caja = b.dataset.f === 'caja';
    if (!confirm(`¿Cobrar ${eur(o.importe)} de ${o.cliente} (${o.ref}) ${caja ? 'en efectivo, a la caja de la tienda' : 'con tarjeta, por el TPV del banco'}?`)) return;
    b.disabled = true; b.textContent = 'Cobrando…';
    try { const r = await jpost('/api/tienda/cobrar', { so_id: o.id, forma: b.dataset.f, t: TI.tienda });
      alertBox(`Cobrado ${eur(r.importe)} (${r.facturas.join(', ')})`); tiOpen(); } catch (e) { alertBox(e.message); tiOpen(); }
  });
  $('#ti-info').textContent = `${rep.length} reposiciones · ${TI.encargos.length} encargos pendientes`;
}
$('#ti-sel').onchange = () => tiOpen();
$('#ti-rec').onclick = () => tiOpen();
