"""Modelos de datos de VikingoControl.

Todas las tablas usan el prefijo vk_ para poder convivir con otras apps
en la misma base de datos de Supabase.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()
TZ = ZoneInfo("America/Bogota")


def ahora():
    """Fecha y hora actual de Colombia (sin zona, para guardar en BD)."""
    return datetime.now(TZ).replace(tzinfo=None, microsecond=0)


ROLES = {
    "admin": "Administrador",
    "produccion": "Producción",
    "vendedor": "Vendedor",
}

UNIDADES = [
    "unidad", "bolsa", "litro", "mililitro", "kilogramo", "gramo",
    "libra", "paquete", "caja", "galón",
]

TIPOS_MO = {
    "ninguna": "Sin mano de obra",
    "unidad": "Pago por vikingo hecho",
    "jornada": "Pago por jornada / lote",
}

METODOS_PAGO = ["Efectivo", "Nequi", "Daviplata", "Transferencia", "Crédito"]


class Usuario(UserMixin, db.Model):
    __tablename__ = "vk_usuarios"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    username = db.Column(db.String(60), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    rol = db.Column(db.String(20), nullable=False, default="vendedor")
    activo = db.Column(db.Boolean, nullable=False, default=True)
    creado = db.Column(db.DateTime, default=ahora)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self):
        return self.activo

    @property
    def rol_nombre(self):
        return ROLES.get(self.rol, self.rol)


class Configuracion(db.Model):
    """Una sola fila con la regla de distribución del dinero."""
    __tablename__ = "vk_configuracion"
    id = db.Column(db.Integer, primary_key=True)
    pct_reinversion = db.Column(db.Float, nullable=False, default=60)
    pct_dueno = db.Column(db.Float, nullable=False, default=30)
    pct_emergencia = db.Column(db.Float, nullable=False, default=10)
    base = db.Column(db.String(20), nullable=False, default="ventas")  # ventas | ganancia

    @classmethod
    def actual(cls):
        c = db.session.get(cls, 1)
        if not c:
            c = cls(id=1)
            db.session.add(c)
            db.session.commit()
        return c


class DatosNegocio(db.Model):
    """Datos que salen en el encabezado de los comprobantes de venta."""
    __tablename__ = "vk_negocio"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False, default="Vikingos Juanchis")
    nit = db.Column(db.String(40))
    direccion = db.Column(db.String(160))
    ciudad = db.Column(db.String(80))
    telefono = db.Column(db.String(40))
    mensaje = db.Column(db.String(200), default="¡Gracias por su compra!")

    @classmethod
    def actual(cls):
        d = db.session.get(cls, 1)
        if not d:
            d = cls(id=1)
            db.session.add(d)
            db.session.commit()
        return d


class Insumo(db.Model):
    __tablename__ = "vk_insumos"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    unidad = db.Column(db.String(30), nullable=False, default="unidad")
    stock = db.Column(db.Float, nullable=False, default=0)
    costo_promedio = db.Column(db.Float, nullable=False, default=0)
    stock_minimo = db.Column(db.Float, nullable=False, default=0)
    activo = db.Column(db.Boolean, nullable=False, default=True)

    @property
    def valor_stock(self):
        return max(self.stock or 0, 0) * (self.costo_promedio or 0)

    @property
    def bajo_minimo(self):
        return (self.stock_minimo or 0) > 0 and (self.stock or 0) <= self.stock_minimo


class CompraInsumo(db.Model):
    __tablename__ = "vk_compras"
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    insumo_id = db.Column(db.Integer, db.ForeignKey("vk_insumos.id"), nullable=False)
    cantidad = db.Column(db.Float, nullable=False)
    costo_total = db.Column(db.Float, nullable=False)
    costo_unitario = db.Column(db.Float, nullable=False)
    proveedor = db.Column(db.String(120))
    nota = db.Column(db.String(255))
    usuario_id = db.Column(db.Integer, db.ForeignKey("vk_usuarios.id"))
    anulada = db.Column(db.Boolean, nullable=False, default=False)

    insumo = db.relationship("Insumo", lazy="joined")
    usuario = db.relationship("Usuario", lazy="joined")


class Producto(db.Model):
    """Una referencia de vikingo (sabor/presentación)."""
    __tablename__ = "vk_productos"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    descripcion = db.Column(db.String(255))
    precio_venta = db.Column(db.Float, nullable=False, default=0)
    stock = db.Column(db.Integer, nullable=False, default=0)
    costo_promedio = db.Column(db.Float, nullable=False, default=0)
    stock_minimo = db.Column(db.Integer, nullable=False, default=0)
    activo = db.Column(db.Boolean, nullable=False, default=True)

    receta = db.relationship(
        "RecetaItem", back_populates="producto", lazy="selectin",
        cascade="all, delete-orphan", order_by="RecetaItem.id",
    )

    @property
    def costo_receta(self):
        """Costo solo de insumos para 1 vikingo, con los costos promedio actuales."""
        return sum(r.cantidad * (r.insumo.costo_promedio or 0) for r in self.receta)

    @property
    def capacidad(self):
        """Cuántos vikingos se pueden hacer con el inventario actual de insumos.

        Devuelve (cantidad, insumo_limitante) o (None, None) si no hay receta.
        """
        mejor, limitante = None, None
        for r in self.receta:
            if r.cantidad <= 0:
                continue
            posible = int(max(r.insumo.stock or 0, 0) // r.cantidad)
            if mejor is None or posible < mejor:
                mejor, limitante = posible, r.insumo
        return mejor, limitante

    @property
    def margen_unitario(self):
        return (self.precio_venta or 0) - (self.costo_promedio or 0)

    @property
    def bajo_minimo(self):
        return (self.stock_minimo or 0) > 0 and (self.stock or 0) <= self.stock_minimo


class RecetaItem(db.Model):
    """Cantidad de un insumo que lleva 1 vikingo."""
    __tablename__ = "vk_receta"
    id = db.Column(db.Integer, primary_key=True)
    producto_id = db.Column(db.Integer, db.ForeignKey("vk_productos.id"), nullable=False)
    insumo_id = db.Column(db.Integer, db.ForeignKey("vk_insumos.id"), nullable=False)
    cantidad = db.Column(db.Float, nullable=False)

    producto = db.relationship("Producto", back_populates="receta")
    insumo = db.relationship("Insumo", lazy="joined")


class Produccion(db.Model):
    """Transformación: insumos + mano de obra + indirectos -> vikingos."""
    __tablename__ = "vk_producciones"
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    producto_id = db.Column(db.Integer, db.ForeignKey("vk_productos.id"), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)

    costo_insumos = db.Column(db.Float, nullable=False, default=0)
    mo_tipo = db.Column(db.String(20), nullable=False, default="ninguna")
    mo_valor = db.Column(db.Float, nullable=False, default=0)
    mo_responsable = db.Column(db.String(120))
    costo_mano_obra = db.Column(db.Float, nullable=False, default=0)

    costo_energia = db.Column(db.Float, nullable=False, default=0)
    costo_gas_agua = db.Column(db.Float, nullable=False, default=0)
    costo_transporte = db.Column(db.Float, nullable=False, default=0)
    costo_otros = db.Column(db.Float, nullable=False, default=0)
    otros_descripcion = db.Column(db.String(255))

    costo_total = db.Column(db.Float, nullable=False, default=0)
    costo_unitario = db.Column(db.Float, nullable=False, default=0)
    nota = db.Column(db.String(255))
    usuario_id = db.Column(db.Integer, db.ForeignKey("vk_usuarios.id"))
    anulada = db.Column(db.Boolean, nullable=False, default=False)

    producto = db.relationship("Producto", lazy="joined")
    usuario = db.relationship("Usuario", lazy="joined")
    consumos = db.relationship(
        "ProduccionConsumo", back_populates="produccion", lazy="selectin",
        cascade="all, delete-orphan",
    )

    @property
    def costo_indirectos(self):
        return (self.costo_energia + self.costo_gas_agua
                + self.costo_transporte + self.costo_otros)


class ProduccionConsumo(db.Model):
    __tablename__ = "vk_produccion_consumos"
    id = db.Column(db.Integer, primary_key=True)
    produccion_id = db.Column(db.Integer, db.ForeignKey("vk_producciones.id"), nullable=False)
    insumo_id = db.Column(db.Integer, db.ForeignKey("vk_insumos.id"), nullable=False)
    cantidad = db.Column(db.Float, nullable=False)
    costo_unitario = db.Column(db.Float, nullable=False)
    costo_total = db.Column(db.Float, nullable=False)

    produccion = db.relationship("Produccion", back_populates="consumos")
    insumo = db.relationship("Insumo", lazy="joined")


class Venta(db.Model):
    __tablename__ = "vk_ventas"
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, nullable=False, default=ahora, index=True)
    cliente = db.Column(db.String(120))
    metodo_pago = db.Column(db.String(30), default="Efectivo")
    nota = db.Column(db.String(255))
    total = db.Column(db.Float, nullable=False, default=0)
    costo_total = db.Column(db.Float, nullable=False, default=0)
    ganancia = db.Column(db.Float, nullable=False, default=0)
    anulada = db.Column(db.Boolean, nullable=False, default=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("vk_usuarios.id"))

    usuario = db.relationship("Usuario", lazy="joined")
    items = db.relationship(
        "VentaItem", back_populates="venta", cascade="all, delete-orphan", lazy="selectin",
    )

    @property
    def unidades(self):
        return sum(i.cantidad for i in self.items)


class VentaItem(db.Model):
    __tablename__ = "vk_venta_items"
    id = db.Column(db.Integer, primary_key=True)
    venta_id = db.Column(db.Integer, db.ForeignKey("vk_ventas.id"), nullable=False)
    producto_id = db.Column(db.Integer, db.ForeignKey("vk_productos.id"), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    precio_unitario = db.Column(db.Float, nullable=False)
    costo_unitario = db.Column(db.Float, nullable=False)
    subtotal = db.Column(db.Float, nullable=False)
    costo = db.Column(db.Float, nullable=False)

    venta = db.relationship("Venta", back_populates="items")
    producto = db.relationship("Producto", lazy="joined")


class Activo(db.Model):
    """Equipos y herramientas (selladora, congelador, ollas…). Se deprecian en línea recta."""
    __tablename__ = "vk_activos"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(120), nullable=False)
    descripcion = db.Column(db.String(255))
    fecha_compra = db.Column(db.Date, nullable=False)
    valor = db.Column(db.Float, nullable=False)
    valor_residual = db.Column(db.Float, nullable=False, default=0)
    vida_util_meses = db.Column(db.Integer, nullable=False)
    fecha_baja = db.Column(db.Date)
    motivo_baja = db.Column(db.String(255))
    eliminado = db.Column(db.Boolean, nullable=False, default=False)
    proveedor = db.Column(db.String(120))

    # --- cálculos (línea recta, prorrateo diario) ---
    @property
    def depreciable(self):
        return max((self.valor or 0) - (self.valor_residual or 0), 0)

    @property
    def cuota_mensual(self):
        return self.depreciable / self.vida_util_meses if self.vida_util_meses else 0

    @property
    def cuota_diaria(self):
        return self.cuota_mensual * 12 / 365

    @property
    def fin_vida(self):
        from datetime import timedelta
        return self.fecha_compra + timedelta(days=round(self.vida_util_meses * 365 / 12))

    def depreciacion_entre(self, desde, hasta):
        """Depreciación de los días [desde, hasta) — fechas date."""
        ini = max(desde, self.fecha_compra)
        fin = min(hasta, self.fin_vida, self.fecha_baja or hasta)
        dias = (fin - ini).days
        return max(dias, 0) * self.cuota_diaria

    def acumulada(self, hoy):
        from datetime import timedelta
        return min(self.depreciacion_entre(self.fecha_compra, hoy + timedelta(days=1)), self.depreciable)

    def estado(self, hoy):
        if self.fecha_baja:
            return "baja"
        if hoy >= self.fin_vida:
            return "recuperado"
        return "vigente"


CATEGORIAS_GASTO = ["Publicidad", "Arriendo", "Servicios públicos", "Internet / celular",
                    "Transporte", "Mantenimiento de equipos", "Impuestos y trámites",
                    "Papelería y empaques", "Otros"]


class Gasto(db.Model):
    """Gastos del negocio que no son insumos ni equipos: publicidad, arriendo, servicios…"""
    __tablename__ = "vk_gastos"
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.Date, nullable=False, index=True)
    categoria = db.Column(db.String(60), nullable=False, default="Otros")
    descripcion = db.Column(db.String(255))
    valor = db.Column(db.Float, nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("vk_usuarios.id"))
    usuario = db.relationship("Usuario")
