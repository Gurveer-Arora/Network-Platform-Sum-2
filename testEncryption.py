import hashlib
import pandas as pd

df = pd.read_csv('customer.csv')
validation = True

for i in range(len(df)):
    password = df.at[df.index[i], 'password']
    password_bytes = password.encode("utf-8")
    salt = bytes.fromhex(df.at[df.index[i], 'salt'])
    iterations = 600000

    hashed_password = hashlib.pbkdf2_hmac("sha256", password_bytes, salt, iterations).hex()

    if hashed_password != df.at[df.index[i], 'hashedPassword']:
        validation = False
        print(hashed_password, "BREAK", df.at[df.index[i], 'hashedPassword'])
        break

print("yes" if validation else "no")