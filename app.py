import warnings
# --- SILENCIAR TUDO PARA LIMPAR O LOG ---
warnings.simplefilter(action='ignore', category=FutureWarning)
warnings.simplefilter(action='ignore', category=UserWarning)

import os
import logging
import traceback 
import difflib 
from datetime import datetime, timedelta
import requests
import urllib3
import json
import smtplib
import io 
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for, make_response
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import joinedload
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib import colors
from reportlab.platypus import Table, TableStyle
import google.generativeai as genai
from slack_bolt import App as SlackApp
from slack_bolt.adapter.flask import SlackRequestHandler

# ==============================================================================
# CONFIGURAÇÕES
# ==============================================================================
logging.basicConfig(level=logging.ERROR) # Só mostra erro grave no console
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "chave_secreta_padrao_desenv")

# --- BANCO DE DADOS (PostgreSQL) ---
# Tenta pegar do ambiente, senão usa o padrão do Dokploy/Local
DB_USER = os.getenv("DB_USER", "leandro.oliveira")
DB_PASS = os.getenv("DB_PASS", "temporario") # Em produção, use variável de ambiente!
DB_HOST = os.getenv("DB_HOST", "152.53.165.62")
DB_PORT = os.getenv("DB_PORT", "5435")
DB_NAME = os.getenv("DB_NAME", "elostock")

# Monta a URL de conexão
if os.getenv("DATABASE_URL"):
    app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL")
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True, 
    "pool_recycle": 300,
}

db = SQLAlchemy(app)

# --- INTEGRAÇÕES ---
DIRECTUS_URL = "https://admin.elobrindes.com.br" 
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")

# Configuração Slack Bolt
slack_app = None
handler = None
if SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET:
    try:
        slack_app = SlackApp(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)
        handler = SlackRequestHandler(slack_app)
    except Exception as e:
        print(f"Erro ao iniciar Slack: {e}")

# --- IA GEMINI ---
GENAI_API_KEY = os.getenv("GENAI_API_KEY")
model = None
if GENAI_API_KEY:
    genai.configure(api_key=GENAI_API_KEY)
    generation_config = {
        "temperature": 0.3, # Um pouco mais criativo, mas ainda seguro
        "top_p": 0.95,
        "top_k": 64,
        "max_output_tokens": 8192,
        "response_mime_type": "text/plain",
    }
    # ATUALIZADO: Instruções do Sistema com a nova lógica de Categoria
    system_instruction = """
    Você é o EloBot, o assistente oficial de logística da Elo Brindes.
    Sua persona é profissional, direta e eficiente.

    SEUS OBJETIVOS:
    1. Gerenciar o estoque de PRODUTOS (Consumo/Brindes) e AMOSTRAS (Showroom).
    2. Diferenciar produtos de SHOWROOM (para clientes) de ALMOXARIFADO (interno).
    3. Registrar movimentações (entradas/saídas) usando as tools disponíveis.
    4. Tirar dúvidas sobre quantidades e localizações.

    REGRAS DE NEGÓCIO:
    - Se o usuário perguntar "tem caneta?", verifique tanto em Showroom quanto em Almoxarifado se houver distinção.
    - Produtos de 'Almoxarifado' são de uso interno. Produtos 'Showroom' são para clientes.
    - Amostras não tem quantidade, elas tem STATUS (Disponível, Em Rua, etc).
    - Produtos tem QUANTIDADE e PREÇO UNITÁRIO.
    - Sempre que fizer uma alteração de estoque, confirme o valor final.
    - Se não encontrar um item exato, busque por aproximação e pergunte "Você quis dizer...?".

    TOOLS:
    - Use `consultar_estoque(termo)` para ver saldo e status.
    - Use `alterar_estoque(produto, qtd, acao)` APENAS se o usuário confirmar explicitamente que retirou/colocou itens.
    """
    
    model = genai.GenerativeModel(
        model_name="gemini-1.5-flash",
        generation_config=generation_config,
        system_instruction=system_instruction,
    )

# ==============================================================================
# MODELOS (DB) - ATUALIZADO
# ==============================================================================

