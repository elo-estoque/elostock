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
from sqlalchemy import or_, and_
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

# Variáveis
DIRECTUS_URL = os.environ.get("DIRECTUS_URL")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET")

# Configuração de E-mail
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

# --- GERADOR PDF REPORTLAB ---
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
    
    # Datas e Vendedor
    d_envio = protocolo.data_criacao.strftime('%d/%m/%Y') if protocolo.data_criacao else '--/--/----'
    d_dev = protocolo.data_prevista_devolucao.strftime('%d/%m/%Y') if protocolo.data_prevista_devolucao else '--/--/----'
    
    dados_topo = [
        [f"DATA DE ENVIO: {d_envio}", f"DEVOLUÇÃO PREVISTA: {d_dev}"],
        [f"VENDEDOR: {protocolo.vendedor_email or ''}", ""]
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
        ["Empresa:", protocolo.cliente_empresa or ''],
        ["Contato:", protocolo.cliente_nome or ''],
        ["CNPJ:", protocolo.cliente_cnpj or ''],
        ["Email:", protocolo.cliente_email or ''],
        ["Telefone:", protocolo.cliente_telefone or ''],
        ["Endereço:", protocolo.cliente_endereco or '']
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
    
    data_itens = [['SKU', 'PRODUTO / DESCRIÇÃO', 'QTD', 'UNIT.', 'TOTAL']]
    total_protocolo = 0.0
    
    if protocolo.itens_json:
        for item in protocolo.itens_json:
            try:
                sku_txt = str(item.get('sku') or '-')
                nome_txt = str(item.get('nome') or 'Item sem nome')
                qtd_txt = str(item.get('qtd') or '1')
                
                raw_unit = item.get('preco_unit')
                raw_total = item.get('subtotal')
                
                val_unit = float(raw_unit) if raw_unit is not None else 0.0
                val_total = float(raw_total) if raw_total is not None else 0.0
                
                total_protocolo += val_total
                
                data_itens.append([
                    sku_txt,
                    nome_txt,
                    qtd_txt,
                    f"R$ {val_unit:.2f}",
                    f"R$ {val_total:.2f}"
                ])
            except Exception as e:
                print(f"Erro processando item PDF: {e}")
                data_itens.append(["ERR", "Erro nos dados do item", "0", "0.00", "0.00"])
            
    # Total Geral
    data_itens.append(['', '', '', 'TOTAL:', f"R$ {total_protocolo:.2f}"])
    
    t_itens = Table(data_itens, colWidths=[3*cm, 8.5*cm, 2*cm, 2.5*cm, 2.5*cm])
    t_itens.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.darkred),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('ALIGN', (1, 0), (1, -1), 'LEFT'), 
        ('ALIGN', (3, 1), (-1, -1), 'RIGHT'), 
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

def enviar_email_protocolo(protocolo, pdf_bytes):
    if not SMTP_USER or not SMTP_PASS:
        print("⚠️ SMTP não configurado. Email não enviado.")
        return

    try:
        msg = MIMEMultipart()
        msg['From'] = SMTP_USER
        msg['To'] = f"{protocolo.vendedor_email}, {EMAIL_CHEFE}"
        msg['Subject'] = f"Protocolo #{protocolo.id} - {protocolo.cliente_empresa}"

        body = f"Olá,\n\nSegue em anexo o protocolo #{protocolo.id} gerado para o cliente {protocolo.cliente_empresa}.\n\nSistema Elo Showroom."
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
                user_info = requests.get(f"{DIRECTUS_URL}/users/me?fields=role.name", headers=headers, verify=False)
                if user_info.status_code == 200:
                    data = user_info.json().get('data', {})
                    role_name = data.get('role', {}).get('name', 'Public') if data.get('role') else 'Public'
                    session['user_role'] = role_name.upper()
                else:
                    session['user_role'] = 'PUBLIC'
                return redirect('/showroom/dashboard')
            return render_template('index.html', view_mode='login', erro="Credenciais inválidas.")
        except Exception as e:
            return render_template('index.html', view_mode='login', erro=f"Erro de Conexão: {str(e)}")
    if 'user_email' in session: return redirect('/showroom/dashboard')
    return render_template('index.html', view_mode='login')

