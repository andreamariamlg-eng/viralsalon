import os
import re
from datetime import datetime
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from anthropic import Anthropic

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "viralsalon-secret-2024")
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL", "sqlite:///viralsalon.db")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

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
    guiones = db.relationship('Guion', backref='autor', lazy=True)

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

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ── HOOKS Y DESARROLLOS ───────────────────────────────────────────────────────

HOOKS = {
    "pregunta_directa": "PREGUNTA DIRECTA: Empieza con una pregunta que el cliente se hace en su cabeza sobre el servicio. Ej: '¿Cuánto dura realmente [el servicio]?' Que sea la pregunta exacta que se están haciendo miles de personas.",
    "mito_destruido": "MITO DESTRUIDO: Empieza EXACTAMENTE así: 'Si te han dicho que [creencia falsa común], te han mentido.' Una frase contundente. Sin más.",
    "comparacion": "COMPARACIÓN: '¿Qué es mejor, [opción A] o [opción B]? Ahora te lo cuento.' Una comparación que el cliente ya se está planteando.",
    "secuencia": "SECUENCIA: '[N] cosas que nadie te cuenta sobre [servicio]' o '[N] errores que veo cada semana en mi salón.' El número genera curiosidad.",
    "dato_importante": "DATO IMPORTANTE: 'Si tienes [situación específica], no puedes hacerte [servicio] sin antes [condición].' O un dato sorprendente del sector que nadie comparte."
}

DEVS = {
    "experto": "EXPERTO: Responde la pregunta con datos reales, hechos técnicos o estadísticas del sector. Posiciónate como la referencia. Frases como 'La realidad es que...', 'Lo que la mayoría no sabe es que...', 'En mi salón llevo X años viendo que...'. Directo, sin rodeos, máximo 5-6 frases.",
    "emocional": "EMOCIONAL: Cuenta una historia breve y real (tuya o de una clienta) con la que el cliente ideal se sienta identificado. 'La semana pasada me llegó una clienta que...'. Hazle sentir que ese video es para ella. Máximo 5-6 frases."
}

# ── RUTAS DE AUTENTICACIÓN ────────────────────────────────────────────────────

@app.route("/registro", methods=["GET", "POST"])
def registro():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
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
            flash("Ya existe una cuenta con ese email.", "error")
            return redirect(url_for("registro"))
        user = User(
            email=email,
            password=generate_password_hash(password),
            nombre_salon=nombre_salon,
            especialidad=especialidad,
            ciudad=ciudad
        )
        db.session.add(user)
        db.session.commit()
        login_user(user)
        return redirect(url_for("index"))
    return render_template("registro.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password, password):
            flash("Email o contraseña incorrectos.", "error")
            return redirect(url_for("login"))
        login_user(user)
        return redirect(url_for("index"))
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

# ── RUTAS PRINCIPALES ─────────────────────────────────────────────────────────

@app.route("/")
def landing():
    if current_user.is_authenticated:
        return redirect(url_for("generador"))
    return render_template("landing.html")

@app.route("/app")
@login_required
def generador():
    return render_template("index.html", user=current_user)

@app.route("/biblioteca")
@login_required
def biblioteca():
    guiones = Guion.query.filter_by(user_id=current_user.id).order_by(Guion.creado.desc()).all()
    return render_template("biblioteca.html", guiones=guiones, user=current_user)

@app.route("/planificador")
@login_required
def planificador():
    guiones = Guion.query.filter_by(user_id=current_user.id).order_by(Guion.creado.desc()).all()
    return render_template("planificador.html", guiones=guiones, user=current_user)

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

    data = request.json
    nombre = data.get("nombre") or current_user.nombre_salon
    esp = data.get("especialidad") or current_user.especialidad or ""
    ciudad = data.get("ciudad") or current_user.ciudad or "España"
    servicio = data.get("servicio", "")
    cliente = data.get("cliente_ideal", "")
    pregunta = data.get("pregunta", "")
    hook = data.get("hook", "pregunta_directa")
    dev = data.get("desarrollo", "experto")
    palabra = data.get("palabra_cta", "INFO")
    regalo = data.get("regalo", "toda la información")

    if not all([esp, servicio, cliente, pregunta, palabra, regalo]):
        return jsonify({"error": "Faltan campos obligatorios"}), 400

    prompt = f"""Eres el sistema de marketing de contenidos más avanzado para salones de belleza. Tu metodología genera guiones para Reels que CONVIERTEN espectadores en clientes reales, no solo likes.

DATOS DEL SALÓN:
Nombre: {nombre}
Especialidad: {esp}
Ciudad: {ciudad}
Servicio del video: {servicio}
Cliente ideal: {cliente}
Pregunta que frena al cliente a comprar: "{pregunta}"

REGLA ABSOLUTA: Cada frase del guión debe existir por una razón. Si se puede quitar sin perder impacto, se quita. El guión entero gira alrededor de responder UNA sola pregunta.

HOOK A USAR: {HOOKS.get(hook, HOOKS["pregunta_directa"])}

DESARROLLO A USAR: {DEVS.get(dev, DEVS["experto"])}

CTA SIEMPRE ASÍ: "Comenta {palabra} y te mando {regalo}."
NUNCA: sígueme, dale like, comparte, suscríbete.

RESPONDE ÚNICAMENTE en este formato exacto:

[HOOK]
(el hook aquí, máximo 2-3 frases)

[DESARROLLO]
(el desarrollo aquí, máximo 5-6 frases)

[CTA]
(el cta aquí, 1-2 frases)

REGLAS DE ESCRITURA:
- Español natural de España, primera persona
- Sin emojis, sin asteriscos, sin guiones al inicio de frases
- Sin introducción, sin explicación, solo el guión
- Que suene como alguien hablando, no escribiendo
- Máximo 110 palabras en total"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
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

        # Guardar en base de datos
        guion = Guion(
            user_id=current_user.id,
            servicio=servicio,
            hook_tipo=hook,
            dev_tipo=dev,
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
            "guion_id": guion.id
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── INICIO ────────────────────────────────────────────────────────────────────

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)