class Produto(db.Model):
    __tablename__ = 'produtos'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), unique=True, nullable=False)
    quantidade = db.Column(db.Integer, default=0)
    localizacao = db.Column(db.String(50))
    estoque_minimo = db.Column(db.Integer, default=10)
    
    # NOVOS CAMPOS ADICIONADOS (Mapeados para o seu SQL)
    sku = db.Column('sku_produtos', db.String(100))
    categoria = db.Column('categoria_produtos', db.String(100), default='SHOWROOM') # Showroom ou Almoxarifado
    valor_unitario = db.Column(db.Numeric(10, 2), default=0.00)
    
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "nome": self.nome,
            "quantidade": self.quantidade,
            "localizacao": self.localizacao or "N/D",
            "estoque_minimo": self.estoque_minimo,
            "sku": self.sku or "",
            "categoria": self.categoria or "Geral",
            "valor": float(self.valor_unitario) if self.valor_unitario else 0.00
        }

class Amostra(db.Model):
    __tablename__ = 'amostras'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    codigo_patrimonio = db.Column(db.String(50), unique=True)
    status = db.Column(db.String(20), default='DISPONIVEL') 
    
    vendedor_responsavel = db.Column(db.String(100))
    cliente_destino = db.Column(db.String(100))
    data_saida = db.Column(db.DateTime)
    data_prevista_retorno = db.Column(db.DateTime)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "nome": self.nome,
            "status": self.status,
            "responsavel": self.vendedor_responsavel or "-",
            "cliente": self.cliente_destino or "-",
            "data_saida": self.data_saida.strftime('%d/%m/%Y') if self.data_saida else "-"
        }

class Log(db.Model):
    __tablename__ = 'logs_movimentacao'
    id = db.Column(db.Integer, primary_key=True)
    tipo_item = db.Column(db.String(20)) 
    item_id = db.Column(db.Integer, nullable=False)
    acao = db.Column(db.String(20)) 
    quantidade = db.Column(db.Integer) 
    usuario_slack_id = db.Column(db.String(50))
    usuario_nome = db.Column(db.String(100))
    data_evento = db.Column(db.DateTime, default=datetime.utcnow)

class Protocolo(db.Model):
    __tablename__ = 'protocolos'
    id = db.Column(db.Integer, primary_key=True)
    vendedor_email = db.Column(db.String(150))
    cliente_nome = db.Column(db.String(150))
    cliente_empresa = db.Column(db.String(150))
    cliente_cnpj = db.Column(db.String(50))
    cliente_email = db.Column(db.String(150))
    cliente_telefone = db.Column(db.String(50))
    cliente_endereco = db.Column(db.Text)
    
    itens_json = db.Column(db.Text) 
    status = db.Column(db.String(50), default='ABERTO')
    arquivo_pdf = db.Column(db.String(255))
    
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)
    data_prevista_devolucao = db.Column(db.DateTime)

# ==============================================================================
# FUNÇÕES AUXILIARES E TOOLS IA
# ==============================================================================

def api_consultar_estoque(termo_busca: str):
    """Consulta produtos (com categoria e preço) e amostras."""
    termo = f"%{termo_busca}%"
    produtos = Produto.query.filter(Produto.nome.ilike(termo)).all()
    amostras = Amostra.query.filter(Amostra.nome.ilike(termo)).all()
    
    if not produtos and not amostras:
        return "Nenhum item encontrado."
    
    res = []
    if produtos:
        res.append("📋 PRODUTOS:")
        for p in produtos:
            res.append(f"- {p.nome} | Qtd: {p.quantidade} | Loc: {p.localizacao} | Cat: {p.categoria} | R$ {p.valor_unitario}")
    
    if amostras:
        res.append("📦 AMOSTRAS:")
        for a in amostras:
            status_desc = f"{a.status}"
            if a.status == 'EM_RUA':
                status_desc += f" (Com {a.vendedor_responsavel})"
            res.append(f"- {a.nome} | Status: {status_desc}")
            
    return "\n".join(res)

