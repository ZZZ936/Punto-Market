# Punto Market

Aplicación inicial de catálogo y gestión de evento. Incluye una página pública en español, mapa interactivo de puestos, formulario de postulación y un panel privado con acceso de administrador. El servidor, la base de datos y la interfaz usan Python y bibliotecas estándar: no hay que instalar paquetes.

## Requisitos

- Python 3.10 o posterior.
- Para publicar en internet: un servidor Linux o servicio de hosting para Python y un proxy HTTPS (por ejemplo, Caddy o Nginx). El servidor de desarrollo integrado no debe exponerse directamente a internet.

## Ejecutar localmente

Desde esta carpeta, crea la cuenta de administración. El programa pedirá la contraseña sin mostrarla; debe tener al menos 12 caracteres:

```sh
python app.py create-admin admin@tudominio.com
```

En Windows puedes usar `py` en lugar de `python`. Luego inicia la aplicación:

```sh
python app.py
```

Abre <http://127.0.0.1:8000>. Para cambiar el puerto, define `PORT`; por ejemplo, `PORT=8080 python app.py`. La app escucha solo en `127.0.0.1` por defecto.

## Publicación

1. Coloca la aplicación detrás de un proxy que termine HTTPS y enrute las peticiones a `127.0.0.1:8000`.
2. Conserva `PUNTO_HOST=127.0.0.1`, define `PUNTO_HTTPS=1` y asigna un `PUNTO_DATA_DIR` persistente, fuera del directorio del código.
3. Mantén la base de datos y las fotos en ese directorio persistente; respáldalo regularmente. Restringe su acceso al usuario del servicio.
4. Crea administradores en el servidor con `python app.py create-admin correo@dominio.com`. No uses la opción `--password` en producción.
5. Desactiva el registro público de cuentas. La única vía para crear administradores es el comando local del servidor.

Para un alojamiento sin consola interactiva, puedes definir `PUNTO_ADMIN_EMAIL` y `PUNTO_ADMIN_PASSWORD` en el panel privado de variables de entorno del proveedor. Si todavía no existe ninguna cuenta, la app crea el primer administrador al iniciar. Usa una contraseña de al menos 12 caracteres y nunca guardes estas variables en GitHub. Después de creada la cuenta, la app ignora esas variables mientras exista un administrador.

La cookie de sesión es `HttpOnly` y `SameSite=Strict`; en producción lleva además el atributo `Secure`. Las sesiones caducan después de siete días. Las operaciones de escritura del panel requieren un token CSRF ligado a la sesión.

## Privacidad y permisos

- Las rutas públicas entregan solo la información del evento, los puestos y los productos con estado `approved`.
- Las postulaciones, correo y teléfono de vendedores, importes y notas de compra/venta, registros no aprobados y contenido de administración requieren sesión y rol de admin/manager en el servidor. No se confía en ocultar botones de la interfaz como control de acceso.
- Las solicitudes de vendedor son privadas y empiezan en estado `pending`. Aprobar la solicitud cambia su estado, pero no publica automáticamente productos; el equipo agrega y aprueba los productos por separado.
- Compras y ventas son registros internos. La app no procesa pagos, no captura datos de tarjetas ni crea pedidos de clientes.
- El mapa público muestra los puestos ocupados, disponibles y de información; solo expone el nombre público de la marca asociada a un puesto ocupado.
- Las imágenes de catálogo admiten JPEG, PNG y WebP de hasta 5 MB. Se guardan con nombre aleatorio y se sirven desde `/uploads/`.

## Datos de ejemplo y persistencia

La primera ejecución crea datos de muestra editables para un evento en San Salvador y tres productos aprobados. También crea espacios de ejemplo en el mapa. Cambia la fecha, horario, ubicación y descripción desde **Acceso admin → Evento**. La base SQLite y las fotos se guardan en `data/` por defecto; para moverlas, define `PUNTO_DATA_DIR` antes de iniciar la app. Elimina manualmente los productos de demostración que no correspondan al evento.

## Funciones del panel

- Resumen de productos, postulaciones y totales registrados.
- Aprobar o rechazar postulaciones; publicar, ocultar y eliminar productos.
- Agregar productos con foto, marca, precio y puesto.
- Registrar y borrar compras y ventas con contraparte y notas privadas.
- Agregar, reservar/liberar y borrar puestos con coordenadas en porcentaje (0–100).
- Editar información pública del evento.

El mapa es una distribución visual ilustrativa, no un plano a escala. Ajusta la posición de cada puesto desde sus valores `x` e `y` en la base SQLite; la interfaz administrativa inicial permite crear nuevos espacios y cambiar su estado.

## Estructura

```text
app.py                 servidor HTTP, API, autenticación y SQLite
static/index.html      página y diálogos
static/styles.css      diseño adaptable
static/app.js          interfaz y llamadas a la API
data/                  base de datos y fotos (generados al ejecutar)
```

No se incluyen llaves, cuentas ni registros reales en el proyecto. Antes de abrirlo al público, sustituye el contenido de ejemplo, crea la cuenta de administrador en el servidor y configura HTTPS, copias de seguridad y almacenamiento persistente.
