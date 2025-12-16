import warnings
# --- SILENCIAR TUDO PARA LIMPAR O LOG ---
warnings.simplefilter(action='ignore', category=FutureWarning)
warnings.simplefilter(action='ignore', category=UserWarning)

import os
import logging
import traceback 
import difflib # Biblioteca para comparação inteligente de texto
from datetime import datetime, timedelta
import requests
import urllib3
import json
from flask import Flask, request, render_template, session, redirect, url_for, jsonify
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_
from slack_bolt import App as BoltApp
from slack_bolt.adapter.flask import SlackRequestHandler
import google.generativeai as genai
from google.generativeai.types import FunctionDeclaration, Tool

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

# --- FUNÇÃO DE BUSCA INTELIGENTE (Fuzzy Match) ---
def encontrar_produto_inteligente(termo_busca):
    """
    Tenta encontrar um produto usando:
    1. Busca exata (SQL ILIKE)
    2. Busca singular (remove 's' final)
    3. Busca por similaridade (difflib)
    Retorna (produto_obj, mensagem_explicativa) ou (None, erro)
    """
    termo_limpo = termo_busca.strip()
    
    # 1. Tentativa SQL Padrão (Nome ou SKU)
    produto = Produto.query.filter(or_(
        Produto.nome.ilike(f'%{termo_limpo}%'),
        Produto.sku_produtos.ilike(f'%{termo_limpo}%')
    )).first()
    
    if produto:
        return produto, None # Encontrou direto

    # 2. Tentativa Singular (Se for plural "cadernos" -> "caderno")
    if termo_limpo.lower().endswith('s'):
        termo_singular = termo_limpo[:-1]
        produto = Produto.query.filter(Produto.nome.ilike(f'%{termo_singular}%')).first()
        if produto:
            return produto, f"(Assumi que '{termo_limpo}' era '{produto.nome}')"

    # 3. Tentativa Fuzzy (Similaridade) - A "Mágica"
    todos_produtos = db.session.query(Produto.id, Produto.nome).all()
    nomes_db = [p.nome for p in todos_produtos]
    
    # Busca o nome mais parecido (cutoff 0.6 significa 60% de semelhança mínima)
    matches = difflib.get_close_matches(termo_limpo, nomes_db, n=1, cutoff=0.5)
    
    if matches:
        nome_encontrado = matches[0]
        produto = Produto.query.filter_by(nome=nome_encontrado).first()
        return produto, f"(Não achei '{termo_limpo}', mas encontrei '{nome_encontrado}'. Usei esse.)"
        
    return None, f"Não encontrei nada parecido com '{termo_busca}'."

# --- TOOLS DO GEMINI ---

def api_alterar_estoque(nome_ou_sku, quantidade, usuario):
    with app.app_context():
        # Usa a busca inteligente agora
        produto, msg_extra = encontrar_produto_inteligente(nome_ou_sku)
        
        if not produto:
            return msg_extra # Retorna o erro de não encontrado
        
        try:
            qtd_int = int(quantidade)
        except:
            return "Erro: Quantidade inválida."

        nova_qtd = produto.quantidade + qtd_int
        if nova_qtd < 0:
             return f"Erro: O produto {produto.nome} só tem {produto.quantidade} unidades. Não dá para retirar {abs(qtd_int)}."

        produto.quantidade = nova_qtd
        
        acao_log = 'CHAT_ENTRADA' if qtd_int > 0 else 'CHAT_SAIDA'
        db.session.add(Log(
            tipo_item='produto', 
            item_id=produto.id, 
            acao=acao_log, 
            quantidade=abs(qtd_int), 
            usuario_nome=usuario
        ))
        db.session.commit()
        
        feedback = f"Sucesso! Estoque de {produto.nome} foi para {produto.quantidade}."
        if msg_extra:
            feedback += f" {msg_extra}"
            
        return feedback

