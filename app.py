import os
import re
import stripe
import secrets
import resend
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from anthropic import Anthropic

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "viralsalon-secret-2024")
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL", "sqlite:///viralsalon.db")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# ── MODELOS ──────────────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre_salon = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(256), nullable=False)
    especialidad = db.Column(db.String(120))
    ciudad = db.Column(db.String(120))
    creado = db.Column(db.DateTime, default=datetime.utcnow)
    stripe_customer_id = db.Column(db.String(120), nullable=True)
    subscription_status = db.Column(db.String(50), default="none")
    reset_token = db.Column(db.String(100), nullable=True)
    reset_token_expiry = db.Column(db.DateTime, nullable=True)
    # Onboarding
    tipo_negocio = db.Column(db.String(100), nullable=True)
    servicios_propios = db.Column(db.Text, nullable=True)  # JSON lista de servicios
    onboarding_completo = db.Column(db.Boolean, default=False)
    guiones = db.relationship('Guion', backref='autor', lazy=True)

    def puede_acceder(self):
        return self.subscription_status in ['active', 'trialing']

    def get_servicios_lista(self):
        """Devuelve la lista de servicios propios como lista Python."""
        if not self.servicios_propios:
            return []
        import json
        try:
            return json.loads(self.servicios_propios)
        except Exception:
            return []

class Guion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    servicio = db.Column(db.String(200))
    hook_tipo = db.Column(db.String(50))
    dev_tipo = db.Column(db.String(50))
    hook = db.Column(db.Text)
    desarrollo = db.Column(db.Text)
    cta = db.Column(db.Text)
    palabras = db.Column(db.Integer)
    publicado = db.Column(db.Boolean, default=False)
    fecha_publicacion = db.Column(db.Date, nullable=True)
    notas = db.Column(db.Text, nullable=True)
    creado = db.Column(db.DateTime, default=datetime.utcnow)
    # Métricas
    views = db.Column(db.Integer, nullable=True)
    comentarios = db.Column(db.Integer, nullable=True)
    guardados = db.Column(db.Integer, nullable=True)
    compartidos = db.Column(db.Integer, nullable=True)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ── HOOKS Y TONOS ───────────────────────────────────────────────────────

HOOKS = {
    "pregunta_directa": "El hook es una PREGUNTA DIRECTA. La pregunta exacta que se hace la clienta en su cabeza. Ej: '¿Quieres hacerte las uñas pero no sabes qué pedir?' o '¿Sigues usando cuchilla para depilarte?' Que sea tan concreta que quien la oiga piense 'eso me pasa a mí'.",
    "mito_destruido": "El hook destruye un MITO. Empieza así: 'Si tu amiga te ha dicho que [cosa falsa], te ha mentido.' o 'No me digas que todavía crees que [mito común].' Una frase que sorprenda y genere ganas de seguir viendo.",
    "comparacion": "El hook plantea una COMPARACIÓN que la clienta ya se está haciendo. '¿Qué es mejor, [opción A] o [opción B]? Ahora mismo te lo cuento.' Que sea una duda real que tienen antes de pedir cita.",
    "secuencia": "El hook anuncia una LISTA corta. '[N] cosas que nadie te cuenta sobre [servicio]' o '[N] errores que veo cada semana en mi salón y cómo evitarlos.' El número despierta la curiosidad.",
    "dato_importante": "El hook da un DATO que sorprende. 'Lo que nadie te cuenta sobre [servicio] es que...' o 'Aparte del dinero que te gastas en [alternativa peor], no sabes el daño que te haces.' Algo que no esperaban oír."
}