@app.route('/showroom/dashboard')
def dashboard():
    if 'user_email' not in session: return redirect('/showroom/')
    role = session.get('user_role', 'PUBLIC')
    search_query = request.args.get('q', '').strip()
    filter_cat = request.args.get('cat', '').strip()
    filter_sub = request.args.get('sub', '').strip() 

    # Somente Showroom e Amostras
    produtos_showroom = [] 
    amostras = []
    
    # Busca Categorias somente de itens Showroom/Amostra
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
    
    # 1. Produtos do Showroom (Tem Quantidade e Categoria "Showroom")
    query_sp = Produto.query.filter(Produto.categoria_produtos.ilike('%showroom%'))
    if search_query: 
        query_sp = query_sp.filter(or_(Produto.nome.ilike(f'%{search_query}%'), Produto.sku_produtos.ilike(f'%{search_query}%')))
    if filter_cat:
         query_sp = query_sp.filter(Produto.categoria_produtos == filter_cat)
    if filter_sub: 
         query_sp = query_sp.filter(Produto.subcategoria == filter_sub)
         
    produtos_showroom = query_sp.order_by(Produto.nome).all()

    # 2. Amostras Únicas (Patrimônio)
    query_am = Amostra.query
    if search_query: query_am = query_am.filter(or_(Amostra.nome.ilike(f'%{search_query}%'), Amostra.sku_amostras.ilike(f'%{search_query}%')))
    if filter_cat: query_am = query_am.filter(Amostra.categoria_amostra == filter_cat)
    
    amostras = query_am.order_by(Amostra.status.desc(), Amostra.nome).all()
    
    return render_template('index.html', view_mode='dashboard', 
                           produtos_showroom=produtos_showroom, amostras=amostras, 
                           categorias=categorias_disponiveis, subcategorias=subcategorias_disponiveis,
                           search_query=search_query, selected_cat=filter_cat, selected_sub=filter_sub,
                           user=session['user_email'], role=role)

@app.route('/showroom/protocolos')
def listar_protocolos():
    if 'user_email' not in session: return redirect('/showroom/')
    role = session.get('user_role', 'PUBLIC')
    # Admin ou Vendas veem seus protocolos (ou todos no caso de Admin)
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

        # --- PREVIEW DO PROTOCOLO ---
        if acao == 'revisar':
            dados_cliente = {
                'nome': request.form.get('cliente_nome'),
                'empresa': request.form.get('cliente_empresa'),
                'cnpj': request.form.get('cliente_cnpj'),
                'email': request.form.get('cliente_email'),
                'telefone': request.form.get('cliente_telefone'),
                'endereco': request.form.get('cliente_endereco'),
                'data_prevista': request.form.get('data_prevista')
            }
            
            skus = request.form.getlist('item_sku[]')
            nomes = request.form.getlist('item_nome[]')
            qtds = request.form.getlist('item_qtd[]')
            
            itens_preview = []
            total_geral = 0.0
            
            for i in range(len(skus)):
                if nomes[i].strip():
                    qtd_val = int(qtds[i])
                    
                    # 1. Tenta achar em Produto Showroom para pegar preço
                    prod = Produto.query.filter(
                        or_(Produto.sku_produtos == skus[i], Produto.nome == nomes[i]),
                        Produto.categoria_produtos.ilike('%showroom%')
                    ).first()

                    # Se não, Amostras geralmente não têm preço unitário definido aqui, assume 0
                    
                    preco = float(prod.valor_unitario) if (prod and prod.valor_unitario) else 0.0
                    subtotal = preco * qtd_val
                    total_geral += subtotal
                    
                    itens_preview.append({
                        "sku": skus[i], 
                        "nome": nomes[i], 
                        "qtd": qtd_val,
                        "preco_unit": preco,
                        "subtotal": subtotal
                    })

            return render_template('index.html', view_mode='novo_protocolo', 
                                   user=session['user_email'], 
                                   preview_mode=True,
                                   dados_cliente=dados_cliente,
                                   itens_preview=itens_preview,
                                   total_geral=total_geral,
                                   produtos_db=[]) 

        # --- GRAVAR PROTOCOLO ---
        elif acao == 'confirmar':
            try:
                data_prevista = datetime.strptime(request.form.get('data_prevista'), '%Y-%m-%d')
                skus = request.form.getlist('item_sku[]')
                nomes = request.form.getlist('item_nome[]')
                qtds = request.form.getlist('item_qtd[]')
                
                itens_json = []
                for i in range(len(skus)):
                    if nomes[i].strip():
                        qtd_val = int(qtds[i])
                        
                        prod = Produto.query.filter(
                            or_(Produto.sku_produtos == skus[i], Produto.nome == nomes[i]),
                            Produto.categoria_produtos.ilike('%showroom%')
                        ).first()
                        
                        preco_unit = float(prod.valor_unitario) if (prod and prod.valor_unitario) else 0.0
                        subtotal = preco_unit * qtd_val
                        
                        itens_json.append({
                            "sku": skus[i], 
                            "nome": nomes[i], 
                            "qtd": qtd_val,
                            "preco_unit": preco_unit,
                            "subtotal": subtotal
                        })
                
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
                
                # --- PROCESSAR BAIXA/STATUS ---
                for item in itens_json:
                    sku = item.get('sku')
                    nome = item.get('nome')
                    qtd_saida = int(item.get('qtd', 1))

                    # 1. Tenta achar Amostra Única (Patrimônio)
                    amostra_db = None
                    if sku: amostra_db = Amostra.query.filter_by(sku_amostras=sku).first()
                    if not amostra_db and nome: amostra_db = Amostra.query.filter(Amostra.nome.ilike(nome)).first()
                    
                    if amostra_db and amostra_db.status == 'DISPONIVEL':
                        amostra_db.status = 'EM_RUA'
                        amostra_db.vendedor_responsavel = session['user_email']
                        amostra_db.cliente_destino = novo.cliente_empresa
                        amostra_db.data_saida = datetime.now()
                        amostra_db.data_prevista_retorno = data_prevista
                        db.session.add(Log(tipo_item='amostra', item_id=amostra_db.id, acao='PROTOCOLO_SAIDA', usuario_nome=session['user_email']))
                    
                    # 2. Se não for Amostra Única, DEVE ser Produto Showroom (Baixa Estoque)
                    elif not amostra_db:
                        prod_db = Produto.query.filter(
                            or_(Produto.sku_produtos == sku, Produto.nome == nome),
                            Produto.categoria_produtos.ilike('%showroom%')
                        ).first()
                        
                        if prod_db:
                            prod_db.quantidade -= qtd_saida
                            db.session.add(Log(
                                tipo_item='produto_showroom', 
                                item_id=prod_db.id, 
                                acao='SAIDA_PROTOCOLO', 
                                quantidade=qtd_saida,
                                usuario_nome=session['user_email']
                            ))
                        else:
                            print(f"⚠️ AVISO: Item '{nome}' não encontrado nem como Amostra nem como Showroom.")

                db.session.commit()
                
                # Gera e Envia
                pdf_bytes = gerar_pdf_protocolo(novo)
                enviar_email_protocolo(novo, pdf_bytes)
                
                return redirect('/showroom/protocolos')
                
            except Exception as e:
                db.session.rollback()
                print(f"Erro ao criar protocolo: {traceback.format_exc()}")
                return f"Erro Crítico ao Gerar Protocolo: {e}"

    # AUTOCOMPLETE (Carrega TUDO: Produtos Showroom + Amostras)
    todos_produtos = Produto.query.filter(Produto.categoria_produtos.ilike('%showroom%')).with_entities(Produto.sku_produtos, Produto.nome).all()
    todas_amostras = Amostra.query.with_entities(Amostra.sku_amostras, Amostra.nome).all()
    
    lista_final = []
    seen = set()
    
    # Adiciona produtos showroom
    for p in todos_produtos:
        if p.nome not in seen:
            lista_final.append({"sku": (p.sku_produtos or ""), "nome": p.nome})
            seen.add(p.nome)
    
    # Adiciona amostras
    for a in todas_amostras:
        if a.nome not in seen:
            lista_final.append({"sku": (a.sku_amostras or ""), "nome": a.nome})
            seen.add(a.nome)

    return render_template('index.html', view_mode='novo_protocolo', user=session['user_email'], produtos_db=lista_final)

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
        print(f"Erro no Download PDF: {traceback.format_exc()}")
        return f"Erro interno ao gerar PDF: {str(e)}", 500

