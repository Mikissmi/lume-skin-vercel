import json
import os
import tempfile
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from .models import Usuario, PerfilDermatologico, RegraTratamento
from .recomendacao import (
    MAPA_OBJETIVO_PREOCUPACAO, OBJETIVOS, normalizar_objetivo, preocupacoes_do_perfil, rotulo_objetivo,
)
from .views import _alergias_do_perfil, _indicadores_do_perfil, _iniciais_do_nome


# ==========================================================================
# PERFIL E CONFIGURAÇÕES - abas Resumo, Dados, Perfil de pele, Segurança e Privacidade
# ==========================================================================

SENHA = 'senha-segura-123'


def criar_usuario_com_perfil(email='ana@email.com', com_perfil=True):
    usuario = Usuario.objects.create_user(email=email, nome_usuario='Ana Souza', password=SENHA)
    perfil = None
    if com_perfil:
        perfil = PerfilDermatologico.objects.create(
            usuario=usuario,
            idade=27,
            tipo_pele='mista',
            alergias='fragrância, parabenos',
            objetivo='controlar oleosidade',
            preferencia_produto='gel',
            usa_maquiagem_diariamente='nao',
            porcentagem_saude=78,
            fototipo=3,
            foto_rosto='https://exemplo.com/scan.jpg',
            dados_ia=json.dumps({'oiliness': {'score': 90}, 'moisture': {'score': 60}}),
        )
    return usuario, perfil


class PerfilBaseTestCase(TestCase):
    def setUp(self):
        self.usuario, self.perfil = criar_usuario_com_perfil()
        self.client.login(username='ana@email.com', password=SENHA)


class FuncoesAuxiliaresTests(TestCase):
    def test_iniciais_do_nome(self):
        self.assertEqual(_iniciais_do_nome('Ana Souza'), 'AS')
        self.assertEqual(_iniciais_do_nome('Maria'), 'MA')
        self.assertEqual(_iniciais_do_nome(''), 'U')

    def test_alergias_ignora_respostas_sim_nao_do_questionario(self):
        _, perfil = criar_usuario_com_perfil()
        perfil.alergias = '1'
        self.assertEqual(_alergias_do_perfil(perfil), [])
        perfil.alergias = '0'
        self.assertEqual(_alergias_do_perfil(perfil), [])
        perfil.alergias = 'fragrância; parabenos, látex'
        self.assertEqual(_alergias_do_perfil(perfil), ['fragrância', 'parabenos', 'látex'])

    def test_indicadores_do_ultimo_scan(self):
        _, perfil = criar_usuario_com_perfil()
        indicadores = {i['chave']: i for i in _indicadores_do_perfil(perfil)}
        self.assertEqual(indicadores['oleosidade']['nota'], 90)
        self.assertEqual(indicadores['oleosidade']['classificacao'], 'Ótimo')
        self.assertEqual(indicadores['oleosidade']['classe_selo'], 'badge-otimo')
        self.assertEqual(indicadores['hidratacao']['classificacao'], 'Regular')
        self.assertIsNone(indicadores['textura']['nota'])

    def test_sem_scan_nao_ha_indicadores(self):
        _, perfil = criar_usuario_com_perfil()
        perfil.dados_ia = None
        self.assertEqual(_indicadores_do_perfil(perfil), [])
        perfil.dados_ia = 'isto nao e json'
        self.assertEqual(_indicadores_do_perfil(perfil), [])


