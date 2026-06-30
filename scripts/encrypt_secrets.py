#!/usr/bin/env python3
"""
CLI para cifrar/descifrar el archivo de secretos AES-256-GCM.

Uso:
    # Cifrar secrets.env → secrets.env.enc
    python scripts/encrypt_secrets.py encrypt --key "mi-clave-secreta"

    # Descifrar secrets.env.enc → secrets.env (solo inspección)
    python scripts/encrypt_secrets.py decrypt --key "mi-clave-secreta" --output secrets.dec.env

    # Generar una clave de cifrado aleatoria
    python scripts/encrypt_secrets.py generate-key
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Asegurar que podemos importar desde el proyecto
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.core.secrets_manager import EncryptedFileStore


def cmd_generate_key() -> None:
    """Genera una clave de cifrado AES-256 aleatoria (hex)."""
    key = os.urandom(32).hex()
    print(f"🔑 SECRETS_ENCRYPTION_KEY={key}")
    print("Guarda esta clave en una variable de entorno segura (ej: .env o Docker secrets).")


def cmd_encrypt(args: argparse.Namespace) -> None:
    """Cifra el archivo .env plano a .env.enc."""
    dotenv_path = Path(args.input)
    if not dotenv_path.is_file():
        print(f"❌ Archivo no encontrado: {dotenv_path}")
        sys.exit(1)

    enc_path = args.output or "config/secrets.env.enc"
    key = args.key or os.environ.get("SECRETS_ENCRYPTION_KEY")
    if not key:
        print("❌ Debes proporcionar --key o definir SECRETS_ENCRYPTION_KEY")
        sys.exit(1)

    # Cargar secretos desde .env
    secrets: dict[str, str] = {}
    for line in dotenv_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip("\"'")
        if k and v:
            secrets[k] = v

    if not secrets:
        print("⚠️  No se encontraron secretos en el archivo.")
        return

    # Cifrar
    store = EncryptedFileStore(file_path=enc_path)
    store.save(secrets, enc_key=key)

    print(f"✅ Cifrados {len(secrets)} secreto(s) en {enc_path}")
    print(f"   Algoritmo: {store.ALGORITHM}")
    print(f"   Tamaño: {Path(enc_path).stat().st_size} bytes")
    print("")
    print("⚠️  Elimina el archivo .env original si no lo necesitas:")
    print(f"   rm {dotenv_path}")


def cmd_decrypt(args: argparse.Namespace) -> None:
    """Descifra el archivo .env.enc a texto plano (solo inspección)."""
    enc_path = Path(args.input)
    if not enc_path.is_file():
        print(f"❌ Archivo no encontrado: {enc_path}")
        sys.exit(1)

    output_path = args.output or "secrets.dec.env"
    key = args.key or os.environ.get("SECRETS_ENCRYPTION_KEY")
    if not key:
        print("❌ Debes proporcionar --key o definir SECRETS_ENCRYPTION_KEY")
        sys.exit(1)

    store = EncryptedFileStore(file_path=str(enc_path))
    secrets = store.load(enc_key=key)

    if not secrets:
        print("⚠️  No se encontraron secretos en el archivo cifrado.")
        return

    with open(output_path, "w") as f:
        f.write(f"# Descifrado desde {enc_path}\n")
        f.write(f"# Fecha: {__import__('datetime').datetime.now().isoformat()}\n")
        f.write(f"# ⚠️  Archivo temporal solo para inspección. NO subir al repositorio.\n")
        f.write("\n")
        for k, v in sorted(secrets.items()):
            f.write(f"{k}={v}\n")

    print(f"✅ Descifrados {len(secrets)} secreto(s) en {output_path}")
    print("⚠️  Elimina este archivo después de usarlo:")
    print(f"   rm {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cifrado/descifrado de secretos AES-256-GCM",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # Generate key
    sub.add_parser("generate-key", help="Genera una clave de cifrado aleatoria")

    # Encrypt
    enc_parser = sub.add_parser("encrypt", help="Cifra secrets.env → secrets.env.enc")
    enc_parser.add_argument("--input", default="config/secrets.env",
                            help="Archivo .env de entrada")
    enc_parser.add_argument("--output", default=None,
                            help="Archivo .enc de salida")
    enc_parser.add_argument("--key", default=None,
                            help="Clave de cifrado (o usa SECRETS_ENCRYPTION_KEY)")

    # Decrypt
    dec_parser = sub.add_parser("decrypt", help="Descifra secrets.env.enc (solo inspección)")
    dec_parser.add_argument("--input", default="config/secrets.env.enc",
                            help="Archivo .enc de entrada")
    dec_parser.add_argument("--output", default=None,
                            help="Archivo .env de salida")
    dec_parser.add_argument("--key", default=None,
                            help="Clave de cifrado (o usa SECRETS_ENCRYPTION_KEY)")

    args = parser.parse_args()

    if args.command == "generate-key":
        cmd_generate_key()
    elif args.command == "encrypt":
        cmd_encrypt(args)
    elif args.command == "decrypt":
        cmd_decrypt(args)


if __name__ == "__main__":
    main()
