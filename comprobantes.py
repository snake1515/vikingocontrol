"""Comprobantes de venta en PDF (para PC) y JPG (para celular).

No es una factura electrónica DIAN: es un soporte de la venta para el cliente.
"""
import io
import os

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

FONT_DIR = os.path.join(os.path.dirname(__file__), "static", "fonts")
F_REG = os.path.join(FONT_DIR, "DejaVuSans.ttf")
F_BOLD = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")
LOGO = os.path.join(os.path.dirname(__file__), "static", "logo.jpg")
HAY_LOGO = os.path.exists(LOGO)
pdfmetrics.registerFont(TTFont("DV", F_REG))
pdfmetrics.registerFont(TTFont("DVB", F_BOLD))

TINTA = "#14233a"
GRIS = "#5f6f84"
LINEA = "#d9e2ea"
ACENTO = "#0aa5c2"
ROJO = "#c53030"
NOTA_LEGAL = "Documento soporte de venta. No es factura electrónica de venta."


def numero(venta):
    return f"VK-{venta.id:06d}"


def cop(v):
    v = v or 0
    return ("-$ " if v < -0.5 else "$ ") + f"{abs(v):,.0f}".replace(",", ".")


def _datos(venta, negocio):
    lineas_neg = [x for x in (
        f"NIT {negocio.nit}" if negocio.nit else None,
        " · ".join(x for x in (negocio.direccion, negocio.ciudad) if x) or None,
        f"Tel. {negocio.telefono}" if negocio.telefono else None,
    ) if x]
    items = [dict(nombre=i.producto.nombre + (" (obsequio)" if i.bonificado else ""), cant=i.cantidad, precio=i.precio_unitario,
                  subtotal=i.subtotal) for i in venta.items]
    return dict(
        negocio=negocio.nombre or "Mi negocio", lineas_neg=lineas_neg,
        numero=numero(venta), fecha=venta.fecha.strftime("%d/%m/%Y %I:%M %p"),
        cliente=venta.cliente or "Cliente general", pago=venta.metodo_pago or "",
        vendedor=venta.usuario.nombre if venta.usuario else "", items=items,
        unidades=venta.unidades, total=venta.total, anulada=venta.anulada,
        mensaje=negocio.mensaje or "", nota=venta.nota or "",
    )