class TelaPerfilTests(PerfilBaseTestCase):
    def test_exige_login(self):
        self.client.logout()
        resposta = self.client.get(reverse('perfil'))
        self.assertEqual(resposta.status_code, 302)
        self.assertIn(reverse('login'), resposta.url)

    def test_abre_na_aba_resumo_por_padrao(self):
        resposta = self.client.get(reverse('perfil'))
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['aba_ativa'], 'resumo')
        self.assertContains(resposta, 'Indicadores do último scan')
        self.assertContains(resposta, 'AS')  # iniciais do avatar

    def test_abre_a_aba_pedida_e_ignora_aba_invalida(self):
        for aba in ('dados', 'pele', 'seguranca', 'privacidade'):
            resposta = self.client.get(reverse('perfil'), {'aba': aba})
            self.assertEqual(resposta.context['aba_ativa'], aba)
        resposta = self.client.get(reverse('perfil'), {'aba': 'qualquer-coisa'})
        self.assertEqual(resposta.context['aba_ativa'], 'resumo')

    def test_usuario_sem_questionario_ainda_acessa_conta_e_seguranca(self):
        self.client.logout()
        criar_usuario_com_perfil('sem-perfil@email.com', com_perfil=False)
        self.client.login(username='sem-perfil@email.com', password=SENHA)
        for aba in ('resumo', 'dados', 'pele', 'seguranca', 'privacidade'):
            resposta = self.client.get(reverse('perfil'), {'aba': aba})
            self.assertEqual(resposta.status_code, 200)


class DadosPessoaisTests(PerfilBaseTestCase):
    def test_atualiza_nome_e_idade(self):
        resposta = self.client.post(reverse('editar_dados_pessoais'), {'nome_usuario': 'Ana Lima', 'idade': '30'})
        self.assertRedirects(resposta, f"{reverse('perfil')}?aba=dados")
        self.usuario.refresh_from_db()
        self.perfil.refresh_from_db()
        self.assertEqual(self.usuario.nome_usuario, 'Ana Lima')
        self.assertEqual(self.perfil.idade, 30)

    def test_nome_vazio_nao_salva(self):
        self.client.post(reverse('editar_dados_pessoais'), {'nome_usuario': '   ', 'idade': '30'})
        self.usuario.refresh_from_db()
        self.perfil.refresh_from_db()
        self.assertEqual(self.usuario.nome_usuario, 'Ana Souza')
        self.assertEqual(self.perfil.idade, 27)

    def test_idade_invalida_mantem_a_anterior(self):
        self.client.post(reverse('editar_dados_pessoais'), {'nome_usuario': 'Ana', 'idade': 'abc'})
        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.idade, 27)

    def test_email_nao_e_alterado(self):
        self.client.post(reverse('editar_dados_pessoais'), {'nome_usuario': 'Ana', 'email': 'outro@email.com'})
        self.usuario.refresh_from_db()
        self.assertEqual(self.usuario.email, 'ana@email.com')


class PerfilDePeleTests(PerfilBaseTestCase):
    def test_atualiza_preferencias(self):
        resposta = self.client.post(reverse('editar_perfil'), {
            'tipo_pele': 'seca',
            'objetivo': 'hidratar',
            'alergias': 'látex',
            'preferencia_produto': 'creme',
            'usa_maquiagem_diariamente': 'sim',
        })
        self.assertRedirects(resposta, f"{reverse('perfil')}?aba=pele")
        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.tipo_pele, 'seca')
        self.assertEqual(self.perfil.objetivo, 'hidratar')
        self.assertEqual(self.perfil.alergias, 'látex')
        self.assertEqual(self.perfil.preferencia_produto, 'creme')
        self.assertEqual(self.perfil.usa_maquiagem_diariamente, 'sim')

    def test_valores_invalidos_sao_ignorados(self):
        self.client.post(reverse('editar_perfil'), {
            'tipo_pele': 'inventada',
            'preferencia_produto': 'pomada',
            'usa_maquiagem_diariamente': 'talvez',
        })
        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.tipo_pele, 'mista')
        self.assertEqual(self.perfil.preferencia_produto, 'gel')
        self.assertEqual(self.perfil.usa_maquiagem_diariamente, 'nao')

    def test_sem_perfil_manda_para_o_questionario(self):
        self.client.logout()
        criar_usuario_com_perfil('novo@email.com', com_perfil=False)
        self.client.login(username='novo@email.com', password=SENHA)
        resposta = self.client.post(reverse('editar_perfil'), {'tipo_pele': 'seca'})
        self.assertRedirects(resposta, reverse('questionario'))

    def test_objetivo_editado_continua_gerando_preocupacoes(self):
        # bug da Fase 0: o texto da aba de perfil nao era entendido pelo mapa de recomendacao
        for codigo, preocupacoes in MAPA_OBJETIVO_PREOCUPACAO.items():
            self.client.post(reverse('editar_perfil'), {'objetivo': codigo})
            self.perfil.refresh_from_db()
            self.assertEqual(self.perfil.objetivo, codigo)
            self.assertEqual(preocupacoes_do_perfil(self.perfil, {}), preocupacoes)

    def test_texto_antigo_do_objetivo_e_convertido_ao_salvar(self):
        self.client.post(reverse('editar_perfil'), {'objetivo': 'Prevenir sinais de idade'})
        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.objetivo, 'anti-idade')

    def test_select_de_objetivo_mostra_o_objetivo_atual_selecionado(self):
        self.perfil.objetivo = 'reduzir acne'
        self.perfil.save()
        resposta = self.client.get(reverse('perfil'), {'aba': 'pele'})
        self.assertContains(resposta, '<option value="reduzir acne" selected>Reduzir acne</option>', html=True)
        # as 6 opcoes do questionario existem no select
        for codigo in OBJETIVOS:
            self.assertContains(resposta, f'<option value="{codigo}"')

    def test_objetivo_antigo_salvo_no_banco_aparece_selecionado(self):
        self.perfil.objetivo = 'Hidratar a pele'  # gravado pela versao antiga da aba
        self.perfil.save()
        resposta = self.client.get(reverse('perfil'), {'aba': 'pele'})
        self.assertContains(resposta, '<option value="hidratar" selected>Hidratar</option>', html=True)

    def test_objetivo_texto_livre_continua_na_lista(self):
        self.perfil.objetivo = 'pele de porcelana'
        self.perfil.save()
        resposta = self.client.get(reverse('perfil'), {'aba': 'pele'})
        self.assertContains(resposta, '<option value="pele de porcelana" selected>pele de porcelana</option>', html=True)


