"""Métricas de clientes: rotación, frecuencia, estado, meta mensual y bonificaciones."""
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import case, func

from models import Bonificacion, Cliente, Venta, VentaItem, db

ESTADOS = {
    "activo": ("Activo", "ok"),
    "riesgo": ("Se está quedando", "warn"),
    "inactivo": ("Inactivo", "err"),
    "nuevo": ("Sin compras", "gray"),
}


def _unidades_por_cliente(desde=None, hasta=None, bonificadas=False):
    q = (db.session.query(Venta.cliente_id, func.sum(VentaItem.cantidad), func.sum(VentaItem.subtotal))
         .join(VentaItem, VentaItem.venta_id == Venta.id)
         .filter(Venta.anulada.is_(False), Venta.cliente_id.isnot(None),
                 VentaItem.bonificado.is_(bonificadas)))
    if desde:
        q = q.filter(Venta.fecha >= desde)
    if hasta:
        q = q.filter(Venta.fecha < hasta)
    return {cid: (int(u or 0), v or 0) for cid, u, v in q.group_by(Venta.cliente_id).all()}


def metricas(clientes, hoy):
    """Devuelve dict cliente_id -> métricas. hoy: date."""
    ini_hoy = datetime.combine(hoy, datetime.min.time())
    manana = ini_hoy + timedelta(days=1)
    ult30 = _unidades_por_cliente(ini_hoy - timedelta(days=29), manana)
    prev30 = _unidades_por_cliente(ini_hoy - timedelta(days=59), ini_hoy - timedelta(days=29))
    mes = _unidades_por_cliente(ini_hoy.replace(day=1), manana)
    total = _unidades_por_cliente()
    regalados = _unidades_por_cliente(bonificadas=True)
    resumen = {cid: (n, first, last) for cid, n, first, last in (
        db.session.query(Venta.cliente_id, func.count(Venta.id), func.min(Venta.fecha), func.max(Venta.fecha))
        .filter(Venta.anulada.is_(False), Venta.cliente_id.isnot(None))
        .group_by(Venta.cliente_id).all())}
    otorgadas = {cid: int(c or 0) for cid, c in (
        db.session.query(Bonificacion.cliente_id, func.sum(Bonificacion.cantidad))
        .group_by(Bonificacion.cliente_id).all())}

    out = {}
    for c in clientes:
        n, first, last = resumen.get(c.id, (0, None, None))
        frec = ((last - first).days / (n - 1)) if n and n > 1 else None
        dias = (hoy - last.date()).days if last else None
        if not n:
            estado = "nuevo"
        else:
            base = frec or 7
            if dias <= max(15, 1.5 * base):
                estado = "activo"
            elif dias <= max(30, 3 * base):
                estado = "riesgo"
            else:
                estado = "inactivo"
        u30, v30 = ult30.get(c.id, (0, 0))
        up, _ = prev30.get(c.id, (0, 0))
        tendencia = ((u30 - up) / up * 100) if up else (100.0 if u30 else None)
        um, _ = mes.get(c.id, (0, 0))
        meta = c.meta_mensual or 0
        pend = otorgadas.get(c.id, 0) - regalados.get(c.id, (0, 0))[0]
        out[c.id] = dict(
            compras=n, primera=first, ultima=last, dias=dias, frecuencia=frec, estado=estado,
            estado_txt=ESTADOS[estado][0], estado_cls=ESTADOS[estado][1],
            u30=u30, v30=v30, tendencia=tendencia, mes=um, meta=meta,
            meta_pct=(um / meta * 100) if meta else None, meta_ok=bool(meta and um >= meta),
            total_u=total.get(c.id, (0, 0))[0], total_v=total.get(c.id, (0, 0))[1],
            bonif_pend=max(pend, 0), bonif_otorgadas=otorgadas.get(c.id, 0),
            bonif_entregadas=regalados.get(c.id, (0, 0))[0],
        )
    return out


def pendiente(cliente_id):
    otorg = db.session.query(func.coalesce(func.sum(Bonificacion.cantidad), 0)).filter(
        Bonificacion.cliente_id == cliente_id).scalar() or 0
    entreg = (db.session.query(func.coalesce(func.sum(VentaItem.cantidad), 0))
              .join(Venta, VentaItem.venta_id == Venta.id)
              .filter(Venta.cliente_id == cliente_id, Venta.anulada.is_(False),
                      VentaItem.bonificado.is_(True)).scalar() or 0)
    return max(int(otorg) - int(entreg), 0)


def por_mes(cliente_id, hoy, meses=6):
    """Unidades compradas por mes (últimos `meses`)."""
    ini = hoy.replace(day=1)
    for _ in range(meses - 1):
        ini = (ini - timedelta(days=1)).replace(day=1)
    datos = defaultdict(int)
    for f, u in (db.session.query(Venta.fecha, VentaItem.cantidad)
                 .join(VentaItem, VentaItem.venta_id == Venta.id)
                 .filter(Venta.cliente_id == cliente_id, Venta.anulada.is_(False),
                         VentaItem.bonificado.is_(False),
                         Venta.fecha >= datetime.combine(ini, datetime.min.time())).all()):
        datos[(f.year, f.month)] += u
    salida, k = [], ini
    while k <= hoy:
        salida.append(((k.year, k.month), datos[(k.year, k.month)]))
        k = (k.replace(day=28) + timedelta(days=4)).replace(day=1)
    return salida