def api_alterar_estoque(nome_produto: str, quantidade: int, operacao: str, usuario: str = "ChatBot"):
    """Altera estoque de PRODUTOS de Consumo."""
    todos = Produto.query.all()
    nomes = [p.nome for p in todos]
    match = difflib.get_close_matches(nome_produto, nomes, n=1, cutoff=0.5)
    
    if not match:
        return f"Produto '{nome_produto}' não encontrado."
    
    produto = Produto.query.filter_by(nome=match[0]).first()
    qtd_anterior = produto.quantidade
    
    if operacao in ['adicionar', 'entrada', 'somar']:
        produto.quantidade += quantidade
        acao_log = 'ENTRADA'
    elif operacao in ['remover', 'saida', 'retirar']:
        if produto.quantidade < quantidade:
            return f"Erro: Saldo insuficiente ({produto.quantidade})."
        produto.quantidade -= quantidade
        acao_log = 'SAIDA'
    else:
        return "Operação inválida."

    # Log
    db.session.add(Log(
        tipo_item='PRODUTO', item_id=produto.id, acao=acao_log, 
        quantidade=quantidade, usuario_nome=usuario
    ))
    db.session.commit()
    
    return f"Sucesso! {produto.nome}: {qtd_anterior} -> {produto.quantidade}."

# Mapping de tools para o Gemini usar via código (se necessário)
TOOLS = {
    'consultar_estoque': api_consultar_estoque,
    'alterar_estoque': api_alterar_estoque
}

# ==============================================================================
# ROTAS FLASK (WEB)
# ==============================================================================

@app.route('/elostock/')
def index():
    if 'user' not in session:
        return render_template('index.html', view_mode='login')
    
    # View padrão: Dashboard ou o que estiver na URL query
    mode = request.args.get('mode', 'dashboard')
    return render_template('index.html', view_mode=mode, user=session['user'])

@app.route('/elostock/api/login', methods=['POST'])
def api_login():
    data = request.json
    email = data.get('email')
    password = data.get('password')

    # Auth via Directus
    try:
        resp = requests.post(f"{DIRECTUS_URL}/auth/login", json={"email": email, "password": password})
        if resp.status_code == 200:
            tokens = resp.json()['data']
            # Pega dados do usuario
            headers = {"Authorization": f"Bearer {tokens['access_token']}"}
            user_resp = requests.get(f"{DIRECTUS_URL}/users/me", headers=headers)
            user_data = user_resp.json()['data']
            
            # Pega a Role
            role_id = user_data.get('role')
            role_name = "USER"
            if role_id:
                role_resp = requests.get(f"{DIRECTUS_URL}/roles/{role_id}", headers=headers)
                if role_resp.status_code == 200:
                    role_name = role_resp.json()['data']['name'].upper()

            session['user'] = {
                'id': user_data['id'],
                'name': f"{user_data.get('first_name','')} {user_data.get('last_name','')}",
                'email': user_data['email'],
                'role': role_name,
                'token': tokens['access_token']
            }
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "message": "Credenciais inválidas"})
    except Exception as e:
        print(e)
        # Fallback local para testes se Directus falhar
        if email == "admin@elobrindes.com.br" and password == "admin":
            session['user'] = {'name': 'Admin Local', 'email': email, 'role': 'ADMINISTRATOR'}
            return jsonify({"success": True})
            
        return jsonify({"success": False, "message": "Erro de conexão"})

# --- ROTAS DE API DE DADOS ---

@app.route('/elostock/api/produtos')
def get_produtos():
    termo = request.args.get('q', '').lower()
    query = Produto.query
    if termo:
        query = query.filter(Produto.nome.ilike(f"%{termo}%"))
    
    # Retorna todos (Showroom e Almoxarifado)
    # O front filtra se precisar
    prods = query.order_by(Produto.nome).all()
    return jsonify([p.to_dict() for p in prods])

@app.route('/elostock/api/amostras')
def get_amostras():
    amostras = Amostra.query.order_by(Amostra.status, Amostra.nome).all()
    return jsonify([a.to_dict() for a in amostras])