TONOS = {
    "historia_real": {
        "instruccion": "El desarrollo cuenta la historia de una clienta real. Usa un nombre femenino natural (elige uno diferente cada vez de esta lista variada: Sara, Laura, Carmen, Elena, Paula, Marta, Lucía, Ana, Isabel, Rosa, Nuria, Cristina, Sofía, Raquel, Mónica). Estructura: qué le pasaba → qué descubrió o hizo → cómo está ahora. Que quien lo vea piense 'eso me pasa exactamente a mí'. Sin nombres de marcas ni términos técnicos. Como si se lo contaras a una amiga.",
        "con_nombre": True
    },
    "directa": {
        "instruccion": "El desarrollo responde la pregunta de forma directa y clara, como si le explicaras algo a una amiga en un café. Sin rodeos. Sin palabras difíciles. Usa frases cortas. Puede ser explicando cómo funciona algo, qué tiene que pedir, cuándo sirve o no sirve el servicio, o qué tiene que saber antes de venir. Todo en palabras del día a día.",
        "con_nombre": False
    },
    "revelacion": {
        "instruccion": "El desarrollo revela algo que normalmente no se sabe o que la gente tiene mal entendido. 'Lo que nadie te cuenta es que...' o 'La mayoría no lo sabe pero...' Genera curiosidad y posiciona al salón como el que realmente sabe. Sin tecnicismos, todo en palabras sencillas. Puede o no mencionar una clienta brevemente.",
        "con_nombre": False
    },
    "inspiracional": {
        "instruccion": "El desarrollo lleva a la clienta a imaginarse ya con el resultado. Que sienta el deseo de tenerlo. Habla de cómo se va a sentir, qué va a cambiar en su día a día, qué dirán los demás. Conecta con el deseo, no con el miedo. Nada técnico. Puede mencionar brevemente una clienta que ya lo ha vivido.",
        "con_nombre": False
    }
}

# ── HELPER EMAIL ─────────────────────────────────────────────────────────────

resend.api_key = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = "ViralSalon <hola@andreamariaoficial.es>"

def enviar_email(destinatario, asunto, cuerpo_html):
    """Envía un email usando Resend."""
    if not resend.api_key:
        return False
    try:
        resend.Emails.send({
            "from": FROM_EMAIL,
            "to": destinatario,
            "subject": asunto,
            "html": cuerpo_html,
            "reply_to": "viralsalon.app@gmail.com"
        })
        return True
    except Exception:
        return False

def enviar_email_async(destinatario, asunto, cuerpo_html):
    """Envía email en segundo plano para no bloquear la web."""
    t = threading.Thread(target=enviar_email, args=(destinatario, asunto, cuerpo_html))
    t.daemon = True
    t.start()

