import os
import re
from flask import Flask, request, jsonify, render_template
from anthropic import Anthropic

app = Flask(__name__)
client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

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

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/generar", methods=["POST"])
def generar():
    data = request.json
    nombre = data.get("nombre", "el salón")
    esp = data.get("especialidad", "")
    ciudad = data.get("ciudad", "España")
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

        return jsonify({
            "hook": hm.group(1).strip() if hm else "",
            "desarrollo": dm.group(1).strip() if dm else "",
            "cta": cm.group(1).strip() if cm else f"Comenta {palabra} y te mando {regalo}.",
            "palabras": len(texto.replace('[HOOK]','').replace('[DESARROLLO]','').replace('[CTA]','').split())
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)
