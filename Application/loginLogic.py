import hashlib
import os

def hashPassword(password, salt):
    password_bytes = password.encode("utf-8")
    salt = bytes.fromhex(salt)
    iterations = 600000

    hashed_password = hashlib.pbkdf2_hmac("sha256", password_bytes, salt, iterations).hex()

    return hashed_password

def generateSalt():
    salt = os.urandom(16)
    return salt