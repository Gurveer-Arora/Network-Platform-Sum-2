import hashlib
import os
import pandas as pd

df = pd.read_csv('customer.csv')
hashes = list()
salts = list()

for i in range(len(df)):
    password = df.at[df.index[i], 'password']
    password_bytes = password.encode("utf-8")
    salt = os.urandom(16)
    iterations = 600000

    hashed_password = hashlib.pbkdf2_hmac("sha256", password_bytes, salt, iterations)

    hashes.append(hashed_password.hex())
    salts.append(salt.hex())

df['hashedPassword'] = hashes
df['salt'] = salts

df.to_csv('customer.csv', index=False)
