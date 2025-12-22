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
from flask import Flask, request, render_template, session, redirect, url_for, jsonify, render_template_string, make_response
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_
from slack_bolt import App as BoltApp
from slack_bolt.adapter.flask import SlackRequestHandler
import google.generativeai as genai
from google.generativeai.types import FunctionDeclaration, Tool

# --- REPORTLAB (SOLUÇÃO NATIVA PYTHON PARA PDF) ---
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER

# --- CONFIGURAÇÃO ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
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

# Configuração de E-mail
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = os.environ.get("SMTP_PORT", 587)
SMTP_USER = os.environ.get("SMTP_USER")     
SMTP_PASS = os.environ.get("SMTP_PASS")     
EMAIL_CHEFE = os.environ.get("EMAIL_CHEFE", "chefe@elobrindes.com.br")

# Configuração Slack
slack_app = None
handler = None
if SLACK_BOT_TOKEN and SLACK_SIGNING_SECRET:
    try:
        slack_app = BoltApp(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)
        handler = SlackRequestHandler(slack_app)
    except Exception as e:
        print(f"⚠️ Slack não configurado: {e}")

# --- MODELOS ---
class Produto(db.Model):
    __tablename__ = 'produtos'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150))
    quantidade = db.Column(db.Integer)
    localizacao = db.Column(db.String(50))
    estoque_minimo = db.Column(db.Integer, default=5)
    sku_produtos = db.Column(db.String(100))
    categoria_produtos = db.Column(db.String(100))

class Amostra(db.Model):
    __tablename__ = 'amostras'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150))
    codigo_patrimonio = db.Column(db.String(50))
    status = db.Column(db.String(50)) 
    local_fisico = db.Column(db.String(100))
    vendedor_responsavel = db.Column(db.String(100))
    cliente_destino = db.Column(db.String(150))
    logradouro = db.Column(db.String(255))
    data_saida = db.Column(db.DateTime)
    data_prevista_retorno = db.Column(db.DateTime)
    sku_amostras = db.Column(db.String(100))
    categoria_amostra = db.Column(db.String(100))

class Log(db.Model):
    __tablename__ = 'logs_movimentacao'
    id = db.Column(db.Integer, primary_key=True)
    tipo_item = db.Column(db.String(20))
    item_id = db.Column(db.Integer)
    acao = db.Column(db.String(50))
    quantidade = db.Column(db.Integer, default=1)
    usuario_nome = db.Column(db.String(100))
    data_evento = db.Column(db.DateTime, default=datetime.now)

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
    itens_json = db.Column(db.JSON) 
    status = db.Column(db.String(50), default='ABERTO')
    arquivo_pdf = db.Column(db.String(255))
    data_criacao = db.Column(db.DateTime, default=datetime.now)
    data_prevista_devolucao = db.Column(db.DateTime)

# --- BUSCA INTELIGENTE ---
def encontrar_produto_inteligente(termo_busca):
    termo_limpo = termo_busca.strip()
    produto = Produto.query.filter(or_(
        Produto.nome.ilike(f'%{termo_limpo}%'),
        Produto.sku_produtos.ilike(f'%{termo_limpo}%')
    )).first()
    
    if produto: return produto, None 

    if termo_limpo.lower().endswith('s'):
        termo_singular = termo_limpo[:-1]
        produto = Produto.query.filter(Produto.nome.ilike(f'%{termo_singular}%')).first()
        if produto: return produto, f"(Assumi que '{termo_limpo}' era '{produto.nome}')"

    todos_produtos = db.session.query(Produto.id, Produto.nome).all()
    nomes_db = [p.nome for p in todos_produtos]
    matches = difflib.get_close_matches(termo_limpo, nomes_db, n=1, cutoff=0.5)
    
    if matches:
        nome_encontrado = matches[0]
        produto = Produto.query.filter_by(nome=nome_encontrado).first()
        return produto, f"(Não achei '{termo_limpo}', mas encontrei '{nome_encontrado}'. Usei esse.)"
        
    return None, f"Não encontrei nada parecido com '{termo_busca}'."

