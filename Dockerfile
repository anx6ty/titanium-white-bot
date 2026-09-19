FROM node:20-alpine

WORKDIR /app

# Agar aapka bot Node.js hai to yeh line use karo
# COPY . .
# CMD ["node", "index.js"]

# Agar bot Python hai to yeh line use karo
# COPY . .
# CMD ["python3", "main.py"]

# Agar abhi koi bot file nahi hai, to temporary fail command rakho
CMD ["sh", "-c", "echo 'No bot entrypoint found'; exit 1"]
