import json

from .models import PerfilDermatologico, Produto

MAPA_TIPO_PELE = {
    'oleosa': 'Oleosa',
    'seca': 'Seca',
    'mista': 'Mista',
    'normal': 'Normal',
}

# Objetivos oferecidos nos DOIS formularios (questionario.html e aba "Perfil de pele").
# A chave e o valor salvo no banco; o texto e o que aparece na tela.
# Mexeu aqui? Ajuste tambem os <option> do questionario.html e o MAPA abaixo.
OBJETIVOS = {
    'hidratar': 'Hidratar',
    'controlar oleosidade': 'Controlar oleosidade',
    'reduzir manchas': 'Reduzir manchas',
    'anti-idade': 'Anti-idade',
    'reduzir acne': 'Reduzir acne',
    'melhorar textura': 'Melhorar textura',
}

# Textos que a aba "Perfil de pele" gravava antes de unificar os valores. Quem ja
# editou o perfil tem um deles no banco, entao convertemos na leitura (sem mexer nos dados).
OBJETIVOS_ANTIGOS = {
    'hidratar a pele': 'hidratar',
    'prevenir sinais de idade': 'anti-idade',
}

# Bate com as chaves de OBJETIVOS
MAPA_OBJETIVO_PREOCUPACAO = {
    'hidratar': {'Hidratação'},
    'controlar oleosidade': {'Oleosidade'},
    'reduzir manchas': {'Manchas'},
    'anti-idade': {'Rugas e Linhas Finas', 'Fotoenvelhecimento'},
    'reduzir acne': {'Acne'},
    'melhorar textura': {'Textura Irregular'},
    # 'outro' não tem preocupação mapeada de propósito: o formulário salva só
    # a string "outro" e não guarda o texto livre do campo objetivo_outro.
}

CATEGORIA_POR_PREFERENCIA = {
    'creme': {'Hidratante', 'Contorno de Olhos', 'Máscara Facial'},
    'gel': {'Limpeza', 'Sérum', 'Tônico', 'Água Micelar', 'Esfoliante'},
}


def normalizar_objetivo(objetivo):
    """Devolve o codigo do objetivo ('reduzir acne'). Texto livre ("Outro") passa sem mudar."""
    texto = (objetivo or '').strip()
    codigo = OBJETIVOS_ANTIGOS.get(texto.lower(), texto.lower())
    return codigo if codigo in OBJETIVOS else texto


def rotulo_objetivo(objetivo):
    """Texto bonito para mostrar na tela ('reduzir acne' -> 'Reduzir acne')."""
    texto = normalizar_objetivo(objetivo)
    return OBJETIVOS.get(texto, texto)


def classificar_indicador(nota):
    if nota >= 85:
        return 'Ótimo'
    if nota >= 70:
        return 'Bom'
    if nota >= 50:
        return 'Regular'
    return 'Atenção'


def extrair_sinais_faciais(dados_ia_raw):
    """Lê o JSON da YouCam salvo em PerfilDermatologico.dados_ia."""
    if not dados_ia_raw:
        return {}
    try:
        dados = json.loads(dados_ia_raw)
    except (json.JSONDecodeError, TypeError):
        return {}

    sinais = {}
    campos = {
        'oleosidade_alta': dados.get('oiliness', {}).get('score'),
        'acne_presente': dados.get('acne', {}).get('score'),
        'manchas_presentes': dados.get('age_spot', {}).get('score'),
        'textura_irregular': dados.get('texture', {}).get('score'),
        'baixa_hidratacao': dados.get('moisture', {}).get('score'),
    }
    for sinal, nota in campos.items():
        if nota is not None and classificar_indicador(nota) in ('Regular', 'Atenção'):
            sinais[sinal] = True

    return sinais


def tipos_pele_compativeis(perfil, sinais_faciais):
    tipos = {'Todos os tipos'}
    tipo = MAPA_TIPO_PELE.get(perfil.tipo_pele)
    if tipo:
        tipos.add(tipo)
    if perfil.idade and perfil.idade >= 40:
        tipos.add('Madura')
    return tipos


def preocupacoes_do_perfil(perfil, sinais_faciais):
    preocupacoes = set(MAPA_OBJETIVO_PREOCUPACAO.get(normalizar_objetivo(perfil.objetivo), set()))

    if sinais_faciais.get('oleosidade_alta'):
        preocupacoes.add('Oleosidade')
    if sinais_faciais.get('acne_presente'):
        preocupacoes.add('Acne')
    if sinais_faciais.get('manchas_presentes'):
        preocupacoes.add('Manchas')
    if sinais_faciais.get('textura_irregular'):
        preocupacoes.add('Textura Irregular')
    if sinais_faciais.get('baixa_hidratacao'):
        preocupacoes.add('Hidratação')

    return preocupacoes


def recomendar_produtos(perfil, limite=20):
    if perfil is None:
        return list(Produto.objects.all()[:limite])

    sinais_faciais = extrair_sinais_faciais(perfil.dados_ia)
    tipos_compat = tipos_pele_compativeis(perfil, sinais_faciais)
    preocupacoes = preocupacoes_do_perfil(perfil, sinais_faciais)
    categorias_preferidas = CATEGORIA_POR_PREFERENCIA.get(perfil.preferencia_produto, set())

    candidatos = [
        produto for produto in Produto.objects.all()
        if set(produto.tipos_pele()) & tipos_compat
    ]

    pontuados = []
    for produto in candidatos:
        pontos = 2 * len(preocupacoes & set(produto.preocupacoes()))
        if produto.categoria in categorias_preferidas:
            pontos += 1
        if produto.avaliacao is not None:
            pontos += float(produto.avaliacao) * 0.1
        pontuados.append((pontos, produto))

    pontuados.sort(key=lambda item: item[0], reverse=True)
    return [produto for _, produto in pontuados[:limite]]