# --- TOOLS GEMINI ---
def api_alterar_estoque(nome_ou_sku, quantidade, usuario):
    with app.app_context():
        produto, msg_extra = encontrar_produto_inteligente(nome_ou_sku)
        if not produto: return msg_extra
        try: qtd_int = int(quantidade)
        except: return "Erro: Quantidade inválida."
        nova_qtd = produto.quantidade + qtd_int
        if nova_qtd < 0: return f"Erro: O produto {produto.nome} só tem {produto.quantidade} unidades."
        produto.quantidade = nova_qtd
        acao_log = 'CHAT_ENTRADA' if qtd_int > 0 else 'CHAT_SAIDA'
        db.session.add(Log(tipo_item='produto', item_id=produto.id, acao=acao_log, quantidade=abs(qtd_int), usuario_nome=usuario))
        db.session.commit()
        feedback = f"Sucesso! Estoque de {produto.nome} foi para {produto.quantidade}."
        if msg_extra: feedback += f" {msg_extra}"
        return feedback

def api_movimentar_amostra(nome_ou_pat, acao, cliente_destino, usuario):
    with app.app_context():
        # Busca Amostra
        amostra = Amostra.query.filter(or_(
            Amostra.nome.ilike(f'%{nome_ou_pat}%'),
            Amostra.codigo_patrimonio.ilike(f'%{nome_ou_pat}%'),
            Amostra.sku_amostras.ilike(f'%{nome_ou_pat}%')
        )).first()
        
        # Fallback Fuzzy
        if not amostra:
            todos = db.session.query(Amostra.nome).all()
            nomes = [a.nome for a in todos]
            matches = difflib.get_close_matches(nome_ou_pat, nomes, n=1, cutoff=0.6)
            if matches: amostra = Amostra.query.filter_by(nome=matches[0]).first()
            else: return f"Erro: Amostra '{nome_ou_pat}' não encontrada."

        if acao.lower() == 'retirar':
            # AGORA A RETIRADA DEVE SER FEITA VIA PROTOCOLO, MAS O CHAT AINDA PODE FAZER SE FOR URGENTE
            # MANTEMOS A LÓGICA DO CHAT, MAS NO WEB OBRIGAMOS O PROTOCOLO
            if amostra.status != 'DISPONIVEL': return f"Erro: A amostra {amostra.nome} já está com {amostra.vendedor_responsavel}."
            amostra.status = 'EM_RUA'
            amostra.vendedor_responsavel = usuario
            amostra.cliente_destino = cliente_destino or "Cliente Não Informado (Via Chat)"
            amostra.data_saida = datetime.now()
            amostra.data_prevista_retorno = datetime.now() + timedelta(days=7)
            db.session.add(Log(tipo_item='amostra', item_id=amostra.id, acao='CHAT_RETIRADA', usuario_nome=usuario))
        
        elif acao.lower() == 'devolver':
            if amostra.status == 'DISPONIVEL': return f"A amostra {amostra.nome} já consta como disponível."
            amostra.status = 'DISPONIVEL'
            amostra.vendedor_responsavel = None
            amostra.cliente_destino = None
            db.session.add(Log(tipo_item='amostra', item_id=amostra.id, acao='CHAT_DEVOLUCAO', usuario_nome=usuario))
        else: return "Ação desconhecida. Use 'retirar' ou 'devolver'."

        db.session.commit()
        return f"Feito! Amostra {amostra.nome} agora está {amostra.status}."

