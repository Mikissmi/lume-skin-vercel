# Contador de Jogos de Carta (Android – Java)

Só são necessários 2 arquivos: `activity_main.xml` (a tela) e `MainActivity.java` (a lógica).

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
