from flask import Flask, send_from_directory, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room
from guru import Jogo
from flask import send_from_directory
import os
import qrcode
import io
import base64
import random
import string
import uuid
import eventlet

app = Flask(__name__, static_folder='../frontend', template_folder='../frontend')
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")
jogos_ativos = {} 
salas_info = {} 


def gerar_id_sala(tamanho=5):
    while True:
        id_sala = ''.join(random.choices(string.ascii_uppercase + string.digits, k=tamanho))
        if id_sala not in jogos_ativos:
            return id_sala

def gerar_qr_code(url):
    img = qrcode.make(url)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    img_str = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{img_str}"

@app.route('/favicon.ico')
def favicon():
    """Serve o Logo.svg como o favicon."""
    return send_from_directory(
        os.path.join(app.static_folder, 'css', 'img'),
        'Logo.svg', 
        mimetype='image/svg+xml'
    )
    
@app.route('/')
def home():
    return send_from_directory(app.template_folder, 'index.html')

@app.route('/mestre')
def mestre_dashboard():
    return send_from_directory(app.template_folder, 'mestre.html')

@app.route('/lobby')
def lobby_jogo():
    return send_from_directory(app.template_folder, 'lobby.html')

@app.route('/jogador')
def jogador_dashboard():
    return send_from_directory(app.template_folder, 'jogador.html')

@app.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory(app.static_folder, filename)


@socketio.on('iniciar_jogo')
def handle_iniciar_jogo(dados_equipes):
    try:
        id_sala = gerar_id_sala()
        jogo = Jogo()
        status_inicial = jogo.iniciar_jogo(dados_equipes.get("equipes"))
        
        jogos_ativos[id_sala] = jogo
        
        nomes_equipes = [startup.get("nome") for startup in status_inicial]
        
        jogadores_info_inicial = {
            nome: {"sid": None, "conectado": False} for nome in nomes_equipes
        }
        
        salas_info[id_sala] = {
            "mestre_sid": request.sid, 
            "nomes_equipes": nomes_equipes,
            "jogadores_info": jogadores_info_inicial,
            "validacao_pendente": [] 
        }
        
        print(f"Novo jogo criado! Sala ID: {id_sala}, Mestre SID: {request.sid}")

        base_url = request.host_url
        dados_lobby = []
        for startup in status_inicial:
            nome_startup = startup.get("nome")
            url_jogador = f"{base_url}jogador?sala={id_sala}&startup={nome_startup}"
            
            dados_lobby.append({
                "nome_startup": nome_startup,
                "url": url_jogador,
                "qr_code": gerar_qr_code(url_jogador),
                "jogadores": startup.get("jogadores", [])
            })

        emit('jogo_criado', {"id_sala": id_sala, "dados_lobby": dados_lobby})
        
    except Exception as e:
        print(f"Erro ao iniciar jogo: {e}")
        emit('erro_jogo', {"mensagem": str(e)})

@socketio.on('entrar_sala_mestre')
def handle_entrar_sala_mestre(data):
    id_sala = data.get('id_sala')
    jogo = jogos_ativos.get(id_sala)
    
    if jogo and id_sala in salas_info:
        join_room(id_sala) 
        salas_info[id_sala]["mestre_sid"] = request.sid
        print(f"Mestre (re)entrou na sala: {id_sala}")
        
        jogadores_info = salas_info[id_sala].get("jogadores_info", {})
        jogadores_conectados = [nome for nome, info in jogadores_info.items() if info["conectado"]]
        
        emit('status_lobby_atual', {"jogadores_conectados": jogadores_conectados})
        
        emit('fila_validacao_atual', {"fila": salas_info[id_sala]["validacao_pendente"]})
        
        emit('atualizar_estado', jogo.get_status_completo()) 
            
    else:
        emit('erro_jogo', {"mensagem": "Sala não encontrada."})

