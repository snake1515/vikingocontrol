# VikingoControl

App web para negocios que fabrican **vikingos / bolis / helados de bolsa**: insumos con costo, transformación en producto, inventario, ventas y ganancias.

## Qué hace

| Módulo | Qué resuelve |
|---|---|
| **Insumos** | Bolsas, leche, azúcar, pulpas… con stock, unidad, costo promedio ponderado y alerta de mínimo |
| **Compras** | Entrada de insumos (varios en una compra). Recalcula el costo promedio solo |
| **Vikingos (referencias)** | Cada sabor con precio de venta y **receta por 1 vikingo**. Muestra costo estimado, margen y **cuántos puedes hacer hoy** (y qué insumo te limita) |
| **Producción** | La transformación: insumos usados (llenados según receta, editables) + **mano de obra** (por vikingo o por jornada/lote) + **indirectos** (energía/congelación, gas/agua, transporte/domicilios, otros con descripción) → **costo real por vikingo**. Compara lo que salió contra lo que la receta decía (merma) |
| **Ventas** | Pantalla rápida tipo POS (tocar el sabor para sumar). Guarda el costo de cada vikingo vendido para calcular la ganancia real |
| **Resumen** | Ventas, costo de lo vendido, **ganancia y margen**, gráfica diaria, **referencias más vendidas**, desglose de costos de producción, inventario valorizado |
| **Usuarios** | Roles: **Administrador** (todo), **Producción** (insumos, compras, producción; no ve ventas ni ganancias), **Vendedor** (vende y ve solo sus ventas; no ve costos) |

### Cómo se calcula la ganancia
1. Cada compra actualiza el **costo promedio** del insumo.
2. Cada producción calcula: `(insumos + mano de obra + indirectos) ÷ vikingos obtenidos = costo por vikingo`, y lo promedia con el stock existente de esa referencia.
3. Cada venta guarda ese costo → `ganancia = precio de venta − costo del vikingo`.

## Correr en tu PC

```bash
python -m venv venv
venv\Scripts\activate          # Windows  (Linux/Mac: source venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env         # y ajusta los valores (sin DATABASE_URL usa SQLite local)
python app.py                  # http://localhost:5000
```

Usuario inicial: `admin` / `admin123` (o lo que pongas en `ADMIN_USER` / `ADMIN_PASSWORD`). Cámbiala en **Mi cuenta**.

**Datos de prueba** (solo en una base vacía): `python seed_demo.py` → crea 9 insumos, 5 sabores, 30 días de producción y ventas, y los usuarios `marta` (producción) y `julian` (vendedor), clave `demo123`.

## Desplegar en Render + Supabase

1. **Supabase** → crea el proyecto → *Project Settings → Database → Connection string → URI*. Usa el **Session pooler** (puerto 5432) y reemplaza `[YOUR-PASSWORD]`.
2. Sube esta carpeta a un repo de GitHub.
3. **Render** → *New → Blueprint* → elige el repo (lee `render.yaml`).
   O *New → Web Service*: Build `pip install -r requirements.txt`, Start `gunicorn app:app --workers 2 --timeout 60`.
4. Variables de entorno: `DATABASE_URL` (la de Supabase), `ADMIN_PASSWORD` (clave del admin inicial). `SECRET_KEY` se genera sola con el blueprint.
5. Al arrancar, la app **crea las tablas** (prefijo `vk_`, para convivir con otras apps en la misma base) y el usuario admin.

Si quieres datos demo en producción: en Render → *Shell* → `python seed_demo.py` (solo con la base vacía).

## Estructura

```
app.py          rutas, filtros, arranque
models.py       tablas (vk_*)
services.py     lógica: compras, producción, ventas, anulación
seed_demo.py    datos de ejemplo
templates/      pantallas (Jinja)
static/         estilos + Chart.js local
```
