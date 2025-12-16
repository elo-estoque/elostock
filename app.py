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
# Usa a chave configurada no painel ou fallback
app.secret_key = os.environ.get("SECRET_KEY", "chave_padrao_segura")

# Banco de Dados (Pega da sua variável de ambiente do print)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Variáveis
DIRECTUS_URL = os.environ.get("DIRECTUS_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# --- CONFIGURAÇÃO SLACK (Opcional) ---
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET")

slack_app = None
handler = None

if SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET:
    try:
        slack_app = BoltApp(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)
        handler = SlackRequestHandler(slack_app)
        print("✅ Slack conectado!")
    except Exception as e:
        print(f"⚠️ Slack não configurado: {e}")

# --- MODELOS SINCRONIZADOS COM DIRECTUS ---

class Produto(db.Model):
    __tablename__ = 'produtos'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150))
    quantidade = db.Column(db.Integer)
    localizacao = db.Column(db.String(50))
    estoque_minimo = db.Column(db.Integer, default=5) # Novo campo detectado
    updated_at = db.Column(db.DateTime, onupdate=datetime.now)

class Amostra(db.Model):
    __tablename__ = 'amostras'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150))
    status = db.Column(db.String(50)) # DISPONIVEL, EM_RUA
    vendedor_responsavel = db.Column(db.String(100))
    cliente_destino = db.Column(db.String(150)) # Novo campo
    codigo_patrimonio = db.Column(db.String(50)) # Novo campo
    data_prevista_retorno = db.Column(db.DateTime)
    data_saida = db.Column(db.DateTime)

class Log(db.Model):
    __tablename__ = 'logs_movimentacao'
    id = db.Column(db.Integer, primary_key=True)
    tipo_item = db.Column(db.String(20)) # 'produto' ou 'amostra'
    item_id = db.Column(db.Integer)
    acao = db.Column(db.String(50))
    quantidade = db.Column(db.Integer, default=1)
    usuario_slack_id = db.Column(db.String(50))
    usuario_nome = db.Column(db.String(100))
    data_evento = db.Column(db.DateTime, default=datetime.now)

# --- INTELIGÊNCIA ARTIFICIAL (GEMINI) ---

# 1. Tool para a IA ler o banco (Consultiva)
def tool_consultar_status():
    """Consulta o status atual de produtos com estoque baixo e amostras emprestadas."""
    with app.app_context():
        # Verifica produtos acabando
        baixos = Produto.query.filter(Produto.quantidade <= Produto.estoque_minimo).all()
        txt_baixos = ", ".join([f"{p.nome} ({p.quantidade} un)" for p in baixos]) if baixos else "Nenhum produto crítico."
        
        # Verifica amostras na rua
        rua = Amostra.query.filter(Amostra.status == 'EM_RUA').all()
        txt_rua = ", ".join([f"{a.nome} com {a.vendedor_responsavel} (Volta: {a.data_prevista_retorno})" for a in rua]) if rua else "Todas as amostras estão aqui."
        
        return f"RELATÓRIO:\nEstoque Baixo: {txt_baixos}\nAmostras em Rua: {txt_rua}"

# 2. Tool para a IA realizar ação (Executiva)
def tool_atualizar_estoque(nome_produto, qtd_retirada):
    """Atualiza o estoque de um produto pelo nome."""
    with app.app_context():
        produto = Produto.query.filter(Produto.nome.ilike(f'%{nome_produto}%')).first()
        if not produto: return "Erro: Produto não encontrado."
        
        produto.quantidade -= int(qtd_retirada)
        
        # Log completo conforme Directus pede
        log = Log(
            tipo_item='produto',
            item_id=produto.id,
            acao='SLACK_RETIRADA',
            quantidade=int(qtd_retirada),
            usuario_nome='SlackBot'
        )
        db.session.add(log)
        db.session.commit()
        
        aviso = "⚠️ ESTOQUE BAIXO!" if produto.quantidade <= (produto.estoque_minimo or 0) else ""
        return f"Feito! {produto.nome} agora tem {produto.quantidade} un. {aviso}"