@app.route('/elostock/api/protocolos')
def get_protocolos():
    # Retorna ultimos 50 protocolos
    protos = Protocolo.query.order_by(Protocolo.id.desc()).limit(50).all()
    data = []
    for p in protos:
        data.append({
            "id": p.id,
            "empresa": p.cliente_empresa,
            "data": p.data_criacao.strftime("%d/%m/%Y"),
            "status": p.status,
            "vendedor": p.vendedor_email
        })
    return jsonify(data)

# --- ROTAS DE AÇÃO (Chat e Protocolo) ---

@app.route('/elostock/api/chat', methods=['POST'])
def chat_endpoint():
    data = request.json
    msg = data.get('message')
    user_context = session.get('user', {}).get('name', 'Usuario')

    if not model:
        return jsonify({"response": "IA não configurada."})

    try:
        chat = model.start_chat(enable_automatic_function_calling=True)
        # Injetamos as funções no escopo global para o SDK do Gemini achar
        # Nota: O SDK Python do Gemini 1.5 faz a chamada automatica se as funcoes estiverem no globals ou passadas na config
        # Aqui vamos fazer um wrapper manual simples se o automatico falhar, ou confiar no prompt
        
        # Hack para tools manuais:
        prompt_final = f"Usuário {user_context}: {msg}"
        
        # Vamos usar a logica de Function Calling do Gemini
        # Para simplificar neste arquivo unico sem definir as tools objects complexos:
        # Vamos deixar o modelo gerar texto e se ele pedir uma acao, nós (desenvolvedor) poderiamos parsear.
        # MAS, para manter simples: O modelo vai responder em texto natural chamando a função simulada internamente.
        
        # Se quiser tool use real:
        # tools_list = [api_consultar_estoque, api_alterar_estoque]
        # chat = model.start_chat(history=[], tools=tools_list)
        
        # Como as tools não foram passadas na inicialização do model acima (para economizar linhas),
        # vamos fazer o "ReAct" manual simples ou apenas resposta de texto.
        
        # Vamos tentar identificar intenção básica no código para ser rápido
        resposta_txt = ""
        
        if "consultar" in msg.lower() or "ver" in msg.lower() or "quant" in msg.lower():
            # Extrai termo simples
            termo = msg.replace("consultar","").replace("ver","").strip()
            if len(termo) > 2:
                dados = api_consultar_estoque(termo)
                resposta_txt = f"Aqui está o que encontrei:\n{dados}"
            else:
                resp = chat.send_message(prompt_final)
                resposta_txt = resp.text
        else:
            resp = chat.send_message(prompt_final)
            resposta_txt = resp.text
            
        return jsonify({"response": resposta_txt})

    except Exception as e:
        print(f"Erro Chat: {e}")
        return jsonify({"response": "Erro ao processar sua solicitação."})

@app.route('/elostock/api/novo_protocolo', methods=['POST'])
def novo_protocolo():
    data = request.json
    user = session.get('user', {})
    
    # Cria registro
    novo = Protocolo(
        vendedor_email=user.get('email', 'sistema@elobrindes.com.br'),
        cliente_empresa=data.get('empresa'),
        cliente_nome=data.get('contato'),
        cliente_cnpj=data.get('cnpj'),
        cliente_endereco=data.get('endereco'),
        cliente_email=data.get('email'),
        cliente_telefone=data.get('telefone'),
        itens_json=json.dumps(data.get('itens')),
        status='EMITIDO'
    )
    
    # Tenta usar data de envio do form ou hoje
    data_envio = data.get('data_envio')
    if data_envio:
        try:
            novo.data_criacao = datetime.strptime(data_envio, '%Y-%m-%d')
        except:
            pass

    db.session.add(novo)
    db.session.commit()
    
    # Baixa de Estoque e Snapshot de Preço
    itens = data.get('itens', [])
    for item in itens:
        nome_item = item.get('nome')
        qtd = int(item.get('qtd', 0))
        
        # Tenta achar o produto para atualizar qtd e pegar valor real
        prod = Produto.query.filter_by(nome=nome_item).first()
        if prod:
            # Baixa de estoque
            if prod.quantidade >= qtd:
                prod.quantidade -= qtd
                db.session.add(Log(
                    tipo_item='PRODUTO', item_id=prod.id, 
                    acao='SAIDA_PROTOCOLO', quantidade=qtd, 
                    usuario_nome=user.get('name')
                ))
            
            # Se o item no JSON veio sem preço (0), usa o do cadastro
            # Se já veio com preço manual, mantemos
            if float(item.get('valor', 0)) == 0:
               item['valor'] = float(prod.valor_unitario)
        
    # Atualiza o JSON com os preços corrigidos se necessário
    novo.itens_json = json.dumps(itens)
    db.session.commit()

    return jsonify({"success": True, "id": novo.id})

