package com.example.contadorcartas;

import android.os.Bundle;
import android.view.View;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.ListView;

import androidx.appcompat.app.AppCompatActivity;

import java.util.ArrayList;

public class HistoricoActivity extends AppCompatActivity {

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_historico);

        ListView listaPartidas = findViewById(R.id.listaPartidas);
        Button botaoVoltar = findViewById(R.id.botaoVoltar);

        // Pega as partidas salvas no banco
        BancoHelper banco = new BancoHelper(this);
        ArrayList<String> partidas = banco.listarPartidas();

        if (partidas.isEmpty()) {
            partidas.add("Nenhuma partida salva ainda.");
        }

        // O ArrayAdapter coloca cada texto da lista dentro do ListView
        ArrayAdapter<String> adapter = new ArrayAdapter<String>(
                this, android.R.layout.simple_list_item_1, partidas);
        listaPartidas.setAdapter(adapter);

        // Fecha esta tela e volta para o placar
        botaoVoltar.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                finish();
            }
        });
    }
}
