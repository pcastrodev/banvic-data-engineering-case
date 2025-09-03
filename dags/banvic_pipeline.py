from __future__ import annotations

import io
import os
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
import sqlalchemy as sa
from airflow import DAG
from airflow.operators.empty import EmptyOperator
from airflow.operators.python import PythonOperator, get_current_context

import logging
log = logging.getLogger(__name__)
log.info("[BANVIC DAG LOADED v7] PID=%s", os.getpid())
print("[BANVIC DAG LOADED v7]")

# ------------------------- helpers -------------------------

def _run_date_folder() -> str:
    ctx = get_current_context()
    return ctx["ds"]

def _ensure_dir(path_obj: Path) -> None:
    path_obj.mkdir(parents=True, exist_ok=True)

def _atomic_write_df_to_csv(frame: pd.DataFrame, dest_path: Path) -> None:
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")
    frame.to_csv(tmp_path, index=False)
    os.replace(tmp_path, dest_path)

def _get_engine(user: str, password: str, host: str, port: int, db: str):
    encoded_pw = quote_plus(password)
    dsn = f"postgresql+psycopg2://{user}:{encoded_pw}@{host}:{port}/{db}"
    return sa.create_engine(dsn)

def _ensure_table_text_schema(conn, schema: str, table: str, columns: list[str]) -> None:
    cols_def = ", ".join(f'"{c}" TEXT' for c in columns)
    conn.exec_driver_sql(f'CREATE TABLE IF NOT EXISTS "{schema}"."{table}" ({cols_def});')
    conn.exec_driver_sql(f'TRUNCATE TABLE "{schema}"."{table}";')

def _copy_df_to_postgres(df: pd.DataFrame, engine: sa.Engine, fq_table: str) -> None:
    schema, table = fq_table.split(".", 1)

    data_copy = df.copy()
    data_copy.columns = [str(c) for c in data_copy.columns]
    cols_list = ", ".join([f'"{c}"' for c in data_copy.columns])

    with engine.begin() as dw_session:
        _ensure_table_text_schema(dw_session, schema, table, list(data_copy.columns))

        csv_buf = io.StringIO()
        data_copy.to_csv(csv_buf, index=False, header=False)
        csv_buf.seek(0)

        raw_conn = dw_session.connection
        with raw_conn.cursor() as cur:
            cur.copy_expert(
                sql=f'COPY "{schema}"."{table}" ({cols_list}) FROM STDIN WITH (FORMAT CSV)',
                file=csv_buf,
            )
        raw_conn.commit()

# --------------------- task callables ----------------------

