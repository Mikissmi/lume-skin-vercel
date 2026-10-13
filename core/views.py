from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import login as auth_login, authenticate, logout as auth_logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from functools import wraps
from .models import Usuario, PerfilDermatologico, Artigo, Especialista, Produto
from .youcam_utils import _extract_score
from .recomendacao import (
    OBJETIVOS, recomendar_produtos, classificar_indicador, montar_rotina, normalizar_objetivo, rotulo_objetivo,
)
import requests
import io
import json
import time
import os
import zipfile


# ==========================================================================
# ACESSO DO ADMIN - decorator que protege as telas administrativas
# ==========================================================================

# substitui o @login_required nas telas de administradores: aqui o acesso
# nao depende de um Usuario cadastrado, so da flag de sessao setada no
# login com o email/senha fixos do admin (ver tela_login)
def admin_required(view_func):
    @wraps(view_func)
    def view_wrapper(request, *args, **kwargs):
        if not request.session.get('admin_logado'):
            return redirect('login')
        return view_func(request, *args, **kwargs)
    return view_wrapper


# ==========================================================================
# INTEGRAÇÃO COM A YOUCAM - configuração, envio da foto e espera do resultado
# ==========================================================================

# URLs da YouCam
YOUCAM_FILE_URL = 'https://yce-api-01.makeupar.com/s2s/v2.0/file'
YOUCAM_URL = 'https://yce-api-01.makeupar.com/s2s/v2.1/task/skin-analysis'

# Ações que pedimos à YouCam (consome 9 creditos por analise, independente da quantidade de indicadores)
YOUCAM_ACOES_BASE = ['acne', 'skin_type']
YOUCAM_ACOES_COMPLETAS = ['acne', 'pore', 'texture', 'oiliness', 'moisture', 'age_spot', 'skin_type']


# cabeçalho da autenticação; lê a chave do arquivo .env
def _youcam_headers():
    api_key = os.getenv('YOUCAM_API_KEY') or getattr(settings, 'YOUCAM_API_KEY', '')
    if not api_key:
        raise RuntimeError('YOUCAM_API_KEY não foi configurada no arquivo .env.')
    return {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }


# imprime o erro da YouCam (HTTP + detalhe) no terminal
def _youcam_error(response, etapa):
    try:
        detalhe = response.json()
    except ValueError:
        detalhe = response.text
    print(f'[YouCam] {etapa} - HTTP {response.status_code}: {detalhe}')


# ==========================================================================
# LEITURA DO RESULTADO DA YOUCAM - extrai notas e imagens do json
# ==========================================================================

# o json muda de formato conforme o recurso (e o plano), entao nao da pra
# apontar um caminho fixo: as funcoes abaixo procuram o valor em qualquer nivel
SUFIXOS_IMAGEM = ('.png', '.jpg', '.jpeg', '.webp')
INDICADORES_LIDOS = ('oiliness', 'moisture', 'acne', 'pore', 'texture', 'age_spot')


# corta a URL no ? para ver so o nome do arquivo (URLs assinadas tem parametros)
def _caminho_da_url(valor):
    return valor.split('?', 1)[0].lower() if isinstance(valor, str) else ''