class ObjetivoTests(TestCase):
    def test_normalizar_objetivo(self):
        self.assertEqual(normalizar_objetivo('reduzir acne'), 'reduzir acne')
        self.assertEqual(normalizar_objetivo('Controlar oleosidade'), 'controlar oleosidade')
        self.assertEqual(normalizar_objetivo('Hidratar a pele'), 'hidratar')
        self.assertEqual(normalizar_objetivo('Prevenir sinais de idade'), 'anti-idade')
        self.assertEqual(normalizar_objetivo('  Meu Objetivo  '), 'Meu Objetivo')  # texto livre nao muda
        self.assertEqual(normalizar_objetivo(None), '')

    def test_rotulo_objetivo(self):
        self.assertEqual(rotulo_objetivo('anti-idade'), 'Anti-idade')
        self.assertEqual(rotulo_objetivo('Hidratar a pele'), 'Hidratar')
        self.assertEqual(rotulo_objetivo('pele de porcelana'), 'pele de porcelana')

    def test_todo_objetivo_tem_preocupacao_no_mapa(self):
        self.assertEqual(set(OBJETIVOS), set(MAPA_OBJETIVO_PREOCUPACAO))

    def test_questionario_oferece_os_mesmos_objetivos(self):
        resposta = self.client.get(reverse('questionario'))
        for codigo in OBJETIVOS:
            self.assertContains(resposta, f'<option value="{codigo}">')

    def test_telas_mostram_o_texto_e_nao_o_codigo(self):
        usuario, _ = criar_usuario_com_perfil()
        self.client.login(username='ana@email.com', password=SENHA)
        for nome in ('dashboard', 'produtos'):
            self.assertContains(self.client.get(reverse(nome)), 'Controlar oleosidade')


# ==========================================================================
# QUESTIONÁRIO - o POST precisa gravar todas as respostas no perfil
# ==========================================================================

def respostas_do_questionario(**mudancas):
    respostas = {
        'idade': '25', 'tipo_pele': 'oleosa', 'alergias': '0', 'maquiagem': '0',
        'reacao_sol': '2', 'base_produto': 'gel', 'objetivo': 'reduzir acne',
        'sono_horas': 'curto', 'exercicio_frequencia': '5x+', 'protetor_solar': '2',
    }
    respostas.update(mudancas)
    return respostas


