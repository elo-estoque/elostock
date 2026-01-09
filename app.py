import warnings
# --- SILENCIAR TUDO PARA LIMPAR O LOG ---
warnings.simplefilter(action='ignore', category=FutureWarning)
warnings.simplefilter(action='ignore', category=UserWarning)

import os
import logging
import traceback 
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
from sqlalchemy import or_, and_, func
from slack_bolt import App as BoltApp
from slack_bolt.adapter.flask import SlackRequestHandler

# --- REPORTLAB (PDF) ---
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_RIGHT

# --- CONFIGURAÇÃO ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "chave_padrao_segura")

# Banco de Dados
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Variáveis Externas
DIRECTUS_URL = os.environ.get("DIRECTUS_URL")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET")
# Token opcional para o CNPJa (se tiver, coloque no .env, senão ele tenta sem)
CNPJA_TOKEN = os.environ.get("CNPJA_TOKEN")

# --- CONFIGURAÇÃO AUTENTIQUE ---
AUTENTIQUE_TOKEN = os.environ.get("AUTENTIQUE_TOKEN")
AUTENTIQUE_URL = "https://api.autentique.com.br/v2/graphql"

# Configuração de E-mail (SMTP)
SMTP_SERVER = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = os.environ.get("SMTP_PORT", 587)
SMTP_USER = os.environ.get("SMTP_USER")     
SMTP_PASS = os.environ.get("SMTP_PASS")     
EMAIL_CHEFE = os.environ.get("EMAIL_CHEFE", "chefe@elobrindes.com.br")

# Slack
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
    subcategoria = db.Column(db.String(100)) 
    valor_unitario = db.Column(db.Numeric(10, 2), nullable=True)

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
    
    # Dados do Cliente
    cliente_nome = db.Column(db.String(150)) # Nome
    cliente_sobrenome = db.Column(db.String(150)) # Sobrenome
    cliente_empresa = db.Column(db.String(150)) # Razão Social
    cliente_cnpj = db.Column(db.String(50))
    endereco_ie = db.Column(db.String(50)) # IE
    
    cliente_email = db.Column(db.String(150))
    cliente_telefone = db.Column(db.String(50))
    
    # Endereço Separado
    endereco_cep = db.Column(db.String(20))
    endereco_rua = db.Column(db.String(200))
    endereco_numero = db.Column(db.String(20))
    endereco_bairro = db.Column(db.String(100))
    endereco_cidade = db.Column(db.String(100))
    endereco_uf = db.Column(db.String(5))
    cliente_endereco = db.Column(db.Text) 
    
    # Rastreio e Envio
    transportadora = db.Column(db.String(100))
    rastreio = db.Column(db.String(100))
    entregador_nome = db.Column(db.String(100))
    data_envio = db.Column(db.DateTime)
    
    # Dados do Vendedor
    vendedor_nome = db.Column(db.String(150))
    vendedor_telefone = db.Column(db.String(50))

    itens_json = db.Column(db.JSON) 
    status = db.Column(db.String(50), default='ABERTO')
    arquivo_pdf = db.Column(db.String(255))
    data_criacao = db.Column(db.DateTime, default=datetime.now)
    data_prevista_devolucao = db.Column(db.DateTime)

# --- FUNÇÕES AUXILIARES ---