def api_consultar(termo):
    with app.app_context():
        p, _ = encontrar_produto_inteligente(termo)
        res_p = f"Produto: {p.nome} | Qtd: {p.quantidade} | Local: {p.localizacao}" if p else ""
        a = Amostra.query.filter(Amostra.nome.ilike(f'%{termo}%')).first()
        if not a:
            todos = [x.nome for x in db.session.query(Amostra.nome).all()]
            match = difflib.get_close_matches(termo, todos, n=1, cutoff=0.6)
            if match: a = Amostra.query.filter_by(nome=match[0]).first()
        status_a = f"Com {a.vendedor_responsavel}" if a and a.status != 'DISPONIVEL' else "Disponível"
        res_a = f"Amostra: {a.nome} | Status: {status_a}" if a else ""
        if not p and not a: return "Não encontrei nada parecido no estoque nem nas amostras."
        return f"{res_p}\n{res_a}"

tools_gemini = [api_alterar_estoque, api_movimentar_amostra, api_consultar]

if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"Erro ao configurar GENAI: {e}", flush=True)

# --- GERADOR PDF REPORTLAB ---
def gerar_pdf_protocolo(protocolo):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
    elements = []
    
    # Estilos
    styles = getSampleStyleSheet()
    style_title = ParagraphStyle('Title', parent=styles['Heading1'], alignment=TA_CENTER, fontSize=24, spaceAfter=20, textColor=colors.darkred)
    style_center = ParagraphStyle('Center', parent=styles['Normal'], alignment=TA_CENTER, fontSize=10)
    style_warning = ParagraphStyle('Warning', parent=styles['Normal'], alignment=TA_CENTER, fontSize=11, textColor=colors.red, fontName='Helvetica-Bold')
    style_address = ParagraphStyle('Address', parent=styles['Normal'], alignment=TA_CENTER, fontSize=10, textColor=colors.white, backColor=colors.black, borderPadding=8)

    # Cabeçalho
    elements.append(Paragraph("<b>ELO BRINDES</b>", style_title))
    elements.append(Paragraph(f"PROTOCOLO DE AMOSTRA <b>#{protocolo.id}</b>", styles['Heading2']))
    elements.append(Spacer(1, 0.5 * cm))
    
    # Dados Gerais
    dados_topo = [
        [f"DATA DE ENVIO: {protocolo.data_criacao.strftime('%d/%m/%Y')}", f"DEVOLUÇÃO PREVISTA: {protocolo.data_prevista_devolucao.strftime('%d/%m/%Y')}"],
        [f"VENDEDOR: {protocolo.vendedor_email}", ""]
    ]
    t_topo = Table(dados_topo, colWidths=[10*cm, 9*cm])
    t_topo.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.lightgrey),
        ('GRID', (0,0), (-1,-1), 1, colors.white),
        ('FONTNAME', (0,0), (-1,-1), 'Helvetica-Bold'),
        ('PADDING', (0,0), (-1,-1), 6),
    ]))
    elements.append(t_topo)
    elements.append(Spacer(1, 0.5 * cm))

    # Dados Cliente
    elements.append(Paragraph("<b>DADOS DO CLIENTE</b>", styles['Heading4']))
    dados_cliente = [
        ["Empresa:", protocolo.cliente_empresa],
        ["Contato:", protocolo.cliente_nome],
        ["CNPJ:", protocolo.cliente_cnpj],
        ["Email:", protocolo.cliente_email],
        ["Telefone:", protocolo.cliente_telefone],
        ["Endereço:", protocolo.cliente_endereco]
    ]
    t_cliente = Table(dados_cliente, colWidths=[3.5*cm, 15.5*cm])
    t_cliente.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
        ('PADDING', (0,0), (-1,-1), 4),
    ]))
    elements.append(t_cliente)
    elements.append(Spacer(1, 0.5 * cm))

    # Itens
    elements.append(Paragraph("<b>ITENS SOLICITADOS</b>", styles['Heading4']))
    data_itens = [['SKU', 'PRODUTO / DESCRIÇÃO', 'QTD']]
    
    if protocolo.itens_json:
        for item in protocolo.itens_json:
            data_itens.append([
                item.get('sku', '-'),
                item.get('nome', 'Item sem nome'),
                str(item.get('qtd', 1))
            ])
    
    t_itens = Table(data_itens, colWidths=[4*cm, 12*cm, 3*cm])
    t_itens.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.darkred),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (1, 0), (1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    elements.append(t_itens)
    
    # Rodapé
    elements.append(Spacer(1, 1.5 * cm))
    elements.append(Paragraph("Caso seja necessário a prorrogação do prazo de devolução, por favor, entre em contato com a vendedora.", style_center))
    elements.append(Spacer(1, 0.2 * cm))
    elements.append(Paragraph("ESTE PROTOCOLO SERÁ USADO COMO COMPROVANTE DE DÉBITO EM CASO DE AQUISIÇÃO, NÃO DEVOLUÇÃO, EXTRAVIO OU AMOSTRAS DANIFICADAS.", style_warning))
    elements.append(Spacer(1, 0.1 * cm))
    elements.append(Paragraph("Tanto a retirada quanto a devolução da amostra são de responsabilidade do cliente.", style_center))
    elements.append(Spacer(1, 1.5 * cm))
    elements.append(Paragraph("_____________________________________________________________", style_center))
    elements.append(Paragraph("<b>Retirada Cliente</b>", style_center))
    elements.append(Spacer(1, 0.5 * cm))
    elements.append(Paragraph("Fone: (11) 2262-9800 / (11) 2949-9387", style_center))
    elements.append(Paragraph("<a href='https://www.elobrindes.com.br' color='blue'>www.elobrindes.com.br</a>", style_center))
    elements.append(Spacer(1, 0.5 * cm))
    elements.append(Paragraph("Rua Paula Ney, 550 - Vila Mariana, São Paulo - SP, 04107-021", style_address))
    
    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()

def enviar_email_protocolo(protocolo, pdf_bytes):
    if not SMTP_USER or not SMTP_PASS:
        print("⚠️ SMTP não configurado. Email não enviado.")
        return

    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USER
        msg['To'] = f"{protocolo.vendedor_email}, {EMAIL_CHEFE}"
        msg['Subject'] = f"Protocolo #{protocolo.id} - {protocolo.cliente_empresa}"

        body = f"Olá,\n\nSegue em anexo o protocolo #{protocolo.id} gerado para o cliente {protocolo.cliente_empresa}.\n\nSistema EloStock."
        msg.attach(MIMEText(body, 'plain'))

        part = MIMEApplication(pdf_bytes, Name=f"Protocolo_{protocolo.id}.pdf")
        part['Content-Disposition'] = f'attachment; filename="Protocolo_{protocolo.id}.pdf"'
        msg.attach(part)

        server = smtplib.SMTP(SMTP_SERVER, int(SMTP_PORT))
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [protocolo.vendedor_email, EMAIL_CHEFE], msg.as_string())
        server.quit()
        print(f"📧 Email enviado com sucesso para {protocolo.vendedor_email}")
    except Exception as e:
        print(f"❌ Erro ao enviar email: {e}")