class QuestionarioTests(TestCase):
    def setUp(self):
        self.usuario = Usuario.objects.create_user(email='nova@email.com', nome_usuario='Nova', password=SENHA)
        self.client.login(username='nova@email.com', password=SENHA)

    def perfil(self):
        return PerfilDermatologico.objects.get(usuario=self.usuario)

    def test_primeiro_envio_salva_sono_exercicio_protetor_e_objetivo(self):
        resposta = self.client.post(reverse('questionario'), respostas_do_questionario())
        self.assertRedirects(resposta, reverse('dashboard'))
        perfil = self.perfil()
        self.assertEqual(perfil.sono_horas, 'curto')
        self.assertEqual(perfil.exercicio_frequencia, '5x+')
        self.assertEqual(perfil.protetor_solar, 2)
        self.assertEqual(perfil.objetivo, 'reduzir acne')

    def test_responder_de_novo_atualiza_os_campos(self):
        self.client.post(reverse('questionario'), respostas_do_questionario())
        self.client.post(reverse('questionario'), respostas_do_questionario(
            sono_horas='longo', exercicio_frequencia='nunca', protetor_solar='0'))
        perfil = self.perfil()
        self.assertEqual(perfil.sono_horas, 'longo')
        self.assertEqual(perfil.exercicio_frequencia, 'nunca')
        self.assertEqual(perfil.protetor_solar, 0)  # 0 e uma resposta valida, nao "vazio"

    def test_valores_invalidos_no_primeiro_envio_ficam_com_o_padrao(self):
        self.client.post(reverse('questionario'), respostas_do_questionario(
            sono_horas='dormi', exercicio_frequencia='sempre', protetor_solar='9'))
        perfil = self.perfil()
        self.assertEqual(perfil.sono_horas, 'adequado')
        self.assertEqual(perfil.exercicio_frequencia, '1-2x')
        self.assertEqual(perfil.protetor_solar, 1)

    def test_valores_invalidos_mantem_o_que_ja_estava(self):
        self.client.post(reverse('questionario'), respostas_do_questionario())
        self.client.post(reverse('questionario'), respostas_do_questionario(
            sono_horas='dormi', exercicio_frequencia='', protetor_solar='abc'))
        perfil = self.perfil()
        self.assertEqual(perfil.sono_horas, 'curto')
        self.assertEqual(perfil.exercicio_frequencia, '5x+')
        self.assertEqual(perfil.protetor_solar, 2)

    def test_objetivo_do_questionario_gera_preocupacao(self):
        self.client.post(reverse('questionario'), respostas_do_questionario(objetivo='reduzir acne'))
        self.assertEqual(preocupacoes_do_perfil(self.perfil(), {}), {'Acne'})


class SegurancaTests(PerfilBaseTestCase):
    def test_troca_de_senha_mantem_o_usuario_logado(self):
        resposta = self.client.post(reverse('alterar_senha'), {
            'old_password': SENHA,
            'new_password1': 'outra-senha-forte-456',
            'new_password2': 'outra-senha-forte-456',
        })
        self.assertRedirects(resposta, f"{reverse('perfil')}?aba=seguranca")
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password('outra-senha-forte-456'))
        # continua logado: abrir o perfil nao redireciona pro login
        self.assertEqual(self.client.get(reverse('perfil')).status_code, 200)

    def test_senha_atual_errada_mostra_erro_e_nao_troca(self):
        resposta = self.client.post(reverse('alterar_senha'), {
            'old_password': 'errada',
            'new_password1': 'outra-senha-forte-456',
            'new_password2': 'outra-senha-forte-456',
        })
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['aba_ativa'], 'seguranca')
        self.assertTrue(resposta.context['form_senha'].errors)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(SENHA))

    def test_senha_fraca_e_recusada(self):
        resposta = self.client.post(reverse('alterar_senha'), {
            'old_password': SENHA,
            'new_password1': '12345678',
            'new_password2': '12345678',
        })
        self.assertEqual(resposta.status_code, 200)
        self.usuario.refresh_from_db()
        self.assertTrue(self.usuario.check_password(SENHA))


