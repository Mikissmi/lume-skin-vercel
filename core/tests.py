import json

from django.test import TestCase
from django.urls import reverse

from .models import Usuario, PerfilDermatologico
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
            objetivo='Controlar oleosidade',
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
            'objetivo': 'Hidratar a pele',
            'alergias': 'látex',
            'preferencia_produto': 'creme',
            'usa_maquiagem_diariamente': 'sim',
        })
        self.assertRedirects(resposta, f"{reverse('perfil')}?aba=pele")
        self.perfil.refresh_from_db()
        self.assertEqual(self.perfil.tipo_pele, 'seca')
        self.assertEqual(self.perfil.objetivo, 'Hidratar a pele')
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