# --- ROTAS ---

@app.route('/elostock/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST' and 'login_email' in request.form:
        email = request.form.get('login_email')
        password = request.form.get('login_password')
        try:
            if not DIRECTUS_URL: return render_template('index.html', view_mode='login', erro="Sem URL Directus")
            resp = requests.post(f"{DIRECTUS_URL}/auth/login", json={"email": email, "password": password}, verify=False)
            if resp.status_code == 200:
                token = resp.json()['data']['access_token']
                session['user_token'] = token
                session['user_email'] = email
                session['chat_history'] = []
                headers = {"Authorization": f"Bearer {token}"}
                user_info = requests.get(f"{DIRECTUS_URL}/users/me?fields=role.name", headers=headers, verify=False)
                if user_info.status_code == 200:
                    data = user_info.json().get('data', {})
                    role_name = data.get('role', {}).get('name', 'Public') if data.get('role') else 'Public'
                    session['user_role'] = role_name.upper()
                else:
                    session['user_role'] = 'PUBLIC'
                return redirect('/elostock/dashboard')
            return render_template('index.html', view_mode='login', erro="Credenciais inválidas.")
        except Exception as e:
            return render_template('index.html', view_mode='login', erro=f"Erro de Conexão: {str(e)}")
    if 'user_email' in session: return redirect('/elostock/dashboard')
    return render_template('index.html', view_mode='login')

@app.route('/elostock/dashboard')
def dashboard():
    if 'user_email' not in session: return redirect('/elostock/')
    role = session.get('user_role', 'PUBLIC')
    search_query = request.args.get('q', '').strip()
    filter_cat = request.args.get('cat', '').strip()

    produtos = []
    amostras = []
    
    # CORREÇÃO DO SELECT DE CATEGORIAS
    # As consultas distinct() retornam tuplas, precisamos extrair o valor string
    cats_prod_raw = db.session.query(Produto.categoria_produtos).distinct().all()
    cats_amos_raw = db.session.query(Amostra.categoria_amostra).distinct().all()
    
    cats_set = set()
    for c in cats_prod_raw:
        if c[0]: cats_set.add(c[0])
    for c in cats_amos_raw:
        if c[0]: cats_set.add(c[0])
        
    categorias_disponiveis = sorted(list(cats_set))

    ver_tudo = role == 'ADMINISTRATOR'
    ver_compras = role == 'COMPRAS' or ver_tudo
    ver_vendas = role == 'VENDAS' or ver_tudo
    
    if ver_compras:
        query = Produto.query
        if search_query: query = query.filter(or_(Produto.nome.ilike(f'%{search_query}%'), Produto.sku_produtos.ilike(f'%{search_query}%')))
        if filter_cat: query = query.filter(Produto.categoria_produtos == filter_cat)
        produtos = query.order_by(Produto.nome).all()
        
    if ver_vendas:
        query = Amostra.query
        if search_query: query = query.filter(or_(Amostra.nome.ilike(f'%{search_query}%'), Amostra.sku_amostras.ilike(f'%{search_query}%')))
        if filter_cat: query = query.filter(Amostra.categoria_amostra == filter_cat)
        amostras = query.order_by(Amostra.status.desc(), Amostra.nome).all()
    
    return render_template('index.html', view_mode='dashboard', produtos=produtos, amostras=amostras, categorias=categorias_disponiveis, search_query=search_query, selected_cat=filter_cat, user=session['user_email'], role=role)

@app.route('/elostock/protocolos')
def listar_protocolos():
    if 'user_email' not in session: return redirect('/elostock/')
    role = session.get('user_role', 'PUBLIC')
    if role == 'ADMINISTRATOR':
        protocolos = Protocolo.query.order_by(Protocolo.id.desc()).all()
    else:
        protocolos = Protocolo.query.filter_by(vendedor_email=session['user_email']).order_by(Protocolo.id.desc()).all()
    
    return render_template('index.html', view_mode='protocolos', protocolos=protocolos, user=session['user_email'], role=role)

@app.route('/elostock/protocolo/novo', methods=['GET', 'POST'])
def novo_protocolo():
    if 'user_email' not in session: return redirect('/elostock/')
    
    if request.method == 'POST':
        try:
            data_prevista = datetime.strptime(request.form.get('data_prevista'), '%Y-%m-%d')
            skus = request.form.getlist('item_sku[]')
            nomes = request.form.getlist('item_nome[]')
            qtds = request.form.getlist('item_qtd[]')
            
            itens_json = []
            for i in range(len(skus)):
                if nomes[i].strip():
                    itens_json.append({"sku": skus[i], "nome": nomes[i], "qtd": qtds[i]})
            
            novo = Protocolo(
                vendedor_email=session['user_email'],
                cliente_nome=request.form.get('cliente_nome'),
                cliente_empresa=request.form.get('cliente_empresa'),
                cliente_cnpj=request.form.get('cliente_cnpj'),
                cliente_email=request.form.get('cliente_email'),
                cliente_telefone=request.form.get('cliente_telefone'),
                cliente_endereco=request.form.get('cliente_endereco'),
                data_prevista_devolucao=data_prevista,
                itens_json=itens_json
            )
            
            db.session.add(novo)
            
            # --- ATUALIZAÇÃO AUTOMÁTICA DE STATUS PARA 'EM_RUA' ---
            # Para cada item do protocolo, tenta achar a amostra e atualizar
            for item in itens_json:
                sku = item.get('sku')
                nome = item.get('nome')
                
                amostra_db = None
                if sku:
                    amostra_db = Amostra.query.filter_by(sku_amostras=sku).first()
                
                if not amostra_db and nome:
                    amostra_db = Amostra.query.filter(Amostra.nome.ilike(nome)).first()
                
                if amostra_db and amostra_db.status == 'DISPONIVEL':
                    amostra_db.status = 'EM_RUA'
                    amostra_db.vendedor_responsavel = session['user_email']
                    amostra_db.cliente_destino = novo.cliente_empresa
                    amostra_db.data_saida = datetime.now()
                    amostra_db.data_prevista_retorno = data_prevista
                    db.session.add(Log(
                        tipo_item='amostra', 
                        item_id=amostra_db.id, 
                        acao='PROTOCOLO_SAIDA', 
                        usuario_nome=session['user_email']
                    ))

            db.session.commit()
            
            pdf_bytes = gerar_pdf_protocolo(novo)
            enviar_email_protocolo(novo, pdf_bytes)
            
            return redirect('/elostock/protocolos')
            
        except Exception as e:
            print(f"Erro ao criar protocolo: {e}")
            return f"Erro: {e}"

    # AUTOCOMPLETE
    todos_produtos = Produto.query.with_entities(Produto.sku_produtos, Produto.nome).all()
    # Adicionamos também as Amostras no autocomplete para facilitar
    todas_amostras = Amostra.query.with_entities(Amostra.sku_amostras, Amostra.nome).all()
    
    lista_final = []
    seen = set()
    
    for p in todos_produtos:
        if p.nome not in seen:
            lista_final.append({"sku": (p.sku_produtos or ""), "nome": p.nome})
            seen.add(p.nome)
            
    for a in todas_amostras:
        if a.nome not in seen:
            lista_final.append({"sku": (a.sku_amostras or ""), "nome": a.nome})
            seen.add(a.nome)

    return render_template('index.html', view_mode='novo_protocolo', user=session['user_email'], produtos_db=lista_final)

@app.route('/elostock/protocolo/download/<int:id>')
def download_protocolo(id):
    if 'user_email' not in session: return redirect('/elostock/')
    
    protocolo = Protocolo.query.get_or_404(id)
    pdf_bytes = gerar_pdf_protocolo(protocolo)
    
    response = make_response(pdf_bytes)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename=Protocolo_{protocolo.id}.pdf'
    return response

# --- API CHAT ---
@app.route('/elostock/api/chat', methods=['POST'])
def api_chat():
    if 'user_email' not in session: return jsonify({"response": "Você precisa estar logado."}), 401
    data = request.json
    user_msg = data.get('message')
    usuario_atual = session['user_email']
    historico = session.get('chat_history', [])
    if len(historico) > 6: historico = historico[-6:]

    if not GEMINI_API_KEY: return jsonify({"response": "ERRO: GEMINI_API_KEY não configurada."})

    try:
        modelos_disponiveis = []
        try:
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods: modelos_disponiveis.append(m.name)
        except: pass
        modelo_escolhido = 'models/gemini-1.5-flash'
        if modelos_disponiveis:
            found_flash = next((m for m in modelos_disponiveis if 'flash' in m.lower()), None)
            if found_flash: modelo_escolhido = found_flash
            else: modelo_escolhido = modelos_disponiveis[0]

        generation_config = {"temperature": 0.3, "top_p": 0.95, "top_k": 40, "max_output_tokens": 1024, "response_mime_type": "text/plain"}
        model = genai.GenerativeModel(model_name=modelo_escolhido, tools=tools_gemini, generation_config=generation_config)
        hist_str = "\n".join([f"{h['role']}: {h['text']}" for h in historico])
        
        prompt_sistema = f"""
        Você é o assistente inteligente do EloStock. Usuário: {usuario_atual}.
        Histórico recente: {hist_str}
        REGRAS: 1. Busque produto com precisão. 2. Informe se usou nome diferente. 3. Passe '{usuario_atual}' no usuario das tools.
        """
        
        chat = model.start_chat(enable_automatic_function_calling=True)
        response = chat.send_message(f"{prompt_sistema}\nUsuário diz: {user_msg}")
        
        historico.append({"role": "user", "text": user_msg})
        historico.append({"role": "assistant", "text": response.text})
        session['chat_history'] = historico

        return jsonify({"response": response.text})

    except Exception as e:
        erro_bruto = traceback.format_exc()
        print(f"❌ ERRO GRAVE NO CHAT: {erro_bruto}", flush=True)
        return jsonify({"response": f"ERRO TÉCNICO: {str(e)}"})

@app.route('/elostock/acao/<tipo>/<int:id>', methods=['GET', 'POST'])
def acao(tipo, id):
    if 'user_email' not in session: return redirect('/elostock/')
    role = session.get('user_role', 'PUBLIC')
    msg_sucesso = None
    item = None

    if tipo == 'produto':
        if role == 'VENDAS': return "⛔ Acesso Negado"
        item = Produto.query.get_or_404(id)
        if request.method == 'POST':
            qtd = int(request.form.get('qtd', 1))
            item.quantidade -= qtd
            db.session.add(Log(tipo_item='produto', item_id=item.id, acao='WEB_RETIRADA', quantidade=qtd, usuario_nome=session['user_email']))
            db.session.commit()
            msg_sucesso = f"Retirado {qtd} un de {item.nome}."

    elif tipo == 'amostra':
        if role == 'COMPRAS': return "⛔ Acesso Negado"
        item = Amostra.query.get_or_404(id)
        
        if request.method == 'POST':
            acao_realizada = request.form.get('acao_amostra')
            
            # 1. DEVOLUÇÃO
            if acao_realizada == 'devolver':
                item.status = 'DISPONIVEL'
                item.vendedor_responsavel = None
                item.cliente_destino = None
                db.session.add(Log(tipo_item='amostra', item_id=item.id, acao='WEB_DEVOLUCAO', usuario_nome=session['user_email']))
            
            # 2. VENDIDO
            elif acao_realizada == 'vendido':
                item.status = 'VENDIDO'
                # Mantém quem vendeu como responsável no log
                item.vendedor_responsavel = session['user_email'] 
                db.session.add(Log(tipo_item='amostra', item_id=item.id, acao='WEB_VENDIDO', usuario_nome=session['user_email']))
            
            # 3. FORA DE LINHA
            elif acao_realizada == 'fora_linha':
                item.status = 'FORA_DE_LINHA'
                db.session.add(Log(tipo_item='amostra', item_id=item.id, acao='WEB_BAIXA', usuario_nome=session['user_email']))
            
            # OBS: 'retirar' foi removido daqui pois agora exige protocolo
            
            db.session.commit()
            msg_sucesso = "Status atualizado com sucesso!"

    return render_template('index.html', view_mode='acao', item=item, tipo=tipo, msg=msg_sucesso)

@app.route('/elostock/logout')
def logout():
    session.clear()
    return redirect('/elostock/')

if slack_app:
    @app.route("/elostock/slack/events", methods=["POST"])
    def slack_events(): return handler.handle(request)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
