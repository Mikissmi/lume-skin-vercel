package com.example.contadorcartas;

import android.content.Intent;
import android.os.Bundle;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

import androidx.appcompat.app.AppCompatActivity;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

public class MainActivity extends AppCompatActivity {

    // Componentes da tela
    EditText editNome1, editNome2, editMaximo;
    TextView textPontos1, textPontos2, textMensagem;
    Button botaoMais1, botaoMenos1, botaoMais2, botaoMenos2, botaoReiniciar;
    Button botaoFinalizar, botaoHistorico;

    // Banco de dados (SQLite)
    BancoHelper banco;

    // Pontuação de cada jogador
    int pontos1 = 0;
    int pontos2 = 0;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);

        // Ligando as variáveis aos componentes do XML
        editNome1 = findViewById(R.id.editNome1);
        editNome2 = findViewById(R.id.editNome2);
        editMaximo = findViewById(R.id.editMaximo);
        textPontos1 = findViewById(R.id.textPontos1);
        textPontos2 = findViewById(R.id.textPontos2);
        textMensagem = findViewById(R.id.textMensagem);
        botaoMais1 = findViewById(R.id.botaoMais1);
        botaoMenos1 = findViewById(R.id.botaoMenos1);
        botaoMais2 = findViewById(R.id.botaoMais2);
        botaoMenos2 = findViewById(R.id.botaoMenos2);
        botaoReiniciar = findViewById(R.id.botaoReiniciar);
        botaoFinalizar = findViewById(R.id.botaoFinalizar);
        botaoHistorico = findViewById(R.id.botaoHistorico);

        banco = new BancoHelper(this);

        // O que cada botão faz quando é clicado
        botaoMais1.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                aumentar(1);
            }
        });

        botaoMenos1.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                diminuir(1);
            }
        });

        botaoMais2.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                aumentar(2);
            }
        });

        botaoMenos2.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                diminuir(2);
            }
        });

        botaoReiniciar.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                reiniciar();
            }
        });

        botaoFinalizar.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                finalizarPartida();
            }
        });

        // Abre a tela de histórico
        botaoHistorico.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                Intent intent = new Intent(MainActivity.this, HistoricoActivity.class);
                startActivity(intent);
            }
        });
    }

    // Salva a partida no banco, mostra um Toast e zera o placar
    void finalizarPartida() {
        String nome1 = editNome1.getText().toString();
        String nome2 = editNome2.getText().toString();
        if (nome1.isEmpty()) {
            nome1 = "Jogador 1";
        }
        if (nome2.isEmpty()) {
            nome2 = "Jogador 2";
        }

        // Descobre quem venceu
        String vencedor;
        if (pontos1 > pontos2) {
            vencedor = nome1;
        } else if (pontos2 > pontos1) {
            vencedor = nome2;
        } else {
            vencedor = "Empate";
        }

        // Data e hora de agora
        String data = new SimpleDateFormat("dd/MM/yyyy HH:mm", Locale.getDefault()).format(new Date());

        banco.salvarPartida(data, nome1, nome2, pontos1, pontos2, vencedor);
        Toast.makeText(this, "Partida salva no histórico!", Toast.LENGTH_SHORT).show();

        reiniciar();
    }

    // Aumenta 1 ponto do jogador (1 ou 2)
    void aumentar(int jogador) {
        // Lê a pontuação máxima digitada. Se estiver vazia, avisa e para.
        String textoMaximo = editMaximo.getText().toString();
        if (textoMaximo.isEmpty()) {
            Toast.makeText(this, "Digite a pontuação máxima primeiro!", Toast.LENGTH_SHORT).show();
            return;
        }
        int maximo = Integer.parseInt(textoMaximo);

        if (jogador == 1) {
            if (pontos1 >= maximo) {
                Toast.makeText(this, "Já chegou na pontuação máxima!", Toast.LENGTH_SHORT).show();
                return;
            }
            pontos1++;
            textPontos1.setText(String.valueOf(pontos1));
            if (pontos1 == maximo) {
                vitoria(editNome1, "Jogador 1");
            }
        } else {
            if (pontos2 >= maximo) {
                Toast.makeText(this, "Já chegou na pontuação máxima!", Toast.LENGTH_SHORT).show();
                return;
            }
            pontos2++;
            textPontos2.setText(String.valueOf(pontos2));
            if (pontos2 == maximo) {
                vitoria(editNome2, "Jogador 2");
            }
        }
    }

    // Diminui 1 ponto do jogador (1 ou 2), sem deixar ficar negativo
    void diminuir(int jogador) {
        if (jogador == 1) {
            if (pontos1 == 0) {
                Toast.makeText(this, "A pontuação não pode ser negativa!", Toast.LENGTH_SHORT).show();
                return;
            }
            pontos1--;
            textPontos1.setText(String.valueOf(pontos1));
        } else {
            if (pontos2 == 0) {
                Toast.makeText(this, "A pontuação não pode ser negativa!", Toast.LENGTH_SHORT).show();
                return;
            }
            pontos2--;
            textPontos2.setText(String.valueOf(pontos2));
        }
    }

    // Mostra a mensagem de vitória e bloqueia os botões de pontuação
    void vitoria(EditText editNome, String nomePadrao) {
        String nome = editNome.getText().toString();
        if (nome.isEmpty()) {
            nome = nomePadrao; // se ninguém digitou o nome, usa "Jogador 1" ou "Jogador 2"
        }
        textMensagem.setText("Parabéns, " + nome + "! Você venceu!");

        botaoMais1.setEnabled(false);
        botaoMenos1.setEnabled(false);
        botaoMais2.setEnabled(false);
        botaoMenos2.setEnabled(false);
    }

    // Zera tudo e libera os botões de novo
    void reiniciar() {
        pontos1 = 0;
        pontos2 = 0;
        textPontos1.setText("0");
        textPontos2.setText("0");
        textMensagem.setText("");

        botaoMais1.setEnabled(true);
        botaoMenos1.setEnabled(true);
        botaoMais2.setEnabled(true);
        botaoMenos2.setEnabled(true);
    }
}