def api_movimentar_amostra(nome_ou_pat, acao, cliente_destino, usuario):
    with app.app_context():
        # Busca Amostra (Lógica simples por enquanto, pode aplicar fuzzy aqui tbm se quiser)
        amostra = Amostra.query.filter(or_(
            Amostra.nome.ilike(f'%{nome_ou_pat}%'),
            Amostra.codigo_patrimonio.ilike(f'%{nome_ou_pat}%'),
            Amostra.sku_amostras.ilike(f'%{nome_ou_pat}%')
        )).first()

        if not amostra:
            # Tenta Fuzzy para amostra também
            todas = db.session.query(Amostra.nome).all()
            nomes = [a.nome for a in todas]
            matches = difflib.get_close_matches(nome_ou_pat, nomes, n=1, cutoff=0.6)
            if matches:
                amostra = Amostra.query.filter_by(nome=matches[0]).first()
            else:
                return f"Erro: Amostra '{nome_ou_pat}' não encontrada."

        if acao.lower() == 'retirar':
            if amostra.status != 'DISPONIVEL':
                return f"Erro: A amostra {amostra.nome} já está com {amostra.vendedor_responsavel}."
            
            amostra.status = 'EM_RUA'
            amostra.vendedor_responsavel = usuario
            amostra.cliente_destino = cliente_destino or "Cliente Não Informado"
            amostra.data_saida = datetime.now()
            amostra.data_prevista_retorno = datetime.now() + timedelta(days=7)
            
            db.session.add(Log(tipo_item='amostra', item_id=amostra.id, acao='CHAT_RETIRADA', usuario_nome=usuario))
        
        elif acao.lower() == 'devolver':
            if amostra.status == 'DISPONIVEL':
                return f"A amostra {amostra.nome} já consta como disponível."
            
            amostra.status = 'DISPONIVEL'
            amostra.vendedor_responsavel = None
            amostra.cliente_destino = None
            
            db.session.add(Log(tipo_item='amostra', item_id=amostra.id, acao='CHAT_DEVOLUCAO', usuario_nome=usuario))
        
        else:
            return "Ação desconhecida. Use 'retirar' ou 'devolver'."

        db.session.commit()
        return f"Feito! Amostra {amostra.nome} agora está {amostra.status}."

def api_consultar(termo):
    with app.app_context():
        # Busca Produto Inteligente
        p, _ = encontrar_produto_inteligente(termo)
        res_p = f"Produto: {p.nome} | Qtd: {p.quantidade} | Local: {p.localizacao}" if p else ""
        
        # Busca Amostra (Simples)
        a = Amostra.query.filter(Amostra.nome.ilike(f'%{termo}%')).first()
        if not a: # Fuzzy fallback
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

# --- ROTAS ---

