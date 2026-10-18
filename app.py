"""VikingoControl — insumos, producción, inventario y ventas de vikingos/bolis."""
import os
import secrets
from collections import defaultdict
from datetime import date, datetime, timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import (Flask, Response, abort, flash, redirect, render_template,
                   request, session, url_for)
from flask_login import (LoginManager, current_user, login_required,
                         login_user, logout_user)
from sqlalchemy import func

from models import (CATEGORIAS_GASTO, METODOS_PAGO, ROLES, TIPOS_MO, UNIDADES, Activo,
                    CompraInsumo, Gasto,
                    Configuracion, DatosNegocio,
                    Insumo, Produccion, Producto, RecetaItem, Usuario, Venta,
                    VentaItem, ahora, db)
import comprobantes
import reportes as rpt
from activos_calc import activos_vigentes, depreciacion_dia, depreciacion_periodo
from services import (ErrorNegocio, anular_compra, anular_produccion, anular_venta, registrar_compra,
                      registrar_produccion, registrar_venta)

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY") or secrets.token_hex(32)
db_url = (os.getenv("DATABASE_URL") or "sqlite:///vikingos.db").strip().strip('"').strip("'")
if "[YOUR-PASSWORD]" in db_url:
    raise SystemExit("ERROR DATABASE_URL: aún contiene [YOUR-PASSWORD]. Reemplázalo por la contraseña real de Supabase.")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True, "pool_recycle": 280}
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
NOMBRE_NEGOCIO = os.getenv("NOMBRE_NEGOCIO", "VikingoControl")