def enviar_bienvenida(user):
    asunto = "Ya eres parte de ViralSalon"
    cuerpo = f"""
    <div style="font-family:Arial,sans-serif;background:#0a0a0a;color:#fff;padding:48px 40px;max-width:580px;margin:0 auto;border-radius:16px;">
      <p style="color:#C9A84C;font-size:0.8rem;font-weight:700;letter-spacing:3px;text-transform:uppercase;margin-bottom:32px;">ViralSalon · by Andrea Maria</p>

      <h2 style="font-size:1.6rem;font-weight:900;margin-bottom:20px;line-height:1.3;">
        ¡Bienvenida, {user.nombre_salon}! 🥂
      </h2>

      <p style="color:rgba(255,255,255,0.85);line-height:1.8;margin-bottom:16px;font-size:0.97rem;">
        Hola, soy Andrea. Quería escribirte yo personalmente para decirte una cosa:
        <strong>me alegra muchísimo que estés aquí.</strong>
      </p>

      <p style="color:rgba(255,255,255,0.85);line-height:1.8;margin-bottom:16px;font-size:0.97rem;">
        Sé lo que es tener un salón lleno de talento y no saber cómo contarlo en redes.
        Sé lo que es grabarte un vídeo, subirlo y que no pase nada. Esa sensación de
        "¿para qué sirve esto?" la he vivido yo también.
      </p>

      <p style="color:rgba(255,255,255,0.85);line-height:1.8;margin-bottom:24px;font-size:0.97rem;">
        Por eso creé ViralSalon. Para que en dos minutos tengas un guión que suena de
        verdad, que conecta con tus clientas, y que les da ganas de pedir cita.
        <strong>Sin complicarte la vida.</strong>
      </p>

      <p style="color:rgba(255,255,255,0.85);line-height:1.8;margin-bottom:32px;font-size:0.97rem;">
        Ya tienes todo listo. Entra, elige tu servicio y genera tu primer guión ahora:
      </p>

      <a href="https://viralsalon.andreamariaoficial.es/app"
         style="display:inline-block;background:linear-gradient(135deg,#E8CB7A,#C9A84C,#8a6c28);color:#000;padding:16px 32px;border-radius:12px;font-weight:900;text-decoration:none;font-size:1rem;">
        Crear mi primer guión →
      </a>

      <div style="margin-top:40px;padding-top:24px;border-top:1px solid rgba(201,168,76,0.2);">
        <p style="color:rgba(255,255,255,0.7);line-height:1.7;font-size:0.9rem;margin-bottom:8px;">
          Un abrazo enorme,
        </p>
        <p style="color:#C9A84C;font-weight:900;font-size:1rem;margin:0;">Andrea Maria</p>
        <p style="color:rgba(255,255,255,0.4);font-size:0.8rem;margin-top:4px;">Fundadora de ViralSalon</p>
      </div>

      <p style="color:rgba(255,255,255,0.25);font-size:0.75rem;margin-top:32px;">
        ¿Tienes alguna duda? Responde a este email y te ayudo personalmente.
      </p>
    </div>
    """
    enviar_email_async(user.email, asunto, cuerpo)

def enviar_reset_password(user, token):
    link = f"https://viralsalon.andreamariaoficial.es/reset/{token}"
    asunto = "Recuperar contraseña — ViralSalon"
    cuerpo = f"""
    <div style="font-family:Arial,sans-serif;background:#000;color:#fff;padding:40px;max-width:600px;margin:0 auto;">
      <h1 style="color:#C9A84C;font-size:2rem;margin-bottom:8px;">ViralSalon</h1>
      <p style="color:rgba(255,255,255,0.6);font-size:0.85rem;margin-bottom:32px;">by Andrea Maria</p>
      <h2 style="font-size:1.4rem;margin-bottom:16px;">Recuperar contraseña</h2>
      <p style="color:rgba(255,255,255,0.8);line-height:1.7;margin-bottom:20px;">
        Hemos recibido una solicitud para restablecer la contraseña de tu cuenta.<br><br>
        Haz clic en el botón de abajo. El enlace caduca en <strong>1 hora</strong>.
      </p>
      <a href="{link}"
         style="display:inline-block;background:linear-gradient(135deg,#E8CB7A,#C9A84C,#8a6c28);color:#000;padding:14px 28px;border-radius:10px;font-weight:900;text-decoration:none;font-size:1rem;">
        Cambiar contraseña →
      </a>
      <p style="color:rgba(255,255,255,0.4);font-size:0.8rem;margin-top:32px;">
        Si no solicitaste esto, ignora este email.<br>
        ViralSalon · by Andrea Maria
      </p>
    </div>
    """
    enviar_email_async(user.email, asunto, cuerpo)

# ── HELPER STRIPE ─────────────────────────────────────────────────────────────

def buscar_suscripcion_stripe(email):
    """Busca si el email tiene una suscripción activa o en trial en Stripe."""
    try:
        customers = stripe.Customer.list(email=email, limit=1)
        if not customers.data:
            return None, None
        customer = customers.data[0]
        subs = stripe.Subscription.list(customer=customer.id, limit=1)
        if not subs.data:
            return customer.id, "none"
        sub = subs.data[0]
        return customer.id, sub.status
    except Exception:
        return None, None

# ── RUTAS DE AUTENTICACIÓN ────────────────────────────────────────────────────