# --------------------------------------------------------------------- PDF
def pdf(venta, negocio):
    d = _datos(venta, negocio)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle(f"Comprobante {d['numero']}")
    W, H = letter
    x0, x1 = 20 * mm, W - 20 * mm
    y = H - 22 * mm

    # Encabezado
    c.setFillColor(HexColor(ACENTO))
    c.rect(0, H - 8 * mm, W, 8 * mm, stroke=0, fill=1)
    xt = x0
    if HAY_LOGO:
        lw = 30 * mm
        c.drawImage(LOGO, x0, y - 24 * mm, width=lw, height=lw * 769 / 800, mask=None)
        xt = x0 + lw + 5 * mm
    c.setFillColor(HexColor(TINTA))
    c.setFont("DVB", 18)
    c.drawString(xt, y - 2 * mm, d["negocio"])
    c.setFont("DV", 9.5)
    c.setFillColor(HexColor(GRIS))
    yy = y - 8 * mm
    for ln in d["lineas_neg"]:
        c.drawString(xt, yy, ln)
        yy -= 4.6 * mm
    if HAY_LOGO:
        yy = min(yy, y - 26 * mm)
    c.setFillColor(HexColor(TINTA))
    c.setFont("DVB", 12)
    c.drawRightString(x1, y, "COMPROBANTE DE VENTA")
    c.setFont("DVB", 16)
    c.setFillColor(HexColor(ACENTO))
    c.drawRightString(x1, y - 8 * mm, d["numero"])
    c.setFont("DV", 9.5)
    c.setFillColor(HexColor(GRIS))
    c.drawRightString(x1, y - 14 * mm, d["fecha"])

    y = min(yy, y - 20 * mm) - 6 * mm
    # Datos de la venta
    c.setStrokeColor(HexColor(LINEA))
    c.roundRect(x0, y - 16 * mm, x1 - x0, 16 * mm, 3 * mm, stroke=1, fill=0)
    cols = [("Cliente", d["cliente"]), ("Medio de pago", d["pago"]), ("Atendió", d["vendedor"])]
    cw = (x1 - x0) / 3
    for i, (k, v) in enumerate(cols):
        cx = x0 + 5 * mm + i * cw
        c.setFont("DV", 8)
        c.setFillColor(HexColor(GRIS))
        c.drawString(cx, y - 6 * mm, k.upper())
        c.setFont("DVB", 10.5)
        c.setFillColor(HexColor(TINTA))
        c.drawString(cx, y - 11.5 * mm, v[:34])
    y -= 26 * mm

    # Tabla
    cx_cant, cx_precio, cx_sub = x1 - 90 * mm, x1 - 40 * mm, x1
    c.setFont("DVB", 8.5)
    c.setFillColor(HexColor(GRIS))
    c.drawString(x0, y, "PRODUCTO")
    c.drawRightString(cx_cant, y, "CANT.")
    c.drawRightString(cx_precio, y, "PRECIO UNIT.")
    c.drawRightString(cx_sub, y, "SUBTOTAL")
    y -= 3 * mm
    c.setStrokeColor(HexColor(TINTA))
    c.line(x0, y, x1, y)
    y -= 7 * mm
    c.setStrokeColor(HexColor(LINEA))
    for it in d["items"]:
        if y < 50 * mm:
            c.showPage()
            y = H - 25 * mm
        c.setFont("DV", 10.5)
        c.setFillColor(HexColor(TINTA))
        c.drawString(x0, y, it["nombre"][:48])
        c.drawRightString(cx_cant, y, str(it["cant"]))
        c.drawRightString(cx_precio, y, cop(it["precio"]))
        c.setFont("DVB", 10.5)
        c.drawRightString(cx_sub, y, cop(it["subtotal"]))
        y -= 4 * mm
        c.line(x0, y, x1, y)
        y -= 6 * mm

    # Total
    y -= 2 * mm
    c.setFillColor(HexColor("#eef7fa"))
    c.roundRect(x1 - 85 * mm, y - 14 * mm, 85 * mm, 16 * mm, 3 * mm, stroke=0, fill=1)
    c.setFillColor(HexColor(GRIS))
    c.setFont("DV", 9.5)
    c.drawString(x1 - 80 * mm, y - 4 * mm, f"{d['unidades']} vikingo{'s' if d['unidades'] != 1 else ''}")
    c.setFillColor(HexColor(TINTA))
    c.setFont("DVB", 11)
    c.drawString(x1 - 80 * mm, y - 10 * mm, "TOTAL")
    c.setFont("DVB", 17)
    c.drawRightString(x1 - 5 * mm, y - 10 * mm, cop(d["total"]))
    y -= 26 * mm

    if d["nota"]:
        c.setFont("DV", 9.5)
        c.setFillColor(HexColor(GRIS))
        c.drawString(x0, y, f"Nota: {d['nota'][:100]}")
        y -= 8 * mm
    if d["mensaje"]:
        c.setFont("DVB", 11)
        c.setFillColor(HexColor(TINTA))
        c.drawCentredString(W / 2, y, d["mensaje"])

    c.setFont("DV", 8)
    c.setFillColor(HexColor(GRIS))
    c.drawCentredString(W / 2, 14 * mm, NOTA_LEGAL)

    if d["anulada"]:
        c.saveState()
        c.translate(W / 2, H / 2)
        c.rotate(30)
        c.setFillColor(HexColor(ROJO))
        c.setFillAlpha(0.18)
        c.setFont("DVB", 90)
        c.drawCentredString(0, 0, "ANULADA")
        c.restoreState()

    c.showPage()
    c.save()
    return buf.getvalue()


