import json
import boto3
import os
import logging
from decimal import Decimal
from datetime import datetime

# Configuração do logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Conectar ao DynamoDB (LocalStack ou AWS)
dynamodb_endpoint = os.getenv("DYNAMODB_ENDPOINT", None)
dynamodb = boto3.resource("dynamodb", endpoint_url=dynamodb_endpoint)
table = dynamodb.Table("NotasFiscais")


# ------------------------- Função de Validação -------------------------
def validar_registro(registro):
    campos_obrigatorios = {"id", "cliente", "valor", "data_emissao"}

    if not isinstance(registro, dict):
        return False, "Registro não é um objeto JSON válido."

    if not campos_obrigatorios.issubset(registro.keys()):
        return False, f"Campos obrigatórios faltando. Esperados: {campos_obrigatorios}"

    if not isinstance(registro["id"], str):
        return False, "O campo 'id' deve ser uma string."

    if not isinstance(registro["cliente"], str):
        return False, "O campo 'cliente' deve ser uma string."

    if not isinstance(registro["valor"], (int, float, Decimal)):
        return False, "O campo 'valor' deve ser numérico."

    if not isinstance(registro["data_emissao"], str):
        return False, "O campo 'data_emissao' deve ser uma string no formato de data."

    return True, "Registro válido."


# ------------------------- Função para mover arquivo -------------------------
def mover_arquivo_s3(s3, bucket, key, destino):
    """Move o arquivo para a pasta de destino no S3."""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    novo_key = f"{destino}/{timestamp}_{key.split('/')[-1]}"
    logger.info(f"Movendo arquivo para: s3://{bucket}/{novo_key}")
    s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": key}, Key=novo_key)
    s3.delete_object(Bucket=bucket, Key=key)


# ------------------------- Inserir registros -------------------------
def inserir_registros(event):
    s3 = boto3.client("s3")

    for record in event["Records"]:
        s3_bucket = record["s3"]["bucket"]["name"]
        s3_key = record["s3"]["object"]["key"]

        # Ignorar arquivos nas pastas erro/ ou sucesso/
        if s3_key.startswith("erro/") or s3_key.startswith("sucesso/"):
            logger.info(f"Ignorando arquivo em pasta de erro ou sucesso: {s3_key}")
            continue

        logger.info(f"Processando arquivo: s3://{s3_bucket}/{s3_key}")

        try:
            # Ler o arquivo do S3
            response = s3.get_object(Bucket=s3_bucket, Key=s3_key)
            file_content = response["Body"].read().decode("utf-8")
            registros = json.loads(file_content)

        except Exception as e:
            logger.error(f"Erro ao ler arquivo do S3: {str(e)}")
            mover_arquivo_s3(s3, s3_bucket, s3_key, "erro")
            continue

        # Processar cada registro
        for registro in registros:
            valido, mensagem = validar_registro(registro)
            if not valido:
                logger.warning(f"Registro inválido: {mensagem}")
                continue

            try:
                logger.info(f"Inserindo registro no DynamoDB: {registro}")
                table.put_item(Item=registro)
                logger.info("Registro inserido com sucesso!")
            except Exception as e:
                logger.error(f"Erro ao inserir registro no DynamoDB: {str(e)}")
                mover_arquivo_s3(s3, s3_bucket, s3_key, "erro")
                break  # sai do loop se der erro

        else:
            # Só move para sucesso se não houve erro no loop
            mover_arquivo_s3(s3, s3_bucket, s3_key, "sucesso")

    return {"statusCode": 200, "body": json.dumps("Processamento concluído!")}


# ------------------------- Consultar registro -------------------------
def consultar_registro(event):
    """Consulta um registro no DynamoDB por ID."""
    try:
        body = json.loads(event["body"])
        registro_id = body.get("id")

        if not registro_id:
            return {
                "statusCode": 400,
                "body": json.dumps("O campo 'id' é obrigatório.")
            }

        logger.info(f"Consultando registro com ID: {registro_id}")

        response = table.get_item(Key={"id": registro_id})
        if "Item" not in response:
            return {
                "statusCode": 404,
                "body": json.dumps(f"Registro com ID '{registro_id}' não encontrado.")
            }

        return {
            "statusCode": 200,
            "body": json.dumps(response["Item"], default=str)
        }

    except Exception as e:
        logger.error(f"Erro ao consultar registro: {str(e)}")
        return {
            "statusCode": 500,
            "body": json.dumps("Erro interno ao consultar registro.")
        }


# ------------------------- Função principal Lambda -------------------------
def lambda_handler(event, context):
    """Função principal que direciona o processamento baseado no evento da API."""
    if "httpMethod" in event:
        method = event["httpMethod"]
        if method == "POST" and "/notas" in event["path"]:
            return inserir_registros(event)
        elif method == "GET" and "/notas" in event["path"]:
            return consultar_registro(event)

    # Caso não seja uma chamada de API, tratar como evento S3
    return inserir_registros(event)