class PrivacidadeTests(PerfilBaseTestCase):
    def test_apagar_imagem_do_scan(self):
        resposta = self.client.post(reverse('apagar_foto_perfil'))
        self.assertRedirects(resposta, f"{reverse('perfil')}?aba=privacidade")
        self.perfil.refresh_from_db()
        self.assertIsNone(self.perfil.foto_rosto)
        # a analise e o score continuam
        self.assertEqual(self.perfil.porcentagem_saude, 78)
        self.assertIsNotNone(self.perfil.dados_ia)

    def test_apagar_so_aceita_post(self):
        self.client.get(reverse('apagar_foto_perfil'))
        self.perfil.refresh_from_db()
        self.assertIsNotNone(self.perfil.foto_rosto)

    def test_exportar_dados_baixa_json(self):
        resposta = self.client.get(reverse('exportar_dados'))
        self.assertEqual(resposta.status_code, 200)
        self.assertIn('attachment', resposta['Content-Disposition'])
        dados = json.loads(resposta.content)
        self.assertEqual(dados['conta']['email'], 'ana@email.com')
        self.assertEqual(dados['perfil_dermatologico']['tipo_pele'], 'mista')
        self.assertEqual(dados['perfil_dermatologico']['analise_ia']['oiliness']['score'], 90)
        self.assertNotIn('password', json.dumps(dados))

    def test_exportar_exige_login(self):
        self.client.logout()
        resposta = self.client.get(reverse('exportar_dados'))
        self.assertEqual(resposta.status_code, 302)

    def test_excluir_conta_exige_a_palavra_de_confirmacao(self):
        self.client.post(reverse('excluir_conta'), {'senha': SENHA, 'confirmacao': 'sim'})
        self.assertTrue(Usuario.objects.filter(pk=self.usuario.pk).exists())

    def test_excluir_conta_exige_a_senha_correta(self):
        self.client.post(reverse('excluir_conta'), {'senha': 'errada', 'confirmacao': 'EXCLUIR'})
        self.assertTrue(Usuario.objects.filter(pk=self.usuario.pk).exists())

    def test_excluir_conta_apaga_usuario_e_perfil(self):
        resposta = self.client.post(reverse('excluir_conta'), {'senha': SENHA, 'confirmacao': 'excluir'})
        self.assertRedirects(resposta, reverse('login'))
        self.assertFalse(Usuario.objects.filter(pk=self.usuario.pk).exists())
        self.assertFalse(PerfilDermatologico.objects.filter(pk=self.perfil.pk).exists())
        # a sessao foi encerrada
        self.assertEqual(self.client.get(reverse('perfil')).status_code, 302)

    def test_excluir_so_aceita_post(self):
        self.client.get(reverse('excluir_conta'))
        self.assertTrue(Usuario.objects.filter(pk=self.usuario.pk).exists())


# ==========================================================================
# TRATAMENTO - model RegraTratamento e comando import_tratamento_csv
# ==========================================================================

CABECALHO_TRATAMENTO = (
    'Faixa_Etária,Tipo_de_Pele,Subtipo_de_Pele,Sensibilidade,Preocupação,Tipo_Interno,'
    'Ingredientes,Concentrações,Efeitos'
)

# 3 linhas de exemplo: acne (tipo interno), rugas ("Geral") e uma sem sensibilidade
CSV_TRATAMENTO = f"""{CABECALHO_TRATAMENTO}
14-18,Oleosa,Oleosa a Normal,Sim,Acne,Comedonal,PCA de Zinco + Peróxido de Benzoíla + Ácido Salicílico,0.5% + 2% + 5%,"Controla o sebo, Desobstrui os poros"
45+,Seca,Extremamente Seca,Não,Rugas,Geral,Retinol + Ácido Hialurônico + Peptídeos,10% + 1% + 2%,Estimula o colágeno
25-36,Mista,Zona T Oleosa e Bochechas Secas,Não,Poros Dilatados,Geral,Niacinamida + Ácido Salicílico + Argila,5% + 2% + 3%,Disfarça os poros
"""


def importar_tratamento(texto):
    # o CSV real vem com BOM (utf-8-sig); os exemplos tambem, para testar isso
    with tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False, encoding='utf-8-sig') as arquivo:
        arquivo.write(texto)
    try:
        saida = StringIO()
        call_command('import_tratamento_csv', arquivo.name, stdout=saida)
        return saida.getvalue()
    finally:
        os.unlink(arquivo.name)


