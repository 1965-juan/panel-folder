/* ---------- Vencimientos: pagos y cobros por empresa ---------- */
let VE_T = 'pagos', VE = null;
document.querySelectorAll('.ve-tabs button[data-t]').forEach(b => b.onclick = () => { VE_T = b.dataset.t; document.querySelectorAll('.ve-tabs button[data-t]').forEach(x => x.classList.toggle('on', x === b)); veOpen(); });
$('#ve-hasta').onchange = () => veOpen(); $('#ve-pag').onchange = () => veOpen();
const veFecha = d => { const x = new Date(); x.setDate(x.getDate() + d); return x.toISOString().slice(0, 10); };
async function veOpen() {
  $('#ve-l').innerHTML = '<p class="muted">Cargando vencimientos…</p>';
  try { VE = await api(`/api/vencimientos?company=${company}&tipo=${VE_T}&hasta=${veFecha(+$('#ve-hasta').value)}&incluir_pagados=${$('#ve-pag').checked ? 1 : 0}`); }
  catch (e) { $('#ve-l').innerHTML = `<p class="err">${escH(e.message)}</p>`; return; }
  vePinta();
}
function vePinta() {
  const L = VE.vencimientos, hoy = VE.hoy, s7 = veFecha(7), s30 = veFecha(30);
  const sum = f => L.filter(f).reduce((a, v) => a + (v.pagado ? 0 : v.importe), 0);
  const ven = sum(v => v.fecha < hoy), d7 = sum(v => v.fecha >= hoy && v.fecha <= s7), d30 = sum(v => v.fecha >= hoy && v.fecha <= s30), tot = sum(() => true);
  const pal = VE_T === 'pagos' ? 'a pagar' : 'a cobrar';
  $('#ve-k').innerHTML = `<div class="kpi ${ven ? 'mal' : ''}"><span>Vencido sin ${VE_T === 'pagos' ? 'pagar' : 'cobrar'}</span><b>${eur(ven)}</b></div>
    <div class="kpi"><span>Próximos 7 días</span><b>${eur(d7)}</b></div><div class="kpi"><span>Próximos 30 días</span><b>${eur(d30)}</b></div>
    <div class="kpi"><span>Total ${pal} en la lista</span><b>${eur(tot)}</b></div>`;
  // agrupar: vencido, y luego por semanas
  const lunes = f => { const d = new Date(f + 'T00:00:00'); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return d.toISOString().slice(0, 10); };
  const grupos = new Map();
  L.forEach(v => { const k = !v.fecha ? 'sin' : v.fecha < hoy ? 'vencido' : lunes(v.fecha); if (!grupos.has(k)) grupos.set(k, []); grupos.get(k).push(v); });
  const tit = k => k === 'vencido' ? '⚠️ Vencido' : k === 'sin' ? 'Sin fecha' : `Semana del ${coDate(k)}`;
  const orden = [...grupos.keys()].sort((a, b) => (a === 'vencido' ? '0' : a) < (b === 'vencido' ? '0' : b) ? -1 : 1);
  $('#ve-l').innerHTML = L.length ? orden.map(k => { const g = grupos.get(k); const t = g.reduce((a, v) => a + (v.pagado ? 0 : v.importe), 0);
    return `<div class="ve-g ${k === 'vencido' ? 'mal' : ''}"><div class="ve-gh"><b>${tit(k)}</b><span>${eur(t)}</span></div>
      <table class="co-t ve-t"><tbody>${g.map(v => `<tr class="${v.pagado ? 've-pg' : ''}" data-id="${v.id}"><td>${coDate(v.fecha)}${v.vence && v.vence !== v.fecha ? `<br><small class="muted">vence ${coDate(v.vence)}</small>` : ''}</td>
        <td><span class="bdg">${escH(v.empresa_n)}</span></td><td><b>${escH(v.tercero)}</b><br><small>${escH(v.concepto)}</small>${v.nota ? `<br><small class="muted">📝 ${escH(v.nota)}</small>` : ''}</td>
        <td class="num"><b>${eur(v.importe)}</b></td>
        <td>${v.origen === 'manual' ? `<span class="bdg b-warn">a mano</span>` : `<span class="bdg">${escH(v.forma || 'Odoo')}</span>`}</td>
        <td class="ve-ac">${v.origen === 'manual'
          ? `<button class="lnk" data-a="pagado">${v.pagado ? '↩ no pagado' : '✓ pagado'}</button><button class="lnk" data-a="editar">editar</button><button class="lnk del" data-a="borrar">borrar</button>`
          : `<a class="lnk" target="_blank" rel="noopener" href="${ODOO}/odoo/action-account.action_move_${VE_T === 'pagos' ? 'in' : 'out'}_invoice_type/${v.move_id}">abrir</a><button class="lnk" data-a="nota">nota / fecha prevista</button>`}</td></tr>`).join('')}</tbody></table></div>`; }).join('')
    : `<p class="empty">No hay vencimientos ${pal} en este periodo</p>`;
  $('#ve-l').querySelectorAll('[data-a]').forEach(b => b.onclick = () => veAccion(b.dataset.a, L.find(v => v.id === b.closest('tr').dataset.id)));
}
async function veAccion(a, v) {
  try {
    if (a === 'pagado') await jpost(`/api/vencimientos/manual/${v.id.slice(1)}`, { pagado: !v.pagado });
    else if (a === 'borrar') {
      const todos = v.grupo && confirm('Este vencimiento es de una serie. ¿Borrar también los siguientes que estén sin pagar? (Cancelar = solo este)');
      if (!todos && !confirm('¿Borrar este vencimiento?')) return;
      await jpost(`/api/vencimientos/manual/${v.id.slice(1)}`, { borrar: true, todos });
    } else if (a === 'editar') return veForm(v);
    else if (a === 'nota') {
      const n = prompt('Nota para este vencimiento (p. ej. "se paga por confirming", "domiciliado"):', v.nota || ''); if (n === null) return;
      const f = prompt('Fecha prevista de pago (AAAA-MM-DD), vacío = la de vencimiento:', v.fecha !== v.vence ? v.fecha : ''); if (f === null) return;
      await jpost(`/api/vencimientos/odoo/${v.aml_id}`, { nota: n, fecha_prevista: f || null });
    }
    veOpen();
  } catch (e) { alertBox(e.message); }
}
function veForm(v) {
  const f = $('#ve-form'); f.hidden = false;
  const emps = VE ? VE.empresas : [];
  f.innerHTML = `<div class="ve-f"><h3>${v ? 'Editar vencimiento' : 'Nuevo vencimiento ' + (VE_T === 'pagos' ? 'a pagar' : 'a cobrar')}</h3>
    <div class="ve-fr">${v ? '' : `<label>Empresa<select id="vf-emp">${emps.map(e => `<option value="${e.id}" ${String(e.id) === String(company) ? 'selected' : ''}>${escH(e.nombre)}</option>`).join('')}</select></label>`}
      <label>Concepto<input id="vf-con" value="${escH(v ? v.concepto : '')}" placeholder="Préstamo Caixa, leasing furgoneta, alquiler nave…"></label>
      <label>${VE_T === 'pagos' ? 'A quién' : 'De quién'}<input id="vf-ter" value="${escH(v ? v.tercero : '')}"></label>
      <label>Fecha<input type="date" id="vf-fec" value="${v ? v.fecha : new Date().toISOString().slice(0, 10)}"></label>
      <label>Importe €<input type="number" step="0.01" id="vf-imp" value="${v ? v.importe : ''}"></label>
      ${v ? '' : `<label>Repetir<select id="vf-cada"><option value="0">una sola vez</option><option value="1">cada mes</option><option value="3">cada trimestre</option><option value="6">cada semestre</option><option value="12">cada año</option></select></label>
      <label>Nº de veces<input type="number" min="1" max="120" id="vf-n" value="12"></label>`}
      <label class="ve-wide">Nota<input id="vf-nota" value="${escH(v ? v.nota : '')}" placeholder="domiciliado, confirming, transferencia…"></label></div>
    <div class="fa-bar"><button class="ac-go" id="vf-ok">Guardar</button><button class="btn" id="vf-x">Cancelar</button></div></div>`;
  $('#vf-x').onclick = () => { f.hidden = true; };
  $('#vf-ok').onclick = async () => {
    const d = { concepto: $('#vf-con').value, tercero: $('#vf-ter').value, fecha: $('#vf-fec').value, importe: $('#vf-imp').value, nota: $('#vf-nota').value };
    try {
      if (v) await jpost(`/api/vencimientos/manual/${v.id.slice(1)}`, d);
      else { const cada = +$('#vf-cada').value; await jpost('/api/vencimientos/manual', { ...d, empresa: +$('#vf-emp').value, tipo: VE_T, cada: cada || 1, repetir: cada ? +$('#vf-n').value : 1 }); }
      f.hidden = true; veOpen();
    } catch (e) { alertBox(e.message); }
  };
  f.scrollIntoView({ behavior: 'smooth' });
}
$('#ve-add').onclick = () => veForm(null);
