"""Lógica de negocio: compras, producción (transformación) y ventas.

Cada función valida, mueve inventario y calcula costos. Si algo no cuadra
lanza ErrorNegocio con la lista de mensajes para mostrar al usuario.
El commit lo hace quien llama.
"""
from models import (CompraInsumo, Preparacion, PreparacionConsumo, Produccion,
                    ProduccionConsumo, Venta,
                    VentaItem, ahora, db)

TOL = 1e-9


class ErrorNegocio(Exception):
    def __init__(self, errores):
        self.errores = errores if isinstance(errores, list) else [errores]
        super().__init__("; ".join(self.errores))


def promedio_ponderado(stock, costo_actual, cantidad, costo_unitario):
    """Costo promedio ponderado al recibir una entrada de inventario."""
    stock = stock or 0
    nuevo = stock + cantidad
    if stock <= 0 or nuevo <= 0:
        return costo_unitario
    return (stock * (costo_actual or 0) + cantidad * costo_unitario) / nuevo


def registrar_compra(insumo, cantidad, costo_total, proveedor="", nota="",
                     fecha=None, usuario_id=None):
    if cantidad <= 0:
        raise ErrorNegocio(f"La cantidad de «{insumo.nombre}» debe ser mayor a 0.")
    if costo_total < 0:
        raise ErrorNegocio(f"El costo de «{insumo.nombre}» no puede ser negativo.")
    costo_unitario = costo_total / cantidad
    insumo.costo_promedio = promedio_ponderado(
        insumo.stock, insumo.costo_promedio, cantidad, costo_unitario)
    insumo.stock = (insumo.stock or 0) + cantidad
    compra = CompraInsumo(
        fecha=fecha or ahora(), insumo=insumo, cantidad=cantidad,
        costo_total=costo_total, costo_unitario=costo_unitario,
        proveedor=proveedor or None, nota=nota or None, usuario_id=usuario_id,
    )
    db.session.add(compra)
    return compra


def calcular_mano_obra(tipo, valor, cantidad):
    if tipo == "unidad":
        return valor * cantidad
    if tipo == "jornada":
        return valor
    return 0.0


def registrar_produccion(producto, cantidad, consumos, mo_tipo="ninguna",
                         mo_valor=0.0, mo_responsable="", energia=0.0,
                         gas_agua=0.0, transporte=0.0, otros=0.0,
                         otros_descripcion="", nota="", fecha=None,
                         usuario_id=None):
    """consumos: dict {Insumo: cantidad_usada}."""
    errores = []
    if not producto:
        errores.append("Selecciona la referencia de vikingo que vas a producir.")
    if cantidad <= 0:
        errores.append("La cantidad de vikingos obtenidos debe ser mayor a 0.")
    consumos = {i: c for i, c in consumos.items() if c > 0}
    if not consumos:
        errores.append("Registra al menos un insumo utilizado.")
    for insumo, cant in consumos.items():
        if cant > (insumo.stock or 0) + TOL:
            errores.append(
                f"No hay suficiente «{insumo.nombre}»: necesitas "
                f"{cant:g} {insumo.unidad} y hay {insumo.stock:g}.")
    if mo_tipo not in ("ninguna", "unidad", "jornada"):
        mo_tipo = "ninguna"
    if mo_tipo != "ninguna" and mo_valor <= 0:
        errores.append("Indica el valor de la mano de obra o elige «Sin mano de obra».")
    for etiqueta, v in (("energía", energia), ("gas/agua", gas_agua),
                        ("transporte", transporte), ("otros", otros), ("mano de obra", mo_valor)):
        if v < 0:
            errores.append(f"El costo de {etiqueta} no puede ser negativo.")
    if otros > 0 and not (otros_descripcion or "").strip():
        errores.append("Aclara en qué consisten los «otros» costos.")
    if errores:
        raise ErrorNegocio(errores)

    p = Produccion(
        fecha=fecha or ahora(), producto=producto, cantidad=cantidad,
        mo_tipo=mo_tipo, mo_valor=mo_valor if mo_tipo != "ninguna" else 0,
        mo_responsable=(mo_responsable or "").strip() or None,
        costo_energia=energia, costo_gas_agua=gas_agua,
        costo_transporte=transporte, costo_otros=otros,
        otros_descripcion=(otros_descripcion or "").strip() or None,
        nota=nota or None, usuario_id=usuario_id,
    )
    costo_insumos = 0.0
    for insumo, cant in consumos.items():
        cu = insumo.costo_promedio or 0
        total = cant * cu
        costo_insumos += total
        insumo.stock = (insumo.stock or 0) - cant
        p.consumos.append(ProduccionConsumo(
            insumo=insumo, cantidad=cant, costo_unitario=cu, costo_total=total))

    p.costo_insumos = costo_insumos
    p.costo_mano_obra = calcular_mano_obra(p.mo_tipo, p.mo_valor, cantidad)
    p.costo_total = costo_insumos + p.costo_mano_obra + p.costo_indirectos
    p.costo_unitario = p.costo_total / cantidad

    producto.costo_promedio = promedio_ponderado(
        producto.stock, producto.costo_promedio, cantidad, p.costo_unitario)
    producto.stock = (producto.stock or 0) + cantidad
    db.session.add(p)
    return p


