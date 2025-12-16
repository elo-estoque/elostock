import os
import logging
from datetime import datetime, timedelta
import requests
from flask import Flask, request, render_template, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from slack_bolt import App as BoltApp
from slack_bolt.adapter.flask import SlackRequestHandler
import google.generativeai as genai
from google.generativeai.types import FunctionDeclaration, Tool

# --- CONFIGURAÇÃO ---
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "chave_padrao_segura")

# Banco de Dados
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Variáveis
DIRECTUS_URL = os.environ.get("DIRECTUS_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET")

# Configuração Slack
slack_app = None
handler = None
if SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET:
    try:
        slack_app = BoltApp(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)
        handler = SlackRequestHandler(slack_app)
    except Exception as e:
        print(f"⚠️ Slack não configurado: {e}")

# --- MODELOS ATUALIZADOS ---

class Produto(db.Model):
    __tablename__ = 'produtos'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150))
    quantidade = db.Column(db.Integer)
    localizacao = db.Column(db.String(50))
    estoque_minimo = db.Column(db.Integer, default=5)
    updated_at = db.Column(db.DateTime, onupdate=datetime.now)

class Amostra(db.Model):
    __tablename__ = 'amostras'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150))
    codigo_patrimonio = db.Column(db.String(50))
    status = db.Column(db.String(50)) # DISPONIVEL, EM_RUA
    
    # Controle de Showroom
    local_fisico = db.Column(db.String(100)) # Ex: Gaveta 01, Estante B (CRIAR NO DIRECTUS)
    
    # Controle de Saída
    vendedor_responsavel = db.Column(db.String(100))
    cliente_destino = db.Column(db.String(150))
    logradouro = db.Column(db.String(255)) # Endereço de envio
    data_saida = db.Column(db.DateTime)
    data_prevista_retorno = db.Column(db.DateTime)

class Log(db.Model):
    __tablename__ = 'logs_movimentacao'
    id = db.Column(db.Integer, primary_key=True)
    tipo_item = db.Column(db.String(20))
    item_id = db.Column(db.Integer)
    acao = db.Column(db.String(50))
    quantidade = db.Column(db.Integer, default=1)
    usuario_nome = db.Column(db.String(100))
    data_evento = db.Column(db.DateTime, default=datetime.now)

# --- INTELIGÊNCIA ARTIFICIAL ---

def tool_consultar_status():
    """Consulta detalhada de estoque e localização de amostras."""
    with app.app_context():
        # Produtos Críticos
        baixos = Produto.query.filter(Produto.quantidade <= Produto.estoque_minimo).all()
        txt_baixos = ", ".join([f"{p.nome} ({p.quantidade}un)" for p in baixos]) if baixos else "Estoque OK."
        
        # Amostras (Com detalhe de onde estão)
        amostras = Amostra.query.all()
        lista_amostras = []
        for a in amostras:
            if a.status == 'EM_RUA':
                lista_amostras.append(f"🔴 {a.nome} com {a.vendedor_responsavel} (Cliente: {a.cliente_destino}, Volta: {a.data_prevista_retorno})")
            else:
                lista_amostras.append(f"🟢 {a.nome} disponível em {a.local_fisico or 'Local não def.'}")
        
        txt_amostras = "\n".join(lista_amostras)
        
        return f"--- RELATÓRIO ---\n⚠️ Estoque Crítico: {txt_baixos}\n\n📍 Situação Amostras:\n{txt_amostras}"

def tool_atualizar_estoque(nome_produto, qtd_retirada):
    with app.app_context():
        produto = Produto.query.filter(Produto.nome.ilike(f'%{nome_produto}%')).first()
        if not produto: return "Erro: Produto não encontrado."
        produto.quantidade -= int(qtd_retirada)
        db.session.add(Log(tipo_item='produto', item_id=produto.id, acao='SLACK_RETIRADA', quantidade=qtd_retirada, usuario_nome='SlackBot'))
        db.session.commit()
        return f"Feito! {produto.nome} agora tem {produto.quantidade} un."

# Config Gemini
model_gemini = None
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model_gemini = genai.GenerativeModel(
        'gemini-1.5-flash', 
        tools=[tool_consultar_status, tool_atualizar_estoque],
        system_instruction="Você é o SmartStock. Responda sobre onde estão as amostras (gaveta ou cliente) e níveis de estoque."
    )

# --- ROTAS ---

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST' and 'login_email' in request.form:
        email = request.form.get('login_email')
        password = request.form.get('login_password')
        try:
            if not DIRECTUS_URL: return render_template('index.html', view_mode='login', erro="Sem URL Directus")
            resp = requests.post(f"{DIRECTUS_URL}/auth/login", json={"email": email, "password": password})
            if resp.status_code == 200:
                session['user_email'] = email
                return redirect(url_for('dashboard'))
            return render_template('index.html', view_mode='login', erro="Credenciais inválidas.")
        except Exception as e:
            return render_template('index.html', view_mode='login', erro=str(e))

    if 'user_email' in session: return redirect(url_for('dashboard'))
    return render_template('index.html', view_mode='login')

@app.route('/dashboard')
def dashboard():
    if 'user_email' not in session: return redirect(url_for('index'))
    produtos = Produto.query.order_by(Produto.nome).all()
    amostras = Amostra.query.order_by(Amostra.status.desc(), Amostra.nome).all()
    return render_template('index.html', view_mode='dashboard', produtos=produtos, amostras=amostras, user=session['user_email'])

@app.route('/acao/<tipo>/<int:id>', methods=['GET', 'POST'])
def acao(tipo, id):
    if 'user_email' not in session: return redirect(url_for('index'))
    msg_sucesso = None
    
    if tipo == 'produto':
        item = Produto.query.get_or_404(id)
        if request.method == 'POST':
            qtd = int(request.form.get('qtd', 1))
            item.quantidade -= qtd
            db.session.add(Log(tipo_item='produto', item_id=item.id, acao='WEB_RETIRADA', quantidade=qtd, usuario_nome=session['user_email']))
            db.session.commit()
            msg_sucesso = f"Retirado {qtd} un de {item.nome}."

    elif tipo == 'amostra':
        item = Amostra.query.get_or_404(id)
        if request.method == 'POST':
            acao_realizada = request.form.get('acao_amostra')
            
            if acao_realizada == 'retirar':
                # Captura dados logísticos do formulário
                item.status = 'EM_RUA'
                item.vendedor_responsavel = session['user_email']
                item.cliente_destino = request.form.get('cliente_destino')
                item.logradouro = request.form.get('logradouro')
                item.data_saida = datetime.now()
                dias = int(request.form.get('dias_prazo', 7))
                item.data_prevista_retorno = datetime.now() + timedelta(days=dias)
                
            elif acao_realizada == 'devolver':
                item.status = 'DISPONIVEL'
                item.vendedor_responsavel = None
                item.cliente_destino = None
                item.logradouro = None
            
            db.session.add(Log(tipo_item='amostra', item_id=item.id, acao=acao_realizada.upper(), usuario_nome=session['user_email']))
            db.session.commit()
            msg_sucesso = "Logística da amostra atualizada!"

    return render_template('index.html', view_mode='acao', item=item, tipo=tipo, msg=msg_sucesso)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# --- SLACK ---
if slack_app:
    @app.route("/slack/events", methods=["POST"])
    def slack_events(): return handler.handle(request)

    @slack_app.event("message")
    def handle_message(body, say):
        if "bot_id" in body["event"]: return
        if model_gemini:
            chat = model_gemini.start_chat(enable_automatic_function_calling=True)
            res = chat.send_message(body["event"].get("text", ""))
            say(res.text)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