db.init_app(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Inicia sesión para continuar."
login_manager.login_message_category = "info"


@login_manager.user_loader
def cargar_usuario(uid):
    return db.session.get(Usuario, int(uid))


# ---------------------------------------------------------------- utilidades
def n(valor, defecto=0.0):
    """Convierte texto a número aceptando 1.500,5 / 1500.5 / 1500,5."""
    if valor is None:
        return defecto
    v = str(valor).strip().replace(" ", "").replace("$", "")
    if not v:
        return defecto
    if "," in v and "." in v:
        v = v.replace(".", "").replace(",", ".")
    elif "," in v:
        v = v.replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return defecto


def fecha_form(texto):
    """Fecha del formulario + hora actual (si es hoy) o mediodía."""
    try:
        d = datetime.strptime(texto, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return ahora()
    hoy = ahora()
    if d == hoy.date():
        return hoy
    return datetime.combine(d, datetime.min.time()).replace(hour=12)


def periodo():
    """Rango de fechas de los filtros (por defecto, el mes actual)."""
    hoy = ahora().date()
    try:
        desde = datetime.strptime(request.args.get("desde", ""), "%Y-%m-%d").date()
    except ValueError:
        desde = hoy.replace(day=1)
    try:
        hasta = datetime.strptime(request.args.get("hasta", ""), "%Y-%m-%d").date()
    except ValueError:
        hasta = hoy
    if hasta < desde:
        desde, hasta = hasta, desde
    ini = datetime.combine(desde, datetime.min.time())
    fin = datetime.combine(hasta + timedelta(days=1), datetime.min.time())
    return ini, fin, desde.isoformat(), hasta.isoformat()


def gastos_por_dia(desde, hasta):
    """dict date -> total de gastos, entre fechas date (inclusive)."""
    filas = (db.session.query(Gasto.fecha, func.sum(Gasto.valor))
             .filter(Gasto.fecha >= desde, Gasto.fecha <= hasta).group_by(Gasto.fecha).all())
    return {f: v or 0 for f, v in filas}


def rol_requerido(*roles):
    def deco(f):
        @wraps(f)
        @login_required
        def envoltura(*a, **kw):
            if current_user.rol not in roles:
                flash("No tienes permiso para entrar a esa sección.", "error")
                return redirect(url_for("inicio"))
            return f(*a, **kw)
        return envoltura
    return deco


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


@app.before_request
def verificar_csrf():
    if request.method == "POST":
        if request.form.get("csrf_token") != session.get("_csrf"):
            flash("La sesión expiró. Intenta de nuevo.", "error")
            return redirect(request.url)


@app.context_processor
def contexto():
    def puede(*roles):
        return current_user.is_authenticated and current_user.rol in roles
    nombre = NOMBRE_NEGOCIO
    try:
        neg = db.session.get(DatosNegocio, 1)
        if neg and neg.nombre and neg.nombre != "Mi negocio de vikingos":
            nombre = neg.nombre
    except Exception:
        db.session.rollback()
    return dict(csrf_token=csrf_token, puede=puede, ROLES=ROLES,
                NOMBRE_NEGOCIO=nombre, hoy=ahora().date().isoformat())


@app.template_filter("cop")
def filtro_cop(v):
    v = v or 0
    s = f"{abs(v):,.0f}".replace(",", ".")
    return ("-$ " if v < -0.5 else "$ ") + s


@app.template_filter("num")
def filtro_num(v, dec=2):
    v = v or 0
    s = f"{v:,.{dec}f}"
    entero, _, decimal = s.partition(".")
    entero = entero.replace(",", ".")
    decimal = decimal.rstrip("0")
    return f"{entero},{decimal}" if decimal else entero


@app.template_filter("fecha")
def filtro_fecha(v, hora=False):
    if not v:
        return ""
    return v.strftime("%d/%m/%Y %I:%M %p" if hora else "%d/%m/%Y")


def insumos_json():
    return [dict(id=i.id, nombre=i.nombre, unidad=i.unidad,
                 costo=i.costo_promedio or 0, stock=i.stock or 0)
            for i in Insumo.query.filter_by(activo=True).order_by(Insumo.nombre)]


# ------------------------------------------------------------------- sesión
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("inicio"))
    if request.method == "POST":
        u = Usuario.query.filter(
            func.lower(Usuario.username) == request.form.get("username", "").strip().lower()
        ).first()
        if u and u.activo and u.check_password(request.form.get("password", "")):
            login_user(u, remember=bool(request.form.get("recordar")))
            return redirect(request.args.get("next") or url_for("inicio"))
        flash("Usuario o contraseña incorrectos.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.route("/cuenta", methods=["GET", "POST"])
@login_required
def cuenta():
    if request.method == "POST":
        actual = request.form.get("actual", "")
        nueva = request.form.get("nueva", "")
        if not current_user.check_password(actual):
            flash("La contraseña actual no es correcta.", "error")
        elif len(nueva) < 6:
            flash("La nueva contraseña debe tener al menos 6 caracteres.", "error")
        elif nueva != request.form.get("confirmar", ""):
            flash("Las contraseñas no coinciden.", "error")
        else:
            current_user.set_password(nueva)
            db.session.commit()
            flash("Contraseña actualizada.", "ok")
            return redirect(url_for("inicio"))
    return render_template("cuenta.html")


# ---------------------------------------------------------------- resumen
@app.route("/")
@login_required
def inicio():
    if current_user.rol == "produccion":
        return redirect(url_for("producciones"))
    if current_user.rol == "vendedor":
        return redirect(url_for("venta_nueva"))

    ini, fin, desde, hasta = periodo()
    ventas = Venta.query.filter(Venta.anulada.is_(False), Venta.fecha >= ini,
                                Venta.fecha < fin).all()
    ingresos = sum(v.total for v in ventas)
    costo_vendido = sum(v.costo_total for v in ventas)
    ganancia = ingresos - costo_vendido
    unidades_vendidas = sum(v.unidades for v in ventas)

    prods = Produccion.query.filter(Produccion.anulada.is_(False), Produccion.fecha >= ini,
                                    Produccion.fecha < fin).all()
    unidades_prod = sum(p.cantidad for p in prods)
    desglose = {
        "Insumos": sum(p.costo_insumos for p in prods),
        "Mano de obra": sum(p.costo_mano_obra for p in prods),
        "Energía / congelación": sum(p.costo_energia for p in prods),
        "Gas / agua": sum(p.costo_gas_agua for p in prods),
        "Transporte / domicilios": sum(p.costo_transporte for p in prods),
        "Otros": sum(p.costo_otros for p in prods),
    }
    costo_prod = sum(desglose.values())
    compras = db.session.query(func.coalesce(func.sum(CompraInsumo.costo_total), 0)).filter(
        CompraInsumo.anulada.is_(False), CompraInsumo.fecha >= ini, CompraInsumo.fecha < fin).scalar()
    compras_equipos = db.session.query(func.coalesce(func.sum(Activo.valor), 0)).filter(
        Activo.eliminado.is_(False), Activo.fecha_compra >= ini.date(),
        Activo.fecha_compra < fin.date()).scalar() or 0

    ranking = (db.session.query(
        Producto.id, Producto.nombre,
        func.sum(VentaItem.cantidad), func.sum(VentaItem.subtotal),
        func.sum(VentaItem.subtotal - VentaItem.costo))
        .select_from(VentaItem)
        .join(Producto, VentaItem.producto_id == Producto.id)
        .join(Venta, VentaItem.venta_id == Venta.id)
        .filter(Venta.anulada.is_(False), Venta.fecha >= ini, Venta.fecha < fin)
        .group_by(Producto.id, Producto.nombre)
        .order_by(func.sum(VentaItem.cantidad).desc()).all())
    ranking = [dict(id=r[0], nombre=r[1], unidades=int(r[2] or 0), ingresos=r[3] or 0,
                    ganancia=r[4] or 0) for r in ranking]

    por_dia = defaultdict(lambda: [0.0, 0.0])
    for v in ventas:
        k = v.fecha.date().isoformat()
        por_dia[k][0] += v.total
        por_dia[k][1] += v.ganancia
    dias, d = [], ini.date()
    while d < fin.date():
        dias.append(d)
        d += timedelta(days=1)
    serie = dict(
        labels=[x.strftime("%d/%m") for x in dias],
        ventas=[round(por_dia[x.isoformat()][0]) for x in dias],
        ganancia=[round(por_dia[x.isoformat()][1]) for x in dias],
    )

    hoy_d = ahora().date()
    hasta_dep = min((fin - timedelta(days=1)).date(), hoy_d)
    depreciacion = depreciacion_periodo(ini.date(), hasta_dep) if hasta_dep >= ini.date() else 0
    gastos_det = (db.session.query(Gasto.categoria, func.sum(Gasto.valor))
                  .filter(Gasto.fecha >= ini.date(), Gasto.fecha < fin.date())
                  .group_by(Gasto.categoria).order_by(func.sum(Gasto.valor).desc()).all())
    gastos_tot = sum(v or 0 for _, v in gastos_det)
    ganancia_neta = ganancia - depreciacion - gastos_tot

    insumos = Insumo.query.filter_by(activo=True).all()
    productos = Producto.query.filter_by(activo=True).order_by(Producto.nombre).all()
    return render_template(
        "dashboard.html", desde=desde, hasta=hasta, ingresos=ingresos,
        costo_vendido=costo_vendido, ganancia=ganancia,
        depreciacion=depreciacion, ganancia_neta=ganancia_neta,
        gastos_det=gastos_det, gastos_tot=gastos_tot,
        margen=(ganancia / ingresos * 100) if ingresos else 0,
        unidades_vendidas=unidades_vendidas, num_ventas=len(ventas),
        unidades_prod=unidades_prod, costo_prod=costo_prod, desglose=desglose,
        costo_unit_prod=(costo_prod / unidades_prod) if unidades_prod else 0,
        compras=compras or 0, compras_equipos=compras_equipos, ranking=ranking, serie=serie,
        valor_insumos=sum(i.valor_stock for i in insumos),
        valor_productos=sum(max(p.stock, 0) * p.costo_promedio for p in productos),
        alertas_insumos=[i for i in insumos if i.bajo_minimo],
        alertas_productos=[p for p in productos if p.bajo_minimo],
        productos=productos,
    )


# ------------------------------------------------------------ distribución
@app.route("/distribucion")
@rol_requerido("admin")
def distribucion():
    ini, fin, desde, hasta = periodo()
    cfg = Configuracion.actual()
    ventas = Venta.query.filter(Venta.anulada.is_(False), Venta.fecha >= ini,
                                Venta.fecha < fin).all()
    por_dia = defaultdict(lambda: {"ventas": 0.0, "costo": 0.0, "num": 0})
    for v in ventas:
        d = por_dia[v.fecha.date()]
        d["ventas"] += v.total
        d["costo"] += v.costo_total
        d["num"] += 1
    pct = dict(reinversion=cfg.pct_reinversion, dueno=cfg.pct_dueno,
               emergencia=cfg.pct_emergencia)
    activos = activos_vigentes()

    gastos_dia = gastos_por_dia(ini.date(), (fin - timedelta(days=1)).date())

    def repartir(ventas_, costo_, equipos_, gastos_=0.0):
        ganancia = ventas_ - costo_
        fijos = equipos_ + gastos_
        if cfg.base == "ventas":
            base = ventas_
        else:
            # Primero se repone el costo y se reserva lo de los equipos; se reparte el resto.
            base = max(ganancia - fijos, 0)
        r = {k: base * p / 100 for k, p in pct.items()}
        r.update(ventas=ventas_, costo=costo_, ganancia=ganancia, base=base, equipos=equipos_,
                 gastos=gastos_, neta=ganancia - fijos)
        r["reponer"] = costo_ if cfg.base == "ganancia" else 0
        r["faltante"] = (max(costo_ + fijos - r["reinversion"], 0)
                         if cfg.base == "ventas" else max(fijos - max(ganancia, 0), 0))
        return r

    hoy_d = ahora().date()
    filas = []
    dia = min((fin - timedelta(days=1)).date(), hoy_d)
    while dia >= ini.date():
        d = por_dia.get(dia, {"ventas": 0.0, "costo": 0.0, "num": 0})
        eq = depreciacion_dia(dia, activos)
        gd = gastos_dia.get(dia, 0)
        if d["num"] or eq > 0.005 or gd:
            f = repartir(d["ventas"], d["costo"], eq, gd)
            f.update(fecha=dia, num=d["num"])
            filas.append(f)
        dia -= timedelta(days=1)
    tot = repartir(sum(f["ventas"] for f in filas), sum(f["costo"] for f in filas),
                   sum(f["equipos"] for f in filas), sum(f["gastos"] for f in filas))
    return render_template("distribucion.html", cfg=cfg, filas=filas, tot=tot,
                           desde=desde, hasta=hasta)


@app.route("/configuracion", methods=["GET", "POST"])
@rol_requerido("admin")
def configuracion():
    cfg = Configuracion.actual()
    if request.method == "POST":
        r, d, e = (n(request.form.get(k)) for k in ("pct_reinversion", "pct_dueno", "pct_emergencia"))
        base = request.form.get("base", "ventas")
        if min(r, d, e) < 0:
            flash("Los porcentajes no pueden ser negativos.", "error")
        elif abs(r + d + e - 100) > 0.01:
            flash(f"Los porcentajes deben sumar 100%. Ahora suman {r + d + e:g}%.", "error")
        elif base not in ("ventas", "ganancia"):
            flash("Base de cálculo no válida.", "error")
        else:
            cfg.pct_reinversion, cfg.pct_dueno, cfg.pct_emergencia, cfg.base = r, d, e, base
            db.session.commit()
            flash("Regla de distribución guardada.", "ok")
            return redirect(url_for("distribucion"))
    return render_template("configuracion.html", cfg=cfg)


# ------------------------------------------------------------------ activos
@app.route("/activos")
@rol_requerido("admin")
def activos():
    hoy_d = ahora().date()
    items = Activo.query.filter_by(eliminado=False).order_by(Activo.fecha_baja.isnot(None),
                                                               Activo.fecha_compra.desc()).all()
    filas = []
    for a in items:
        acum = a.acumulada(hoy_d)
        meses_rest = 0
        if a.estado(hoy_d) == "vigente":
            meses_rest = max((a.fin_vida - hoy_d).days, 0) / (365 / 12)
        filas.append(dict(a=a, acum=acum, pendiente=a.depreciable - acum,
                          pct=(acum / a.depreciable * 100) if a.depreciable else 100,
                          estado=a.estado(hoy_d), meses_rest=meses_rest))
    vig = [f for f in filas if f["estado"] == "vigente"]
    inicio_mes = hoy_d.replace(day=1)
    return render_template(
        "activos.html", filas=filas, hoy=hoy_d.isoformat(),
        total_valor=sum(f["a"].valor for f in filas if f["estado"] != "baja"),
        total_pendiente=sum(f["pendiente"] for f in vig),
        cuota_mes=sum(f["a"].cuota_mensual for f in vig),
        dep_mes=depreciacion_periodo(inicio_mes, hoy_d, [f["a"] for f in filas]))


def _fecha(txt, defecto=None):
    try:
        return datetime.strptime(txt or "", "%Y-%m-%d").date()
    except ValueError:
        return defecto


@app.route("/activos/nuevo", methods=["GET", "POST"])
@app.route("/activos/<int:id>/editar", methods=["GET", "POST"])
@rol_requerido("admin")
def activo_form(id=None):
    a = db.session.get(Activo, id) if id else None
    if id and (not a or a.eliminado):
        abort(404)
    if request.method == "POST":
        f = request.form
        nombre = f.get("nombre", "").strip()
        valor, residual = n(f.get("valor")), n(f.get("valor_residual"))
        vida = int(n(f.get("vida_util_meses")))
        fecha = _fecha(f.get("fecha_compra"))
        errores = []
        if not nombre:
            errores.append("Escribe el nombre del equipo.")
        if not fecha:
            errores.append("Indica la fecha de compra.")
        elif fecha > ahora().date():
            errores.append("La fecha de compra no puede ser futura.")
        if valor <= 0:
            errores.append("El valor de compra debe ser mayor a 0.")
        if residual < 0 or residual >= valor > 0:
            errores.append("El valor residual debe ser menor que el valor de compra.")
        if vida < 1:
            errores.append("La vida útil debe ser de al menos 1 mes.")
        if errores:
            for m in errores:
                flash(m, "error")
        else:
            if not a:
                a = Activo()
                db.session.add(a)
            a.nombre, a.fecha_compra, a.valor = nombre[:120], fecha, valor
            a.valor_residual, a.vida_util_meses = residual, vida
            a.descripcion = f.get("descripcion", "").strip()[:255] or None
            a.proveedor = f.get("proveedor", "").strip()[:120] or None
            db.session.commit()
            flash(f"Equipo «{nombre}» guardado. Ya aparece en Compras y en Equipos.", "ok")
            return redirect(url_for("compras") if f.get("origen") == "compras" else url_for("activos"))
    return render_template("activo_form.html", a=a, origen=request.values.get("origen", ""))


@app.route("/activos/<int:id>/baja", methods=["POST"])
@rol_requerido("admin")
def activo_baja(id):
    a = db.session.get(Activo, id) or abort(404)
    if request.form.get("deshacer"):
        a.fecha_baja, a.motivo_baja = None, None
        flash(f"«{a.nombre}» vuelve a estar en uso.", "ok")
    else:
        a.fecha_baja = _fecha(request.form.get("fecha_baja"), ahora().date())
        a.motivo_baja = request.form.get("motivo", "").strip()[:255] or None
        flash(f"«{a.nombre}» dado de baja. Deja de depreciarse desde esa fecha.", "ok")
    db.session.commit()
    return redirect(url_for("activos"))


@app.route("/activos/<int:id>/eliminar", methods=["POST"])
@rol_requerido("admin")
def activo_eliminar(id):
    a = db.session.get(Activo, id) or abort(404)
    a.eliminado = True
    db.session.commit()
    flash(f"«{a.nombre}» eliminado (registrado por error).", "ok")
    return redirect(url_for("activos"))


# ----------------------------------------------------------------- reportes
def _params_reporte():
    agrupar = request.args.get("agrupar", "semanal")
    if agrupar not in rpt.AGRUPACIONES:
        agrupar = "semanal"
    hoy_d = ahora().date()
    d0, h0 = rpt.rango_por_defecto(agrupar, hoy_d)
    desde = _fecha(request.args.get("desde"), d0)
    hasta = _fecha(request.args.get("hasta"), h0)
    if hasta < desde:
        desde, hasta = hasta, desde
    if (hasta - desde).days > 3 * 366:  # límite de seguridad: 3 años
        desde = hasta - timedelta(days=3 * 366)
    return agrupar, desde, hasta


@app.route("/reportes")
@rol_requerido("admin")
def reportes():
    agrupar, desde, hasta = _params_reporte()
    rep = rpt.generar(desde, hasta, agrupar)
    serie = dict(labels=[f["periodo"] for f in rep["filas"]],
                 ventas=[round(f["ventas"]) for f in rep["filas"]],
                 compras=[round(f["compras"] + f["compras_eq"]) for f in rep["filas"]],
                 neta=[round(f["neta"]) for f in rep["filas"]])
    return render_template("reportes.html", rep=rep, serie=serie, agrupar=agrupar,
                           agrupaciones=rpt.AGRUPACIONES, columnas=rpt.COLUMNAS,
                           desde=desde.isoformat(), hasta=hasta.isoformat())


@app.route("/reportes/excel")
@rol_requerido("admin")
def reportes_excel():
    agrupar, desde, hasta = _params_reporte()
    rep = rpt.generar(desde, hasta, agrupar)
    data = rpt.excel(rep, desde, hasta, agrupar, DatosNegocio.actual().nombre)
    nombre = f"reporte_{agrupar}_{desde.isoformat()}_a_{hasta.isoformat()}.xlsx"
    return Response(data, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


# ------------------------------------------------------------------- gastos
@app.route("/gastos")
@rol_requerido("admin")
def gastos():
    ini, fin, desde, hasta = periodo()
    cat = request.args.get("categoria", "")
    q = Gasto.query.filter(Gasto.fecha >= ini.date(), Gasto.fecha < fin.date())
    if cat:
        q = q.filter(Gasto.categoria == cat)
    items = q.order_by(Gasto.fecha.desc(), Gasto.id.desc()).all()
    por_cat = defaultdict(float)
    for g in items:
        por_cat[g.categoria] += g.valor
    por_cat = sorted(por_cat.items(), key=lambda x: -x[1])
    return render_template("gastos.html", items=items, desde=desde, hasta=hasta, cat=cat,
                           categorias=CATEGORIAS_GASTO, por_cat=por_cat,
                           total=sum(g.valor for g in items))


@app.route("/gastos/nuevo", methods=["GET", "POST"])
@app.route("/gastos/<int:id>/editar", methods=["GET", "POST"])
@rol_requerido("admin")
def gasto_form(id=None):
    g = db.session.get(Gasto, id) if id else None
    if id and not g:
        abort(404)
    if request.method == "POST":
        f = request.form
        valor = n(f.get("valor"))
        fecha = _fecha(f.get("fecha"))
        cat = f.get("categoria", "Otros")
        desc = f.get("descripcion", "").strip()
        if valor <= 0:
            flash("El valor debe ser mayor a 0.", "error")
        elif not fecha:
            flash("Indica la fecha del gasto.", "error")
        elif cat not in CATEGORIAS_GASTO:
            flash("Categoría no válida.", "error")
        elif cat == "Otros" and not desc:
            flash("Describe en qué consiste el gasto «Otros».", "error")
        else:
            if not g:
                g = Gasto(usuario_id=current_user.id)
                db.session.add(g)
            g.fecha, g.categoria, g.valor, g.descripcion = fecha, cat, valor, desc[:255] or None
            db.session.commit()
            flash(f"Gasto de {filtro_cop(valor)} en {cat} guardado.", "ok")
            return redirect(url_for("gastos"))
    return render_template("gasto_form.html", g=g, categorias=CATEGORIAS_GASTO)


@app.route("/gastos/<int:id>/eliminar", methods=["POST"])
@rol_requerido("admin")
def gasto_eliminar(id):
    g = db.session.get(Gasto, id) or abort(404)
    db.session.delete(g)
    db.session.commit()
    flash("Gasto eliminado.", "ok")
    return redirect(url_for("gastos"))


# ---------------------------------------------------------------- insumos
@app.route("/insumos")
@rol_requerido("admin", "produccion")
def insumos():
    q = request.args.get("q", "").strip()
    query = Insumo.query.filter_by(activo=True)
    if q:
        query = query.filter(Insumo.nombre.ilike(f"%{q}%"))
    items = query.order_by(Insumo.nombre).all()
    return render_template("insumos.html", items=items, q=q,
                           total=sum(i.valor_stock for i in items))


@app.route("/insumos/nuevo", methods=["GET", "POST"])
@app.route("/insumos/<int:id>/editar", methods=["GET", "POST"])
@rol_requerido("admin", "produccion")
def insumo_form(id=None):
    ins = db.session.get(Insumo, id) if id else None
    if id and not ins:
        abort(404)
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        unidad = request.form.get("unidad", "unidad")
        minimo = n(request.form.get("stock_minimo"))
        dup = Insumo.query.filter(func.lower(Insumo.nombre) == nombre.lower(),
                                  Insumo.activo.is_(True), Insumo.id != (id or 0)).first()
        if not nombre:
            flash("Escribe el nombre del insumo.", "error")
        elif dup:
            flash("Ya existe un insumo activo con ese nombre.", "error")
        else:
            try:
                if not ins:
                    ins = Insumo(nombre=nombre, unidad=unidad, stock_minimo=minimo)
                    db.session.add(ins)
                    cant = n(request.form.get("cantidad_inicial"))
                    if cant > 0:
                        registrar_compra(ins, cant, n(request.form.get("costo_inicial")),
                                         nota="Inventario inicial", usuario_id=current_user.id)
                else:
                    ins.nombre, ins.unidad, ins.stock_minimo = nombre, unidad, minimo
                db.session.commit()
                flash(f"Insumo «{nombre}» guardado.", "ok")
                return redirect(url_for("insumos"))
            except ErrorNegocio as e:
                db.session.rollback()
                for m in e.errores:
                    flash(m, "error")
    return render_template("insumo_form.html", ins=ins, unidades=UNIDADES)


@app.route("/insumos/<int:id>/eliminar", methods=["POST"])
@rol_requerido("admin")
def insumo_eliminar(id):
    ins = db.session.get(Insumo, id) or abort(404)
    if RecetaItem.query.filter_by(insumo_id=id).join(Producto).filter(Producto.activo.is_(True)).count():
        flash("Ese insumo está en la receta de un vikingo activo. Quítalo de la receta primero.", "error")
    else:
        ins.activo = False
        db.session.commit()
        flash(f"Insumo «{ins.nombre}» eliminado (su historial se conserva).", "ok")
    return redirect(url_for("insumos"))


# ---------------------------------------------------------------- compras
@app.route("/compras")
@rol_requerido("admin", "produccion")
def compras():
    ini, fin, desde, hasta = periodo()
    tipo = request.args.get("tipo", "")
    filas = []
    if tipo in ("", "insumos"):
        for c in (CompraInsumo.query.filter(CompraInsumo.fecha >= ini, CompraInsumo.fecha < fin)
                  .order_by(CompraInsumo.fecha.desc()).all()):
            filas.append(dict(tipo="insumo", fecha=c.fecha, orden=c.fecha, nombre=c.insumo.nombre,
                              cantidad=f"{filtro_num(c.cantidad, 3)} {c.insumo.unidad}", unitario=c.costo_unitario,
                              total=c.costo_total, proveedor=c.proveedor, nota=c.nota,
                              anulada=c.anulada, obj=c))
    if tipo in ("", "equipos") and current_user.rol == "admin":
        for a in (Activo.query.filter(Activo.eliminado.is_(False), Activo.fecha_compra >= ini.date(),
                                      Activo.fecha_compra < fin.date()).all()):
            filas.append(dict(tipo="equipo", fecha=a.fecha_compra,
                              orden=datetime.combine(a.fecha_compra, datetime.min.time()),
                              nombre=a.nombre, cantidad="1 equipo", unitario=a.valor, total=a.valor,
                              proveedor=a.proveedor, nota=a.descripcion, anulada=False, obj=a))
    filas.sort(key=lambda x: x["orden"], reverse=True)
    t_ins = sum(f["total"] for f in filas if f["tipo"] == "insumo" and not f["anulada"])
    t_eq = sum(f["total"] for f in filas if f["tipo"] == "equipo")
    return render_template("compras.html", filas=filas, desde=desde, hasta=hasta, tipo=tipo,
                           total=t_ins + t_eq, total_insumos=t_ins, total_equipos=t_eq)


@app.route("/compras/nueva", methods=["GET", "POST"])
@rol_requerido("admin", "produccion")
def compra_nueva():
    if request.method == "POST":
        ids = request.form.getlist("insumo_id[]")
        cants = request.form.getlist("cantidad[]")
        costos = request.form.getlist("costo[]")
        fecha = fecha_form(request.form.get("fecha"))
        proveedor = request.form.get("proveedor", "").strip()
        nota = request.form.get("nota", "").strip()
        registradas = 0
        try:
            for iid, c, ct in zip(ids, cants, costos):
                if not iid:
                    continue
                ins = db.session.get(Insumo, int(iid))
                if not ins:
                    continue
                registrar_compra(ins, n(c), n(ct), proveedor, nota, fecha, current_user.id)
                registradas += 1
            if not registradas:
                raise ErrorNegocio("Agrega al menos un insumo a la compra.")
            db.session.commit()
            flash(f"Compra registrada ({registradas} insumo(s)). El inventario y el costo promedio se actualizaron.", "ok")
            return redirect(url_for("compras"))
        except ErrorNegocio as e:
            db.session.rollback()
            for m in e.errores:
                flash(m, "error")
    return render_template("compra_form.html", insumos=insumos_json())


# --------------------------------------------------------------- productos
@app.route("/productos")
@rol_requerido("admin", "produccion")
def productos():
    items = Producto.query.filter_by(activo=True).order_by(Producto.nombre).all()
    return render_template("productos.html", items=items)


@app.route("/productos/nuevo", methods=["GET", "POST"])
@app.route("/productos/<int:id>/editar", methods=["GET", "POST"])
@rol_requerido("admin")
def producto_form(id=None):
    prod = db.session.get(Producto, id) if id else None
    if id and not prod:
        abort(404)
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        dup = Producto.query.filter(func.lower(Producto.nombre) == nombre.lower(),
                                    Producto.activo.is_(True), Producto.id != (id or 0)).first()
        receta = {}
        for iid, c in zip(request.form.getlist("receta_insumo[]"),
                          request.form.getlist("receta_cantidad[]")):
            if iid and n(c) > 0:
                receta[int(iid)] = receta.get(int(iid), 0) + n(c)
        if not nombre:
            flash("Escribe el nombre de la referencia.", "error")
        elif dup:
            flash("Ya existe una referencia activa con ese nombre.", "error")
        else:
            if not prod:
                prod = Producto()
                db.session.add(prod)
            prod.nombre = nombre
            prod.descripcion = request.form.get("descripcion", "").strip() or None
            prod.precio_venta = n(request.form.get("precio_venta"))
            prod.stock_minimo = int(n(request.form.get("stock_minimo")))
            prod.receta.clear()
            db.session.flush()
            for iid, c in receta.items():
                ins = db.session.get(Insumo, iid)
                if ins:
                    prod.receta.append(RecetaItem(insumo=ins, cantidad=c))
            db.session.commit()
            flash(f"Referencia «{nombre}» guardada.", "ok")
            return redirect(url_for("producto_detalle", id=prod.id))
    receta = [dict(insumo_id=r.insumo_id, cantidad=r.cantidad) for r in prod.receta] if prod else []
    return render_template("producto_form.html", prod=prod, insumos=insumos_json(), receta=receta)


@app.route("/productos/<int:id>")
@rol_requerido("admin", "produccion")
def producto_detalle(id):
    prod = db.session.get(Producto, id) or abort(404)
    prods = (Produccion.query.filter_by(producto_id=id)
             .order_by(Produccion.fecha.desc()).limit(10).all())
    vendidos = (db.session.query(func.coalesce(func.sum(VentaItem.cantidad), 0),
                                 func.coalesce(func.sum(VentaItem.subtotal - VentaItem.costo), 0))
                .select_from(VentaItem).join(Venta, VentaItem.venta_id == Venta.id)
                .filter(VentaItem.producto_id == id, Venta.anulada.is_(False)).first())
    return render_template("producto_detalle.html", prod=prod, prods=prods,
                           vendidos=int(vendidos[0]), ganancia_hist=vendidos[1])


@app.route("/productos/<int:id>/eliminar", methods=["POST"])
@rol_requerido("admin")
def producto_eliminar(id):
    prod = db.session.get(Producto, id) or abort(404)
    prod.activo = False
    db.session.commit()
    flash(f"Referencia «{prod.nombre}» eliminada (su historial se conserva).", "ok")
    return redirect(url_for("productos"))


# --------------------------------------------------------------- producción
@app.route("/produccion")
@rol_requerido("admin", "produccion")
def producciones():
    ini, fin, desde, hasta = periodo()
    items = (Produccion.query.filter(Produccion.fecha >= ini, Produccion.fecha < fin)
             .order_by(Produccion.fecha.desc()).all())
    validas = [p for p in items if not p.anulada]
    unidades = sum(p.cantidad for p in validas)
    costo = sum(p.costo_total for p in validas)
    return render_template("producciones.html", items=items, desde=desde, hasta=hasta,
                           unidades=unidades, costo=costo)


@app.route("/produccion/nueva", methods=["GET", "POST"])
@rol_requerido("admin", "produccion")
def produccion_nueva():
    prefill = None
    if request.method == "POST":
        f = request.form
        prod = db.session.get(Producto, int(f.get("producto_id") or 0))
        consumos = {}
        filas = []
        for iid, c in zip(f.getlist("consumo_insumo[]"), f.getlist("consumo_cantidad[]")):
            if not iid:
                continue
            ins = db.session.get(Insumo, int(iid))
            if ins:
                consumos[ins] = consumos.get(ins, 0) + n(c)
                filas.append(dict(insumo_id=ins.id, cantidad=n(c)))
        try:
            p = registrar_produccion(
                prod, int(n(f.get("cantidad"))), consumos,
                mo_tipo=f.get("mo_tipo", "ninguna"), mo_valor=n(f.get("mo_valor")),
                mo_responsable=f.get("mo_responsable", ""),
                energia=n(f.get("costo_energia")), gas_agua=n(f.get("costo_gas_agua")),
                transporte=n(f.get("costo_transporte")), otros=n(f.get("costo_otros")),
                otros_descripcion=f.get("otros_descripcion", ""), nota=f.get("nota", "").strip(),
                fecha=fecha_form(f.get("fecha")), usuario_id=current_user.id)
            db.session.commit()
            flash(f"Producción registrada: {p.cantidad} vikingos de «{p.producto.nombre}» "
                  f"a {filtro_cop(p.costo_unitario)} c/u.", "ok")
            return redirect(url_for("produccion_detalle", id=p.id))
        except ErrorNegocio as e:
            db.session.rollback()
            for m in e.errores:
                flash(m, "error")
            prefill = {k: f.get(k, "") for k in (
                "producto_id", "cantidad", "fecha", "mo_tipo", "mo_valor", "mo_responsable",
                "costo_energia", "costo_gas_agua", "costo_transporte", "costo_otros",
                "otros_descripcion", "nota")}
            prefill["consumos"] = filas
    productos_data = [dict(id=p.id, nombre=p.nombre, precio=p.precio_venta,
                           receta=[dict(insumo_id=r.insumo_id, cantidad=r.cantidad) for r in p.receta])
                      for p in Producto.query.filter_by(activo=True).order_by(Producto.nombre)]
    return render_template("produccion_form.html", productos=productos_data,
                           insumos=insumos_json(), prefill=prefill,
                           preseleccion=request.args.get("producto", type=int), tipos_mo=TIPOS_MO)


@app.route("/produccion/<int:id>")
@rol_requerido("admin", "produccion")
def produccion_detalle(id):
    p = db.session.get(Produccion, id) or abort(404)
    usados = {c.insumo_id: c.cantidad for c in p.consumos}
    teorico, limitante = None, None
    for r in p.producto.receta:
        if r.cantidad > 0:
            posible = usados.get(r.insumo_id, 0) / r.cantidad
            if teorico is None or posible < teorico:
                teorico, limitante = posible, r.insumo
    return render_template("produccion_detalle.html", p=p, teorico=teorico,
                           limitante=limitante, tipos_mo=TIPOS_MO)


# ------------------------------------------------------------------ ventas
@app.route("/ventas")
@rol_requerido("admin", "vendedor")
def ventas():
    ini, fin, desde, hasta = periodo()
    q = Venta.query.filter(Venta.fecha >= ini, Venta.fecha < fin)
    if current_user.rol == "vendedor":
        q = q.filter(Venta.usuario_id == current_user.id)
    items = q.order_by(Venta.fecha.desc()).all()
    validas = [v for v in items if not v.anulada]
    return render_template("ventas.html", items=items, desde=desde, hasta=hasta,
                           total=sum(v.total for v in validas),
                           ganancia=sum(v.ganancia for v in validas),
                           unidades=sum(v.unidades for v in validas))


@app.route("/ventas/nueva", methods=["GET", "POST"])
@rol_requerido("admin", "vendedor")
def venta_nueva():
    if request.method == "POST":
        f = request.form
        items = []
        for pid, c, pr in zip(f.getlist("producto_id[]"), f.getlist("cantidad[]"),
                              f.getlist("precio[]")):
            if pid:
                prod = db.session.get(Producto, int(pid))
                if prod:
                    items.append((prod, int(n(c)), n(pr)))
        try:
            v = registrar_venta(items, f.get("cliente", ""), f.get("metodo_pago", "Efectivo"),
                                f.get("nota", "").strip(), fecha_form(f.get("fecha")),
                                current_user.id)
            db.session.commit()
            flash(f"Venta #{v.id} registrada por {filtro_cop(v.total)}.", "ok")
            return redirect(url_for("venta_detalle", id=v.id))
        except ErrorNegocio as e:
            db.session.rollback()
            for m in e.errores:
                flash(m, "error")
    productos_data = [dict(id=p.id, nombre=p.nombre, precio=p.precio_venta, stock=p.stock)
                      for p in Producto.query.filter_by(activo=True).order_by(Producto.nombre)]
    return render_template("venta_form.html", productos=productos_data, metodos=METODOS_PAGO)


@app.route("/ventas/<int:id>")
@rol_requerido("admin", "vendedor")
def venta_detalle(id):
    v = db.session.get(Venta, id) or abort(404)
    if current_user.rol == "vendedor" and v.usuario_id != current_user.id:
        abort(404)
    return render_template("venta_detalle.html", v=v)


@app.route("/ventas/<int:id>/comprobante.<fmt>")
@rol_requerido("admin", "vendedor")
def venta_comprobante(id, fmt):
    v = db.session.get(Venta, id) or abort(404)
    if current_user.rol == "vendedor" and v.usuario_id != current_user.id:
        abort(404)
    negocio = DatosNegocio.actual()
    nombre = f"{comprobantes.numero(v)}.{fmt}"
    if fmt == "pdf":
        data, mime = comprobantes.pdf(v, negocio), "application/pdf"
    elif fmt == "jpg":
        data, mime = comprobantes.jpg(v, negocio), "image/jpeg"
    else:
        abort(404)
    modo = "attachment" if request.args.get("descargar") else "inline"
    return Response(data, mimetype=mime,
                    headers={"Content-Disposition": f'{modo}; filename="{nombre}"'})


@app.route("/negocio", methods=["GET", "POST"])
@rol_requerido("admin")
def negocio():
    neg = DatosNegocio.actual()
    if request.method == "POST":
        f = request.form
        nombre = f.get("nombre", "").strip()
        if not nombre:
            flash("El nombre del negocio es obligatorio.", "error")
        else:
            neg.nombre = nombre[:120]
            for campo, largo in (("nit", 40), ("direccion", 160), ("ciudad", 80),
                                 ("telefono", 40), ("mensaje", 200)):
                setattr(neg, campo, (f.get(campo, "").strip()[:largo]) or None)
            db.session.commit()
            flash("Datos del negocio guardados. Así saldrán en los comprobantes.", "ok")
            return redirect(url_for("negocio"))
    ultima = Venta.query.order_by(Venta.id.desc()).first()
    return render_template("negocio.html", neg=neg, ultima=ultima)


@app.route("/compras/<int:id>/anular", methods=["POST"])
@rol_requerido("admin")
def compra_anular(id):
    c = db.session.get(CompraInsumo, id) or abort(404)
    try:
        anular_compra(c)
        db.session.commit()
        flash(f"Compra de {c.cantidad:g} {c.insumo.unidad} de «{c.insumo.nombre}» anulada. "
              "Se descontó del inventario.", "ok")
    except ErrorNegocio as e:
        db.session.rollback()
        flash(e.errores[0], "error")
    return redirect(request.referrer or url_for("compras"))


@app.route("/produccion/<int:id>/anular", methods=["POST"])
@rol_requerido("admin")
def produccion_anular(id):
    p = db.session.get(Produccion, id) or abort(404)
    try:
        anular_produccion(p)
        db.session.commit()
        flash(f"Producción #{p.id} anulada: se quitaron {p.cantidad} vikingos y los insumos "
              "volvieron a la bodega.", "ok")
    except ErrorNegocio as e:
        db.session.rollback()
        flash(e.errores[0], "error")
    return redirect(url_for("produccion_detalle", id=id))


@app.route("/ventas/<int:id>/anular", methods=["POST"])
@rol_requerido("admin")
def venta_anular(id):
    v = db.session.get(Venta, id) or abort(404)
    try:
        anular_venta(v)
        db.session.commit()
        flash(f"Venta #{v.id} anulada. Los vikingos volvieron al inventario.", "ok")
    except ErrorNegocio as e:
        flash(e.errores[0], "error")
    return redirect(url_for("venta_detalle", id=id))


# ---------------------------------------------------------------- usuarios
@app.route("/usuarios")
@rol_requerido("admin")
def usuarios():
    return render_template("usuarios.html",
                           items=Usuario.query.order_by(Usuario.activo.desc(), Usuario.nombre).all())


@app.route("/usuarios/nuevo", methods=["GET", "POST"])
@app.route("/usuarios/<int:id>/editar", methods=["GET", "POST"])
@rol_requerido("admin")
def usuario_form(id=None):
    u = db.session.get(Usuario, id) if id else None
    if id and not u:
        abort(404)
    if request.method == "POST":
        f = request.form
        nombre, username = f.get("nombre", "").strip(), f.get("username", "").strip().lower()
        rol, clave = f.get("rol", "vendedor"), f.get("password", "")
        activo = bool(f.get("activo")) if u else True
        dup = Usuario.query.filter(func.lower(Usuario.username) == username,
                                   Usuario.id != (id or 0)).first()
        if not nombre or not username:
            flash("Nombre y usuario son obligatorios.", "error")
        elif dup:
            flash("Ese nombre de usuario ya existe.", "error")
        elif rol not in ROLES:
            flash("Rol no válido.", "error")
        elif not u and len(clave) < 6:
            flash("La contraseña debe tener al menos 6 caracteres.", "error")
        elif u and u.id == current_user.id and (rol != "admin" or not activo):
            flash("No puedes quitarte el rol de administrador ni desactivarte a ti mismo.", "error")
        else:
            if not u:
                u = Usuario()
                db.session.add(u)
            u.nombre, u.username, u.rol, u.activo = nombre, username, rol, activo
            if clave:
                if len(clave) < 6:
                    flash("La contraseña debe tener al menos 6 caracteres.", "error")
                    return render_template("usuario_form.html", u=u)
                u.set_password(clave)
            db.session.commit()
            flash(f"Usuario «{username}» guardado.", "ok")
            return redirect(url_for("usuarios"))
    return render_template("usuario_form.html", u=u)


@app.errorhandler(404)
def no_encontrado(e):
    return render_template("error.html", titulo="No encontrado",
                           mensaje="Lo que buscas no existe o fue eliminado."), 404


# ------------------------------------------------------------- arranque BD
def migrar_columnas():
    """Agrega columnas nuevas a tablas que ya existían (create_all no lo hace)."""
    from sqlalchemy import inspect, text
    nuevas = [("vk_compras", "anulada", "BOOLEAN NOT NULL DEFAULT FALSE"),
              ("vk_producciones", "anulada", "BOOLEAN NOT NULL DEFAULT FALSE"),
              ("vk_activos", "proveedor", "VARCHAR(120)")]
    insp = inspect(db.engine)
    for tabla, col, tipo in nuevas:
        if col not in {c["name"] for c in insp.get_columns(tabla)}:
            with db.engine.begin() as cn:
                cn.execute(text(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}"))


def inicializar_bd():
    with app.app_context():
        try:
            db.create_all()
            migrar_columnas()
        except Exception as e:
            print("=" * 70, flush=True)
            print("ERROR: no se pudo conectar a la base de datos (DATABASE_URL).", flush=True)
            print("Revisa: usar 'Session pooler' (puerto 5432), contraseña correcta sin", flush=True)
            print("corchetes, y sin espacios. Detalle técnico:", flush=True)
            print(str(e).split("\n")[0][:300], flush=True)
            print("=" * 70, flush=True)
            raise
        if not Usuario.query.first():
            try:
                admin = Usuario(nombre=os.getenv("ADMIN_NOMBRE", "Administrador"),
                                username=os.getenv("ADMIN_USER", "admin").lower(), rol="admin")
                admin.set_password(os.getenv("ADMIN_PASSWORD", "admin123"))
                db.session.add(admin)
                db.session.commit()
            except Exception:  # otro worker lo creó al mismo tiempo
                db.session.rollback()


inicializar_bd()

if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", 5000)))
