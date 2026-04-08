import sys

path = r'D:\work\QT\quant-trading\src\data\market-data-provider.ts'
with open(path, 'rb') as f:
    data = f.read()

# The broken bytes on line 294: garbled name + ? without closing quote
# E6 B5 9C E6 97 82 E4 BC AF E5 A8 91 3F 2C 20 6D
old_bytes = b'\xe6\xb5\x9c\xe6\x97\x82\xe4\xbc\xaf\xe5\xa8\x91\x3f\x2c\x20\x6d'
# Replace with: 五粮液", m
new_bytes = '五粮液", m'.encode('utf-8')

count = data.count(old_bytes)
print(f'Found {count} occurrence(s) of broken pattern')

if count == 1:
    data = data.replace(old_bytes, new_bytes)
    with open(path, 'wb') as f:
        f.write(data)
    print('Fixed successfully')
else:
    print('Unexpected count, not modifying file')
    sys.exit(1)