@socketio.on('entrar_sala_jogador')
def handle_entrar_sala_jogador(data):
    id_sala = data.get('id_sala')
    nome_startup = data.get('nome_startup')
    jogo = jogos_ativos.get(id_sala)
    
    if not jogo:
        emit('jogo_nao_encontrado')
        return

    join_room(id_sala)
    
    if id_sala in salas_info and nome_startup in salas_info[id_sala]["jogadores_info"]:
        salas_info[id_sala]["jogadores_info"][nome_startup]["sid"] = request.sid
        salas_info[id_sala]["jogadores_info"][nome_startup]["conectado"] = True
        print(f"Jogador '{nome_startup}' (re)conectou com SID: {request.sid}")

        mestre_sid = salas_info[id_sala].get("mestre_sid")
        if mestre_sid:
            emit('jogador_conectou', {"nome_startup": nome_startup}, to=mestre_sid)

    emit('atualizar_estado', jogo.get_status_completo())

@socketio.on('disconnect')
def handle_disconnect():
    sid_desconectado = request.sid
    print(f"Cliente desconectado: {sid_desconectado}")

    for id_sala, info_sala in salas_info.items():
        jogadores_info = info_sala.get("jogadores_info", {})
        for nome_startup, info_jogador in jogadores_info.items():
            if info_jogador["sid"] == sid_desconectado:
                info_jogador["conectado"] = False
                info_jogador["sid"] = None
                print(f"Jogador '{nome_startup}' da sala '{id_sala}' foi marcado como desconectado.")
                
                mestre_sid = info_sala.get("mestre_sid")
                if mestre_sid:
                    emit('jogador_desconectou', {"nome_startup": nome_startup}, to=mestre_sid)
                
                return

@socketio.on('submeter_acao')
def handle_submeter_acao(data):
    id_sala = data.get('id_sala')
    if id_sala not in salas_info:
        emit('erro_jogo', {"mensagem": "Sala não encontrada."})
        return
        
    mestre_sid = salas_info[id_sala].get("mestre_sid")
    if not mestre_sid:
        emit('erro_jogo', {"mensagem": "Mestre não encontrado nesta sala."})
        return

    data['jogador_sid'] = request.sid
    data['id_unico_acao'] = str(uuid.uuid4())
    salas_info[id_sala]["validacao_pendente"].append(data)

    print(f"Ação submetida pela startup {data.get('nome_startup')}. A notificar o Mestre (SID: {mestre_sid}).")
    emit('acao_submetida', data, to=mestre_sid) 

@socketio.on('validar_acao')
def handle_validar_acao(data):
    id_sala = data.get('id_sala')
    aprovada = data.get('aprovada')
    acao_data = data.get('acao_data')
    
    jogo = jogos_ativos.get(id_sala)
    if not jogo:
        emit('erro_jogo', {"mensagem": "Jogo não encontrado."})
        return
    
    id_unico_para_remover = acao_data.get('id_unico_acao')
    if id_unico_para_remover:
        fila = salas_info[id_sala]["validacao_pendente"]
        salas_info[id_sala]["validacao_pendente"] = [
            item for item in fila if item.get('id_unico_acao') != id_unico_para_remover
        ]
        
    if not aprovada:
        print(f"Ação de {acao_data['nome_startup']} RECUSADA pelo Mestre.")
        
        socketio.emit('log_mensagem', {
            "mensagem": f"Ação '{acao_data.get('acao_nome')}' da startup {acao_data['nome_startup']} foi RECUSADA pelo Mestre.",
            "tipo": "log-aviso"
        }, to=id_sala)
        
        jogador_sid_atual = salas_info[id_sala]["jogadores_info"][acao_data['nome_startup']].get("sid")
        if jogador_sid_atual:
            emit('acao_recusada', acao_data, to=jogador_sid_atual)
        else:
            print(f"Não foi possível enviar 'acao_recusada' para {acao_data['nome_startup']}. Jogador offline.")
        
    else:
        print(f"Ação de {acao_data['nome_startup']} APROVADA pelo Mestre.")
        jogo.registrar_acao_aprovada(acao_data)
        
        socketio.emit('log_mensagem', {
            "mensagem": f"Ação '{acao_data.get('acao_nome')}' da {acao_data['nome_startup']} foi APROVADA. (Aguardando resolução)",
            "tipo": "log-normal"
        }, to=id_sala)
    
    socketio.emit('atualizar_estado', jogo.get_status_completo(), to=id_sala)