def registrar_venta(items, cliente="", metodo_pago="Efectivo", nota="",
                    fecha=None, usuario_id=None, cliente_id=None):
    """items: lista de (Producto, cantidad, precio_unitario[, bonificado])."""
    errores = []
    agrupado = {}
    por_producto = {}
    for item in items:
        producto, cant, precio = item[:3]
        bonif = bool(item[3]) if len(item) > 3 else False
        if not producto or cant <= 0:
            continue
        if bonif:
            precio = 0.0
        if precio < 0:
            errores.append(f"El precio de «{producto.nombre}» no puede ser negativo.")
        clave = (producto, bonif)
        if clave in agrupado:
            agrupado[clave][0] += cant
        else:
            agrupado[clave] = [cant, precio]
        por_producto[producto] = por_producto.get(producto, 0) + cant
    if not agrupado:
        errores.append("Agrega al menos un vikingo a la venta.")
    for producto, cant in por_producto.items():
        if cant > (producto.stock or 0):
            errores.append(
                f"Solo hay {producto.stock} unidades de «{producto.nombre}» "
                f"y quieres vender {cant}.")
    if errores:
        raise ErrorNegocio(errores)

    v = Venta(fecha=fecha or ahora(), cliente=(cliente or "").strip() or None, cliente_id=cliente_id,
              metodo_pago=metodo_pago, nota=nota or None, usuario_id=usuario_id)
    total = costo = 0.0
    for (producto, bonif), (cant, precio) in agrupado.items():
        cu = producto.costo_promedio or 0
        it = VentaItem(producto=producto, cantidad=cant, precio_unitario=precio, bonificado=bonif,
                       costo_unitario=cu, subtotal=cant * precio, costo=cant * cu)
        v.items.append(it)
        total += it.subtotal
        costo += it.costo
        producto.stock -= cant
    v.total, v.costo_total, v.ganancia = total, costo, total - costo
    db.session.add(v)
    return v


def anular_venta(venta):
    if venta.anulada:
        raise ErrorNegocio("Esta venta ya estaba anulada.")
    for it in venta.items:
        p = it.producto
        p.costo_promedio = promedio_ponderado(
            p.stock, p.costo_promedio, it.cantidad, it.costo_unitario)
        p.stock = (p.stock or 0) + it.cantidad
    venta.anulada = True


def _quitar_de_promedio(stock, costo_prom, cantidad, costo_unit):
    """Inverso del promedio ponderado: saca una entrada del costo promedio."""
    restante = (stock or 0) - cantidad
    if restante <= TOL:
        return costo_prom
    return max(((stock or 0) * (costo_prom or 0) - cantidad * costo_unit) / restante, 0)


def anular_compra(compra):
    if compra.anulada:
        raise ErrorNegocio("Esta compra ya estaba anulada.")
    ins = compra.insumo
    if (ins.stock or 0) + TOL < compra.cantidad:
        raise ErrorNegocio(
            f"No se puede anular: de «{ins.nombre}» quedan {ins.stock:g} {ins.unidad} y la compra fue de "
            f"{compra.cantidad:g}. Parte ya se usó en producción; anula primero esas producciones.")
    ins.costo_promedio = _quitar_de_promedio(ins.stock, ins.costo_promedio, compra.cantidad,
                                             compra.costo_unitario)
    ins.stock = max((ins.stock or 0) - compra.cantidad, 0)
    compra.anulada = True


