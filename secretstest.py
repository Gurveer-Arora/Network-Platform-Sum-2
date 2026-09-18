import secrets
import pandas as pd

df = pd.read_csv('customer.csv')
passwords = list()

for i in range(len(df)):
    token = secrets.token_urlsafe(10)
    while token.startswith("="):
        token = secrets.token_urlsafe(10)
    
    passwords.append(token)

df['password'] = passwords

df.to_csv('customer.csv', index=False)