def extract_from_source_db() -> None:
    exec_date = _run_date_folder()
    out_base_dir = Path("/opt/airflow/data") / exec_date / "source"
    _ensure_dir(out_base_dir)

    src_engine = _get_engine(
        user=os.getenv("SOURCE_DB_USER", "data_engineer"),
        password=os.getenv("SOURCE_DB_PASSWORD", "v3rysecur&pas5w0rd"),
        host=os.getenv("SOURCE_DB_HOST", "source_db"),
        port=int(os.getenv("SOURCE_DB_PORT", "5432")),
        db=os.getenv("SOURCE_DB_NAME", "banvic"),
    )

    def _df_from_query(src_conn, sql: str) -> pd.DataFrame:
        res = src_conn.execute(sa.text(sql))
        rows = res.fetchall()
        cols = list(res.keys())
        return pd.DataFrame(rows, columns=cols)

    with src_engine.connect() as src_conn:
        tabelas_df = _df_from_query(
            src_conn,
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name;
            """,
        )
        tabelas = tabelas_df["table_name"].tolist()
        if not tabelas:
            raise RuntimeError("Nenhuma tabela encontrada no schema public do source_db.")

        for tabela in tabelas:
            dados = _df_from_query(src_conn, f'SELECT * FROM "{tabela}"')
            _atomic_write_df_to_csv(dados, out_base_dir / f"{tabela}.csv")

def extract_from_csv_dataset() -> None:
    exec_date = _run_date_folder()
    csv_fonte_path = Path("/opt/airflow/datasets") / "transacoes.csv"
    if not csv_fonte_path.exists():
        raise FileNotFoundError("datasets/transacoes.csv não encontrado. Coloque o arquivo fornecido em ./datasets/")

    dados_csv = pd.read_csv(csv_fonte_path)
    dir_saida = Path("/opt/airflow/data") / exec_date / "csv"
    _ensure_dir(dir_saida)
    _atomic_write_df_to_csv(dados_csv, dir_saida / "transacoes.csv")

def load_to_dw() -> None:
    exec_date = _run_date_folder()
    dir_particao = Path("/opt/airflow/data") / exec_date

    dw_engine = _get_engine(
        user=os.getenv("DW_DB_USER", "dw_user"),
        password=os.getenv("DW_DB_PASSWORD", "dw_password"),
        host=os.getenv("DW_DB_HOST", "dw_db"),
        port=int(os.getenv("DW_DB_PORT", "5432")),
        db=os.getenv("DW_DB_NAME", "dw"),
    )

    with dw_engine.begin() as dw_conn:
        log.info("[LOAD] Entrou no load_to_dw (run_date=%s)", exec_date)
        dw_conn.exec_driver_sql("SET LOCAL lock_timeout = '2s';")
        dw_conn.exec_driver_sql("SET LOCAL statement_timeout = '5min';")

        dir_source = dir_particao / "source"
        if dir_source.exists():
            for arquivo_csv in sorted(dir_source.glob("*.csv")):
                nome_tabela = arquivo_csv.stem
                tabela_stg = f"stg_source_{nome_tabela}"
                tabela_final = f"source_{nome_tabela}"

                dados = pd.read_csv(arquivo_csv)
                _copy_df_to_postgres(dados, dw_engine, f"staging.{tabela_stg}")

                log.info("[LOAD] Atualizando tabela final %s a partir de staging.%s ...", tabela_final, tabela_stg)
                dw_conn.exec_driver_sql("SET LOCAL lock_timeout = '2s'; SET LOCAL statement_timeout = '5min';")
                dw_conn.exec_driver_sql(
                    f"""
                    DO $$
                    BEGIN
                        IF EXISTS (
                            SELECT 1 FROM information_schema.tables
                            WHERE table_schema='public' AND table_name='{tabela_final}'
                        ) THEN
                            EXECUTE 'TRUNCATE TABLE "' || '{tabela_final}' || '"';
                            EXECUTE 'INSERT INTO "' || '{tabela_final}' || '" SELECT * FROM staging.{tabela_stg}';
                        ELSE
                            EXECUTE 'CREATE TABLE "' || '{tabela_final}' || '" AS TABLE staging.{tabela_stg}';
                        END IF;
                    END $$;
                    """
                )
                log.info("[LOAD] Final %s atualizada.", tabela_final)

        caminho_transacoes = dir_particao / "csv" / "transacoes.csv"
        if caminho_transacoes.exists():
            tabela_stg = "stg_csv_transacoes"
            tabela_final = "csv_transacoes"

            dados_tx = pd.read_csv(caminho_transacoes)
            _copy_df_to_postgres(dados_tx, dw_engine, f"staging.{tabela_stg}")

            log.info("[LOAD] Atualizando tabela final %s a partir de staging.%s ...", tabela_final, tabela_stg)
            dw_conn.exec_driver_sql("SET LOCAL lock_timeout = '2s'; SET LOCAL statement_timeout = '5min';")
            dw_conn.exec_driver_sql(
                f"""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema='public' AND table_name='{tabela_final}'
                    ) THEN
                        EXECUTE 'TRUNCATE TABLE "' || '{tabela_final}' || '"';
                        EXECUTE 'INSERT INTO "' || '{tabela_final}' || '" SELECT * FROM staging.{tabela_stg}';
                    ELSE
                        EXECUTE 'CREATE TABLE "' || '{tabela_final}' || '" AS TABLE staging.{tabela_stg}';
                    END IF;
                END $$;
                """
            )
            log.info("[LOAD] Final %s atualizada.", tabela_final)

# ----------------------- DAG definition --------------------

default_args = {
    "owner": "banvic",
    "depends_on_past": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=3),
}

with DAG(
    dag_id="banvic_pipeline_dag",
    description="Pipeline BanVic: extrai (Postgres+CSV) -> grava CSV por data -> carrega DW",
    default_args=default_args,
    start_date=datetime(2025, 1, 1),
    schedule_interval="35 4 * * *",
    catchup=False,
    max_active_runs=1,
    tags=["banvic", "etl"],
) as dag:
    start = EmptyOperator(task_id="start")

    extract_db = PythonOperator(
        task_id="extract_from_source_db",
        python_callable=extract_from_source_db,
    )

    extract_csv = PythonOperator(
        task_id="extract_from_csv_dataset",
        python_callable=extract_from_csv_dataset,
    )

    load = PythonOperator(
        task_id="load_to_dw",
        python_callable=load_to_dw,
    )

    end = EmptyOperator(task_id="end")

    start >> [extract_db, extract_csv] >> load >> end