# Configuração do Gemini
model_gemini = None
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    tools = [tool_consultar_status, tool_atualizar_estoque]
    
    # Instrução de Sistema: Define a personalidade
    system_instruction = """
    Você é o SmartStock, um gerente de almoxarifado eficiente e prestativo.
    1. Se o usuário perguntar "como está o estoque" ou "quem está com a amostra", USE a tool_consultar_status.
    2. Se o usuário pedir para "pegar" ou "retirar" algo, USE a tool_atualizar_estoque.
    Seja breve e profissional.
    """
    
    model_gemini = genai.GenerativeModel(
        'gemini-1.5-flash', 
        tools=tools,
        system_instruction=system_instruction
    )

# --- ROTAS WEB ---

@app.route('/', methods=['GET', 'POST'])
def index():
    # Lógica de Login via Directus
    if request.method == 'POST' and 'login_email' in request.form:
        email = request.form.get('login_email')
        password = request.form.get('login_password')
        try:
            if not DIRECTUS_URL:
                return render_template('index.html', view_mode='login', erro="URL Directus não configurada")
            
            # Auth no Directus
            resp = requests.post(f"{DIRECTUS_URL}/auth/login", json={"email": email, "password": password})
            if resp.status_code == 200:
                session['user_token'] = resp.json()['data']['access_token']
                session['user_email'] = email
                return redirect(url_for('dashboard'))
            else:
                return render_template('index.html', view_mode='login', erro="Acesso negado.")
        except Exception as e:
            return render_template('index.html', view_mode='login', erro=f"Erro técnico: {e}")

    if 'user_email' in session:
        return redirect(url_for('dashboard'))
    
    return render_template('index.html', view_mode='login')

@app.route('/dashboard')
def dashboard():
    if 'user_email' not in session: return redirect(url_for('index'))
    
    # Busca dados ordenados
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
            # Log Sincronizado
            db.session.add(Log(
                tipo_item='produto',
                item_id=item.id, 
                acao='WEB_RETIRADA', 
                quantidade=qtd,
                usuario_nome=session['user_email']
            ))
            db.session.commit()
            msg_sucesso = f"Retirado {qtd} un de {item.nome}."

    elif tipo == 'amostra':
        item = Amostra.query.get_or_404(id)
        if request.method == 'POST':
            acao_realizada = request.form.get('acao_amostra')
            
            if acao_realizada == 'retirar':
                item.status = 'EM_RUA'
                item.vendedor_responsavel = session['user_email']
                item.data_saida = datetime.now()
                # Define 7 dias padrão se não informado
                item.data_prevista_retorno = datetime.now() + timedelta(days=7)
                log_acao = 'WEB_EMPRESTIMO'
                
            elif acao_realizada == 'devolver':
                item.status = 'DISPONIVEL'
                item.vendedor_responsavel = None
                log_acao = 'WEB_DEVOLUCAO'
            
            # Log Sincronizado
            db.session.add(Log(
                tipo_item='amostra',
                item_id=item.id,
                acao=log_acao,
                quantidade=1,
                usuario_nome=session['user_email']
            ))
            db.session.commit()
            msg_sucesso = "Status da amostra atualizado."

    return render_template('index.html', view_mode='acao', item=item, tipo=tipo, msg=msg_sucesso)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# --- SLACK CHATBOT INTELIGENTE ---
if slack_app:
    @app.route("/slack/events", methods=["POST"])
    def slack_events():
        return handler.handle(request)

    @slack_app.event("message")
    def handle_message(body, say):
        if "bot_id" in body["event"]: return
        if not model_gemini: 
            say("Estou sem cérebro (Sem chave API).")
            return

        user_text = body["event"].get("text", "")
        # Inicia chat com permissão automática para chamar funções
        chat = model_gemini.start_chat(enable_automatic_function_calling=True)
        response = chat.send_message(user_text)
        
        say(response.text)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