def anular_produccion(prod):
    if prod.anulada:
        raise ErrorNegocio("Esta producción ya estaba anulada.")
    p = prod.producto
    if (p.stock or 0) < prod.cantidad:
        raise ErrorNegocio(
            f"No se puede anular: de «{p.nombre}» quedan {p.stock} vikingos y esta producción fue de "
            f"{prod.cantidad}. Algunos ya se vendieron; anula primero esas ventas.")
    p.costo_promedio = _quitar_de_promedio(p.stock, p.costo_promedio, prod.cantidad, prod.costo_unitario)
    p.stock = (p.stock or 0) - prod.cantidad
    for c in prod.consumos:
        ins = c.insumo
        ins.costo_promedio = promedio_ponderado(ins.stock, ins.costo_promedio, c.cantidad, c.costo_unitario)
        ins.stock = (ins.stock or 0) + c.cantidad
    prod.anulada = True


def registrar_preparacion(prep_insumo, cantidad, consumos, mano_obra=0.0, otros=0.0,
                          otros_descripcion="", nota="", fecha=None, usuario_id=None):
    """Elabora una preparación propia. consumos: dict {Insumo: cantidad_usada}."""
    errores = []
    if not prep_insumo or not prep_insumo.es_preparacion:
        errores.append("Selecciona la preparación que elaboraste.")
    if cantidad <= 0:
        errores.append("La cantidad obtenida debe ser mayor a 0.")
    consumos = {i: c for i, c in consumos.items() if c > 0}
    if not consumos:
        errores.append("Registra al menos un insumo utilizado.")
    for insumo, cant in consumos.items():
        if prep_insumo and insumo.id == prep_insumo.id:
            errores.append("Una preparación no puede usarse a sí misma como ingrediente.")
        elif cant > (insumo.stock or 0) + TOL:
            errores.append(f"No hay suficiente «{insumo.nombre}»: necesitas {cant:g} {insumo.unidad} "
                           f"y hay {insumo.stock:g}.")
    if mano_obra < 0 or otros < 0:
        errores.append("Los costos no pueden ser negativos.")
    if otros > 0 and not (otros_descripcion or "").strip():
        errores.append("Aclara en qué consisten los otros costos (gas, energía…).")
    if errores:
        raise ErrorNegocio(errores)

    p = Preparacion(fecha=fecha or ahora(), insumo=prep_insumo, cantidad=cantidad,
                    costo_mano_obra=mano_obra, costo_otros=otros,
                    otros_descripcion=(otros_descripcion or "").strip() or None,
                    nota=nota or None, usuario_id=usuario_id)
    total_ins = 0.0
    for insumo, cant in consumos.items():
        cu = insumo.costo_promedio or 0
        total_ins += cant * cu
        insumo.stock = (insumo.stock or 0) - cant
        p.consumos.append(PreparacionConsumo(insumo=insumo, cantidad=cant, costo_unitario=cu,
                                             costo_total=cant * cu))
    p.costo_insumos = total_ins
    p.costo_total = total_ins + mano_obra + otros
    p.costo_unitario = p.costo_total / cantidad
    prep_insumo.costo_promedio = promedio_ponderado(prep_insumo.stock, prep_insumo.costo_promedio,
                                                    cantidad, p.costo_unitario)
    prep_insumo.stock = (prep_insumo.stock or 0) + cantidad
    db.session.add(p)
    return p


def anular_preparacion(p):
    if p.anulada:
        raise ErrorNegocio("Esta preparación ya estaba anulada.")
    ins = p.insumo
    if (ins.stock or 0) + TOL < p.cantidad:
        raise ErrorNegocio(
            f"No se puede anular: de «{ins.nombre}» quedan {ins.stock:g} {ins.unidad} y esta preparación "
            f"fue de {p.cantidad:g}. Parte ya se usó en producción; anula primero esas producciones.")
    ins.costo_promedio = _quitar_de_promedio(ins.stock, ins.costo_promedio, p.cantidad, p.costo_unitario)
    ins.stock = max((ins.stock or 0) - p.cantidad, 0)
    for c in p.consumos:
        i = c.insumo
        i.costo_promedio = promedio_ponderado(i.stock, i.costo_promedio, c.cantidad, c.costo_unitario)
        i.stock = (i.stock or 0) + c.cantidad
    p.anulada = True