def gerar_pdf_protocolo(protocolo):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=20, leftMargin=20, topMargin=20, bottomMargin=20)
    elements = []
    
    styles = getSampleStyleSheet()
    style_title = ParagraphStyle('Title', parent=styles['Heading1'], alignment=TA_CENTER, fontSize=24, spaceAfter=20, textColor=colors.darkred)
    style_center = ParagraphStyle('Center', parent=styles['Normal'], alignment=TA_CENTER, fontSize=10)
    style_warning = ParagraphStyle('Warning', parent=styles['Normal'], alignment=TA_CENTER, fontSize=11, textColor=colors.red, fontName='Helvetica-Bold')
    style_address = ParagraphStyle('Address', parent=styles['Normal'], alignment=TA_CENTER, fontSize=10, textColor=colors.white, backColor=colors.black, borderPadding=8)

    # Header
    elements.append(Paragraph("<b>ELO BRINDES</b>", style_title))
    elements.append(Paragraph(f"PROTOCOLO DE AMOSTRA <b>#{protocolo.id}</b>", styles['Heading2']))
    elements.append(Spacer(1, 0.5 * cm))
    
    # Datas
    d_envio = protocolo.data_envio.strftime('%d/%m/%Y') if protocolo.data_envio else (protocolo.data_criacao.strftime('%d/%m/%Y') if protocolo.data_criacao else '--/--/----')
    d_dev = protocolo.data_prevista_devolucao.strftime('%d/%m/%Y') if protocolo.data_prevista_devolucao else '--/--/----'
    
    vendedor_txt = f"{protocolo.vendedor_nome} | {protocolo.vendedor_telefone}" if protocolo.vendedor_nome else f"{protocolo.vendedor_email}"
    
    dados_topo = [
        [f"DATA DE ENVIO: {d_envio}", f"DEVOLUÇÃO PREVISTA: {d_dev}"],
        [f"VENDEDOR: {vendedor_txt}", ""]
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
    
    endereco_completo = f"{protocolo.endereco_rua or ''}, {protocolo.endereco_numero or ''} - {protocolo.endereco_bairro or ''}, {protocolo.endereco_cidade or ''}/{protocolo.endereco_uf or ''} - CEP: {protocolo.endereco_cep or ''}"
    if not protocolo.endereco_rua:
        endereco_completo = protocolo.cliente_endereco 
        
    nome_completo = f"{protocolo.cliente_nome} {protocolo.cliente_sobrenome or ''}"

    dados_cliente = [
        ["Empresa:", protocolo.cliente_empresa or ''],
        ["CNPJ:", protocolo.cliente_cnpj or ''],
        ["Inscr. Estadual:", protocolo.endereco_ie or 'ISENTO'],
        ["Contato:", nome_completo],
        ["Email:", protocolo.cliente_email or ''],
        ["Telefone:", protocolo.cliente_telefone or ''],
        ["Endereço:", endereco_completo]
    ]
    t_cliente = Table(dados_cliente, colWidths=[3.5*cm, 15.5*cm])
    t_cliente.setStyle(TableStyle([
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
        ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
        ('PADDING', (0,0), (-1,-1), 4),
    ]))
    elements.append(t_cliente)
    
    # Envio/Rastreio
    if protocolo.transportadora or protocolo.entregador_nome:
        elements.append(Spacer(1, 0.2 * cm))
        dados_envio = [
            ["Transporte:", f"{protocolo.transportadora or 'Próprio'}"],
            ["Rastreio/Entregador:", f"{protocolo.rastreio or protocolo.entregador_nome or '-'}"]
        ]
        t_envio = Table(dados_envio, colWidths=[3.5*cm, 15.5*cm])
        t_envio.setStyle(TableStyle([
            ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
            ('FONTNAME', (0,0), (0,-1), 'Helvetica-Bold'),
            ('PADDING', (0,0), (-1,-1), 4),
        ]))
        elements.append(t_envio)

    elements.append(Spacer(1, 0.5 * cm))

    # Itens
    elements.append(Paragraph("<b>ITENS SOLICITADOS</b>", styles['Heading4']))
    
    data_itens = [['PRODUTO / DESCRIÇÃO', 'QTD', 'UNIT.', 'TOTAL']]
    total_protocolo = 0.0
    
    if protocolo.itens_json:
        for item in protocolo.itens_json:
            try:
                nome_txt = str(item.get('nome') or 'Item sem nome')
                qtd_txt = str(item.get('qtd') or '1')
                raw_unit = item.get('preco_unit')
                raw_total = item.get('subtotal')
                val_unit = float(raw_unit) if raw_unit is not None else 0.0
                val_total = float(raw_total) if raw_total is not None else 0.0
                total_protocolo += val_total
                
                data_itens.append([
                    nome_txt,
                    qtd_txt,
                    f"R$ {val_unit:.2f}",
                    f"R$ {val_total:.2f}"
                ])
            except Exception as e:
                print(f"Erro processando item PDF: {e}")
                data_itens.append(["Erro nos dados do item", "0", "0.00", "0.00"])
            
    data_itens.append(['', '', 'TOTAL:', f"R$ {total_protocolo:.2f}"])
    
    t_itens = Table(data_itens, colWidths=[11.5*cm, 2*cm, 2.5*cm, 2.5*cm])
    t_itens.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.darkred),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'), 
        ('ALIGN', (2, 1), (-1, -1), 'RIGHT'), 
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), 
        ('FONTNAME', (-2, -1), (-1, -1), 'Helvetica-Bold'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('GRID', (0, 0), (-1, -2), 1, colors.black),
        ('LINEBELOW', (0, -1), (-1, -1), 1, colors.black),
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

def enviar_para_autentique(protocolo, pdf_bytes):
    if not AUTENTIQUE_TOKEN:
        print("⚠️ Token Autentique não configurado.")
        return {"erro": "Token Autentique não encontrado no .env"}

    operations = {
        "query": """
        mutation CreateDocumentMutation(
            $document: DocumentInput!,
            $signers: [SignerInput!]!,
            $file: Upload!
        ) {
            createDocument(
                document: $document,
                signers: $signers,
                file: $file
            ) {
                id
                name
            }
        }
        """,
        "variables": {
            "document": {
                "name": f"Protocolo de Amostra #{protocolo.id} - Elo Brindes",
                "message": f"Olá {protocolo.cliente_nome}, por favor assine o protocolo de recebimento das amostras."
            },
            "signers": [
                {
                    "email": protocolo.cliente_email,
                    "action": "SIGN",
                    "positions": [{"x": "50", "y": "80", "z": "1"}] 
                }
            ],
            "file": None
        }
    }

    map_data = {"0": ["variables.file"]}
    files = {"0": (f"protocolo_{protocolo.id}.pdf", pdf_bytes, "application/pdf")}
    headers = {"Authorization": f"Bearer {AUTENTIQUE_TOKEN}"}

    try:
        response = requests.post(
            AUTENTIQUE_URL,
            data={"operations": json.dumps(operations), "map": json.dumps(map_data)},
            files=files,
            headers=headers
        )
        resp_json = response.json()
        if "errors" in resp_json:
            return {"erro": resp_json['errors'][0]['message']}
        return {"sucesso": True, "data": resp_json['data']['createDocument']}
    except Exception as e:
        return {"erro": str(e)}

def enviar_email_interno(protocolo, pdf_bytes):
    if not SMTP_USER or not SMTP_PASS: return
    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USER
        msg['To'] = f"{protocolo.vendedor_email}, {EMAIL_CHEFE}"
        msg['Subject'] = f"Cópia Interna: Protocolo #{protocolo.id} - {protocolo.cliente_empresa}"
        body = f"Protocolo #{protocolo.id} gerado.\nCliente: {protocolo.cliente_nome}\n\nEste documento foi/será enviado via Autentique."
        msg.attach(MIMEText(body, 'plain'))
        part = MIMEApplication(pdf_bytes, Name=f"Protocolo_{protocolo.id}.pdf")
        part['Content-Disposition'] = f'attachment; filename="Protocolo_{protocolo.id}.pdf"'
        msg.attach(part)
        server = smtplib.SMTP(SMTP_SERVER, int(SMTP_PORT))
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(SMTP_USER, [protocolo.vendedor_email, EMAIL_CHEFE], msg.as_string())
        server.quit()
    except Exception as e:
        print(f"❌ Erro ao enviar email interno: {e}")

# --- ROTAS ---

@app.route('/showroom/', methods=['GET', 'POST'])
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
                headers = {"Authorization": f"Bearer {token}"}
                user_info = requests.get(f"{DIRECTUS_URL}/users/me?fields=role.name,first_name,last_name,title", headers=headers, verify=False)
                if user_info.status_code == 200:
                    data = user_info.json().get('data', {})
                    role_name = data.get('role', {}).get('name', 'Public') if data.get('role') else 'Public'
                    session['user_role'] = role_name.upper()
                    session['user_name'] = f"{data.get('first_name','')} {data.get('last_name','')}"
                    session['user_phone'] = data.get('title', '') 
                else:
                    session['user_role'] = 'PUBLIC'
                return redirect('/showroom/dashboard')
            return render_template('index.html', view_mode='login', erro="Credenciais inválidas.")
        except Exception as e:
            return render_template('index.html', view_mode='login', erro=f"Erro de Conexão: {str(e)}")
    if 'user_email' in session: return redirect('/showroom/dashboard')
    return render_template('index.html', view_mode='login')

# --- NOVA ROTA API INTERNA (PROXY DE CNPJ COM SUPORTE A IE) ---
@app.route('/showroom/api/consulta_cnpj/<cnpj>')
def consulta_cnpj_proxy(cnpj):
    if 'user_email' not in session: return jsonify({'erro': 'Acesso negado'}), 403
    
    cnpj_limpo = ''.join(filter(str.isdigit, cnpj))
    dados_finais = {}

    # 1. Tenta BrasilAPI (Gratuita, Rápida para Endereço)
    try:
        resp = requests.get(f"https://brasilapi.com.br/api/cnpj/v1/{cnpj_limpo}", timeout=5)
        if resp.status_code == 200:
            dados_finais = resp.json()
    except Exception as e:
        print(f"Erro BrasilAPI: {e}")

    # 2. Busca Inscrição Estadual (IE) via CNPJa (API PUBLICA)
    # A BrasilAPI raramente retorna IE, então forçamos a busca no CNPJa se a IE não existir
    ie_encontrada = dados_finais.get('inscricao_estadual')
    
    if not ie_encontrada: 
        try:
            # Configura Headers para evitar bloqueio e usa o Token se disponível
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            if CNPJA_TOKEN:
                headers['Authorization'] = CNPJA_TOKEN
            
            # --- CORREÇÃO: Endpoint da API PÚBLICA (Open) ---
            resp_cnpja = requests.get(f"https://cnpja.com/api/open/cnpj/{cnpj_limpo}", headers=headers, timeout=8)
            
            if resp_cnpja.status_code == 200:
                dados_cnpja = resp_cnpja.json()
                
                # Se BrasilAPI falhou totalmente, usa dados cadastrais do CNPJa
                if not dados_finais:
                    dados_finais = {
                        'razao_social': dados_cnpja.get('name') or dados_cnpja.get('company', {}).get('name'),
                        'nome_fantasia': dados_cnpja.get('alias'),
                        'logradouro': dados_cnpja.get('address', {}).get('street'),
                        'numero': dados_cnpja.get('address', {}).get('number'),
                        'bairro': dados_cnpja.get('address', {}).get('district'),
                        'municipio': dados_cnpja.get('address', {}).get('city'),
                        'uf': dados_cnpja.get('address', {}).get('state'),
                        'cep': dados_cnpja.get('address', {}).get('zip')
                    }
                
                # LÓGICA PARA EXTRAIR A INSCRIÇÃO ESTADUAL (IE) NA API PÚBLICA
                # A API Pública pode retornar IE em 'sincor', 'registrations' ou 'inscriptions'
                registros = dados_cnpja.get('sincor', []) or dados_cnpja.get('registrations', []) or dados_cnpja.get('inscriptions', [])
                estado_empresa = dados_finais.get('uf')
                
                ie_localizada = None

                # Tenta achar a IE do mesmo estado da empresa
                if registros and isinstance(registros, list):
                    for reg in registros:
                        # O campo pode vir como 'state' ou 'uf'
                        uf_reg = reg.get('state') or reg.get('uf')
                        if uf_reg == estado_empresa and reg.get('number'):
                            ie_localizada = reg.get('number')
                            break
                    
                    # Se não achou do estado específico, tenta pegar a primeira válida (ex: Inscrição Única)
                    if not ie_localizada and len(registros) > 0:
                        ie_localizada = registros[0].get('number')

                # Tenta campo direto se a lista falhar (algumas versões retornam direto)
                if not ie_localizada:
                     ie_localizada = dados_cnpja.get('inscricao_estadual')

                if ie_localizada:
                    dados_finais['inscricao_estadual'] = ie_localizada

        except Exception as e:
            print(f"Erro CNPJa: {e}")

    if dados_finais:
        return jsonify(dados_finais)
    else:
        return jsonify({'erro': 'CNPJ não encontrado'}), 404

@app.route('/showroom/dashboard')
def dashboard():
    if 'user_email' not in session: return redirect('/showroom/')
    role = session.get('user_role', 'PUBLIC')
    search_query = request.args.get('q', '').strip()
    filter_cat = request.args.get('cat', '').strip()
    filter_sub = request.args.get('sub', '').strip() 

    total_estoque = db.session.query(func.sum(Produto.quantidade)).scalar() or 0
    valor_estoque = db.session.query(func.sum(Produto.quantidade * Produto.valor_unitario)).scalar() or 0
    ticket_medio = (valor_estoque / total_estoque) if total_estoque > 0 else 0
    
    hoje = datetime.now().date()
    movimentacoes_hoje = Log.query.filter(func.date(Log.data_evento) == hoje).count()

    produtos_showroom = [] 
    amostras = []
    
    cats_prod_raw = db.session.query(Produto.categoria_produtos).filter(Produto.categoria_produtos.ilike('%showroom%')).distinct().all()
    cats_amos_raw = db.session.query(Amostra.categoria_amostra).distinct().all()
    subs_raw = db.session.query(Produto.subcategoria).filter(Produto.categoria_produtos.ilike('%showroom%')).distinct().all() 

    cats_set = set()
    for c in cats_prod_raw:
        if c[0]: cats_set.add(c[0])
    for c in cats_amos_raw:
        if c[0]: cats_set.add(c[0])
    
    categorias_disponiveis = sorted(list(cats_set))
    subcategorias_disponiveis = sorted([s[0] for s in subs_raw if s[0]]) 
    
    query_sp = Produto.query.filter(Produto.categoria_produtos.ilike('%showroom%'))
    if search_query: 
        query_sp = query_sp.filter(or_(Produto.nome.ilike(f'%{search_query}%'), Produto.sku_produtos.ilike(f'%{search_query}%')))
    if filter_cat:
         query_sp = query_sp.filter(Produto.categoria_produtos == filter_cat)
    if filter_sub: 
         query_sp = query_sp.filter(Produto.subcategoria == filter_sub)
         
    produtos_showroom = query_sp.order_by(Produto.nome).all()

    query_am = Amostra.query
    if search_query: query_am = query_am.filter(or_(Amostra.nome.ilike(f'%{search_query}%'), Amostra.sku_amostras.ilike(f'%{search_query}%')))
    if filter_cat: query_am = query_am.filter(Amostra.categoria_amostra == filter_cat)
    
    amostras = query_am.order_by(Amostra.status.desc(), Amostra.nome).all()
    
    return render_template('index.html', view_mode='dashboard', 
                           produtos_showroom=produtos_showroom, amostras=amostras, 
                           categorias=categorias_disponiveis, subcategorias=subcategorias_disponiveis,
                           search_query=search_query, selected_cat=filter_cat, selected_sub=filter_sub,
                           user=session['user_email'], role=role,
                           total_estoque=total_estoque, valor_estoque=valor_estoque, 
                           ticket_medio=ticket_medio, mov_hoje=movimentacoes_hoje)

@app.route('/showroom/protocolos')
def listar_protocolos():
    if 'user_email' not in session: return redirect('/showroom/')
    role = session.get('user_role', 'PUBLIC')
    if role == 'ADMINISTRATOR':
        protocolos = Protocolo.query.order_by(Protocolo.id.desc()).all()
    else:
        protocolos = Protocolo.query.filter_by(vendedor_email=session['user_email']).order_by(Protocolo.id.desc()).all()
    
    return render_template('index.html', view_mode='protocolos', protocolos=protocolos, user=session['user_email'], role=role)

@app.route('/showroom/protocolo/novo', methods=['GET', 'POST'])
def novo_protocolo():
    if 'user_email' not in session: return redirect('/showroom/')
    
    if request.method == 'POST':
        acao = request.form.get('acao')
        
        cliente_dados = {
            'nome': request.form.get('cliente_nome'),
            'sobrenome': request.form.get('cliente_sobrenome'),
            'empresa': request.form.get('cliente_empresa'),
            'cnpj': request.form.get('cliente_cnpj'),
            'ie': request.form.get('endereco_ie'), 
            'email': request.form.get('cliente_email'),
            'telefone': request.form.get('cliente_telefone'),
            
            'vendedor_nome': request.form.get('vendedor_nome'),
            'vendedor_telefone': request.form.get('vendedor_telefone'),

            'cep': request.form.get('endereco_cep'),
            'rua': request.form.get('endereco_rua'),
            'numero': request.form.get('endereco_numero'),
            'bairro': request.form.get('endereco_bairro'),
            'cidade': request.form.get('endereco_cidade'),
            'uf': request.form.get('endereco_uf'),
            'data_prevista': request.form.get('data_prevista'),
            'data_envio': request.form.get('data_envio'),
            'transportadora': request.form.get('transportadora'),
            'rastreio': request.form.get('rastreio'),
            'entregador': request.form.get('entregador')
        }

        endereco_str = f"{cliente_dados['rua']}, {cliente_dados['numero']} - {cliente_dados['bairro']}, {cliente_dados['cidade']}/{cliente_dados['uf']} - CEP: {cliente_dados['cep']}"

        skus = request.form.getlist('item_sku[]')
        nomes = request.form.getlist('item_nome[]')
        qtds = request.form.getlist('item_qtd[]')

        itens_processados = []
        erros_validacao = []

        for i in range(len(skus)):
            if nomes[i].strip():
                qtd_val = int(qtds[i])
                prod = Produto.query.filter(
                    or_(Produto.sku_produtos == skus[i], Produto.nome == nomes[i]),
                    Produto.categoria_produtos.ilike('%showroom%')
                ).first()

                if acao == 'confirmar' and prod:
                    if prod.quantidade < qtd_val:
                        erros_validacao.append(f"Produto '{prod.nome}' só tem {prod.quantidade} unidades em estoque (solicitado: {qtd_val}).")
                
                amostra = Amostra.query.filter(
                    or_(Amostra.sku_amostras == skus[i], Amostra.nome == nomes[i])
                ).first()
                if acao == 'confirmar' and amostra:
                    if amostra.status != 'DISPONIVEL':
                        erros_validacao.append(f"Amostra '{amostra.nome}' não está DISPONÍVEL (Status atual: {amostra.status}).")

                preco = float(prod.valor_unitario) if (prod and prod.valor_unitario) else 0.0
                subtotal = preco * qtd_val
                itens_processados.append({
                    "sku": skus[i], "nome": nomes[i], "qtd": qtd_val,
                    "preco_unit": preco, "subtotal": subtotal
                })

        if acao == 'revisar':
            return render_template('index.html', view_mode='novo_protocolo', 
                                   user=session['user_email'], preview_mode=True,
                                   dados_cliente=cliente_dados, itens_preview=itens_processados,
                                   total_geral=0, produtos_db=[]) 

        elif acao == 'confirmar':
            if erros_validacao:
                todos_produtos = Produto.query.filter(Produto.categoria_produtos.ilike('%showroom%')).with_entities(Produto.sku_produtos, Produto.nome).all()
                lista_final = [{"sku": (p.sku_produtos or ""), "nome": p.nome} for p in todos_produtos]
                return render_template('index.html', view_mode='novo_protocolo', 
                                       user=session['user_email'], produtos_db=lista_final,
                                       erro_validacao=" | ".join(erros_validacao), dados_cliente=cliente_dados)

            try:
                data_prevista = datetime.strptime(cliente_dados['data_prevista'], '%Y-%m-%d')
                data_envio = datetime.strptime(cliente_dados['data_envio'], '%Y-%m-%d') if cliente_dados['data_envio'] else datetime.now()
                
                ultimo_p = Protocolo.query.order_by(Protocolo.id.desc()).first()
                proximo_id = (ultimo_p.id + 1) if ultimo_p else 1

                novo = Protocolo(
                    id=proximo_id,
                    vendedor_email=session['user_email'],
                    
                    vendedor_nome=cliente_dados['vendedor_nome'],
                    vendedor_telefone=cliente_dados['vendedor_telefone'],

                    cliente_nome=cliente_dados['nome'],
                    cliente_sobrenome=cliente_dados['sobrenome'],
                    cliente_empresa=cliente_dados['empresa'],
                    cliente_cnpj=cliente_dados['cnpj'],
                    endereco_ie=cliente_dados['ie'], 
                    cliente_email=cliente_dados['email'],
                    cliente_telefone=cliente_dados['telefone'],
                    
                    endereco_cep=cliente_dados['cep'],
                    endereco_rua=cliente_dados['rua'],
                    endereco_numero=cliente_dados['numero'],
                    endereco_bairro=cliente_dados['bairro'],
                    endereco_cidade=cliente_dados['cidade'],
                    endereco_uf=cliente_dados['uf'],
                    cliente_endereco=endereco_str,
                    
                    transportadora=cliente_dados['transportadora'],
                    rastreio=cliente_dados['rastreio'],
                    entregador_nome=cliente_dados['entregador'],
                    data_envio=data_envio,

                    data_prevista_devolucao=data_prevista,
                    itens_json=itens_processados
                )
                db.session.add(novo)
                
                for item in itens_processados:
                    sku = item.get('sku')
                    nome = item.get('nome')
                    qtd_saida = int(item.get('qtd', 1))

                    amostra_db = None
                    if sku: amostra_db = Amostra.query.filter_by(sku_amostras=sku).first()
                    if not amostra_db and nome: amostra_db = Amostra.query.filter(Amostra.nome.ilike(nome)).first()
                    
                    if amostra_db:
                        amostra_db.status = 'EM_RUA'
                        amostra_db.vendedor_responsavel = session['user_email']
                        amostra_db.cliente_destino = novo.cliente_empresa
                        amostra_db.data_saida = datetime.now()
                        amostra_db.data_prevista_retorno = data_prevista
                        db.session.add(Log(tipo_item='amostra', item_id=amostra_db.id, acao='PROTOCOLO_SAIDA', usuario_nome=session['user_email']))
                    
                    else:
                        prod_db = Produto.query.filter(
                            or_(Produto.sku_produtos == sku, Produto.nome == nome),
                            Produto.categoria_produtos.ilike('%showroom%')
                        ).first()
                        if prod_db:
                            prod_db.quantidade -= qtd_saida
                            db.session.add(Log(
                                tipo_item='produto_showroom', item_id=prod_db.id, 
                                acao='SAIDA_PROTOCOLO', quantidade=qtd_saida,
                                usuario_nome=session['user_email']
                            ))

                db.session.commit()
                return redirect(f'/showroom/protocolo/detalhe/{novo.id}')
                
            except Exception as e:
                db.session.rollback()
                print(f"Erro ao criar protocolo: {traceback.format_exc()}")
                return f"Erro Crítico: {e}"

    todos_produtos = Produto.query.filter(Produto.categoria_produtos.ilike('%showroom%')).with_entities(Produto.sku_produtos, Produto.nome).all()
    lista_final = [{"sku": (p.sku_produtos or ""), "nome": p.nome} for p in todos_produtos]

    return render_template('index.html', view_mode='novo_protocolo', user=session['user_email'], produtos_db=lista_final)

@app.route('/showroom/protocolo/detalhe/<int:id>', methods=['GET', 'POST'])
def detalhe_protocolo(id):
    if 'user_email' not in session: return redirect('/showroom/')
    
    protocolo = Protocolo.query.get_or_404(id)
    msg = None
    erro = None

    if request.method == 'POST':
        acao = request.form.get('acao')
        
        if acao == 'enviar_autentique':
            pdf_bytes = gerar_pdf_protocolo(protocolo)
            resultado = enviar_para_autentique(protocolo, pdf_bytes)
            
            if resultado.get('sucesso'):
                msg = "✅ Documento enviado para o cliente via Autentique com sucesso!"
                protocolo.status = 'AGUARDANDO_ASSINATURA'
                db.session.commit()
                enviar_email_interno(protocolo, pdf_bytes)
            else:
                erro = f"❌ Erro Autentique: {resultado.get('erro')}"

    return render_template('index.html', view_mode='detalhe_protocolo', p=protocolo, msg=msg, erro=erro, user=session['user_email'])

@app.route('/showroom/protocolo/download/<int:id>')
def download_protocolo(id):
    if 'user_email' not in session: return redirect('/showroom/')
    try:
        protocolo = Protocolo.query.get_or_404(id)
        pdf_bytes = gerar_pdf_protocolo(protocolo)
        response = make_response(pdf_bytes)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=Protocolo_{protocolo.id}.pdf'
        return response
    except Exception as e:
        return f"Erro interno ao gerar PDF: {str(e)}", 500

@app.route('/showroom/acao/<tipo>/<int:id>', methods=['GET', 'POST'])
def acao(tipo, id):
    if 'user_email' not in session: return redirect('/showroom/')
    item = None
    msg_sucesso = None

    if tipo == 'produto':
        item = Produto.query.get_or_404(id)
        if 'showroom' in (item.categoria_produtos or '').lower():
            item.sku_amostras = item.sku_produtos
            item.categoria_amostra = item.categoria_produtos
            item.status = 'DISPONIVEL'
            item.vendedor_responsavel = None
            item.codigo_patrimonio = None

            if request.method == 'POST':
                acao_realizada = request.form.get('acao_amostra')
                if acao_realizada in ['vendido', 'fora_linha']:
                    if item.quantidade > 0:
                        item.quantidade -= 1
                        acao_log = 'WEB_BAIXA_VENDIDO' if acao_realizada == 'vendido' else 'WEB_BAIXA_FORA_LINHA'
                        db.session.add(Log(
                            tipo_item='produto_showroom', item_id=item.id, 
                            acao=acao_log, quantidade=1, usuario_nome=session['user_email']
                        ))
                        db.session.commit()
                        msg_sucesso = "1 Unidade baixada do estoque de Showroom."
                    else:
                        msg_sucesso = "Erro: Sem estoque para baixar."
            return render_template('index.html', view_mode='acao', item=item, tipo='amostra', msg=msg_sucesso)
        else:
            return "⛔ Acesso Negado: Este item não pertence ao Showroom."

    elif tipo == 'amostra':
        item = Amostra.query.get_or_404(id)
        if request.method == 'POST':
            acao_realizada = request.form.get('acao_amostra')
            if acao_realizada == 'devolver':
                item.status = 'DISPONIVEL'
                item.vendedor_responsavel = None
                item.cliente_destino = None
                db.session.add(Log(tipo_item='amostra', item_id=item.id, acao='WEB_DEVOLUCAO', usuario_nome=session['user_email']))
            elif acao_realizada == 'vendido':
                item.status = 'VENDIDO'
                item.vendedor_responsavel = session['user_email'] 
                db.session.add(Log(tipo_item='amostra', item_id=item.id, acao='WEB_VENDIDO', usuario_nome=session['user_email']))
            elif acao_realizada == 'fora_linha':
                item.status = 'FORA_DE_LINHA'
                db.session.add(Log(tipo_item='amostra', item_id=item.id, acao='WEB_BAIXA', usuario_nome=session['user_email']))
            
            db.session.commit()
            msg_sucesso = "Status atualizado com sucesso!"

    return render_template('index.html', view_mode='acao', item=item, tipo=tipo, msg=msg_sucesso)

@app.route('/showroom/perfil/senha', methods=['GET', 'POST'])
def alterar_senha():
    if 'user_email' not in session: return redirect('/showroom/')
    msg = None
    erro = None

    if request.method == 'POST':
        nova_senha = request.form.get('nova_senha')
        confirma_senha = request.form.get('confirma_senha')

        if nova_senha != confirma_senha:
            erro = "As senhas não coincidem."
        elif not nova_senha or len(nova_senha) < 4:
            erro = "A senha deve ter pelo menos 4 caracteres."
        else:
            try:
                headers = {
                    "Authorization": f"Bearer {session.get('user_token')}",
                    "Content-Type": "application/json"
                }
                payload = {"password": nova_senha}
                
                resp = requests.patch(f"{DIRECTUS_URL}/users/me", json=payload, headers=headers)

                if resp.status_code == 200:
                    msg = "Senha alterada com sucesso!"
                else:
                    erro = f"Erro ao alterar senha no sistema: {resp.text}"
            except Exception as e:
                erro = f"Erro de conexão: {str(e)}"

    return render_template('index.html', view_mode='alterar_senha', msg=msg, erro=erro, user=session['user_email'])

@app.route('/showroom/logout')
def logout():
    session.clear()
    return redirect('/showroom/')

if slack_app:
    @app.route("/showroom/slack/events", methods=["POST"])
    def slack_events(): return handler.handle(request)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
