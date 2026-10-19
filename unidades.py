"""Conversión entre unidades de la misma familia (masa y volumen)."""

FACTORES = {
    # masa → gramos
    "kilogramo": ("masa", 1000.0), "gramo": ("masa", 1.0), "libra": ("masa", 453.592),
    # volumen → mililitros
    "litro": ("volumen", 1000.0), "mililitro": ("volumen", 1.0), "galón": ("volumen", 3785.41),
}
ABREV = {"kilogramo": "kg", "gramo": "g", "libra": "lb", "litro": "L", "mililitro": "ml",
         "galón": "gal", "unidad": "und"}


def compatibles(unidad):
    """Unidades en que se puede expresar un insumo medido en `unidad`."""
    fam = FACTORES.get(unidad, (None,))[0]
    if not fam:
        return [unidad]
    return [u for u, (f, _) in FACTORES.items() if f == fam]


def convertir(cantidad, de, a):
    if not de or not a or de == a:
        return cantidad
    fd, fa = FACTORES.get(de), FACTORES.get(a)
    if not fd or not fa or fd[0] != fa[0]:
        return cantidad
    return cantidad * fd[1] / fa[1]


def unidad_valida(pedida, base):
    return pedida if pedida in compatibles(base) else base
