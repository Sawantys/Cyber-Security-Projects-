import os
import json
import sys
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend
from cryptography.exceptions import InvalidTag

KEY_DIR = "keys"
PRIVATE_KEY_PATH = os.path.join(KEY_DIR, "private_key.pem")
PUBLIC_KEY_PATH = os.path.join(KEY_DIR, "public_key.pem")
SALT_PATH = os.path.join(KEY_DIR, "salt.bin")


# ---------- KEY DERIVATION ----------
def derive_key(password: str, salt: bytes) -> bytes:
    """Derive 256-bit key from password using PBKDF2"""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=200000,
        backend=default_backend()
    )
    return kdf.derive(password.encode())


# ---------- KEY GENERATION ----------
def generate_keys():
    try:
        password = input("Set password for private key: ").strip()
        if not password:
            print("❌ Password cannot be empty.")
            return

        os.makedirs(KEY_DIR, exist_ok=True)

        salt = os.urandom(16)
        key = derive_key(password, salt)

        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048
        )
        public_key = private_key.public_key()

        # Save private key (encrypted)
        with open(PRIVATE_KEY_PATH, "wb") as f:
            f.write(
                private_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.BestAvailableEncryption(key)
                )
            )

        # Save public key
        with open(PUBLIC_KEY_PATH, "wb") as f:
            f.write(
                public_key.public_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo
                )
            )

        # Save salt
        with open(SALT_PATH, "wb") as f:
            f.write(salt)

        print("✔ Keys generated successfully.\n")

    except Exception as e:
        print("❌ Error generating keys:", e)


# ---------- ENCRYPTION ----------
def encrypt_file():
    try:
        if not os.path.exists(PUBLIC_KEY_PATH):
            print("❌ Public key not found. Generate keys first.")
            return

        file_path = input("Enter file to encrypt: ").strip()

        if not os.path.exists(file_path):
            print("❌ File does not exist.")
            return

        # Load public key
        with open(PUBLIC_KEY_PATH, "rb") as f:
            public_key = serialization.load_pem_public_key(f.read())

        # Read file
        with open(file_path, "rb") as f:
            plaintext = f.read()

        # Generate AES key
        aes_key = AESGCM.generate_key(bit_length=256)
        aes = AESGCM(aes_key)
        nonce = os.urandom(12)

        ciphertext = aes.encrypt(nonce, plaintext, None)

        # Encrypt AES key with RSA
        encrypted_aes_key = public_key.encrypt(
            aes_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )

        output_data = {
            "nonce": nonce.hex(),
            "ciphertext": ciphertext.hex(),
            "encrypted_key": encrypted_aes_key.hex()
        }

        output_file = file_path + ".enc"
        with open(output_file, "w") as f:
            json.dump(output_data, f, indent=4)

        print(f"✔ File encrypted successfully → {output_file}\n")

    except Exception as e:
        print("❌ Encryption failed:", e)


# ---------- DECRYPTION ----------
def decrypt_file():
    try:
        if not os.path.exists(PRIVATE_KEY_PATH):
            print("❌ Private key not found. Generate keys first.")
            return

        password = input("Enter private key password: ").strip()

        # Load salt
        with open(SALT_PATH, "rb") as f:
            salt = f.read()

        key = derive_key(password, salt)

        # Load private key
        with open(PRIVATE_KEY_PATH, "rb") as f:
            private_key = serialization.load_pem_private_key(
                f.read(),
                password=key
            )

        file_path = input("Enter encrypted file: ").strip()

        if not os.path.exists(file_path):
            print("❌ File does not exist.")
            return

        # Load encrypted data
        with open(file_path, "r") as f:
            data = json.load(f)

        nonce = bytes.fromhex(data["nonce"])
        ciphertext = bytes.fromhex(data["ciphertext"])
        encrypted_key = bytes.fromhex(data["encrypted_key"])

        # Decrypt AES key
        aes_key = private_key.decrypt(
            encrypted_key,
            padding.OAEP(
                mgf=padding.MGF1(hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )

        aes = AESGCM(aes_key)

        try:
            plaintext = aes.decrypt(nonce, ciphertext, None)
        except InvalidTag:
            print("❌ Integrity check failed! File tampered.")
            return

        output_file = "decrypted_" + os.path.basename(file_path.replace(".enc", ""))
        with open(output_file, "wb") as f:
            f.write(plaintext)

        print(f"✔ File decrypted successfully → {output_file}\n")

    except Exception as e:
        print("❌ Decryption failed:", e)


# ---------- MAIN MENU ----------
def main():
    while True:
        print("----- Secure File Storage (Hybrid Cryptography) -----")
        print("1. Generate Keys")
        print("2. Encrypt File")
        print("3. Decrypt File")
        print("4. Exit")

        choice = input("Choose option: ").strip()

        if choice == "1":
            generate_keys()
        elif choice == "2":
            encrypt_file()
        elif choice == "3":
            decrypt_file()
        elif choice == "4":
            print("Exiting...")
            sys.exit()
        else:
            print("❌ Invalid option.\n")


if __name__ == "__main__":
    main()