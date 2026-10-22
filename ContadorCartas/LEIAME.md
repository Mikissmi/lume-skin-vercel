# Contador de Jogos de Carta (Android – Java)

Arquivos:

| Arquivo | Onde colocar no Android Studio |
|---|---|
| `activity_main.xml` | `res/layout/` (substitui o existente) |
| `activity_historico.xml` | `res/layout/` (arquivo novo) |
| `MainActivity.java` | pasta do package (substitui o existente) |
| `HistoricoActivity.java` | pasta do package (arquivo novo) |
| `BancoHelper.java` | pasta do package (arquivo novo) |

**Importante:** registre a nova tela no `AndroidManifest.xml`, dentro de `<application>`
(logo abaixo da `MainActivity`):

```xml
<activity android:name=".HistoricoActivity" />
```

## Histórico com SQLite

- `BancoHelper` cria o banco `partidas.db` com a tabela `partidas`
  (id, data, jogador1, jogador2, pontos1, pontos2, vencedor).
- "Finalizar partida" salva o resultado no banco, mostra um `Toast` e zera o placar.
- "Ver histórico" abre a `HistoricoActivity`, que lista as partidas num `ListView`.
- Como os dados ficam no banco, o histórico continua aparecendo depois de fechar e abrir o app.

## Como usar no Android Studio

1. **New Project** → **Empty Views Activity** → Language: **Java**.
   - Nome: `ContadorCartas`
   - Package name: `com.example.contadorcartas`
     (se for outro, troque a primeira linha do `MainActivity.java` para o seu package).
2. Abra `app/res/layout/activity_main.xml`, clique em **Code** (canto superior direito)
   e substitua **todo** o conteúdo pelo de `activity_main.xml` desta pasta.
3. Abra `MainActivity.java` e substitua **todo** o conteúdo pelo de `MainActivity.java` desta pasta.
4. Rode (▶) no emulador ou no celular.

## O que cada requisito do PDF faz no código

| Requisito | Onde |
|---|---|
| Nome dos jogadores com *hint* | `EditText` com `android:hint` no XML |
| Pontuação máxima configurável | `editMaximo` (começa com 21) |
| Botões +1 / -1 e `TextView` da pontuação | `botaoMais`, `botaoMenos`, `textPontos` |
| Não ficar negativo (com `Toast`) | método `diminuir` |
| Não ultrapassar o máximo (com `Toast`) | método `aumentar` |
| Mensagem de vitória | método `vitoria` (também desativa os botões +1/-1) |
| Extra: reiniciar partida | método `reiniciar` |

## Para explicar ao professor

- `findViewById` liga cada variável a um componente do XML pelo `id`.
- `setOnClickListener` define o que acontece quando o botão é clicado.
- Os botões são desativados com `setEnabled(false)` depois que alguém vence,
  para ninguém continuar pontuando. O botão "Reiniciar partida" libera tudo de novo.