@socketio.on('mestre_avancar_fase')
def handle_mestre_avancar_fase(data):
    id_sala = data.get('id_sala')
    jogo = jogos_ativos.get(id_sala)
    if not jogo:
        emit('erro_jogo', {"mensagem": "Jogo não encontrado."})
        return
    
    nova_fase = jogo.avancar_fase()
    print(f"Mestre avançou para a fase: {nova_fase} na sala {id_sala}")
    
    socketio.emit('log_mensagem', {
        "mensagem": f"Mestre iniciou a Fase: {nova_fase}",
        "tipo": "log-info"
    }, to=id_sala)
    
    socketio.emit('atualizar_estado', jogo.get_status_completo(), to=id_sala)

@socketio.on('mestre_resolver_turno')
def handle_mestre_resolver_turno(data):
    id_sala = data.get('id_sala')
    jogo = jogos_ativos.get(id_sala)
    if not jogo:
        emit('erro_jogo', {"mensagem": "Jogo não encontrado."})
        return
        
    print(f"Mestre a resolver o turno da sala {id_sala}...")
    
    try:
        resultados = jogo.resolver_turno_completo()
        
        if resultados.get("status") == "erro":
            print(f"Erro ao resolver turno: {resultados.get('mensagem')}")
            emit('erro_jogo', {"mensagem": resultados.get('mensagem')})
            return

    except Exception as e:
        print(f"CRASH CRÍTICO ao resolver turno: {e}")
        emit('erro_jogo', {"mensagem": f"Erro crítico no servidor ao resolver o turno: {e}"})
        return
    
    for log in resultados.get("logs", []):
        socketio.emit('log_mensagem', log, to=id_sala)
        
    for item_evento in resultados.get("eventos", []):
        startup_nome = item_evento.get("startup_nome")
        
        jogador_sid = None
        jogadores_info = salas_info[id_sala].get("jogadores_info", {})
        if startup_nome in jogadores_info:
            jogador_sid = jogadores_info[startup_nome].get("sid")

        if jogador_sid:
            emit('evento_subir_de_nivel', item_evento.get("evento"), to=jogador_sid)
        
        emit('evento_subir_de_nivel', item_evento.get("evento"), to=salas_info[id_sala]["mestre_sid"])

        
    if resultados.get("jogo_terminado"):
        print(f"O jogo na sala {id_sala} terminou! Vencedor: {resultados.get('vencedor')}")
        socketio.emit('jogo_terminou', resultados.get('vencedor'), to=id_sala)
        if id_sala in jogos_ativos: del jogos_ativos[id_sala]
        if id_sala in salas_info: del salas_info[id_sala]
        return

    socketio.emit('atualizar_estado', jogo.get_status_completo(), to=id_sala)

@socketio.on('jogador_pedir_estado')
def handle_pedir_estado(data):
    id_sala = data.get('id_sala')
    jogo = jogos_ativos.get(id_sala)
    if jogo:
        emit('atualizar_estado', jogo.get_status_completo())


if __name__ == '__main__':
    porta = int(os.environ.get("PORT", 5000))
    
    print(f"Servidor Socket.IO a arrancar na porta {porta}")
    
    # Alterado: host='0.0.0.0' e port=porta
    socketio.run(app, host='0.0.0.0', port=porta, debug=True, allow_unsafe_werkzeug=True)
