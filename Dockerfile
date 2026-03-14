# Используем Python 3.10 slim
FROM python:3.10-slim

# Рабочая директория в контейнере
WORKDIR /app

# Устанавливаем системные зависимости
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Копируем файл зависимостей
COPY requirements.txt .

# Устанавливаем Python зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь проект в контейнер
COPY . .

# Экспортируем порт, если нужен (например, для виджета)
EXPOSE 8080

# Запуск бота
CMD ["python", "main.py"]