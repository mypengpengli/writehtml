FROM python:3.12-slim

WORKDIR /app

# 先装依赖，利用层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷代码
COPY . .

EXPOSE 9123
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9123"]
