import os
import threading
import time
import requests
import schedule
from datetime import datetime, timedelta
from flask import Flask, request, render_template, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from slack_bolt import App as BoltApp
from slack_bolt.adapter.flask import SlackRequestHandler
import google.generativeai as genai

# --- CONFIGURAÇÃO ---
app = Flask(__name__)
# Se não tiver secret key, usa uma padrão para não travar
app.secret_key = os.environ.get("SECRET_KEY", "chave_super_secreta_padrao")

# Banco de Dados
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Variáveis de Ambiente
DIRECTUS_URL = os.environ.get("DIRECTUS_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# --- CONFIGURAÇÃO CONDICIONAL DO SLACK ---
# Se você não colocar as chaves no Dokploy, o site liga mesmo assim
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET")

slack_app = None
handler = None

if SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET:
    try:
        slack_app = BoltApp(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)
        handler = SlackRequestHandler(slack_app)
        print("✅ Slack conectado com sucesso!")
    except Exception as e:
        print(f"⚠️ Erro ao configurar Slack: {e}")
else:
    print("⚠️ Slack NÃO configurado (Chaves ausentes). O site vai rodar sem o Bot.")

# Configuração Gemini
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# --- MODELOS ---
class Produto(db.Model):
    __tablename__ = 'produtos'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150))
    quantidade = db.Column(db.Integer)
    localizacao = db.Column(db.String(50))

class Amostra(db.Model):
    __tablename__ = 'amostras'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150))
    status = db.Column(db.String(20))
    vendedor_responsavel = db.Column(db.String(100))
    data_prevista_retorno = db.Column(db.DateTime)

class Log(db.Model):
    __tablename__ = 'logs_movimentacao'
    id = db.Column(db.Integer, primary_key=True)
    acao = db.Column(db.String(50))
    item_id = db.Column(db.Integer)
    usuario_nome = db.Column(db.String(100))
    data_evento = db.Column(db.DateTime, default=datetime.now)

# --- TOOLS GEMINI ---
def tool_atualizar_estoque(nome_produto, quantidade):
    with app.app_context():
        produto = Produto.query.filter(Produto.nome.ilike(f'%{nome_produto}%')).first()
        if not produto: return "Produto não encontrado."
        produto.quantidade -= int(quantidade)
        db.session.add(Log(acao="SLACK_RETIRADA", item_id=produto.id, usuario_nome="SlackBot"))
        db.session.commit()
        return f"Sucesso! {produto.nome} agora tem {produto.quantidade} un."

tools = [tool_atualizar_estoque]
# Só cria o model se tiver chave
model_gemini = None
if GEMINI_API_KEY:
    model_gemini = genai.GenerativeModel('gemini-1.5-flash', tools=tools)

# --- ROTAS UNIFICADAS ---

@app.route('/', methods=['GET', 'POST'])
def index():
    # 1. Se tentar fazer LOGIN
    if request.method == 'POST' and 'login_email' in request.form:
        email = request.form.get('login_email')
        password = request.form.get('login_password')
        try:
            # Login no Directus
            if not DIRECTUS_URL:
                return render_template('index.html', view_mode='login', erro="URL do Directus não configurada.")
                
            resp = requests.post(f"{DIRECTUS_URL}/auth/login", json={"email": email, "password": password})
            if resp.status_code == 200:
                session['user_token'] = resp.json()['data']['access_token']
                session['user_email'] = email
                return redirect(url_for('dashboard'))
            else:
                return render_template('index.html', view_mode='login', erro="Credenciais inválidas.")
        except Exception as e:
            return render_template('index.html', view_mode='login', erro=f"Erro de conexão: {e}")

    # 2. Se já estiver logado
    if 'user_email' in session:
        return redirect(url_for('dashboard'))
    
    return render_template('index.html', view_mode='login')

@app.route('/dashboard')
def dashboard():
    if 'user_email' not in session: return redirect(url_for('index'))
    try:
        produtos = Produto.query.order_by(Produto.nome).all()
        amostras = Amostra.query.order_by(Amostra.nome).all()
        return render_template('index.html', view_mode='dashboard', produtos=produtos, amostras=amostras, user=session['user_email'])
    except Exception as e:
         return f"Erro ao conectar no banco de dados: {e}"

@app.route('/acao/<tipo>/<int:id>', methods=['GET', 'POST'])
def acao(tipo, id):
    if 'user_email' not in session: return redirect(url_for('index'))
    
    msg_sucesso = None
    if request.method == 'POST':
        if tipo == 'produto':
            item = Produto.query.get(id)
            qtd = int(request.form.get('qtd', 0))
            item.quantidade -= qtd
            db.session.add(Log(acao="WEB_RETIRADA", item_id=item.id, usuario_nome=session['user_email']))
            db.session.commit()
            msg_sucesso = f"Retirado {qtd} un de {item.nome}!"
        elif tipo == 'amostra':
            item = Amostra.query.get(id)
            acao_realizada = request.form.get('acao_amostra')
            if acao_realizada == 'retirar':
                item.status = 'EM_RUA'
                item.vendedor_responsavel = session['user_email']
                item.data_prevista_retorno = datetime.now() + timedelta(days=7)
            elif acao_realizada == 'devolver':
                item.status = 'DISPONIVEL'
                item.vendedor_responsavel = None
            db.session.commit()
            msg_sucesso = "Status atualizado com sucesso!"

    if tipo == 'produto':
        item = Produto.query.get_or_404(id)
    else:
        item = Amostra.query.get_or_404(id)
        
    return render_template('index.html', view_mode='acao', item=item, tipo=tipo, msg=msg_sucesso)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# --- SLACK E WORKERS (Só ativa se tiver configurado) ---
@app.route("/slack/events", methods=["POST"])
def slack_events_route():
    if handler:
        return handler.handle(request)
    return "Slack não configurado neste servidor.", 200

# Registra o evento do Slack APENAS se o app existir
if slack_app and model_gemini:
    @slack_app.event("message")
    def handle_slack(body, say):
        if "bot_id" in body["event"]: return
        chat = model_gemini.start_chat(enable_automatic_function_calling=True)
        res = chat.send_message(body["event"].get("text", ""))
        say(res.text)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
