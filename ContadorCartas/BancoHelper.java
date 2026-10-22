package com.example.contadorcartas;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;

import java.util.ArrayList;

public class BancoHelper extends SQLiteOpenHelper {

    public BancoHelper(Context context) {
        super(context, "partidas.db", null, 1);
    }

    // Roda só na primeira vez: cria a tabela
    @Override
    public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE partidas (" +
                "id INTEGER PRIMARY KEY AUTOINCREMENT, " +
                "data TEXT, " +
                "jogador1 TEXT, " +
                "jogador2 TEXT, " +
                "pontos1 INTEGER, " +
                "pontos2 INTEGER, " +
                "vencedor TEXT)");
    }

    // Roda quando o número da versão aumenta
    @Override
    public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
        db.execSQL("DROP TABLE IF EXISTS partidas");
        onCreate(db);
    }

    // Salva uma partida na tabela
    public void salvarPartida(String data, String jogador1, String jogador2,
                              int pontos1, int pontos2, String vencedor) {
        SQLiteDatabase db = getWritableDatabase();
        ContentValues valores = new ContentValues();
        valores.put("data", data);
        valores.put("jogador1", jogador1);
        valores.put("jogador2", jogador2);
        valores.put("pontos1", pontos1);
        valores.put("pontos2", pontos2);
        valores.put("vencedor", vencedor);
        db.insert("partidas", null, valores);
    }

    // Busca todas as partidas (da mais nova para a mais antiga)
    // e devolve cada uma como um texto pronto para mostrar na lista
    public ArrayList<String> listarPartidas() {
        ArrayList<String> lista = new ArrayList<String>();
        SQLiteDatabase db = getReadableDatabase();
        Cursor cursor = db.rawQuery("SELECT data, jogador1, jogador2, pontos1, pontos2, vencedor " +
                "FROM partidas ORDER BY id DESC", null);

        while (cursor.moveToNext()) {
            String data = cursor.getString(0);
            String jogador1 = cursor.getString(1);
            String jogador2 = cursor.getString(2);
            int pontos1 = cursor.getInt(3);
            int pontos2 = cursor.getInt(4);
            String vencedor = cursor.getString(5);

            lista.add(data + "\n" +
                    jogador1 + " " + pontos1 + " x " + pontos2 + " " + jogador2 + "\n" +
                    "Vencedor: " + vencedor);
        }
        cursor.close();
        return lista;
    }
}