@app.route('/elostock/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST' and 'login_email' in request.form:
        email = request.form.get('login_email')
        password = request.form.get('login_password')
        try:
            if not DIRECTUS_URL: return render_template('index.html', view_mode='login', erro="Sem URL Directus")
            
            resp = requests.post(
                f"{DIRECTUS_URL}/auth/login", 
                json={"email": email, "password": password},
                verify=False 
            )
            
            if resp.status_code == 200:
                token = resp.json()['data']['access_token']
                session['user_token'] = token
                session['user_email'] = email
                session['chat_history'] = [] # Inicia histórico vazio
                
                headers = {"Authorization": f"Bearer {token}"}
                user_info = requests.get(
                    f"{DIRECTUS_URL}/users/me?fields=role.name", 
                    headers=headers,
                    verify=False
                )
                
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

    if 'user_email' in session: 
        return redirect('/elostock/dashboard')
    return render_template('index.html', view_mode='login')

@app.route('/elostock/dashboard')
def dashboard():
    if 'user_email' not in session: return redirect('/elostock/')
    role = session.get('user_role', 'PUBLIC')
    search_query = request.args.get('q', '').strip()
    filter_cat = request.args.get('cat', '').strip()

    produtos = []
    amostras = []
    categorias_disponiveis = set()

    ver_tudo = role == 'ADMINISTRATOR'
    ver_compras = role == 'COMPRAS' or ver_tudo
    ver_vendas = role == 'VENDAS' or ver_tudo
    
    if ver_compras:
        query = Produto.query
        if search_query:
            query = query.filter(or_(Produto.nome.ilike(f'%{search_query}%'), Produto.sku_produtos.ilike(f'%{search_query}%')))
        if filter_cat:
            query = query.filter(Produto.categoria_produtos == filter_cat)
        produtos = query.order_by(Produto.nome).all()
        cats = db.session.query(Produto.categoria_produtos).distinct().all()
        for c in cats:
            if c.categoria_produtos: categorias_disponiveis.add(c.categoria_produtos)
        
    if ver_vendas:
        query = Amostra.query
        if search_query:
            query = query.filter(or_(Amostra.nome.ilike(f'%{search_query}%'), Amostra.sku_amostras.ilike(f'%{search_query}%')))
        if filter_cat:
            query = query.filter(Amostra.categoria_amostra == filter_cat)
        amostras = query.order_by(Amostra.status.desc(), Amostra.nome).all()
        cats = db.session.query(Amostra.categoria_amostra).distinct().all()
        for c in cats:
            if c.categoria_amostra: categorias_disponiveis.add(c.categoria_amostra)
    
    return render_template('index.html', view_mode='dashboard', 
                           produtos=produtos, 
                           amostras=amostras, 
                           categorias=sorted(list(categorias_disponiveis)),
                           search_query=search_query,
                           selected_cat=filter_cat,
                           user=session['user_email'],
                           role=role) 

# --- ROTA API CHAT COM LÓGICA REFINADA ---
@app.route('/elostock/api/chat', methods=['POST'])
def api_chat():
    if 'user_email' not in session:
        return jsonify({"response": "Você precisa estar logado."}), 401
    
    data = request.json
    user_msg = data.get('message')
    usuario_atual = session['user_email']

    # Gerencia histórico simples na sessão
    historico = session.get('chat_history', [])
    
    # Limita histórico para não estourar a sessão (últimas 6 mensagens)
    if len(historico) > 6:
        historico = historico[-6:]

    if not GEMINI_API_KEY:
         return jsonify({"response": "ERRO: GEMINI_API_KEY não configurada no servidor."})

    try:
        # 1. Detecta modelos (mantido para garantir compatibilidade)
        modelos_disponiveis = []
        try:
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    modelos_disponiveis.append(m.name)
        except:
            pass # Se falhar a listagem, tenta hardcoded abaixo

        modelo_escolhido = 'models/gemini-1.5-flash' # Preferência
        
        # Tenta achar Flash ou Pro na lista real
        if modelos_disponiveis:
            found_flash = next((m for m in modelos_disponiveis if 'flash' in m.lower()), None)
            found_pro = next((m for m in modelos_disponiveis if 'pro' in m.lower()), None)
            if found_flash: modelo_escolhido = found_flash
            elif found_pro: modelo_escolhido = found_pro
            else: modelo_escolhido = modelos_disponiveis[0]

        print(f"DEBUG: Modelo: {modelo_escolhido} | User: {usuario_atual}", flush=True)

        generation_config = {
            "temperature": 0.3, # Mais preciso, menos criativo
            "top_p": 0.95,
            "top_k": 40,
            "max_output_tokens": 1024,
            "response_mime_type": "text/plain",
        }

        model = genai.GenerativeModel(
            model_name=modelo_escolhido, 
            tools=tools_gemini,
            generation_config=generation_config
        )

        # Inicia chat SEMPRE com o histórico da sessão
        # Precisamos converter o histórico simples para o formato do Gemini se quiséssemos stateful
        # Mas para simplicidade e robustez, vamos injetar o histórico no prompt
        
        hist_str = "\n".join([f"{h['role']}: {h['text']}" for h in historico])
        
        prompt_sistema = f"""
        Você é o assistente inteligente do EloStock. Usuário: {usuario_atual}.
        
        Histórico recente da conversa:
        {hist_str}

        REGRAS DE INTELEGÊNCIA:
        1. Se o usuário pedir algo genérico como "retirei cadernos", o sistema de busca vai tentar achar o melhor produto.
        2. Se a função retornar que atualizou um produto com nome diferente (ex: usuário disse "cadernos", sistema achou "Caderno Executivo"), INFORME ISSO CLARAMENTE ao usuário na resposta final. Diga "Entendi que você quis dizer X".
        3. SEMPRE passe '{usuario_atual}' no argumento 'usuario' das funções.
        4. Números: "peguei 5" = -5 (retirada). "chegou 5" = +5 (entrada).
        """
        
        chat = model.start_chat(enable_automatic_function_calling=True)
        response = chat.send_message(f"{prompt_sistema}\nUsuário diz: {user_msg}")
        
        # Salva no histórico
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
    if tipo == 'produto' and role == 'VENDAS': return "⛔ Acesso Negado"
    if tipo == 'amostra' and role == 'COMPRAS': return "⛔ Acesso Negado"

    msg_sucesso = None
    item = None

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
            
            db.session.add(Log(tipo_item='amostra', item_id=item.id, acao=acao_realizada.upper(), usuario_nome=session['user_email']))
            db.session.commit()
            msg_sucesso = "Logística atualizada!"

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