# --------------------------------------------------------------------- JPG
def jpg(venta, negocio):
    d = _datos(venta, negocio)
    W, M = 1080, 64
    f = lambda size, bold=False: ImageFont.truetype(F_BOLD if bold else F_REG, size)
    F = dict(neg=f(54, True), small=f(30), lbl=f(26), val=f(34, True), item=f(36),
             itemb=f(36, True), sub=f(30), tot=f(64, True), totl=f(36, True),
             num=f(46, True), msg=f(34, True), legal=f(24))

    def ancho(txt, font):
        return ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(txt, font=font)

    def cortar(txt, font, maxw):
        if ancho(txt, font) <= maxw:
            return [txt]
        palabras, lineas, actual = txt.split(), [], ""
        for p in palabras:
            prueba = (actual + " " + p).strip()
            if ancho(prueba, font) <= maxw:
                actual = prueba
            else:
                lineas.append(actual)
                actual = p
        lineas.append(actual)
        return lineas

    # Alto según el contenido
    alto_items = sum(46 * len(cortar(it["nombre"], F["item"], W - 2 * M - 300)) + 74 for it in d["items"])
    LOGO_W = 380
    H = 640 + (LOGO_W * 769 // 800 + 30 if HAY_LOGO else 0) + 44 * len(d["lineas_neg"]) + alto_items + 280 + (60 if d["nota"] else 0)
    img = Image.new("RGB", (W, H), "white")
    g = ImageDraw.Draw(img)

    g.rectangle([0, 0, W, 18], fill=ACENTO)
    y = 70
    if HAY_LOGO:
        lg = Image.open(LOGO).convert("RGB")
        lg = lg.resize((LOGO_W, LOGO_W * lg.height // lg.width), Image.LANCZOS)
        img.paste(lg, ((W - LOGO_W) // 2, 50))
        y = 50 + lg.height + 30
    for ln in cortar(d["negocio"], F["neg"], W - 2 * M):
        g.text((W / 2, y), ln, font=F["neg"], fill=TINTA, anchor="mt")
        y += 64
    for ln in d["lineas_neg"]:
        g.text((W / 2, y), ln, font=F["small"], fill=GRIS, anchor="mt")
        y += 44
    y += 24
    g.line([M, y, W - M, y], fill=LINEA, width=3)
    y += 34
    g.text((W / 2, y), "COMPROBANTE DE VENTA", font=F["lbl"], fill=GRIS, anchor="mt")
    y += 42
    g.text((W / 2, y), d["numero"], font=F["num"], fill=ACENTO, anchor="mt")
    y += 64
    g.text((W / 2, y), d["fecha"], font=F["small"], fill=GRIS, anchor="mt")
    y += 64

    for k, v in (("Cliente", d["cliente"]), ("Pago", d["pago"]), ("Atendió", d["vendedor"])):
        g.text((M, y), k, font=F["sub"], fill=GRIS)
        g.text((W - M, y), v[:30], font=F["val"], fill=TINTA, anchor="ra")
        y += 50
    y += 16
    g.line([M, y, W - M, y], fill=TINTA, width=3)
    y += 30

    for it in d["items"]:
        for ln in cortar(it["nombre"], F["item"], W - 2 * M - 300):
            g.text((M, y), ln, font=F["itemb"], fill=TINTA)
            y += 46
        g.text((M, y), f"{it['cant']} × {cop(it['precio'])}", font=F["sub"], fill=GRIS)
        g.text((W - M, y - 46), cop(it["subtotal"]), font=F["itemb"], fill=TINTA, anchor="ra")
        y += 44
        g.line([M, y, W - M, y], fill=LINEA, width=2)
        y += 30

    y += 10
    g.rounded_rectangle([M, y, W - M, y + 150], radius=24, fill="#eef7fa")
    g.text((M + 36, y + 34), "TOTAL", font=F["totl"], fill=TINTA)
    g.text((M + 36, y + 86), f"{d['unidades']} vikingo{'s' if d['unidades'] != 1 else ''}", font=F["sub"], fill=GRIS)
    g.text((W - M - 36, y + 75), cop(d["total"]), font=F["tot"], fill=TINTA, anchor="rm")
    y += 196

    if d["nota"]:
        g.text((W / 2, y), f"Nota: {d['nota'][:60]}", font=F["sub"], fill=GRIS, anchor="mt")
        y += 60
    if d["mensaje"]:
        g.text((W / 2, y), d["mensaje"], font=F["msg"], fill=TINTA, anchor="mt")
    g.text((W / 2, H - 50), NOTA_LEGAL, font=F["legal"], fill=GRIS, anchor="mt")

    if d["anulada"]:
        capa = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        t = Image.new("RGBA", (900, 220), (0, 0, 0, 0))
        ImageDraw.Draw(t).text((450, 110), "ANULADA", font=f(170, True), fill=(197, 48, 48, 95), anchor="mm")
        t = t.rotate(30, expand=True)
        capa.paste(t, ((W - t.width) // 2, (H - t.height) // 2), t)
        img = Image.alpha_composite(img.convert("RGBA"), capa).convert("RGB")

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()