# procura uma URL de IMAGEM em qualquer nivel do JSON (ignora .zip/.json)
def _extract_url(value):
    if isinstance(value, str):
        if value.startswith('https://') and _caminho_da_url(value).endswith(SUFIXOS_IMAGEM):
            return value
    elif isinstance(value, dict):
        for key in ('overlay_image_url', 'result_image_url', 'image_url'):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.startswith(('http://', 'https://')) \
                    and not _caminho_da_url(candidate).endswith(('.zip', '.json')):
                return candidate
        for item in value.values():
            found = _extract_url(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _extract_url(item)
            if found:
                return found
    return None


# verifica se algum dos indicadores lidos tem nota (ou seja, se a YouCam mediu algum score)
def _tem_algum_score(resultados):
    return any(_extract_score(resultados, chave) is not None for chave in INDICADORES_LIDOS)


# URLs https de arquivos .zip em qualquer nivel do JSON
def _achar_zips(value):
    achados = []
    if isinstance(value, str):
        if value.startswith('https://') and _caminho_da_url(value).endswith('.zip'):
            achados.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            achados.extend(_achar_zips(item))
    elif isinstance(value, list):
        for item in value:
            achados.extend(_achar_zips(item))
    return achados


# baixa o zip de resultados da YouCam e le o score_info.json dentro dele (ate 20 MB)
def _ler_score_info_do_zip(url):
    try:
        resposta = requests.get(url, timeout=30)
        if not resposta.ok or len(resposta.content) > 20 * 1024 * 1024:
            print(f'[YouCam] Não consegui baixar o zip de resultados (HTTP {resposta.status_code}).')
            return None
        with zipfile.ZipFile(io.BytesIO(resposta.content)) as arquivo:
            for nome in arquivo.namelist():
                if nome.lower().endswith('score_info.json'):
                    return json.loads(arquivo.read(nome).decode('utf-8'))
        print('[YouCam] O zip de resultados não tem score_info.json.')
    except (requests.RequestException, zipfile.BadZipFile, ValueError, KeyError) as exc:
        print(f'[YouCam] Erro ao ler o zip de resultados: {exc}')
    return None


# garante um dict e, se os scores nao vierem no JSON, busca-os no .zip de resultados
def _completar_resultados(resultados):
    if not isinstance(resultados, dict):
        resultados = {'output': resultados}
    if not _tem_algum_score(resultados):
        for url in _achar_zips(resultados):
            info = _ler_score_info_do_zip(url)
            if info is not None:
                resultados['score_info'] = info
                break
    return resultados


# mostra (e, em DEBUG, grava em arquivo) a resposta crua quando nenhum score foi reconhecido
def _guardar_resposta_para_diagnostico(resultados):
    texto = json.dumps(resultados, ensure_ascii=False, indent=2, default=str)
    print('[YouCam] Nenhum score reconhecido na resposta. Resposta recebida:')
    print(texto[:6000])
    if settings.DEBUG:
        try:
            with open(os.path.join(settings.BASE_DIR, 'youcam_ultima_resposta.json'), 'w', encoding='utf-8') as arq:
                arq.write(texto)
            print('[YouCam] Resposta completa gravada em youcam_ultima_resposta.json')
        except OSError as exc:
            print(f'[YouCam] Não consegui gravar o arquivo de diagnóstico: {exc}')


# ==========================================================================
# FLUXO COMPLETO DA ANÁLISE - criar a tarefa, esperar e devolver os resultados
# ==========================================================================

# cria a tarefa de analise com os recursos `acoes` e espera o resultado.
# Devolve (resultados, motivo). motivo: 'ok', 'recusada' (a YouCam rejeitou a tarefa),
# 'erro_na_tarefa', 'sem_task_id', 'sem_resultados' ou 'tempo_esgotado'
def _analisar_na_youcam(headers, file_id, acoes):
    task_payload = {
        'src_file_id': file_id,
        'dst_actions': list(acoes),
        'miniserver_args': {
            'enable_mask_overlay': True,
        },
        'format': 'json',
        'pf_camera_kit': False,
    }

    resposta_task = requests.post(
        YOUCAM_URL,
        headers=headers,
        json=task_payload,
        timeout=30,
    )

    if not resposta_task.ok:
        _youcam_error(resposta_task, 'Criação da tarefa Skin Analysis')
        return None, 'recusada'

    task_id = resposta_task.json().get('data', {}).get('task_id')
    if not task_id:
        print(f'[YouCam] Tarefa criada sem task_id: {resposta_task.text}')
        return None, 'sem_task_id'

    # Consulta o resultado. A API é assíncrona.
    for tentativa in range(30):
        time.sleep(2)

        checagem = requests.get(
            f'{YOUCAM_URL}/{task_id}',
            headers=headers,
            timeout=20,
        )

        if not checagem.ok:
            _youcam_error(checagem, f'Consulta da tarefa (tentativa {tentativa + 1})')
            continue

        payload = checagem.json()
        data = payload.get('data', {})
        status = data.get('task_status')

        print(f'[YouCam] tarefa={task_id} status={status}')

        if status == 'success':
            resultados = data.get('results')
            if not resultados:
                print(f'[YouCam] Tarefa concluída, mas sem results: {payload}')
                return None, 'sem_resultados'

            # Guarda a URL do resultado, caso exista, para o mapeamento facial.
            if isinstance(resultados, dict):
                resultado_url = _extract_url(resultados)
                if resultado_url and 'result_image_url' not in resultados:
                    resultados['result_image_url'] = resultado_url

            return resultados, 'ok'

        if status == 'error':
            print(f'[YouCam] Tarefa terminou com erro: {payload}')
            return None, 'erro_na_tarefa'

    print('[YouCam] Tempo limite aguardando o resultado da análise.')
    return None, 'tempo_esgotado'


# envia uma imagem local para a YouCam. Passos:
# 1- File API -> recebe file_id + URL assinada
# 2- PUT da imagem na URL assinada
# 3- cria a tarefa Skin Analysis v2.1 usando src_file_id
# 4- faz polling ate success/error
# Devolve o dict de resultados ou None se algo falhar.
def comunicar_youcam_api(foto_arquivo):
    try:
        headers = _youcam_headers()

        # Lê a imagem uma única vez e mantém os bytes disponíveis para o PUT.
        foto_arquivo.seek(0)
        conteudo = foto_arquivo.read()
        if not conteudo:
            print('[YouCam] A imagem recebida está vazia.')
            return None

        content_type = foto_arquivo.content_type or 'image/jpeg'
        if content_type == 'image/jpg':
            content_type = 'image/jpeg'

        # 1. Solicita URL de upload e file_id.
        file_payload = {
            'files': [{
                'content_type': content_type,
                'file_name': foto_arquivo.name or 'scan.jpg',
                'file_size': len(conteudo),
            }]
        }

        resposta_file = requests.post(
            YOUCAM_FILE_URL,
            headers=headers,
            json=file_payload,
            timeout=30,
        )

        if not resposta_file.ok:
            _youcam_error(resposta_file, 'File API')
            return None

        file_data = resposta_file.json().get('data', {}).get('files', [])
        if not file_data:
            print(f'[YouCam] File API não retornou files: {resposta_file.text}')
            return None

        file_info = file_data[0]
        file_id = file_info.get('file_id')
        requests_info = file_info.get('requests') or []
        upload_request = next((item for item in requests_info if item.get('method', 'PUT').upper() == 'PUT'), None)
        upload_url = upload_request.get('url') if upload_request else None

        if not file_id or not upload_url:
            print(f'[YouCam] File API não retornou file_id/upload URL: {file_info}')
            return None

        # 2. Envia a imagem para a URL assinada.
        upload_headers = dict(upload_request.get('headers') or {})
        upload_headers['Content-Type'] = content_type
        upload_headers['Content-Length'] = str(len(conteudo))

        resposta_upload = requests.put(
            upload_url,
            headers=upload_headers,
            data=conteudo,
            timeout=60,
        )

        if not resposta_upload.ok:
            _youcam_error(resposta_upload, 'Upload da imagem')
            return None

        # 3 e 4. Cria a tarefa e espera o resultado. Primeiro pede todos os recursos que o
        # scanner mostra; se a YouCam recusar (plano ou nome de recurso), repete so com os
        # basicos para a analise nao parar de funcionar.
        resultados, motivo = _analisar_na_youcam(headers, file_id, YOUCAM_ACOES_COMPLETAS)

        if resultados is None and motivo in ('recusada', 'erro_na_tarefa'):
            print('[YouCam] Tentando de novo só com os recursos básicos.')
            resultados, motivo = _analisar_na_youcam(headers, file_id, YOUCAM_ACOES_BASE)

        return resultados

    except requests.RequestException as exc:
        print(f'[Erro de conexão YouCam API]: {exc}')
    except (ValueError, KeyError, TypeError) as exc:
        print(f'[Erro ao interpretar resposta YouCam API]: {exc}')
    except Exception as exc:
        print(f'[Erro YouCam API]: {exc}')

    return None


# ==========================================================================
# SCORE DE SAÚDE DA PELE - calculo feito so com as respostas do questionario
# ==========================================================================

# score de saude (0 a 100): media ponderada das respostas do questionario.
# cada resposta vira uma nota e os pesos somam 1.0, entao o resultado
# tambem fica entre 0 e 100. se a resposta vier invalida, o .get() usa 70
PESO_FOTOTIPO = 0.40
PESO_TIPO_PELE = 0.30
PESO_MAQUIAGEM = 0.15
PESO_ALERGIA = 0.15

# Nota de 0 a 100 pra cada resposta possivel
NOTAS_FOTOTIPO = {
    1: 20,   # sempre queima, nunca bronzeia == pouca protecao natural contra UV
    2: 45,   # sempre queima, bronzeia pouco
    3: 75,   # queima moderado, bronzeia gradualmente
    4: 85,   # raramente queima == mais protecao natural contra UV
    5: 95,   # quase nunca queima == muita protecao natural contra UV
    6: 100,  # nunca queima (fototipo VI) == maxima protecao natural contra UV
}

NOTAS_TIPO_PELE = {
    'normal': 100,  # pele equilibrada
    'mista': 75,    # duas tendencias ao mesmo tempo (oleosa na zona T, seca nas bochechas)
    'seca': 60,     # barreira cutanea mais fragil, mais sensivel a irritacao
    'oleosa': 55,   # mais producao de sebo, mais tendencia a cravos e acne
}

NOTA_MAQUIAGEM_SIM = 50
NOTA_MAQUIAGEM_NAO = 100

NOTA_ALERGIA_SIM = 60
NOTA_ALERGIA_NAO = 100


# calcula o score so com as respostas do questionario (sem depender da ia)
def calcular_porcentagem_saude(tipo_pele, fototipo, usa_maquiagem, tem_alergia):
    nota_fototipo = NOTAS_FOTOTIPO.get(fototipo, 70)
    nota_tipo_pele = NOTAS_TIPO_PELE.get(tipo_pele, 70)
    nota_maquiagem = NOTA_MAQUIAGEM_SIM if usa_maquiagem == 'sim' else NOTA_MAQUIAGEM_NAO
    nota_alergia = NOTA_ALERGIA_SIM if tem_alergia == '1' else NOTA_ALERGIA_NAO

    score = (
        nota_fototipo * PESO_FOTOTIPO +
        nota_tipo_pele * PESO_TIPO_PELE +
        nota_maquiagem * PESO_MAQUIAGEM +
        nota_alergia * PESO_ALERGIA
    )

    return round(score)


# ==========================================================================
# VALIDAÇÕES - checagem de foto enviada e conversão segura de números
# ==========================================================================

TAMANHO_MAX_FOTO = 5 * 1024 * 1024  # 5 MB (mesmo limite do formulario)


# descobre o tipo REAL da imagem pelos primeiros bytes (nao confia no que o navegador informa)
def _tipo_imagem(arquivo):
    arquivo.seek(0)
    inicio = arquivo.read(12)
    arquivo.seek(0)
    if inicio.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if inicio.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if inicio[:4] == b'RIFF' and inicio[8:12] == b'WEBP':
        return 'image/webp'
    return None


# True se for JPG/PNG/WebP de ate 5 MB. Ajusta o content_type para o tipo real da imagem
def _foto_valida(arquivo):
    if arquivo.size > TAMANHO_MAX_FOTO:
        return False
    tipo = _tipo_imagem(arquivo)
    if tipo is None:
        return False
    arquivo.content_type = tipo
    return True


# '1'/'sim' -> True, '0'/'nao' -> False. Qualquer outra coisa -> None (nao respondeu / valor invalido)
def _sim_nao(valor):
    if valor in ('1', 'sim'):
        return True
    if valor in ('0', 'nao'):
        return False
    return None


# tipo de acne so vale quando o objetivo e "reduzir acne". "Nao sei" (vazio ou qualquer valor
# desconhecido) vira '' e a rotina usa Comedonal com aviso. `atual` e o valor a manter se o formulario nao enviou o campo
def _tipo_acne_valido(enviado, atual, objetivo):
    valor = atual if enviado is None else enviado
    if normalizar_objetivo(objetivo) == 'reduzir acne' and valor in dict(PerfilDermatologico.TIPO_ACNE_CHOICES):
        return valor
    return ''


# converte para int sem estourar erro 500 quando o valor vem vazio, texto ou fora da faixa
def _inteiro(valor, padrao=0, minimo=None, maximo=None):
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return padrao
    if (minimo is not None and numero < minimo) or (maximo is not None and numero > maximo):
        return padrao
    return numero


# ==========================================================================
# AUTENTICAÇÃO - cadastro, login e logout
# ==========================================================================

def tela_cadastro(request):
    # verifica se o navegador está enviando dados através de um formulário (POST)
    if request.method == "POST":
        nome = request.POST.get('nome')
        email = request.POST.get('email')
        senha = request.POST.get('senha')

        # cria e salva um novo registro na tabela usuario, já com a senha criptografada
        novo_usuario = Usuario.objects.create_user(
            email=email,
            nome_usuario=nome,
            password=senha,
        )

        # faz o login automático e manda o usuário para a tela do questionário
        auth_login(request, novo_usuario)
        return redirect('questionario')

    # se a requisição NÃO for POST, exibe a tela com o formulário de cadastro limpo.
    return render(request, 'core/cadastro.html')


def tela_login(request):
    if request.method == "POST":
        email = request.POST.get('email')
        senha = request.POST.get('senha')

        # login do administrador: mesma tela/form, mas com email e senha fixos
        # (definidos em ADMIN_LOGIN_EMAIL / ADMIN_LOGIN_SENHA) em vez de um
        # Usuario cadastrado no banco
        if email == settings.ADMIN_LOGIN_EMAIL and senha == settings.ADMIN_LOGIN_SENHA:
            request.session['admin_logado'] = True
            return redirect('administradores')

        usuario = authenticate(request, username=email, password=senha)

        if usuario is not None:
            auth_login(request, usuario)
            return redirect('dashboard')
        else:
            return render(request, 'core/login.html', {'erro': 'Usuário ou senha incorretos'})

    return render(request, 'core/login.html')


def tela_logout(request):
    # so desloga por POST (evita logout por link)
    if request.method == "POST":
        auth_logout(request)
    return redirect('login')


# ==========================================================================
# QUESTIONÁRIO E DASHBOARD - coleta das respostas, calculo do score e tela inicial
# ==========================================================================

def tela_questionario(request):
    if request.method == "POST":
        # pega as respostas do questionario
        idade = _inteiro(request.POST.get('idade'), 0, 0, 120)
        tipo_pele = request.POST.get('tipo_pele')
        alergias = request.POST.get('alergias')
        descricao_alergia = request.POST.get('descricaoalergia', '').strip()
        maquiagem = request.POST.get('maquiagem')
        pontos_sol = _inteiro(request.POST.get('reacao_sol'), 0, 0, 5)
        base_produto = request.POST.get('base_produto')
        objective = request.POST.get('objetivo')
        sono_horas = request.POST.get('sono_horas')
        exercicio_frequencia = request.POST.get('exercicio_frequencia')
        protetor_solar = _inteiro(request.POST.get('protetor_solar'), None, 0, 2)  # None = nao respondeu
        sensibilidade = _sim_nao(request.POST.get('sensibilidade'))
        gestante = _sim_nao(request.POST.get('gestante'))
        tipo_acne = request.POST.get('tipo_acne')

        # "Outro": guarda o texto que a pessoa digitou no lugar da palavra "outro"
        if objective == 'outro':
            objective = request.POST.get('objetivo_outro', '').strip()[:100] or 'outro'  # ajuste o limite ao max_length do campo

        # "Gel-creme" nao existe no modelo (so gel/creme): continua tratado como gel, igual ao formulario antigo
        if base_produto == 'gel-creme':
            base_produto = 'gel'

        # se a pessoa descreveu a alergia, guarda a descricao (o score continua usando so o sim/nao em `alergias`)
        alergias_salvas = descricao_alergia[:255] if alergias == '1' and descricao_alergia else alergias

        quer_escanear = request.POST.get('quer_escanear') == 'sim'
        consentimento_foto = request.POST.get('consentimento_foto') == '1'
        usa_maquiagem = 'sim' if maquiagem == '1' else 'nao'
        dados_ia_json = None
        foto_salva = None

        # calcula o score so com as respostas do questionario. esse e o valor padrao, usado sempre que a pessoa nao escaneia ou a API falha
        porcentagem_regras = calcular_porcentagem_saude(
            tipo_pele=tipo_pele,
            fototipo=pontos_sol + 1,
            usa_maquiagem=usa_maquiagem,
            tem_alergia=alergias,
        )
        porcentagem_calculada = porcentagem_regras

        # so envia a foto para a IA com consentimento explicito (LGPD) e se o arquivo for uma imagem valida
        if quer_escanear and consentimento_foto:
            foto_usuario = request.FILES.get('foto_rosto')

            if foto_usuario and _foto_valida(foto_usuario):
                resultados_ia = comunicar_youcam_api(foto_usuario)

                if resultados_ia:
                    resultados_ia = _completar_resultados(resultados_ia)
                    dados_ia_json = json.dumps(resultados_ia)
                    foto_salva = _url_https(_extract_url(resultados_ia))

                    # a ia entra com 50% do score; indicador que a youcam nao mediu e ignorado
                    porcentagem_ia = _media_ou_none(
                        _extract_score(resultados_ia, 'acne'),
                        _extract_score(resultados_ia, 'pore'),
                        _extract_score(resultados_ia, 'oiliness'),
                    )
                    if porcentagem_ia is not None:
                        porcentagem_calculada = round((porcentagem_ia + porcentagem_regras) / 2)

        # salva ou atualiza o perfil no banco
        if request.user.is_authenticated:
            perfil, created = PerfilDermatologico.objects.get_or_create(
                usuario=request.user,
                defaults={
                    'idade': idade,
                    'tipo_pele': tipo_pele or 'normal',
                    'alergias': alergias_salvas or '',
                    'objetivo': objective or 'Melhorar a pele',
                    'preferencia_produto': base_produto if base_produto in ('creme', 'gel') else 'gel',
                    'usa_maquiagem_diariamente': usa_maquiagem,
                    'porcentagem_saude': porcentagem_calculada,
                    'fototipo': pontos_sol + 1,
                    'dados_ia': dados_ia_json,
                    'foto_rosto': foto_salva,
                }
            )

            perfil.idade = idade or perfil.idade
            perfil.tipo_pele = tipo_pele or perfil.tipo_pele
            perfil.alergias = alergias_salvas or perfil.alergias
            perfil.usa_maquiagem_diariamente = usa_maquiagem
            perfil.fototipo = pontos_sol + 1
            perfil.objetivo = objective or perfil.objetivo
            perfil.preferencia_produto = base_produto if base_produto in ('creme', 'gel') else perfil.preferencia_produto
            # resposta fora das opcoes do model e ignorada (fica o valor que ja estava / o padrao)
            if sono_horas in dict(PerfilDermatologico.SONO_CHOICES):
                perfil.sono_horas = sono_horas
            if exercicio_frequencia in dict(PerfilDermatologico.EXERCICIO_CHOICES):
                perfil.exercicio_frequencia = exercicio_frequencia
            if protetor_solar is not None:
                perfil.protetor_solar = protetor_solar
            if sensibilidade is not None:
                perfil.sensibilidade = sensibilidade
            if gestante is not None:
                perfil.gestante = gestante
            perfil.tipo_acne = _tipo_acne_valido(tipo_acne, perfil.tipo_acne, perfil.objetivo)
            perfil.porcentagem_saude = porcentagem_calculada
            # so troca a analise se houve scan novo
            if dados_ia_json:
                perfil.dados_ia = dados_ia_json
                perfil.foto_rosto = foto_salva

            perfil.save()

        return redirect('dashboard')

    return render(request, 'core/questionario.html')


# Escala de Fitzpatrick do card "Fototipo" do dashboard: numero (1 a 6, o mesmo que
# fica em perfil.fototipo) -> (algarismo romano, cor da pilula, texto sobre o sol).
# Na ordem da escala, do tom mais claro ao mais escuro.
FOTOTIPOS = {
    1: ('I', '#f3d9c8', 'Sensibilidade muito alta ao sol. Use protetor solar todos os dias e evite o sol forte.'),
    2: ('II', '#e7bb9a', 'Sensibilidade alta ao sol. Use protetor solar todos os dias, mesmo em dias nublados.'),
    3: ('III', '#c98e63', 'Sensibilidade moderada ao sol. Use protetor solar todos os dias, mesmo em dias nublados.'),
    4: ('IV', '#a86c44', 'Sensibilidade moderada a baixa ao sol. Use protetor solar todos os dias.'),
    5: ('V', '#7a4b2e', 'Sensibilidade baixa ao sol, mas a proteção solar continua importante.'),
    6: ('VI', '#4a2d1b', 'Sensibilidade baixa ao sol, mas a proteção solar continua importante.'),
}


# dados do card "Fototipo". Sem fototipo valido (campo pode estar vazio) devolve None
def _fototipo_do_perfil(perfil):
    if not perfil or perfil.fototipo not in FOTOTIPOS:
        return None
    romano, _, texto = FOTOTIPOS[perfil.fototipo]
    return {
        'romano': romano,
        'texto': texto,
        'tons': [
            {'romano': r, 'cor': cor, 'atual': numero == perfil.fototipo}
            for numero, (r, cor, _) in FOTOTIPOS.items()
        ],
    }


# o JS do dashboard redesenha a lista ao trocar de aba e nao tem {% url %}:
# por isso o link da tela de detalhe do produto ja vai pronto em cada passo
def _com_link(passos):
    for passo in passos:
        passo['url'] = reverse('produto_detalhe', args=[passo['produto_id']]) if passo['produto_id'] else None
    return passos


def dashboard_view(request):
    # sem login nao mostra perfil (antes pegava o primeiro do banco, que e de outra pessoa)
    if request.user.is_authenticated:
        perfil = PerfilDermatologico.objects.filter(usuario=request.user).first()
    else:
        perfil = None

    # rotina real (produtos do catalogo) montada em recomendacao.py; sem perfil vem vazia
    rotina = montar_rotina(perfil)

    context = {
        'perfil': perfil,
        'rotina_manha': _com_link(rotina['manha']),
        'rotina_noite': _com_link(rotina['noite']),
        'avisos': rotina['avisos'],
        'fototipo': _fototipo_do_perfil(perfil),
        'objetivo_rotulo': rotulo_objetivo(perfil.objetivo) if perfil else '',
    }
    return render(request, 'core/dashboard.html', context)


# ==========================================================================
# SCANNER DE PELE POR IA - endpoints usados pelo JS da core/scanner.html
# ==========================================================================

DESCRICOES_INDICADOR = {
    'oleosidade': {
        'Ótimo': 'Oleosidade sob controle.',
        'Bom': 'Nível de oleosidade equilibrado.',
        'Regular': 'Zona T pode precisar de atenção.',
        'Atenção': 'Oleosidade elevada, considere um produto de controle.',
    },
    'hidratacao': {
        'Ótimo': 'Hidratação excelente.',
        'Bom': 'Nível de hidratação adequado.',
        'Regular': 'Pele levemente desidratada.',
        'Atenção': 'Baixa hidratação, reforce o uso de hidratante.',
    },
    'acne_poros': {
        'Ótimo': 'Poucos sinais de acne ou poros dilatados.',
        'Bom': 'Poros e acne sob controle.',
        'Regular': 'Alguns poros dilatados e pequenas inflamações.',
        'Atenção': 'Sinais de acne e poros dilatados precisam de cuidado.',
    },
    'textura': {
        'Ótimo': 'Textura uniforme e lisa.',
        'Bom': 'Boa textura geral da pele.',
        'Regular': 'Leve irregularidade na textura.',
        'Atenção': 'Textura irregular, considere uma esfoliação leve.',
    },
    'manchas': {
        'Ótimo': 'Tonalidade bem uniforme.',
        'Bom': 'Poucas manchas visíveis.',
        'Regular': 'Algumas manchas de sol ou idade.',
        'Atenção': 'Manchas visíveis, reforce o uso de protetor solar.',
    },
}


def montar_indicador(chave, nota):
    # sem nota = a youcam nao mediu esse indicador nessa foto
    if nota is None:
        return {
            'chave': chave,
            'nota': None,
            'classificacao': None,
            'descricao': 'Não foi possível medir este indicador nesta foto.',
        }

    nota = max(0, min(100, round(nota)))
    classificacao = classificar_indicador(nota)
    descricao = DESCRICOES_INDICADOR[chave][classificacao]
    return {'chave': chave, 'nota': nota, 'classificacao': classificacao, 'descricao': descricao}


def _media_ou_none(*notas):
    # media das notas que existem (None se nao houver nenhuma)
    validas = [nota for nota in notas if nota is not None]
    return sum(validas) / len(validas) if validas else None


def _url_https(valor):
    # so aceita https (o link vai pro src da imagem)
    return valor if isinstance(valor, str) and valor.startswith('https://') else None


def _passou_limite_scanner(usuario):
    # cada analise gasta credito da youcam, entao limita a 3 por usuario por hora (usa o cache)
    chave = f'scanner_analises_{usuario.pk}'
    cache.add(chave, 0, 3600)
    try:
        usadas = cache.incr(chave)
    except ValueError:
        cache.set(chave, 1, 3600)
        usadas = 1
    return usadas <= 3


def scanner_analisar(request):
    # recebe a foto do scanner e devolve a analise em json
    if request.method != 'POST':
        return JsonResponse({'erro': 'Método não permitido.'}, status=405)

    # sem @login_required porque o js espera json, nao um redirect
    if not request.user.is_authenticated:
        return JsonResponse({'erro': 'Faça login para usar o scanner.'}, status=401)

    # foto do rosto e dado sensivel: so com consentimento
    if request.POST.get('consentimento') != '1':
        return JsonResponse({'erro': 'É preciso aceitar o consentimento para analisar a foto.'}, status=400)

    foto = request.FILES.get('foto')
    if not foto:
        return JsonResponse({'erro': 'Nenhuma imagem enviada.'}, status=400)

    if not _foto_valida(foto):
        return JsonResponse({'erro': 'Envie uma imagem JPG, PNG ou WebP de até 5 MB.'}, status=400)

    if not _passou_limite_scanner(request.user):
        return JsonResponse({'erro': 'Você atingiu o limite de 3 análises por hora. Tente novamente mais tarde.'}, status=429)

    resultados_ia = comunicar_youcam_api(foto)
    if not resultados_ia:
        return JsonResponse({'erro': 'Não foi possível analisar a imagem agora. Tente novamente em instantes.'}, status=502)
    resultados_ia = _completar_resultados(resultados_ia)

    # sem nota fica None (nota 0 e valida)
    nota_oleosidade = _extract_score(resultados_ia, 'oiliness')
    nota_hidratacao = _extract_score(resultados_ia, 'moisture')
    nota_acne = _extract_score(resultados_ia, 'acne')
    nota_poros = _extract_score(resultados_ia, 'pore')
    nota_textura = _extract_score(resultados_ia, 'texture')
    nota_manchas = _extract_score(resultados_ia, 'age_spot')

    indicadores = [
        montar_indicador('oleosidade', nota_oleosidade),
        montar_indicador('hidratacao', nota_hidratacao),
        montar_indicador('acne_poros', _media_ou_none(nota_acne, nota_poros)),
        montar_indicador('textura', nota_textura),
        montar_indicador('manchas', nota_manchas),
    ]

    notas_disponiveis = [indicador['nota'] for indicador in indicadores if indicador['nota'] is not None]
    if not notas_disponiveis:
        _guardar_resposta_para_diagnostico(resultados_ia)
        return JsonResponse({'erro': 'A análise não retornou nenhum indicador para esta foto. Tente outra foto.'}, status=502)

    score_geral = round(sum(notas_disponiveis) / len(notas_disponiveis))
    mapeamento_facial = _url_https(_extract_url(resultados_ia))

    # guarda o resultado na sessao ate o usuario confirmar o salvamento
    request.session['ultimo_scan'] = {
        'dados_ia': json.dumps(resultados_ia),
        'foto_rosto': mapeamento_facial,
        'porcentagem_saude': score_geral,
        'consentimento_em': time.time(),
    }

    return JsonResponse({
        'score_geral': score_geral,
        'indicadores': indicadores,
        'mapeamento_facial': mapeamento_facial,
    })


def scanner_salvar(request):
    # salva o ultimo resultado do scanner no perfil do usuario
    if request.method != 'POST':
        return JsonResponse({'erro': 'Método não permitido.'}, status=405)

    if not request.user.is_authenticated:
        return JsonResponse({'erro': 'Você precisa estar logado para salvar o check-in.'}, status=401)

    ultimo_scan = request.session.get('ultimo_scan')
    if not ultimo_scan:
        return JsonResponse({'erro': 'Faça um scan antes de salvar o check-in.'}, status=400)

    # sem perfil nao salva: precisa responder o questionario antes
    perfil = PerfilDermatologico.objects.filter(usuario=request.user).first()
    if not perfil:
        return JsonResponse({
            'erro': 'Responda o questionário antes de salvar um check-in.',
            'ir_para': reverse('questionario'),
        }, status=400)

    perfil.dados_ia = ultimo_scan['dados_ia']
    perfil.foto_rosto = ultimo_scan['foto_rosto']
    perfil.porcentagem_saude = ultimo_scan['porcentagem_saude']
    perfil.save()

    del request.session['ultimo_scan']
    return JsonResponse({'ok': True})


def tela_scanner(request):
    return render(request, 'core/scanner.html')


# ==========================================================================
# PERFIL DO USUÁRIO
# ==========================================================================

# abas da tela de perfil, na ordem em que aparecem no menu. a aba aberta vem
# na URL (?aba=pele) e cada aba tem o seu proprio formulario/POST
ABAS_PERFIL = ('resumo', 'dados', 'pele', 'seguranca', 'privacidade')

# ordem e titulo dos indicadores no "Resumo da pele" (iguais aos do scanner)
INDICADORES_RESUMO = [
    ('oleosidade', 'Oleosidade'),
    ('hidratacao', 'Hidratação'),
    ('acne_poros', 'Acne/Poros'),
    ('textura', 'Textura'),
    ('manchas', 'Manchas'),
]

# classe do selo de cada classificacao (Regular usa o visual padrao, sem classe extra)
CLASSE_SELO_INDICADOR = {
    'Ótimo': 'badge-otimo',
    'Bom': 'badge-bom',
    'Atenção': 'badge-atencao',
}


def _perfil_do_usuario(usuario):
    return PerfilDermatologico.objects.filter(usuario=usuario).first()


def _indicadores_do_perfil(perfil):
    # refaz os indicadores do ultimo scan a partir do JSON da YouCam salvo em
    # perfil.dados_ia (mesmas funcoes que o scanner usa). sem scan salvo = lista vazia
    if not perfil or not perfil.dados_ia:
        return []
    try:
        resultados_ia = json.loads(perfil.dados_ia)
    except (json.JSONDecodeError, TypeError):
        return []

    notas = {
        'oleosidade': _extract_score(resultados_ia, 'oiliness'),
        'hidratacao': _extract_score(resultados_ia, 'moisture'),
        'acne_poros': _media_ou_none(
            _extract_score(resultados_ia, 'acne'),
            _extract_score(resultados_ia, 'pore'),
        ),
        'textura': _extract_score(resultados_ia, 'texture'),
        'manchas': _extract_score(resultados_ia, 'age_spot'),
    }

    indicadores = []
    for chave, titulo in INDICADORES_RESUMO:
        indicador = montar_indicador(chave, notas[chave])
        indicador['titulo'] = titulo
        indicador['classe_selo'] = CLASSE_SELO_INDICADOR.get(indicador['classificacao'], '')
        indicadores.append(indicador)

    # se nenhum indicador foi medido, e como se nao houvesse scan
    if all(indicador['nota'] is None for indicador in indicadores):
        return []
    return indicadores


def _iniciais_do_nome(nome):
    # "Ana Souza" -> "AS" (avatar sem foto)
    partes = (nome or '').split()
    if not partes:
        return 'U'
    if len(partes) == 1:
        return partes[0][:2].upper()
    return (partes[0][0] + partes[-1][0]).upper()


def _alergias_do_perfil(perfil):
    # o questionario guarda '0'/'1' (nao/sim) ou o texto digitado; so o texto vira etiqueta
    texto = (perfil.alergias or '').strip()
    if texto in ('', '0', '1'):
        return []
    return [item.strip() for item in texto.replace(';', ',').split(',') if item.strip()]


def _contexto_perfil(request, perfil, aba, form_senha=None):
    # tudo que o core/perfil.html precisa, incluindo o formulario de senha
    # (volta preenchido, com os erros, quando a troca de senha falha)
    # o select usa os mesmos objetivos do questionario (recomendacao.OBJETIVOS), como
    # pares (valor, texto). se o objetivo salvo for texto livre (opcao "Outro"), entra na lista tambem
    objetivos = list(OBJETIVOS.items())
    objetivo_atual = normalizar_objetivo(perfil.objetivo) if perfil else ''
    if objetivo_atual and objetivo_atual not in OBJETIVOS:
        objetivos.append((objetivo_atual, objetivo_atual))

    return {
        'perfil': perfil,
        'aba_ativa': aba if aba in ABAS_PERFIL else 'resumo',
        'iniciais': _iniciais_do_nome(request.user.nome_usuario),
        'indicadores': _indicadores_do_perfil(perfil),
        'alergias_lista': _alergias_do_perfil(perfil) if perfil else [],
        'objetivos': objetivos,
        'objetivo_atual': objetivo_atual,
        'objetivo_rotulo': rotulo_objetivo(perfil.objetivo) if perfil else '',
        'form_senha': form_senha or PasswordChangeForm(request.user),
    }


def _voltar_ao_perfil(aba):
    # depois de cada POST volta pra mesma aba (e as mensagens aparecem la)
    return redirect(f"{reverse('perfil')}?aba={aba}")


@login_required
def tela_perfil(request):
    perfil = _perfil_do_usuario(request.user)
    aba = request.GET.get('aba', 'resumo')
    return render(request, 'core/perfil.html', _contexto_perfil(request, perfil, aba))


@login_required
def editar_dados_pessoais(request):
    # aba "Dados pessoais": nome e idade (o e-mail e o login e nao muda por aqui)
    if request.method != 'POST':
        return _voltar_ao_perfil('dados')

    nome_usuario = request.POST.get('nome_usuario', '').strip()
    if not nome_usuario:
        messages.error(request, 'Informe o seu nome.')
        return _voltar_ao_perfil('dados')

    request.user.nome_usuario = nome_usuario[:100]
    request.user.save(update_fields=['nome_usuario'])

    # a idade fica no perfil, que so existe depois do questionario
    perfil = _perfil_do_usuario(request.user)
    if perfil and request.POST.get('idade'):
        # valor invalido (texto ou fora de 0-120) mantem a idade que ja estava
        perfil.idade = _inteiro(request.POST.get('idade'), perfil.idade, 0, 120)
        perfil.save(update_fields=['idade'])

    messages.success(request, 'Dados pessoais atualizados.')
    return _voltar_ao_perfil('dados')


@login_required
def editar_perfil(request):
    # aba "Perfil de pele": tipo de pele, objetivo, alergias e preferencias.
    # o formulario fica embutido em core/perfil.html, entao essa view so
    # processa o POST e volta pra la
    perfil = _perfil_do_usuario(request.user)
    if not perfil:
        return redirect('questionario')

    if request.method != 'POST':
        return _voltar_ao_perfil('pele')

    tipo_pele = request.POST.get('tipo_pele')
    alergias = request.POST.get('alergias', '').strip()
    objetivo = normalizar_objetivo(request.POST.get('objetivo'))  # grava o mesmo codigo do questionario
    preferencia_produto = request.POST.get('preferencia_produto')
    usa_maquiagem_diariamente = request.POST.get('usa_maquiagem_diariamente')
    sensibilidade = _sim_nao(request.POST.get('sensibilidade'))
    gestante = _sim_nao(request.POST.get('gestante'))
    tipo_acne = request.POST.get('tipo_acne')

    if tipo_pele in dict(PerfilDermatologico.TIPO_PELE_CHOICES):
        perfil.tipo_pele = tipo_pele
    perfil.alergias = alergias[:255]
    if objetivo:
        perfil.objetivo = objetivo[:100]
    if preferencia_produto in ('creme', 'gel'):
        perfil.preferencia_produto = preferencia_produto
    if usa_maquiagem_diariamente in ('sim', 'nao'):
        perfil.usa_maquiagem_diariamente = usa_maquiagem_diariamente
    if sensibilidade is not None:
        perfil.sensibilidade = sensibilidade
    if gestante is not None:
        perfil.gestante = gestante
    perfil.tipo_acne = _tipo_acne_valido(tipo_acne, perfil.tipo_acne, perfil.objetivo)

    perfil.save()
    messages.success(request, 'Perfil de pele atualizado. As recomendações já usam as novas respostas.')
    return _voltar_ao_perfil('pele')


@login_required
def alterar_senha(request):
    # aba "Seguranca": troca de senha com o formulario pronto do Django
    # (pede a senha atual e aplica os AUTH_PASSWORD_VALIDATORS do settings)
    if request.method != 'POST':
        return _voltar_ao_perfil('seguranca')

    form = PasswordChangeForm(request.user, request.POST)
    if form.is_valid():
        usuario = form.save()
        # sem isso a troca de senha derruba a sessao de quem acabou de trocar
        update_session_auth_hash(request, usuario)
        messages.success(request, 'Senha alterada com sucesso.')
        return _voltar_ao_perfil('seguranca')

    # senha invalida: mostra a mesma tela com os erros embaixo dos campos
    perfil = _perfil_do_usuario(request.user)
    return render(request, 'core/perfil.html', _contexto_perfil(request, perfil, 'seguranca', form))


@login_required
def apagar_foto_perfil(request):
    # aba "Privacidade e dados": apaga a imagem do scan guardada no perfil
    if request.method != 'POST':
        return _voltar_ao_perfil('privacidade')

    perfil = _perfil_do_usuario(request.user)
    if perfil and perfil.foto_rosto:
        perfil.foto_rosto = None
        perfil.save(update_fields=['foto_rosto'])
        messages.success(request, 'A imagem do scan foi apagada da sua conta.')
    else:
        messages.info(request, 'Não há nenhuma imagem de scan guardada na sua conta.')
    return _voltar_ao_perfil('privacidade')


@login_required
def exportar_dados(request):
    # aba "Privacidade e dados": baixa uma copia dos dados da conta em JSON
    perfil = _perfil_do_usuario(request.user)

    dados = {
        'exportado_em': timezone.now().isoformat(),
        'conta': {
            'nome': request.user.nome_usuario,
            'email': request.user.email,
            'cadastrada_em': request.user.data_cadastro.isoformat(),
        },
        'perfil_dermatologico': None,
    }

    if perfil:
        try:
            analise_ia = json.loads(perfil.dados_ia) if perfil.dados_ia else None
        except (json.JSONDecodeError, TypeError):
            analise_ia = None

        dados['perfil_dermatologico'] = {
            'idade': perfil.idade,
            'tipo_pele': perfil.tipo_pele,
            'alergias': perfil.alergias,
            'objetivo': perfil.objetivo,
            'preferencia_produto': perfil.preferencia_produto,
            'usa_maquiagem_diariamente': perfil.usa_maquiagem_diariamente,
            'sono_horas': perfil.sono_horas,
            'protetor_solar': perfil.protetor_solar,
            'exercicio_frequencia': perfil.exercicio_frequencia,
            'fototipo': perfil.fototipo,
            'porcentagem_saude': perfil.porcentagem_saude,
            'imagem_do_scan': perfil.foto_rosto,
            'analise_ia': analise_ia,
        }

    resposta = JsonResponse(dados, json_dumps_params={'ensure_ascii': False, 'indent': 2})
    resposta['Content-Disposition'] = f'attachment; filename="lume-skin-meus-dados-{timezone.localdate()}.json"'
    return resposta


@login_required
def excluir_conta(request):
    # aba "Privacidade e dados": apaga a conta e o perfil (o perfil tem
    # on_delete=CASCADE no Usuario). pede a senha e a palavra EXCLUIR de
    # confirmacao porque nao tem como desfazer
    if request.method != 'POST':
        return _voltar_ao_perfil('privacidade')

    if request.POST.get('confirmacao', '').strip().upper() != 'EXCLUIR':
        messages.error(request, 'Digite EXCLUIR para confirmar a exclusão da conta.')
        return _voltar_ao_perfil('privacidade')

    if not request.user.check_password(request.POST.get('senha', '')):
        messages.error(request, 'Senha incorreta. A conta não foi excluída.')
        return _voltar_ao_perfil('privacidade')

    usuario = request.user
    auth_logout(request)
    usuario.delete()
    return redirect('login')


# ==========================================================================
# CATÁLOGO - produtos, artigos e especialistas (telas de consulta)
# ==========================================================================

CATEGORIA_BUCKET = {
    'limpeza': {'Limpeza', 'Água Micelar', 'Tônico', 'Esfoliante'},
    'tratamento': {'Sérum', 'Hidratante', 'Máscara Facial', 'Óleo Facial', 'Contorno de Olhos'},
    'protecao': {'Protetor Solar'},
}


def tela_produtos(request):
    # sem login nao existe perfil (filtrar por AnonymousUser estoura erro)
    if request.user.is_authenticated:
        perfil = PerfilDermatologico.objects.filter(usuario=request.user).first()
    else:
        perfil = None

    produtos = recomendar_produtos(perfil)

    filtro = request.GET.get("categoria")
    if filtro:
        categorias_do_filtro = CATEGORIA_BUCKET.get(filtro, set())
        produtos = [p for p in produtos if p.categoria in categorias_do_filtro]

    context = {
        "perfil": perfil,
        "objetivo_usuario": rotulo_objetivo(perfil.objetivo) if perfil else "não definido",
        "produtos": produtos,
        "filtro_ativo": filtro,
    }
    return render(request, "core/produtos.html", context)


def produto_detalhe(request, produto_id):
    produto = get_object_or_404(Produto, id=produto_id)
    context = {
        'produto': produto,
    }
    return render(request, 'core/produto_detalhe.html', context)


def tela_artigos(request):
    artigos = Artigo.objects.all().order_by('-ano')
    context = {
        "artigos": artigos,
    }
    return render(request, "core/artigos.html", context)


def tela_especialistas(request):
    especialistas = Especialista.objects.all().order_by('nome')

    filtro = request.GET.get("especialidade")

    if filtro:
        especialistas = especialistas.filter(especialidade=filtro)

    context = {
        "especialistas": especialistas,
        "filtro_ativo": filtro,
    }
    return render(request, "core/especialistas.html", context)


# ==========================================================================
# ÁREA ADMINISTRATIVA - CRUD de artigos e especialistas
# ==========================================================================

@admin_required
def tela_administradores(request):
    context = {
        "artigos": Artigo.objects.all().order_by('-ano'),
        "especialistas": Especialista.objects.all().order_by('nome'),
    }
    return render(request, "core/administradores.html", context)


@admin_required
def artigo_form(request, artigo_id=None):
    # os formularios de criar/editar artigo ficam embutidos direto em
    # core/administradores.html, entao essa view so processa o POST
    if request.method != 'POST':
        return redirect('administradores')

    artigo = get_object_or_404(Artigo, id=artigo_id) if artigo_id else Artigo()

    artigo.titulo = request.POST.get('titulo', '').strip()
    artigo.autor = request.POST.get('autor', '').strip()
    artigo.ano = request.POST.get('ano') or artigo.ano
    artigo.resumo = request.POST.get('resumo', '').strip()
    artigo.url_capa = request.POST.get('url_capa', '').strip()
    artigo.url_leitura = request.POST.get('url_leitura', '').strip()
    artigo.save()
    return redirect('administradores')


@admin_required
def artigo_excluir(request, artigo_id):
    artigo = get_object_or_404(Artigo, id=artigo_id)
    if request.method == 'POST':
        artigo.delete()
    return redirect('administradores')


@admin_required
def especialista_form(request, especialista_id=None):
    # os formularios de criar/editar especialista ficam embutidos direto em
    # core/administradores.html, entao essa view so processa o POST
    if request.method != 'POST':
        return redirect('administradores')

    especialista = get_object_or_404(Especialista, id=especialista_id) if especialista_id else Especialista()

    especialista.nome = request.POST.get('nome', '').strip()
    especialista.especialidade = request.POST.get('especialidade', '').strip()
    especialista.crm = request.POST.get('crm', '').strip()
    especialista.telefone_whatsapp = request.POST.get('telefone_whatsapp', '').strip()
    especialista.url_foto = request.POST.get('url_foto', '').strip()
    especialista.save()
    return redirect('administradores')


@admin_required
def especialista_excluir(request, especialista_id):
    especialista = get_object_or_404(Especialista, id=especialista_id)
    if request.method == 'POST':
        especialista.delete()
    return redirect('administradores')