# --- GERAÇÃO DE PDF (REPORTLAB) ---

@app.route('/elostock/protocolo/pdf/<int:proto_id>')
def gerar_pdf_protocolo(proto_id):
    p = Protocolo.query.get_or_404(proto_id)
    itens = json.loads(p.itens_json)
    
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    
    # Configs visuais
    margin_left = 40
    curr_y = height - 50
    
    # 1. Cabeçalho
    # Logo (placeholder) e Título
    c.setFillColor(colors.HexColor("#1e293b")) # Dark Blue
    c.rect(0, height-100, width, 100, fill=1, stroke=0)
    
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(margin_left, height-50, "ELO BRINDES")
    c.setFont("Helvetica", 10)
    c.drawString(margin_left, height-65, "Logística Promocional & Showroom")
    
    c.setFont("Helvetica-Bold", 16)
    c.drawRightString(width - 40, height - 50, f"PROTOCOLO #{p.id}")
    c.setFont("Helvetica", 12)
    c.drawRightString(width - 40, height - 70, f"Data: {p.data_criacao.strftime('%d/%m/%Y')}")

    curr_y -= 120
    
    # 2. Dados Cliente e Vendedor (Side by Side)
    c.setFillColor(colors.black)
    
    # Coluna Esquerda (Cliente)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin_left, curr_y, "DADOS DO DESTINATÁRIO:")
    c.line(margin_left, curr_y-3, 250, curr_y-3)
    curr_y -= 15
    
    c.setFont("Helvetica", 10)
    c.drawString(margin_left, curr_y, f"Empresa: {p.cliente_empresa or ''}")
    curr_y -= 12
    c.drawString(margin_left, curr_y, f"A/C: {p.cliente_nome or ''}")
    curr_y -= 12
    c.drawString(margin_left, curr_y, f"CNPJ: {p.cliente_cnpj or ''}")
    curr_y -= 12
    # Endereço com quebra de linha simples
    end = (p.cliente_endereco or "")[:60] 
    c.drawString(margin_left, curr_y, f"Endereço: {end}")
    
    # Coluna Direita (Vendedor)
    col_right = 300
    y_right = height - 170
    c.setFont("Helvetica-Bold", 11)
    c.drawString(col_right, y_right, "RESPONSÁVEL ELO:")
    c.line(col_right, y_right-3, 500, y_right-3)
    y_right -= 15
    
    c.setFont("Helvetica", 10)
    c.drawString(col_right, y_right, f"Vendedor(a): {p.vendedor_email.split('@')[0].title()}")
    y_right -= 12
    c.drawString(col_right, y_right, f"Email: {p.vendedor_email}")
    
    curr_y = min(curr_y, y_right) - 40
    
    # 3. Tabela de Itens
    data_tab = [['PRODUTO / ITEM', 'QTD', 'V. UNIT', 'TOTAL']]
    
    total_geral = 0
    for item in itens:
        nome = item.get('nome', '')
        qtd = int(item.get('qtd', 0))
        valor = float(item.get('valor', 0))
        sub = qtd * valor
        total_geral += sub
        
        data_tab.append([
            nome[:50], # Truncate
            str(qtd),
            f"R$ {valor:.2f}",
            f"R$ {sub:.2f}"
        ])
    
    # Totais
    data_tab.append(['', '', 'TOTAL GERAL:', f"R$ {total_geral:.2f}"])
    
    t = Table(data_tab, colWidths=[300, 50, 80, 80])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#cbd5e1")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.black),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('ALIGN', (0,1), (0,-1), 'LEFT'), # Nomes a esquerda
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('BOTTOMPADDING', (0,0), (-1,0), 8),
        ('GRID', (0,0), (-1,-2), 0.5, colors.grey),
        ('LINEBELOW', (0,-1), (-1,-1), 1, colors.black), # Linha do total
        ('FONTNAME', (-2,-1), (-1,-1), 'Helvetica-Bold'), # Total bold
    ]))
    
    w, h = t.wrapOn(c, width, height)
    
    # Verifica se cabe na pagina
    if curr_y - h < 50:
        c.showPage()
        curr_y = height - 50
        
    t.drawOn(c, margin_left, curr_y - h)
    
    curr_y = curr_y - h - 50
    
    # 4. Assinaturas
    if curr_y < 100:
        c.showPage()
        curr_y = height - 150

    c.line(margin_left, curr_y, 250, curr_y)
    c.drawString(margin_left, curr_y - 15, "Recebido por (Nome Legível)")
    
    c.line(300, curr_y, 500, curr_y)
    c.drawString(300, curr_y - 15, "Data e Assinatura")
    
    c.save()
    buffer.seek(0)
    
    return send_file(buffer, as_attachment=True, download_name=f"Protocolo_{p.id}.pdf", mimetype='application/pdf')

