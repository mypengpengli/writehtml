FROM node:24-bookworm-slim AS pi_deps

WORKDIR /opt/pi_runtime
COPY pi_runtime/package.json pi_runtime/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts

FROM python:3.12-slim

# Pi Agent Core requires Node >=22.19. The production package tree is copied
# separately below, so the final image contains no npm cache or dev packages.
COPY --from=node:24-bookworm-slim /usr/local /usr/local

WORKDIR /app

# 先装依赖，利用层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷代码
COPY --from=pi_deps /opt/pi_runtime/node_modules ./pi_runtime/node_modules
COPY . .

EXPOSE 9123
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9123"]
