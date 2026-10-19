"""Carga datos de ejemplo para probar la app (30 días de operación).

Uso:  python seed_demo.py
Solo úsalo en una base de datos vacía o de pruebas.
"""
import random
from datetime import timedelta

from app import app
from models import Activo, Gasto, Insumo, PrepRecetaItem, Producto, RecetaItem, Usuario, ahora, db
from services import (registrar_compra, registrar_preparacion, registrar_produccion,
                      registrar_venta)

random.seed(7)

with app.app_context():
    if Producto.query.first():
        raise SystemExit("La base ya tiene datos. El demo solo se carga en una base vacía.")

    admin = Usuario.query.filter_by(rol="admin").first()
    for nombre, user, rol in (("Marta (producción)", "marta", "produccion"),
                              ("Julián (ventas)", "julian", "vendedor")):
        if not Usuario.query.filter_by(username=user).first():
            u = Usuario(nombre=nombre, username=user, rol=rol)
            u.set_password("demo123")
            db.session.add(u)
    db.session.commit()
    marta = Usuario.query.filter_by(username="marta").first()
    julian = Usuario.query.filter_by(username="julian").first()

    # nombre, unidad, mínimo, cantidad comprada, costo total
    datos = [
        ("Bolsas para vikingo", "unidad", 300, 3000, 240000),
        ("Leche entera", "litro", 20, 200, 900000),
        ("Azúcar", "kilogramo", 5, 50, 210000),
        ("Pulpa de fresa", "kilogramo", 3, 25, 225000),
        ("Pulpa de mora", "kilogramo", 3, 20, 190000),
        ("Chocolate en polvo", "kilogramo", 1, 8, 192000),
        ("Coco rallado", "kilogramo", 1, 6, 108000),
        ("Arequipe", "kilogramo", 2, 12, 180000),
        ("Esencia de vainilla", "mililitro", 100, 1000, 24000),
    ]
    hace30 = ahora() - timedelta(days=30)
    ins = {}
    for nombre, unidad, minimo, cant, costo in datos:
        i = Insumo(nombre=nombre, unidad=unidad, stock_minimo=minimo)
        db.session.add(i)
        registrar_compra(i, cant, costo, "Distribuidora La Frontera", "Compra inicial",
                         hace30, admin.id)
        ins[nombre] = i

    recetas = {
        "Vikingo de fresa en leche": (1500, [("Bolsas para vikingo", 1), ("Leche entera", .08),
                                             ("Azúcar", .015), ("Pulpa de fresa", .03)]),
        "Vikingo de chocolate": (1500, [("Bolsas para vikingo", 1), ("Leche entera", .09),
                                        ("Azúcar", .015), ("Chocolate en polvo", .01)]),
        "Vikingo de coco": (1500, [("Bolsas para vikingo", 1), ("Leche entera", .08), ("Azúcar", .015),
                                   ("Coco rallado", .012), ("Esencia de vainilla", 1)]),
        "Vikingo de mora (en agua)": (1000, [("Bolsas para vikingo", 1), ("Pulpa de mora", .04),
                                             ("Azúcar", .02)]),
        "Vikingo de arequipe": (2000, [("Bolsas para vikingo", 1), ("Leche entera", .08),
                                       ("Arequipe", .025)]),
    }
    prods = {}
    for nombre, (precio, items) in recetas.items():
        p = Producto(nombre=nombre, precio_venta=precio, stock_minimo=30)
        for iname, c in items:
            p.receta.append(RecetaItem(insumo=ins[iname], cantidad=c))
        db.session.add(p)
        prods[nombre] = p
    db.session.commit()

    # Producción y ventas día a día
    peso = {"Vikingo de fresa en leche": 5, "Vikingo de chocolate": 4, "Vikingo de coco": 2,
            "Vikingo de mora (en agua)": 3, "Vikingo de arequipe": 2}
    for d in range(28, -1, -1):
        dia = (ahora() - timedelta(days=d)).replace(hour=8, minute=0, second=0)
        if d % 3 == 0:  # día de producción
            for nombre, p in prods.items():
                cant = random.choice([60, 80, 100, 120]) if peso[nombre] >= 3 else random.choice([40, 50, 60])
                factor = random.uniform(1.0, 1.05)  # algo de merma
                consumos = {r.insumo: round(r.cantidad * cant * factor, 4) for r in p.receta}
                consumos[ins["Bolsas para vikingo"]] = cant
                if any(c > i.stock for i, c in consumos.items()):
                    for i in consumos:
                        if consumos[i] > i.stock:
                            registrar_compra(i, consumos[i] * 3, consumos[i] * 3 * i.costo_promedio,
                                             "Distribuidora La Frontera", "Reposición", dia, marta.id)
                if random.random() < .6:
                    mo = dict(mo_tipo="unidad", mo_valor=100)
                else:
                    mo = dict(mo_tipo="jornada", mo_valor=random.choice([8000, 10000]))
                registrar_produccion(
                    p, cant, consumos, mo_responsable="Doña Rosa", energia=random.choice([2500, 3000, 3500]),
                    gas_agua=random.choice([1000, 1500]), transporte=random.choice([0, 0, 2000]),
                    otros=random.choice([0, 0, 1200]), otros_descripcion="Bolsa de hielo para transporte",
                    fecha=dia, usuario_id=marta.id, **mo)
            db.session.commit()
        for k in range(random.randint(3, 7)):
            hora = dia.replace(hour=random.randint(10, 19), minute=random.randint(0, 59))
            items = []
            for nombre in random.sample(list(prods), k=random.randint(1, 3)):
                p = prods[nombre]
                cant = random.randint(1, 4) * peso[nombre] + random.randint(0, 6)
                cant = min(cant, p.stock)
                if cant > 0:
                    items.append((p, cant, p.precio_venta))
            if items:
                registrar_venta(items, random.choice(["", "Tienda Don Pedro", "Colegio San José", "Cliente mostrador", ""]),
                                random.choice(["Efectivo", "Efectivo", "Nequi", "Daviplata"]), "", hora,
                                random.choice([julian.id, admin.id]))
        db.session.commit()
    # Reposición final para dejar la bodega con existencias sanas
    for i in Insumo.query.all():
        objetivo = i.stock_minimo * 4
        if i.stock < objetivo:
            registrar_compra(i, round(objetivo - i.stock, 2), round((objetivo - i.stock) * i.costo_promedio),
                             "Distribuidora La Frontera", "Reposición", ahora(), marta.id)
    db.session.commit()
    # Preparación propia: base de maíz (maizena + leche + azúcar), usada en el vikingo de maíz
    maizena = Insumo(nombre="Maizena", unidad="kilogramo", stock_minimo=1)
    db.session.add(maizena)
    registrar_compra(maizena, 5, 42500, "Distribuidora La Frontera", "Compra inicial",
                     ahora() - timedelta(days=6), admin.id)
    # Tanda estándar: 375 g de maizena + 1.500 ml de leche → 3 kg de base
    base = Insumo(nombre="Base de maíz", unidad="kilogramo", stock_minimo=0.5, es_preparacion=True,
                  tanda_rinde=3)
    base.receta_prep.append(PrepRecetaItem(componente=maizena, cantidad=0.375 / 3, unidad="gramo"))
    base.receta_prep.append(PrepRecetaItem(componente=ins["Leche entera"], cantidad=1.5 / 3,
                                           unidad="mililitro"))
    db.session.add(base)
    db.session.flush()
    registrar_preparacion(base, 3, {maizena: 0.375, ins["Leche entera"]: 1.5},
                          mano_obra=3000, otros=1500, otros_descripcion="Gas de la cocción",
                          fecha=ahora() - timedelta(days=5), usuario_id=marta.id)
    # Una tanda de 15 vikingos de maíz lleva 200 g de base y 15 bolsas
    maiz = Producto(nombre="Vikingo de maíz", precio_venta=1500, stock_minimo=20, tanda_rinde=15)
    maiz.receta.append(RecetaItem(insumo=ins["Bolsas para vikingo"], cantidad=1, unidad="unidad"))
    maiz.receta.append(RecetaItem(insumo=base, cantidad=0.2 / 15, unidad="gramo"))
    maiz.receta.append(RecetaItem(insumo=ins["Leche entera"], cantidad=0.06, unidad="mililitro"))
    db.session.add(maiz)
    db.session.flush()
    registrar_produccion(maiz, 45, {ins["Bolsas para vikingo"]: 45, base: 0.6, ins["Leche entera"]: 2.7},
                         mo_tipo="unidad", mo_valor=100, energia=2000,
                         fecha=ahora() - timedelta(days=4), usuario_id=marta.id)
    db.session.commit()

    hoy = ahora().date()
    for nombre, valor, meses, dias in (("Selladora de impulso 30 cm", 350000, 24, 40),
                                       ("Congelador horizontal 300 L", 1800000, 60, 200),
                                       ("Ollas y utensilios", 180000, 36, 90)):
        db.session.add(Activo(nombre=nombre, valor=valor, vida_util_meses=meses,
                              fecha_compra=hoy - timedelta(days=dias)))
    for dias, cat, desc, valor in ((25, "Publicidad", "Pauta Facebook e Instagram", 60000),
                                   (18, "Publicidad", "Volantes 500 unidades", 35000),
                                   (12, "Servicios públicos", "Recibo de luz (parte del negocio)", 45000),
                                   (5, "Publicidad", "Pauta Instagram semana", 40000),
                                   (3, "Papelería y empaques", "Bolsas para domicilio", 15000)):
        db.session.add(Gasto(fecha=hoy - timedelta(days=dias), categoria=cat, descripcion=desc, valor=valor))
    db.session.commit()
    print("Datos demo cargados. Usuarios: admin / marta / julian (clave demo: demo123 para marta y julian).")
