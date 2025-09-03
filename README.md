# 📊 BanVic Data Engineering Case

Este projeto implementa um pipeline de dados com **Apache Airflow**, **PostgreSQL** e **Docker**, desenvolvido para o case da BanVic.  
O objetivo foi construir um processo de **extração, transformação e carga (ETL)** que orquestra dados de diferentes fontes (banco de origem e arquivo CSV) e carrega tudo em um Data Warehouse.  

---

## 🛠️ Tecnologias utilizadas

- **Apache Airflow** para orquestração  
- **PostgreSQL** como banco de origem e Data Warehouse  
- **Docker + Docker Compose** para infraestrutura  
- **Python (Pandas + SQLAlchemy + psycopg2)** para tratamento e carga dos dados  

---

## ⚙️ Como rodar o projeto

### 1. Clonar o repositório

git clone https://github.com/pcastrodev/banvic-data-engineering-case.git
cd banvic-data-engineering-case

---

### 2. Subir os containers
docker compose up -d --build

---

### 3. Acessar o Airflow

URL: http://localhost:8080
Usuário: airflow
Senha: airflow

---

### 4. Executar o DAG

No Airflow, ative o DAG banvic_pipeline_dag e rode manualmente ou aguarde a execução agendada (todos os dias às 04h35).

## 🚀 Fluxo do pipeline

extract_from_source_db → Extrai tabelas do Postgres de origem e salva em CSV
extract_from_csv_dataset → Copia o dataset transacoes.csv para a partição do dia
load_to_dw → Carrega todos os CSVs no Data Warehouse (staging + tabelas finais)

---

## 📊 Estrutura no Data Warehouse

Após rodar o pipeline, o DW recebe as tabelas:

public.source_agencias
public.source_clientes
public.source_colaboradores
public.source_colaborador_agencia
public.source_contas
public.source_propostas_credito
public.csv_transacoes

Exemplos de consultas:
-- Total de clientes
SELECT COUNT(*) FROM public.source_clientes;

-- Total de contas
SELECT COUNT(*) FROM public.source_contas;

-- Total de transações
SELECT COUNT(*) FROM public.csv_transacoes;

---

## 👨‍💻 Autor
Desenvolvido por Pedro Castro (@pcastrodev
) como parte do case de Data Engineering da BanVic.