# --- INTEGRAÇÃO SLACK EVENTS (Mantida Integralmente) ---
if slack_app:
    @app.route("/elostock/slack/events", methods=["POST"])
    def slack_events():
        return handler.handle(request)

    # Evento: App Mention (Bot mencionado no canal)
    @slack_app.event("app_mention")
    def handle_app_mentions(body, say):
        text = body["event"]["text"]
        user = body["event"]["user"]
        
        # Simples repasse pro Gemini (se configurado) ou resposta padrão
        if model:
            # Aqui poderiamos chamar a mesma logica do chat_endpoint
            say(f"Olá <@{user}>! Recebi sua mensagem. Use o painel web para interagir melhor, ou aguarde futura implementação completa via Slack.")
        else:
            say(f"Olá <@{user}>! Sou o EloStock Bot.")

# --- ROTA DE ADMINISTRAÇÃO/AÇÃO RÁPIDA (Exemplo de botões na interface) ---
@app.route('/elostock/acao/<tipo>/<int:id>', methods=['GET', 'POST'])
def acao_rapida(tipo, id):
    if 'user' not in session: return redirect('/elostock/')
    
    # Lógica para devolver amostra ou dar baixa manual
    msg_sucesso = None
    item = None
    
    if tipo == 'amostra':
        item = Amostra.query.get(id)
        if request.method == 'POST':
            nova_acao = request.form.get('acao') # Devolver, Baixar
            if nova_acao == 'devolver':
                item.status = 'DISPONIVEL'
                item.vendedor_responsavel = None
                item.cliente_destino = None
                item.data_saida = None
                db.session.add(Log(
                    tipo_item='AMOSTRA', item_id=item.id, acao='DEVOLUCAO', 
                    usuario_nome=session['user']['name']
                ))
            elif nova_acao == 'saida':
                item.status = 'EM_RUA'
                item.vendedor_responsavel = session['user']['name']
                # Pega cliente do form
                item.cliente_destino = request.form.get('cliente')
                item.data_saida = datetime.utcnow()
                db.session.add(Log(
                    tipo_item='AMOSTRA', item_id=item.id, acao='EMPRESTIMO', 
                    usuario_nome=session['user']['name']
                ))
            
            db.session.commit()
            msg_sucesso = "Status atualizado com sucesso!"

    return render_template('index.html', view_mode='acao', item=item, tipo=tipo, msg=msg_sucesso)

@app.route('/elostock/logout')
def logout():
    session.clear()
    return redirect('/elostock/')

if __name__ == '__main__':
    # Cria tabelas se não existirem
    with app.app_context():
        db.create_all()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