@app.route('/showroom/acao/<tipo>/<int:id>', methods=['GET', 'POST'])
def acao(tipo, id):
    if 'user_email' not in session: return redirect('/showroom/')
    role = session.get('user_role', 'PUBLIC')
    msg_sucesso = None
    item = None

    if tipo == 'produto':
        # Aqui, por definição do novo escopo, TODO produto acessado deve ser Showroom
        item = Produto.query.get_or_404(id)
        
        # Confirma se é showroom para segurança
        if 'showroom' in (item.categoria_produtos or '').lower():
            
            # Truque para usar o template de Gestão visual (Protocolo/Venda/Baixa)
            tipo_visualizacao = 'amostra' 
            # Mapeia campos para não quebrar o HTML que espera campos de Amostra
            item.sku_amostras = item.sku_produtos
            item.categoria_amostra = item.categoria_produtos
            item.status = 'DISPONIVEL' # Para liberar os botões
            item.vendedor_responsavel = None
            item.codigo_patrimonio = None

            if request.method == 'POST':
                acao_realizada = request.form.get('acao_amostra')
                
                if acao_realizada in ['vendido', 'fora_linha']:
                    if item.quantidade > 0:
                        item.quantidade -= 1
                        acao_log = 'WEB_BAIXA_VENDIDO' if acao_realizada == 'vendido' else 'WEB_BAIXA_FORA_LINHA'
                        db.session.add(Log(
                            tipo_item='produto_showroom', 
                            item_id=item.id, 
                            acao=acao_log, 
                            quantidade=1, 
                            usuario_nome=session['user_email']
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

@app.route('/showroom/logout')
def logout():
    session.clear()
    return redirect('/showroom/')

if slack_app:
    @app.route("/showroom/slack/events", methods=["POST"])
    def slack_events(): return handler.handle(request)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