class ImportarTratamentoTests(TestCase):
    def test_importa_as_linhas_do_csv(self):
        saida = importar_tratamento(CSV_TRATAMENTO)
        self.assertEqual(RegraTratamento.objects.count(), 3)
        self.assertIn('3 criadas, 0 atualizadas', saida)

    def test_converte_os_campos_para_o_formato_do_perfil(self):
        importar_tratamento(CSV_TRATAMENTO)
        acne = RegraTratamento.objects.get(preocupacao='Acne')
        self.assertEqual(acne.faixa_etaria, '14-18')
        self.assertEqual(acne.tipo_pele, 'oleosa')       # minusculo, como no perfil
        self.assertIs(acne.sensibilidade, True)          # "Sim"
        self.assertEqual(acne.tipo_interno, 'Comedonal')
        self.assertEqual(acne.efeitos, 'Controla o sebo, Desobstrui os poros')
        rugas = RegraTratamento.objects.get(preocupacao='Rugas')
        self.assertIs(rugas.sensibilidade, False)        # "Não"
        self.assertEqual(rugas.tipo_interno, '')         # "Geral" vira vazio

    def test_rodar_duas_vezes_nao_duplica(self):
        importar_tratamento(CSV_TRATAMENTO)
        saida = importar_tratamento(CSV_TRATAMENTO)
        self.assertEqual(RegraTratamento.objects.count(), 3)
        self.assertIn('0 criadas, 3 atualizadas', saida)

    def test_reimportar_atualiza_os_ingredientes(self):
        importar_tratamento(CSV_TRATAMENTO)
        importar_tratamento(CSV_TRATAMENTO.replace('Estimula o colágeno', 'Efeito novo'))
        self.assertEqual(RegraTratamento.objects.get(preocupacao='Rugas').efeitos, 'Efeito novo')
        self.assertEqual(RegraTratamento.objects.count(), 3)

    def test_concentracoes_nao_sao_importadas(self):
        importar_tratamento(CSV_TRATAMENTO)
        campos = {campo.name for campo in RegraTratamento._meta.get_fields()}
        self.assertFalse(any('concentra' in campo for campo in campos))
        for regra in RegraTratamento.objects.all():
            self.assertNotIn('%', regra.ingredientes + regra.efeitos)

    def test_lista_ingredientes(self):
        importar_tratamento(CSV_TRATAMENTO)
        regra = RegraTratamento.objects.get(preocupacao='Rugas')
        self.assertEqual(regra.lista_ingredientes(), ['Retinol', 'Ácido Hialurônico', 'Peptídeos'])

    def test_arquivo_inexistente(self):
        with self.assertRaises(CommandError):
            call_command('import_tratamento_csv', '/caminho/que/nao/existe.csv')

    def test_csv_sem_coluna_obrigatoria(self):
        with self.assertRaises(CommandError):
            importar_tratamento('Faixa_Etária,Tipo_de_Pele\n14-18,Oleosa\n')

    def test_sensibilidade_invalida_nao_importa_nada(self):
        linhas = CSV_TRATAMENTO.splitlines()
        linhas[3] = linhas[3].replace(',Não,Poros', ',Talvez,Poros')
        with self.assertRaises(CommandError):
            importar_tratamento('\n'.join(linhas))
        self.assertEqual(RegraTratamento.objects.count(), 0)  # tudo ou nada

    def test_dataset_completo_tem_1120_regras(self):
        caminho = os.path.join(settings.BASE_DIR, 'core', 'data', 'tratamento_skincare.csv')
        call_command('import_tratamento_csv', caminho, stdout=StringIO())
        self.assertEqual(RegraTratamento.objects.count(), 1120)
        call_command('import_tratamento_csv', caminho, stdout=StringIO())
        self.assertEqual(RegraTratamento.objects.count(), 1120)
        # nenhuma regra com tipo de pele fora dos valores do perfil
        tipos = set(RegraTratamento.objects.values_list('tipo_pele', flat=True))
        self.assertEqual(tipos, {'normal', 'seca', 'oleosa', 'mista'})