@app.route("/registro", methods=["GET", "POST"])
def registro():
    if current_user.is_authenticated:
        return redirect(url_for("generador"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        nombre_salon = request.form.get("nombre_salon", "").strip()
        especialidad = request.form.get("especialidad", "").strip()
        ciudad = request.form.get("ciudad", "").strip()

        if not all([email, password, nombre_salon]):
            flash("Rellena todos los campos obligatorios.", "error")
            return redirect(url_for("registro"))

        if User.query.filter_by(email=email).first():
            flash("Ya existe una cuenta con ese email. Inicia sesión.", "error")
            return redirect(url_for("login"))

        # Verificar suscripción en Stripe
        customer_id, status = buscar_suscripcion_stripe(email)

        if status not in ['active', 'trialing']:
            flash("No encontramos una suscripción activa con ese email. Empieza tu prueba gratuita primero.", "error")
            return redirect(url_for("registro"))

        user = User(
            email=email,
            password=generate_password_hash(password),
            nombre_salon=nombre_salon,
            especialidad=especialidad,
            ciudad=ciudad,
            stripe_customer_id=customer_id,
            subscription_status=status,
            onboarding_completo=False
        )
        db.session.add(user)
        db.session.commit()
        enviar_bienvenida(user)
        login_user(user)
        return redirect(url_for("onboarding"))  # Primera vez → onboarding

    return render_template("registro.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("generador"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password, password):
            flash("Email o contraseña incorrectos.", "error")
            return redirect(url_for("login"))
        # Actualizar estado suscripción al hacer login
        if user.stripe_customer_id:
            try:
                subs = stripe.Subscription.list(customer=user.stripe_customer_id, limit=1)
                if subs.data:
                    user.subscription_status = subs.data[0].status
                    db.session.commit()
            except Exception:
                pass
        login_user(user)
        if not user.puede_acceder():
            flash("Tu suscripción ha caducado. Renuévala para continuar.", "error")
            return redirect(url_for("suscripcion_caducada"))
        # Si no ha completado el onboarding, mandarlo ahí
        if not user.onboarding_completo:
            return redirect(url_for("onboarding"))
        return redirect(url_for("generador"))
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/suscripcion-caducada")
def suscripcion_caducada():
    return render_template("suscripcion_caducada.html")

@app.route("/olvide-contrasena", methods=["GET", "POST"])
def olvide_contrasena():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user:
            token = secrets.token_urlsafe(32)
            user.reset_token = token
            user.reset_token_expiry = datetime.utcnow() + timedelta(hours=1)
            db.session.commit()
            enviar_reset_password(user, token)
        # Siempre mostramos el mismo mensaje por seguridad
        flash("Si ese email está registrado, recibirás un enlace en unos minutos.", "ok")
        return redirect(url_for("olvide_contrasena"))
    return render_template("olvide_contrasena.html")

@app.route("/reset/<token>", methods=["GET", "POST"])
def reset_contrasena(token):
    user = User.query.filter_by(reset_token=token).first()
    if not user or not user.reset_token_expiry or user.reset_token_expiry < datetime.utcnow():
        flash("El enlace no es válido o ha caducado.", "error")
        return redirect(url_for("login"))
    if request.method == "POST":
        nueva = request.form.get("password", "")
        confirmar = request.form.get("confirmar", "")
        if len(nueva) < 8:
            flash("La contraseña debe tener al menos 8 caracteres.", "error")
            return redirect(url_for("reset_contrasena", token=token))
        if nueva != confirmar:
            flash("Las contraseñas no coinciden.", "error")
            return redirect(url_for("reset_contrasena", token=token))
        user.password = generate_password_hash(nueva)
        user.reset_token = None
        user.reset_token_expiry = None
        db.session.commit()
        flash("Contraseña cambiada. Ya puedes iniciar sesión.", "ok")
        return redirect(url_for("login"))
    return render_template("reset_contrasena.html", token=token)

# ── WEBHOOK STRIPE ────────────────────────────────────────────────────────────

@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception:
        return jsonify({"error": "Invalid signature"}), 400

    if event["type"] in ["customer.subscription.updated", "customer.subscription.created"]:
        sub = event["data"]["object"]
        customer_id = sub["customer"]
        status = sub["status"]
        user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if user:
            user.subscription_status = status
            db.session.commit()

    elif event["type"] == "customer.subscription.deleted":
        sub = event["data"]["object"]
        customer_id = sub["customer"]
        user = User.query.filter_by(stripe_customer_id=customer_id).first()
        if user:
            user.subscription_status = "canceled"
            db.session.commit()

    return jsonify({"ok": True})

# ── ONBOARDING ────────────────────────────────────────────────────────────────

@app.route("/onboarding", methods=["GET", "POST"])
@login_required
def onboarding():
    if not current_user.puede_acceder():
        return redirect(url_for("suscripcion_caducada"))
    if request.method == "POST":
        import json
        tipo = request.form.get("tipo_negocio", "").strip()
        servicios = request.form.getlist("servicios")
        if tipo:
            current_user.tipo_negocio = tipo
        if servicios:
            current_user.servicios_propios = json.dumps(servicios)
        current_user.onboarding_completo = True
        db.session.commit()
        return redirect(url_for("generador"))
    return render_template("onboarding.html", user=current_user)

# ── RUTAS PRINCIPALES ─────────────────────────────────────────────────────────

@app.route("/")
def landing():
    if current_user.is_authenticated:
        return redirect(url_for("generador"))
    return render_template("landing.html")

@app.route("/app")
@login_required
def generador():
    if not current_user.puede_acceder():
        return redirect(url_for("suscripcion_caducada"))
    servicios_lista = current_user.get_servicios_lista()
    return render_template("index.html", user=current_user, servicios_sugeridos=servicios_lista)

@app.route("/biblioteca")
@login_required
def biblioteca():
    if not current_user.puede_acceder():
        return redirect(url_for("suscripcion_caducada"))
    guiones = Guion.query.filter_by(user_id=current_user.id).order_by(Guion.creado.desc()).all()
    return render_template("biblioteca.html", guiones=guiones, user=current_user)

@app.route("/planificador")
@login_required
def planificador():
    if not current_user.puede_acceder():
        return redirect(url_for("suscripcion_caducada"))
    guiones = Guion.query.filter_by(user_id=current_user.id).order_by(Guion.creado.desc()).all()
    return render_template("planificador.html", guiones=guiones, user=current_user)

@app.route("/manychat")
@login_required
def manychat():
    if not current_user.puede_acceder():
        return redirect(url_for("suscripcion_caducada"))
    return render_template("manychat.html", user=current_user)

@app.route("/guion/<int:guion_id>/publicar", methods=["POST"])
@login_required
def marcar_publicado(guion_id):
    guion = Guion.query.filter_by(id=guion_id, user_id=current_user.id).first_or_404()
    data = request.json
    guion.publicado = data.get("publicado", True)
    fecha = data.get("fecha_publicacion")
    if fecha:
        guion.fecha_publicacion = datetime.strptime(fecha, "%Y-%m-%d").date()
    guion.notas = data.get("notas", guion.notas)
    db.session.commit()
    return jsonify({"ok": True})

@app.route("/guion/<int:guion_id>/metricas", methods=["POST"])
@login_required
def guardar_metricas(guion_id):
    guion = Guion.query.filter_by(id=guion_id, user_id=current_user.id).first_or_404()
    data = request.json
    if data.get("views") is not None:
        guion.views = int(data["views"]) if data["views"] != "" else None
    if data.get("comentarios") is not None:
        guion.comentarios = int(data["comentarios"]) if data["comentarios"] != "" else None
    if data.get("guardados") is not None:
        guion.guardados = int(data["guardados"]) if data["guardados"] != "" else None
    if data.get("compartidos") is not None:
        guion.compartidos = int(data["compartidos"]) if data["compartidos"] != "" else None
    db.session.commit()
    return jsonify({"ok": True})

@app.route("/guion/<int:guion_id>", methods=["DELETE"])
@login_required
def eliminar_guion(guion_id):
    guion = Guion.query.filter_by(id=guion_id, user_id=current_user.id).first_or_404()
    db.session.delete(guion)
    db.session.commit()
    return jsonify({"ok": True})

# ── GENERADOR ─────────────────────────────────────────────────────────────────

@app.route("/generar", methods=["POST"])
@login_required
def generar():
    if not current_user.puede_acceder():
        return jsonify({"error": "Suscripción no activa"}), 403

    data = request.json
    nombre = data.get("nombre") or current_user.nombre_salon
    esp = data.get("especialidad") or current_user.especialidad or ""
    ciudad = data.get("ciudad") or current_user.ciudad or "España"
    servicio = data.get("servicio", "")
    cliente = data.get("cliente_ideal", "")
    pregunta = data.get("pregunta", "")
    hook = data.get("hook", "pregunta_directa")
    tono = data.get("tono", "directa")
    duracion = data.get("duracion", "corto")
    palabra = data.get("palabra_cta", "INFO")
    regalo = data.get("regalo", "toda la información")

    if not all([esp, servicio, cliente, pregunta, palabra, regalo]):
        return jsonify({"error": "Faltan campos obligatorios"}), 400

    tono_data = TONOS.get(tono, TONOS["directa"])
    tono_instruccion = tono_data["instruccion"]

    if duracion == "largo":
        palabras_max = 150
        duracion_texto = "60 segundos"
        palabras_hook = "2-3 frases"
        palabras_dev = "6-8 frases"
    else:
        palabras_max = 85
        duracion_texto = "30-40 segundos"
        palabras_hook = "1-2 frases"
        palabras_dev = "3-4 frases"

    prompt = f"""Eres la voz de un salón de {esp} en {ciudad}. Tu trabajo es escribir un guión para un Reel de Instagram que suene completamente natural, como si la dueña del salón se lo estuviera contando a una amiga tomando un café.

DATOS:
Salón: {nombre}
Servicio: {servicio}
Clienta ideal: {cliente}
Pregunta que le frena a comprar: "{pregunta}"

GUIONES DE REFERENCIA QUE FUNCIONAN DE VERDAD:
---
Guión 1 (uñas, directo):
"¿Quieres hacerte las uñas, pero no sabes qué pedir? Después de ver este vídeo, ya vas a saberlo. Pide permanente si tu uña es fuerte y no trabajas mucho con las manos. Pide refuerzo si tus uñas son más débiles o usas mucho las manos en tu día a día. Pide acrílico o gel si tus uñas son muy cortas y te las dañas. Comenta INFO y te envío la lista de precios completa."
---
Guión 2 (peluquería, historia real):
"Si tu amiga te ha dicho que no hay manera de recuperar la fuerza de tu pelo, te ha mentido. El otro día vino Sara diciéndome que odiaba recogerse el pelo porque se le veía demasiado fino. Le hablé del bótox capilar, no sabía ni lo que era. Hoy lleva tres sesiones y está alucinada con su pelo. Si quieres saber cómo te puede ayudar a ti, comenta BÓTOX y te envío toda la info."
---
Guión 3 (depilación láser, revelación):
"No me digas que sigues usando cuchilla para depilarte. Aparte del dinero que te gastas, no sabes el daño que le haces a tu piel, y sobre todo la pereza que da. El otro día hablé con Laura sobre esto, y cuando le dije el precio del láser de cuerpo completo, no se lo podía creer. Pidió cita al día siguiente. No le dolió nada y ya en la segunda sesión ha notado un cambio enorme. ¿Quieres que te sorprenda a ti también? Comenta LÁSER y te mando un regalo."
---

HOOK A ESCRIBIR: {HOOKS.get(hook, HOOKS["pregunta_directa"])}
Longitud del hook: {palabras_hook}.

DESARROLLO A ESCRIBIR: {tono_instruccion}
Longitud del desarrollo: {palabras_dev}.

CTA: Termina SIEMPRE así (natural, no robótico): "Comenta {palabra} y te {regalo}." o una variación muy natural de esto.
PROHIBIDO en el CTA: sígueme, dale like, comparte, suscríbete.

DURACIÓN OBJETIVO: {duracion_texto} — máximo {palabras_max} palabras en total.

RESPONDE SOLO en este formato:

[HOOK]
(el hook)

[DESARROLLO]
(el desarrollo)

[CTA]
(el cta)

REGLAS QUE NO SE PUEDEN ROMPER:
1. Sin palabras técnicas del sector. Si hay que nombrar el servicio, usa el nombre más simple posible.
2. Sin emojis, sin asteriscos, sin guiones al inicio de frases.
3. Que suene a persona hablando, no a texto escrito. Frases cortas. Pausas naturales.
4. Que lo entienda cualquier persona, de cualquier edad, sin saber nada del sector.
5. Cada frase tiene que estar por una razón. Si se puede quitar sin perder nada, se quita.
6. Sin introducción ni explicación. Solo el guión."""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}]
        )
        texto = response.content[0].text

        hm = re.search(r'\[HOOK\]([\s\S]*?)(?=\[DESARROLLO\])', texto, re.IGNORECASE)
        dm = re.search(r'\[DESARROLLO\]([\s\S]*?)(?=\[CTA\])', texto, re.IGNORECASE)
        cm = re.search(r'\[CTA\]([\s\S]*?)$', texto, re.IGNORECASE)

        hook_txt = hm.group(1).strip() if hm else ""
        dev_txt = dm.group(1).strip() if dm else ""
        cta_txt = cm.group(1).strip() if cm else f"Comenta {palabra} y te mando {regalo}."
        palabras = len(texto.replace('[HOOK]','').replace('[DESARROLLO]','').replace('[CTA]','').split())

        guion = Guion(
            user_id=current_user.id,
            servicio=servicio,
            hook_tipo=hook,
            dev_tipo=tono,
            hook=hook_txt,
            desarrollo=dev_txt,
            cta=cta_txt,
            palabras=palabras
        )
        db.session.add(guion)
        db.session.commit()

        return jsonify({
            "hook": hook_txt,
            "desarrollo": dev_txt,
            "cta": cta_txt,
            "palabras": palabras,
            "guion_id": guion.id,
            "palabra_cta": palabra,
            "servicio": servicio
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── CANCELACIÓN SUSCRIPCIÓN ───────────────────────────────────────────────────

@app.route("/cancelar", methods=["GET", "POST"])
@login_required
def cancelar_suscripcion():
    if request.method == "POST":
        try:
            if current_user.stripe_customer_id:
                subs = stripe.Subscription.list(customer=current_user.stripe_customer_id, limit=1)
                if subs.data:
                    stripe.Subscription.modify(subs.data[0].id, cancel_at_period_end=True)
                    db.session.commit()
                    flash("Tu suscripción se cancelará al final del período. Puedes seguir usando ViralSalon hasta entonces.", "ok")
                    return redirect(url_for("generador"))
            flash("No encontramos una suscripción activa.", "error")
        except Exception as e:
            flash("Hubo un problema al cancelar. Escríbenos a hola@andreamariaoficial.es y lo resolvemos.", "error")
        return redirect(url_for("cancelar_suscripcion"))
    return render_template("cancelar.html", user=current_user)

# ── INICIO ────────────────────────────────────────────────────────────────────

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)
